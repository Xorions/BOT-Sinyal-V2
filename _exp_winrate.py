"""Validasi 3 filter win-rate (CONFIDENCE_MIN / MAX_ATR_REL / threshold) multi-jendela.

Pemakaian (tiap kombinasi dijalankan di proses terpisah agar config termuat ulang):
    python _exp_winrate.py --conf-min 0 --atr-max 0 --buy-thr 0.10 --offset 0
    python _exp_winrate.py --conf-min 70 --atr-max 0.03 --buy-thr 0.15 --offset 7
    ...
Jendela: --offset 0 = 7 hari terakhir, 7 = jendela 7-14 hari lalu, 14 = 14-21 hari lalu.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

WIB = timezone(timedelta(hours=7))
MIN15_MS = 900_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf-min", type=int, default=0, help="CONFIDENCE_MIN (0 = mati)")
    ap.add_argument("--atr-max", type=float, default=0.0, help="MAX_ATR_REL (0.0 = mati)")
    ap.add_argument("--buy-thr", type=float, default=0.10, help="BUY_THRESHOLD (SELL = negatifnya)")
    ap.add_argument("--offset", type=int, default=0, help="geser jendela mundur N hari")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--pairs", type=int, default=20)
    args = ap.parse_args()

    # Load dotenv TIDAK menimpa env yang sudah ada (override=False), jadi nilai
    # eksperimen di sini menang atas .env selama di-set SEBELUM import config.
    os.environ["CONFIDENCE_MIN"] = str(args.conf_min)
    os.environ["MAX_ATR_REL"] = str(args.atr_max)
    os.environ["BUY_THRESHOLD"] = str(args.buy_thr)
    os.environ["SELL_THRESHOLD"] = str(-args.buy_thr)

    import backtest as bt

    bt._EVAL_NOW_MS = int((datetime.now(WIB) - timedelta(days=args.offset)).timestamp() * 1000)
    bt._EVAL_START_MS = int((datetime.now(WIB) - timedelta(days=args.offset + args.days)).timestamp() * 1000)

    pairs = bt._pick_pairs(args.pairs)
    stats = bt.run_backtest(pairs, args.days, MIN15_MS, verbose=False)

    d = stats["wins"] + stats["losses"]
    wr = stats["wins"] / d * 100.0 if d else 0.0
    ev = (
        (stats["tp1"] * 0.7 + stats["tp2"] * 1.4 - stats["sl"] * 1.0) / d
        if d
        else 0.0
    )
    print(
        f"conf={args.conf_min:<3} atr={args.atr_max:<5} thr={args.buy_thr:<5} off={args.offset:>2}d "
        f"| WR {wr:5.1f}% ({stats['wins']}W/{stats['losses']}L/{d}) "
        f"TP1 {stats['tp1']} TP2 {stats['tp2']} SL {stats['sl']} FLOAT {stats['floating']} "
        f"eval {stats['evaluated']} (filter {stats['filtered']}) EV {ev:+.3f}R"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())