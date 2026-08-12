"""Evaluasi Sinyal Sesi Sebelumnya (Daily Recap) + penyimpanan riwayat sinyal.

- `add_signals_today()` mencatat sinyal terpilih pada sesi ini ke `data/history.json`
  (key = timestamp sesi WIB `YYYY-MM-DD HH:MM`). Dipakai sebagai data evaluasi
  sesi berikutnya (2x sehari: 13:30 & 19:00 WIB).
- `build_recap()` dipanggil SEBELUM Day Trading Briefing baru dikirim: membaca
  riwayat sesi sebelumnya (termasuk sesi pagi yang sama), mengambil harga 24j
  terakhir tiap pair (high/low/current) dari Bitget, menentukan status tiap
  sinyal (TP2 / TP1 / SL / Floating), menghitung win rate, lalu menyusun teks
  ringkasan evaluasi yang dikirim sebagai pesan Telegram terpisah (History Review).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import EVAL_MAX_HOURS
from engine import ACTION_BUY, ACTION_SELL, Signal, _esc, _fmt_price

HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_PATH = os.path.join(HISTORY_DIR, "history.json")

# WIB = UTC+7 (bot & CI memakai zona ini agar tanggal riwayat konsisten)
WIB = timezone(timedelta(hours=7))

STATUS_TP2 = "TP2"
STATUS_TP1 = "TP1"
STATUS_SL = "SL"
STATUS_FLOATING = "FLOATING"

STATUS_EMOJI = {
    STATUS_TP2: "🎯",
    STATUS_TP1: "💰",
    STATUS_SL: "🛡️",
    STATUS_FLOATING: "⏳",
}

STATUS_LABEL = {
    STATUS_TP2: "TP2",
    STATUS_TP1: "TP1",
    STATUS_SL: "SL",
    STATUS_FLOATING: "FLOATING",
}


def wib_now() -> datetime:
    return datetime.now(WIB)


def today_str() -> str:
    return wib_now().strftime("%Y-%m-%d")


def session_now_str() -> str:
    """Kunci sesi WIB `YYYY-MM-DD HH:MM` — unik per run (2x sehari)."""
    return wib_now().strftime("%Y-%m-%d %H:%M")


def _display_key(key: str) -> str:
    """Terima kunci sesi (`YYYY-MM-DD HH:MM`) atau kunci tanggal (`YYYY-MM-DD`)."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(key, fmt).strftime("%d %b %Y %H:%M")
        except ValueError:
            continue
    return key


