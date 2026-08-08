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

    Robust terhadap tren kuat satu arah: hanya butuh swing yang tersedia
    (high saja / low saja), tidak wajib keduanya.
    """
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    swings = find_swings(highs, lows, left, right)
    sw_highs = swings["highs"]
    sw_lows = swings["lows"]
    if not sw_highs and not sw_lows:
        return {"trend": None, "bos": None, "choch": None}

    last_high = sw_highs[-1]["value"] if sw_highs else None
    last_low = sw_lows[-1]["value"] if sw_lows else None
    price = closes[-1]

    structure: Dict[str, Optional[str]] = {"trend": None, "bos": None, "choch": None}
    if sw_highs and price > last_high:
        structure["trend"] = "bullish"
        structure["bos"] = "bullish"
    elif sw_lows and price < last_low:
        structure["trend"] = "bearish"
        structure["choch"] = "bearish"
    else:
        hi_higher = len(sw_highs) >= 2 and sw_highs[-1]["value"] > sw_highs[-2]["value"]
        lo_higher = len(sw_lows) >= 2 and sw_lows[-1]["value"] > sw_lows[-2]["value"]
        hi_lower = len(sw_highs) >= 2 and sw_highs[-1]["value"] < sw_highs[-2]["value"]
        lo_lower = len(sw_lows) >= 2 and sw_lows[-1]["value"] < sw_lows[-2]["value"]
        # Kriteria simetris: bullish butuh HH+HL, bearish butuh LH+LL (bila kedua
        # sisi tersedia); bila hanya satu sisi yang ada, sinyal sisi itu dipakai.
        if sw_highs and sw_lows:
            if hi_higher and lo_higher:
                structure["trend"] = "bullish"
            elif hi_lower and lo_lower:
                structure["trend"] = "bearish"
        elif hi_higher or lo_higher:
            structure["trend"] = "bullish"
        elif hi_lower or lo_lower:
            structure["trend"] = "bearish"
    if last_high is not None:
        structure["last_swing_high"] = last_high
    if last_low is not None:
        structure["last_swing_low"] = last_low
    return structure


def nearest_order_block(price: float, blocks: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """OB terdekat di bawah harga (untuk entry/SL di sinyal BUY)."""
    candidates = [b for b in blocks if b["high"] < price]
    if not candidates:
        return None
    return max(candidates, key=lambda b: b["high"])


def nearest_bullish_ob(price: float, blocks: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """Bullish OB terdekat di bawah harga (support institutional BUY)."""
    candidates = [b for b in blocks if b["type"] == "bullish" and b["high"] < price]
    if not candidates:
        return None
    return max(candidates, key=lambda b: b["high"])


def nearest_bearish_ob(price: float, blocks: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """Bearish OB terdekat di atas harga (resistance institutional SELL)."""
    candidates = [b for b in blocks if b["type"] == "bearish" and b["low"] > price]
    if not candidates:
        return None
    return min(candidates, key=lambda b: b["low"])


def detect_equal_highs_lows(
    candles: List[Dict[str, float]],
    left: int = 3,
    right: int = 3,
    tolerance_pct: float = 0.25,
) -> List[Dict[str, float]]:
    """Equal Highs (EQH) / Equal Lows (EQL) — pool likuiditas.

    Dua swing high/low berurutan dengan selisih < tolerance_pct dianggap "equal".
    Level ini menjadi magnet likuiditas (target Liquidity Sweep).
    """
    if len(candles) < left + right + 3:
        return []
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    swings = find_swings(highs, lows, left, right)
    out: List[Dict[str, float]] = []
    swh = swings["highs"]
    swl = swings["lows"]
    for a, b in zip(swh, swh[1:]):
        if b["value"] > 0 and abs(b["value"] - a["value"]) / b["value"] * 100 <= tolerance_pct:
            out.append({"type": "eqh", "value": (a["value"] + b["value"]) / 2, "index": b["index"]})
    for a, b in zip(swl, swl[1:]):
        if b["value"] > 0 and abs(b["value"] - a["value"]) / b["value"] * 100 <= tolerance_pct:
            out.append({"type": "eql", "value": (a["value"] + b["value"]) / 2, "index": b["index"]})
    return out


def detect_liquidity_sweep(
    candles: List[Dict[str, float]],
    left: int = 3,
    right: int = 3,
    lookback: int = 12,
) -> List[Dict[str, float]]:
    """Liquidity Sweep — likuiditas di bawah swing/EQL atau di atas swing/EQH tersapu.

    - sell_sweep: harga menembus swing low / EQL lalu menutup kembali di atasnya
      → likuiditas sell tersapu → bullish.
    - buy_sweep:  harga menembus swing high / EQH lalu menutup kembali di bawahnya
      → likuiditas buy tersapu → bearish.
    """
    if len(candles) < left + right + 2:
        return []
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    swings = find_swings(highs, lows, left, right)
    out: List[Dict[str, float]] = []
    for i in range(max(1, len(candles) - lookback), len(candles)):
        candle = candles[i]
        lows_before = [s for s in swings["lows"] if s["index"] < i and s["value"] > candle["low"]]
        if lows_before:
            nearest = min(lows_before, key=lambda s: s["value"] - candle["low"])
            if candle["close"] > nearest["value"]:
                out.append({"type": "sell_sweep", "value": nearest["value"], "index": i})
        highs_before = [s for s in swings["highs"] if s["index"] < i and s["value"] < candle["high"]]
        if highs_before:
            nearest = min(highs_before, key=lambda s: candle["high"] - s["value"])
            if candle["close"] < nearest["value"]:
                out.append({"type": "buy_sweep", "value": nearest["value"], "index": i})
    return out


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
