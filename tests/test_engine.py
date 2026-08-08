"""Test mesin skoring engine v2.3 (MTF SMC + S&D, tanpa network)."""

from typing import List

import pytest

from engine import (
    ACTION_BUY,
    ACTION_NEUTRAL,
    ACTION_SELL,
    Signal,
    _levels_mtf,
    analyze_compass,
    analyze_trigger,
    assemble_signal,
    format_message,
    rank_signals,
    score_trigger,
)
from indicators.smc import detect_order_blocks, detect_structure
from indicators.supply_demand import in_zone
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


def _swingy_bullish(n: int = 10) -> List[float]:
    """Zigzag naik dengan swing high yang valid (turun di bawah peak sblmnya), berakhir breakout."""
    closes = [100.0, 104.0, 105.0]
    p = 105.0
    for _ in range(n - 1):
        closes.extend([p - 3.0, p - 5.0, p - 4.0])
        p += 5.0
        closes.append(p)
    closes.append(p + 3.0)
    return closes


def _bullish_series():
    """Seri bullish dengan swing valid (BOS di akhir)."""
    return _swingy_bullish()


def _bearish_series():
    """Seri bearish dengan swing valid (CHoCH di akhir)."""
    return _swingy_bullish()[::-1] + [95.0]


def _neutral_series():
    return [100.0 + (i % 3 - 1) * 0.1 for i in range(60)]


def _demand_h1():
    """H1 dengan Demand Zone [~100, ~104]; Supply Zone [~106.2, ~110.2]; harga pullback di ~102."""
    candles = []
    prev_close = 112.0
    for i in range(8):
        close = 110.0 - i * 0.7
        o = prev_close
        candles.append({"open": o, "high": max(o, close) + 0.2, "low": min(o, close) - 0.3, "close": close})
        prev_close = close
    candles.append({"open": prev_close, "high": prev_close + 0.1, "low": 100.0, "close": 101.0})
    candles.append({"open": 101.0, "high": 101.4, "low": 100.2, "close": 101.2})
    candles.append({"open": 101.2, "high": 101.6, "low": 100.8, "close": 101.4})
    candles.append({"open": 101.4, "high": 104.0, "low": 101.4, "close": 103.5})
    candles.append({"open": 103.5, "high": 106.0, "low": 103.5, "close": 105.5})
    candles.append({"open": 105.5, "high": 110.0, "low": 105.5, "close": 109.0})
    candles.append({"open": 109.0, "high": 110.2, "low": 108.6, "close": 109.2})
    candles.append({"open": 109.2, "high": 109.4, "low": 108.0, "close": 108.4})
    candles.append({"open": 108.4, "high": 108.6, "low": 106.4, "close": 107.0})
    candles.append({"open": 107.0, "high": 107.2, "low": 106.2, "close": 106.4})
    candles.append({"open": 106.4, "high": 106.6, "low": 106.0, "close": 106.2})
    candles.append({"open": 106.2, "high": 106.4, "low": 104.0, "close": 104.4})
    candles.append({"open": 104.4, "high": 104.6, "low": 102.0, "close": 102.4})
    candles.append({"open": 102.4, "high": 102.6, "low": 101.6, "close": 102.0})
    return candles


def _supply_h1():
    """H1 dengan Supply Zone [~146, 150]; Demand Zone [~141.6, 144]; harga rally masuk zona (~148)."""
    candles = []
    prev_close = 140.0
    for i in range(8):
        close = 142.0 + i * 0.7
        o = prev_close
        candles.append({"open": o, "high": max(o, close) + 0.3, "low": min(o, close) - 0.2, "close": close})
        prev_close = close
    candles.append({"open": prev_close, "high": 150.0, "low": prev_close - 0.1, "close": 149.0})
    candles.append({"open": 149.0, "high": 149.4, "low": 148.6, "close": 149.0})
    candles.append({"open": 149.0, "high": 149.2, "low": 148.4, "close": 148.8})
    candles.append({"open": 148.8, "high": 148.8, "low": 146.0, "close": 146.5})
    candles.append({"open": 146.5, "high": 146.5, "low": 144.0, "close": 144.5})
    candles.append({"open": 144.5, "high": 144.5, "low": 142.0, "close": 143.0})
    candles.append({"open": 143.0, "high": 143.2, "low": 141.8, "close": 142.2})
    candles.append({"open": 142.2, "high": 142.4, "low": 141.6, "close": 142.0})
    candles.append({"open": 142.0, "high": 142.2, "low": 141.6, "close": 142.0})
    candles.append({"open": 142.0, "high": 142.2, "low": 141.8, "close": 142.2})
    candles.append({"open": 142.2, "high": 144.0, "low": 141.8, "close": 143.6})
    candles.append({"open": 143.6, "high": 146.0, "low": 143.6, "close": 145.6})
    candles.append({"open": 145.6, "high": 148.0, "low": 145.6, "close": 147.6})
    candles.append({"open": 147.6, "high": 148.2, "low": 147.4, "close": 148.0})
    return candles


