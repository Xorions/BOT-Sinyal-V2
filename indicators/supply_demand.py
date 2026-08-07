"""Supply & Demand zone — area institusional dari base + konsolidasi sebelum impuls.

Zona demand (bullish) dibentuk di swing low (base) + beberapa candle konsolidasi
di atasnya; zona supply (bearish) di swing high + konsolidasi di bawahnya. Harga
yang kembali ke zona (ter-*touched*) dianggap entry mengikuti impuls sebelumnya.

Murni fungsional: menerima list candle {open, high, low, close}.
"""

from typing import Dict, List, Optional

from indicators.support_resistance import find_swings


def detect_supply_demand(
    candles: List[Dict[str, float]],
    left: int = 3,
    right: int = 3,
    pause: int = 3,
) -> List[Dict[str, float]]:
    """Deteksi zona demand & supply.

    Return list of {'type': 'demand'|'supply', 'low', 'high', 'index'}.
    """
    if len(candles) < left + right + pause + 2:
        return []
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    swings = find_swings(highs, lows, left, right)

    zones: List[Dict[str, float]] = []
    for sl in swings["lows"]:
        base = sl["value"]
        top = base
        for j in range(sl["index"] + 1, min(sl["index"] + 1 + pause, len(candles))):
            top = max(top, candles[j]["high"])
        if top > base:
            zones.append({"type": "demand", "low": base, "high": top, "index": sl["index"]})

    for sh in swings["highs"]:
        base = sh["value"]
        bottom = base
        for j in range(sh["index"] + 1, min(sh["index"] + 1 + pause, len(candles))):
            bottom = min(bottom, candles[j]["low"])
        if bottom < base:
            zones.append({"type": "supply", "low": bottom, "high": base, "index": sh["index"]})

    return _dedupe_zones(zones)


def in_zone(price: float, zone: Dict[str, float], tolerance_pct: float = 1.0) -> bool:
    """Apakah harga berada di dalam zona (dengan toleransi %)."""
    lo = zone["low"] * (1 - tolerance_pct / 100)
    hi = zone["high"] * (1 + tolerance_pct / 100)
    return lo <= price <= hi


def nearest_demand(price: float, zones: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """Zona demand terdekat di bawah harga (support institusional)."""
    candidates = [z for z in zones if z["type"] == "demand" and z["high"] < price]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z["high"])


def nearest_supply(price: float, zones: List[Dict[str, float]]) -> Optional[Dict[str, float]]:
    """Zona supply terdekat di atas harga (resistance institusional)."""
    candidates = [z for z in zones if z["type"] == "supply" and z["low"] > price]
    if not candidates:
        return None
    return min(candidates, key=lambda z: z["low"])


def _dedupe_zones(zones: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Gabungkan zona sejenis yang tumpang-tindih."""
    if not zones:
        return []
    merged: List[Dict[str, float]] = []
    for zone in zones:
        placed = False
        for m in merged:
            if m["type"] != zone["type"]:
                continue
            overlap = min(m["high"], zone["high"]) - max(m["low"], zone["low"])
            if overlap > 0:
                m["low"] = min(m["low"], zone["low"])
                m["high"] = max(m["high"], zone["high"])
                placed = True
                break
        if not placed:
            merged.append(dict(zone))
    return merged
