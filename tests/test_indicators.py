"""Test indikator teknis (murni, tanpa network)."""

import pytest

from indicators.macd import macd
from indicators.rsi import rsi
from indicators.smc import detect_fvg, detect_order_blocks, detect_structure, nearest_order_block
from indicators.support_resistance import find_swings, nearest_levels, pivot_points


class TestRSI:
    def test_too_short(self):
        assert rsi([1.0, 2.0, 3.0]) is None

    def test_all_up_is_100(self):
        prices = [float(i) for i in range(1, 40)]
        assert rsi(prices) == 100.0

    def test_all_down_is_0(self):
        prices = [float(i) for i in range(40, 1, -1)]
        assert rsi(prices) == 0.0

    def test_oscillation_around_50(self):
        prices = [50.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(40)]
        value = rsi(prices)
        assert value is not None
        assert 40 <= value <= 60


class TestMACD:
    def test_rising_trend_positive_histogram(self):
        prices = [10.0 + 0.01 * i * i for i in range(80)]
        result = macd(prices)
        assert result is not None
        assert result["histogram"] > 0

    def test_falling_trend_negative_histogram(self):
        prices = [100.0 - 0.01 * i * i for i in range(80)]
        result = macd(prices)
        assert result is not None
        assert result["histogram"] < 0

    def test_insufficient_data(self):
        assert macd([1.0, 2.0]) is None


class TestSupportResistance:
    def test_find_swings(self):
        highs = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11]
        lows = [9, 10, 11, 10, 9, 10, 11, 12, 11, 10]
        swings = find_swings(highs, lows, left=2, right=2)
        assert any(s["value"] == 12.0 for s in swings["highs"])
        assert any(s["value"] == 9.0 for s in swings["lows"])

    def test_nearest_levels(self):
        price = 105.0
        highs = [100, 102, 110, 108, 112, 109, 113]
        lows = [98, 100, 105, 103, 108, 106, 109]
        levels = nearest_levels(price, highs, lows, left=1, right=1)
        assert levels["resistance"] == 110.0
        assert levels["support"] == 103.0

    def test_pivot_points(self):
        piv = pivot_points(high=110, low=100, close=105)
        assert piv["pivot"] == pytest.approx(105.0)
        assert piv["s1"] < piv["pivot"] < piv["r1"]


class TestSMC:
    def test_detect_fvg_bullish(self):
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 102, "high": 110, "low": 101, "close": 109},
            {"open": 110, "high": 112, "low": 109, "close": 111},
        ]
        gaps = detect_fvg(candles)
        assert len(gaps) == 1
        assert gaps[0]["type"] == "bullish"

    def test_detect_order_blocks(self):
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 100, "high": 100.5, "low": 99.5, "close": 99.8},
            {"open": 99.8, "high": 100, "low": 99.5, "close": 99.7},
            {"open": 99.7, "high": 99.9, "low": 99.3, "close": 99.5},
            {"open": 99.5, "high": 115, "low": 99.4, "close": 114},
        ]
        blocks = detect_order_blocks(candles, lookback=3)
        assert len(blocks) >= 1
        assert blocks[0]["type"] == "bullish"

    def test_nearest_order_block(self):
        blocks = [
            {"type": "bullish", "low": 90, "high": 95, "index": 0},
            {"type": "bullish", "low": 100, "high": 104, "index": 1},
        ]
        nearest = nearest_order_block(price=110.0, blocks=blocks)
        assert nearest["high"] == 104.0

    def test_structure_bos(self):
        candles = [
            {"open": 100, "high": 105, "low": 99, "close": 104},
            {"open": 104, "high": 108, "low": 103, "close": 103},
            {"open": 103, "high": 104, "low": 102, "close": 102.5},
            {"open": 102.5, "high": 103, "low": 101.5, "close": 102},
            {"open": 102, "high": 110, "low": 101.5, "close": 109},
        ]
        structure = detect_structure(candles, left=1, right=1)
        assert structure["bos"] == "bullish"
