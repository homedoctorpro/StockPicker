"""
Backtest the bounce-back screen: would buying US stocks down 75%+ (52-week)
with >$300M market cap have worked historically?

For each month-end since --start, form a cohort of stocks meeting the screen
at that date, then measure 3/6/12-month forward returns vs SPY.

Usage:
  python backtest.py                    # full run (downloads ~12y of prices, cached)
  python backtest.py --start 2016-01
  python backtest.py --skip-download    # reuse cached prices only
  python backtest.py --universe-limit 500   # quick test run

Honesty notes (also stated in the report):
- Survivorship bias: the universe is TODAY's listed stocks. Companies that
  fell 75% and were later delisted (bankruptcy, buyout) are mostly absent,
  which flatters the results. Delistings we CAN observe (price series that
  end mid-window) are reported both at last traded price and at -100%.
- Market cap at cohort date is estimated as current_cap * price_then/price_now
  (assumes constant share count). Tickers with no live quote use a dollar
  -volume proxy (median 60-day dollar volume >= $2M) instead.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# Windows consoles default to cp1252 and choke on the arrows/symbols we log.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = Path(__file__).parent / "cache" / "backtest"
DOCS_DIR = Path(__file__).parent / "docs"

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Security names that are not common stock
NON_COMMON_RE = re.compile(
    r"warrant|right(s)?\b|\bunit(s)?\b|preferred|preference|depositary|"
    r"\bnote(s)?\b|\bbond\b|%|due \d{4}",
    re.IGNORECASE,
)

FWD_WINDOWS = {"3m": 63, "6m": 126, "12m": 252}
LOOKBACK = 252  # trading days ~ 52 weeks
CHUNK = 200


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def get_universe() -> list[str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / "universe.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 30 * 86400:
        return json.loads(cache_file.read_text())

    tickers: set[str] = set()

    nasdaq = pd.read_csv(NASDAQ_LISTED, sep="|")
    nasdaq = nasdaq[nasdaq["Test Issue"] == "N"]
    nasdaq = nasdaq[nasdaq["ETF"] != "Y"]
    for _, row in nasdaq.iterrows():
        name = str(row["Security Name"])
        sym = str(row["Symbol"]).strip()
        if NON_COMMON_RE.search(name) or not sym or sym == "nan":
            continue
        tickers.add(sym)

    other = pd.read_csv(OTHER_LISTED, sep="|")
    other = other[other["Test Issue"] == "N"]
    other = other[other["ETF"] != "Y"]
    for _, row in other.iterrows():
        name = str(row["Security Name"])
        sym = str(row["ACT Symbol"]).strip()
        if NON_COMMON_RE.search(name) or not sym or sym == "nan":
            continue
        if "$" in sym:  # preferred/when-issued suffixes
            continue
        tickers.add(sym.replace(".", "-"))  # Yahoo class-share format

    result = sorted(t for t in tickers if re.fullmatch(r"[A-Z]+(-[A-Z])?", t))
    cache_file.write_text(json.dumps(result))
    print(f"[universe] {len(result)} common-stock tickers")
    return result


# ---------------------------------------------------------------------------
# Price download (chunked, resumable)
# ---------------------------------------------------------------------------

def download_prices(tickers: list[str], skip_download: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (closes, dollar_volume) DataFrames: index=dates, columns=tickers."""
    combined = CACHE / "prices_combined.pkl"
    if combined.exists():
        closes, dollar_vol = pd.read_pickle(combined)
        print(f"[prices] Loaded combined cache: {closes.shape[1]} tickers, "
              f"{closes.index[0].date()} → {closes.index[-1].date()}")
        return closes, dollar_vol

    if skip_download:
        print("[prices] --skip-download set but no combined cache exists")
        sys.exit(1)

    tickers = list(dict.fromkeys(tickers + ["SPY"]))
    close_parts, dvol_parts = [], []
    n_chunks = (len(tickers) + CHUNK - 1) // CHUNK
    for i in range(0, len(tickers), CHUNK):
        chunk_no = i // CHUNK
        chunk_file = CACHE / f"chunk_{chunk_no:03d}.pkl"
        batch = tickers[i:i + CHUNK]
        if chunk_file.exists():
            c, v = pd.read_pickle(chunk_file)
        else:
            print(f"[prices] Downloading chunk {chunk_no + 1}/{n_chunks} ({len(batch)} tickers)...")
            for attempt in range(3):
                try:
                    df = yf.download(batch, period="12y", interval="1d",
                                     auto_adjust=True, progress=False, threads=True,
                                     group_by="column")
                    break
                except Exception as e:
                    print(f"[prices] chunk {chunk_no} attempt {attempt + 1} failed: {e}")
                    time.sleep(30)
            else:
                print(f"[prices] chunk {chunk_no} failed 3x — skipping {len(batch)} tickers")
                continue
            if df is None or df.empty:
                pd.to_pickle((pd.DataFrame(), pd.DataFrame()), chunk_file)
                continue
            c = df["Close"] if "Close" in df.columns else pd.DataFrame()
            vol = df["Volume"] if "Volume" in df.columns else pd.DataFrame()
            if isinstance(c, pd.Series):  # single-ticker chunk
                c = c.to_frame(batch[0])
                vol = vol.to_frame(batch[0])
            c = c.dropna(axis=1, how="all")
            vol = vol.reindex(columns=c.columns)
            v = (c * vol)  # dollar volume
            pd.to_pickle((c, v), chunk_file)
            time.sleep(1.0)
        if not c.empty:
            close_parts.append(c)
            dvol_parts.append(v)

    closes = pd.concat(close_parts, axis=1)
    dollar_vol = pd.concat(dvol_parts, axis=1)
    closes = closes.loc[:, ~closes.columns.duplicated()].sort_index()
    dollar_vol = dollar_vol.loc[:, ~dollar_vol.columns.duplicated()].sort_index()
    pd.to_pickle((closes, dollar_vol), combined)
    print(f"[prices] {closes.shape[1]} tickers with data, "
          f"{closes.index[0].date()} → {closes.index[-1].date()}")
    return closes, dollar_vol