# ---------------------------------------------------------------- riwayat
def load_history(path: str = HISTORY_PATH) -> Dict[str, List[Dict]]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_history(history: Dict[str, List[Dict]], path: str = HISTORY_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def add_signals_today(signals: List[Signal], timestamp: str, path: str = HISTORY_PATH, session_key: Optional[str] = None) -> None:
    """Simpan sinyal terpilih sesi ini (menimpa entri sesi yang sama).

    Hanya sinyal berarah (BUY/SELL) yang dicatat — NEUTRAL tidak disimpan agar
    tidak mencemari evaluasi win rate sesi berikutnya (NEUTRAL tak pernah jadi
    win, hanya menambah denominator Floating).
    """
    key = session_key or session_now_str()
    history = load_history(path)
    history[key] = [
        {
            "symbol": s.symbol,
            "base": s.base,
            "action": s.action,
            "entry": s.entry,
            "sl": s.sl,
            "tp1": s.tp1,
            "tp2": s.tp2,
            "timestamp": timestamp,
        }
        for s in signals
        if s.action in (ACTION_BUY, ACTION_SELL)
    ]
    save_history(history, path)


def previous_session_signals(history: Dict[str, List[Dict]], now_key: Optional[str] = None):
    """Sinyal pada sesi terakhir SEBELUM sesi sekarang (tahan hari yang dilewati).

    Kunci sesi (`YYYY-MM-DD HH:MM`) & kunci tanggal lama (`YYYY-MM-DD`) campur
    dibandingkan leksikografis — urutan waktu tetap benar.
    """
    now_key = now_key or session_now_str()
    older = sorted(k for k in history if k < now_key)
    if not older:
        return None, []
    date = older[-1]
    return date, history[date]


def _session_since(key: Optional[str]) -> Optional[datetime]:
    """Parse kunci sesi WIB (`YYYY-MM-DD HH:MM` atau `YYYY-MM-DD`) -> datetime WIB-aware.

    Dipakai sebagai `since` evaluasi: high/low dihitung HANYA dari candle setelah
    sesi sinyal, bukan ticker 24j rolling. Kunci tak dikenal -> None (fallback).
    """
    if not key:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(key, fmt).replace(tzinfo=WIB)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- evaluasi
def _within_window(candles: List[Dict], since: Optional[datetime], max_hours: float = EVAL_MAX_HOURS) -> List[Dict]:
    """Potong candle ke jendela `since`..`since+max_hours` (Fix R4).

    Candle tanpa `ts` (mis. fallback ticker 24j) selalu dipertahankan.
    Membatasi jendela evaluasi agar SL/TP yang tersentuh JAUH setelah sesi
    (bukan bagian dari niat day trade) tidak ikut dihitung.
    """
    if not since or not max_hours:
        return candles
    cutoff = since + timedelta(hours=max_hours)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    return [c for c in candles if c.get("ts") is None or c["ts"] <= cutoff_ms]


def _evaluate_candles(sig: Dict, candles: List[Dict]):
    """(status, harga acuan) berdasar urutan candle M15 (kronologis).

    Fix R4: sebelumnya evaluasi memakai agregat high/low seluruh jendela, jadi
    bila harga menyentuh TP1 DULUAN lalu SL belakangan (atau sebaliknya) urutan
    tak bisa dibedakan dan selalu dihitung SL (konservatif). Kini candle diwalk
    berurutan: posisi dianggap ditutup begitu TP1/TP2 tersentuh SEBELUM SL, dan
    SL hanya menang bila SL tersentuh di candle yang tak menyentuh TP di
    candle-candle sebelumnya. Dalam SATU candle yang sama menyentuh keduanya,
    urutan tetap tak bisa dipastikan -> SL (konservatif).
    """
    action = sig.get("action")
    if action == ACTION_BUY:
        for c in candles:
            low = c.get("low")
            high = c.get("high")
            if low is None or high is None:
                continue
            sl_touched = low <= sig["sl"]
            if not sl_touched and high >= sig["tp2"]:
                return STATUS_TP2, sig["tp2"]
            if not sl_touched and high >= sig["tp1"]:
                return STATUS_TP1, sig["tp1"]
            if sl_touched:
                return STATUS_SL, sig["sl"]
    elif action == ACTION_SELL:
        for c in candles:
            low = c.get("low")
            high = c.get("high")
            if low is None or high is None:
                continue
            sl_touched = high >= sig["sl"]
            if not sl_touched and low <= sig["tp2"]:
                return STATUS_TP2, sig["tp2"]
            if not sl_touched and low <= sig["tp1"]:
                return STATUS_TP1, sig["tp1"]
            if sl_touched:
                return STATUS_SL, sig["sl"]
    current = None
    for c in reversed(candles):
        if c.get("close") is not None:
            current = c["close"]
            break
    return STATUS_FLOATING, current


def _evaluate(sig: Dict, high: float, low: float, current: float):
    """(status, harga acuan) berdasar high/low/current agregat (satu candle).

    Dipakai `evaluate_signal` untuk satu jendela agregat; dalam satu jendela
    yang menyentuh SL DAN TP sekaligus urutannya tidak bisa dipastikan, jadi
    dicatat konservatif sebagai SL agar win rate tidak melebih-lebihkan.
    """
    return _evaluate_candles(sig, [{"high": high, "low": low, "close": current}])


def evaluate_signal(sig: Dict, high: float, low: float, current: float) -> str:
    """Status sinyal berdasar high/low/current 24j terakhir."""
    status, _ = _evaluate(sig, high, low, current)
    return status


def _pnl_pct(sig: Dict, ref: Optional[float]) -> str:
    """Persentase PnL dari Entry ke harga acuan, ber-tanda +/-."""
    if ref is None:
        return "n/a"
    entry = sig.get("entry")
    if not entry:
        return "n/a"
    if sig.get("action") == "SELL":
        pct = (entry - ref) / entry * 100.0
    else:
        pct = (ref - entry) / entry * 100.0
    return f"{pct:+.2f}%"


def evaluate_signals(signals: List[Dict], fetch_fn, since: Optional[datetime] = None) -> List[Dict]:
    """Isi 'status' + 'ref' (harga acuan) tiap sinyal.

    fetch_fn(pair, since) -> list candle M15 kronologis (dict {high, low, close,
    ts}) atau None. `since` = waktu sesi sinyal (WIB-aware); dipakai untuk
    membatasi jendela evaluasi (EVAL_MAX_HOURS) & diteruskan ke fetch_fn.
    """
    out: List[Dict] = []
    for sig in signals:
        if sig.get("action") not in (ACTION_BUY, ACTION_SELL):
            out.append({**sig, "status": None})
            continue
        try:
            candles = fetch_fn(sig["symbol"], since)
        except Exception:
            candles = None
        if not candles:
            out.append({**sig, "status": None})
            continue
        candles = _within_window(candles, since)
        status, ref = _evaluate_candles(sig, candles)
        out.append({**sig, "status": status, "ref": ref})
    return out


# ---------------------------------------------------------------- recap
def _format_recap(date: str, evaluated: List[Dict]) -> str:
    """Susun teks recap dari daftar sinyal yang sudah dievaluasi."""
    tp2 = sum(1 for r in evaluated if r["status"] == STATUS_TP2)
    tp1 = sum(1 for r in evaluated if r["status"] == STATUS_TP1)
    sl = sum(1 for r in evaluated if r["status"] == STATUS_SL)
    floating = sum(1 for r in evaluated if r["status"] == STATUS_FLOATING)
    won = tp2 + tp1
    total = len(evaluated)
    win_rate = round(won / total * 100) if total else 0

    lines = [
        f"<b>📊 EVALUASI SINYAL SESI SEBELUMNYA — {_esc(_display_key(date))}</b>",
        f"🏆 Win rate: <b>{win_rate}%</b> ({won}/{total})",
        f"💰 TP1: {tp1}",
        f"🎯 TP2: {tp2}",
        f"🛡️ SL: {sl}",
        f"⏳ Floating: {floating}",
        "━━━━━━━━━━━━",
    ]
    for i, r in enumerate(evaluated):
        status = r["status"]
        label = STATUS_LABEL[status]
        ref = r.get("ref")
        ref_str = _esc(_fmt_price(ref)) if ref is not None else "n/a"
        pnl = _esc(_pnl_pct(r, ref))
        lines.append(f"#{_esc(r['base'])} {r['action']}")
        lines.append(f"🔑 Entry {_esc(_fmt_price(r['entry']))} → {STATUS_EMOJI[status]} <b>{label}</b>")
        if status == STATUS_FLOATING:
            lines.append(f"📋 Harga saat ini {ref_str} ({pnl})")
        else:
            lines.append(f"📋 Hit {label} di {ref_str} ({pnl})")
        if i != len(evaluated) - 1:
            lines.append("───")
    lines.append("━━━━━━━━━━━━")
    return "\n".join(lines)


def build_recap(history: Dict[str, List[Dict]], fetch_fn, today: Optional[str] = None, now_key: Optional[str] = None) -> Optional[str]:
    """Teks ringkasan evaluasi sinyal sesi sebelumnya; None bila tak ada riwayat / semua gagal.

    Win rate = % sinyal yang menyentuh TP1/TP2 dari seluruh sinyal yang dievaluasi.
    `since` (dari kunci sesi) diteruskan ke `fetch_fn` agar high/low hanya dihitung
    dari candle SETELAH sesi sinyal — bukan 24j rolling yang bisa mencakup harga pra-entry.

    Fix #5: bila sesi terakhir tidak dapat dievaluasi (tanpa sinyal / semua fetch
    harga gagal), evaluasi MUNDUR ke sesi lebih lama yang masih valid — bukan
    menghentikan recap di sesi yang gagal.
    """
    now_key = now_key or today or session_now_str()
    older_keys = sorted((k for k in history if k < now_key), reverse=True)
    for date in older_keys:
        signals = history[date]
        if not signals:
            continue
        since = _session_since(date)
        results = evaluate_signals(signals, fetch_fn, since=since)
        evaluated = [r for r in results if r["status"]]
        if not evaluated:
            continue
        return _format_recap(date, evaluated)
    return None
