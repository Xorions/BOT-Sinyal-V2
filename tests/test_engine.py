"""Test mesin skoring engine v2.3 (MTF SMC + S&D, tanpa network)."""

from typing import List

import pytest

from engine import (
    ACTION_BUY,
    ACTION_NEUTRAL,
    ACTION_SELL,
    Signal,
    _blocked_by_zone,
    _levels_mtf,
    _ob_near,
    _setup_valid,
    analyze_compass,
    analyze_trigger,
    assemble_signal,
    format_message,
    map_h1_zones,
    rank_signals,
    score_ema,
    score_fibo,
    score_smc,
    score_sr,
    score_trigger,
)
from indicators.ema import analyze_ema, ema as ema_series
from indicators.fibonacci import analyze_fibonacci
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
        # pct_change_24h netral (0.0) agar momentum kontrarian tidak menambahi.
        score, reasons = score_trigger(_candles_from_closes(_bullish_series()), pct_change_24h=0.0)
        assert score > 0
        assert reasons
        assert any(r.startswith("[M15]") for r in reasons)

    def test_golden_cross_scores_higher_than_plain_bullish_histogram(self, monkeypatch):
        # Fix #1: cross MACD (konfirmasi kuat) berbobot > histogram biasa.
        import engine as engine_mod

        candles = _candles_from_closes([100.0] * 40)
        monkeypatch.setattr(engine_mod, "rsi", lambda *a, **k: 50.0)
        monkeypatch.setattr(engine_mod, "detect_structure", lambda *a, **k: {"bos": None, "choch": None})
        # Histogram >0 tanpa cross -> +0.15.
        monkeypatch.setattr(
            engine_mod, "macd_histogram_series",
            lambda *a, **k: [float("nan")] * 39 + [0.5],
        )
        s_hist, r_hist = engine_mod.score_trigger(candles, 0.0)
        assert any("MACD Bullish" in r for r in r_hist)
        # Golden cross (hist[-2] <= 0 < hist[-1]) -> +0.25.
        monkeypatch.setattr(
            engine_mod, "macd_histogram_series",
            lambda *a, **k: [float("nan")] * 38 + [-0.1, 0.5],
        )
        s_cross, r_cross = engine_mod.score_trigger(candles, 0.0)
        assert any("Golden Cross" in r for r in r_cross)
        assert s_cross > s_hist

    def test_momentum_contrarian_aligned_with_rsi(self):
        # Fix #6: momentum 24j kontrarian — overbought (>=3%) bearish, oversold (<=-3%) bullish.
        closes = [100.0 + (i % 3 - 1) * 0.1 for i in range(60)]
        candles = _candles_from_closes(closes)
        s_pumped, _ = score_trigger(candles, pct_change_24h=5.0)
        s_dumped, _ = score_trigger(candles, pct_change_24h=-5.0)
        assert s_dumped > s_pumped

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
        assert set(sig.breakdown) == {"sr", "smc", "fibo", "ema", "teknikal", "onchain", "sentimen"}
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

    def test_whale_onchain_applied_only_to_source_coin(self):
        # Fix #2: netflow ETH & statistik BTC hanya untuk koin sumbernya.
        price, h4, d1, h1, m15 = _bullish_mtf()
        whale_inflow = {"net_usd": 50_000_000.0}  # inflow = bearish
        btc_stats = {"n_tx_24h": 300_000}

        # DOGE (bukan sumber) tidak boleh terpengaruh netflow ETH / statistik BTC.
        sig_doge = assemble_signal(
            symbol="DOGEUSDT", base="DOGE", price=price, pct_change_24h=0.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=whale_inflow, btc_stats=btc_stats,
        )
        assert sig_doge.breakdown["onchain"] == 0.0

        sig_eth = assemble_signal(
            symbol="ETHUSDT", base="ETH", price=price, pct_change_24h=0.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=whale_inflow, btc_stats=btc_stats,
        )
        assert sig_eth.breakdown["onchain"] == -0.5  # inflow exchange = bearish (proxy, ±0.5)

        sig_btc = assemble_signal(
            symbol="BTCUSDT", base="BTC", price=price, pct_change_24h=0.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=whale_inflow, btc_stats=btc_stats,
        )
        assert sig_btc.breakdown["onchain"] == 0.5

    def test_missing_optional_data_does_not_dilute_score(self):
        # Fix #2: data opsional yang tidak tersedia (whale/on-chain) = kategori
        # DILEWATI sepenuhnya -> skor ETH/altcoin dengan setup sama harus SAMA
        # (data yang hilang tidak boleh memperkecil skor via penyebut).
        price, h4, d1, h1, m15 = _bullish_mtf()
        kwargs = dict(
            price=price, pct_change_24h=0.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=None, btc_stats=None,
        )
        sig_eth = assemble_signal(symbol="ETHUSDT", base="ETH", **kwargs)
        sig_doge = assemble_signal(symbol="DOGEUSDT", base="DOGE", **kwargs)
        assert sig_eth.total_score == pytest.approx(sig_doge.total_score)


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

    def test_buy_swaps_tp_when_target_beyond_tp2_projection(self):
        # Supply zone di 120 melewati proyeksi TP2 (1:3) -> TP1/TP2 ditukar agar
        # target terdekat jadi TP1: entry < TP1 < TP2 tetap terjaga.
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[(120, 122)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_BUY)
        sl_dist = price - sl
        assert tp1 == pytest.approx(price + 3 * sl_dist)
        assert tp2 == pytest.approx(120.0)
        assert sl < entry < tp1 < tp2

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

    def test_sell_swaps_tp_when_target_beyond_tp2_projection(self):
        # Demand zone di 82 melewati proyeksi TP2 (1:3) -> TP1/TP2 ditukar agar
        # target terdekat jadi TP1: entry > TP1 > TP2 tetap terjaga.
        price, h1_map = 100.0, self._map(demand=[(80, 82)], supply=[(101, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_SELL)
        sl_dist = sl - price
        assert tp1 == pytest.approx(price - 3 * sl_dist)
        assert tp2 == pytest.approx(82.0)
        assert sl > entry > tp1 > tp2

    def test_neutral_fallback_fixed_levels(self):
        price = 100.0
        entry, sl, tp1, tp2 = _levels_mtf(price, [], {}, ACTION_NEUTRAL)
        assert entry == price
        assert sl == pytest.approx(price * 0.97)
        assert tp1 == pytest.approx(price * (1 + 1.5 * 0.03))
        assert tp2 == pytest.approx(price * (1 + 3.0 * 0.03))

    def test_bad_rr_signal_rejected_to_neutral(self, monkeypatch):
        """Setup BUY kuat namun `_levels_mtf` menolak RRR -> NEUTRAL + alasan [RR]."""
        import engine as engine_mod

        # Force RR-rejection hanya untuk BUY; fallback NEUTRAL tetap memakai level
        # kosmetik asli agar jalur NEUTRAL + [RR] deterministik.
        real_levels_mtf = engine_mod._levels_mtf

        def _reject_buy_only(price, h1_candles, h1_map, action, **kwargs):
            if action == ACTION_BUY:
                return None
            return real_levels_mtf(price, h1_candles, h1_map, action, **kwargs)

        monkeypatch.setattr(engine_mod, "_levels_mtf", _reject_buy_only)

        price, h4, d1, h1, m15 = _bullish_mtf()
        sig = assemble_signal(
            symbol="BTCUSDT", base="BTC", price=price, pct_change_24h=5.2,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=None, btc_stats=None,
        )
        assert sig.action == ACTION_NEUTRAL
        assert any("[RR]" in r for r in sig.reasons)
        assert sig.entry > 0  # level fallback kosmetik tetap ada


class TestBlockedByZone:
    """Fix #2: `_blocked_by_zone` harus mendeteksi harga yang sudah di dalam zona."""

    def test_price_inside_supply_blocks_buy(self):
        # Harga 100 sudah DI DALAM supply zone (99..102) -> jalur ke TP1 terblokir.
        zones = [{"type": "supply", "low": 99.0, "high": 102.0}]
        assert _blocked_by_zone(100.0, 110.0, zones, "supply") is True

    def test_price_inside_demand_blocks_sell(self):
        # Harga 100 sudah DI DALAM demand zone (98..101) -> jalur SELL terblokir.
        zones = [{"type": "demand", "low": 98.0, "high": 101.0}]
        assert _blocked_by_zone(90.0, 100.0, zones, "demand") is True

    def test_zone_below_entry_not_blocking(self):
        # Supply zone di bawah Entry (BUY): harga bergerak naik, tidak terblokir.
        zones = [{"type": "supply", "low": 90.0, "high": 95.0}]
        assert _blocked_by_zone(100.0, 110.0, zones, "supply") is False

    def test_zone_above_target_not_blocking(self):
        # Supply zone jauh di atas TP1: tidak memotong jalur.
        zones = [{"type": "supply", "low": 112.0, "high": 115.0}]
        assert _blocked_by_zone(100.0, 110.0, zones, "supply") is False

    def test_wrong_zone_type_ignored(self):
        zones = [{"type": "demand", "low": 105.0, "high": 108.0}]
        assert _blocked_by_zone(100.0, 110.0, zones, "supply") is False


class TestSwapBlocked:
    """Fix #1: swap TP1/TP2 saat target melewati proyeksi TP2 wajib cek zona."""

    def test_buy_swap_blocked_by_supply_inside_path(self):
        # Target jauh (120) melewati proyeksi TP2 (swap), tapi supply zone
        # (98..108) mencakup harga & jalur proyeksi -> swap dibatalkan (None).
        price, h1_map = 100.0, TestLevelsRRR._map(demand=[(97, 99)], supply=[(98, 108), (120, 122)])
        assert _levels_mtf(price, [], h1_map, ACTION_BUY) is None

    def test_sell_swap_blocked_by_demand_inside_path(self):
        # Target jauh (80) melewati proyeksi TP2, tapi ada demand zone (96..98)
        # yang memotong jalur SELL -> swap harus dibatalkan (None).
        price, h1_map = 100.0, TestLevelsRRR._map(demand=[(80, 82), (96, 98)], supply=[(101, 103)])
        assert _levels_mtf(price, [], h1_map, ACTION_SELL) is None


class TestObNear:
    """Fix #4: OB hanya valid bila harga di dalam zona atau berjarak <= 2%."""

    def test_none_ob_invalid(self):
        assert _ob_near(100.0, None) is False

    def test_far_ob_invalid(self):
        ob = {"type": "bullish", "low": 90.0, "high": 95.0}
        assert _ob_near(100.0, ob) is False

    def test_near_ob_valid(self):
        ob = {"type": "bullish", "low": 98.0, "high": 99.0}
        assert _ob_near(100.0, ob) is True

    def test_price_inside_ob_valid(self):
        ob = {"type": "bullish", "low": 98.0, "high": 101.0}
        assert _ob_near(100.0, ob) is True

    def test_setup_valid_rejects_far_bullish_ob(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "sweeps": [],
            "bullish_ob": {"type": "bullish", "low": 90.0, "high": 95.0},
        }
        trigger = {"histogram": 0.5, "cross": None, "bos": "bullish", "choch": None}
        assert _setup_valid(ACTION_BUY, h1_map, trigger) is False

    def test_setup_valid_accepts_near_bullish_ob(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "sweeps": [],
            "bullish_ob": {"type": "bullish", "low": 98.0, "high": 99.0},
        }
        trigger = {"histogram": 0.5, "cross": None, "bos": "bullish", "choch": None}
        assert _setup_valid(ACTION_BUY, h1_map, trigger) is True

    def test_setup_valid_rejects_far_bearish_ob(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "sweeps": [],
            "bearish_ob": {"type": "bearish", "low": 105.0, "high": 110.0},
        }
        trigger = {"histogram": -0.5, "cross": None, "bos": None, "choch": "bearish"}
        assert _setup_valid(ACTION_SELL, h1_map, trigger) is False

    def test_setup_valid_accepts_near_bearish_ob(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "sweeps": [],
            "bearish_ob": {"type": "bearish", "low": 100.5, "high": 102.0},
        }
        trigger = {"histogram": -0.5, "cross": None, "bos": None, "choch": "bearish"}
        assert _setup_valid(ACTION_SELL, h1_map, trigger) is True


class TestFvgIndependent:
    """Fix #6: FVG bullish & bearish dievaluasi independen (bukan if/elif)."""

    def test_both_fvg_directions_scored(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "zones": [],
            "order_blocks": [],
            "fvgs": [
                {"type": "bullish", "bottom": 95.0, "top": 97.0, "index": 1},
                {"type": "bearish", "bottom": 103.0, "top": 105.0, "index": 2},
            ],
            "sweeps": [],
            "levels": {"support_dist_pct": None, "resistance_dist_pct": None},
            "bullish_ob": None,
            "bearish_ob": None,
        }
        score, reasons = score_smc(100.0, h1_map)
        bull = [r for r in reasons if "FVG bullish" in r]
        bear = [r for r in reasons if "FVG bearish" in r]
        assert bull and bear  # keduanya dievaluasi (independen, bukan if/elif)
        assert score == pytest.approx(0.25 - 0.25)  # FVG bull +0.25, FVG bear -0.25
        assert -1.0 <= score <= 1.0

    def test_fvg_only_bearish_scores_negative(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "zones": [],
            "order_blocks": [],
            "fvgs": [
                {"type": "bearish", "bottom": 103.0, "top": 105.0, "index": 2},
            ],
            "sweeps": [],
            "levels": {"support_dist_pct": None, "resistance_dist_pct": None},
            "bullish_ob": None,
            "bearish_ob": None,
        }
        score, _ = score_smc(100.0, h1_map)
        assert score == pytest.approx(-0.25)


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
            breakdown={"sr": 0.5, "smc": 0.5, "fibo": 0.5, "ema": 0.5, "teknikal": 0.5, "onchain": 0.5, "sentimen": 0.5},
        )
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "🛡️ SL: <b>$96.00</b> (-4.00%)" in message
        assert "🎯 TP1: <b>$106.00</b> (+6.00%)" in message
        assert "🎯 TP2: <b>$112.00</b> (+12.00%)" in message
        assert "━━━━━━━━━━━━" in message

    def test_signal_lines_include_level_pct_sell(self):
        sig = Signal(
            "ETHUSDT", "ETH", 100.0, -5.2, -0.5, "SELL", 70, 100.0, 104.0, 96.0, 92.0,
            breakdown={"sr": -0.5, "smc": -0.5, "fibo": -0.5, "ema": -0.5, "teknikal": -0.5, "onchain": -0.5, "sentimen": -0.5},
        )
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "🛡️ SL: <b>$104.00</b> (-4.00%)" in message
        assert "🎯 TP1: <b>$96.00</b> (+4.00%)" in message
        assert "🎯 TP2: <b>$92.00</b> (+8.00%)" in message


class TestConfigWeights:
    """Pembobotan strategi baru v2.4 (total harus 1.00)."""

    def test_weights_sum_to_one(self):
        import config

        total = (
            config.WEIGHT_SR
            + config.WEIGHT_SMC
            + config.WEIGHT_FIBO
            + config.WEIGHT_EMA
            + config.WEIGHT_TECHNICAL
            + config.WEIGHT_ONCHAIN
            + config.WEIGHT_SENTIMENT
        )
        assert total == pytest.approx(1.00)

    def test_sr_is_kompas_utama(self):
        import config

        assert config.WEIGHT_SR == pytest.approx(0.35)
        assert config.WEIGHT_SR > config.WEIGHT_SMC > config.WEIGHT_FIBO
        assert config.WEIGHT_FIBO == config.WEIGHT_EMA == pytest.approx(0.15)
        assert config.WEIGHT_SENTIMENT < config.WEIGHT_TECHNICAL < config.WEIGHT_FIBO


class TestScoreSR:
    """Skoring S&R (bobot 0.35) — kompas utama + key level H1."""

    def test_bullish_sr_positive(self):
        price, h4, d1, h1, m15 = _bullish_mtf()
        h1_map = map_h1_zones(h1, price)
        score, reasons = score_sr(price, h1_map, h4, d1)
        assert score > 0
        assert any("[H4]" in r for r in reasons)
        assert any("[H1]" in r for r in reasons)

    def test_bearish_sr_negative(self):
        price, h4, d1, h1, m15 = _bearish_mtf()
        h1_map = map_h1_zones(h1, price)
        score, reasons = score_sr(price, h1_map, h4, d1)
        assert score < 0
        assert any("[H4]" in r for r in reasons)

    def test_sr_dominates_total_score(self):
        # Setup bullish penuh: kontribusi S&R harus yang terbesar di breakdown.
        price, h4, d1, h1, m15 = _bullish_mtf()
        sig = assemble_signal(
            symbol="BTCUSDT", base="BTC", price=price, pct_change_24h=5.2,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[0.0001], ls_ratio=0.8,
            whale_flow=None, btc_stats=None,
        )
        assert sig.breakdown["sr"] >= max(
            sig.breakdown["smc"], sig.breakdown["fibo"],
            sig.breakdown["ema"], sig.breakdown["teknikal"],
            sig.breakdown["sentimen"], sig.breakdown["onchain"],
        )

    def test_sr_empty_map_in_range(self):
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "zones": [],
            "levels": {
                "support": None, "resistance": None,
                "support_dist_pct": None, "resistance_dist_pct": None,
            },
        }
        score, reasons = score_sr(100.0, h1_map, [], [])
        assert -1.0 <= score <= 1.0
        assert score == 0.0
        assert reasons == []