# ---------------------------------------------------------------------------
# Current market caps (for cap-at-date estimation)
# ---------------------------------------------------------------------------

def get_current_caps(tickers: list[str]) -> dict[str, float]:
    cache_file = CACHE / "caps.json"
    caps: dict[str, float] = {}
    if cache_file.exists():
        caps = json.loads(cache_file.read_text())
    missing = [t for t in tickers if t not in caps]
    if missing:
        print(f"[caps] Fetching current market cap for {len(missing)} tickers...")
        for n, t in enumerate(missing):
            try:
                cap = getattr(yf.Ticker(t).fast_info, "market_cap", None)
                caps[t] = float(cap) if cap else 0.0
            except Exception:
                caps[t] = 0.0  # no live quote (delisted) → dollar-volume proxy applies
            time.sleep(0.1)
            if (n + 1) % 50 == 0:
                print(f"[caps] {n + 1}/{len(missing)}")
                cache_file.write_text(json.dumps(caps))
        cache_file.write_text(json.dumps(caps))
    return caps


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------

def run_backtest(closes: pd.DataFrame, dollar_vol: pd.DataFrame,
                 start: str, min_cap_mm: int, drop_pct: float) -> dict:
    idx = closes.index
    month_ends = closes.groupby(idx.to_period("M")).tail(1).index
    month_ends = [d for d in month_ends
                  if d >= pd.Timestamp(start + "-01") + pd.Timedelta(days=380)
                  and idx.get_loc(d) >= LOOKBACK
                  and d <= idx[-1] - pd.Timedelta(days=5)]

    spy = closes["SPY"]
    universe_cols = [c for c in closes.columns if c != "SPY"]
    px = closes[universe_cols]
    last_valid = px.apply(lambda s: s.last_valid_index())

    # Pass 1: find every ticker that ever meets the price-drop test (for cap fetch)
    print(f"[backtest] {len(month_ends)} monthly cohorts, "
          f"{month_ends[0].date()} → {month_ends[-1].date()}")
    droppers: set[str] = set()
    perf_by_date = {}
    for d in month_ends:
        pos = idx.get_loc(d)
        now = px.iloc[pos]
        then = px.iloc[pos - LOOKBACK]
        perf = now / then - 1
        perf_by_date[d] = perf
        droppers.update(perf[(perf <= drop_pct / 100) & then.notna() & now.notna()].index)
    print(f"[backtest] {len(droppers)} tickers met the price-drop test at least once")

    caps = get_current_caps(sorted(droppers))
    latest_px = px.ffill().iloc[-1]

    cohorts = []
    rows = []
    for d in month_ends:
        pos = idx.get_loc(d)
        perf = perf_by_date[d]
        candidates = perf[perf <= drop_pct / 100].dropna().index

        members = []
        for t in candidates:
            p_t = px[t].iloc[pos]
            if not np.isfinite(p_t) or p_t <= 0:
                continue
            cap_now = caps.get(t, 0.0)
            if cap_now > 0 and np.isfinite(latest_px[t]) and latest_px[t] > 0:
                est_cap = cap_now * p_t / latest_px[t]
                if est_cap < min_cap_mm * 1e6:
                    continue
            else:
                # No live quote (likely delisted since) → liquidity proxy
                dv = dollar_vol[t].iloc[max(0, pos - 60):pos].median()
                if not np.isfinite(dv) or dv < 2e6:
                    continue
            members.append(t)

        for t in members:
            p_t = px[t].iloc[pos]
            row = {"date": d, "ticker": t, "perf52w": perf[t],
                   "delisted_by": last_valid[t] if last_valid[t] is not None and last_valid[t] < idx[-5] else None}
            for label, w in FWD_WINDOWS.items():
                end_pos = pos + w
                if end_pos >= len(idx):
                    row[f"ret_{label}"] = np.nan
                    row[f"spy_{label}"] = np.nan
                    row[f"dead_{label}"] = False
                    continue
                fwd = px[t].iloc[pos:end_pos + 1].dropna()
                spy_ret = spy.iloc[end_pos] / spy.iloc[pos] - 1
                if len(fwd) < 2:
                    row[f"ret_{label}"] = np.nan
                    row[f"dead_{label}"] = False
                elif fwd.index[-1] < idx[end_pos - 5]:
                    # stopped trading during the window → delisted
                    row[f"ret_{label}"] = fwd.iloc[-1] / p_t - 1
                    row[f"dead_{label}"] = True
                else:
                    row[f"ret_{label}"] = fwd.iloc[-1] / p_t - 1
                    row[f"dead_{label}"] = False
                row[f"spy_{label}"] = spy_ret
            rows.append(row)
        cohorts.append({"date": d, "n": len(members)})

    df = pd.DataFrame(rows)
    print(f"[backtest] {len(df)} cohort memberships total")
    return {"df": df, "cohorts": pd.DataFrame(cohorts)}


