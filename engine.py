"""Mesin skoring sinyal v2 — skor per-kategori berbobot.

Skor tiap kategori dinormalisasi -1.0..+1.0, lalu digabung berbobot:
  Teknikal 40% · SMC/S&R 20% · Sentiment 15% · Whale 15% · On-chain 10%

Output: Signal (BUY/SELL/NEUTRAL) + confidence + SL/TP berbasis level S&R/OB.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import (
    BUY_THRESHOLD,
    CONFIDENCE_BASE,
    DISCLAIMER,
    SELL_THRESHOLD,
    TOP_SIGNALS,
    WEIGHT_ONCHAIN,
    WEIGHT_SENTIMENT,
    WEIGHT_SMC,
    WEIGHT_TECHNICAL,
    WEIGHT_WHALE,
)
from data.sentiment import score_fear_greed
from indicators.macd import macd
from indicators.rsi import rsi
from indicators.smc import (
    detect_fvg,
    detect_order_blocks,
    detect_structure,
    nearest_order_block,
)
from indicators.support_resistance import nearest_levels

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


# ---------------------------------------------------------------- teknikal
def score_technical(closes: List[float], pct_change_24h: float) -> tuple:
    """RSI + MACD + momentum 24h → skor -1.0..+1.0."""
    reasons: List[str] = []
    score = 0.0

    rsi_val = rsi(closes, RSI_PERIOD)
    if rsi_val is not None:
        if rsi_val < 30:
            reasons.append(f"RSI {rsi_val:.0f} oversold")
            score += 0.30
        elif rsi_val < 40:
            reasons.append(f"RSI {rsi_val:.0f} mendekati oversold")
            score += 0.15
        elif rsi_val > 70:
            reasons.append(f"RSI {rsi_val:.0f} overbought")
            score -= 0.30
        elif rsi_val > 60:
            reasons.append(f"RSI {rsi_val:.0f} mendekati overbought")
            score -= 0.15

    macd_val = macd(closes, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    if macd_val is not None:
        if macd_val["histogram"] > 0:
            reasons.append("MACD bullish (histogram +)")
            score += 0.35
        else:
            reasons.append("MACD bearish (histogram -)")
            score -= 0.35

    if pct_change_24h is not None:
        if pct_change_24h >= 3:
            reasons.append(f"Momentum 24j +{pct_change_24h:.1f}%")
            score += 0.20
        elif pct_change_24h <= -3:
            reasons.append(f"Momentum 24j {pct_change_24h:.1f}%")
            score -= 0.20
        elif pct_change_24h >= 0.5:
            score += 0.05
        elif pct_change_24h <= -0.5:
            score -= 0.05

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- SMC & S&R
def score_smc(candles: List[Dict[str, float]], price: float) -> tuple:
    """OB, FVG, struktur (BOS/CHoCH), level S&R → skor -1.0..+1.0."""
    reasons: List[str] = []
    score = 0.0
    if len(candles) < 15 or price <= 0:
        return 0.0, reasons

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    structure = detect_structure(candles)
    trend = structure.get("trend")
    if trend == "bullish":
        reasons.append("Struktur bullish (BOS/higher high)")
        score += 0.25
    elif trend == "bearish":
        reasons.append("Struktur bearish (CHoCH/lower low)")
        score -= 0.25

    blocks = detect_order_blocks(candles)
    if blocks:
        nearest = nearest_order_block(price, blocks)
        if nearest:
            reasons.append(f"OB support {nearest['low']:.2f}")
            score += 0.25

    fvgs = detect_fvg(candles)
    if fvgs:
        recent = [g for g in fvgs if g["type"] == "bullish" and g["bottom"] < price]
        recent_sell = [g for g in fvgs if g["type"] == "bearish" and g["top"] > price]
        if recent:
            reasons.append("FVG bullish di bawah harga")
            score += 0.15
        elif recent_sell:
            reasons.append("FVG bearish di atas harga")
            score -= 0.15

    levels = nearest_levels(price, highs, lows)
    if levels["support"] is not None and levels["support_dist_pct"] is not None:
        if levels["support_dist_pct"] <= 3:
            reasons.append(f"Support dekat ({levels['support_dist_pct']:.1f}%)")
            score += 0.20
    if levels["resistance"] is not None and levels["resistance_dist_pct"] is not None:
        if levels["resistance_dist_pct"] <= 3:
            reasons.append(f"Resistance dekat ({levels['resistance_dist_pct']:.1f}%)")
            score -= 0.20

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- sentiment
def score_sentiment(fg_value: float, funding_rates: List[float], ls_ratio: Optional[float]) -> tuple:
    """Fear&Greed (contrarian) + funding rate + long/short ratio → -1.0..+1.0."""
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

    return max(-1.0, min(1.0, score)), reasons


# ---------------------------------------------------------------- whale
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


# ---------------------------------------------------------------- onchain
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
def assemble_signal(
    symbol: str,
    base: str,
    price: float,
    pct_change_24h: float,
    closes: List[float],
    candles: List[Dict[str, float]],
    fg_value: float,
    funding_rates: List[float],
    ls_ratio: Optional[float],
    whale_flow: Optional[Dict[str, float]],
    btc_stats: Optional[Dict],
) -> Signal:
    tech, tech_reasons = score_technical(closes, pct_change_24h)
    smc, smc_reasons = score_smc(candles, price)
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

    if total >= BUY_THRESHOLD:
        action = ACTION_BUY
    elif total <= SELL_THRESHOLD:
        action = ACTION_SELL
    else:
        action = ACTION_NEUTRAL

    confidence = max(25, min(95, CONFIDENCE_BASE + int(abs(total) * 40)))
    entry, sl, tp1, tp2 = _levels(price, closes, candles, action)
    breakdown = {
        "teknikal": round(tech, 2),
        "smc": round(smc, 2),
        "sentimen": round(senti, 2),
        "whale": round(whale, 2),
        "onchain": round(onchain, 2),
    }
    reasons = tech_reasons + smc_reasons + senti_reasons + whale_reasons + onchain_reasons

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


def _levels(price: float, closes: List[float], candles: List[Dict[str, float]], action: str) -> tuple:
    """SL/TP dari level S&R terdekat, fallback ke persentase (SL 4%, TP 8%/15%)."""
    if not closes or price <= 0:
        return price, price * 0.96, price * 1.08, price * 1.15
    highs = [c["high"] for c in candles] if candles else closes
    lows = [c["low"] for c in candles] if candles else closes
    levels = nearest_levels(price, highs, lows)
    support, resistance = levels["support"], levels["resistance"]

    if action == ACTION_BUY:
        sl = support if support and support < price else price * 0.96
        tp1 = resistance if resistance and resistance > price else price * 1.08
        tp2 = price + 2 * (tp1 - price)
    elif action == ACTION_SELL:
        sl = resistance if resistance and resistance > price else price * 1.04
        tp1 = support if support and support < price else price * 0.92
        tp2 = price - 2 * (price - tp1)
    else:
        sl = price * 0.97
        tp1 = price * 1.03
        tp2 = price * 1.06
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


def _signal_lines(sig: Signal, number: int) -> List[str]:
    arrow = "🟢" if sig.action == ACTION_BUY else ("🔴" if sig.action == ACTION_SELL else "⚪")
    b = sig.breakdown
    lines = [
        f"{number}. <b>{arrow} {sig.base} ({sig.symbol})</b> — {sig.action} · Confidence {sig.confidence}%",
        f"📊 Skor: <b>{sig.total_score:+.2f}</b>  (Tek {b['teknikal']:+.2f} · SMC {b['smc']:+.2f} · Sent {b['sentimen']:+.2f} · Whale {b['whale']:+.2f} · Onch {b['onchain']:+.2f})",
        f"💹 24j: {sig.pct_change_24h:+.2f}%",
        f"🔑 Entry: <b>{_fmt_price(sig.entry)}</b>",
        f"🛡️ SL: <b>{_fmt_price(sig.sl)}</b>",
        f"🎯 TP1: <b>{_fmt_price(sig.tp1)}</b>  ·  TP2: <b>{_fmt_price(sig.tp2)}</b>",
        "📝 " + (" · ".join(sig.reasons) if sig.reasons else "—"),
        "───",
    ]
    return lines


def format_message(signals: List[Signal], timestamp: str, market_note: str = "") -> str:
    buys = sorted((s for s in signals if s.action == ACTION_BUY), key=lambda s: abs(s.total_score), reverse=True)
    sells = sorted((s for s in signals if s.action == ACTION_SELL), key=lambda s: abs(s.total_score), reverse=True)
    neutrals = [s for s in signals if s.action == ACTION_NEUTRAL]

    lines = [
        "<b>📊 DAILY BRIEFING — SINYAL TRADING v2</b>",
        f"🕐 {timestamp}",
    ]
    if market_note:
        lines.append(f"🌐 {market_note}")
    lines.append("")

    number = 1
    for label, group in (
        ("<b>🟢 SINYAL LONG (BUY)</b>", buys),
        ("<b>🔴 SINYAL SHORT (SELL)</b>", sells),
        ("<b>⚪ WATCHLIST (NEUTRAL)</b>", neutrals),
    ):
        if not group:
            continue
        lines.append(label)
        lines.append("")
        for sig in group:
            lines.extend(_signal_lines(sig, number))
            number += 1

    lines.append(DISCLAIMER)
    return "\n".join(lines)