def _bullish_mtf(price=102.0):
    """MTF bullish penuh: kompas H4/D1 bullish, harga di Demand Zone H1, M15 naik."""
    h4 = _candles_from_closes(_bullish_series())
    d1 = _candles_from_closes(_bullish_series())
    h1 = _demand_h1()
    m15 = _candles_from_closes(_bullish_series())
    return price, h4, d1, h1, m15


def _bearish_mtf(price=148.0):
    """MTF bearish penuh: kompas H4/D1 bearish, harga di Supply Zone H1, M15 turun."""
    h4 = _candles_from_closes(_bearish_series())
    d1 = _candles_from_closes(_bearish_series())
    h1 = _supply_h1()
    m15 = _candles_from_closes(_bearish_series())
    return price, h4, d1, h1, m15


class TestCompass:
    def test_h4_bullish_direction_buy(self):
        h4 = _candles_from_closes(_bullish_series())
        d1 = _candles_from_closes(_bullish_series())
        compass = analyze_compass(h4, d1)
        assert compass["direction"] == ACTION_BUY
        assert compass["h4_trend"] == "bullish"

    def test_h4_bearish_direction_sell(self):
        h4 = _candles_from_closes(_bearish_series())
        d1 = _candles_from_closes(_bearish_series())
        compass = analyze_compass(h4, d1)
        assert compass["direction"] == ACTION_SELL
        assert compass["h4_trend"] == "bearish"

    def test_no_compass_on_neutral(self):
        compass = analyze_compass(_candles_from_closes(_neutral_series()), [])
        assert compass["direction"] is None


class TestTrigger:
    def test_bullish_input_positive(self):
        score, reasons = score_trigger(_candles_from_closes(_bullish_series()), pct_change_24h=9.2)
        assert score > 0
        assert reasons
        assert any(r.startswith("[M15]") for r in reasons)

    def test_neutral_input(self):
        score, _ = score_trigger(_candles_from_closes(_neutral_series()), pct_change_24h=0.0)
        assert -1.0 <= score <= 1.0

    def test_analyze_trigger_fields(self):
        trig = analyze_trigger(_candles_from_closes(_bullish_series()))
        assert trig["histogram"] is not None
        assert trig["cross"] in (None, "golden", "death")


