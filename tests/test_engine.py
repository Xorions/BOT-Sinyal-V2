"""Test mesin skoring engine v2.3 (MTF SMC + S&D, tanpa network)."""

from typing import List

import pytest

from engine import (
    ACTION_BUY,
    ACTION_NEUTRAL,
    ACTION_SELL,
    Signal,
    _blocked_by_zone,
    _group_reason_lines,
    _hist_confirmed,
    _levels_mtf,
    _ob_near,
    _setup_valid,
    _trigger_confirmed,
    analyze_compass,
    analyze_trigger,
    assemble_signal,
    btc_regime,
    format_message,
    map_h1_zones,
    rank_signals,
    score_ema,
    score_fibo,
    score_smc,
    score_sr,
    score_trigger,
)
from config import RRR_MIN, RRR_TP2
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


class TestCompassEma50Filter:
    """Fix R3: kompas H4/D1 ditahan bila harga menembus sisi berlawanan EMA 50 H4."""

    def _trending(self, series):
        """Seri trending >= 50 bar agar EMA 50 H4 terhitung."""
        base = _bullish_series() if series == "bull" else _bearish_series()
        return _candles_from_closes(base * 2)

    def test_buy_blocked_when_price_below_ema50(self):
        h4 = self._trending("bull")
        d1 = _candles_from_closes(_bullish_series())
        price = _bullish_series()[-1]  # close terakhir seri tren
        compass = analyze_compass(h4, d1, price=price)
        assert compass["direction"] == ACTION_BUY  # harga di atas EMA50 -> tidak diblokir
        compass = analyze_compass(h4, d1, price=price * 0.5)
        assert compass["ema50_blocked"] is True
        assert compass["direction"] is None

    def test_sell_blocked_when_price_above_ema50(self):
        h4 = self._trending("bear")
        d1 = _candles_from_closes(_bearish_series())
        price = _bearish_series()[-1]
        compass = analyze_compass(h4, d1, price=price)
        assert compass["direction"] == ACTION_SELL
        compass = analyze_compass(h4, d1, price=price * 2.0)
        assert compass["ema50_blocked"] is True
        assert compass["direction"] is None

    def test_no_filter_without_price(self):
        h4 = self._trending("bull")
        d1 = _candles_from_closes(_bullish_series())
        compass = analyze_compass(h4, d1)
        assert compass["direction"] == ACTION_BUY
        assert compass["ema50_blocked"] is False

    def test_no_filter_when_insufficient_h4_bars(self):
        # < 50 bar H4 -> EMA 50 belum ada -> filter tidak aktif.
        h4 = _candles_from_closes(_bullish_series())
        d1 = _candles_from_closes(_bullish_series())
        price = _bullish_series()[-1]
        compass = analyze_compass(h4, d1, price=price * 0.5)
        assert compass["ema50_blocked"] is False
        assert compass["direction"] == ACTION_BUY


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


