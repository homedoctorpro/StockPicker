"""
Winner analysis for the bounce-back backtest.

Answers: were there stocks that actually rebounded, and what — using only
information knowable AT ENTRY — separated them from the falling knives?

Faithful-replay caveat: the live scorer/disqualifier read yfinance .info and
financial statements, which are only served as TODAY's snapshot (no history,
and no statements older than ~4 years). Scoring a 2019 cohort with 2026
fundamentals is lookahead bias, so we do NOT replay the fundamental scorer.
Instead we characterise winners with point-in-time PRICE/VOLUME features that
were genuinely observable on the entry date. Reads only cached data — no network.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = Path(__file__).parent / "cache" / "backtest"
WIN_DOUBLE = 1.0   # +100%
WIN_UP50 = 0.5     # +50%


def _bucket_stats(df: pd.DataFrame, col: str, order: list[str]) -> list[dict]:
    out = []
    for label in order:
        g = df[df[col] == label]
        if len(g) == 0:
            continue
        out.append({
            "label": label,
            "n": int(len(g)),
            "median": float(g["ret_12m"].median()),
            "spy_median": float(g["spy_12m"].median()),
            "pct_beat_spy": float((g["ret_12m"] > g["spy_12m"]).mean()),
            "pct_doubled": float((g["ret_12m"] >= WIN_DOUBLE).mean()),
            "pct_up50": float((g["ret_12m"] >= WIN_UP50).mean()),
        })
    return out


def main():
    closes, dollar_vol = pd.read_pickle(CACHE / "prices_combined.pkl")
    detail = pd.read_csv(CACHE / "backtest_detail.csv", parse_dates=["date"])
    caps = json.loads((CACHE / "caps.json").read_text())

    detail = detail[detail["ret_12m"].notna()].copy()
    print(f"[winners] {len(detail)} positions with a 12-month outcome")

    # Rolling feature matrices on just the cohort tickers (keeps memory sane).
    tickers = sorted(set(detail["ticker"]) & set(closes.columns))
    px = closes[tickers]
    dv = dollar_vol[tickers]
    ret = px.pct_change()

    feat = {
        "ma50": px.rolling(50, min_periods=30).mean(),
        "ma200": px.rolling(200, min_periods=120).mean(),
        "low252": px.rolling(252, min_periods=120).min(),
        "ret21": px / px.shift(21) - 1,
        "ret63": px / px.shift(63) - 1,
        "vol60": ret.rolling(60, min_periods=30).std() * np.sqrt(252),
        "dvol60": dv.rolling(60, min_periods=30).median(),
    }

    # Look up each position's entry-date features.
    def col(name):
        m = feat[name]
        vals = []
        for d, t in zip(detail["date"], detail["ticker"]):
            try:
                vals.append(m.at[d, t])
            except Exception:
                vals.append(np.nan)
        return pd.Series(vals, index=detail.index)

    price_at = col_price = pd.Series(
        [px.at[d, t] if (d in px.index and t in px.columns) else np.nan
         for d, t in zip(detail["date"], detail["ticker"])], index=detail.index)

    ma50 = col("ma50"); ma200 = col("ma200"); low252 = col("low252")
    detail["mom_1m"] = col("ret21")
    detail["mom_3m"] = col("ret63")
    detail["vol60"] = col("vol60")
    detail["dvol60"] = col("dvol60")
    detail["above_50dma"] = np.where(price_at > ma50, "Above 50-day MA", "Below 50-day MA")
    detail["above_200dma"] = np.where(price_at > ma200, "Above 200-day MA", "Below 200-day MA")
    detail["pct_above_low"] = (price_at - low252) / low252
    detail["est_cap"] = detail["ticker"].map(lambda t: caps.get(t, 0.0)) * \
        (price_at / detail["ticker"].map(lambda t: px[t].ffill().iloc[-1] if t in px else np.nan))

    # ---- categorical buckets ----
    detail["decline_bucket"] = pd.cut(
        detail["perf52w"], [-1.0001, -0.90, -0.80, -0.75],
        labels=["Down 90%+", "Down 80-90%", "Down 75-80%"])
    detail["mom3_bucket"] = pd.cut(
        detail["mom_3m"], [-1.01, -0.25, -0.10, 0.0, 0.15, 100],
        labels=["3m ≤ -25%", "3m -25 to -10%", "3m -10 to 0%", "3m 0 to +15%", "3m > +15%"])
    detail["low_bucket"] = pd.cut(
        detail["pct_above_low"], [-0.01, 0.05, 0.20, 0.50, 100],
        labels=["≤5% off 52w low", "5-20% off low", "20-50% off low", ">50% off low"])
    detail["vol_bucket"] = pd.qcut(detail["vol60"], [0, .33, .66, 1.0],
                                   labels=["Low vol", "Mid vol", "High vol"], duplicates="drop")
    detail["dvol_bucket"] = pd.cut(
        detail["dvol60"], [-1, 1e6, 1e7, 1e8, 1e15],
        labels=["< $1M/day", "$1-10M/day", "$10-100M/day", "> $100M/day"])
    detail["cap_bucket"] = pd.cut(
        detail["est_cap"], [0, 1e9, 5e9, 1e15],
        labels=["$300M-1B", "$1-5B", "> $5B"])

    n = len(detail)
    winners = {
        "n": n,
        "n_doubled": int((detail["ret_12m"] >= WIN_DOUBLE).sum()),
        "pct_doubled": float((detail["ret_12m"] >= WIN_DOUBLE).mean()),
        "n_up50": int((detail["ret_12m"] >= WIN_UP50).sum()),
        "pct_up50": float((detail["ret_12m"] >= WIN_UP50).mean()),
        "n_beat_spy": int((detail["ret_12m"] > detail["spy_12m"]).sum()),
        "pct_beat_spy": float((detail["ret_12m"] > detail["spy_12m"]).mean()),
        "best_ret": float(detail["ret_12m"].max()),
    }

    # top rebounders
    top = detail.nlargest(15, "ret_12m")
    winners["top_rebounders"] = [
        {"ticker": r.ticker, "month": r.date.strftime("%Y-%m"),
         "perf52w": float(r.perf52w), "ret_12m": float(r.ret_12m)}
        for r in top.itertuples()
    ]

    winners["features"] = [
        {"name": "Depth of decline at entry",
         "buckets": _bucket_stats(detail, "decline_bucket", ["Down 75-80%", "Down 80-90%", "Down 90%+"])},
        {"name": "Position vs 200-day moving average",
         "buckets": _bucket_stats(detail, "above_200dma", ["Below 200-day MA", "Above 200-day MA"])},
        {"name": "Position vs 50-day moving average",
         "buckets": _bucket_stats(detail, "above_50dma", ["Below 50-day MA", "Above 50-day MA"])},
        {"name": "3-month momentum into entry",
         "buckets": _bucket_stats(detail, "mom3_bucket",
                    ["3m ≤ -25%", "3m -25 to -10%", "3m -10 to 0%", "3m 0 to +15%", "3m > +15%"])},
        {"name": "Distance above the 52-week low",
         "buckets": _bucket_stats(detail, "low_bucket",
                    ["≤5% off 52w low", "5-20% off low", "20-50% off low", ">50% off low"])},
        {"name": "60-day realized volatility",
         "buckets": _bucket_stats(detail, "vol_bucket", ["Low vol", "Mid vol", "High vol"])},
        {"name": "Liquidity (60-day median $ volume)",
         "buckets": _bucket_stats(detail, "dvol_bucket",
                    ["< $1M/day", "$1-10M/day", "$10-100M/day", "> $100M/day"])},
        {"name": "Estimated market cap at entry",
         "buckets": _bucket_stats(detail, "cap_bucket", ["$300M-1B", "$1-5B", "> $5B"])},
    ]

    # ---- the punchline: catch-the-knife vs wait-for-stabilization ----
    falling = detail[(detail["above_200dma"] == "Below 200-day MA") & (detail["mom_3m"] < 0)]
    stabilizing = detail[(detail["above_200dma"] == "Above 200-day MA") & (detail["mom_3m"] > 0)]

    def grp(g, label):
        return {"label": label, "n": int(len(g)),
                "median": float(g["ret_12m"].median()),
                "pct_beat_spy": float((g["ret_12m"] > g["spy_12m"]).mean()),
                "pct_doubled": float((g["ret_12m"] >= WIN_DOUBLE).mean())}

    winners["stabilization"] = {
        "falling": grp(falling, "Still falling (below 200-day MA & negative 3-month momentum)"),
        "stabilizing": grp(stabilizing, "Stabilizing (above 200-day MA & positive 3-month momentum)"),
        "spy_median": float(detail["spy_12m"].median()),
    }

    (CACHE / "winners_summary.json").write_text(json.dumps(winners, indent=2))
    print(f"[winners] doubled: {winners['n_doubled']} ({winners['pct_doubled']:.1%})  "
          f"beat SPY: {winners['pct_beat_spy']:.1%}  best: {winners['best_ret']:+.0%}")
    print(f"[winners] falling n={winners['stabilization']['falling']['n']} "
          f"median={winners['stabilization']['falling']['median']:+.1%} "
          f"beatSPY={winners['stabilization']['falling']['pct_beat_spy']:.0%}")
    print(f"[winners] stabilizing n={winners['stabilization']['stabilizing']['n']} "
          f"median={winners['stabilization']['stabilizing']['median']:+.1%} "
          f"beatSPY={winners['stabilization']['stabilizing']['pct_beat_spy']:.0%}")

    # merge into backtest summary and re-render the report
    summ = json.loads((CACHE / "backtest_summary.json").read_text())
    summ["winners"] = winners
    (CACHE / "backtest_summary.json").write_text(json.dumps(summ, indent=2))
    from src.backtest_report import render
    print(f"[winners] Report → {render(summ)}")


if __name__ == "__main__":
    main()
