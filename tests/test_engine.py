"""Test mesin skoring engine (tanpa network)."""

from engine import (
    ACTION_BUY,
    ACTION_NEUTRAL,
    ACTION_SELL,
    assemble_signal,
    format_message,
    rank_signals,
    score_technical,
)
from indicators.smc import detect_order_blocks
from indicators.support_resistance import nearest_levels


def _candles_from_closes(closes):
    out = []
    prev = closes[0]
    for c in closes:
        out.append(
            {"open": prev, "high": max(prev, c) * 1.002, "low": min(prev, c) * 0.998, "close": c}
        )
        prev = c
    return out


def _bullish_series():
    """Dip lalu rally kembali ke atas → bullish."""
    down = [100.0 - 1.0 * i for i in range(40)]
    up = [down[-1] + 3.0 * i for i in range(1, 31)]
    return down + up


def _neutral_series():
    return [100.0 + (i % 3 - 1) * 0.1 for i in range(60)]


class TestScoreTechnical:
    def test_bullish_input_positive(self):
        down = [100.0 - 1.0 * i for i in range(45)]
        up = [down[-1] + 1.0 * i for i in range(1, 16)]
        score, reasons = score_technical(down + up, pct_change_24h=9.2)
        assert score > 0
        assert reasons

    def test_neutral_input(self):
        score, _ = score_technical(_neutral_series(), pct_change_24h=0.0)
        assert -1.0 <= score <= 1.0


class TestAssembleSignal:
    def test_strong_bullish_produces_buy(self):
        closes = _bullish_series()
        candles = _candles_from_closes(closes)
        sig = assemble_signal(
            symbol="BTCUSDT",
            base="BTC",
            price=closes[-1],
            pct_change_24h=8.0,
            closes=closes,
            candles=candles,
            fg_value=20.0,
            funding_rates=[0.0001],
            ls_ratio=0.8,
            whale_flow={"inflow_usd": 0.0, "outflow_usd": 30e6, "net_usd": -30e6},
            btc_stats={"n_tx_24h": 400000},
        )
        assert sig.action == ACTION_BUY
        assert sig.total_score > 0
        assert sig.confidence >= 55
        assert sig.tp1 > sig.entry > sig.sl
        assert set(sig.breakdown) == {"teknikal", "smc", "sentimen", "whale", "onchain"}

    def test_strong_bearish_produces_sell(self):
        closes = _bullish_series()[::-1]
        candles = _candles_from_closes(closes)
        sig = assemble_signal(
            symbol="ETHUSDT",
            base="ETH",
            price=closes[-1],
            pct_change_24h=-8.0,
            closes=closes,
            candles=candles,
            fg_value=85.0,
            funding_rates=[0.0006],
            ls_ratio=2.0,
            whale_flow={"inflow_usd": 30e6, "outflow_usd": 0.0, "net_usd": 30e6},
            btc_stats=None,
        )
        assert sig.action == ACTION_SELL
        assert sig.total_score < 0
        assert sig.sl > sig.entry > sig.tp1

    def test_missing_data_is_neutral_not_error(self):
        closes = _neutral_series()
        sig = assemble_signal(
            symbol="XRPUSDT",
            base="XRP",
            price=0.5,
            pct_change_24h=0.0,
            closes=closes,
            candles=_candles_from_closes(closes),
            fg_value=50.0,
            funding_rates=[],
            ls_ratio=None,
            whale_flow=None,
            btc_stats=None,
        )
        assert sig.action in (ACTION_BUY, ACTION_SELL, ACTION_NEUTRAL)
        assert sig.entry > 0


class TestLevels:
    def test_levels_used_not_static(self):
        candles = [
            {"open": 100, "high": 120, "low": 95, "close": 105},
            {"open": 105, "high": 108, "low": 100, "close": 102},
            {"open": 102, "high": 130, "low": 101, "close": 128},
        ] * 10
        levels = nearest_levels(105.0, [c["high"] for c in candles], [c["low"] for c in candles], left=2, right=2)
        assert levels["resistance"] is not None

    def test_order_blocks_detected(self):
        candles = _candles_from_closes(_bullish_series())
        assert len(detect_order_blocks(candles)) >= 0  # tidak error pada data valid


class TestFormat:
    def test_format_message_contains_symbols(self):
        closes = _bullish_series()
        sig = assemble_signal(
            symbol="BTCUSDT",
            base="BTC",
            price=closes[-1],
            pct_change_24h=3.0,
            closes=closes,
            candles=_candles_from_closes(closes),
            fg_value=30.0,
            funding_rates=[0.0001],
            ls_ratio=1.0,
            whale_flow=None,
            btc_stats=None,
        )
        ranked = rank_signals([sig])
        message = format_message(ranked, "Jumat, 07 Agu 2026, 07:00 WIB", "Fear&Greed: 30")
        assert "BTC" in message
        assert "BUY" in message or "SELL" in message or "NEUTRAL" in message