class TestTriggerStability:
    """Fix R2: konfirmasi M15 stabil — histogram harus searah selama N bar
    berturut-turut dengan margin, agar bar nyaris-nol tidak memutuskan setup.
    (Fix R4: TRIG_MIN_BARS=3 bar & margin 20% dari puncak.)"""

    def test_single_strong_bar_not_enough(self):
        # 1 bar kuat bukan konfirmasi: butuh TRIG_MIN_BARS=3 bar beruntun.
        hist = [0.0] * 60 + [2.0]
        assert _hist_confirmed(hist, True) is False

    def test_three_consecutive_bars_confirms(self):
        hist = [0.0] * 60 + [1.7, 1.8, 2.0]
        assert _hist_confirmed(hist, True) is True

    def test_last_bar_zero_breaks_confirmation(self):
        # Bar terakhir nyaris nol (noise) -> setup tidak konfirmasi walau bar
        # sebelumnya kuat. Inilah kasus yang dulu membuat valid/nevalid flip.
        hist = [0.0] * 60 + [2.0, 2.0, 0.01]
        assert _hist_confirmed(hist, True) is False

    def test_bear_direction_requires_negative_bars(self):
        hist = [0.0] * 60 + [-1.7, -1.8, -2.0]
        assert _hist_confirmed(hist, False) is True
        assert _hist_confirmed(hist, True) is False

    def test_margin_ignores_small_noise_bars(self):
        # Histogram sempat kecil (di bawah 20% puncak) = noise, bukan pembalikan.
        hist = [0.0] * 60 + [0.45, 1.8, 2.0]
        assert _hist_confirmed(hist, True) is True

    def test_nan_values_skipped(self):
        hist = [float("nan")] * 60 + [1.5, 1.6, 1.7]
        assert _hist_confirmed(hist, True) is True
        assert _hist_confirmed([float("nan")] * 70, True) is False

    def test_analyze_trigger_exposes_confirmation_flags(self):
        # Rally akselerasi akhir -> histogram MACD naik beruntun >= TRIG_MIN_BARS.
        closes = [100.0 + i for i in range(30)] + [130.0 + i * 3 for i in range(10)]
        trig = analyze_trigger(_candles_from_closes(closes))
        assert trig["hist_confirm_bull"] is True
        assert trig["hist_confirm_bear"] is False

    def test_manual_trigger_dict_fallback(self):
        # Dict trigger manual (tanpa key hist_confirm_*) tetap memakai tanda histogram.
        manual = {"histogram": 0.5, "cross": None, "bos": "bullish", "choch": None}
        assert _trigger_confirmed(manual, ACTION_BUY) is True
        assert _trigger_confirmed(manual, ACTION_SELL) is False
        manual_bear = {"histogram": -0.5, "cross": None, "bos": None, "choch": "bearish"}
        assert _trigger_confirmed(manual_bear, ACTION_SELL) is True

    def test_analyze_trigger_flag_overrides_fallback(self):
        # Flag eksplisit (False) mengalahkan tanda histogram (positif) — ini yang
        # mencegah trigger "M15 benar tapi hist 1 bar terakhir sudah membalik".
        trig = {"histogram": 0.5, "hist_confirm_bull": False, "cross": None, "bos": None, "choch": None}
        assert _trigger_confirmed(trig, ACTION_BUY) is False


