"""Mesin skoring sinyal v2.4 — Day Trading Multi-Timeframe (S&R + SMC + Fibo + EMA).

Alur analisa 3 lapis:
  [Kompas H4/D1]   -> arah utama (BUY bila bullish, SELL bila bearish; BOS/CHoCH skala besar).
  [Pemetaan H1]    -> S&R key level (kompas utama), area institusional SMC (OB, FVG,
                      Liquidity Sweep), Fibonacci Golden Zone (0.5/0.618/0.786),
                      EMA 20/50 dynamic S/R + pullback. Entry/SL/TP dari zona H1.
  [Pelatuk M15]    -> konfirmasi eksekusi akhir (RSI / MACD cross / momentum / BOS M15).

Pembobotan baru (total 1.00):
  - WEIGHT_SR       0.35  Support & Resistance sebagai kompas utama
  - WEIGHT_SMC      0.20  Smart Money Concepts (OB & FVG)
  - WEIGHT_FIBO     0.15  Fibonacci Golden Zone
  - WEIGHT_EMA      0.15  EMA 20 & EMA 50 dynamic S/R
  - WEIGHT_TECHNICAL 0.08 MACD / RSI momentum
  - WEIGHT_ONCHAIN  0.05  Netflow / Whale Data
  - WEIGHT_SENTIMENT 0.02 Fear & Greed, Funding Rate

Aturan baku:
  - H4 bullish  -> HANYA izinkan sinyal BUY.
  - H4 bearish  -> HANYA izinkan sinyal SELL.
  - Sinyal tervalidasi bila M15 searah H4/D1 DAN harga menyentuh zona SMC/S&D H1.

Output: Signal (BUY/SELL/NEUTRAL) + confidence + Entry/SL/TP1/TP2 dari zona H1.
"""

from collections import Counter
from dataclasses import dataclass, field
from html import escape as _html_escape
from typing import Dict, List, Optional

from config import (
    BUY_THRESHOLD,
    CONFIDENCE_BASE,
    DISCLAIMER,
    RRR_MIN,
    RRR_TP2,
    SELL_THRESHOLD,
    SENTIMENT_MAX,
    SL_ATR_MULT,
    SL_BUFFER_PCT,
    SL_MIN_DIST_PCT,
    TOP_SIGNALS,
    WEIGHT_EMA,
    WEIGHT_FIBO,
    WEIGHT_ONCHAIN,
    WEIGHT_SENTIMENT,
    WEIGHT_SMC,
    WEIGHT_SR,
    WEIGHT_TECHNICAL,
)
from data.sentiment import score_fear_greed
from indicators.ema import analyze_ema
from indicators.fibonacci import analyze_fibonacci
from indicators.macd import macd_histogram_series
from indicators.rsi import rsi
from indicators.smc import (
    detect_equal_highs_lows,
    detect_fvg,
    detect_liquidity_sweep,
    detect_order_blocks,
    detect_structure,
    nearest_bearish_ob,
    nearest_bullish_ob,
)
from indicators.supply_demand import (
    detect_supply_demand,
    in_zone,
    nearest_demand,
    nearest_supply,
)
from indicators.support_resistance import find_swings, nearest_levels

ACTION_BUY = "BUY"
ACTION_SELL = "SELL"
ACTION_NEUTRAL = "NEUTRAL"

RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


@dataclass
class Signal:
    symbol: str
    base: str
    price: float
    pct_change_24h: float
    total_score: float
    action: str
    confidence: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- kompas H4/D1
def analyze_compass(h4_candles: List[Dict[str, float]], d1_candles: List[Dict[str, float]]) -> Dict[str, Optional[str]]:
    """[Kompas] Tren utama skala besar: H4 utama, D1 sebagai fallback bila H4 netral."""
    h4 = detect_structure(h4_candles)
    d1 = detect_structure(d1_candles)
    h4_trend = h4.get("trend")
    d1_trend = d1.get("trend")

    direction: Optional[str] = None
    if h4_trend == "bullish":
        direction = ACTION_BUY
    elif h4_trend == "bearish":
        direction = ACTION_SELL
    elif d1_trend == "bullish":
        direction = ACTION_BUY
    elif d1_trend == "bearish":
        direction = ACTION_SELL

    return {
        "direction": direction,
        "h4_trend": h4_trend,
        "d1_trend": d1_trend,
        "h4_bos": h4.get("bos"),
        "h4_choch": h4.get("choch"),
        "d1_bos": d1.get("bos"),
        "d1_choch": d1.get("choch"),
    }


# ---------------------------------------------------------------- pemetaan H1
def map_h1_zones(h1_candles: List[Dict[str, float]], price: float) -> Dict:
    """[Pemetaan] Area institusional H1: S&D, OB, FVG, EQH/EQL, Liquidity Sweep, S&R."""
    highs = [c["high"] for c in h1_candles]
    lows = [c["low"] for c in h1_candles]
    closes = [c["close"] for c in h1_candles]
    blocks = detect_order_blocks(h1_candles)
    zones = detect_supply_demand(h1_candles)
    return {
        "price": price,
        "closes": closes,
        "zones": zones,
        "demand_zones": [z for z in zones if z["type"] == "demand"],
        "supply_zones": [z for z in zones if z["type"] == "supply"],
        "order_blocks": blocks,
        "fvgs": detect_fvg(h1_candles),
        "equal_hl": detect_equal_highs_lows(h1_candles),
        "sweeps": detect_liquidity_sweep(h1_candles),
        "levels": nearest_levels(price, highs, lows),
        "bullish_ob": nearest_bullish_ob(price, blocks),
        "bearish_ob": nearest_bearish_ob(price, blocks),
    }


