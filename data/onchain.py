"""On-chain & whale signal (proxy data gratis).

Keterbatasan tier free (tanpa Glassnode/Santiment):
- Etherscan/BscScan: riwayat transfer untuk daftar alamat yang dilacak.
- blockchain.info: statistik jaringan BTC (jumlah tx, hash rate, harga).

Signal whale disini adalah PROXY sederhana: melacak inflow/outflow ETH dari
daftar alamat yang dikenal (default: hot wallet exchange populer). Anda bisa
mengganti daftar alamat lewat env `ONCHAIN_TRACKED_ADDRESSES` (dipisah koma).
"""

import os
import time
from typing import Any, Dict, List, Optional

from config import ETHERSCAN_API_KEY, WHALE_LOOKBACK_HOURS, WHALE_MIN_USD
from data._client import DataSourceError, http_get_json

ETHERSCAN_API = "https://api.etherscan.io/api"

# Default: hot wallet exchange yang relatif stabil dikenal publik.
# Inflow ke wallet exchange ~ potensi jual; outflow ~ potensi simpan (accumulate).
DEFAULT_TRACKED_ADDRESSES: List[str] = [
    "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance hot wallet
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2",  # Kraken
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3",  # Coinbase
    "0x876eabf441b2ee5b5b0554fd502a8e0600950cfa",  # Bitfinex cold
]


def _tracked_addresses() -> List[str]:
    raw = os.getenv("ONCHAIN_TRACKED_ADDRESSES", "").strip()
    if not raw:
        return DEFAULT_TRACKED_ADDRESSES
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _etherscan(method: str, **params: Any) -> Any:
    if not ETHERSCAN_API_KEY.strip():
        raise DataSourceError("[etherscan] ETHERSCAN_API_KEY belum diisi di .env")
    params["module"] = "account"
    params["action"] = method
    params["apikey"] = ETHERSCAN_API_KEY.strip()
    data = http_get_json(ETHERSCAN_API, params, source="etherscan")
    if data.get("status") != "1":
        raise DataSourceError(f"[etherscan] API error: {data.get('message')}")
    return data.get("result", [])


def _usd_value(wei_value: int, eth_price_usd: float) -> float:
    return (wei_value / 1e18) * eth_price_usd


def get_exchange_flow_eth(eth_price_usd: float, lookback_hours: int = WHALE_LOOKBACK_HOURS) -> Optional[Dict[str, float]]:
    """Netflow ETH untuk alamat yang dilacak (USD).

    Return {'inflow_usd': ..., 'outflow_usd': ..., 'net_usd': ...}
    net_usd > 0 = lebih banyak ETH masuk ke exchange (bearish/tekanan jual).
    Kembalikan None bila tidak ada data yang berhasil diambil (mis. tanpa API key).
    """
    cutoff = time.time() - lookback_hours * 3600
    inflow = 0.0
    outflow = 0.0
    tracked = set(a.lower() for a in _tracked_addresses())
    collected = 0

    for address in tracked:
        try:
            txs = _etherscan("txlist", address=address, startblock=0, endblock=99999999, page=1, offset=100, sort="desc")
        except DataSourceError:
            continue
        collected += 1
        for tx in txs:
            try:
                ts = int(tx["timeStamp"])
                value = int(tx["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            if value <= 0:
                continue
            usd = _usd_value(value, eth_price_usd)
            if usd < WHALE_MIN_USD:
                continue
            if tx["to"].lower() in tracked:
                inflow += usd
            elif tx["from"].lower() in tracked:
                outflow += usd

    if collected == 0:
        return None
    return {"inflow_usd": inflow, "outflow_usd": outflow, "net_usd": inflow - outflow}


def get_btc_stats() -> Optional[Dict[str, Any]]:
    """Statistik jaringan BTC dari blockchain.info (proxy aktivitas on-chain)."""
    try:
        data = http_get_json("https://api.blockchain.info/stats", source="blockchain.info")
    except DataSourceError:
        return None
    return {
        "n_tx_24h": data.get("n_tx"),
        "hash_rate": data.get("hash_rate"),
        "trade_volume_usd": data.get("trade_volume_usd"),
        "miners_revenue_usd": data.get("miners_revenue_usd"),
    }
