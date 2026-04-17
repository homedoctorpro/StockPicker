"""
Screen for US stocks down 75%+ in the past year with >$300M market cap.

Primary: Finviz screener (no API key needed, free)
Fallback: yfinance batch download against Russell 3000 from iShares CSV
"""
import time
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

FINVIZ_SCREENER = "https://finviz.com/screener.ashx"
FINVIZ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def screen_finviz(min_market_cap_mm: int = 300, max_perf_pct: float = -75.0) -> list[str]:
    """
    Query Finviz screener for US stocks with 52-week performance <= max_perf_pct
    and market cap >= min_market_cap_mm ($M).

    Finviz performance filter codes (52-week):
      perf52w_-75%to-100% = down 75% to 100%
    Market cap codes:
      cap_midover = Mid cap and above ($2B+) — too restrictive
      cap_smallover = Small cap and above ($300M+) is closest available
      We filter precisely by market cap after fetching.
    """
    tickers = []
    row = 1
    while True:
        params = {
            "v": "111",
            "f": "geo_usa,perf52w_-75to-100",
            "r": str(row),
            "ft": "4",
        }
        try:
            resp = requests.get(FINVIZ_SCREENER, params=params, headers=FINVIZ_HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"[screener] Finviz request failed at row {row}: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", {"id": "screener-views-table"})
        if not table:
            # Try alternate table id used by Finviz
            table = soup.find("table", class_="table-light")
        if not table:
            break

        rows = table.find_all("tr")[1:]  # skip header
        if not rows:
            break

        batch = []
        for tr in rows:
            cells = tr.find_all("td")
            if len(cells) >= 2:
                ticker_cell = cells[1].get_text(strip=True)
                if ticker_cell and ticker_cell.isalpha():
                    batch.append(ticker_cell)

        if not batch:
            break

        tickers.extend(batch)
        row += len(batch)

        if len(batch) < 20:
            break

        time.sleep(1.5)

    print(f"[screener] Finviz returned {len(tickers)} raw tickers")
    return tickers


def filter_by_market_cap(tickers: list[str], min_cap_mm: int = 300) -> list[str]:
    """Drop tickers below min_cap_mm ($M) using yfinance fast_info."""
    import yfinance as yf
    passing = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            cap = getattr(info, "market_cap", None)
            if cap and cap >= min_cap_mm * 1_000_000:
                passing.append(ticker)
            time.sleep(0.1)
        except Exception:
            passing.append(ticker)  # include on error, disqualifier will handle
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