def summarize(res: dict, min_cap_mm: int, drop_pct: float) -> dict:
    df, cohorts = res["df"], res["cohorts"]
    out = {"windows": {}, "min_cap_mm": min_cap_mm, "drop_pct": drop_pct,
           "n_memberships": len(df), "n_unique": df["ticker"].nunique() if len(df) else 0,
           "n_cohorts": len(cohorts), "avg_cohort": float(cohorts["n"].mean()) if len(cohorts) else 0}
    for label in FWD_WINDOWS:
        r = df[f"ret_{label}"].dropna()
        if r.empty:
            continue
        sub = df.loc[r.index]
        spy_r = sub[f"spy_{label}"]
        dead = sub[f"dead_{label}"]
        pess = r.where(~dead, -1.0)  # delisted → -100% variant
        out["windows"][label] = {
            "n": int(len(r)),
            "mean": float(r.mean()), "median": float(r.median()),
            "pct_pos": float((r > 0).mean()),
            "pct_beat_spy": float((r > spy_r).mean()),
            "pct_lost_half": float((r <= -0.5).mean()),
            "pct_doubled": float((r >= 1.0).mean()),
            "spy_mean": float(spy_r.mean()), "spy_median": float(spy_r.median()),
            "n_delisted": int(dead.sum()),
            "mean_pessimistic": float(pess.mean()), "median_pessimistic": float(pess.median()),
            "p10": float(r.quantile(0.10)), "p25": float(r.quantile(0.25)),
            "p75": float(r.quantile(0.75)), "p90": float(r.quantile(0.90)),
        }
    # per-year medians (12m window)
    if len(df):
        df["year"] = df["date"].dt.year
        by_year = []
        for y, g in df.groupby("year"):
            r = g["ret_12m"].dropna()
            if len(r) == 0:
                continue
            by_year.append({"year": int(y), "n": int(len(r)),
                            "median": float(r.median()),
                            "spy_median": float(g.loc[r.index, "spy_12m"].median()),
                            "pct_beat_spy": float((r > g.loc[r.index, "spy_12m"]).mean())})
        out["by_year"] = by_year
        out["cohort_sizes"] = [{"date": str(c["date"].date()), "n": int(c["n"])}
                               for _, c in res["cohorts"].iterrows()]
        # distribution buckets for the 12m histogram
        r12 = df["ret_12m"].dropna()
        buckets = [(-1.01, -0.75), (-0.75, -0.5), (-0.5, -0.25), (-0.25, 0),
                   (0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 1e9)]
        out["dist_12m"] = [{"label": lab, "n": int(((r12 > lo) & (r12 <= hi)).sum())}
                           for (lo, hi), lab in zip(buckets,
                           ["≤-75%", "-75 to -50%", "-50 to -25%", "-25 to 0%",
                            "0 to +25%", "+25 to +50%", "+50 to +100%", "+100 to +200%", ">+200%"])]
    return out


