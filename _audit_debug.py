"""Audit debug: replay sinyal 19:07 WIB 12-Aug-2026 (PENGU/ETH/LINK) dengan data Bitget.

Replay tanpa look-ahead:
  - candle M15/H1/H4/D1 hanya yang SUDAH TUTUP sebelum 19:07 WIB (openTime+interval <= 19:07)
  - price = entry dari history.json (ticker live saat bot jalan)
  - lalu walk candle M15 dari 19:00 WIB -> 13 Aug 13:00 WIB untuk lihat path harga & SL.

Menjawab:
  1. Kenapa trigger M15 & filter EMA20 LOLOS menyuruh BUY padahal harga dump?
  2. Apakah jarak SL (ATR) cukup lebar untuk menahan noise saat itu?
  3. Kondisi BTC (regime) pada jendela yang sama (untuk filter trend induk).
"""

import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass

from data import bitget
from engine import (
    ACTION_BUY,
    _atr,
    _setup_valid,
    _trigger_valid,
    assemble_signal,
    analyze_compass,
    analyze_trigger,
    map_h1_zones,
    score_smc,
    score_sr,
)
from indicators.ema import analyze_ema
from indicators.smc import detect_structure

WIB = timezone(timedelta(hours=7))
SIGNAL = datetime(2026, 8, 12, 19, 7, tzinfo=WIB)
WINDOW_END = datetime(2026, 8, 13, 13, 0, tzinfo=WIB)

SIGNALS = {
    "PENGUUSDT": {"base": "PENGU", "action": "BUY", "entry": 0.006419, "sl": 0.006309877, "tp1": 0.0064953861, "tp2": 0.0065717722},
    "ETHUSDT": {"base": "ETH", "action": "BUY", "entry": 1917.36, "sl": 1884.76488, "tp1": 1940.1765839999998, "tp2": 1962.9931679999997},
    "LINKUSDT": {"base": "LINK", "action": "BUY", "entry": 8.878, "sl": 8.715774, "tp1": 8.9915582, "tp2": 9.1051164},
}

INTERVAL_MS = {bitget.INTERVAL_M15: 15 * 60_000, bitget.INTERVAL_1H: 60 * 60_000,
               bitget.INTERVAL_4H: 4 * 60 * 60_000, bitget.INTERVAL_1D: 24 * 60 * 60_000}


def wib(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=WIB).strftime("%m-%d %H:%M")


def completed_before(candles, cutoff: datetime) -> list:
    cutoff_ms = int(cutoff.timestamp() * 1000)
    return [c for c in candles if c["ts"] + 0 <= cutoff_ms]


def slice_by_interval(candles, interval: str, cutoff: datetime) -> list:
    dur = INTERVAL_MS[interval]
    cutoff_ms = int(cutoff.timestamp() * 1000)
    return [c for c in candles if c["ts"] + dur <= cutoff_ms]


def pct_24h_before(m15_before: list, price: float) -> float:
    if len(m15_before) < 96:
        return 0.0
    base = m15_before[-96]["close"]
    return (price / base - 1.0) * 100.0 if base else 0.0


def fetch(pair: str, interval: str, limit: int) -> list:
    return bitget.get_klines(pair, interval, limit)


def load_pair(pair: str):
    m15 = fetch(pair, bitget.INTERVAL_M15, 700)
    h1 = fetch(pair, bitget.INTERVAL_1H, 130)
    h4 = fetch(pair, bitget.INTERVAL_4H, 130)
    d1 = fetch(pair, bitget.INTERVAL_1D, 95)
    return {
        bitget.INTERVAL_M15: slice_by_interval(m15, bitget.INTERVAL_M15, SIGNAL),
        bitget.INTERVAL_1H: slice_by_interval(h1, bitget.INTERVAL_1H, SIGNAL),
        bitget.INTERVAL_4H: slice_by_interval(h4, bitget.INTERVAL_4H, SIGNAL),
        bitget.INTERVAL_1D: slice_by_interval(d1, bitget.INTERVAL_1D, SIGNAL),
        "m15_after": [c for c in m15 if c["ts"] >= int(SIGNAL.timestamp() * 1000) and c["ts"] < int(WINDOW_END.timestamp() * 1000)],
    }


