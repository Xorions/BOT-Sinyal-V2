"""Evaluasi Sinyal Sesi Sebelumnya (Daily Recap) + penyimpanan riwayat sinyal.

- `add_signals_today()` mencatat sinyal terpilih pada sesi ini ke `data/history.json`
  (key = timestamp sesi WIB `YYYY-MM-DD HH:MM`). Dipakai sebagai data evaluasi
  sesi berikutnya (2x sehari: 13:30 & 19:00 WIB).
- `build_recap()` dipanggil SEBELUM Day Trading Briefing baru dikirim: membaca
  riwayat sesi sebelumnya (termasuk sesi pagi yang sama), mengambil harga 24j
  terakhir tiap pair (high/low/current) dari Bitget, menentukan status tiap
  sinyal (TP2 / TP1 / SL / Floating), menghitung win rate, lalu menyusun teks
  ringkasan evaluasi yang dikirim sebagai pesan Telegram terpisah (History Review).
- Pemisahan sesi (Fix 15-Aug-2026): rekap sesi berjalan HANYA mengevaluasi
  sinyal dari sesi SEBELUMNYA + antrean Carry-Over FLOATING — sinyal yang baru
  saja dibuat pada sesi berjalan (belum berumur MIN_SESSION_AGE_HOURS) tidak
  dievaluasi prematur, dan sinyal FLOATING sesi sebelumnya otomatis dibawa
  (carry-over) sampai benar-benar menyentuh TP1/TP2/SL — TANPA batas waktu
  (Fix 16-Aug-2026: tidak ada lagi auto-expire EXPIRED setelah EVAL_MAX_HOURS).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from engine import ACTION_BUY, ACTION_SELL, Signal, _esc, _fmt_price

HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_PATH = os.path.join(HISTORY_DIR, "history.json")

# WIB = UTC+7 (bot & CI memakai zona ini agar tanggal riwayat konsisten)
WIB = timezone(timedelta(hours=7))

STATUS_TP2 = "TP2"
STATUS_TP1 = "TP1"
STATUS_SL = "SL"
STATUS_FLOATING = "FLOATING"
STATUS_EXPIRED = "EXPIRED"

STATUS_EMOJI = {
    STATUS_TP2: "🎯",
    STATUS_TP1: "💰",
    STATUS_SL: "🛡️",
    STATUS_FLOATING: "⏳",
    STATUS_EXPIRED: "⏰",
}

STATUS_LABEL = {
    STATUS_TP2: "TP2",
    STATUS_TP1: "TP1",
    STATUS_SL: "SL",
    STATUS_FLOATING: "FLOATING",
    STATUS_EXPIRED: "EXPIRED",
}

# Umur minimal sesi (jam) sebelum boleh dievaluasi (Pemisahan Sesi, Fix 15-Aug-2026):
# sinyal yang BARU SAJA dibuat pada sesi berjalan (mis. 19:06) belum punya
# umur/progress harga — dilarang dievaluasi prematur oleh rekap sesi yang sama.
# Lebih kecil dari jarak antar sesi (13:30 -> 19:00 = 5,5 jam), jadi sesi
# sebelumnya selalu lolos; lebih besar dari re-run dalam sesi berjalan.
MIN_SESSION_AGE_HOURS: float = 3.0


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

    Fix pemisahan sesi: sesi yang belum berumur MIN_SESSION_AGE_HOURS (masih
    bagian dari sesi berjalan, sinyalnya baru dibuat) dianggap belum "sesi
    sebelumnya" dan dilewati.
    """
    now_key = now_key or session_now_str()
    now_dt = _session_since(now_key) or wib_now()
    older = sorted(k for k in history if k < now_key)
    for date in reversed(older):
        since = _session_since(date)
        if since is None or not _is_mature(since, now_dt):
            continue
        return date, history[date]
    return None, []


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
def _is_mature(since: Optional[datetime], now_dt: Optional[datetime] = None, min_age_hours: float = MIN_SESSION_AGE_HOURS) -> bool:
    """True bila sesi sudah cukup umur untuk dievaluasi (min MIN_SESSION_AGE_HOURS).

    Sinyal yang BARU SAJA dibuat pada sesi berjalan belum punya umur/progress
    harga — dilarang dievaluasi prematur oleh rekap sesi yang sama. Sesi dengan
    kunci tak dikenal (since=None) dianggap belum mature.
    """
    if since is None:
        return False
    now_dt = now_dt or wib_now()
    return now_dt - since >= timedelta(hours=min_age_hours)


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
    ts}) atau None. `since` = waktu sesi sinyal (WIB-aware); diteruskan ke
    fetch_fn agar high/low hanya dihitung dari candle SETELAH sesi sinyal.

    Fix 16-Aug-2026 (hapus expiry 24 jam): TIDAK ada lagi auto-expire —
    sinyal FLOATING tidak pernah ditandai EXPIRED dan terus dievaluasi pada
    rekap-rekap berikutnya (carry-over) tanpa batas waktu, sampai harga
    benar-benar menyentuh TP1/TP2/SL. Seluruh candle yang dikembalikan
    fetch_fn ikut dievaluasi (tidak dipotong jendela EVAL_MAX_HOURS).
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
        status, ref = _evaluate_candles(sig, candles)
        out.append({**sig, "status": status, "ref": ref})
    return out


