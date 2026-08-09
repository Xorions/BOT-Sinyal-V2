"""Test indikator teknis v2.3 (murni, tanpa network) — MTF SMC + S&D."""

import pytest

from indicators.ema import analyze_ema, ema as ema_series
from indicators.fibonacci import analyze_fibonacci, fib_levels, swing_extremes
from indicators.macd import macd, macd_histogram_series
from indicators.rsi import rsi
from indicators.smc import (
    detect_equal_highs_lows,
    detect_fvg,
    detect_liquidity_sweep,
    detect_order_blocks,
    detect_structure,
    nearest_bearish_ob,
    nearest_bullish_ob,
    nearest_order_block,
)
from indicators.supply_demand import (
    detect_supply_demand,
    in_zone,
    nearest_demand,
    nearest_supply,
)
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

    def test_histogram_series_length_and_sign(self):
        prices = [10.0 + 0.01 * i * i for i in range(80)]
        hist = macd_histogram_series(prices)
        assert len(hist) == 80
        assert hist[-1] > 0


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

    def test_nearest_directional_ob(self):
        blocks = [
            {"type": "bullish", "low": 90, "high": 95, "index": 0},
            {"type": "bearish", "low": 97, "high": 103, "index": 1},
            {"type": "bullish", "low": 100, "high": 104, "index": 2},
        ]
        assert nearest_bullish_ob(110.0, blocks)["high"] == 104.0
        assert nearest_bearish_ob(95.0, blocks)["high"] == 103.0

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

    def _mk_candles(self, closes):
        out = []
        prev = closes[0]
        for c in closes:
            out.append(
                {"open": prev, "high": max(prev, c) + 0.5, "low": min(prev, c) - 0.5, "close": c}
            )
            prev = c
        return out

    def test_structure_lower_high_higher_low_neutral(self):
        # Konflik: swing high turun (110 -> 105) tapi swing low naik (99 -> 101).
        # Kriteria seimbang: tidak bullish (bukan HH+HL) dan tidak bearish (bukan LH+LL).
        closes = [102, 106, 109, 110, 108, 104, 100, 102, 104, 105, 102, 103, 102, 104]
        structure = detect_structure(self._mk_candles(closes), left=2, right=2)
        assert structure["trend"] is None

    def test_structure_higher_high_higher_low_bullish(self):
        # HH (105 -> 110) + HL (100 -> 103) -> bullish.
        closes = [100, 103, 105, 103, 101, 100, 102, 104, 103, 100, 98, 103, 105, 108, 110, 107, 105, 103, 104, 106]
        structure = detect_structure(self._mk_candles(closes), left=2, right=2)
        assert structure["trend"] == "bullish"

    def test_structure_lower_high_lower_low_bearish(self):
        # LH (110 -> 105) + LL (104 -> 99) -> bearish.
        closes = [123, 120, 111, 110, 112, 115, 106, 105, 107, 110, 101, 100, 102, 105, 104, 100]
        structure = detect_structure(self._mk_candles(closes), left=2, right=2)
        assert structure["trend"] == "bearish"

    def test_structure_higher_high_lower_low_neutral(self):
        # Konflik kebalikan: swing high naik (105 -> 110) tapi swing low turun (102 -> 98).
        closes = [100, 102, 104, 105, 104, 103, 104, 107, 108, 110, 107, 105, 103, 101, 99, 100, 102, 104, 106]
        structure = detect_structure(self._mk_candles(closes), left=2, right=2)
        assert structure["trend"] is None

    def test_equal_highs_detected(self):
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 100, "high": 105, "low": 100, "close": 104},
            {"open": 104, "high": 105, "low": 103, "close": 104},
            {"open": 104, "high": 101, "low": 100, "close": 101},
            {"open": 101, "high": 99, "low": 97, "close": 98},
            {"open": 98, "high": 101, "low": 97, "close": 100},
            {"open": 100, "high": 105, "low": 100, "close": 104},
            {"open": 104, "high": 105, "low": 103, "close": 104},
        ]
        eq = detect_equal_highs_lows(candles, left=1, right=1)
        assert any(e["type"] == "eqh" for e in eq)

    def test_liquidity_sweep_sell(self):
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 100, "high": 102, "low": 98, "close": 101},
            {"open": 101, "high": 103, "low": 100, "close": 102},
            {"open": 102, "high": 103, "low": 99, "close": 102},
            {"open": 102, "high": 104, "low": 101, "close": 103},
            {"open": 103, "high": 104, "low": 100, "close": 103},
            {"open": 103, "high": 105, "low": 98, "close": 103},
        ]
        sweeps = detect_liquidity_sweep(candles, left=1, right=1, lookback=10)
        assert any(s["type"] == "sell_sweep" for s in sweeps)


