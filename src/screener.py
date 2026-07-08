"""
Screen for US stocks down 75%+ in the past year with >$300M market cap.

Primary: Finviz screener (no API key needed, free)
Fallback: yfinance batch download against Russell 3000 from iShares CSV
"""
import re
import time
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

FINVIZ_SCREENER = "https://finviz.com/screener.ashx"
# If Finviz reports more results than this, the performance filter was ignored
# (e.g. token renamed) and we'd be screening the whole US market — bail to fallback.
FINVIZ_MAX_PLAUSIBLE = 1000
FINVIZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def screen_finviz(min_market_cap_mm: int = 300, max_perf_pct: float = -75.0) -> list[str]:
    """
    Query Finviz screener for US stocks down 75%+ over 52 weeks with
    market cap >= min_market_cap_mm ($M).

    Finviz filter tokens (verified against the live screener July 2026):
      ta_perf_52w75u = Performance Year -75% or worse
      cap_smallover  = Small cap and above ($300M+)
      geo_usa        = USA
    We still filter precisely by market cap after fetching.
    """
    filters = "geo_usa,ta_perf_52w75u"
    if min_market_cap_mm >= 300:
        filters = "geo_usa,cap_smallover,ta_perf_52w75u"

    tickers: list[str] = []
    seen: set[str] = set()
    total = None
    row = 1
    while True:
        params = {
            "v": "111",
            "f": filters,
            "r": str(row),
            "ft": "4",
        }
        try:
            resp = requests.get(FINVIZ_SCREENER, params=params, headers=FINVIZ_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[screener] Finviz request failed at row {row}: {e}")
            break

        if total is None:
            m = re.search(r"#\d+ / (\d+) Total", resp.text)
            total = int(m.group(1)) if m else None
            print(f"[screener] Finviz reports {total} total matches")
            if total is not None and total > FINVIZ_MAX_PLAUSIBLE:
                print(f"[screener] {total} matches is implausible — the performance "
                      f"filter was ignored. Abandoning Finviz (fallback will be used).")
                return []

        # Ticker rows are anchors like <a class="tab-link" href="stock?t=XYZ...">XYZ</a>.
        # Other tab-link anchors ("Open in Compare", export links, ...) have no t= param
        # or their text doesn't match the ticker — both checks required to avoid junk.
        soup = BeautifulSoup(resp.text, "lxml")
        batch = []
        for a in soup.find_all("a", class_="tab-link", href=True):
            m = re.search(r"(?:stock|quote\.ashx)\?t=([A-Za-z0-9.\-]+)", a["href"])
            if m and a.get_text(strip=True) == m.group(1):
                batch.append(m.group(1))

        new = [t for t in batch if t not in seen]
        if not new:
            break
        tickers.extend(new)
        seen.update(new)

        if total is not None and len(tickers) >= total:
            break

        row += len(new)
        time.sleep(1.5)

    print(f"[screener] Finviz returned {len(tickers)} raw tickers")
    return tickers


def filter_by_market_cap(tickers: list[str], min_cap_mm: int = 300) -> list[str]:
    """Drop tickers below min_cap_mm ($M) using yfinance fast_info."""
    import yfinance as yf
    passing = []
    for ticker in tickers:
        cap = None
        for attempt in range(2):
            try:
                info = yf.Ticker(ticker).fast_info
                cap = getattr(info, "market_cap", None)
                break
            except Exception:
                time.sleep(2.0)
        if cap is None:
            # Excluding on failure keeps junk symbols out; a real ticker whose
            # lookup failed twice would score poorly anyway with no data.
            print(f"[screener] market cap lookup failed for {ticker} — excluding")
            continue
        if cap >= min_cap_mm * 1_000_000:
            passing.append(ticker)
        time.sleep(0.1)
    return passing


def screen_fallback_russell3000(min_cap_mm: int = 300, max_perf_pct: float = -75.0) -> list[str]:
    """
    Fallback: download iShares Russell 3000 ETF holdings CSV, then batch-check
    52-week performance via yfinance.
    """
    import yfinance as yf
    from datetime import datetime, timedelta

    print("[screener] Using fallback: Russell 3000 from iShares CSV")
    IWV_CSV = "https://www.ishares.com/us/products/239714/IWV/1467271812596.ajax?fileType=csv&fileName=IWV_holdings&dataType=fund"
    try:
        df = pd.read_csv(IWV_CSV, skiprows=9, thousands=",")
        tickers_all = df["Ticker"].dropna().tolist()
        tickers_all = [str(t).strip() for t in tickers_all if str(t).strip().isalpha()]
    except Exception as e:
        print(f"[screener] iShares CSV failed: {e}. Trying Wikipedia S&P 500 list.")
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers_all = sp500["Symbol"].tolist()

    print(f"[screener] Universe: {len(tickers_all)} tickers")

    end = datetime.today()
    start = end - timedelta(days=370)

    # Batch download — yfinance handles this efficiently
    try:
        hist = yf.download(
            tickers_all, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
            auto_adjust=True, progress=False, threads=True
        )
        closes = hist["Close"] if "Close" in hist.columns else hist
    except Exception as e:
        print(f"[screener] yfinance batch download failed: {e}")
        return []

    candidates = []
    for ticker in closes.columns:
        series = closes[ticker].dropna()
        if len(series) < 50:
            continue
        price_1y_ago = series.iloc[0]
        price_now = series.iloc[-1]
        if price_1y_ago <= 0:
            continue
        perf = (price_now - price_1y_ago) / price_1y_ago * 100
        if perf <= max_perf_pct:
            candidates.append(ticker)

    print(f"[screener] Fallback: {len(candidates)} candidates before market cap filter")
    return filter_by_market_cap(candidates, min_cap_mm)


def get_candidates(min_cap_mm: int = 300, use_fallback: bool = False) -> list[str]:
    """Return tickers meeting screen criteria, deduped."""
    if use_fallback:
        tickers = screen_fallback_russell3000(min_cap_mm)
    else:
        tickers = screen_finviz(min_cap_mm)
        if not tickers:
            print("[screener] Finviz returned no results, trying fallback")
            tickers = screen_fallback_russell3000(min_cap_mm)
        else:
            tickers = filter_by_market_cap(tickers, min_cap_mm)

    tickers = list(dict.fromkeys(tickers))  # dedupe preserving order
    print(f"[screener] Final candidates after market cap filter: {len(tickers)}")
    return tickers