# ---------------------------------------------------------------- recap
def _age_str(age: timedelta) -> str:
    """Umur sinyal dalam teks pendek (mis. '5 jam', '1 hari 3 jam')."""
    total_min = max(0, int(age.total_seconds() // 60))
    hours = total_min // 60
    if hours < 1:
        return f"{total_min} menit"
    if hours < 48:
        return f"{hours} jam"
    return f"{hours // 24} hari {hours % 24} jam"


def _signal_lines(r: Dict, age_str: str = "") -> List[str]:
    """Baris detail satu sinyal (dipakai seksi utama & carry-over)."""
    status = r["status"]
    label = STATUS_LABEL[status]
    ref = r.get("ref")
    ref_str = _esc(_fmt_price(ref)) if ref is not None else "n/a"
    pnl = _esc(_pnl_pct(r, ref))
    header = f"#{_esc(r['base'])} {r['action']}"
    if age_str:
        header += f" ({label} - {age_str})"
    lines = [header]
    lines.append(f"🔑 Entry {_esc(_fmt_price(r['entry']))} → {STATUS_EMOJI[status]} <b>{label}</b>")
    if status == STATUS_FLOATING:
        lines.append(f"📋 Harga saat ini {ref_str} ({pnl})")
    else:
        lines.append(f"📋 Hit {label} di {ref_str} ({pnl})")
    return lines


def _format_recap(date: str, evaluated: List[Dict], carryover: Optional[List[Dict]] = None, now: Optional[datetime] = None) -> str:
    """Susun teks recap dari daftar sinyal yang sudah dievaluasi.

    `carryover` (opsional): sinyal FLOATING dari sesi-sesi SEBELUM sesi utama
    yang dibawa (carry-over) untuk dievaluasi ulang sampai TP/SL tersentuh —
    ditampilkan sebagai seksi terpisah dengan umur sinyal tiap koin. Hanya
    sinyal berstatus FLOATING yang dibawa (Fix duplikasi): status final
    (TP1/TP2/SL) langsung keluar dari antrean carry-over.

    Fix continuity (15-Aug-2026): sinyal FLOATING dari sesi utama juga otomatis
    masuk antrean carry-over — seksi utama hanya menampilkan status final
    (TP2/TP1/SL), posisi aktif ditampilkan sekali di seksi CARRY-OVER.

    Fix no-expiry (16-Aug-2026): sinyal FLOATING dibawa TANPA batas waktu
    (tidak ada lagi EXPIRED setelah EVAL_MAX_HOURS) sampai harga benar-benar
    menyentuh TP1/TP2/SL.

    Format (Fix 16-Aug-2026):
    - Statistik horizontal 1 baris: `🏆 Win rate ... | 💰 TP1 | 🎯 TP2 | 🛡️ SL | ⏳ Floating`.
    - Seksi 1 `SEKSI 1 — SESI UTAMA`: status final sinyal sesi sebelumnya yang
      baru dievaluasi; bila tak ada sinyal selesai tampil catatan singkat.
    - Seksi 2 `SEKSI 2 — CARRY-OVER — POSISI AKTIF DARI SESI SEBELUMNYA`:
      posisi FLOATING yang dibawa antar sesi. Pembatas garis dipertahankan
      walau seksi utama kosong, agar carry-over selalu terpisah rapi.
    """
    tp2 = sum(1 for r in evaluated if r["status"] == STATUS_TP2)
    tp1 = sum(1 for r in evaluated if r["status"] == STATUS_TP1)
    sl = sum(1 for r in evaluated if r["status"] == STATUS_SL)
    floating = sum(1 for r in evaluated if r["status"] == STATUS_FLOATING)
    won = tp2 + tp1
    total = len(evaluated)
    win_rate = round(won / total * 100) if total else 0

    lines = [
        f"<b>📊 EVALUASI SINYAL SESI SEBELUMNYA — {_esc(_display_key(date))}</b>",
        f"🏆 Win rate: <b>{win_rate}%</b> ({won}/{total}) | 💰 TP1: {tp1} | 🎯 TP2: {tp2} | 🛡️ SL: {sl} | ⏳ Floating: {floating}",
    ]
    resolved = [r for r in evaluated if r["status"] != STATUS_FLOATING]

    # Seksi 1 — Sesi Utama: sinyal sesi sebelumnya yang baru di-evaluasi (status final).
    lines.append("━━━━━━━━━━━━")
    lines.append("📋 <b>SEKSI 1 — SESI UTAMA</b>")
    if resolved:
        for i, r in enumerate(resolved):
            lines.extend(_signal_lines(r))
            if i != len(resolved) - 1:
                lines.append("───")
    else:
        lines.append("Tidak ada sinyal selesai di sesi utama — semua posisi masih aktif.")

    # Seksi 2 — Carry-Over: posisi FLOATING yang dibawa dari sesi sebelumnya.
    if carryover:
        lines.append("━━━━━━━━━━━━")
        lines.append("⏳ <b>SEKSI 2 — CARRY-OVER — POSISI AKTIF DARI SESI SEBELUMNYA</b>")
        lines.append(f"🧾 {len(carryover)} sinyal ⏳ FLOATING dibawa dari sesi sebelumnya")
        for i, r in enumerate(carryover):
            age_str = ""
            if now is not None and r.get("session"):
                since = _session_since(r["session"])
                if since is not None:
                    age_str = _age_str(now - since)
            lines.extend(_signal_lines(r, age_str))
            if i != len(carryover) - 1:
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

    Carry-over: sinyal yang masih FLOATING di sesi-sesi sebelumnya TIDAK dihapus
    dari antrean evaluasi — ikut dievaluasi ulang di rekap ini sebagai seksi
    CARRY-OVER sampai benar-benar menyentuh TP1/TP2/SL. Fix 16-Aug-2026: TANPA
    batas waktu (auto-expire EXPIRED dihapus) — sinyal FLOATING terus di-carry-
    over tanpa batas waktu, berapa pun umurnya, hingga harga menyentuh TP1/TP2/SL.

    Fix pemisahan sesi & continuity (15-Aug-2026):
    - Pemisahan sesi: rekap sesi berjalan (mis. 19:06) HANYA mengevaluasi sesi
      yang sudah berumur MIN_SESSION_AGE_HOURS (sesi sebelumnya, mis. 13:35) —
      sinyal yang BARU SAJA dibuat pada sesi 19:06 tersebut TIDAK dievaluasi
      prematur (belum punya umur/progress harga).
    - Continuity: sinyal FLOATING dari sesi utama otomatis masuk antrean
      carry-over untuk sesi berikutnya selama belum tersentuh TP1/TP2/SL —
      posisi aktif tidak pernah hilang dari tracking antar sesi (tanpa batas
      waktu, Fix 16-Aug-2026).

    Fix duplikasi & penumpukan (14-Aug-2026):
    - Carry-over HANYA berisi sinyal berstatus FLOATING — sinyal yang sudah
      mencapai status final (TP1/TP2/SL) LANGSUNG keluar dari antrean
      di sesi berikutnya (tidak diseret berulang kali).
    - Deduplikasi per symbol/base: bila koin sama muncul di beberapa sesi
      (mis. RFXI 2 entry), hanya 1 sinyal TERBARU (sesi paling akhir) yang
      ditampilkan agar chat Telegram tidak penuh duplikasi.
    """
    now_key = now_key or today or session_now_str()
    now_dt = _session_since(now_key) or wib_now()
    older_keys = sorted((k for k in history if k < now_key), reverse=True)

    primary_date: Optional[str] = None
    primary_results: Optional[List[Dict]] = None
    carryover: List[Dict] = []
    # symbol(base) yang SUDAH tampil -> index di carryover (nilai < 0 = milik
    # sesi utama dengan status final, tidak boleh ditambahkan lagi ke carry-over).
    carry_seen: Dict[str, int] = {}

    for date in older_keys:
        signals = history[date]
        if not signals:
            continue
        since = _session_since(date)
        if since is None:
            continue
        # Pemisahan sesi: sinyal yang baru dibuat pada sesi berjalan belum punya
        # umur/progress harga — dilewati, baru dievaluasi pada sesi berikutnya.
        if not _is_mature(since, now_dt):
            continue
        # Fix 16-Aug-2026: tanpa batas waktu — sesi yang lebih lama tetap
        # dievaluasi & FLOATING-nya terus di-carry-over sampai TP1/TP2/SL.
        results = evaluate_signals(signals, fetch_fn, since=since)
        evaluated = [r for r in results if r["status"]]
        if not evaluated:
            continue
        if primary_results is None:
            primary_date = date
            primary_results = evaluated
            # Continuity: FLOATING dari sesi utama otomatis masuk antrean
            # carry-over (dibawa ke sesi berikutnya tanpa batas waktu sampai
            # TP1/TP2/SL tersentuh); status final langsung menutup posisi.
            for r in evaluated:
                if r["status"] == STATUS_FLOATING:
                    entry = {**r, "session": date}
                    carry_seen[entry["base"]] = len(carryover)
                    carryover.append(entry)
                else:
                    carry_seen.setdefault(r["base"], -1)
            continue
        for r in evaluated:
            # Strict filter: hanya FLOATING yang dibawa; status final dibuang.
            if r["status"] != STATUS_FLOATING:
                continue
            entry = {**r, "session": date}
            # Dedupe per symbol: simpan hanya yang TERBARU (older_keys diurut
            # terbalik, jadi kemunculan pertama = sesi paling akhir).
            if entry["base"] in carry_seen:
                continue
            carry_seen[entry["base"]] = len(carryover)
            carryover.append(entry)

    if primary_results is None:
        return None
    return _format_recap(primary_date, primary_results, carryover, now_dt)
