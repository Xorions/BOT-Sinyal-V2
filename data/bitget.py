"""Data pasar Bitget: klines, ticker 24j, funding rate, long/short ratio.

Memakai endpoint publik Bitget V2 (`https://api.bitget.com/api/v2/...`) tanpa
API key, sehingga aman dipakai di GitHub Actions runner AS:

  - Spot  : /api/v2/spot/market/candles  & /api/v2/spot/market/tickers
  - Futures: /api/v2/mix/market/candles  & /api/v2/mix/market/tickers
  - Funding: /api/v2/mix/market/history-fund-rate
  - L/S    : /api/v2/mix/market/account-long-short

Konvensi symbol (transparan, dipakai semua fungsi di modul ini):
  - Spot   : BTCUSDT
  - Futures: BTCUSDT_UMCBL (USDT-M Perpetual) — lihat `to_futures_symbol()`.

Semua fungsi mempertahankan kontrak output data lama (Binance):
kline -> dict {open, high, low, close, volume, ts} dengan `ts` dalam
milidetik & urutan kronologis (Bitget V2 mengembalikan terbaru-duluan;
di-sort menaik agar indikator / Kompas / Trigger M15 / evaluator
candle-per-candle tetap berjalan sempurna).
"""

from typing import Any, Dict, List, Optional

from config import BITGET_BASE_URL, BITGET_FUTURES_URL
from data._client import DataSourceError, http_get_json

# Interval kline untuk analisa Multi-Timeframe (MTF):
#   - Kompas (arah utama): D1 & H4
#   - Pemetaan (zona institusional): H1
#   - Pelatuk (konfirmasi eksekusi): M15
# Nama interval internal TETAP sama (15m/1h/4h/1d) — pemetaan ke nilai
# `granularity` Bitget (spot: 15min/1day; futures: 15m/1H/1D) terjadi
# transparan di `_granularity()`.
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

MARKET_SPOT = "spot"
MARKET_FUTURES = "futures"

# productType futures Bitget untuk pasar USDT-M Perpetual.
FUTURES_PRODUCT_TYPE = "USDT-FUTURES"
# Suffix symbol futures (USDT-M Perpetual).
FUTURES_SYMBOL_SUFFIX = "_UMCBL"

# Pemetaan interval internal -> nilai `granularity` Bitget per pasar.
# (Spot memakai 15min/1day; Mix memakai 15m/1H/1D.)
_GRANULARITY: Dict[str, Dict[str, str]] = {
    MARKET_SPOT: {
        INTERVAL_M15: "15min",
        INTERVAL_1H: "1h",
        INTERVAL_4H: "4h",
        INTERVAL_1D: "1day",
    },
    MARKET_FUTURES: {
        INTERVAL_M15: "15m",
        INTERVAL_1H: "1H",
        INTERVAL_4H: "4H",
        INTERVAL_1D: "1D",
    },
}

_KLINES_ENDPOINTS: Dict[str, str] = {
    MARKET_SPOT: "/api/v2/spot/market/candles",
    MARKET_FUTURES: "/api/v2/mix/market/candles",
}

_TICKERS_ENDPOINTS: Dict[str, str] = {
    MARKET_SPOT: "/api/v2/spot/market/tickers",
    MARKET_FUTURES: "/api/v2/mix/market/tickers",
}


def to_spot_symbol(symbol: str) -> str:
    """Normalisasi symbol menjadi format Spot Bitget (BTCUSDT)."""
    return symbol.upper().replace(FUTURES_SYMBOL_SUFFIX, "")


def to_futures_symbol(symbol: str) -> str:
    """Konversi symbol Spot/plain -> format Futures Bitget (BTCUSDT -> BTCUSDT_UMCBL).

    Representasi USDT-M Perpetual Bitget memakai suffix `_UMCBL` di semua
    endpoint `mix`. Symbol yang sudah ber-suffix tidak diubah.
    """
    symbol = to_spot_symbol(symbol)
    if symbol.endswith(FUTURES_SYMBOL_SUFFIX):
        return symbol
    return f"{symbol}{FUTURES_SYMBOL_SUFFIX}"