def walk_sl_tp(sig: dict, candles: list):
    """Semantik evaluation._evaluate_candles: candle berurutan, TP menang bila tersentuh
    di candle yang tak menyentuh SL di candle-candle sebelumnya."""
    hits = []
    action = sig["action"]
    for c in candles:
        low, high = c["low"], c["high"]
        if action == ACTION_BUY:
            sl_touched = low <= sig["sl"]
            if not sl_touched and high >= sig["tp2"]:
                hits.append(("TP2", c, None)); break
            if not sl_touched and high >= sig["tp1"]:
                hits.append(("TP1", c, None)); break
            if sl_touched:
                hits.append(("SL", c, low)); break
        else:
            sl_touched = high >= sig["sl"]
            if not sl_touched and low <= sig["tp2"]:
                hits.append(("TP2", c, None)); break
            if not sl_touched and low <= sig["tp1"]:
                hits.append(("TP1", c, None)); break
            if sl_touched:
                hits.append(("SL", c, high)); break
    if not hits:
        return None
    status, candle, touch = hits[0]
    return status, candle, touch


def report_pair(pair: str, data: dict):
    info = SIGNALS[pair]
    m15_before = data[bitget.INTERVAL_M15]
    h1 = data[bitget.INTERVAL_1H]
    h4 = data[bitget.INTERVAL_4H]
    d1 = data[bitget.INTERVAL_1D]
    price = info["entry"]
    pct = pct_24h_before(m15_before, price)

    print("=" * 78)
    print(f"### {pair} — replay 12-Aug 19:07 WIB (entry {price}, 24h {pct:+.2f}%)")
    print(f"    candles: M15 {len(m15_before)} (sd {wib(m15_before[-1]['ts'])}), "
          f"H1 {len(h1)} (sd {wib(h1[-1]['ts'])}), H4 {len(h4)}, D1 {len(d1)}")

    sig = assemble_signal(
        symbol=pair, base=info["base"], price=price, pct_change_24h=pct,
        h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15_before,
        fg_value=50.0, funding_rates=[], ls_ratio=None, whale_flow=None, btc_stats=None,
    )

    compass = analyze_compass(h4, d1, price=price)
    trigger = analyze_trigger(m15_before)
    ema_info = analyze_ema(h1 or h4, price)

    print(f"\n  [Kompas] direction={compass['direction']} H4={compass['h4_trend']} "
          f"D1={compass['d1_trend']} ema50_blocked={compass['ema50_blocked']}")
    print(f"  [EMA20 H1] ema20={ema_info['ema_fast']:.6g} ema50={ema_info['ema_slow']:.6g} "
          f"trend={ema_info['trend']} dist={ema_info['dist_fast_pct']:.3f}%")
    print(f"  [Trigger M15] rsi={trigger['rsi']:.1f} hist={trigger['histogram']:+.3e} "
          f"cross={trigger['cross']} trend={trigger['trend']} bos={trigger['bos']} choch={trigger['choch']}")
    print(f"     hist_confirm_bull={trigger['hist_confirm_bull']} hist_confirm_bear={trigger['hist_confirm_bear']}")
    print(f"     trigger_valid(BUY)={_trigger_valid(trigger, ACTION_BUY)}")

    h1_map = map_h1_zones(h1, price)
    setup = _setup_valid(compass["direction"], h1_map, trigger)
    print(f"  [_setup_valid]={setup}  (zone_ok dan trigger searah kompas)")
    if not setup:
        in_demand = [z for z in h1_map["demand_zones"] if z["low"] * 0.99 <= price <= z["high"] * 1.01]
        print(f"     in_demand_zone={len(in_demand)} bullish_ob={bool(h1_map.get('bullish_ob'))} "
              f"sell_sweep={sum(1 for s in h1_map['sweeps'] if s['type'] == 'sell_sweep')}")

    ema_aligned = ema_info["ema_fast"] is None or price > ema_info["ema_fast"]
    print(f"  [Filter EMA20] price>EMA20(H1)={ema_aligned} (EMA20={ema_info['ema_fast']:.6g}, price={price})")
    print(f"  [Aksi engine] action={sig.action} skor={sig.total_score:+.3f} "
          f"breakdown={sig.breakdown}")
    for r in sig.reasons:
        print(f"     - {r}")

    # ---- SL / ATR / RR ----
    atr = _atr(h1, 14)
    sl_min = max(0.017 * price, 1.2 * atr) if atr else 0.017 * price
    hist_dist = (price - info["sl"]) / price * 100.0
    print(f"\n  [SL analysis] ATR14(H1)={atr:.6g} -> 1.2*ATR={(1.2 * atr if atr else 0):.6g} "
          f"({(1.2 * atr / price * 100 if atr else 0):.2f}%) | floor 1.7% | SL min={sl_min:.6g} "
          f"({sl_min / price * 100:.2f}%)")
    print(f"  [SL history] SL={info['sl']} ({hist_dist:.2f}%) | SL engine={sig.sl} "
          f"({(price - sig.sl) / price * 100:.2f}%)")
    print(f"  [RRR] risk={hist_dist:.2f}% reward TP1={(info['tp1'] - price) / price * 100:.2f}% "
          f"(x{(info['tp1'] - price) / (price - info['sl']):.2f}) TP2={(info['tp2'] - price) / price * 100:.2f}% "
          f"(x{(info['tp2'] - price) / (price - info['sl']):.2f})")

    # ---- walk harga setelah sinyal ----
    after = data["m15_after"]
    print(f"\n  [Harga setelah 19:07 WIB] {len(after)} candle M15 (19:07 -> 13 Aug 13:00 WIB)")
    first = after[0] if after else None
    last = after[-1] if after else None
    lows = [c["low"] for c in after]
    highs = [c["high"] for c in after]
    if lows:
        i_min = lows.index(min(lows))
        print(f"  Low terendah: {min(lows):.6g} ({wib(after[i_min]['ts'])}) "
              f"= {(min(lows) / price - 1) * 100:+.2f}% dari entry")
        print(f"  High tertinggi: {max(highs):.6g} ({wib(after[highs.index(max(highs))]['ts'])}) "
              f"= {(max(highs) / price - 1) * 100:+.2f}% dari entry")
        if last:
            print(f"  Close terakhir ({wib(last['ts'])}): {last['close']:.6g} "
                  f"({(last['close'] / price - 1) * 100:+.2f}%)")
    result = walk_sl_tp(info, after)
    if result:
        status, candle, touch = result
        print(f"  >>> Hasil evaluasi: {status} di candle {wib(candle['ts'])} "
              f"(low={candle['low']:.6g}, high={candle['high']:.6g})")
    else:
        print("  >>> Tidak tersentuh SL/TP (Floating).")
    print()


