"""Smart Money Concepts (SMC) — Order Block, Fair Value Gap, BOS/CHoCH, struktur.

Murni fungsional: menerima list candle {open, high, low, close}.
"""

from typing import Dict, List, Optional

from indicators.support_resistance import find_swings


def detect_order_blocks(candles: List[Dict[str, float]], lookback: int = 3) -> List[Dict[str, float]]:
    """Deteksi Order Block (kandil penolakan sebelum gerakan impulsif).

    OB bullish = zona beli (low s/d high kandil bearish terakhir sebelum naik impulsif).
    OB bearish = zona jual (kandil bullish terakhir sebelum turun impulsif).
    """
    if len(candles) < lookback + 2:
        return []
    bodies = [abs(c["close"] - c["open"]) for c in candles]
    avg_body = sum(bodies) / len(bodies)
    if avg_body <= 0:
        return []

    blocks: List[Dict[str, float]] = []
    for i in range(1, len(candles)):
        candle = candles[i]
        body = abs(candle["close"] - candle["open"])
        if body < 1.5 * avg_body:
            continue
        impulsive_up = candle["close"] > candle["open"]
        for j in range(max(0, i - lookback), i):
            prev = candles[j]
            if impulsive_up and prev["close"] < prev["open"]:
                blocks.append({"type": "bullish", "low": prev["low"], "high": prev["high"], "index": j})
                break
            if not impulsive_up and prev["close"] > prev["open"]:
                blocks.append({"type": "bearish", "low": prev["low"], "high": prev["high"], "index": j})
                break
    return _dedupe_blocks(blocks)


def detect_fvg(candles: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Deteksi Fair Value Gap (imbalance) — zona harga yang dilewati cepat.

    Bullish FVG: low[i+1] > high[i-1]; Bearish FVG: high[i+1] < low[i-1].
    """
    if len(candles) < 3:
        return []
    gaps: List[Dict[str, float]] = []
    for i in range(1, len(candles) - 1):
        prev, cur, nxt = candles[i - 1], candles[i], candles[i + 1]
        if nxt["low"] > prev["high"]:
            gaps.append({"type": "bullish", "top": nxt["low"], "bottom": prev["high"], "index": i})
        elif nxt["high"] < prev["low"]:
            gaps.append({"type": "bearish", "top": prev["low"], "bottom": nxt["high"], "index": i})
    return gaps


def detect_structure(
    candles: List[Dict[str, float]],
    left: int = 3,
    right: int = 3,
) -> Dict[str, Optional[str]]:
    """Struktur market & pergerakan harga terakhir (BOS/CHoCH).

    - BOS (bullish): close menembus swing high terakhir → lanjut tren naik.
    - CHoCH (bearish): close menembus swing low terakhir → pembalikan turun.
    """
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    swings = find_swings(highs, lows, left, right)
    sw_highs = swings["highs"]
    sw_lows = swings["lows"]
    if not sw_highs or not sw_lows:
        return {"trend": None, "bos": None, "choch": None}

    last_high = sw_highs[-1]["value"]
    last_low = sw_lows[-1]["value"]
    price = closes[-1]

    structure: Dict[str, Optional[str]] = {"trend": None, "bos": None, "choch": None}
    if price > last_high:
        structure["trend"] = "bullish"
        structure["bos"] = "bullish"
    elif price < last_low:
        structure["trend"] = "bearish"
        structure["choch"] = "bearish"
    else:
        hi_higher = len(sw_highs) >= 2 and sw_highs[-1]["value"] > sw_highs[-2]["value"]
        lo_higher = len(sw_lows) >= 2 and sw_lows[-1]["value"] > sw_lows[-2]["value"]
        if hi_higher and lo_higher:
            structure["trend"] = "bullish"
        elif sw_highs and len(sw_highs) >= 2 and sw_highs[-1]["value"] < sw_highs[-2]["value"]:
            structure["trend"] = "bearish"
    structure["last_swing_high"] = last_high
    structure["last_swing_low"] = last_low
    return structure


def nearest_order_block(price: float, blocks: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """OB terdekat di bawah harga (untuk entry/SL di sinyal BUY)."""
    candidates = [b for b in blocks if b["high"] < price]
    if not candidates:
        return None
    return max(candidates, key=lambda b: b["high"])


def _dedupe_blocks(blocks: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Gabungkan OB yang berdekatan (jarak < 0.5% dari harga)."""
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: b["high"])
    merged: List[Dict[str, float]] = [dict(ordered[0])]
    for block in ordered[1:]:
        last = merged[-1]
        base = block["low"] if block["low"] else 0.0
        if base > 0 and abs(block["low"] - last["low"]) / last["low"] < 0.005:
            last["high"] = max(last["high"], block["high"])
            last["low"] = min(last["low"], block["low"])
        else:
            merged.append(dict(block))
    return merged
