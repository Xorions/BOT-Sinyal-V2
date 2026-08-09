"""Fibonacci Retracement — Swing High/Low terbaru, fokus Golden Zone 0.5/0.618/0.786.

Aturan (bobot 0.15):
  - Tarik Fibonacci otomatis dari Swing High & Swing Low terbaru.
  - Level utama: 0.50, 0.618, 0.786 (Golden Zone = band 0.786 .. 0.50).
  - Konfluensi Golden Zone ∩ Key Level S&R / Order Block -> skor konfirmasi tinggi.

Murni fungsional: menerima list candle {open, high, low, close}.
"""

from typing import Dict, List, Optional

from indicators.support_resistance import find_swings

GOLDEN_LEVELS = (0.5, 0.618, 0.786)
ALL_LEVELS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 0.886, 1.0)


def fib_levels(high: float, low: float) -> Dict[float, float]:
    """Level retracement antara low (0%) dan high (100%)."""
    rng = high - low
    if rng <= 0:
        return {}
    return {level: high - rng * level for level in ALL_LEVELS}


def swing_extremes(candles: List[Dict[str, float]], left: int = 3, right: int = 3) -> Optional[Dict]:
    """Swing High & Swing Low terbaru + arah kaki tren (leg) di antara keduanya.

    - last low sebelum last high -> kaki naik (uptrend leg).
    - last high sebelum last low -> kaki turun (downtrend leg).
    """
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    swings = find_swings(highs, lows, left, right)
    if not swings["highs"] or not swings["lows"]:
        return None
    sh = swings["highs"][-1]
    sl = swings["lows"][-1]
    return {
        "swing_high": sh,
        "swing_low": sl,
        "trend": "bullish" if sh["index"] > sl["index"] else "bearish",
    }


def analyze_fibonacci(
    candles: List[Dict[str, float]],
    price: float,
    left: int = 3,
    right: int = 3,
    golden_levels: tuple = GOLDEN_LEVELS,
) -> Dict:
    """Analisa Fibonacci terbaru: swing, level, dan posisi harga vs Golden Zone.

    Return dict (ok=False bila data/range tidak cukup):
      - ok: bool.
      - swing_high / swing_low / range: float.
      - levels: dict level -> harga.
      - golden_zone_high (= level 0.50) / golden_zone_low (= level 0.786).
      - in_golden_zone: harga berada di dalam band Golden Zone.
      - golden_zone_dist_pct: jarak % harga ke tepi Golden Zone terdekat.
      - nearest_level: level golden (0.5/0.618/0.786) terdekat & jaraknya.
      - leg_trend: "bullish"|"bearish" dari urutan swing terakhir.
    """
    ex = swing_extremes(candles, left, right)
    if not ex:
        return {"ok": False}
    high = ex["swing_high"]["value"]
    low = ex["swing_low"]["value"]
    rng = high - low
    if rng <= 0 or high <= low:
        return {"ok": False}

    levels = fib_levels(high, low)
    gz_hi = levels[0.5]
    gz_lo = levels[0.786]

    in_zone = gz_lo <= price <= gz_hi
    if price > gz_hi:
        dist = (price - gz_hi) / price * 100.0
    elif price < gz_lo:
        dist = (gz_lo - price) / price * 100.0
    else:
        dist = 0.0

    nearest_level = min(golden_levels, key=lambda level: abs(price - levels[level]))
    nearest_dist = abs(price - levels[nearest_level]) / price * 100.0 if price else None

    return {
        "ok": True,
        "swing_high": high,
        "swing_low": low,
        "range": rng,
        "levels": levels,
        "golden_zone_high": gz_hi,
        "golden_zone_low": gz_lo,
        "in_golden_zone": in_zone,
        "golden_zone_dist_pct": dist,
        "nearest_level": nearest_level,
        "nearest_level_dist_pct": nearest_dist,
        "leg_trend": ex["trend"],
    }