def _ok(data: Any, source: str = "bitget") -> List[Any]:
    """Validasi envelope Bitget V2: `code == "00000"` dan `data` berupa list."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise DataSourceError(f"[{source}] Respons tidak valid: {data}")
    if str(data.get("code", "00000")) != "00000":
        raise DataSourceError(f"[{source}] Error API ({data.get('code')}): {data.get('msg')}")
    return data["data"]


def _granularity(interval: str, market: str = MARKET_SPOT) -> str:
    """Nilai `granularity` Bitget V2 untuk interval internal & pasar tertentu."""
    try:
        return _GRANULARITY[market][interval]
    except KeyError as exc:
        raise DataSourceError(f"[bitget] Interval tidak didukung: {interval!r}") from exc


def _klines(
    symbol: str,
    interval: str,
    limit: int,
    start_time: Optional[int] = None,
    market: str = MARKET_SPOT,
) -> List[Dict[str, float]]:
    """Fetch kline Bitget V2 dan ubah menjadi list dict {open, high, low, close, volume}.

    `start_time` (ms epoch) opsional: hanya kline dengan openTime >= start_time
    yang dikembalikan (untuk evaluasi presisi: harga hanya dari SETELAH sesi
    sinyal, bukan 24j rolling). Bitget V2 membulatkan startTime ke bawah per
    granularity, jadi candle yang memuat `start_time` dicek ulang di sisi klien
    (sama dengan semantik `openTime >= startTime` Binance, Fix #4).
    """
    api_symbol = to_spot_symbol(symbol) if market == MARKET_SPOT else to_futures_symbol(symbol)
    url = f"{BITGET_BASE_URL}{_KLINES_ENDPOINTS[market]}"
    params: Dict[str, Any] = {
        "symbol": api_symbol,
        "granularity": _granularity(interval, market),
        "limit": int(limit),
    }
    if market == MARKET_FUTURES:
        params["productType"] = FUTURES_PRODUCT_TYPE
    if start_time is not None:
        params["startTime"] = int(start_time)
    rows = _ok(http_get_json(url, params, source="bitget" if market == MARKET_SPOT else "bitget-futures"))

    # Bitget V2: data = [[ts_ms, open, high, low, close, volume, quote_vol, ...], ...]
    # terbaru-duluan; internal memakai urutan kronologis menaik (konsisten Binance).
    out: List[Dict[str, float]] = []
    for row in sorted(rows, key=lambda r: int(r[0]) if isinstance(r[0], (int, str)) and str(r[0]).isdigit() else 0):
        try:
            ts = int(row[0])
            if start_time is not None and ts < int(start_time):
                continue
            out.append(
                {
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "ts": ts,
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return out


def get_klines(symbol: str, interval: str = INTERVAL_1D, limit: int = 60) -> List[Dict[str, float]]:
    """Candle OHLCV untuk satu pair (Spot). Default daily (60 bar = ~2 bulan)."""
    return _klines(symbol, interval, limit)


def get_klines_futures(symbol: str, interval: str = INTERVAL_1D, limit: int = 60) -> List[Dict[str, float]]:
    """Candle OHLCV dari pasar Futures USDT-M Bitget (routing transparan)."""
    return _klines(symbol, interval, limit, market=MARKET_FUTURES)


def get_klines_since(symbol: str, interval: str, since, limit: int = 1000) -> List[Dict[str, float]]:
    """Candle OHLCV mulai `since` (datetime WIB/aware) sampai sekarang (Spot).

    Dipakai evaluasi sinyal sesi sebelumnya: high/low dihitung HANYA dari candle
    dengan openTime >= waktu sesi sinyal, bukan dari ticker 24j rolling (yang
    bisa mencakup pergerakan harga SEBELUM entry).

    Fix #4: TIDAK mundur satu interval. Bitget membulatkan startTime ke bawah
    per granularity dan mengembalikan candle yang memuat `since`; di sisi klien
    candle dengan openTime < startTime tetap dibuang, sehingga candle pertama
    = openTime >= since — candle yang memuat waktu entry ikut dihitung bila
    `since` tepat di batas interval (entry terjadi di awal candle itu) — tanpa
    mengikutsertakan aksi harga pra-entry dari candle sebelumnya.
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


def _parse_ticker(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parsing satu item ticker Bitget V2 (spot & futures memakai nama field sama)."""
    try:
        return {
            "symbol": row["symbol"],
            "price": float(row["lastPr"]),
            "price_change_pct_24h": float(row["change24h"]) * 100.0,  # Bitget = pecahan (0.00321 -> 0.321%)
            "volume": float(row["baseVolume"]),
            "quote_volume": float(row["quoteVolume"]),
            "high_24h": float(row["high24h"]),
            "low_24h": float(row["low24h"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def get_ticker_24h(symbol: str, market: str = MARKET_SPOT) -> Optional[Dict[str, Any]]:
    """Ticker 24 jam: last price, % change, volume, quote volume (default Spot).

    Futures tersedia via `market=bitget.MARKET_FUTURES` (routing transparan).
    """
    api_symbol = to_spot_symbol(symbol) if market == MARKET_SPOT else to_futures_symbol(symbol)
    url = f"{BITGET_BASE_URL}{_TICKERS_ENDPOINTS[market]}"
    params: Dict[str, Any] = {"symbol": api_symbol}
    if market == MARKET_FUTURES:
        params["productType"] = FUTURES_PRODUCT_TYPE
    rows = _ok(
        http_get_json(url, params, source="bitget" if market == MARKET_SPOT else "bitget-futures"),
        source="bitget" if market == MARKET_SPOT else "bitget-futures",
    )
    if not rows:
        return None
    ticker = _parse_ticker(rows[0])
    if not ticker:
        raise DataSourceError(f"[bitget] Ticker {symbol} tidak valid: {rows[0]}")
    return ticker


def get_all_tickers_24h() -> Dict[str, Dict[str, Any]]:
    """Ticker 24j untuk SEMUA pasangan dalam SATU panggilan (hemat weight API).

    Return {symbol: {'price', 'price_change_pct_24h', 'quote_volume', 'volume'}}.
    """
    url = f"{BITGET_BASE_URL}{_TICKERS_ENDPOINTS[MARKET_SPOT]}"
    rows = _ok(http_get_json(url, source="bitget"))
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            symbol = row["symbol"]
            if not symbol.endswith("USDT"):
                continue
            ticker = _parse_ticker(row)
            if not ticker:
                continue
            out[symbol] = {
                "price": ticker["price"],
                "price_change_pct_24h": ticker["price_change_pct_24h"],
                "quote_volume": ticker["quote_volume"],
                "volume": ticker["volume"],
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_funding_rate(symbol: str, limit: int = 8) -> List[float]:
    """Riwayat funding rate terakhir (per 8 jam untuk perpetual USDT).

    Endpoint futures Bitget (mix) bila gagal -> kembalikan [] agar skor
    sentiment memakai sumber lain (Fear & Greed).
    """
    url = f"{BITGET_FUTURES_URL}/api/v2/mix/market/history-fund-rate"
    params = {
        "productType": FUTURES_PRODUCT_TYPE,
        "symbol": to_futures_symbol(symbol),
        "pageSize": int(limit),
    }
    try:
        data = _ok(http_get_json(url, params, source="bitget-futures", timeout=8, retries=1), source="bitget-futures")
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
    """Rasio akun long/short di posisi (data terbaru). >1 = lebih banyak long.

    Endpoint Bitget `account-long-short` (kategori data derivatif) memakai symbol
    futures (BTCUSDT_UMCBL); diambil baris dengan ts terbaru.
    """
    url = f"{BITGET_FUTURES_URL}/api/v2/mix/market/account-long-short"
    params = {
        "productType": FUTURES_PRODUCT_TYPE,
        "symbol": to_futures_symbol(symbol),
        "period": "5m",
        "pageSize": int(limit),
    }
    try:
        data = _ok(http_get_json(url, params, source="bitget-futures", timeout=8, retries=1), source="bitget-futures")
    except DataSourceError:
        return None
    if not data:
        return None
    try:
        newest = max(data, key=lambda row: int(row.get("ts", 0) or 0))
        return float(newest["longShortAccountRatio"])
    except (KeyError, TypeError, ValueError):
        return None