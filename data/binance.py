"""Data pasar Binance: klines, ticker 24j, funding rate, long/short ratio.

Memakai endpoint publik `https://data-api.binance.vision` yang tidak geo-block,
sehingga aman dipakai di GitHub Actions (runner AS).
"""

from typing import Any, Dict, List, Optional

from config import BINANCE_BASE_URL, BINANCE_FUTURES_URL
from data._client import DataSourceError, http_get_json

# Interval kline untuk analisa Multi-Timeframe (MTF):
#   - Kompas (arah utama): D1 & H4
#   - Pemetaan (zona institusional): H1
#   - Pelatuk (konfirmasi eksekusi): M15
INTERVAL_M15 = "15m"
INTERVAL_1H = "1h"
INTERVAL_4H = "4h"
INTERVAL_1D = "1d"

# Limit default tiap timeframe (cukup untuk RSI/MACD/structure/swing di tiap lapis).
MTF_LIMITS = {
    INTERVAL_1D: 90,
    INTERVAL_4H: 120,
    INTERVAL_1H: 120,
    INTERVAL_M15: 200,
}

def _klines(symbol: str, interval: str, limit: int, start_time: Optional[int] = None) -> List[Dict[str, float]]:
    """Fetch kline Binance dan ubah menjadi list dict {open, high, low, close, volume}.

    `start_time` (ms epoch) opsional: kembalikan kline dengan openTime >= start_time
    (untuk evaluasi presisi: harga hanya dari SETELAH sesi sinyal, bukan 24j rolling).
    """
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = int(start_time)
    rows = http_get_json(url, params, source="binance")
    out: List[Dict[str, float]] = []
    for row in rows:
        try:
            out.append(
                {
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return out


def get_klines(symbol: str, interval: str = INTERVAL_1D, limit: int = 60) -> List[Dict[str, float]]:
    """Candle OHLCV untuk satu pair. Default daily (60 bar = ~2 bulan)."""
    return _klines(symbol, interval, limit)


def get_klines_since(symbol: str, interval: str, since, limit: int = 1000) -> List[Dict[str, float]]:
    """Candle OHLCV mulai `since` (datetime WIB/aware) sampai sekarang.

    Dipakai evaluasi sinyal sesi sebelumnya: high/low dihitung HANYA dari candle
    dengan openTime >= waktu sesi sinyal, bukan dari ticker 24j rolling (yang
    bisa mencakup pergerakan harga SEBELUM entry).

    Fix #4: TIDAK mundur satu interval. Binance mengembalikan candle pertama
    dengan openTime >= startTime, sehingga candle yang memuat waktu entry ikut
    dihitung bila `since` tepat di batas interval (entry terjadi di awal candle
    itu) — tanpa mengikutsertakan aksi harga pra-entry dari candle sebelumnya.
    """
    start_ms = int(since.timestamp() * 1000)
    return _klines(symbol, interval, limit, start_time=start_ms)


def get_klines_multi(symbol: str, limits: Optional[Dict[str, int]] = None) -> Dict[str, List[Dict[str, float]]]:
    """Klines multi-timeframe (Day Trading MTF): D1/H4 (kompas), H1 (pemetaan), M15 (pelatuk).

    Return {interval: [candle, ...]}. Satu sumber gagal -> interval itu tidak ada.
    """
    limits = limits or MTF_LIMITS
    out: Dict[str, List[Dict[str, float]]] = {}
    for interval, limit in limits.items():
        out[interval] = _klines(symbol, interval, limit)
    return out


def get_ticker_24h(symbol: str) -> Optional[Dict[str, Any]]:
    """Ticker 24 jam: last price, % change, volume, quote volume."""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"
    data = http_get_json(url, {"symbol": symbol}, source="binance")
    try:
        return {
            "symbol": data["symbol"],
            "price": float(data["lastPrice"]),
            "price_change_pct_24h": float(data["priceChangePercent"]),
            "volume": float(data["volume"]),
            "quote_volume": float(data["quoteVolume"]),
            "high_24h": float(data["highPrice"]),
            "low_24h": float(data["lowPrice"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise DataSourceError(f"[binance] Ticker {symbol} tidak valid: {exc}") from exc


def get_all_tickers_24h() -> Dict[str, Dict[str, Any]]:
    """Ticker 24j untuk SEMUA pasangan dalam SATU panggilan (hemat weight API).

    Return {symbol: {'price', 'price_change_pct_24h', 'quote_volume', 'volume'}}.
    """
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"
    rows = http_get_json(url, source="binance")
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            symbol = row["symbol"]
            if not symbol.endswith("USDT"):
                continue
            out[symbol] = {
                "price": float(row["lastPrice"]),
                "price_change_pct_24h": float(row["priceChangePercent"]),
                "quote_volume": float(row["quoteVolume"]),
                "volume": float(row["volume"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_funding_rate(symbol: str, limit: int = 8) -> List[float]:
    """Riwayat funding rate terakhir (per 8 jam untuk perpetual USDT).

    Endpoint futures Binance bisa diblokir region tertentu — bila gagal,
    kembalikan [] agar skor sentiment memakai sumber lain (Fear & Greed).
    """
    url = f"{BINANCE_FUTURES_URL}/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}
    try:
        data = http_get_json(url, params, source="binance-futures", timeout=8, retries=1)
    except DataSourceError:
        return []
    rates: List[float] = []
    for row in data:
        try:
            rates.append(float(row["fundingRate"]))
        except (TypeError, ValueError, KeyError):
            continue
    return rates


def get_long_short_ratio(symbol: str, limit: int = 1) -> Optional[float]:
    """Rasio akun long/short di posisi (data terbaru). >1 = lebih banyak long."""
    url = f"{BINANCE_FUTURES_URL}/futures/data/globalLongShortAccountRatio"
    try:
        data = http_get_json(url, {"symbol": symbol, "period": "5m", "limit": limit}, source="binance-futures", timeout=8, retries=1)
        if data:
            return float(data[-1]["longShortRatio"])
    except (DataSourceError, KeyError, TypeError, ValueError, IndexError):
        return None
    return None