class TestScoreFibo:
    """Skoring Fibonacci Golden Zone (bobot 0.15)."""

    @staticmethod
    def _map(levels=None, order_blocks=()):
        return {
            "price": 97.0,
            "zones": [],
            "demand_zones": [],
            "supply_zones": [],
            "order_blocks": list(order_blocks),
            "levels": levels or {
                "support": None, "resistance": None,
                "support_dist_pct": None, "resistance_dist_pct": None,
            },
        }

    @staticmethod
    def _fibo(price):
        candles = [
            {"open": c, "high": c, "low": c, "close": c}
            for c in [95, 94, 93, 92, 90, 93, 97, 101, 105, 108, 110, 109, 107, 105]
        ]
        return analyze_fibonacci(candles, price)

    def test_in_golden_zone_positive_for_buy(self):
        fibo = self._fibo(97.0)
        score, reasons = score_fibo(fibo, 97.0, self._map(), ACTION_BUY)
        assert score > 0
        assert any("Golden Zone" in r for r in reasons)

    def test_golden_zone_negative_for_sell(self):
        fibo = self._fibo(97.0)
        score, _ = score_fibo(fibo, 97.0, self._map(), ACTION_SELL)
        assert score < 0

    def test_golden_zone_neutral_without_compass(self):
        fibo = self._fibo(97.0)
        score, _ = score_fibo(fibo, 97.0, self._map(), None)
        assert score == 0.0

    def test_confluence_with_sr_boosts_score(self):
        fibo = self._fibo(97.0)
        levels = {"support": 96.0, "resistance": None, "support_dist_pct": 1.0, "resistance_dist_pct": None}
        plain, _ = score_fibo(fibo, 97.0, self._map(), ACTION_BUY)
        with_sr, reasons = score_fibo(fibo, 97.0, self._map(levels=levels), ACTION_BUY)
        assert with_sr > plain
        assert any("Key Level S&R" in r for r in reasons)

    def test_confluence_with_ob_boosts_score(self):
        fibo = self._fibo(97.0)
        ob = {"type": "bullish", "low": 95.0, "high": 97.0, "index": 0}
        plain, _ = score_fibo(fibo, 97.0, self._map(), ACTION_BUY)
        with_ob, reasons = score_fibo(fibo, 97.0, self._map(order_blocks=[ob]), ACTION_BUY)
        assert with_ob > plain
        assert any("Order Block" in r for r in reasons)

    def test_far_from_golden_zone_zero(self):
        fibo = self._fibo(108.0)
        score, reasons = score_fibo(fibo, 108.0, self._map(), ACTION_BUY)
        assert score == 0.0
        assert reasons == []

    def test_invalid_fibo_zero(self):
        score, reasons = score_fibo({"ok": False}, 97.0, self._map(), ACTION_BUY)
        assert score == 0.0
        assert reasons == []


