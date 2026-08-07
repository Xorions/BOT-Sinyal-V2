"""CoinMarketCap: daftar top koin (free tier).

Catatan keterbatasan tier free CMC:
- Kuota sangat terbatas (perlu hemat pemakaian).
- Data harga dapat delay dan tidak ada candle/OHLCV historis.
Karena itu: CMC dipakai hanya untuk ranking; semua indikator
teknikal tetap dihitung dari candle Binance.
"""

from typing import List

from config import CMC_API_KEY, CMC_BASE_URL, TOP_COINS
from data._client import DataSourceError, http_get_json


def is_configured() -> bool:
    return bool(CMC_API_KEY.strip())


def _symbols_from_cmc(top: int = TOP_COINS) -> List[str]:
    if not is_configured():
        return []
    url = f"{CMC_BASE_URL}/cryptocurrency/listings/latest"
    params = {"start": 1, "limit": top, "convert": "USD"}
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    data = http_get_json(url, params, headers, source="cmc")
    symbols: List[str] = []
    for item in data.get("data", []):
        symbol = item.get("symbol")
        if symbol:
            symbols.append(str(symbol).upper())
    return symbols


def get_top_symbols(top: int = TOP_COINS) -> List[str]:
    """Symbol top-N dari CMC. Jika CMC tidak dikonfigurasi, kembalikan kosong
    (caller bisa fallback ke daftar Binance populer)."""
    if not is_configured():
        return []
    try:
        return _symbols_from_cmc(top)
    except DataSourceError:
        return []
