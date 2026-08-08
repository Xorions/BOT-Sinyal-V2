"""Konfigurasi bot v2: memuat kredensial & parameter skoring dari .env"""

import os
from dotenv import load_dotenv

load_dotenv()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Sumber data ---
# Endpoint publik Binance (tidak geo-block, aman dipakai GitHub Actions runner AS)
BINANCE_BASE_URL: str = os.getenv("BINANCE_BASE_URL", "https://data-api.binance.vision")

# Futures Binance (funding rate / long-short ratio). Opsional — dapat
# diblokir region tertentu; bila gagal, skor sentiment memakai Fear & Greed saja.
BINANCE_FUTURES_URL: str = os.getenv("BINANCE_FUTURES_URL", "https://fapi.binance.com")

# Opsional: CoinMarketCap free key (banyak keterbatasan di tier free)
CMC_API_KEY: str = os.getenv("CMC_API_KEY", "")
CMC_BASE_URL: str = os.getenv("CMC_BASE_URL", "https://pro-api.coinmarketcap.com/v1")

# Opsional: Etherscan/BscScan free key (untuk whale transfer proxy)
ETHERSCAN_API_KEY: str = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY: str = os.getenv("BSCSCAN_API_KEY", "")

# --- Cakupan scan ---
# Maks 250 koin (kapasitas listing CMC & batas aman waktu eksekusi CI).
TOP_COINS: int = _env_int("TOP_COINS", 250)
TOP_SIGNALS: int = _env_int("TOP_SIGNALS", 5)
MIN_VOLUME_USD: float = _env_float("MIN_VOLUME_USD", 1_000_000)

# --- Ambang sinyal ---
BUY_THRESHOLD: float = _env_float("BUY_THRESHOLD", 0.10)
SELL_THRESHOLD: float = _env_float("SELL_THRESHOLD", -0.10)
CONFIDENCE_BASE: int = _env_int("CONFIDENCE_BASE", 55)

# --- Risk-to-Reward Ratio (RRR) level SL/TP ---
# TP1 minimal RRR_MIN x jarak SL (default 1:1.5); TP2 proyeksi RRR_TP2 x jarak SL.
RRR_MIN: float = _env_float("RRR_MIN", 1.5)
RRR_TP2: float = _env_float("RRR_TP2", 3.0)
# Buffer SL di luar zona Demand/Supply terdekat (0.3% = 0.003).
SL_BUFFER_PCT: float = _env_float("SL_BUFFER_PCT", 0.003)

# Jarak SL MINIMAL dari Entry (dalam % harga) — SL yang terlalu dekat (<1%)
# rawan tersapu noise pasar (contoh VIRTUAL -0.63%, DASH -0.84%). Bila zona
# memberi SL lebih dekat dari batas ini, SL dipaksa menjauh ke batas minimum.
SL_MIN_DIST_PCT: float = _env_float("SL_MIN_DIST_PCT", 0.015)
# Pengali ATR(H1) untuk jarak SL dinamis: jarak SL = max(SL_MIN_DIST_PCT,
# SL_ATR_MULT * ATR/price) sehingga koin volatil dapat SL lebih lebar.
SL_ATR_MULT: float = _env_float("SL_ATR_MULT", 1.0)

# --- Bobot kategori skoring (jumlah harus 1.0) ---
# Prioritas day trading MTF: SMC + S&D (kompas H4/D1 + zona H1) paling besar.
WEIGHT_TECHNICAL: float = _env_float("WEIGHT_TECHNICAL", 0.20)
WEIGHT_SMC: float = _env_float("WEIGHT_SMC", 0.40)
WEIGHT_SENTIMENT: float = _env_float("WEIGHT_SENTIMENT", 0.15)
WEIGHT_WHALE: float = _env_float("WEIGHT_WHALE", 0.15)
WEIGHT_ONCHAIN: float = _env_float("WEIGHT_ONCHAIN", 0.10)

# Batas maksimum skor kategori "sentimen" (Fear&Greed + funding + L/S).
# Sentimen adalah bias pasar (regime), bukan penentu arah: tanpa cap, kondisi
# Fear(30)+funding negatif+L/S rendah bisa memberi +0.9 seragam ke SEMUA koin
# sehingga setup bearish yang jelas pun gagal jadi SELL. Cap membuat arah
# ditentukan oleh setup SMC/teknikal, sentimen hanya sebagai pelengkap.
SENTIMENT_MAX: float = _env_float("SENTIMENT_MAX", 0.5)

# --- Ambang data on-chain (whale transfer, dalam USD) ---
WHALE_MIN_USD: float = _env_float("WHALE_MIN_USD", 5_000_000)
WHALE_LOOKBACK_HOURS: int = _env_int("WHALE_LOOKBACK_HOURS", 24)

# --- Fear & Greed ---
FEAR_GREED_URL: str = "https://api.alternative.me/fng/"
FEAR_GREED_LOOKBACK: int = _env_int("FEAR_GREED_LOOKBACK", 7)

# --- HTTP ---
REQUEST_TIMEOUT: int = _env_int("REQUEST_TIMEOUT", 30)
REQUEST_RETRIES: int = _env_int("REQUEST_RETRIES", 3)

DISCLAIMER: str = (
    "⚠️ Disclaimer: Sinyal berbasis indikator otomatis & data publik. "
    "Bukan saran finansial. Selalu lakukan riset sendiri (DYOR)."
)