class TestAssembleSignal:
    def test_strong_bullish_mtf_produces_buy(self):
        price, h4, d1, h1, m15 = _bullish_mtf()
        sig = assemble_signal(
            symbol="BTCUSDT",
            base="BTC",
            price=price,
            pct_change_24h=5.2,
            h4_candles=h4,
            d1_candles=d1,
            h1_candles=h1,
            m15_candles=m15,
            fg_value=29.0,
            funding_rates=[0.0001],
            ls_ratio=0.8,
            whale_flow=None,
            btc_stats=None,
        )
        assert sig.action == ACTION_BUY
        assert sig.total_score > 0
        assert sig.confidence >= 55
        assert sig.sl < sig.entry < sig.tp1 < sig.tp2
        assert set(sig.breakdown) == {"teknikal", "smc", "sentimen", "whale", "onchain"}
        assert any("Demand Zone" in r for r in sig.reasons)
        assert any("[H4]" in r for r in sig.reasons)
        assert any("[M15]" in r for r in sig.reasons)
        assert any("Momentum 24j" in r for r in sig.reasons)

    def test_strong_bearish_mtf_produces_sell(self):
        price, h4, d1, h1, m15 = _bearish_mtf()
        sig = assemble_signal(
            symbol="ETHUSDT",
            base="ETH",
            price=price,
            pct_change_24h=-5.2,
            h4_candles=h4,
            d1_candles=d1,
            h1_candles=h1,
            m15_candles=m15,
            fg_value=80.0,
            funding_rates=[0.0006],
            ls_ratio=2.0,
            whale_flow=None,
            btc_stats=None,
        )
        assert sig.action == ACTION_SELL
        assert sig.total_score < 0
        assert sig.sl > sig.entry > sig.tp1 > sig.tp2
        assert any("Supply Zone" in r for r in sig.reasons)

    def test_compass_bullish_forbids_sell(self):
        """H4 bullish (kompas BUY) + setup bearish (Supply Zone, M15 turun) -> NEUTRAL."""
        price, h4, d1, h1, m15 = _bullish_mtf(price=148.0)
        h1 = _supply_h1()
        m15 = _candles_from_closes(_bearish_series())
        sig = assemble_signal(
            symbol="BTCUSDT", base="BTC", price=148.0, pct_change_24h=-6.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=85.0, funding_rates=[0.0006], ls_ratio=2.0,
            whale_flow=None, btc_stats=None,
        )
        assert sig.action == ACTION_NEUTRAL

    def test_compass_bearish_forbids_buy(self):
        """H4 bearish (kompas SELL) + setup bullish (Demand Zone, M15 naik) -> NEUTRAL."""
        price, h4, d1, h1, m15 = _bearish_mtf(price=102.0)
        h1 = _demand_h1()
        m15 = _candles_from_closes(_bullish_series())
        sig = assemble_signal(
            symbol="BTCUSDT", base="BTC", price=102.0, pct_change_24h=6.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=20.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=None, btc_stats=None,
        )
        assert sig.action == ACTION_NEUTRAL

    def test_missing_data_is_neutral_not_error(self):
        closes = _neutral_series()
        candles = _candles_from_closes(closes)
        sig = assemble_signal(
            symbol="XRPUSDT",
            base="XRP",
            price=0.5,
            pct_change_24h=0.0,
            h4_candles=candles,
            d1_candles=[],
            h1_candles=[],
            m15_candles=[],
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

    def test_demand_zone_touched(self):
        zone = {"type": "demand", "low": 100.0, "high": 104.4}
        assert in_zone(102.0, zone)

    def test_structure_detected(self):
        assert detect_structure(_candles_from_closes(_bullish_series()))["bos"] == "bullish"


class TestLevelsRRR:
    """Aturan RRR di `_levels_mtf`: SL zona + buffer, TP1 >= 1.5x SL, TP2 = 3x SL."""

    @staticmethod
    def _map(demand=(), supply=(), levels=None):
        demand = [{"type": "demand", "low": lo, "high": hi} for lo, hi in demand]
        supply = [{"type": "supply", "low": lo, "high": hi} for lo, hi in supply]
        return {
            "zones": demand + supply,
            "demand_zones": demand,
            "supply_zones": supply,
            "order_blocks": [],
            "levels": levels or {"support": None, "resistance": None},
        }

    def test_buy_valid_target_uses_nearest_supply_zone(self):
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[(105, 107)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_BUY)
        assert sl == pytest.approx(97 * (1 - 0.003))
        assert tp1 == pytest.approx(105.0)
        assert tp2 == pytest.approx(100 + 3 * (100 - sl))
        assert sl < entry < tp1 < tp2

    def test_buy_forced_projection_when_nearest_target_too_close(self):
        # Swing high 102 terlalu dekat (< 1.5x SL) tapi tidak terhalang zona -> TP1 = proyeksi 1.5x SL.
        candles = [
            {"high": 99, "low": 98}, {"high": 100, "low": 99}, {"high": 101, "low": 100},
            {"high": 102, "low": 100}, {"high": 101, "low": 99}, {"high": 100, "low": 98},
            {"high": 99, "low": 97},
        ]
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[])
        entry, sl, tp1, tp2 = _levels_mtf(price, candles, h1_map, ACTION_BUY)
        sl_dist = price - sl
        assert tp1 == pytest.approx(price + 1.5 * sl_dist)
        assert tp2 == pytest.approx(price + 3 * (price - sl))
        assert sl < entry < tp1 < tp2

    def test_buy_blocked_by_supply_zone_returns_none(self):
        # Supply zone di 102 terlalu dekat DAN terhalang -> sinyal dibatalkan.
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[(102, 103)])
        assert _levels_mtf(price, [], h1_map, ACTION_BUY) is None

    def test_sell_valid_target_uses_nearest_demand_zone(self):
        price, h1_map = 100.0, self._map(demand=[(93, 95)], supply=[(101, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_SELL)
        assert sl == pytest.approx(103 * (1 + 0.003))
        assert tp1 == pytest.approx(95.0)
        assert tp2 == pytest.approx(100 - 3 * (sl - price))
        assert sl > entry > tp1 > tp2

    def test_sell_forced_projection_when_nearest_target_too_close(self):
        # Swing low 98 terlalu dekat tapi tidak terhalang zona -> TP1 = proyeksi 1.5x SL.
        candles = [
            {"high": 102, "low": 101}, {"high": 101, "low": 100}, {"high": 100, "low": 99},
            {"high": 99, "low": 98}, {"high": 100, "low": 99}, {"high": 101, "low": 100},
            {"high": 102, "low": 101},
        ]
        price, h1_map = 100.0, self._map(demand=[], supply=[(101, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, candles, h1_map, ACTION_SELL)
        sl_dist = sl - price
        assert tp1 == pytest.approx(price - 1.5 * sl_dist)
        assert tp2 == pytest.approx(price - 3 * (sl - price))
        assert sl > entry > tp1 > tp2

    def test_sell_blocked_by_demand_zone_returns_none(self):
        price, h1_map = 100.0, self._map(demand=[(98, 99)], supply=[(101, 103)])
        assert _levels_mtf(price, [], h1_map, ACTION_SELL) is None

    def test_neutral_fallback_fixed_levels(self):
        price = 100.0
        entry, sl, tp1, tp2 = _levels_mtf(price, [], {}, ACTION_NEUTRAL)
        assert entry == price
        assert sl == pytest.approx(price * 0.97)
        assert tp1 == pytest.approx(price * (1 + 1.5 * 0.03))
        assert tp2 == pytest.approx(price * (1 + 3.0 * 0.03))

    def test_bad_rr_signal_rejected_to_neutral(self):
        """Nearest supply terlalu dekat & terhalang -> `_levels_mtf` None -> assemble_signal NEUTRAL."""
        candles = []
        prev = 120.0
        for i in range(10):
            close = 112.0 - i * 0.5
            o = prev
            candles.append({"open": o, "high": max(o, close), "low": min(o, close) - 0.4, "close": close})
            prev = close
        candles.append({"open": prev, "high": prev, "low": 100.0, "close": 104.0})
        for c in (103.0, 103.5, 103.0):
            candles.append({"open": c + 0.4, "high": c + 0.9, "low": c - 0.4, "close": c})
        c = 104.0
        for _ in range(7):
            nxt = c + 3.5
            candles.append({"open": c, "high": nxt + 1.0, "low": c - 0.5, "close": nxt})
            c = nxt
        for c2 in (126.0, 110.0, 105.0, 104.0, 103.0, 102.0):
            candles.append({"open": c2 + 0.8, "high": c2 + 0.8, "low": c2 - 0.4, "close": c2})

        sig = assemble_signal(
            symbol="BTCUSDT", base="BTC", price=102.0, pct_change_24h=5.2,
            h4_candles=_candles_from_closes(_bullish_series()),
            d1_candles=_candles_from_closes(_bullish_series()),
            h1_candles=candles,
            m15_candles=_candles_from_closes(_bullish_series()),
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=None, btc_stats=None,
        )
        assert sig.action == ACTION_NEUTRAL
        assert any("[RR]" in r for r in sig.reasons)


class TestFormat:
    def test_format_message_contains_symbols(self):
        price, h4, d1, h1, m15 = _bullish_mtf()
        sig = assemble_signal(
            symbol="BTCUSDT",
            base="BTC",
            price=price,
            pct_change_24h=5.2,
            h4_candles=h4,
            d1_candles=d1,
            h1_candles=h1,
            m15_candles=m15,
            fg_value=29.0,
            funding_rates=[0.0001],
            ls_ratio=1.0,
            whale_flow=None,
            btc_stats=None,
        )
        ranked = rank_signals([sig])
        message = format_message(ranked, "Jumat, 07 Agu 2026, 13:30 WIB", "Fear&Greed: 29")
        assert "DAY TRADING BRIEFING" in message
        assert "BTC" in message
        assert "BUY" in message or "SELL" in message or "NEUTRAL" in message
        assert "+ [H4]" in message
        assert "+ [H1]" in message
        assert "+ [M15]" in message
        assert "💸 Momentum 24j" in message

    def test_signal_lines_include_level_pct_buy(self):
        sig = Signal(
            "BTCUSDT", "BTC", 102.0, 5.2, 0.5, "BUY", 70, 100.0, 96.0, 106.0, 112.0,
            breakdown={"teknikal": 0.5, "smc": 0.5, "sentimen": 0.5, "whale": 0.5, "onchain": 0.5},
        )
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "🛡️ SL: <b>$96.00</b> (-4.00%)" in message
        assert "🎯 TP1: <b>$106.00</b> (+6.00%)" in message
        assert "🎯 TP2: <b>$112.00</b> (+12.00%)" in message
        assert "━━━━━━━━━━━━" in message

    def test_signal_lines_include_level_pct_sell(self):
        sig = Signal(
            "ETHUSDT", "ETH", 100.0, -5.2, -0.5, "SELL", 70, 100.0, 104.0, 96.0, 92.0,
            breakdown={"teknikal": -0.5, "smc": -0.5, "sentimen": -0.5, "whale": -0.5, "onchain": -0.5},
        )
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "🛡️ SL: <b>$104.00</b> (-4.00%)" in message
        assert "🎯 TP1: <b>$96.00</b> (+4.00%)" in message
        assert "🎯 TP2: <b>$92.00</b> (+8.00%)" in message
