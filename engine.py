"""Mesin skoring sinyal v2.3 — Day Trading Multi-Timeframe (MTF SMC + Supply & Demand).

Alur analisa 3 lapis:
  [Kompas H4/D1]   -> arah utama (BUY bila bullish, SELL bila bearish; BOS/CHoCH skala besar).
  [Pemetaan H1]    -> area institusional: Supply & Demand, OB, FVG, EQH/EQL, Liquidity
                      Sweep, pivot & swing Support/Resistance. Entry/SL/TP dari zona H1.
  [Pelatuk M15]    -> konfirmasi eksekusi akhir (RSI / MACD cross / momentum / BOS M15).

Aturan baku:
  - H4 bullish  -> HANYA izinkan sinyal BUY.
  - H4 bearish  -> HANYA izinkan sinyal SELL.
  - Sinyal tervalidasi bila M15 searah H4/D1 DAN harga menyentuh zona SMC/S&D H1.

Output: Signal (BUY/SELL/NEUTRAL) + confidence + Entry/SL/TP1/TP2 dari zona H1.
"""

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
    WEIGHT_ONCHAIN,
    WEIGHT_SENTIMENT,
    WEIGHT_SMC,
    WEIGHT_TECHNICAL,
    WEIGHT_WHALE,
)
from data.sentiment import score_fear_greed
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
    if cross == "golden":
        m15_parts.append("MACD Golden Cross")
        score += 0.15
    elif cross == "death":
        m15_parts.append("MACD Death Cross")
        score -= 0.15
    elif hist_now is not None:
        if hist_now > 0:
            m15_parts.append("MACD Bullish")
            score += 0.25
        else:
            m15_parts.append("MACD Bearish")
            score -= 0.25

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

    if pct_change_24h is not None:
        if pct_change_24h >= 3:
            score += 0.20
        elif pct_change_24h <= -3:
            score -= 0.20
        elif pct_change_24h >= 0.5:
            score += 0.05
        elif pct_change_24h <= -0.5:
            score -= 0.05

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- skor SMC MTF
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