def report_btc():
    m15 = fetch("BTCUSDT", bitget.INTERVAL_M15, 700)
    h1 = fetch("BTCUSDT", bitget.INTERVAL_1H, 130)
    m15_before = slice_by_interval(m15, bitget.INTERVAL_M15, SIGNAL)
    h1_before = slice_by_interval(h1, bitget.INTERVAL_1H, SIGNAL)
    after = [c for c in m15 if c["ts"] >= int(SIGNAL.timestamp() * 1000) and c["ts"] < int(WINDOW_END.timestamp() * 1000)]

    price_before = m15_before[-1]["close"]
    print("=" * 78)
    print("### BTCUSDT — regime induk (replay 12-Aug 19:07 WIB)")
    struct = detect_structure(m15_before)
    ema_info = analyze_ema(h1_before, price_before)
    print(f"  [M15] trend={struct['trend']} bos={struct['bos']} choch={struct['choch']} "
          f"last_swing_low={struct.get('last_swing_low')} price={price_before}")
    print(f"  [H1] EMA20={ema_info['ema_fast']:.6g} EMA50={ema_info['ema_slow']:.6g} "
          f"trend={ema_info['trend']} price={price_before} "
          f"({(price_before / ema_info['ema_fast'] - 1) * 100:+.2f}% vs EMA20)")
    if after:
        lows = [c["low"] for c in after]
        highs = [c["high"] for c in after]
        i_min = lows.index(min(lows))
        print(f"  [Path 19:07 -> 13 Aug 13:00] low terendah {min(lows):.6g} "
              f"({wib(after[i_min]['ts'])}, {(min(lows) / price_before - 1) * 100:+.2f}%), "
              f"high tertinggi {max(highs):.6g} ({(max(highs) / price_before - 1) * 100:+.2f}%), "
              f"close terakhir {after[-1]['close']:.6g} ({(after[-1]['close'] / price_before - 1) * 100:+.2f}%)")
        print(f"  [20 bar pertama setelah 19:07]")
        for c in after[:20]:
            print(f"     {wib(c['ts'])}  O={c['open']:.6g} H={c['high']:.6g} L={c['low']:.6g} C={c['close']:.6g}")
    print()


if __name__ == "__main__":
    report_btc()
    for pair in SIGNALS:
        report_pair(pair, load_pair(pair))