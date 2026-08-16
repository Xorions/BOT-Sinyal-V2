"""Entry point bot sinyal trading v2 — Day Trading Multi-Timeframe (MTF SMC + S&D).

Alur: ambil ticker 24j Bitget (1 panggilan) → tentukan top coin (CMC bila
dikonfigurasi, else fallback) → kumpulkan klines MTF (D1/H4 kompas, H1 pemetaan,
M15 pelatuk) + funding/LS tiap coin → agregat sentiment/whale/on-chain → skoring
MTF berbobot → evaluasi sinyal sesi sebelumnya → kirim Telegram.

Uji lokal tanpa Telegram:  python bot.py
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# Encoding eksplisit UTF-8 (Fix mojibake 16-Aug-2026): pastikan seluruh teks
# (stdout/stderr, file, subproses) diproses sebagai UTF-8 agar emoji seperti
# 📊 🏆 💰 🎯 🛡️ ⏳ 📋 tidak terdistorsi menjadi karakter aneh.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass

from config import (
    BTC_REGIME_ENABLED,
    COOLDOWN_ENTRY_TOL_PCT,
    COOLDOWN_SESSIONS,
    MAX_ATR_REL,
    MIN_VOLUME_USD,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TOP_COINS,
)
from data import bitget
from data import cmc
from data._client import DataSourceError
from data.onchain import get_btc_stats, get_exchange_flow_eth
from data.sentiment import get_fear_greed_current
from engine import (
    ACTION_BUY,
    ACTION_SELL,
    _atr,
    assemble_signal,
    btc_regime,
    format_message,
    rank_signals,
)
from evaluation import add_signals_today, build_recap, load_history
from telegram_sender import TelegramSendError, send_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("signal-bot-v2")

# WIB = UTC+7 — dipakai untuk timestamp briefing & key tanggal riwayat evaluasi,
# agar konsisten antara mesin lokal dan runner GitHub Actions (UTC).
WIB = timezone(timedelta(hours=7))

# Aset yang di-skip (bukan koin kripto yang valid untuk sinyal)
STABLECOINS = {
    "usdt", "usdc", "dai", "busd", "tusd", "usdd", "usde", "pyusd",
    "fdusd", "eurs", "eurc", "eurt", "euroc", "euri", "eur", "ust",
    "ustc", "usdp", "gusd", "usdx", "susde", "usdm", "usdn", "usd1",
    "usd0", "usda", "usdt0", "usdy", "usdl", "lusd", "susd", "xsusd",
    "rlusd", "xusd", "frax", "bfusd", "eurit",
}
# Bitget leveraged token (BTCUP/BTCDOWN/BTCBULL/BTCBEAR) — bukan aset riil.
# Wajib lowercase karena `base` sudah di-lowercase sebelum dicek endswith.
SKIP_SUFFIXES = ("up", "down", "bull", "bear")
# Token saham/ETF Bitget (Bitget Shares) — base = ticker US, atau ticker US + "B"
# (mis. NVDAB -> NVDA, QQQB -> QQQ, SPYB -> SPY). MUB/BB sudah bertipe langsung.
US_STOCK_TICKERS = {
    "aapl", "amzn", "googl", "goog", "meta", "msft", "nvda", "tsla", "avgo",
    "aaoi", "alab", "amat", "amd", "arm", "asml", "asts", "axti", "baba", "be",
    "bmnr", "cbrs", "cohr", "coin", "crcl", "crdo", "crwv", "dell", "dram", "ewy",
    "flnc", "glw", "gs", "hood", "ibm", "intc", "intw", "iren", "koru", "lite",
    "mrvl", "mstr", "muu", "mub", "mvll", "nbis", "nflx", "nok", "orcl", "pltr",
    "pypl", "qcom", "qqq", "rklb", "skhy", "smci", "smh", "sndk", "snxx", "soxl",
    "soxs", "spcx", "spy", "tqqq", "tsm", "usar", "wdc",
}


def _is_stock_token(base: str) -> bool:
    if base in US_STOCK_TICKERS:
        return True
    if base.endswith("b") and base[:-1] in US_STOCK_TICKERS:
        return True
    return False


def _eligible_pair(pair: str) -> bool:
    base = pair[:-4].lower() if pair.endswith("USDT") else ""
    if not base:
        return False
    if not base.isascii():
        return False
    if base in STABLECOINS:
        return False
    if base.endswith(SKIP_SUFFIXES):
        return False
    if _is_stock_token(base):
        return False
    return True


def _pick_pairs(tickers: Dict[str, Dict]) -> List[str]:
    """Pilih pasangan kandidat: CMC top bila ada, else semua USDT Bitget.
    Filter stablecoin/leveraged token + likuiditas min, urut volume, ambil TOP_COINS."""
    cmc_symbols = cmc.get_top_symbols(TOP_COINS)
    if cmc_symbols:
        wanted = [
            f"{base.upper().replace('USDT', '')}USDT"
            for base in cmc_symbols
            if f"{base.upper().replace('USDT', '')}USDT" in tickers
        ]
        log.info("Daftar top coin dari CoinMarketCap (%d symbol, %d tersedia di Bitget).", len(cmc_symbols), len(wanted))
    else:
        wanted = list(tickers.keys())
        log.info("CMC tidak dikonfigurasi — pakai semua pasangan USDT Bitget (%d).", len(wanted))

    candidates: List[str] = []
    for pair in wanted:
        if not _eligible_pair(pair):
            continue
        info = tickers.get(pair)
        if not info or info["price"] <= 0:
            continue
        if info["quote_volume"] < MIN_VOLUME_USD:
            continue
        candidates.append(pair)

    candidates.sort(key=lambda p: tickers[p]["quote_volume"], reverse=True)
    return candidates[:min(TOP_COINS, 250)]


def _fetch_candidate(pair: str, tickers: Dict[str, Dict], fg_value: float, whale_flow: Optional[Dict], btc_stats: Optional[Dict], futures_ok: bool, btc_regime_info: Optional[Dict] = None):
    info = tickers[pair]
    # MTF Day Trading: kompas (D1/H4), pemetaan zona (H1), pelatuk (M15)
    d1 = bitget.get_klines(pair, bitget.INTERVAL_1D, bitget.MTF_LIMITS[bitget.INTERVAL_1D])
    h4 = bitget.get_klines(pair, bitget.INTERVAL_4H, bitget.MTF_LIMITS[bitget.INTERVAL_4H])
    h1 = bitget.get_klines(pair, bitget.INTERVAL_1H, bitget.MTF_LIMITS[bitget.INTERVAL_1H])
    m15 = bitget.get_klines(pair, bitget.INTERVAL_M15, bitget.MTF_LIMITS[bitget.INTERVAL_M15])
    # Filter volatilitas (Fix 14-Aug-2026): koin dengan ATR H1 relatif terlalu
    # tinggi dilewati — SL min 1.7% rawan tersapu noise sebelum TP (backtest:
    # token ber-ATR rendah 73-100% WR vs altcoin volatil 38-50%).
    if MAX_ATR_REL > 0:
        atr = _atr(h1, 14) if h1 else None
        atr_rel = atr / info["price"] if atr else 0.0
        if atr_rel > MAX_ATR_REL:
            raise DataSourceError(
                f"{pair}: ATR H1 {atr_rel:.2%} > MAX_ATR_REL {MAX_ATR_REL:.2%} — koin terlalu volatil"
            )
    funding = bitget.get_funding_rate(pair, 8) if futures_ok else []
    ls_ratio = bitget.get_long_short_ratio(pair, 1) if futures_ok else None
    return assemble_signal(
        symbol=pair,
        base=pair.replace("USDT", ""),
        price=info["price"],
        pct_change_24h=info["price_change_pct_24h"],
        h4_candles=h4,
        d1_candles=d1,
        h1_candles=h1,
        m15_candles=m15,
        fg_value=fg_value,
        funding_rates=funding,
        ls_ratio=ls_ratio,
        whale_flow=whale_flow,
        btc_stats=btc_stats,
        btc_regime_info=btc_regime_info,
    )


def _ticker_range(pair: str) -> Optional[tuple]:
    """Fallback (high_24h, low_24h, current) dari ticker 24j; None bila gagal."""
    ticker = bitget.get_ticker_24h(pair)
    if not ticker:
        return None
    return ticker["high_24h"], ticker["low_24h"], ticker["price"]


def _range_since(pair: str, since=None) -> Optional[list]:
    """Candle M15 SETELAH sesi sinyal (kronologis) untuk evaluasi sinyal sebelumnya.

    Bila `since` (datetime WIB-aware) diberikan, candle diambil dari
    `get_klines_since` — evaluasi SL/TP dihitung candle-per-candle dalam urutan
    waktu (Fix R4), bukan agregat high/low yang tidak bisa membedakan urutan
    TP vs SL. Fallback ke ticker 24j (`_ticker_range`) menjadi satu candle
    sintetis bila klines sejak-sesi gagal / tidak tersedia.
    """
    if since is not None:
        try:
            candles = bitget.get_klines_since(pair, bitget.INTERVAL_M15, since)
        except DataSourceError:
            candles = []
        if candles:
            return candles
    ticker = bitget.get_ticker_24h(pair)
    if not ticker:
        return None
    return [
        {"high": ticker["high_24h"], "low": ticker["low_24h"], "close": ticker["price"]}
    ]


def _recent_directional(history: Dict, n: int = COOLDOWN_SESSIONS) -> List[tuple]:
    """Sinyal berarah (BUY/SELL) pada `n` sesi terakhir: [(base, action, entry), ...].

    Dipakai anti re-entry: kalau base + arah + entry nyaris sama sudah pernah
    disinyalkan di sesi-sesi terakhir, sinyal yang sama tidak ditampilkan lagi.
    """
    recent: List[tuple] = []
    for key in sorted(history, reverse=True)[:n]:
        for s in history[key]:
            if s.get("action") in (ACTION_BUY, ACTION_SELL) and s.get("entry"):
                recent.append((s["base"].lower(), s["action"], s["entry"]))
    return recent


def _apply_cooldown(signals: List, history: Dict) -> List:
    """Turunkan sinyal yang mengulang setup sesi sebelumnya jadi NEUTRAL.

    Fix R4 (anti sinyal berulang): bot sebelumnya bisa menyuruh masuk ke koin
    yang sama di level yang hampir identik berulang sesi (contoh UTK 0.00795
    selama 7 sesi). Bila base + arah cocok dan entry beda <=
    COOLDOWN_ENTRY_TOL_PCT dari sinyal sesi-sesi terakhir, sinyal dilewati
    (tetap muncul di WATCHLIST agar tidak hilang total).
    """
    if not history:
        return signals
    recent = _recent_directional(history)
    if not recent:
        return signals
    out = []
    for s in signals:
        if s.action in (ACTION_BUY, ACTION_SELL) and s.entry:
            for prev_base, prev_action, prev_entry in recent:
                if not prev_entry:
                    continue
                if (
                    s.base.lower() == prev_base
                    and s.action == prev_action
                    and abs(s.entry - prev_entry) / max(s.entry, prev_entry) <= COOLDOWN_ENTRY_TOL_PCT
                ):
                    intended = s.action
                    s.action = "NEUTRAL"
                    s.reasons.append(
                        f"[Cooldown] {intended} yang sama (entry ≈{prev_entry}) sudah disinyalkan "
                        f"di sesi sebelumnya — dilewati (anti re-entry level yang sama)"
                    )
                    break
        out.append(s)
    return out


def run_scan() -> tuple:
    log.info("Mengambil ticker 24j Bitget...")
    try:
        tickers = bitget.get_all_tickers_24h()
    except DataSourceError:
        log.warning("Gagal pertama mengambil ticker Bitget — coba ulang sekali...")
        time.sleep(5)
        tickers = bitget.get_all_tickers_24h()
    log.info("Tersedia %d pasangan USDT di Bitget.", len(tickers))

    pairs = _pick_pairs(tickers)
    log.info("Dianalisa %d koin (MTF: kompas H4/D1 -> zona H1 -> pelatuk M15).", len(pairs))
    if not pairs:
        raise DataSourceError("Tidak ada pasangan kandidat — periksa MIN_VOLUME_USD / koneksi.")

    try:
        fg_value = get_fear_greed_current()
    except DataSourceError:
        fg_value = None
        log.warning("Fear & Greed error, pakai default 50.")
    if fg_value is None:
        fg_value = 50.0
        log.warning("Fear & Greed gagal diambil, pakai default 50.")

    futures_ok = bool(bitget.get_funding_rate("BTCUSDT", limit=1))
    if futures_ok:
        log.info("Bitget futures terjangkau — funding & L/S ratio aktif.")
    else:
        log.warning("Bitget futures tidak terjangkau — skor sentiment memakai Fear & Greed saja.")

    eth_price = tickers.get("ETHUSDT", {}).get("price")
    whale_flow = None
    if eth_price:
        try:
            whale_flow = get_exchange_flow_eth(float(eth_price))
            if whale_flow is not None:
                log.info("Whale netflow ETH: %s", whale_flow)
            else:
                log.warning("Whale flow kosong (cek ETHERSCAN_API_KEY).")
        except DataSourceError as exc:
            log.warning("Whale flow dilewati: %s", exc)
    else:
        log.warning("ETH price tidak tersedia — skor whale dilewati.")

    btc_stats = get_btc_stats()
    if btc_stats:
        log.info("BTC stats: %s", btc_stats)
    else:
        log.warning("BTC on-chain stats tidak tersedia — skor onchain dilewati.")

    # Filter Trend Induk: regime BTC dihitung SEKALI per scan (hemat panggilan
    # API), dipakai menolak BUY altcoin saat BTC bearish. Gagal -> None (filter
    # dilewati, graceful degradation — jangan jadikan BTC wajib).
    btc_regime_info = None
    if BTC_REGIME_ENABLED:
        try:
            btc_price = tickers.get("BTCUSDT", {}).get("price")
            btc_regime_info = btc_regime(
                price=btc_price,
                m15_candles=bitget.get_klines("BTCUSDT", bitget.INTERVAL_M15, bitget.MTF_LIMITS[bitget.INTERVAL_M15]),
                h1_candles=bitget.get_klines("BTCUSDT", bitget.INTERVAL_1H, bitget.MTF_LIMITS[bitget.INTERVAL_1H]),
                h4_candles=bitget.get_klines("BTCUSDT", bitget.INTERVAL_4H, bitget.MTF_LIMITS[bitget.INTERVAL_4H]),
                d1_candles=bitget.get_klines("BTCUSDT", bitget.INTERVAL_1D, bitget.MTF_LIMITS[bitget.INTERVAL_1D]),
            )
            log.info("BTC regime: %s", btc_regime_info.get("regime"))
        except DataSourceError as exc:
            btc_regime_info = None
            log.warning("BTC regime dilewati: %s", exc)

    signals = []
    for i, pair in enumerate(pairs, start=1):
        try:
            sig = _fetch_candidate(pair, tickers, fg_value, whale_flow, btc_stats, futures_ok, btc_regime_info)
            signals.append(sig)
            log.info("[%d/%d] %s -> %s (skor %+.2f)", i, len(pairs), sig.symbol, sig.action, sig.total_score)
        except DataSourceError as exc:
            log.warning("[%d/%d] %s dilewati: %s", i, len(pairs), pair, exc)

    signals = _apply_cooldown(signals, load_history())
    ranked = rank_signals(signals)
    log.info("Terpilih %d sinyal terbaik.", len(ranked))

    now = datetime.now(WIB)
    timestamp = now.strftime("%A, %d %b %Y, %H:%M WIB")
    market_note = f"Fear&Greed: {fg_value:.0f}"
    if whale_flow is not None:
        market_note += f" · Whale net ETH ${whale_flow['net_usd'] / 1e6:+.1f}M"

    # Evaluasi sinyal sesi sebelumnya → pesan terpisah (History Review).
    recap = build_recap(load_history(), _range_since)
    briefing = format_message(ranked, timestamp, market_note)
    return recap, briefing, ranked, timestamp


def main() -> int:
    try:
        recap, briefing, ranked, timestamp = run_scan()
    except Exception as exc:  # noqa: BLE001 - tampilkan error apa pun agar terlihat di CI
        log.error("Gagal menjalankan scan: %s", exc)
        return 1

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Kredensial belum diisi di .env - hasil hanya ditampilkan di konsol.")
        print("\n" + ((recap + "\n" + briefing) if recap else briefing) + "\n")
        add_signals_today(ranked, timestamp)
        return 0

    try:
        if recap:
            send_telegram(recap)
            log.info("Pesan evaluasi sesi sebelumnya terkirim ke Telegram.")
        send_telegram(briefing)
        log.info("Pesan briefing terkirim ke Telegram.")
        add_signals_today(ranked, timestamp)
        return 0
    except TelegramSendError as exc:
        log.error("Gagal mengirim: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