# ---------------------------------------------------------------- pelatuk M15
def analyze_trigger(m15_candles: List[Dict[str, float]]) -> Dict:
    """[Pelatuk] Konfirmasi M15: RSI, histogram/cross MACD, struktur BOS/CHoCH."""
    closes = [c["close"] for c in m15_candles]
    struct = detect_structure(m15_candles)
    rsi_val = rsi(closes, RSI_PERIOD)
    hist = macd_histogram_series(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    hist_now = hist[-1] if hist else None
    if hist_now is not None and hist_now != hist_now:
        hist_now = None
    cross: Optional[str] = None
    if len(hist) >= 2 and hist[-2] == hist[-2] and hist_now is not None:
        if hist[-2] <= 0 < hist_now:
            cross = "golden"
        elif hist[-2] >= 0 > hist_now:
            cross = "death"
    return {
        "closes": closes,
        "rsi": rsi_val,
        "histogram": hist_now,
        "cross": cross,
        "trend": struct.get("trend"),
        "bos": struct.get("bos"),
        "choch": struct.get("choch"),
    }


def _ob_near(price: float, ob: Optional[Dict], pct: float = 2.0) -> bool:
    """True bila harga berada DI DALAM Order Block atau berjarak <= pct% dari OB."""
    if not ob:
        return False
    lo, hi = ob.get("low"), ob.get("high")
    if lo is None or hi is None:
        return False
    if in_zone(price, ob):
        return True
    if not price:
        return False
    dist = min(abs(price - lo), abs(price - hi))
    return dist / price * 100.0 <= pct


def _setup_valid(compass_dir: Optional[str], h1_map: Dict, trigger: Dict) -> bool:
    """Validasi: harga menyentuh zona SMC/S&D H1 DAN M15 searah kompas."""
    price = h1_map["price"]
    if compass_dir == ACTION_BUY:
        zone_ok = bool(
            [z for z in h1_map["demand_zones"] if in_zone(price, z)]
            or _ob_near(price, h1_map.get("bullish_ob"))
            or [s for s in h1_map["sweeps"] if s["type"] == "sell_sweep"]
        )
        trig_ok = bool(
            (trigger["histogram"] is not None and trigger["histogram"] > 0)
            or trigger["cross"] == "golden"
            or trigger["bos"] == "bullish"
        )
        return zone_ok and trig_ok
    if compass_dir == ACTION_SELL:
        zone_ok = bool(
            [z for z in h1_map["supply_zones"] if in_zone(price, z)]
            or _ob_near(price, h1_map.get("bearish_ob"))
            or [s for s in h1_map["sweeps"] if s["type"] == "buy_sweep"]
        )
        trig_ok = bool(
            (trigger["histogram"] is not None and trigger["histogram"] < 0)
            or trigger["cross"] == "death"
            or trigger["choch"] == "bearish"
        )
        return zone_ok and trig_ok
    return False


# ---------------------------------------------------------------- skor pelatuk
def score_trigger(m15_candles: List[Dict[str, float]], pct_change_24h: float) -> tuple:
    """[Pelatuk] Skor konfirmasi M15 + momentum 24j -> -1.0..+1.0."""
    reasons: List[str] = []
    score = 0.0
    if not m15_candles:
        return 0.0, reasons
    closes = [c["close"] for c in m15_candles]

    rsi_val = rsi(closes, RSI_PERIOD)
    hist = macd_histogram_series(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    hist_now = hist[-1] if hist else None
    if hist_now is not None and hist_now != hist_now:
        hist_now = None
    cross: Optional[str] = None
    if len(hist) >= 2 and hist[-2] == hist[-2] and hist_now is not None:
        if hist[-2] <= 0 < hist_now:
            cross = "golden"
        elif hist[-2] >= 0 > hist_now:
            cross = "death"

    m15_parts: List[str] = []
    # Fix #1: cross MACD (konfirmasi lebih kuat) diberi bobot LEBIH BESAR dari
    # sekadar arah histogram. Sebelumnya terbalik (cross +-0.15, histogram +-0.25).
    if cross == "golden":
        m15_parts.append("MACD Golden Cross")
        score += 0.25
    elif cross == "death":
        m15_parts.append("MACD Death Cross")
        score -= 0.25
    elif hist_now is not None:
        if hist_now > 0:
            m15_parts.append("MACD Bullish")
            score += 0.15
        else:
            m15_parts.append("MACD Bearish")
            score -= 0.15

    if rsi_val is not None:
        if rsi_val < 30:
            m15_parts.append("RSI Rebound")
            score += 0.20
        elif rsi_val > 70:
            m15_parts.append("RSI Melemah")
            score -= 0.20

    struct = detect_structure(m15_candles)
    if struct.get("bos") == "bullish":
        m15_parts.append("BOS Bullish")
        score += 0.20
    elif struct.get("choch") == "bearish":
        m15_parts.append("CHoCH Bearish")
        score -= 0.20

    if m15_parts:
        reasons.append("[M15] " + " & ".join(m15_parts))

    # Fix #6: momentum 24j selaras konvensi kontrarian (sama seperti RSI/funding):
    # kenaikan ekstrem = overbought -> bias bearish; penurunan ekstrem = oversold
    # -> bias bullish. Sebelumnya momentum trend-following melawan RSI/SMC.
    if pct_change_24h is not None:
        if pct_change_24h <= -3:
            score += 0.20
        elif pct_change_24h >= 3:
            score -= 0.20
        elif pct_change_24h <= -0.5:
            score += 0.05
        elif pct_change_24h >= 0.5:
            score -= 0.05

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- skor S&R (kompas 0.35)
def _dist_pct(price: float, level: Optional[float]) -> Optional[float]:
    if level is None or not price:
        return None
    return abs((level - price) / price) * 100.0


def _atr(candles: List[Dict[str, float]], period: int = 14) -> Optional[float]:
    """Average True Range (H1) — ukuran volatilitas untuk SL dinamis.

    Defensif terhadap candle tanpa field close (cukup pakai high/low bila ada).
    """
    trs: List[float] = []
    prev_close: Optional[float] = None
    for candle in candles:
        high = candle.get("high")
        low = candle.get("low")
        if high is None or low is None:
            continue
        if prev_close is None:
            close = candle.get("close")
            prev_close = close if close is not None else None
            continue
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        close = candle.get("close")
        prev_close = close if close is not None else prev_close
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def score_sr(
    price: float,
    h1_map: Dict,
    h4_candles: List[Dict[str, float]],
    d1_candles: List[Dict[str, float]],
) -> tuple:
    """[S&R Kompas] Key levels skala besar (H4/D1) + zona/level S&R H1 -> -1.0..+1.0.

    S&R adalah kompas utama (bobot 0.35): tren struktur H4/D1, harga di/ dekat
    Demand/Supply key level, Support/Resistance terdekat, dan breakout level kunci.
    """
    reasons: List[str] = []
    score = 0.0
    compass = analyze_compass(h4_candles, d1_candles)
    h4_trend = compass["h4_trend"]
    d1_trend = compass["d1_trend"]

    if h4_trend == "bullish":
        label = "BOS skala besar" if compass["h4_bos"] == "bullish" else "higher high"
        reasons.append(f"[H4] S&R skala besar Bullish ({label})")
        score += 0.35
    elif h4_trend == "bearish":
        label = "CHoCH skala besar" if compass["h4_choch"] == "bearish" else "lower low"
        reasons.append(f"[H4] S&R skala besar Bearish ({label})")
        score -= 0.35
    elif d1_trend == "bullish":
        reasons.append("[D1] S&R skala besar Bullish (fallback)")
        score += 0.25
    elif d1_trend == "bearish":
        reasons.append("[D1] S&R skala besar Bearish (fallback)")
        score -= 0.25

    demand = h1_map.get("demand_zones", [])
    supply = h1_map.get("supply_zones", [])
    in_demand = [z for z in demand if in_zone(price, z)]
    in_supply = [z for z in supply if in_zone(price, z)]
    near_demand = nearest_demand(price, h1_map.get("zones", []))
    near_supply = nearest_supply(price, h1_map.get("zones", []))
    # Sisi zona yang berlawanan dengan bias kompas tidak dilaporkan: zona yang
    # berisi Entry sudah tidak memblokir (lihat _blocked_by_zone), sehingga
    # "masuk Demand Zone" + "masuk Supply Zone" sekaligus hanya noise.
    bias = compass["direction"]

    if bias != ACTION_SELL:
        if in_demand:
            reasons.append("[H1] Harga masuk Demand Zone")
            score += 0.25
        elif near_demand:
            dist = _dist_pct(near_demand["high"], price)
            if dist is not None and dist <= 2.0:
                reasons.append(f"[H1] Harga dekat Demand Zone ({dist:.1f}%)")
                score += 0.15
    if bias != ACTION_BUY:
        if in_supply:
            reasons.append("[H1] Harga masuk Supply Zone")
            score -= 0.25
        elif near_supply:
            dist = _dist_pct(price, near_supply["low"])
            if dist is not None and dist <= 2.0:
                reasons.append(f"[H1] Harga dekat Supply Zone ({dist:.1f}%)")
                score -= 0.15

    levels = h1_map.get("levels", {})
    if levels.get("support_dist_pct") is not None and levels["support_dist_pct"] <= 3:
        reasons.append(f"[H1] Support dekat ({levels['support_dist_pct']:.1f}%)")
        score += 0.20
    if levels.get("resistance_dist_pct") is not None and levels["resistance_dist_pct"] <= 3:
        reasons.append(f"[H1] Resistance dekat ({levels['resistance_dist_pct']:.1f}%)")
        score -= 0.20
    if levels.get("resistance") is not None and price > levels["resistance"]:
        reasons.append("[H1] Breakout Resistance kunci")
        score += 0.20
    if levels.get("support") is not None and price < levels["support"]:
        reasons.append("[H1] Breakout Support kunci")
        score -= 0.20

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- skor SMC (OB & FVG 0.20)
def score_smc(price: float, h1_map: Dict, compass_dir: Optional[str] = None) -> tuple:
    """[SMC] Order Block + FVG + Liquidity Sweep H1 -> -1.0..+1.0.

    SMC adalah lapisan konfluensi: saat arah kompas sudah jelas (BUY/SELL), hanya
    komponen yang searah yang dinilai (bullish OB/FVG untuk BUY, bearish OB/FVG
    untuk SELL) agar setup campuran tidak meniadakan skor kompas. Tanpa arah
    kompas (None), kedua arah dievaluasi independen (bukan if/elif).
    """
    reasons: List[str] = []
    score = 0.0
    allow_bull = compass_dir in (None, ACTION_BUY)
    allow_bear = compass_dir in (None, ACTION_SELL)

    if allow_bull and h1_map.get("bullish_ob"):
        reasons.append("[H1] Bullish OB di bawah harga")
        score += 0.35
    if allow_bear and h1_map.get("bearish_ob"):
        reasons.append("[H1] Bearish OB di atas harga")
        score -= 0.35

    # Fix: kehadiran per tipe, bukan per-gap. Sebelumnya tiap FVG/sweep
    # dijumlahkan (+0.25/+0.40 per gap) sehingga kategori SMC jenuh di ±1.00
    # untuk hampir semua koin (double-counting) dan tidak lagi membedakan
    # kekuatan setup; alasan mentah juga penuh baris duplikat.
    bull_fvg = any(
        gap["type"] == "bullish" and gap["bottom"] < price
        for gap in h1_map.get("fvgs", [])
    )
    bear_fvg = any(
        gap["type"] == "bearish" and gap["top"] > price
        for gap in h1_map.get("fvgs", [])
    )
    if allow_bull and bull_fvg:
        reasons.append("[H1] FVG bullish tervalidasi di bawah harga")
        score += 0.25
    if allow_bear and bear_fvg:
        reasons.append("[H1] FVG bearish tervalidasi di atas harga")
        score -= 0.25

    bull_sweep = any(
        sweep["type"] == "sell_sweep" for sweep in h1_map.get("sweeps", [])
    )
    bear_sweep = any(
        sweep["type"] == "buy_sweep" for sweep in h1_map.get("sweeps", [])
    )
    if allow_bull and bull_sweep:
        reasons.append("[H1] Liquidity Sweep tereksekusi (EQL tersapu)")
        score += 0.40
    if allow_bear and bear_sweep:
        reasons.append("[H1] Liquidity Sweep tereksekusi (EQH tersapu)")
        score -= 0.40

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- skor Fibonacci (0.15)
def _golden_zone_overlaps_sr(fibo: Dict, h1_map: Dict) -> bool:
    """Golden Zone beririsan dengan Support/Resistance key level H1."""
    levels = h1_map.get("levels") or {}
    gz_lo = fibo["golden_zone_low"]
    gz_hi = fibo["golden_zone_high"]
    for level in (levels.get("support"), levels.get("resistance")):
        if level is not None and gz_lo <= level <= gz_hi:
            return True
    return False


def _golden_zone_overlaps_ob(fibo: Dict, h1_map: Dict) -> bool:
    """Golden Zone beririsan dengan Order Block SMC (bullish/bearish)."""
    gz_lo = fibo["golden_zone_low"]
    gz_hi = fibo["golden_zone_high"]
    return any(
        block.get("low") <= gz_hi and block.get("high") >= gz_lo
        for block in h1_map.get("order_blocks", [])
    )


def score_fibo(fibo: Dict, price: float, h1_map: Dict, compass_dir: Optional[str]) -> tuple:
    """[Fibonacci] Golden Zone (0.5/0.618/0.786) -> -1.0..+1.0.

    - Harga di Golden Zone  -> skor dasar tinggi.
    - Harga dekat (<=1%)    -> skor dasar sedang.
    - Konfluensi: Golden Zone ∩ Key Level S&R (+0.25) / Order Block SMC (+0.20)
      -> skor konfirmasi tinggi.
    - Arah mengikuti kompas (BUY = +, SELL = -). Tanpa arah kompas -> netral.
    """
    reasons: List[str] = []
    if not fibo or not fibo.get("ok") or fibo.get("range", 0) <= 0:
        return 0.0, reasons

    magnitude = 0.0
    if fibo["in_golden_zone"]:
        magnitude = 0.45
        reasons.append("[H1] Harga di Fibonacci Golden Zone (0.500/0.618/0.786)")
    else:
        dist = fibo.get("golden_zone_dist_pct")
        if dist is not None and dist <= 1.0:
            magnitude = 0.20
            reasons.append(f"[H1] Harga dekat Fibonacci Golden Zone ({dist:.1f}%)")
    if magnitude <= 0:
        return 0.0, reasons

    if _golden_zone_overlaps_sr(fibo, h1_map):
        magnitude += 0.25
        reasons.append("[H1] Konfluensi Golden Zone ∩ Key Level S&R")
    if _golden_zone_overlaps_ob(fibo, h1_map):
        magnitude += 0.20
        reasons.append("[H1] Konfluensi Golden Zone ∩ Order Block (SMC)")

    if compass_dir == ACTION_BUY:
        score = magnitude
    elif compass_dir == ACTION_SELL:
        score = -magnitude
    else:
        score = 0.0
    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- skor EMA 20/50 (0.15)
def score_ema(
    ema_info: Dict,
    rsi_now: Optional[float],
    rsi_prev: Optional[float],
    compass_dir: Optional[str] = None,
) -> tuple:
    """[EMA] EMA 20 & EMA 50 dynamic S/R + pullback + RSI hook -> -1.0..+1.0.

    - Uptrend (BUY):  Harga > EMA 20 > EMA 50.
    - Pullback BUY:   Harga mendekati/menyentuh EMA 20 (<=0.5%) + RSI hook up 30-40.
    - Downtrend (SELL): Harga < EMA 20 < EMA 50.
    - Pullback SELL:  Harga mendekati/menyentuh EMA 20 (<=0.5%) + RSI hook down 60-70.

    Fix (alignment kompas): bila `compass_dir` (BUY/SELL) diberikan dan arah
    trend EMA berlawanan (mis. kompas H4 bearish tapi EMA H1 uptrend), skor EMA
    dinetralkan agar EMA tidak menggeser skor melawan arah kompas — konsisten
    dengan aturan "H4 bullish -> HANYA BUY / H4 bearish -> HANYA SELL".
    Tanpa kompas (None), perilaku lama dipertahankan (arah ikut EMA).
    """
    reasons: List[str] = []
    if not ema_info or ema_info.get("ema_fast") is None:
        return 0.0, reasons

    ema_bull = bool(ema_info.get("uptrend"))
    ema_bear = bool(ema_info.get("downtrend"))
    if compass_dir in (ACTION_BUY, ACTION_SELL):
        if (compass_dir == ACTION_BUY and ema_bear) or (
            compass_dir == ACTION_SELL and ema_bull
        ):
            reasons.append("[H1] EMA berlawanan arah kompas (dinetralkan)")
            return 0.0, reasons

    score = 0.0
    if ema_info["uptrend"]:
        reasons.append("[H1] EMA 20 > EMA 50 (dynamic support naik)")
        score += 0.30
        if ema_info["pullback_buy"]:
            reasons.append(f"[H1] Pullback ke EMA 20 ({ema_info['dist_fast_pct']:.1f}%)")
            score += 0.30
            if rsi_now is not None and rsi_prev is not None and 30 <= rsi_now <= 40 and rsi_now > rsi_prev:
                reasons.append(f"[H1] RSI Hook UP ({rsi_now:.0f}) di pullback EMA 20")
                score += 0.40
    elif ema_info["downtrend"]:
        reasons.append("[H1] EMA 20 < EMA 50 (dynamic resistance turun)")
        score -= 0.30
        if ema_info["pullback_sell"]:
            reasons.append(f"[H1] Pullback ke EMA 20 ({ema_info['dist_fast_pct']:.1f}%)")
            score -= 0.30
            if rsi_now is not None and rsi_prev is not None and 60 <= rsi_now <= 70 and rsi_now < rsi_prev:
                reasons.append(f"[H1] RSI Hook DOWN ({rsi_now:.0f}) di pullback EMA 20")
                score -= 0.40

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- sentiment / whale / onchain
def score_sentiment(fg_value: float, funding_rates: List[float], ls_ratio: Optional[float]) -> tuple:
    """Fear&Greed (contrarian) + funding rate + long/short ratio -> -1.0..+1.0.

    Hasil di-cap ke ±SENTIMENT_MAX: sentimen adalah bias regime, tidak boleh
    menenggelamkan arah setup SMC/teknikal (mis. di Fear semua koin diberi bias
    positif seragam sehingga setup bearish yang jelas pun gagal jadi SELL).
    """
    reasons: List[str] = []
    score = score_fear_greed(fg_value)
    reasons.append(f"Fear&Greed {fg_value:.0f}")

    if funding_rates:
        avg_funding = sum(funding_rates) / len(funding_rates)
        funding_pct = avg_funding * 100.0
        if funding_pct >= 0.03:
            reasons.append(f"Funding tinggi ({funding_pct:.3f}%) = crowd long")
            score -= 0.30
        elif funding_pct <= -0.03:
            reasons.append(f"Funding negatif ({funding_pct:.3f}%) = crowd short")
            score += 0.30

    if ls_ratio is not None:
        if ls_ratio >= 1.5:
            reasons.append(f"L/S ratio {ls_ratio:.2f} = dominan long")
            score -= 0.20
        elif ls_ratio <= 0.7:
            reasons.append(f"L/S ratio {ls_ratio:.2f} = dominan short")
            score += 0.20

    score = max(-SENTIMENT_MAX, min(SENTIMENT_MAX, score))
    return score, reasons


def score_whale(flow: Optional[Dict[str, float]]) -> tuple:
    """Netflow exchange ETH (proxy whale). Net positif (masuk exchange) = bearish.

    Terbatas ke ±0.5 (sama seperti on-chain): data proxy ini tidak boleh
    sendirian meloloskan ETH jadi BUY/SELL tanpa konfirmasi setup teknikal.
    """
    reasons: List[str] = []
    if not flow:
        return 0.0, reasons
    net = flow.get("net_usd", 0.0)
    if net > 0:
        reasons.append(f"Whale inflow exchange (net ${net/1e6:.1f}M)")
        return -0.5, reasons
    if net < 0:
        reasons.append(f"Whale outflow exchange (net ${-net/1e6:.1f}M)")
        return 0.5, reasons
    return 0.0, reasons


def score_onchain(btc_stats: Optional[Dict]) -> tuple:
    """Proxy aktivitas on-chain BTC (jumlah tx / volume jaringan)."""
    reasons: List[str] = []
    if not btc_stats:
        return 0.0, reasons
    n_tx = btc_stats.get("n_tx_24h")
    if n_tx:
        reasons.append(f"On-chain aktif ({n_tx:,} tx/hari)")
        return 0.5, reasons
    return 0.0, reasons


# ---------------------------------------------------------------- agregasi
def _build_reasons(
    action: str,
    h1_map: Dict,
    pct_change_24h: Optional[float],
    fg_value: float,
    sr_reasons: List[str],
    smc_reasons: List[str],
    fibo_reasons: List[str],
    ema_reasons: List[str],
    trigger_reasons: List[str],
) -> List[str]:
    price = h1_map["price"]
    if action == ACTION_BUY:
        parts = []
        if [z for z in h1_map["demand_zones"] if in_zone(price, z)]:
            parts.append("Demand Zone")
        if h1_map.get("bullish_ob"):
            parts.append("Bullish OB")
        headline = " & ".join(parts) + " H1 Tersentuh" if parts else "Setup BUY MTF H1"
    elif action == ACTION_SELL:
        parts = []
        if [z for z in h1_map["supply_zones"] if in_zone(price, z)]:
            parts.append("Supply Zone")
        if h1_map.get("bearish_ob"):
            parts.append("Bearish OB")
        headline = " & ".join(parts) + " H1 Tersentuh" if parts else "Setup SELL MTF H1"
    else:
        headline = "Analisa MTF — Setup Belum Tervalidasi"

    pct_str = f"{pct_change_24h:+.1f}%" if pct_change_24h is not None else "n/a"
    last_line = f"Momentum 24j {pct_str} | Fear&Greed {fg_value:.0f}"
    return [headline] + sr_reasons + smc_reasons + fibo_reasons + ema_reasons + trigger_reasons + [last_line]


def assemble_signal(
    symbol: str,
    base: str,
    price: float,
    pct_change_24h: float,
    h4_candles: List[Dict[str, float]],
    d1_candles: List[Dict[str, float]],
    h1_candles: List[Dict[str, float]],
    m15_candles: List[Dict[str, float]],
    fg_value: float,
    funding_rates: List[float],
    ls_ratio: Optional[float],
    whale_flow: Optional[Dict[str, float]],
    btc_stats: Optional[Dict],
) -> Signal:
    compass = analyze_compass(h4_candles, d1_candles)
    h1_map = map_h1_zones(h1_candles, price)
    trigger = analyze_trigger(m15_candles)
    compass_dir = compass["direction"]

    # EMA 20/50 dihitung pada H1 (fallback H4 bila H1 kosong); RSI hook memakai
    # seri yang sama agar pullback EMA dinilai dengan momentum konsisten.
    ema_source = h1_candles or h4_candles
    ema_info = analyze_ema(ema_source, price)
    ema_closes = [c["close"] for c in ema_source if c.get("close") is not None]
    rsi_now = rsi(ema_closes, RSI_PERIOD)
    rsi_prev = rsi(ema_closes[:-1], RSI_PERIOD) if len(ema_closes) > 1 else None

    fibo = analyze_fibonacci(ema_source, price)

    sr, sr_reasons = score_sr(price, h1_map, h4_candles, d1_candles)
    smc, smc_reasons = score_smc(price, h1_map, compass_dir)
    fibo_score, fibo_reasons = score_fibo(fibo, price, h1_map, compass_dir)
    ema_score, ema_reasons = score_ema(ema_info, rsi_now, rsi_prev, compass_dir)
    tech, tech_reasons = score_trigger(m15_candles, pct_change_24h)
    senti, senti_reasons = score_sentiment(fg_value, funding_rates, ls_ratio)

    # Fix #2: whale (netflow ETH) & on-chain (statistik BTC) bukan konstanta
    # global untuk SEMUA koin — hanya diterapkan ke koin sumbernya (ETH / BTC).
    # Sebelumnya netflow ETH ikut menggeser skor DOGE/SOL/dst, membuat setup
    # netral bisa jadi BUY. Data opsional yang tidak tersedia (whale_flow /
    # btc_stats = None) = kategori DILEWATI sepenuhnya, tidak ikut dihitung
    # bobotnya — sesuai prinsip graceful degradation (AGENTS.md).
    use_whale = base == "ETH" and whale_flow is not None
    use_onchain = base == "BTC" and btc_stats is not None
    whale, whale_reasons = score_whale(whale_flow) if use_whale else (0.0, [])
    onchain, onchain_reasons = score_onchain(btc_stats) if use_onchain else (0.0, [])
    # Netflow / Whale Data digabung ke kategori On-chain (WEIGHT_ONCHAIN = 0.05).
    if use_whale:
        onchain_score, onchain_reasons = whale, whale_reasons
    elif use_onchain:
        onchain_score, onchain_reasons = onchain, onchain_reasons
    else:
        onchain_score, onchain_reasons = 0.0, []

    total = (
        sr * WEIGHT_SR
        + smc * WEIGHT_SMC
        + fibo_score * WEIGHT_FIBO
        + ema_score * WEIGHT_EMA
        + tech * WEIGHT_TECHNICAL
        + senti * WEIGHT_SENTIMENT
        + onchain_score * WEIGHT_ONCHAIN
    )
    # Renormalisasi: total dibagi jumlah bobot yang BENAR-BENAR dipakai agar koin
    # tanpa kategori on-chain (atau datanya sedang tidak tersedia) tidak mendapat
    # skor lebih kecil sistematis (dan skor sebanding lintas koin, bukan rata-rata
    # parsial). Kategori S&R/SMC/Fibo/EMA/Teknikal/Sentimen selalu tersedia (derivasi
    # candle/price); hanya On-chain yang opsional.
    weight_sum = (
        WEIGHT_SR
        + WEIGHT_SMC
        + WEIGHT_FIBO
        + WEIGHT_EMA
        + WEIGHT_TECHNICAL
        + WEIGHT_SENTIMENT
        + (WEIGHT_ONCHAIN if (use_whale or use_onchain) else 0.0)
    )
    total = max(-1.0, min(1.0, total / weight_sum)) if weight_sum else 0.0

    valid = _setup_valid(compass_dir, h1_map, trigger)
    if valid and compass_dir == ACTION_BUY and total >= BUY_THRESHOLD:
        action = ACTION_BUY
    elif valid and compass_dir == ACTION_SELL and total <= SELL_THRESHOLD:
        action = ACTION_SELL
    else:
        action = ACTION_NEUTRAL

    confidence = max(25, min(95, CONFIDENCE_BASE + int(abs(total) * 40)))
    rr_rejected = False
    levels = _levels_mtf(price, h1_candles, h1_map, action)
    if levels is None:
        rr_rejected = True
        intended = action
        action = ACTION_NEUTRAL
        levels = _levels_mtf(price, h1_candles, h1_map, ACTION_NEUTRAL, intended=intended)
    entry, sl, tp1, tp2 = levels
    breakdown = {
        "sr": round(sr, 2),
        "smc": round(smc, 2),
        "fibo": round(fibo_score, 2),
        "ema": round(ema_score, 2),
        "teknikal": round(tech, 2),
        "onchain": round(onchain_score, 2),
        "sentimen": round(senti, 2),
    }
    reasons = _build_reasons(
        action, h1_map, pct_change_24h, fg_value,
        sr_reasons, smc_reasons, fibo_reasons, ema_reasons, tech_reasons,
    )
    if rr_rejected:
        reasons.append("[RR] Ditolak: Risk:Reward < 1:1.5 — target H1 terlalu dekat dengan Entry")

    return Signal(
        symbol=symbol,
        base=base,
        price=price,
        pct_change_24h=pct_change_24h,
        total_score=total,
        action=action,
        confidence=confidence,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        breakdown=breakdown,
        reasons=reasons,
    )


def _above_targets(price: float, h1_candles: List[Dict[str, float]], h1_map: Dict) -> List[float]:
    """Kandidat target resistance di atas harga (BUY), urut menaik (terdekat dulu)."""
    cands: List[float] = []
    if h1_candles:
        swings = find_swings([c["high"] for c in h1_candles], [c["low"] for c in h1_candles])
        cands += [s["value"] for s in swings["highs"] if s["value"] > price]
    cands += [z["low"] for z in h1_map.get("supply_zones", []) if z["low"] > price]
    return sorted({round(c, 8) for c in cands})


def _below_targets(price: float, h1_candles: List[Dict[str, float]], h1_map: Dict) -> List[float]:
    """Kandidat target support di bawah harga (SELL), urut menurun (terdekat dulu)."""
    cands: List[float] = []
    if h1_candles:
        swings = find_swings([c["high"] for c in h1_candles], [c["low"] for c in h1_candles])
        cands += [s["value"] for s in swings["lows"] if s["value"] < price]
    cands += [z["high"] for z in h1_map.get("demand_zones", []) if z["high"] < price]
    return sorted({round(c, 8) for c in cands}, reverse=True)


def _blocked_by_zone(lo: float, hi: float, zones: List[Dict], zone_type: str) -> bool:
    """True bila zona kuat (supply/demand) memotong jalur [lo, hi] menuju target.

    - BUY (zone_type="supply", jalur naik entry->tp): HANYA zona supply yang
      SELURUHNYA berada di atas Entry yang memblokir. Zona yang BERISI Entry
      tidak dianggap terblokir — saat BUY harga justru bergerak KELUAR dari
      zona itu (naik), bukan masuk ke dalamnya.
    - SELL (zone_type="demand", jalur turun entry->tp): HANYA zona demand yang
      SELURUHNYA berada di bawah Entry yang memblokir (simetris).

    (Fix: sebelumnya zona yang berisi Entry ikut memblokir, sehingga hampir
    semua BUY/SELL valid di-reject jadi NEUTRAL karena zona S&D yang lebar
    selalu "mengandung" harga saat ini.)
    """
    # BUY: Entry = ujung bawah (lo); SELL: Entry = ujung atas (hi).
    if zone_type == "supply":
        entry, tp = lo, hi
    else:
        entry, tp = hi, lo
    for z in zones:
        if z.get("type") != zone_type:
            continue
        z_lo = z.get("low")
        z_hi = z.get("high")
        if z_lo is None or z_hi is None:
            continue
        if zone_type == "supply":
            if z_lo > entry and z_lo < tp:
                return True
        else:
            if z_hi < entry and z_hi > tp:
                return True
    return False


def _rr_targets(price: float, sl: float, targets: List[float], zones: List[Dict], action: str):
    """TP1/TP2 dengan RRR wajib: TP1 >= RRR_MIN x SL, TP2 = RRR_TP2 x SL.

    - TP1 di target struktur H1 terdekat bila jarak TP1 (%) >= RRR_MIN x jarak SL.
    - Bila target terdekat terlalu dekat (< RRR_MIN x SL): paksa TP1 =
      Entry +/- (jarak SL x RRR_MIN) HANYA bila tidak terhalang zona kuat.
      Jika tetap tidak valid -> return None (Bad RR, sinyal dibatalkan NEUTRAL).
    - TP2 selalu proyeksi Entry +/- (jarak SL x RRR_TP2).
    - Urutan dijamin TP1 lebih dekat dari TP2: BUY -> entry < TP1 < TP2,
      SELL -> entry > TP1 > TP2 (bila target struktur melewati proyeksi TP2,
      posisi TP1/TP2 ditukar agar target terdekat selalu jadi TP1).
    """
    sl_dist = abs(price - sl)
    if sl_dist <= 0:
        sl_dist = price * 0.001
    rr1_dist = RRR_MIN * sl_dist
    rr2_dist = RRR_TP2 * sl_dist

    if action == ACTION_SELL:
        tp2 = price - rr2_dist
        if not targets:
            tp1 = price - rr1_dist
        else:
            nearest = targets[0]
            if price - nearest >= rr1_dist:
                tp1 = nearest
            else:
                tp1 = price - rr1_dist
                if _blocked_by_zone(tp1, price, zones, "demand"):
                    return None
        if tp1 < tp2:
            # Target melewati proyeksi TP2 -> TP1 jadi proyeksi (price - rr2_dist);
            # pastikan jalur ke proyeksi itu tidak terhalang zona Demand.
            if _blocked_by_zone(tp2, price, zones, "demand"):
                return None
            tp1, tp2 = tp2, tp1
        return tp1, tp2

    tp2 = price + rr2_dist
    if not targets:
        tp1 = price + rr1_dist
    else:
        nearest = targets[0]
        if nearest - price >= rr1_dist:
            tp1 = nearest
        else:
            tp1 = price + rr1_dist
            if _blocked_by_zone(price, tp1, zones, "supply"):
                return None
    if tp1 > tp2:
        # Target melewati proyeksi TP2 -> TP1 jadi proyeksi (price + rr2_dist);
        # pastikan jalur ke proyeksi itu tidak terhalang zona Supply.
        if _blocked_by_zone(price, tp2, zones, "supply"):
            return None
        tp1, tp2 = tp2, tp1
    return tp1, tp2


def _levels_mtf(
    price: float,
    h1_candles: List[Dict[str, float]],
    h1_map: Dict,
    action: str,
    intended: Optional[str] = None,
):
    """Entry/SL/TP1/TP2 dengan RRR wajib minimal 1:1.5 (TP1) & 1:3 (TP2).

    - SL: di bawah Demand/Support H1 terdekat (BUY) / di atas Supply/Resistance
      H1 terdekat (SELL), buffer SL_BUFFER_PCT (0.3%) di luar zona. Jarak SL
      dipaksa minimal max(SL_MIN_DIST_PCT, SL_ATR_MULT * ATR/price) agar SL
      yang terlalu dekat (<1%) tidak tersapu noise pasar.
    - TP1: target struktur H1 terdekat bila jarak TP1 >= RRR_MIN x jarak SL;
      else paksa proyeksi Entry +/- (jarak SL x RRR_MIN) bila tidak terhalang
      zona Supply/Demand kuat. Bila terhalang -> return None (sinyal dibatalkan).
    - TP2: proyeksi Entry +/- (jarak SL x RRR_TP2).
    - NEUTRAL: placeholder kosmetik; arah SL/TP mengikuti `intended` (BUY/SELL
      yang ditolak RR) agar tampilan pesan konsisten, default orientasi BUY.
    """
    highs = [c["high"] for c in h1_candles] if h1_candles else []
    lows = [c["low"] for c in h1_candles] if h1_candles else []
    levels = h1_map.get("levels") or nearest_levels(price, highs or [price], lows or [price])
    demand = h1_map.get("demand_zones", []) or []
    supply = h1_map.get("supply_zones", []) or []
    ob_bull = [b for b in h1_map.get("order_blocks", []) if b["type"] == "bullish"]
    ob_bear = [b for b in h1_map.get("order_blocks", []) if b["type"] == "bearish"]

    atr = _atr(h1_candles) if h1_candles else None
    min_sl_dist = SL_MIN_DIST_PCT * price
    if atr:
        min_sl_dist = max(min_sl_dist, SL_ATR_MULT * atr)

    if action == ACTION_BUY:
        sl_cands = [z["low"] for z in demand if z["low"] < price]
        sl_cands += [b["low"] for b in ob_bull if b["low"] < price]
        if levels and levels.get("support") is not None and levels["support"] < price:
            sl_cands.append(levels["support"])
        sl = max(sl_cands) * (1 - SL_BUFFER_PCT) if sl_cands else price * 0.96
        if price - sl < min_sl_dist:
            sl = price - min_sl_dist
        rr = _rr_targets(price, sl, _above_targets(price, h1_candles, h1_map), supply, ACTION_BUY)
        if rr is None:
            return None
        tp1, tp2 = rr
    elif action == ACTION_SELL:
        sl_cands = [z["high"] for z in supply if z["high"] > price]
        sl_cands += [b["high"] for b in ob_bear if b["high"] > price]
        if levels and levels.get("resistance") is not None and levels["resistance"] > price:
            sl_cands.append(levels["resistance"])
        sl = min(sl_cands) * (1 + SL_BUFFER_PCT) if sl_cands else price * 1.04
        if sl - price < min_sl_dist:
            sl = price + min_sl_dist
        rr = _rr_targets(price, sl, _below_targets(price, h1_candles, h1_map), demand, ACTION_SELL)
        if rr is None:
            return None
        tp1, tp2 = rr
    else:
        if intended == ACTION_SELL:
            sl = price * 1.03
            tp1 = price * (1 - RRR_MIN * 0.03)
            tp2 = price * (1 - RRR_TP2 * 0.03)
        else:
            sl = price * 0.97
            tp1 = price * (1 + RRR_MIN * 0.03)
            tp2 = price * (1 + RRR_TP2 * 0.03)
    return price, sl, tp1, tp2


# ---------------------------------------------------------------- pesan
def rank_signals(signals: List[Signal]) -> List[Signal]:
    signals.sort(key=lambda s: abs(s.total_score), reverse=True)
    return signals[:TOP_SIGNALS]


def _fmt_price(value: float) -> str:
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.6f}"


def _esc(value) -> str:
    """Escape karakter HTML (< > & ") agar aman untuk parse_mode=HTML Telegram."""
    return _html_escape(str(value), quote=True)


def _level_pct(entry: float, level: float, action: str) -> float:
    """Jarak % dari Entry ke level (SL/TP), positif = menguntungkan aksi."""
    if not entry:
        return 0.0
    if action == ACTION_SELL:
        return (entry - level) / entry * 100.0
    return (level - entry) / entry * 100.0


def _group_reason_lines(reasons: List[str]) -> List[str]:
    """Kelompokkan alasan per timeframe untuk format pesan baru.

    Tag `[TF]` ditulis 1x sebagai header grup; sub-alasan diindentasi `- `.
    Baris tanpa tag (mis. momentum) dicetak sebagai baris terpisah ber-prefix 💸.
    Item yang sama terulang (mis. FVG / Liquidity Sweep per-gap) dideduplikasi
    menjadi 1 baris dengan jumlah, contoh: "FVG bullish tervalidasi di bawah
    harga (x8)" — menghindari kalimat berulang-ulang dalam baris terpisah.
    """
    out: List[str] = []
    groups: List[tuple] = []  # [(tf, [item, ...])]
    standalone: List[str] = []
    for reason in reasons:
        if reason.startswith("["):
            end = reason.find("]")
            tf = reason[1:end] if end > 0 else ""
            item = reason[end + 1:].strip() if end > 0 else reason
            if groups and groups[-1][0] == tf:
                groups[-1][1].append(item)
            else:
                groups.append((tf, [item]))
        else:
            standalone.append(reason)
    for tf, items in groups:
        dedup: List[str] = []
        for item, count in Counter(items).items():
            dedup.append(f"{item} (x{count})" if count > 1 else item)
        if len(dedup) == 1:
            out.append(f"    + [{_esc(tf)}] {_esc(dedup[0])}")
        else:
            out.append(f"    + [{_esc(tf)}] ")
            for item in dedup:
                out.append(f"       - {_esc(item)}")
    for line in standalone:
        out.append(f"💸 {_esc(line)}")
    return out


def _signal_lines(sig: Signal) -> List[str]:
    b = sig.breakdown
    reason_lines = []
    if sig.reasons:
        reason_lines.append("📝 " + _esc(sig.reasons[0]))
        reason_lines.extend(_group_reason_lines(sig.reasons[1:]))
    else:
        reason_lines.append("📝 —")
    lines = [
        f"<b>#{_esc(sig.base)} ({_esc(sig.symbol)})</b> — {sig.action} · Confidence {sig.confidence}%",
        f"🔑 Entry: <b>{_esc(_fmt_price(sig.entry))}</b>",
        f"🛡️ SL: <b>{_esc(_fmt_price(sig.sl))}</b> ({_level_pct(sig.entry, sig.sl, sig.action):+.2f}%)",
        f"🎯 TP1: <b>{_esc(_fmt_price(sig.tp1))}</b> ({_level_pct(sig.entry, sig.tp1, sig.action):+.2f}%)",
        f"🎯 TP2: <b>{_esc(_fmt_price(sig.tp2))}</b> ({_level_pct(sig.entry, sig.tp2, sig.action):+.2f}%)",
        f"💹 24j: {sig.pct_change_24h:+.2f}%",
        *reason_lines,
        f"📊 Skor: <b>{sig.total_score:+.2f}</b>  (SR {b['sr']:+.2f} · SMC {b['smc']:+.2f} · Fibo {b['fibo']:+.2f} · EMA {b['ema']:+.2f} · Tek {b['teknikal']:+.2f} · Onch {b['onchain']:+.2f} · Sent {b['sentimen']:+.2f})",
        "",
        "━━━━━━━━━━━━",
        "",
    ]
    return lines


def format_message(signals: List[Signal], timestamp: str, market_note: str = "") -> str:
    buys = sorted((s for s in signals if s.action == ACTION_BUY), key=lambda s: abs(s.total_score), reverse=True)
    sells = sorted((s for s in signals if s.action == ACTION_SELL), key=lambda s: abs(s.total_score), reverse=True)
    neutrals = [s for s in signals if s.action == ACTION_NEUTRAL]

    lines = [
        "<b>🚨 NEW SIGNAL ALERTS 🚨</b>",
        "<b>📊 DAY TRADING BRIEFING — MTF S&amp;R + SMC + FIBO + EMA</b>",
        f"🕐 {_esc(timestamp)}",
        "⚙️ Analisa: Kompas H4/D1 → Zona H1 → Konfirmasi M15",
    ]
    if market_note:
        lines.append(f"🌐 {_esc(market_note)}")
    lines.append("")

    for label, group in (
        ("<b>📈 SINYAL LONG (BUY)</b>", buys),
        ("<b>📉 SINYAL SHORT (SELL)</b>", sells),
        ("<b>⚪ WATCHLIST (NEUTRAL)</b>", neutrals),
    ):
        if not group:
            continue
        lines.append(label)
        lines.append("")
        for sig in group:
            lines.extend(_signal_lines(sig))

    lines.append(_esc(DISCLAIMER))
    return "\n".join(lines)
