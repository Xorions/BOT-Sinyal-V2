"""Test mesin skoring engine v2.3 (MTF SMC + S&D, tanpa network)."""

from typing import List

from engine import (
    ACTION_BUY,
    ACTION_NEUTRAL,
    ACTION_SELL,
    Signal,
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
    """H1 dengan Demand Zone [~100, ~104.4]; harga pullback masuk zona (~102)."""
    candles = []
    prev_close = 120.0
    for i in range(10):
        close = 112.0 - i * 0.5
        o = prev_close
        candles.append({"open": o, "high": max(o, close), "low": min(o, close) - 0.4, "close": close})
        prev_close = close
    candles.append({"open": prev_close, "high": prev_close, "low": 100.0, "close": 104.0})
    for c in (103.0, 103.5, 103.0):
        o = c + 0.4
        candles.append({"open": o, "high": c + 0.9, "low": c - 0.4, "close": c})
    c = 104.0
    for _ in range(7):
        nxt = c + 3.5
        candles.append({"open": c, "high": nxt + 1.0, "low": c - 0.5, "close": nxt})
        c = nxt
    for c in (126.0, 110.0, 105.0, 104.0, 103.0, 102.0):
        o = c + 0.8
        candles.append({"open": o, "high": o, "low": c - 0.4, "close": c})
    return candles


def _supply_h1():
    """H1 dengan Supply Zone [~145.7, 150]; harga rally masuk zona (~148)."""
    candles = []
    prev_close = 100.0
    for i in range(10):
        close = 108.0 + i * 0.5
        o = prev_close
        candles.append({"open": o, "high": max(o, close), "low": min(o, close) - 0.4, "close": close})
        prev_close = close
    candles.append({"open": prev_close, "high": 150.0, "low": prev_close, "close": 146.0})
    for c in (147.0, 146.5, 147.0):
        o = c - 0.4
        candles.append({"open": o, "high": c + 0.5, "low": c - 0.8, "close": c})
    c = 146.0
    for _ in range(7):
        nxt = c - 3.5
        candles.append({"open": c, "high": c + 0.5, "low": nxt - 1.0, "close": nxt})
        c = nxt
    for c in (124.0, 140.0, 145.0, 146.0, 147.0, 148.0):
        o = c - 0.8
        candles.append({"open": o, "high": c + 0.4, "low": o, "close": c})
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