class TestSupplyDemand:
    def test_demand_zone_detected(self):
        candles = [
            {"open": 108, "high": 109, "low": 107, "close": 108},
            {"open": 108, "high": 109, "low": 106, "close": 107},
            {"open": 107, "high": 108, "low": 105, "close": 106},
            {"open": 106, "high": 107, "low": 100, "close": 101},
            {"open": 101, "high": 103, "low": 100, "close": 102},
            {"open": 102, "high": 103.5, "low": 101, "close": 103},
            {"open": 103, "high": 112, "low": 102, "close": 111},
            {"open": 111, "high": 113, "low": 110, "close": 112},
        ]
        zones = detect_supply_demand(candles, left=1, right=1, pause=2)
        assert any(z["type"] == "demand" for z in zones)

    def test_in_zone(self):
        zone = {"type": "demand", "low": 100.0, "high": 105.0, "index": 0}
        assert in_zone(102.0, zone)
        assert in_zone(100.0, zone)
        assert not in_zone(120.0, zone)

    def test_nearest_demand_and_supply(self):
        zones = [
            {"type": "demand", "low": 90, "high": 95, "index": 0},
            {"type": "demand", "low": 100, "high": 104, "index": 1},
            {"type": "supply", "low": 120, "high": 125, "index": 2},
            {"type": "supply", "low": 130, "high": 135, "index": 3},
        ]
        assert nearest_demand(110.0, zones)["high"] == 104.0
        assert nearest_supply(110.0, zones)["low"] == 120.0

    def test_supply_zone_detected(self):
        candles = [
            {"open": 108, "high": 109, "low": 107, "close": 108},
            {"open": 108, "high": 109, "low": 106, "close": 107},
            {"open": 107, "high": 108, "low": 105, "close": 106},
            {"open": 106, "high": 112, "low": 105, "close": 111},
            {"open": 111, "high": 113, "low": 110, "close": 112},
            {"open": 112, "high": 114, "low": 111, "close": 113},
            {"open": 113, "high": 114, "low": 106, "close": 107},
            {"open": 107, "high": 108, "low": 106, "close": 107},
        ]
        zones = detect_supply_demand(candles, left=1, right=1, pause=2)
        assert any(z["type"] == "supply" for z in zones)


def _closes_candles(closes):
    """Candle {open/high/low/close} dari deret close (untuk indikator murni)."""
    return [{"open": c, "high": c * 1.001, "low": c * 0.999, "close": c} for c in closes]


class TestEMA:
    """EMA 20/50 — dynamic S/R + deteksi pullback (bobot 0.15)."""

    def test_ema_series_warmup_and_constant(self):
        series = ema_series([10.0] * 30, 20)
        assert len(series) == 30
        assert all(v != v for v in series[:19])  # NaN sebelum data cukup
        assert series[-1] == pytest.approx(10.0)

    def test_ema_uptrend_price_above_ema20_above_ema50(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        price = ema_series(closes, 20)[-1] * 1.01  # 1% di atas EMA20
        info = analyze_ema(_closes_candles(closes), price)
        assert info["uptrend"] is True
        assert info["trend"] == "bullish"
        assert info["ema_fast"] > info["ema_slow"]

    def test_ema_uptrend_pullback_buy_near_ema20(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        price = ema_series(closes, 20)[-1] * 1.001  # <= 0.5% dari EMA20
        info = analyze_ema(_closes_candles(closes), price)
        assert info["pullback_buy"] is True
        assert info["pullback_sell"] is False
        assert info["dist_fast_pct"] <= 0.5

    def test_ema_uptrend_no_pullback_when_far(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        price = ema_series(closes, 20)[-1] * 1.02  # 2% -> di luar 0.5%
        info = analyze_ema(_closes_candles(closes), price)
        assert info["uptrend"] is True
        assert info["pullback_buy"] is False

    def test_ema_downtrend_pullback_sell_near_ema20(self):
        closes = [200.0 - i * 0.5 for i in range(80)]
        price = ema_series(closes, 20)[-1] * 0.999
        info = analyze_ema(_closes_candles(closes), price)
        assert info["downtrend"] is True
        assert info["trend"] == "bearish"
        assert info["pullback_sell"] is True
        assert info["pullback_buy"] is False

    def test_ema_insufficient_data(self):
        info = analyze_ema(_closes_candles([100.0] * 10), 100.0)
        assert info["ema_fast"] is None
        assert info["trend"] is None
        assert info["pullback_buy"] is False


def _fib_candles():
    """Swing low 90 (idx 4) -> swing high 110 (idx 10) -> pullback."""
    closes = [95, 94, 93, 92, 90, 93, 97, 101, 105, 108, 110, 109, 107, 105]
    return [{"open": c, "high": c, "low": c, "close": c} for c in closes]


class TestFibonacci:
    """Fibonacci Retracement — Golden Zone 0.5/0.618/0.786 (bobot 0.15)."""

    def test_fib_levels_math(self):
        levels = fib_levels(110.0, 100.0)
        assert levels[0.5] == pytest.approx(105.0)
        assert levels[0.618] == pytest.approx(110.0 - 10.0 * 0.618)
        assert levels[0.786] == pytest.approx(110.0 - 10.0 * 0.786)

    def test_swing_extremes_latest(self):
        ex = swing_extremes(_fib_candles())
        assert ex["swing_low"]["value"] == 90.0
        assert ex["swing_high"]["value"] == 110.0
        assert ex["trend"] == "bullish"  # low sebelum high -> leg naik

    def test_price_in_golden_zone(self):
        # Golden zone = [level 0.786, level 0.50] = [94.28, 100] untuk 90..110.
        fibo = analyze_fibonacci(_fib_candles(), 97.0)
        assert fibo["ok"] is True
        assert fibo["in_golden_zone"] is True
        assert fibo["golden_zone_low"] <= 97.0 <= fibo["golden_zone_high"]

    def test_price_outside_golden_zone_has_distance(self):
        fibo = analyze_fibonacci(_fib_candles(), 104.0)
        assert fibo["in_golden_zone"] is False
        assert fibo["golden_zone_dist_pct"] > 0
        assert fibo["nearest_level_dist_pct"] > 0

    def test_nearest_golden_level(self):
        fibo = analyze_fibonacci(_fib_candles(), 101.0)
        assert fibo["nearest_level"] in (0.5, 0.618, 0.786)

    def test_insufficient_swings_ok_false(self):
        fibo = analyze_fibonacci([{"open": 100, "high": 101, "low": 99, "close": 100}] * 3, 100.0)
        assert fibo["ok"] is False