def score_smc_mtf(
    h4_candles: List[Dict[str, float]],
    d1_candles: List[Dict[str, float]],
    h1_map: Dict,
    price: float,
) -> tuple:
    """[Kompas + Pemetaan] Skor struktur H4/D1 + zona H1 -> -1.0..+1.0."""
    reasons: List[str] = []
    score = 0.0
    compass = analyze_compass(h4_candles, d1_candles)
    h4_trend = compass["h4_trend"]
    d1_trend = compass["d1_trend"]

    if h4_trend == "bullish":
        label = "BOS skala besar" if compass["h4_bos"] == "bullish" else "higher high"
        reasons.append(f"[H4] Tren utama Bullish ({label})")
        score += 0.45
    elif h4_trend == "bearish":
        label = "CHoCH skala besar" if compass["h4_choch"] == "bearish" else "lower low"
        reasons.append(f"[H4] Tren utama Bearish ({label})")
        score -= 0.45
    elif d1_trend == "bullish":
        reasons.append("[D1] Tren Bullish (fallback)")
        score += 0.30
    elif d1_trend == "bearish":
        reasons.append("[D1] Tren Bearish (fallback)")
        score -= 0.30

    demand = h1_map.get("demand_zones", [])
    supply = h1_map.get("supply_zones", [])
    sweeps = h1_map.get("sweeps", [])

    in_demand = [z for z in demand if in_zone(price, z)]
    in_supply = [z for z in supply if in_zone(price, z)]
    near_demand = nearest_demand(price, h1_map.get("zones", []))
    near_supply = nearest_supply(price, h1_map.get("zones", []))

    if in_demand:
        reasons.append("[H1] Harga masuk Demand Zone")
        score += 0.30
    elif near_demand:
        dist = _dist_pct(near_demand["high"], price)
        if dist is not None and dist <= 2.0:
            reasons.append(f"[H1] Harga dekat Demand Zone ({dist:.1f}%)")
            score += 0.15
    if in_supply:
        reasons.append("[H1] Harga masuk Supply Zone")
        score -= 0.30
    elif near_supply:
        dist = _dist_pct(price, near_supply["low"])
        if dist is not None and dist <= 2.0:
            reasons.append(f"[H1] Harga dekat Supply Zone ({dist:.1f}%)")
            score -= 0.15

    if h1_map.get("bullish_ob"):
        reasons.append("[H1] Bullish OB di bawah harga")
        score += 0.20
    if h1_map.get("bearish_ob"):
        reasons.append("[H1] Bearish OB di atas harga")
        score -= 0.20

    fvgs = h1_map.get("fvgs", [])
    if [g for g in fvgs if g["type"] == "bullish" and g["bottom"] < price]:
        reasons.append("[H1] FVG bullish tervalidasi di bawah harga")
        score += 0.15
    if [g for g in fvgs if g["type"] == "bearish" and g["top"] > price]:
        reasons.append("[H1] FVG bearish tervalidasi di atas harga")
        score -= 0.15

    if [s for s in sweeps if s["type"] == "sell_sweep"]:
        reasons.append("[H1] Liquidity Sweep tereksekusi (EQL tersapu)")
        score += 0.25
    elif [s for s in sweeps if s["type"] == "buy_sweep"]:
        reasons.append("[H1] Liquidity Sweep tereksekusi (EQH tersapu)")
        score -= 0.25

    levels = h1_map.get("levels", {})
    if levels.get("support_dist_pct") is not None and levels["support_dist_pct"] <= 3:
        reasons.append(f"[H1] Support dekat ({levels['support_dist_pct']:.1f}%)")
        score += 0.15
    if levels.get("resistance_dist_pct") is not None and levels["resistance_dist_pct"] <= 3:
        reasons.append(f"[H1] Resistance dekat ({levels['resistance_dist_pct']:.1f}%)")
        score -= 0.15

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
    """Netflow exchange ETH (proxy whale). Net positif (masuk exchange) = bearish."""
    reasons: List[str] = []
    if not flow:
        return 0.0, reasons
    net = flow.get("net_usd", 0.0)
    if net > 0:
        reasons.append(f"Whale inflow exchange (net ${net/1e6:.1f}M)")
        return -1.0, reasons
    if net < 0:
        reasons.append(f"Whale outflow exchange (net ${-net/1e6:.1f}M)")
        return 1.0, reasons
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
    smc_reasons: List[str],
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
    return [headline] + smc_reasons + trigger_reasons + [last_line]


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

    tech, tech_reasons = score_trigger(m15_candles, pct_change_24h)
    smc, smc_reasons = score_smc_mtf(h4_candles, d1_candles, h1_map, price)
    senti, senti_reasons = score_sentiment(fg_value, funding_rates, ls_ratio)
    whale, whale_reasons = score_whale(whale_flow)
    onchain, onchain_reasons = score_onchain(btc_stats)

    total = (
        tech * WEIGHT_TECHNICAL
        + smc * WEIGHT_SMC
        + senti * WEIGHT_SENTIMENT
        + whale * WEIGHT_WHALE
        + onchain * WEIGHT_ONCHAIN
    )
    total = max(-1.0, min(1.0, total))

    compass_dir = compass["direction"]
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
        "teknikal": round(tech, 2),
        "smc": round(smc, 2),
        "sentimen": round(senti, 2),
        "whale": round(whale, 2),
        "onchain": round(onchain, 2),
    }
    reasons = _build_reasons(action, h1_map, pct_change_24h, fg_value, smc_reasons, tech_reasons)
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
    """True bila zona kuat (supply/demand) memotong ATAU mencakup jalur [lo, hi].

    - supply: zona di antara Entry dan proyeksi TP1, ATAU Entry sudah berada di
      dalam zona Supply -> harga tertahan di zona.
    - demand: zona di antara proyeksi TP1 dan Entry (arah SELL), ATAU Entry
      sudah berada di dalam zona Demand.
    """
    for z in zones:
        if z.get("type") != zone_type:
            continue
        z_lo = z.get("low")
        z_hi = z.get("high")
        if z_lo is None or z_hi is None:
            continue
        if z_lo < hi and z_hi > lo:
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
        if len(items) == 1:
            out.append(f"    + [{_esc(tf)}] {_esc(items[0])}")
        else:
            out.append(f"    + [{_esc(tf)}] ")
            for item in items:
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
        f"📊 Skor: <b>{sig.total_score:+.2f}</b>  (Tek {b['teknikal']:+.2f} · SMC {b['smc']:+.2f} · Sent {b['sentimen']:+.2f} · Whale {b['whale']:+.2f} · Onch {b['onchain']:+.2f})",
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
        "<b>📊 DAY TRADING BRIEFING — MTF SMC + S&amp;D</b>",
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
