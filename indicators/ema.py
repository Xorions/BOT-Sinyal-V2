"""EMA 20 & EMA 50 — Dynamic Support/Resistance & deteksi pullback (H1/H4).

Aturan (bobot 0.15):
  - Uptrend (BUY):   Harga > EMA 20 > EMA 50.
  - Pullback BUY:    Harga mendekati/menyentuh EMA 20 (jarak <= 0.5%).
  - Downtrend (SELL): Harga < EMA 20 < EMA 50.
  - Pullback SELL:   Harga mendekati/menyentuh EMA 20 (jarak <= 0.5%).

Murni fungsional: menerima list candle {open, high, low, close}.
"""

from typing import Dict, List, Optional

EMA_FAST = 20
EMA_SLOW = 50
PULLBACK_PCT = 0.5


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


def ema_latest(values: List[float], period: int) -> Optional[float]:
    """Nilai EMA terakhir (None bila data belum cukup)."""
    series = ema(values, period)
    if not series:
        return None
    last = series[-1]
    if last != last:
        return None
    return last


def _closes_of(candles: List[Dict[str, float]]) -> List[float]:
    return [c["close"] for c in candles if c.get("close") is not None]


def analyze_ema(
    candles: List[Dict[str, float]],
    price: float,
    fast: int = EMA_FAST,
    slow: int = EMA_SLOW,
    pullback_pct: float = PULLBACK_PCT,
) -> Dict:
    """Analisa EMA 20/50 untuk tren & setup pullback.

    Return dict:
      - ema_fast / ema_slow: nilai EMA terakhir (None bila data kurang).
      - trend: "bullish" (price > EMA20 > EMA50), "bearish", atau "neutral".
      - uptrend / downtrend: boolean.
      - pullback_buy:  uptrend & harga mendekati/menyentuh EMA 20 (<= pullback_pct%).
      - pullback_sell: downtrend & harga mendekati/menyentuh EMA 20 (<= pullback_pct%).
      - dist_fast_pct: jarak % harga ke EMA 20.
      - fast_slope:    arah perubahan EMA 20 (untuk konfirmasi hook).
    """
    if not price or price <= 0:
        return _empty_ema()
    closes = _closes_of(candles)
    if len(closes) < slow:
        return _empty_ema()

    fast_series = ema(closes, fast)
    slow_series = ema(closes, slow)
    ema_fast = fast_series[-1]
    ema_slow = slow_series[-1]
    if ema_fast != ema_fast or ema_slow != ema_slow:
        return _empty_ema()
    prev_fast = fast_series[-2] if len(fast_series) >= 2 else ema_fast

    uptrend = price > ema_fast > ema_slow
    downtrend = price < ema_fast < ema_slow
    dist_pct = abs(price - ema_fast) / price * 100.0
    pullback_buy = bool(uptrend and dist_pct <= pullback_pct)
    pullback_sell = bool(downtrend and dist_pct <= pullback_pct)

    return {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "trend": "bullish" if uptrend else ("bearish" if downtrend else "neutral"),
        "uptrend": uptrend,
        "downtrend": downtrend,
        "pullback_buy": pullback_buy,
        "pullback_sell": pullback_sell,
        "dist_fast_pct": dist_pct,
        "fast_slope": ema_fast - prev_fast,
    }


def _empty_ema() -> Dict:
    return {
        "ema_fast": None,
        "ema_slow": None,
        "trend": None,
        "uptrend": False,
        "downtrend": False,
        "pullback_buy": False,
        "pullback_sell": False,
        "dist_fast_pct": None,
        "fast_slope": 0.0,
    }
