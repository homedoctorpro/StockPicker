"""
Faithful point-in-time fundamental replay of the scoring pipeline.

The live scorer/disqualifier read yfinance's current snapshot, so they can't
be run on old cohorts without lookahead bias. This rebuilds the *fundamental*
sub-scores from SEC EDGAR XBRL companyfacts, which stamp every figure with the
date it was FILED — so for a cohort dated D we use only facts filed on or
before D. We then ask the real question: did higher fundamental quality (as the
scorer conceives it) actually separate the winners from the falling knives?

Coverage & honesty:
- Reconstructs the fundamental-derivable sub-scores only: Survivability
  (cash runway, leverage, revenue trend, 30pts), Business Quality gross margin
  (8pts), Valuation P/S + P/B (15pts) — 53 of the 100 live points. The Catalyst
  category (short interest, insider, analyst) needs FINRA/other point-in-time
  feeds and is NOT covered; stated in the report.
- Market cap at date = as-of-date shares (EDGAR) x price (backtest matrix).
- Reads only cached data + EDGAR; run `--fetch-only` first to populate the
  companyfacts cache (resumable), then run without it to analyse.

Usage:
  python edgar_replay.py --fetch-only     # download slim companyfacts (resumable)
  python edgar_replay.py                   # reconstruct, score, analyse, render
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = Path(__file__).parent / "cache" / "backtest"
EDGAR_DIR = CACHE / "edgar"
HEADERS = {"User-Agent": "PhoenixPicks research pross@example.com"}
CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# us-gaap concept fallbacks (first match wins)
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}
SHARES = ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"]


# ---------------------------------------------------------------------------
# Fetch (resumable, slim cache)
# ---------------------------------------------------------------------------

def load_cik_map() -> dict:
    r = requests.get(CIK_MAP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return {v["ticker"].replace(".", "-"): int(v["cik_str"]) for v in r.json().values()}


def _extract(points_by_unit: dict) -> list:
    unit = "USD" if "USD" in points_by_unit else next(iter(points_by_unit), None)
    if unit is None:
        return []
    out = []
    for p in points_by_unit[unit]:
        if p.get("val") is None or "filed" not in p or "end" not in p:
            continue
        out.append({"start": p.get("start"), "end": p["end"], "val": p["val"],
                    "filed": p["filed"], "form": p.get("form"), "fp": p.get("fp")})
    return out


def fetch_slim(ticker: str, cik: int) -> dict | None:
    path = EDGAR_DIR / f"{ticker}.json"
    if path.exists():
        return json.loads(path.read_text())
    try:
        r = requests.get(FACTS_URL.format(cik=cik), headers=HEADERS, timeout=30)
        if r.status_code == 404:
            path.write_text("{}")
            return {}
        r.raise_for_status()
        facts = r.json().get("facts", {})
    except Exception as e:
        print(f"[edgar] {ticker} (CIK {cik}) failed: {e}")
        return None

    gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})
    slim = {}
    for name, candidates in CONCEPTS.items():
        for c in candidates:
            if c in gaap:
                slim[name] = _extract(gaap[c]["units"])
                break
    for c in SHARES:
        src = dei if c in dei else (gaap if c in gaap else None)
        if src is not None:
            units = src[c]["units"]
            unit = "shares" if "shares" in units else next(iter(units), None)
            if unit:
                slim["shares"] = [{"end": p["end"], "val": p["val"], "filed": p["filed"]}
                                  for p in units[unit] if p.get("val") and "filed" in p]
            break
    path.write_text(json.dumps(slim))
    return slim


def run_fetch():
    EDGAR_DIR.mkdir(parents=True, exist_ok=True)
    detail = pd.read_csv(CACHE / "backtest_detail.csv")
    tickers = sorted(set(detail[detail["ret_12m"].notna()]["ticker"]))
    cik_map = load_cik_map()
    have = {p.stem for p in EDGAR_DIR.glob("*.json")}
    todo = [t for t in tickers if t not in have]
    matched = [t for t in todo if t in cik_map]
    print(f"[edgar] {len(tickers)} tickers, {len(have)} cached, "
          f"{len(todo)} to do ({len(matched)} resolve to a CIK)")
    for i, t in enumerate(todo):
        if t not in cik_map:
            (EDGAR_DIR / f"{t}.json").write_text("{}")  # unresolvable → empty
            continue
        fetch_slim(t, cik_map[t])
        time.sleep(0.13)  # < 10 req/s
        if (i + 1) % 100 == 0:
            print(f"[edgar] fetched {i + 1}/{len(todo)}")
    print(f"[edgar] fetch complete: {len(list(EDGAR_DIR.glob('*.json')))} files")


# ---------------------------------------------------------------------------
# Point-in-time reconstruction
# ---------------------------------------------------------------------------

def _annuals_asof(points: list, date: str) -> list:
    """Annual (~1yr duration) values known as of `date`, newest end first."""
    out = []
    for p in points:
        if p["filed"] > date or not p.get("start"):
            continue
        dur = (pd.Timestamp(p["end"]) - pd.Timestamp(p["start"])).days
        if 330 <= dur <= 400:
            out.append(p)
    out.sort(key=lambda p: p["end"], reverse=True)
    # dedupe by end period (keep the earliest-filed original, i.e. as-known-then)
    seen, uniq = set(), []
    for p in out:
        if p["end"] not in seen:
            seen.add(p["end"]); uniq.append(p)
    return uniq


def _instant_asof(points: list, date: str):
    """Most recent instant (balance-sheet) value known as of `date`."""
    cand = [p for p in points if p["filed"] <= date and not p.get("start")]
    if not cand:
        cand = [p for p in points if p["filed"] <= date]
    if not cand:
        return None
    cand.sort(key=lambda p: p["end"], reverse=True)
    return cand[0]["val"]


def reconstruct(slim: dict, date: str) -> dict:
    f = {}
    rev = _annuals_asof(slim.get("revenue", []), date)
    f["revenue"] = rev[0]["val"] if rev else None
    f["revenue_prior"] = rev[1]["val"] if len(rev) > 1 else None
    gp = _annuals_asof(slim.get("gross_profit", []), date)
    f["gross_profit"] = gp[0]["val"] if gp else None
    ocf = _annuals_asof(slim.get("ocf", []), date)
    f["ocf_annual"] = ocf[0]["val"] if ocf else None
    f["cash"] = _instant_asof(slim.get("cash", []), date)
    f["assets"] = _instant_asof(slim.get("assets", []), date)
    f["liabilities"] = _instant_asof(slim.get("liabilities", []), date)
    f["equity"] = _instant_asof(slim.get("equity", []), date)
    f["shares"] = _instant_asof(slim.get("shares", []), date)
    return f


# ---------------------------------------------------------------------------
# Fundamental scoring (mirrors scorer.py; fundamental-derivable parts only)
# ---------------------------------------------------------------------------

def score_fundamentals(f: dict, price: float) -> dict:
    notes = {}
    rev, rev_prior = f["revenue"], f["revenue_prior"]
    cash, ocf = f["cash"], f["ocf_annual"]
    assets, liab, equity = f["assets"], f["liabilities"], f["equity"]
    shares = f["shares"]
    mktcap = price * shares if (price and shares) else None

    # --- Survivability (30) ---
    # runway (12)
    if cash is not None and ocf is not None and ocf < 0:
        runway = cash / (abs(ocf) / 4) * 3
        runway_score = (12 if runway >= 24 else 10 if runway >= 18 else 7 if runway >= 12
                        else 4 if runway >= 6 else 1 if runway >= 3 else 0)
    elif cash is not None and ocf is not None:
        runway = float("inf"); runway_score = 12
    else:
        runway = None; runway_score = 4
    # leverage via liabilities/assets (10)
    if liab is not None and assets:
        lev = liab / assets
        lev_score = (10 if lev < 0.15 else 9 if lev < 0.30 else 7 if lev < 0.50
                     else 4 if lev < 0.70 else 2 if lev < 0.85 else 0)
    else:
        lev = None; lev_score = 7
    # revenue trend (8)
    if rev is not None and rev_prior and rev_prior > 0:
        chg = (rev - rev_prior) / rev_prior
        rev_score = (8 if chg >= 0.05 else 5 if chg >= -0.05 else 3 if chg >= -0.15
                     else 1 if chg >= -0.30 else 0)
    else:
        chg = None; rev_score = 3
    surv = runway_score + lev_score + rev_score

    # --- Business Quality: gross margin only (8) ---
    if rev and rev > 0 and f["gross_profit"] is not None:
        gm = f["gross_profit"] / rev
        gm_score = (8 if gm >= 0.55 else 6 if gm >= 0.40 else 4 if gm >= 0.25
                    else 2 if gm >= 0.10 else 0 if gm >= 0 else 0)
    else:
        gm = None; gm_score = 3

    # --- Valuation (15): P/S (8) + P/B (7) ---
    if mktcap and rev and rev > 0:
        ps = mktcap / rev
        ps_score = (8 if ps <= 0.5 else 6 if ps <= 1 else 4 if ps <= 2 else 2 if ps <= 4 else 0)
    else:
        ps = None; ps_score = 3
    if mktcap and equity and equity > 0:
        pb = mktcap / equity
        pb_score = (7 if pb <= 0.5 else 5 if pb <= 0.75 else 3 if pb <= 1 else 1 if pb <= 1.5 else 0)
    elif equity is not None and equity <= 0:
        pb = None; pb_score = 0  # negative book value
    else:
        pb = None; pb_score = 3

    total = surv + gm_score + ps_score + pb_score  # of 53
    # disqualifier replay (fundamental flags only)
    flags = []
    if rev is None or rev <= 0:
        flags.append("no_revenue")
    if chg is not None and chg < -0.50:
        flags.append("revenue_crash")
    if gm is not None and gm < 0:
        flags.append("negative_gross_margin")
    if runway is not None and runway != float("inf") and runway < 3:
        flags.append("cash_runway_lt_3mo")

    have_core = sum(x is not None for x in (rev, cash, assets)) >= 2
    return {"score53": total, "score100": total / 53 * 100,
            "flags": flags, "passed": len(flags) == 0, "have_data": have_core,
            "runway_score": runway_score, "lev_score": lev_score, "rev_score": rev_score,
            "gm_score": gm_score, "ps_score": ps_score, "pb_score": pb_score}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def run_analysis():
    closes, _ = pd.read_pickle(CACHE / "prices_combined.pkl")
    detail = pd.read_csv(CACHE / "backtest_detail.csv", parse_dates=["date"])
    detail = detail[detail["ret_12m"].notna()].copy()

    rows = []
    missing_files = 0
    for r in detail.itertuples():
        path = EDGAR_DIR / f"{r.ticker}.json"
        if not path.exists():
            missing_files += 1
            continue
        slim = json.loads(path.read_text())
        if not slim:
            continue
        dstr = r.date.strftime("%Y-%m-%d")
        price = closes.at[r.date, r.ticker] if (r.date in closes.index and r.ticker in closes.columns) else None
        f = reconstruct(slim, dstr)
        sc = score_fundamentals(f, float(price) if price and np.isfinite(price) else None)
        if not sc["have_data"]:
            continue
        rows.append({"date": r.date, "ticker": r.ticker, "ret_12m": r.ret_12m,
                     "spy_12m": r.spy_12m, "score100": sc["score100"],
                     "passed": sc["passed"], "flags": ";".join(sc["flags"])})

    df = pd.DataFrame(rows)
    n = len(df)
    print(f"[edgar] reconstructed fundamentals for {n} positions "
          f"({n/len(detail):.0%} of {len(detail)}; {missing_files} had no EDGAR file)")

    def block(g):
        return {"n": int(len(g)), "median": float(g["ret_12m"].median()),
                "spy_median": float(g["spy_12m"].median()),
                "pct_beat_spy": float((g["ret_12m"] > g["spy_12m"]).mean()),
                "pct_doubled": float((g["ret_12m"] >= 1.0).mean())}

    # Score quintiles: does higher fundamental score => better outcome?
    df["q"] = pd.qcut(df["score100"], 5, labels=["Q1 (worst)", "Q2", "Q3", "Q4", "Q5 (best)"], duplicates="drop")
    quintiles = [{"label": str(q), **block(g)} for q, g in df.groupby("q", observed=True)]

    # Disqualifier replay: passed vs would-be-disqualified
    disq = {"passed": block(df[df["passed"]]), "disqualified": block(df[~df["passed"]])}

    summary = {
        "n_positions": n,
        "coverage": n / len(detail),
        "quintiles": quintiles,
        "disqualifier": disq,
        "score_corr": float(df["score100"].corr(df["ret_12m"])),
        "top_quintile_vs_bottom": {
            "top_median": quintiles[-1]["median"] if quintiles else None,
            "bottom_median": quintiles[0]["median"] if quintiles else None,
        },
    }
    (CACHE / "edgar_summary.json").write_text(json.dumps(summary, indent=2))
    df.to_csv(CACHE / "edgar_detail.csv", index=False)

    print(f"[edgar] score/return correlation: {summary['score_corr']:+.3f}")
    print("[edgar] fundamental-score quintiles (12m):")
    for q in quintiles:
        print(f"    {q['label']:12s} n={q['n']:4d}  median {q['median']:+.1%}  "
              f"beatSPY {q['pct_beat_spy']:.0%}  doubled {q['pct_doubled']:.0%}")
    print(f"[edgar] disqualifier — passed:   {disq['passed']['median']:+.1%} median, "
          f"beatSPY {disq['passed']['pct_beat_spy']:.0%} (n={disq['passed']['n']})")
    print(f"[edgar] disqualifier — flagged:  {disq['disqualified']['median']:+.1%} median, "
          f"beatSPY {disq['disqualified']['pct_beat_spy']:.0%} (n={disq['disqualified']['n']})")

    # merge into backtest summary + re-render
    summ = json.loads((CACHE / "backtest_summary.json").read_text())
    summ["edgar"] = summary
    (CACHE / "backtest_summary.json").write_text(json.dumps(summ, indent=2))
    from src.backtest_report import render
    print(f"[edgar] Report → {render(summ)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-only", action="store_true")
    args = ap.parse_args()
    if args.fetch_only:
        run_fetch()
    else:
        run_fetch()
        run_analysis()


if __name__ == "__main__":
    main()
