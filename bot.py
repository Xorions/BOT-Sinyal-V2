"""Entry point bot sinyal trading v2.

Alur: ambil ticker 24j Binance (1 panggilan) → tentukan top coin (CMC bila
dikonfigurasi, else fallback) → kumpulkan klines/funding/LS tiap coin →
agregat data sentiment/whale/on-chain → skoring berbobot → kirim Telegram.

Uji lokal tanpa Telegram:  python bot.py
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

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
    MIN_VOLUME_USD,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TOP_COINS,
)
from data import binance
from data import cmc
from data._client import DataSourceError
from data.onchain import get_btc_stats, get_exchange_flow_eth
from data.sentiment import get_fear_greed_current
from engine import assemble_signal, format_message, rank_signals
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
# Binance leveraged token (BTCUP/BTCDOWN/BTCBULL/BTCBEAR) — bukan aset riil.
# Wajib lowercase karena `base` sudah di-lowercase sebelum dicek endswith.
SKIP_SUFFIXES = ("up", "down", "bull", "bear")
# Token saham/ETF Binance (Binance Shares) — base = ticker US, atau ticker US + "B"
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
    """Pilih pasangan kandidat: CMC top bila ada, else semua USDT Binance.
    Filter stablecoin/leveraged token + likuiditas min, urut volume, ambil TOP_COINS."""
    cmc_symbols = cmc.get_top_symbols(TOP_COINS)
    if cmc_symbols:
        wanted = [
            f"{base.upper().replace('USDT', '')}USDT"
            for base in cmc_symbols
            if f"{base.upper().replace('USDT', '')}USDT" in tickers
        ]
        log.info("Daftar top coin dari CoinMarketCap (%d symbol, %d tersedia di Binance).", len(cmc_symbols), len(wanted))
    else:
        wanted = list(tickers.keys())
        log.info("CMC tidak dikonfigurasi — pakai semua pasangan USDT Binance (%d).", len(wanted))

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


def _fetch_candidate(pair: str, tickers: Dict[str, Dict], fg_value: float, whale_flow: Optional[Dict], btc_stats: Optional[Dict], futures_ok: bool):
    info = tickers[pair]
    closes = [c["close"] for c in binance.get_klines(pair, binance.INTERVAL_1D, 60)]
    candles = binance.get_klines(pair, binance.INTERVAL_4H, 60)
    funding = binance.get_funding_rate(pair, 8) if futures_ok else []
    ls_ratio = binance.get_long_short_ratio(pair, 1) if futures_ok else None
    return assemble_signal(
        symbol=pair,
        base=pair.replace("USDT", ""),
        price=info["price"],
        pct_change_24h=info["price_change_pct_24h"],
        closes=closes,
        candles=candles,
        fg_value=fg_value,
        funding_rates=funding,
        ls_ratio=ls_ratio,
        whale_flow=whale_flow,
        btc_stats=btc_stats,
    )


def _ticker_range(pair: str) -> Optional[tuple]:
    """(high_24h, low_24h, current) untuk evaluasi sinyal kemarin; None bila gagal."""
    ticker = binance.get_ticker_24h(pair)
    if not ticker:
        return None
    return ticker["high_24h"], ticker["low_24h"], ticker["price"]


def run_scan() -> tuple:
    log.info("Mengambil ticker 24j Binance...")
    tickers = binance.get_all_tickers_24h()
    log.info("Tersedia %d pasangan USDT di Binance.", len(tickers))

    pairs = _pick_pairs(tickers)
    log.info("Dianalisa %d koin (top oleh volume, min volume 24j $%.1fM).", len(pairs), MIN_VOLUME_USD / 1e6)
    if not pairs:
        raise DataSourceError("Tidak ada pasangan kandidat — periksa MIN_VOLUME_USD / koneksi.")

    fg_value = get_fear_greed_current()
    if fg_value is None:
        fg_value = 50.0
        log.warning("Fear & Greed gagal diambil, pakai default 50.")

    futures_ok = bool(binance.get_funding_rate("BTCUSDT", limit=1))
    if futures_ok:
        log.info("Binance futures terjangkau — funding & L/S ratio aktif.")
    else:
        log.warning("Binance futures tidak terjangkau — skor sentiment memakai Fear & Greed saja.")

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

    signals = []
    for i, pair in enumerate(pairs, start=1):
        try:
            sig = _fetch_candidate(pair, tickers, fg_value, whale_flow, btc_stats, futures_ok)
            signals.append(sig)
            log.info("[%d/%d] %s -> %s (skor %+.2f)", i, len(pairs), sig.symbol, sig.action, sig.total_score)
        except DataSourceError as exc:
            log.warning("[%d/%d] %s dilewati: %s", i, len(pairs), pair, exc)

    ranked = rank_signals(signals)
    log.info("Terpilih %d sinyal terbaik.", len(ranked))

    now = datetime.now(WIB)
    timestamp = now.strftime("%A, %d %b %Y, %H:%M WIB")
    market_note = f"Fear&Greed: {fg_value:.0f}"
    if whale_flow is not None:
        market_note += f" · Whale net ETH ${whale_flow['net_usd'] / 1e6:+.1f}M"

    # Evaluasi sinyal kemarin → disisipkan tepat sebelum blok DAILY BRIEFING.
    recap = build_recap(load_history(), _ticker_range)
    briefing = format_message(ranked, timestamp, market_note)
    message = (recap + "\n" + briefing) if recap else briefing
    return message, ranked, timestamp


def main() -> int:
    try:
        message, ranked, timestamp = run_scan()
    except Exception as exc:  # noqa: BLE001 - tampilkan error apa pun agar terlihat di CI
        log.error("Gagal menjalankan scan: %s", exc)
        return 1

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Kredensial belum diisi di .env - hasil hanya ditampilkan di konsol.")
        print("\n" + message + "\n")
        add_signals_today(ranked, timestamp)
        return 0

    try:
        send_telegram(message)
        log.info("Pesan terkirim ke Telegram.")
        add_signals_today(ranked, timestamp)
        return 0
    except TelegramSendError as exc:
        log.error("Gagal mengirim: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