def main():
    ap = argparse.ArgumentParser(description="Backtest the bounce-back screen")
    ap.add_argument("--start", default="2016-01", help="First cohort month (YYYY-MM)")
    ap.add_argument("--min-cap", type=int, default=300, help="Min est. market cap $M")
    ap.add_argument("--drop", type=float, default=-75.0, help="52-week performance threshold %%")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--universe-limit", type=int, default=0, help="Cap universe size (testing)")
    args = ap.parse_args()

    universe = get_universe()
    if args.universe_limit:
        universe = universe[: args.universe_limit]

    closes, dollar_vol = download_prices(universe, args.skip_download)
    res = run_backtest(closes, dollar_vol, args.start, args.min_cap, args.drop)
    summary = summarize(res, args.min_cap, args.drop)

    out_json = CACHE / "backtest_summary.json"
    out_json.write_text(json.dumps(summary, indent=2))
    res["df"].to_csv(CACHE / "backtest_detail.csv", index=False)
    print(f"[backtest] Summary → {out_json}")

    for label, s in summary["windows"].items():
        print(f"  {label:>3}: n={s['n']:5d}  median={s['median']:+.1%}  "
              f"spy_median={s['spy_median']:+.1%}  beat_spy={s['pct_beat_spy']:.0%}  "
              f"lost>50%={s['pct_lost_half']:.0%}  doubled={s['pct_doubled']:.0%}")

    from src.backtest_report import render
    path = render(summary)
    print(f"[backtest] Report → {path}")


if __name__ == "__main__":
    main()
