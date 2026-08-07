"""Support & Resistance — swing high/low, pivot, level terdekat.

SL/TP sinyal dihitung dari level ini (bukan persentase statis).
"""

from typing import Dict, List, Optional


def find_swings(
    highs: List[float],
    lows: List[float],
    left: int = 3,
    right: int = 3,
) -> Dict[str, List[Dict[str, float]]]:
    """Deteksi swing high/low (pivot) sederhana.

    Return {'highs': [{'index': i, 'value': v}], 'lows': [...]}
    """
    n = len(highs)
    if n < left + right + 1:
        return {"highs": [], "lows": []}

    sw_highs: List[Dict[str, float]] = []
    sw_lows: List[Dict[str, float]] = []
    for i in range(left, n - right):
        window_high = max(highs[i - left : i + right + 1])
        window_low = min(lows[i - left : i + right + 1])
        if highs[i] == window_high and highs[i] > highs[i - 1]:
            sw_highs.append({"index": i, "value": highs[i]})
        if lows[i] == window_low and lows[i] < lows[i - 1]:
            sw_lows.append({"index": i, "value": lows[i]})
    return {"highs": sw_highs, "lows": sw_lows}


def nearest_levels(price: float, highs: List[float], lows: List[float], left: int = 3, right: int = 3) -> Dict[str, Optional[float]]:
    """Support & resistance terdekat di bawah/atas harga saat ini."""
    swings = find_swings(highs, lows, left, right)
    resistances = [s["value"] for s in swings["highs"] if s["value"] > price]
    supports = [s["value"] for s in swings["lows"] if s["value"] < price]
    resistance = min(resistances) if resistances else None
    support = max(supports) if supports else None
    return {
        "support": support,
        "resistance": resistance,
        "support_dist_pct": _dist_pct(price, support),
        "resistance_dist_pct": _dist_pct(price, resistance),
    }


def pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
    """Classic pivot: P = (H+L+C)/3, S1/S2, R1/R2."""
    p = (high + low + close) / 3.0
    return {
        "pivot": p,
        "s1": 2 * p - high,
        "r1": 2 * p - low,
        "s2": p - (high - low),
        "r2": p + (high - low),
    }


def _dist_pct(price: float, level: Optional[float]) -> Optional[float]:
    if level is None or not price:
        return None
    return abs((level - price) / price) * 100.0