class TestScoreEma:
    """Skoring EMA 20/50 (bobot 0.15) — trend + pullback + RSI hook."""

    @staticmethod
    def _closes(closes):
        return [{"open": c, "high": c * 1.001, "low": c * 0.999, "close": c} for c in closes]

    def test_uptrend_pullback_with_rsi_hook_up(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        info = analyze_ema(self._closes(closes), ema_series(closes, 20)[-1] * 1.001)
        score, reasons = score_ema(info, rsi_now=35.0, rsi_prev=32.0)
        assert score == pytest.approx(0.30 + 0.30 + 0.40)
        assert any("Hook UP" in r for r in reasons)

    def test_uptrend_pullback_without_rsi_hook(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        info = analyze_ema(self._closes(closes), ema_series(closes, 20)[-1] * 1.001)
        score, reasons = score_ema(info, rsi_now=55.0, rsi_prev=52.0)
        assert score == pytest.approx(0.30 + 0.30)
        assert not any("Hook" in r for r in reasons)

    def test_downtrend_pullback_with_rsi_hook_down(self):
        closes = [200.0 - i * 0.5 for i in range(80)]
        info = analyze_ema(self._closes(closes), ema_series(closes, 20)[-1] * 0.999)
        score, reasons = score_ema(info, rsi_now=65.0, rsi_prev=68.0)
        assert score == pytest.approx(-(0.30 + 0.30 + 0.40))
        assert any("Hook DOWN" in r for r in reasons)

    def test_hook_rsi_wrong_zone_ignored(self):
        closes = [100.0 + i * 0.5 for i in range(80)]
        info = analyze_ema(self._closes(closes), ema_series(closes, 20)[-1] * 1.001)
        # RSI 45 di luar area 30-40 -> hook tidak diberi bonus.
        score, reasons = score_ema(info, rsi_now=45.0, rsi_prev=32.0)
        assert score == pytest.approx(0.60)
        assert not any("Hook" in r for r in reasons)

    def test_insufficient_data_zero(self):
        info = analyze_ema(self._closes([100.0] * 10), 100.0)
        score, reasons = score_ema(info, 50.0, 50.0)
        assert score == 0.0
        assert reasons == []