class TestRsiContrarianNeedsMomentum:
    """Fix R2: skor RSI contrarian tidak boleh kontradiksi arah histogram MACD."""

    def _score(self, rsi_val, hist, structure=None, pct=0.0, monkeypatch=None):
        import engine as engine_mod
        candles = _candles_from_closes([100.0] * 40)
        monkeypatch.setattr(engine_mod, "rsi", lambda *a, **k: rsi_val)
        monkeypatch.setattr(
            engine_mod, "macd_histogram_series",
            lambda *a, **k: hist,
        )
        monkeypatch.setattr(
            engine_mod, "detect_structure",
            lambda *a, **k: structure or {"bos": None, "choch": None},
        )
        return engine_mod.score_trigger(candles, pct)

    def test_rsi_oversold_requires_bullish_histogram(self, monkeypatch):
        # RSI<30 TAPI histogram turun (histogram negatif) -> TIDAK ada "RSI Rebound"
        # (dulu tetap +0.20, meracuni setup SELL yang RSI-nya oversold).
        hist = [float("nan")] * 38 + [-1.5, -1.6]
        score, reasons = self._score(25.0, hist, monkeypatch=monkeypatch)
        assert not any("RSI Rebound" in r for r in reasons)

    def test_rsi_oversold_with_bullish_histogram_rebounds(self, monkeypatch):
        hist = [float("nan")] * 37 + [1.5, 1.6, 1.7]
        score, reasons = self._score(25.0, hist, monkeypatch=monkeypatch)
        assert any("RSI Rebound" in r for r in reasons)

    def test_rsi_overbought_requires_bearish_histogram(self, monkeypatch):
        hist = [float("nan")] * 38 + [1.5, 1.6]
        score, reasons = self._score(75.0, hist, monkeypatch=monkeypatch)
        assert not any("RSI Melemah" in r for r in reasons)

    def test_rsi_overbought_with_bearish_histogram_weakens(self, monkeypatch):
        hist = [float("nan")] * 37 + [-1.5, -1.6, -1.7]
        score, reasons = self._score(75.0, hist, monkeypatch=monkeypatch)
        assert any("RSI Melemah" in r for r in reasons)


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
    """Aturan RRR di `_levels_mtf`: SL zona + buffer, TP1 >= RRR_MIN x SL, TP2 = RRR_TP2 x SL."""

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
        # Supply zone 105 melewati proyeksi TP2 (1:RRR_TP2) -> TP1/TP2 ditukar:
        # TP1 = proyeksi TP2 (terdekat), TP2 = 105 (target zona struktur).
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[(105, 107)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_BUY)
        assert sl == pytest.approx(97 * (1 - 0.003))
        assert tp1 == pytest.approx(100 + RRR_TP2 * (100 - sl))
        assert tp2 == pytest.approx(105.0)
        assert sl < entry < tp1 < tp2

    def test_buy_forced_projection_when_nearest_target_too_close(self):
        # Swing high 102 terlalu dekat (< RRR_MIN x SL) tapi tidak terhalang zona -> TP1 = proyeksi RRR_MIN x SL.
        candles = [
            {"high": 99, "low": 98}, {"high": 100, "low": 99}, {"high": 101, "low": 100},
            {"high": 102, "low": 100}, {"high": 101, "low": 99}, {"high": 100, "low": 98},
            {"high": 99, "low": 97},
        ]
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[])
        entry, sl, tp1, tp2 = _levels_mtf(price, candles, h1_map, ACTION_BUY)
        sl_dist = price - sl
        assert tp1 == pytest.approx(price + RRR_MIN * sl_dist)
        assert tp2 == pytest.approx(price + RRR_TP2 * (price - sl))
        assert sl < entry < tp1 < tp2

    def test_buy_blocked_by_supply_zone_returns_none(self):
        # Supply zone di 102 terlalu dekat DAN terhalang -> sinyal dibatalkan.
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[(102, 103)])
        assert _levels_mtf(price, [], h1_map, ACTION_BUY) is None

    def test_buy_not_blocked_by_supply_zone_containing_entry(self):
        # Trap lama: supply zone BERISI Entry (99..103) + swing high 100.5 terlalu
        # dekat -> forced TP1 sebelumnya selalu dianggap "terblokir" -> NEUTRAL.
        # Kini zona yang berisi Entry tidak memblokir BUY (harga keluar zona saat
        # naik), sehingga setup valid tetap jadi BUY.
        candles = [
            {"open": 99.2, "high": 99.0, "low": 98.0, "close": 99.0},
            {"open": 99.5, "high": 100.0, "low": 99.0, "close": 99.8},
            {"open": 99.8, "high": 100.2, "low": 99.3, "close": 100.1},
            {"open": 100.1, "high": 100.5, "low": 99.4, "close": 100.4},
            {"open": 100.4, "high": 100.3, "low": 99.5, "close": 100.0},
            {"open": 100.0, "high": 100.1, "low": 99.2, "close": 99.5},
            {"open": 99.5, "high": 100.0, "low": 99.0, "close": 99.8},
        ]
        price, h1_map = 100.0, self._map(demand=[(98, 102)], supply=[(99, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, candles, h1_map, ACTION_BUY)
        assert sl < entry < tp1 < tp2
        assert tp1 > price  # proyeksi RRR, bukan ditolak jadi NEUTRAL

    def test_buy_swaps_tp_when_target_beyond_tp2_projection(self):
        # Supply zone di 120 melewati proyeksi TP2 (1:RRR_TP2) -> TP1/TP2 ditukar agar
        # target terdekat jadi TP1: entry < TP1 < TP2 tetap terjaga.
        price, h1_map = 100.0, self._map(demand=[(97, 99)], supply=[(120, 122)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_BUY)
        sl_dist = price - sl
        assert tp1 == pytest.approx(price + RRR_TP2 * sl_dist)
        assert tp2 == pytest.approx(120.0)
        assert sl < entry < tp1 < tp2

    def test_sell_valid_target_uses_nearest_demand_zone(self):
        # Demand zone 95 melewati proyeksi TP2 (1:RRR_TP2) -> TP1/TP2 ditukar:
        # TP1 = proyeksi TP2 (terdekat), TP2 = 95 (target zona struktur).
        price, h1_map = 100.0, self._map(demand=[(93, 95)], supply=[(101, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_SELL)
        assert sl == pytest.approx(103 * (1 + 0.003))
        assert tp1 == pytest.approx(100 - RRR_TP2 * (sl - price))
        assert tp2 == pytest.approx(95.0)
        assert sl > entry > tp1 > tp2

    def test_sell_forced_projection_when_nearest_target_too_close(self):
        # Swing low 98 terlalu dekat tapi tidak terhalang zona -> TP1 = proyeksi RRR_MIN x SL.
        candles = [
            {"high": 102, "low": 101}, {"high": 101, "low": 100}, {"high": 100, "low": 99},
            {"high": 99, "low": 98}, {"high": 100, "low": 99}, {"high": 101, "low": 100},
            {"high": 102, "low": 101},
        ]
        price, h1_map = 100.0, self._map(demand=[], supply=[(101, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, candles, h1_map, ACTION_SELL)
        sl_dist = sl - price
        assert tp1 == pytest.approx(price - RRR_MIN * sl_dist)
        assert tp2 == pytest.approx(price - RRR_TP2 * (sl - price))
        assert sl > entry > tp1 > tp2

    def test_sell_blocked_by_demand_zone_returns_none(self):
        price, h1_map = 100.0, self._map(demand=[(98, 99)], supply=[(101, 103)])
        assert _levels_mtf(price, [], h1_map, ACTION_SELL) is None

    def test_sell_swaps_tp_when_target_beyond_tp2_projection(self):
        # Demand zone di 82 melewati proyeksi TP2 (1:RRR_TP2) -> TP1/TP2 ditukar agar
        # target terdekat jadi TP1: entry > TP1 > TP2 tetap terjaga.
        price, h1_map = 100.0, self._map(demand=[(80, 82)], supply=[(101, 103)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_SELL)
        sl_dist = sl - price
        assert tp1 == pytest.approx(price - RRR_TP2 * sl_dist)
        assert tp2 == pytest.approx(82.0)
        assert sl > entry > tp1 > tp2

    def test_neutral_fallback_fixed_levels(self):
        price = 100.0
        entry, sl, tp1, tp2 = _levels_mtf(price, [], {}, ACTION_NEUTRAL)
        assert entry == price
        assert sl == pytest.approx(price * 0.97)
        assert tp1 == pytest.approx(price * (1 + RRR_MIN * 0.03))
        assert tp2 == pytest.approx(price * (1 + RRR_TP2 * 0.03))

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
    """`_blocked_by_zone`: hanya zona SELURUHNYA di atas/bawah Entry yang blokir.

    Zona yang BERISI Entry TIDAK memblokir — saat BUY harga bergerak keluar
    dari zona (naik); saat SELL keluar dari zona (turun).
    """

    def test_price_inside_supply_does_not_block_buy(self):
        # Harga 100 sudah DI DALAM supply zone (99..102) -> bukan blokir BUY.
        zones = [{"type": "supply", "low": 99.0, "high": 102.0}]
        assert _blocked_by_zone(100.0, 110.0, zones, "supply") is False

    def test_price_inside_demand_does_not_block_sell(self):
        # Harga 100 sudah DI DALAM demand zone (98..101) -> bukan blokir SELL.
        zones = [{"type": "demand", "low": 98.0, "high": 101.0}]
        assert _blocked_by_zone(90.0, 100.0, zones, "demand") is False

    def test_supply_above_entry_between_entry_and_tp_blocks_buy(self):
        # Supply zone di 103..106 sepenuhnya di atas Entry 100 & di jalur ke TP -> blokir.
        zones = [{"type": "supply", "low": 103.0, "high": 106.0}]
        assert _blocked_by_zone(100.0, 110.0, zones, "supply") is True

    def test_demand_below_entry_between_tp_and_entry_blocks_sell(self):
        # Demand zone di 94..97 sepenuhnya di bawah Entry 100 & di jalur SELL -> blokir.
        zones = [{"type": "demand", "low": 94.0, "high": 97.0}]
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


class TestZoneReasonsDirectionAware:
    """Fix kosmetik: alasan zona tidak menampilkan sisi berlawanan kompas.

    Karena zona yang berisi Entry tidak lagi memblokir, sinyal BUY tidak perlu
    lagi dilaporkan "Harga masuk Supply Zone" (dan SELL tidak "masuk Demand
    Zone") — hanya sisi zona yang searah bias yang dilaporkan & dinilai.
    """

    @staticmethod
    def _h1_map(price):
        demand = [{"type": "demand", "low": price - 3.0, "high": price + 1.0}]
        supply = [{"type": "supply", "low": price - 1.0, "high": price + 3.0}]
        return {
            "zones": demand + supply,
            "demand_zones": demand,
            "supply_zones": supply,
            "order_blocks": [],
            "levels": {"support": None, "resistance": None},
        }

    def test_buy_reports_only_demand_side(self):
        price, h1_map = 100.0, self._h1_map(100.0)
        h4 = _candles_from_closes(_bullish_series())
        d1 = _candles_from_closes(_bullish_series())
        _, reasons = score_sr(price, h1_map, h4, d1)
        assert "[H1] Harga masuk Demand Zone" in reasons
        assert "[H1] Harga masuk Supply Zone" not in reasons

    def test_sell_reports_only_supply_side(self):
        price, h1_map = 100.0, self._h1_map(100.0)
        h4 = _candles_from_closes(_bearish_series())
        d1 = _candles_from_closes(_bearish_series())
        _, reasons = score_sr(price, h1_map, h4, d1)
        assert "[H1] Harga masuk Supply Zone" in reasons
        assert "[H1] Harga masuk Demand Zone" not in reasons

    def test_neutral_compass_reports_both(self):
        price, h1_map = 100.0, self._h1_map(100.0)
        h4 = _candles_from_closes(_neutral_series())
        _, reasons = score_sr(price, h1_map, h4, [])
        assert "[H1] Harga masuk Demand Zone" in reasons
        assert "[H1] Harga masuk Supply Zone" in reasons


class TestSwapBlocked:
    """Swap TP1/TP2 saat target melewati proyeksi TP2 wajib cek zona."""

    def test_buy_swap_succeeds_when_zone_contains_entry(self):
        # Target jauh (120) melewati proyeksi TP2 (swap). Supply zone (98..108)
        # BERISI Entry -> TIDAK memblokir (harga keluar zona saat naik),
        # sehingga swap berjalan normal: entry < TP1 < TP2.
        price, h1_map = 100.0, TestLevelsRRR._map(demand=[(97, 99)], supply=[(98, 108), (120, 122)])
        entry, sl, tp1, tp2 = _levels_mtf(price, [], h1_map, ACTION_BUY)
        sl_dist = price - sl
        assert tp1 == pytest.approx(price + RRR_TP2 * sl_dist)
        assert tp2 == pytest.approx(120.0)
        assert sl < entry < tp1 < tp2

    def test_sell_swap_blocked_by_demand_inside_path(self):
        # Target jauh (80) melewati proyeksi TP2; demand zone (96..98) di bawah
        # Entry memotong jalur SELL -> swap dibatalkan (None).
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

    def test_fvg_multiple_gaps_score_once(self):
        # Fix double-counting: banyak FVG bullish hanya menambah +0.25 SEKALI
        # (kehadiran per tipe), bukan +0.25 per-gap.
        h1_map = {
            "price": 100.0,
            "demand_zones": [],
            "supply_zones": [],
            "zones": [],
            "order_blocks": [],
            "fvgs": [
                {"type": "bullish", "bottom": 95.0, "top": 97.0, "index": 1},
                {"type": "bullish", "bottom": 94.0, "top": 96.0, "index": 3},
                {"type": "bullish", "bottom": 93.0, "top": 95.5, "index": 4},
            ],
            "sweeps": [],
            "levels": {"support_dist_pct": None, "resistance_dist_pct": None},
            "bullish_ob": None,
            "bearish_ob": None,
        }
        score, reasons = score_smc(100.0, h1_map)
        assert score == pytest.approx(0.25)
        assert len([r for r in reasons if "FVG bullish" in r]) == 1


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
        assert "• Trend (H4)" in message
        assert "• Zona/SMC (H1)" in message
        assert "• M15" in message
        assert "💹 24j:" in message

    def test_signal_lines_include_level_pct_buy(self):
        sig = Signal(
            "BTCUSDT", "BTC", 102.0, 5.2, 0.5, "BUY", 70, 100.0, 96.0, 106.0, 112.0,
            breakdown={"sr": 0.5, "smc": 0.5, "fibo": 0.5, "ema": 0.5, "teknikal": 0.5, "onchain": 0.5, "sentimen": 0.5},
        )
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "🛡️ SL: <b>$96.00</b> (-4.00%)" in message
        assert "🎯 TP1: <b>$106.00</b> (+6.00%)" in message
        assert "🎯 TP2: <b>$112.00</b> (+12.00%)" in message
        assert "────" in message

    def test_signal_lines_include_level_pct_sell(self):
        sig = Signal(
            "ETHUSDT", "ETH", 100.0, -5.2, -0.5, "SELL", 70, 100.0, 104.0, 96.0, 92.0,
            breakdown={"sr": -0.5, "smc": -0.5, "fibo": -0.5, "ema": -0.5, "teknikal": -0.5, "onchain": -0.5, "sentimen": -0.5},
        )
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "🛡️ SL: <b>$104.00</b> (-4.00%)" in message
        assert "🎯 TP1: <b>$96.00</b> (+4.00%)" in message
        assert "🎯 TP2: <b>$92.00</b> (+8.00%)" in message

    def test_message_new_alert_header_first_line(self):
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
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB", "Fear&Greed: 29")
        first = message.splitlines()[0]
        assert first == "<b>🚨 NEW SIGNAL ALERTS 🚨</b>"

    def test_message_updated_title_sr_smc_fibo_ema(self):
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
        message = format_message(rank_signals([sig]), "Jumat, 07 Agu 2026, 13:30 WIB")
        assert "📊 DAY TRADING BRIEFING — MTF S&amp;R + SMC + FIBO + EMA" in message
        assert "MTF SMC + S&amp;D" not in message


class TestGroupReasonLinesDedup:
    """Fix: FVG / Liquidity Sweep tidak dicetak berulang-ulang per baris."""

    def test_fvg_and_sweep_deduplicated_with_count(self):
        reasons = [
            "[H4] S&R skala besar Bullish",
            "[H1] Harga masuk Demand Zone",
            "[H1] FVG bullish tervalidasi di bawah harga",
            "[H1] FVG bullish tervalidasi di bawah harga",
            "[H1] FVG bullish tervalidasi di bawah harga",
            "[H1] Liquidity Sweep tereksekusi (EQL tersapu)",
            "[H1] Liquidity Sweep tereksekusi (EQL tersapu)",
            "[H1] Bullish OB di bawah harga",
            "[M15] RSI Rebound",
        ]
        lines = _group_reason_lines(reasons)
        joined = "\n".join(lines)
        assert "FVG bullish tervalidasi di bawah harga (x3)" in joined
        assert "Liquidity Sweep tereksekusi (EQL tersapu) (x2)" in joined
        assert joined.count("FVG bullish") == 1  # tidak berulang baris terpisah
        assert joined.count("Liquidity Sweep") == 1
        assert "Harga masuk Demand Zone" in joined
        assert "(x1)" not in joined

    def test_dedup_collapses_single_repeated_item_to_inline(self):
        lines = _group_reason_lines(
            ["[H1] FVG bullish tervalidasi di bawah harga"] * 2
        )
        assert lines == ["    + [H1] FVG bullish tervalidasi di bawah harga (x2)"]

    def test_unique_items_untouched(self):
        lines = _group_reason_lines(
            ["[H1] Harga masuk Demand Zone", "[H1] Bullish OB di bawah harga"]
        )
        assert lines == [
            "    + [H1] ",
            "       - Harga masuk Demand Zone",
            "       - Bullish OB di bawah harga",
        ]


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

    def test_ema50_blocked_skips_trend_score(self):
        # Fix R3: kompas ditahan (harga menembus sisi berlawanan EMA 50 H4) ->
        # blok skor tren H4/D1 dihilangkan, diganti alasan penahanan.
        price = 100.0
        demand = [{"type": "demand", "low": 98.0, "high": 101.0}]
        h1_map = {
            "price": price,
            "demand_zones": demand,
            "supply_zones": [],
            "zones": demand,
            "levels": {
                "support": None, "resistance": None,
                "support_dist_pct": None, "resistance_dist_pct": None,
            },
        }
        h4 = _candles_from_closes(_bullish_series())
        d1 = _candles_from_closes(_bullish_series())
        blocked_compass = {
            "direction": None,
            "h4_trend": "bullish",
            "d1_trend": "bullish",
            "h4_bos": None,
            "h4_choch": None,
            "d1_bos": None,
            "d1_choch": None,
            "ema50_blocked": True,
        }
        score, reasons = score_sr(price, h1_map, h4, d1, compass=blocked_compass)
        assert not any("S&R skala besar" in r for r in reasons)
        assert any("Kompas ditahan" in r for r in reasons)
        assert score == pytest.approx(0.25)  # hanya zona Demand H1, tanpa +0.35 tren


class TestRankSignalsPriority:
    """Fix R1: BUY/SELL diprioritaskan di atas NEUTRAL pada top signal."""

    def _sig(self, action, score):
        return Signal(
            "XUSDT", "X", 100.0, 0.0, score, action, 50, 100.0, 99.0, 102.0, 104.0,
            breakdown={}, reasons=[],
        )

    def test_buy_sell_beat_high_scored_neutral(self):
        # NEUTRAL ber-skore tinggi (0.6-0.7) dulu bisa menekan BUY/SELL keluar
        # dari top-5 (kasus 13:30). Sekarang BUY/SELL didahulukan apa pun skornya.
        neutrals = [self._sig(ACTION_NEUTRAL, 0.66), self._sig(ACTION_NEUTRAL, 0.60)]
        buys = [self._sig(ACTION_BUY, 0.42), self._sig(ACTION_BUY, 0.30)]
        ranked = rank_signals(neutrals + buys)
        assert [s.action for s in ranked] == [
            ACTION_BUY, ACTION_BUY, ACTION_NEUTRAL, ACTION_NEUTRAL,
        ]

    def test_neutral_fills_remaining_slots(self):
        import config

        buys = [self._sig(ACTION_BUY, s) for s in (0.5, 0.4, 0.3)]
        neutrals = [self._sig(ACTION_NEUTRAL, 0.9), self._sig(ACTION_NEUTRAL, 0.8)]
        top = rank_signals(buys + neutrals)
        assert [s.action for s in top] == [
            ACTION_BUY, ACTION_BUY, ACTION_BUY, ACTION_NEUTRAL, ACTION_NEUTRAL,
        ]
        assert len(top) == min(5, config.TOP_SIGNALS)

    def test_directional_sorted_by_abs_score(self):
        sigs = [
            self._sig(ACTION_SELL, -0.55),
            self._sig(ACTION_BUY, 0.30),
            self._sig(ACTION_BUY, 0.70),
        ]
        ranked = rank_signals(sigs)
        assert [s.total_score for s in ranked] == [0.70, -0.55, 0.30]

    def test_empty_and_single(self):
        assert rank_signals([]) == []
        one = self._sig(ACTION_NEUTRAL, 0.1)
        assert rank_signals([one]) == [one]


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

    def test_ema_opposing_compass_neutralized(self):
        # Fix alignment: EMA uptrend tapi kompas SELL -> dinetralkan (0.0).
        closes = [100.0 + i * 0.5 for i in range(80)]
        info = analyze_ema(self._closes(closes), ema_series(closes, 20)[-1] * 1.001)
        score, reasons = score_ema(info, rsi_now=35.0, rsi_prev=32.0, compass_dir=ACTION_SELL)
        assert score == 0.0
        assert any("berlawanan arah kompas" in r for r in reasons)

    def test_ema_aligned_compass_scored(self):
        # EMA uptrend selaras kompas BUY -> skor normal tetap dihitung.
        closes = [100.0 + i * 0.5 for i in range(80)]
        info = analyze_ema(self._closes(closes), ema_series(closes, 20)[-1] * 1.001)
        score, reasons = score_ema(info, rsi_now=35.0, rsi_prev=32.0, compass_dir=ACTION_BUY)
        assert score == pytest.approx(0.30 + 0.30 + 0.40)
        assert not any("berlawanan arah kompas" in r for r in reasons)


class TestBtcRegime:
    """Filter Trend Induk (BTC Market Regime): BTC bearish -> BUY altcoin diblokir.

    Audit 12-Aug-2026: saat sinyal PENGU/ETH/LINK lahir, BTC M15/H1 masih bullish
    (BOS) tapi H4/D1 bearish — 3/3 sinyal BUY kena SL (dump altcoin > dump BTC).
    Regime bearish = ADA SATU timeframe bearish (struktur CHoCH / EMA 20<50).
    """

    def test_any_bearish_tf_yields_bearish_regime(self):
        # Mirror audit: M15/H1 bullish, H4/D1 bearish -> regime BEARISH (harus blokir).
        bull = _candles_from_closes(_bullish_series())
        bear = _candles_from_closes(_bearish_series())
        reg = btc_regime(price=148.0, m15_candles=bull, h1_candles=bull, h4_candles=bear, d1_candles=bear)
        assert reg["regime"] == "bearish"
        assert reg["verdicts"]["4h"] == "bearish"
        assert reg["verdicts"]["1d"] == "bearish"

    def test_bearish_m15_alone_blocks(self):
        # Aturan user: BTC bearish M15 -> dilarang BUY (tanpa perlu H4/D1).
        reg = btc_regime(price=148.0, m15_candles=_candles_from_closes(_bearish_series()))
        assert reg["regime"] == "bearish"

    def test_no_bearish_tf_neutral_regime(self):
        bull = _candles_from_closes(_bullish_series())
        reg = btc_regime(price=102.0, m15_candles=bull, h1_candles=bull, h4_candles=bull, d1_candles=bull)
        assert reg["regime"] == "bullish"

    def test_missing_data_graceful_neutral(self):
        reg = btc_regime(price=None)
        assert reg["regime"] == "neutral"
        assert reg["verdicts"] == {}

    def test_buy_blocked_when_btc_bearish(self):
        price, h4, d1, h1, m15 = _bullish_mtf()
        kwargs = dict(
            symbol="PENGUUSDT", base="PENGU", price=price, pct_change_24h=3.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[], ls_ratio=None,
            whale_flow=None, btc_stats=None,
        )
        base = assemble_signal(**kwargs)
        assert base.action == ACTION_BUY
        blocked = assemble_signal(
            **kwargs,
            btc_regime_info={"regime": "bearish", "reason": "BTC BEARISH (4h:bearish, 1d:bearish)"},
        )
        assert blocked.action == ACTION_NEUTRAL
        assert blocked.entry == base.entry
        assert any("[BTC]" in r and "diblokir" in r for r in blocked.reasons)

    def test_buy_allowed_when_btc_not_bearish(self):
        price, h4, d1, h1, m15 = _bullish_mtf()
        sig = assemble_signal(
            symbol="PENGUUSDT", base="PENGU", price=price, pct_change_24h=3.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=29.0, funding_rates=[], ls_ratio=None,
            whale_flow=None, btc_stats=None,
            btc_regime_info={"regime": "neutral", "reason": "BTC NEUTRAL"},
        )
        assert sig.action == ACTION_BUY
        assert not any("diblokir" in r for r in sig.reasons)

    def test_sell_not_blocked_by_btc_bearish(self):
        # Filter hanya melarang BUY saat BTC bearish — SELL tetap diizinkan.
        price, h4, d1, h1, m15 = _bearish_mtf()
        sig = assemble_signal(
            symbol="LINKUSDT", base="LINK", price=price, pct_change_24h=-3.0,
            h4_candles=h4, d1_candles=d1, h1_candles=h1, m15_candles=m15,
            fg_value=80.0, funding_rates=[], ls_ratio=None,
            whale_flow=None, btc_stats=None,
            btc_regime_info={"regime": "bearish", "reason": "BTC BEARISH"},
        )
        assert sig.action == ACTION_SELL
