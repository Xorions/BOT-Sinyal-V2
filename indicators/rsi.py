"""RSI (Wilder) — kekuatan momentum relatif."""

from typing import List, Optional

RSI_PERIOD = 14


def rsi(prices: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    """Relative Strength Index dengan smoothing Wilder.

    <30 oversold (potensi naik), >70 overbought (potensi turun).
    """
    if len(prices) < period + 1:
        return None
    deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
