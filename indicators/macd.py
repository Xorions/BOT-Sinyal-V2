"""MACD — EMA 12/26, signal 9, histogram."""

from typing import Dict, List, Optional

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


def ema(values: List[float], period: int) -> List[float]:
    """Exponential Moving Average untuk seluruh seri.

    Nilai pada index < period-1 berupa NaN (belum cukup data).
    """
    if not values:
        return []
    multiplier = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out: List[float] = [float("nan")] * (period - 1) + [seed]
    prev = seed
    for value in values[period:]:
        prev = (value - prev) * multiplier + prev
        out.append(prev)
    return out


def macd(
    closes: List[float],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> Optional[Dict[str, float]]:
    """MACD terbaru. Return {macd, signal, histogram} atau None bila data kurang."""
    if len(closes) < slow + signal:
        return None
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [
        f - s if f == f and s == s else float("nan")
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if v == v]
    if len(valid) < signal:
        return None
    sig = ema(valid, signal)
    last_macd = macd_line[-1]
    last_sig = sig[-1]
    return {
        "macd": last_macd,
        "signal": last_sig,
        "histogram": last_macd - last_sig,
    }
