import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

import backtest as bt
import engine
from engine import ACTION_BUY, ACTION_SELL
from indicators.ema import ema_latest

WIB = timezone(timedelta(hours=7))

_DAYS = 7
_PAIRS = 20
_STEP_MIN = 15 * 60 * 1000
_OFFSET_DAYS = 0  # 0 = 7 hari terakhir; 7 = jendela 7-14 hari lalu (validasi)

bt._EVAL_NOW_MS = int(datetime.now(WIB).timestamp() * 1000)
bt._EVAL_START_MS = int((datetime.now(WIB) - timedelta(days=_DAYS + _OFFSET_DAYS)).timestamp() * 1000)


def memoize_fetch():
    cache = {}
    orig = bt.fetch_pair

    def fetch(symbol, start_ms, days):
        if symbol not in cache:
            cache[symbol] = orig(symbol, start_ms, days)
        return cache[symbol]

    bt.fetch_pair = fetch


def ema20_filter(pair, base, sig, m15_now, h1_now):
    """Searah tren H1: BUY wajib harga > EMA20(H1), SELL wajib harga < EMA20(H1)."""
    closes = [c["close"] for c in h1_now if c.get("close") is not None]
    ema = ema_latest(closes, 20)
    if ema is None:
        return True
    if sig.action == ACTION_BUY:
        return sig.price > ema
    if sig.action == ACTION_SELL:
        return sig.price < ema
    return True


def _with_conf60(pair, base, sig, m15_now, h1_now):
    return sig.confidence >= 60 and ema20_filter(pair, base, sig, m15_now, h1_now)


def score_cutoff(threshold):
    def _f(pair, base, sig, m15_now, h1_now):
        return sig.total_score >= threshold

    return _f


def buy_only(pair, base, sig, m15_now, h1_now):
    return sig.action == ACTION_BUY


def mixed_rsi_filter(pair, base, sig, m15_now, h1_now):
    """BUY: harga > EMA20(H1). SELL: harga < EMA20(H1) DAN RSI(14,H1) overbought > 60."""
    closes = [c["close"] for c in h1_now if c.get("close") is not None]
    ema = ema_latest(closes, 20)
    if sig.action == ACTION_BUY:
        return ema is None or sig.price > ema
    if sig.action == ACTION_SELL:
        from indicators.rsi import rsi

        r = rsi(closes, 14)
        return ema is not None and sig.price < ema and r is not None and r > 60
    return True


def run(rrr_min, rrr_tp2, eval_hours, tag, filter_fn=None, rrr_only_tag=True):
    engine.RRR_MIN = rrr_min
    engine.RRR_TP2 = rrr_tp2
    bt.EVAL_MAX_HOURS = eval_hours
    t0 = time.time()
    s = bt.run_backtest(bt._pick_pairs(_PAIRS), _DAYS, _STEP_MIN, verbose=False, filter_fn=filter_fn)
    d = s["wins"] + s["losses"]
    wr = s["wins"] / d * 100.0 if d else 0.0
    ev = (
        (s["tp1"] * rrr_min + s["tp2"] * rrr_tp2 - s["sl"] * 1.0) / d
        if d
        else 0.0
    )
    print(
        f"{tag:<28} WR {wr:5.1f}%  ({s['wins']}W/{s['losses']}L/{d})  "
        f"TP1 {s['tp1']} TP2 {s['tp2']} SL {s['sl']}  EV {ev:+.3f}R  "
        f"FLOAT {s['floating']}  eval {s['evaluated']} (filter {s['filtered']})  {time.time() - t0:.0f}s"
    )
    return s


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--pairs", type=int, default=20)
    ap.add_argument("--configs", default=None, help="nama varian yang dijalankan (dipisah koma)")
    args = ap.parse_args()
    _OFFSET_DAYS = args.offset
    _DAYS = args.days
    _PAIRS = args.pairs
    bt._EVAL_NOW_MS = int(datetime.now(WIB).timestamp() * 1000)
    bt._EVAL_START_MS = int((datetime.now(WIB) - timedelta(days=_DAYS + _OFFSET_DAYS)).timestamp() * 1000)

    memoize_fetch()
    pairs = bt._pick_pairs(_PAIRS)
    print(f"Pairs: {len(pairs)} | window {_DAYS} hari offset {_OFFSET_DAYS}d | scan 15m\n")

    defs = [
        ("A0 RRR0.8/1.6+EMA20", lambda: run(0.8, 1.6, 24, "A0 RRR0.8/1.6+EMA20", ema20_filter)),
        ("A1 RRR0.7/1.4+EMA20", lambda: run(0.7, 1.4, 24, "A1 RRR0.7/1.4+EMA20", ema20_filter)),
        ("F RRR0.7/1.4 plain", lambda: run(0.7, 1.4, 24, "F RRR0.7/1.4 plain")),
        ("J F+score>=0.05", lambda: run(0.7, 1.4, 24, "J F+score>=0.05", score_cutoff(0.05))),
        ("K F+score>=0.10", lambda: run(0.7, 1.4, 24, "K F+score>=0.10", score_cutoff(0.10))),
        ("L F+score>=0.15", lambda: run(0.7, 1.4, 24, "L F+score>=0.15", score_cutoff(0.15))),
        ("G RRR0.7/2.0 plain", lambda: run(0.7, 2.0, 24, "G RRR0.7/2.0 plain")),
        ("H RRR0.7/2.5 plain", lambda: run(0.7, 2.5, 24, "H RRR0.7/2.5 plain")),
        ("I RRR0.8/2.0 plain", lambda: run(0.8, 2.0, 24, "I RRR0.8/2.0 plain")),
        ("A2 RRR0.9/1.8+EMA20", lambda: run(0.9, 1.8, 24, "A2 RRR0.9/1.8+EMA20", ema20_filter)),
        ("A3 RRR0.8/1.6+EMA20+conf60", lambda: run(0.8, 1.6, 24, "A3 RRR0.8/1.6+EMA20+conf60", _with_conf60)),
    ]
    if args.configs:
        wanted = [d for d in defs if d[0].split(" ")[0] in args.configs.split(",") or d[0] in args.configs.split(",")]
    else:
        wanted = defs
    print(f"{'varian':<32} {'WR':>8}  detail")
    print("-" * 95)
    for name, fn in wanted:
        fn()
