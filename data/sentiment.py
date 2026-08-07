"""Sentimen pasar: Fear & Greed Index (alternative.me, gratis, tanpa key).

Fear & Greed adalah indikator contrarian:
- Sangat takut (<=25)  = capitulasi → peluang BUY
- Sangat serakah (>=75) = euforia → peluang SELL / risiko pullback
"""

from typing import Dict, List, Optional

from config import FEAR_GREED_LOOKBACK, FEAR_GREED_URL
from data._client import http_get_json


def get_fear_greed(limit: int = FEAR_GREED_LOOKBACK) -> List[Dict[str, float]]:
    """Nilai Fear & Greed terakhir (value 0-100, classification)."""
    data = http_get_json(FEAR_GREED_URL, {"limit": limit}, source="fear-greed")
    out: List[Dict[str, float]] = []
    for item in data.get("data", []):
        try:
            out.append(
                {
                    "value": float(item["value"]),
                    "timestamp": float(item["timestamp"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def get_fear_greed_current() -> Optional[float]:
    values = get_fear_greed(limit=1)
    if not values:
        return None
    return values[0]["value"]


def fear_greed_label(value: float) -> str:
    if value <= 25:
        return "Sangat Takut (Extreme Fear)"
    if value <= 45:
        return "Takut (Fear)"
    if value <= 55:
        return "Netral"
    if value <= 75:
        return "Serakah (Greed)"
    return "Sangat Serakah (Extreme Greed)"


def score_fear_greed(value: float) -> float:
    """Skor -1.0..+1.0 dengan logika contrarian."""
    if value <= 10:
        return 1.0
    if value <= 25:
        return 0.7
    if value <= 40:
        return 0.4
    if value <= 55:
        return 0.1
    if value <= 70:
        return -0.4
    if value <= 85:
        return -0.7
    return -1.0
