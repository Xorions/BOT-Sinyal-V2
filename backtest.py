"""Backtest simulasi sinyal MTF terhadap data histori Bitget (offline, tanpa network socket).

Menjelajah bar M15: pada tiap bar tertutup, mesin sinyal (`assemble_signal`) dijalankan
persis seperti bot live — kompas H4/D1, zona H1, pelatuk M15 — memakai candle yang SUDAH
terjadi sampai waktu itu (tanpa look-ahead). Bila sinyal berarah (BUY/SELL) muncul, posisi
di-walk maju candle-per-candle (urutan kronologis) sampai TP1/TP2 tersentuh lebih dulu,
SL tersentuh, atau melewati jendela EVAL_MAX_HOURS (FLOATING). Anti sinyal berulang
(cooldown re-entry level sama) dan larangan posisi ganda per pair ikut disimulasikan.

Cara pakai:
    python backtest.py --days 5 --pairs 15 --step 15m
    python backtest.py --days 7 --pairs 20 --step 1h --out backtest_report.txt
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, ".")

import bot  # noqa: E402  (reuse _eligible_pair; import di sini agar path tetap)
import evaluation  # noqa: E402
from config import (  # noqa: E402
    COOLDOWN_ENTRY_TOL_PCT,
    COOLDOWN_SESSIONS,
    EVAL_MAX_HOURS,
    MAX_ATR_REL,
    MIN_VOLUME_USD,
)
from data import bitget  # noqa: E402
from engine import (  # noqa: E402
    ACTION_BUY,
    ACTION_SELL,
    _atr,
    assemble_signal,
)

WIB = timezone(timedelta(hours=7))
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
MIN15_MS = 900_000


# ---------------------------------------------------------------- fetch & slicing
def fetch_pair(symbol: str, eval_start_ms: int, days: int) -> Dict[str, List[Dict]]:
    """Fetch 4 timeframe sekali jalan untuk jendela backtest + lookback indikator."""
    m15_start = eval_start_ms - 3 * DAY_MS  # lookback 200 bar M15 (~2.1 hari) + 24j pct change
    h1_start = eval_start_ms - 6 * DAY_MS   # 120 bar H1 = 5 hari
    h4_start = eval_start_ms - 22 * DAY_MS  # 120 bar H4 = 20 hari
    m15 = bitget._klines(symbol, bitget.INTERVAL_M15, 1000, start_time=m15_start)
    h1 = bitget._klines(symbol, bitget.INTERVAL_1H, 1000, start_time=h1_start)
    h4 = bitget._klines(symbol, bitget.INTERVAL_4H, 1000, start_time=h4_start)
    d1 = bitget._klines(symbol, bitget.INTERVAL_1D, 90)
    return {
        "m15": m15,
        "h1": h1,
        "h4": h4,
        "d1": d1,
        "start": eval_start_ms,
        "end": eval_start_ms + days * DAY_MS,
    }


def up_to(candles: List[Dict], ts: int, limit: Optional[int] = None) -> List[Dict]:
    out = [c for c in candles if c.get("ts", 0) <= ts]
    return out[-limit:] if limit and len(out) > limit else out


def after(candles: List[Dict], ts: int, max_hours: float) -> List[Dict]:
    cutoff = ts + int(max_hours * HOUR_MS)
    return [c for c in candles if c.get("ts", 0) > ts and c["ts"] <= cutoff]


# ---------------------------------------------------------------- anti re-entry
def _suppressed_by_cooldown(daily_signals: Dict[str, List[Tuple]], symbol: str, action: str, entry: float, day_key: str) -> bool:
    """True bila base+arah+entry nyaris sama pernah disinyalkan di COOLDOWN_SESSIONS hari terakhir."""
    days = sorted(daily_signals.keys())
    recent_days = [d for d in days if d < day_key][-COOLDOWN_SESSIONS:]
    for d in recent_days:
        for sym, act, ent in daily_signals.get(d, []):
            if sym == symbol and act == action and ent and abs(entry - ent) / max(entry, ent) <= COOLDOWN_ENTRY_TOL_PCT:
                return True
    return False


# ---------------------------------------------------------------- inti backtest
def run_backtest(pairs: List[str], days: int, step_min: int, verbose: bool = False, filter_fn: Optional[callable] = None) -> Dict:
    started = time.time()
    fg_value = 50.0  # Fear&Greed historis tak tersedia -> netral (bobot sentiment kecil)

    stats = {
        "pairs": len(pairs),
        "bars_scanned": 0,
        "generated": 0,       # sinyal berarah sebelum cooldown
        "cooldown_skipped": 0,
        "concurrent_skipped": 0,
        "filtered": 0,        # ditolak filter_fn (mis. EMA alignment / BUY-only)
        "evaluated": 0,       # sinyal yang benar-benar dibuka & dievaluasi
        "wins": 0,
        "losses": 0,
        "floating": 0,
        "tp1": 0,
        "tp2": 0,
        "sl": 0,
        "by_direction": {"BUY": {"w": 0, "l": 0}, "SELL": {"w": 0, "l": 0}},
        "by_pair": {},
        "by_conf": {},       # bucket konfidensi -> {"w","l","f"}
        "errors": 0,
    }

    daily_signals: Dict[str, List[Tuple]] = {}   # day_key -> [(symbol, action, entry)]
    active: Dict[str, Tuple] = {}                # symbol -> (resolve_ts,)

    for idx, pair in enumerate(pairs, 1):
        base = pair.replace("USDT", "")
        if verbose:
            print(f"  [{idx}/{len(pairs)}] {pair}: fetch data...", flush=True)
        try:
            data = fetch_pair(pair, _EVAL_START_MS, days)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{idx}/{len(pairs)}] {pair}: fetch gagal ({exc}) — dilewati", flush=True)
            stats["errors"] += 1
            continue

        m15 = data["m15"]
        if len(m15) < 120:
            print(f"  [{idx}/{len(pairs)}] {pair}: data M15 kurang ({len(m15)} bar) — dilewati", flush=True)
            continue

        # Sinyal hanya dibangkitkan hingga `now - EVAL_MAX_HOURS` agar setiap posisi
        # punya jendela evaluasi maju PENUH (bukan terpotong di ujung jendela).
        eval_end = min(data["end"], _EVAL_NOW_MS - int(EVAL_MAX_HOURS * HOUR_MS))
        eval_bars = [c for c in m15 if data["start"] <= c["ts"] < eval_end]
        if step_min > MIN15_MS:
            # sampling: hanya bar yang kelipatan step (mis. tiap 60 menit)
            step = step_min // MIN15_MS
            eval_bars = eval_bars[::step]

        prev_ts = 0
        for bar in eval_bars:
            ts = bar["ts"]
            if ts < prev_ts:
                continue  # bar belum tertutup (duplikat ts)
            prev_ts = ts
            stats["bars_scanned"] += 1

            # buka posisi baru? tunggu posisi sebelumnya pada pair ini selesai.
            act_open = active.get(pair)
            if act_open and ts <= act_open:
                continue

            m15_now = up_to(m15, ts, 200)
            h1_now = up_to(data["h1"], ts, 120)
            h4_now = up_to(data["h4"], ts, 120)
            d1_now = up_to(data["d1"], ts, 90)
            if len(m15_now) < 60 or len(h1_now) < 10 or not h4_now or not d1_now:
                continue
            price = m15_now[-1]["close"]
            close_24h = m15_now[-96]["close"] if len(m15_now) >= 96 else m15_now[0]["close"]
            pct_24h = (price / close_24h - 1.0) * 100.0 if close_24h else 0.0

            try:
                sig = assemble_signal(
                    symbol=pair,
                    base=base,
                    price=price,
                    pct_change_24h=pct_24h,
                    h4_candles=h4_now,
                    d1_candles=d1_now,
                    h1_candles=h1_now,
                    m15_candles=m15_now,
                    fg_value=fg_value,
                    funding_rates=[],
                    ls_ratio=None,
                    whale_flow=None,
                    btc_stats=None,
                )
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                if verbose:
                    print(f"    {pair} {ts}: engine error {exc!r}", flush=True)
                continue

            if sig.action not in (ACTION_BUY, ACTION_SELL):
                continue
            stats["generated"] += 1

            # Filter volatilitas (Fix 14-Aug-2026, mencerminkan bot.py): koin
            # dengan ATR H1 relatif melebihi MAX_ATR_REL dilewati (0 = mati).
            if MAX_ATR_REL > 0:
                atr = _atr(h1_now, 14) if h1_now else None
                atr_rel = atr / price if atr else 0.0
                if atr_rel > MAX_ATR_REL:
                    stats["filtered"] += 1
                    continue

            # anti sinyal berulang (cooldown re-entry level yang sama)
            day_key = datetime.fromtimestamp(ts / 1000, WIB).strftime("%Y-%m-%d")
            if _suppressed_by_cooldown(daily_signals, pair, sig.action, sig.entry, day_key):
                stats["cooldown_skipped"] += 1
                if verbose:
                    print(f"    {pair} {datetime.fromtimestamp(ts/1000, WIB):%m-%d %H:%M}: {sig.action} ditekan cooldown", flush=True)
                continue

            # filter tambahan (eksperimen tuning): mis. searah EMA20 H1 / BUY-only
            if filter_fn and not filter_fn(pair, base, sig, m15_now, h1_now):
                stats["filtered"] += 1
                continue

            # evaluasi maju candle-per-candle (urutan kronologis)
            future = after(m15, ts, EVAL_MAX_HOURS)
            sig_dict = {"action": sig.action, "sl": sig.sl, "tp1": sig.tp1, "tp2": sig.tp2}
            status, ref = evaluation._evaluate_candles(sig_dict, future)
            stats["evaluated"] += 1
            daily_signals.setdefault(day_key, []).append((pair, sig.action, sig.entry))

            bucket = (sig.confidence // 10) * 10
            cb = stats["by_conf"].setdefault(bucket, {"w": 0, "l": 0, "f": 0})

            if status == evaluation.STATUS_TP2:
                stats["wins"] += 1
                stats["tp2"] += 1
                cb["w"] += 1
                active[pair] = _resolve_ts(future, evaluation.STATUS_TP2, sig_dict)
            elif status == evaluation.STATUS_TP1:
                stats["wins"] += 1
                stats["tp1"] += 1
                cb["w"] += 1
                active[pair] = _resolve_ts(future, evaluation.STATUS_TP1, sig_dict)
            elif status == evaluation.STATUS_SL:
                stats["losses"] += 1
                stats["sl"] += 1
                cb["l"] += 1
                active[pair] = _resolve_ts(future, evaluation.STATUS_SL, sig_dict)
            else:
                stats["floating"] += 1
                cb["f"] += 1
                active[pair] = 0  # tidak ada resolusi dalam jendela -> tidak memblokir

            d = stats["by_direction"][sig.action]
            if status in (evaluation.STATUS_TP1, evaluation.STATUS_TP2):
                d["w"] += 1
            elif status == evaluation.STATUS_SL:
                d["l"] += 1
            bp = stats["by_pair"].setdefault(pair, {"w": 0, "l": 0, "f": 0})
            if status in (evaluation.STATUS_TP1, evaluation.STATUS_TP2):
                bp["w"] += 1
            elif status == evaluation.STATUS_SL:
                bp["l"] += 1
            else:
                bp["f"] += 1

            if verbose:
                print(
                    f"    {pair} {datetime.fromtimestamp(ts/1000, WIB):%m-%d %H:%M}: {sig.action} "
                    f"@ {sig.entry:.5f} SL {sig.sl:.5f} TP1 {sig.tp1:.5f} TP2 {sig.tp2:.5f} -> {status}",
                    flush=True,
                )

    stats["elapsed_s"] = round(time.time() - started, 1)
    return stats


def _resolve_ts(future: List[Dict], status: str, sig: Dict) -> int:
    """Waktu (ms) candle yang memicu resolusi — untuk larangan posisi ganda per pair."""
    for c in future:
        low, high = c.get("low"), c.get("high")
        if low is None or high is None:
            continue
        if sig["action"] == ACTION_BUY:
            sl_hit = low <= sig["sl"]
            if not sl_hit and status == evaluation.STATUS_TP2 and high >= sig["tp2"]:
                return c["ts"]
            if not sl_hit and status == evaluation.STATUS_TP1 and high >= sig["tp1"]:
                return c["ts"]
            if sl_hit:
                return c["ts"]
        else:
            sl_hit = high >= sig["sl"]
            if not sl_hit and status == evaluation.STATUS_TP2 and low <= sig["tp2"]:
                return c["ts"]
            if not sl_hit and status == evaluation.STATUS_TP1 and low <= sig["tp1"]:
                return c["ts"]
            if sl_hit:
                return c["ts"]
    return 0


# ---------------------------------------------------------------- laporan
def render_report(stats: Dict, days: int) -> str:
    decided = stats["wins"] + stats["losses"]
    win_rate = (stats["wins"] / decided * 100.0) if decided else 0.0
    lines = []
    lines.append("=" * 62)
    lines.append("BACKTEST SINYAL MTF — SIMULASI HISTORI BITGET")
    lines.append("=" * 62)
    lines.append(f"  Pasangan          : {stats['pairs']} pair")
    lines.append(f"  Jendela           : {days} hari terakhir")
    lines.append(f"  Bar M15 discan    : {stats['bars_scanned']:,}")
    lines.append(f"  Sinyal berarah    : {stats['generated']} (cooldown ditekan {stats['cooldown_skipped']}, "
                 f"filter {stats['filtered']}, posisi ganda {stats['concurrent_skipped']}, error {stats['errors']})")
    lines.append(f"  Dievaluasi        : {stats['evaluated']}")
    lines.append("-" * 62)
    lines.append(f"  WIN RATE          : {win_rate:.1f}%   ({stats['wins']} menang / {decided} selesai)")
    lines.append(f"  Total TP vs SL    : {stats['tp1'] + stats['tp2']} TP  vs  {stats['sl']} SL")
    lines.append(f"    TP1             : {stats['tp1']}")
    lines.append(f"    TP2             : {stats['tp2']}")
    lines.append(f"    SL              : {stats['sl']}")
    lines.append(f"    FLOATING        : {stats['floating']} (tak terselesaikan dalam {EVAL_MAX_HOURS}h)")
    lines.append("-" * 62)
    lines.append("  Per arah:")
    for side in (ACTION_BUY, ACTION_SELL):
        d = stats["by_direction"][side]
        tot = d["w"] + d["l"]
        wr = d["w"] / tot * 100.0 if tot else 0.0
        lines.append(f"    {side:<4} : {d['w']:>3} W / {d['l']:>3} L  ({wr:.1f}%)")
    lines.append("-" * 62)
    lines.append("  Per konfidensi (bucket):")
    for bucket in sorted(stats["by_conf"]):
        v = stats["by_conf"][bucket]
        tot = v["w"] + v["l"]
        wr = v["w"] / tot * 100.0 if tot else 0.0
        lines.append(f"    conf {bucket:>3}-{bucket + 9} : {v['w']:>3} W / {v['l']:>3} L  ({wr:.0f}%)  F={v['f']}")
    lines.append("-" * 62)
    lines.append("  Per pair (selesai > 0):")
    for pair, v in sorted(stats["by_pair"].items(), key=lambda kv: kv[1]["w"] + kv[1]["l"], reverse=True):
        tot = v["w"] + v["l"]
        wr = v["w"] / tot * 100.0 if tot else 0.0
        lines.append(f"    {pair:<12} : {v['w']:>3} W / {v['l']:>3} L  ({wr:.0f}%)  F={v['f']}")
    lines.append("-" * 62)
    lines.append(f"  Waktu eksekusi    : {stats['elapsed_s']} detik")
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def _pick_pairs(limit: int) -> List[str]:
    tickers = bitget.get_all_tickers_24h()
    cands = [p for p in tickers if bot._eligible_pair(p) and tickers[p]["quote_volume"] >= MIN_VOLUME_USD]
    cands.sort(key=lambda p: tickers[p]["quote_volume"], reverse=True)
    return cands[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest mesin sinyal MTF vs histori Bitget")
    parser.add_argument("--days", type=int, default=5, help="jendela backtest (hari), default 5")
    parser.add_argument("--pairs", type=int, default=15, help="jumlah pair terlikuid, default 15")
    parser.add_argument("--step", default="15m", choices=["15m", "30m", "1h", "4h"], help="frekuensi scan sinyal")
    parser.add_argument("--out", default=None, help="simpan laporan ke file")
    parser.add_argument("--verbose", action="store_true", help="cetak tiap sinyal")
    parser.add_argument("--offset-days", type=int, default=0,
                        help="geser jendela mundur N hari (validasi multi-jendela, default 0)")
    args = parser.parse_args()

    global _EVAL_START_MS, _EVAL_NOW_MS
    _EVAL_NOW_MS = int((datetime.now(WIB) - timedelta(days=args.offset_days)).timestamp() * 1000)
    _EVAL_START_MS = int((datetime.now(WIB) - timedelta(days=args.offset_days + args.days)).timestamp() * 1000)

    step_min = {"15m": MIN15_MS, "30m": 2 * MIN15_MS, "1h": HOUR_MS, "4h": 4 * HOUR_MS}[args.step]

    print(f"Memilih {args.pairs} pair terlikuid...", flush=True)
    pairs = _pick_pairs(args.pairs)
    if not pairs:
        print("Tidak ada pair kandidat — cek koneksi/Min volume.", file=sys.stderr)
        return 1
    print(f"Pair terpilih: {', '.join(pairs)}", flush=True)
    print(f"Backtest {args.days} hari, scan tiap {args.step}...", flush=True)

    stats = run_backtest(pairs, args.days, step_min, verbose=args.verbose)
    report = render_report(stats, args.days)
    print("\n" + report)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(f"\nLaporan tersimpan: {args.out}")

    decided = stats["wins"] + stats["losses"]
    win_rate = (stats["wins"] / decided * 100.0) if decided else 0.0
    return 0 if (decided >= 10 and win_rate >= 55.0) else 2


if __name__ == "__main__":
    sys.exit(main())
