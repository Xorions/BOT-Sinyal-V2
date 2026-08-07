"""Evaluasi Sinyal Kemarin (Daily Recap) + penyimpanan riwayat sinyal.

- `add_signals_today()` mencatat sinyal terpilih hari ini ke `data/history.json`
  (key = tanggal WIB `YYYY-MM-DD`). Dipakai sebagai data evaluasi esok hari.
- `build_recap()` dipanggil SEBELUM Daily Briefing baru dikirim: membaca riwayat
  hari sebelumnya, mengambil harga 24j terakhir tiap pair (high/low/current) dari
  Binance, menentukan status tiap sinyal (TP2 / TP1 / SL / Floating), menghitung
  win rate harian, lalu menyusun teks ringkasan yang disisipkan tepat sebelum
  blok DAILY BRIEFING.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from engine import Signal, _fmt_price

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
    STATUS_TP1: "✅",
    STATUS_SL: "❌",
    STATUS_FLOATING: "⏳",
}


def wib_now() -> datetime:
    return datetime.now(WIB)


def today_str() -> str:
    return wib_now().strftime("%Y-%m-%d")


def _display_date(date_key: str) -> str:
    try:
        return datetime.strptime(date_key, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return date_key


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


def add_signals_today(signals: List[Signal], timestamp: str, path: str = HISTORY_PATH) -> None:
    """Simpan sinyal terpilih hari ini (menimpa entri tanggal yang sama)."""
    date = today_str()
    history = load_history(path)
    history[date] = [
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
    ]
    save_history(history, path)


def previous_day_signals(history: Dict[str, List[Dict]], today: Optional[str] = None):
    """Ambil sinyal pada hari terakhir sebelum hari ini (tahan hari yang dilewati)."""
    today = today or today_str()
    older = sorted(d for d in history if d < today)
    if not older:
        return None, []
    date = older[-1]
    return date, history[date]


# ---------------------------------------------------------------- evaluasi
def evaluate_signal(sig: Dict, high: float, low: float, current: float) -> str:
    """Status sinyal berdasar high/low/current 24j terakhir.

    Urutan cek: TP2 → TP1 → SL → Floating (TP diperiksa lebih dulu).
    current dipakai untuk sinyal yang masih berjalan (floating).
    """
    action = sig.get("action")
    if action == "BUY":
        if high >= sig["tp2"]:
            return STATUS_TP2
        if high >= sig["tp1"]:
            return STATUS_TP1
        if low <= sig["sl"]:
            return STATUS_SL
    elif action == "SELL":
        if low <= sig["tp2"]:
            return STATUS_TP2
        if low <= sig["tp1"]:
            return STATUS_TP1
        if high >= sig["sl"]:
            return STATUS_SL
    return STATUS_FLOATING


def evaluate_signals(signals: List[Dict], fetch_fn) -> List[Dict]:
    """Isi 'status' tiap sinyal. fetch_fn(pair) -> (high, low, current) atau None."""
    out: List[Dict] = []
    for sig in signals:
        try:
            high_low_cur = fetch_fn(sig["symbol"])
        except Exception:
            high_low_cur = None
        if not high_low_cur:
            out.append({**sig, "status": None})
            continue
        high, low, current = high_low_cur
        out.append({**sig, "status": evaluate_signal(sig, high, low, current)})
    return out


# ---------------------------------------------------------------- recap
def build_recap(history: Dict[str, List[Dict]], fetch_fn, today: Optional[str] = None) -> Optional[str]:
    """Teks ringkasan evaluasi sinyal kemarin; None bila tak ada riwayat / semua gagal.

    Win rate = % sinyal yang menyentuh TP1/TP2 dari seluruh sinyal yang dievaluasi.
    """
    date, signals = previous_day_signals(history, today)
    if not signals:
        return None
    results = evaluate_signals(signals, fetch_fn)
    evaluated = [r for r in results if r["status"]]
    if not evaluated:
        return None

    tp2 = sum(1 for r in evaluated if r["status"] == STATUS_TP2)
    tp1 = sum(1 for r in evaluated if r["status"] == STATUS_TP1)
    sl = sum(1 for r in evaluated if r["status"] == STATUS_SL)
    floating = sum(1 for r in evaluated if r["status"] == STATUS_FLOATING)
    won = tp2 + tp1
    total = len(evaluated)
    win_rate = round(won / total * 100) if total else 0

    lines = [
        f"<b>📊 EVALUASI SINYAL KEMARIN — {_display_date(date)}</b>",
        f"🏆 Win rate: <b>{win_rate}%</b> ({won}/{total})  ·  🎯 TP2: {tp2} · ✅ TP1: {tp1} · ❌ SL: {sl} · ⏳ Floating: {floating}",
        "",
    ]
    for r in evaluated:
        emoji = STATUS_EMOJI[r["status"]]
        lines.append(
            f"#{r['base']} {r['action']} · Entry {_fmt_price(r['entry'])} → {emoji} <b>{r['status']}</b>"
        )
    lines.append("")
    return "\n".join(lines)
