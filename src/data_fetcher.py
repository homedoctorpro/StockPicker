"""
Centralized data fetching with disk-based caching.

Cache layout: cache/YYYY-MM/<key>.json
TTLs: price/financials = 1 day, EDGAR filings = 30 days, sector medians = 365 days
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

CACHE_DIR = Path(__file__).parent.parent / "cache"
EDGAR_BASE = "https://data.sec.gov"
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"
EDGAR_HEADERS = {"User-Agent": "StockPicker research@example.com"}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(month: str, key: str) -> Path:
    p = CACHE_DIR / month
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{key}.json"


def _cache_get(month: str, key: str, ttl_seconds: int) -> Any | None:
    path = _cache_path(month, key)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl_seconds:
        return None
    with open(path) as f:
        return json.load(f)


def _cache_set(month: str, key: str, data: Any) -> None:
    path = _cache_path(month, key)
    with open(path, "w") as f:
        json.dump(data, f)


# ---------------------------------------------------------------------------
# yfinance wrappers
# ---------------------------------------------------------------------------

def get_ticker_info(ticker: str, month: str) -> dict:
    key = f"info_{ticker}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached
    try:
        data = yf.Ticker(ticker).info or {}
    except Exception:
        data = {}
    _cache_set(month, key, data)
    return data


def get_financials(ticker: str, month: str) -> dict:
    """Returns income_stmt, balance_sheet, cashflow as serializable dicts."""
    key = f"financials_{ticker}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached

    t = yf.Ticker(ticker)
    result = {}
    for attr in ("income_stmt", "balance_sheet", "cashflow"):
        try:
            df = getattr(t, attr)
            if df is not None and not df.empty:
                result[attr] = df.to_dict("index")  # {metric: {date: value}}
            else:
                result[attr] = {}
        except Exception:
            result[attr] = {}

    # Serialize: {metric_name: {date_str: value}}
    for attr in list(result.keys()):
        serialized = {}
        for row_name, col_vals in result[attr].items():
            serialized[str(row_name)] = {str(k): v for k, v in col_vals.items()}
        result[attr] = serialized

    _cache_set(month, key, result)
    return result


def get_price_history(ticker: str, month: str, period: str = "2y") -> list[dict]:
    key = f"history_{ticker}_{period}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached
    try:
        hist = yf.Ticker(ticker).history(period=period)
        data = [
            {"date": str(idx.date()), "close": row["Close"]}
            for idx, row in hist.iterrows()
        ]
    except Exception:
        data = []
    _cache_set(month, key, data)
    return data


def get_technicals(ticker: str, month: str) -> dict:
    """
    Point-in-time price/volume signals validated by the backtest:
    decline depth, position vs 50/200-day MA, distance off the 52-week low,
    and 60-day median dollar volume (liquidity). Cached like price history.
    Returns {} on failure so callers can treat data as unknown.
    """
    key = f"technicals_{ticker}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        closes = hist["Close"].dropna()
        vols = hist["Volume"].reindex(closes.index)
        if len(closes) < 30:
            data = {}
        else:
            last = float(closes.iloc[-1])
            ma50 = float(closes.tail(50).mean())
            ma200 = float(closes.tail(200).mean())
            low52 = float(closes.min())
            dollar_vol = (closes * vols).dropna()
            data = {
                "decline_52w_pct": (last / float(closes.iloc[0]) - 1) * 100,
                "above_50dma": last > ma50,
                "above_200dma": last > ma200,
                "pct_above_52w_low": (last - low52) / low52 if low52 > 0 else None,
                "median_dollar_vol_60d": float(dollar_vol.tail(60).median()) if len(dollar_vol) else None,
            }
    except Exception:
        data = {}
    _cache_set(month, key, data)
    return data


def get_recommendations(ticker: str, month: str) -> list[dict]:
    key = f"recs_{ticker}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached
    try:
        df = yf.Ticker(ticker).recommendations
        if df is None or df.empty:
            data = []
        else:
            df = df.reset_index()
            data = []
            for _, row in df.iterrows():
                data.append({
                    "date": str(row.get("Date", row.get("date", ""))),
                    "firm": str(row.get("Firm", row.get("firm", ""))),
                    "to_grade": str(row.get("To Grade", row.get("toGrade", ""))),
                    "from_grade": str(row.get("From Grade", row.get("fromGrade", ""))),
                    "action": str(row.get("Action", row.get("action", ""))),
                })
    except Exception:
        data = []
    _cache_set(month, key, data)
    return data


# ---------------------------------------------------------------------------
# SEC EDGAR wrappers
# ---------------------------------------------------------------------------

def get_cik(ticker: str, month: str) -> str | None:
    """Resolve ticker → CIK via EDGAR company search."""
    key = f"cik_{ticker}"
    cached = _cache_get(month, key, ttl_seconds=2592000)  # 30 days
    if cached is not None:
        return cached.get("cik")

    url = f"{EDGAR_BASE}/cgi-bin/browse-edgar?company=&CIK={ticker}&type=10-K&dateb=&owner=include&count=1&search_text=&action=getcompany&output=atom"
    try:
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=10)
        # Extract CIK from URL pattern in response
        import re
        match = re.search(r"CIK=(\d+)", resp.text)
        cik = match.group(1).lstrip("0") if match else None
    except Exception:
        cik = None

    _cache_set(month, key, {"cik": cik})
    return cik


def get_recent_filings(ticker: str, month: str, form_types: list[str]) -> list[dict]:
    """Return recent SEC filings for a ticker filtered by form type."""
    key = f"filings_{ticker}_{'_'.join(form_types)}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached

    cik = get_cik(ticker, month)
    if not cik:
        return []

    try:
        url = f"{EDGAR_BASE}/submissions/CIK{cik.zfill(10)}.json"
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        sub = resp.json()
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        results = []
        for form, date, acc in zip(forms, dates, accessions):
            if form in form_types:
                results.append({"form": form, "date": date, "accession": acc, "cik": cik})
        data = results[:20]
    except Exception:
        data = []

    _cache_set(month, key, data)
    return data


def search_filing_text(ticker: str, month: str, query: str, form_type: str) -> bool:
    """Return True if query text appears in recent filings of given form_type."""
    key = f"fts_{ticker}_{form_type}_{query.replace(' ', '_')[:40]}"
    cached = _cache_get(month, key, ttl_seconds=2592000)
    if cached is not None:
        return cached.get("found", False)

    cik = get_cik(ticker, month)
    if not cik:
        _cache_set(month, key, {"found": False})
        return False

    try:
        params = {
            "q": f'"{query}"',
            "dateRange": "custom",
            "startdt": "2023-01-01",
            "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "forms": form_type,
            "entity": cik,
        }
        resp = requests.get(EDGAR_FTS, params=params, headers=EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        found = resp.json().get("hits", {}).get("total", {}).get("value", 0) > 0
    except Exception:
        found = False

    _cache_set(month, key, {"found": found})
    return found


def get_form4_buys(ticker: str, month: str, lookback_days: int = 90) -> list[dict]:
    """Return open-market insider purchases (Form 4) in the last N days."""
    key = f"form4_{ticker}_{lookback_days}"
    cached = _cache_get(month, key, ttl_seconds=86400)
    if cached is not None:
        return cached

    filings = get_recent_filings(ticker, month, ["4"])
    cutoff = datetime.now(timezone.utc).date()
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=lookback_days)

    buys = []
    for filing in filings[:10]:
        try:
            filing_date = datetime.strptime(filing["date"], "%Y-%m-%d").date()
            if filing_date < cutoff:
                continue
            # Fetch the filing index to get the actual document
            acc = filing["accession"].replace("-", "")
            cik = filing["cik"].zfill(10)
            index_url = f"{EDGAR_BASE}/Archives/edgar/data/{int(filing['cik'])}/{acc}/{filing['accession']}-index.json"
            resp = requests.get(index_url, headers=EDGAR_HEADERS, timeout=10)
            resp.raise_for_status()
            idx = resp.json()
            for doc in idx.get("directory", {}).get("item", []):
                if doc.get("name", "").endswith(".xml") and "doc" in doc.get("name", "").lower():
                    xml_url = f"{EDGAR_BASE}/Archives/edgar/data/{int(filing['cik'])}/{acc}/{doc['name']}"
                    xml_resp = requests.get(xml_url, headers=EDGAR_HEADERS, timeout=10)
                    # Check for transaction code P (open market purchase)
                    if "<transactionCode>P</transactionCode>" in xml_resp.text:
                        import re
                        amounts = re.findall(r"<transactionShares>.*?<value>([\d.]+)</value>", xml_resp.text)
                        prices = re.findall(r"<transactionPricePerShare>.*?<value>([\d.]+)</value>", xml_resp.text)
                        if amounts and prices:
                            shares = float(amounts[0])
                            price = float(prices[0])
                            buys.append({
                                "date": filing["date"],
                                "shares": shares,
                                "price": price,
                                "value": shares * price,
                            })
                    break
        except Exception:
            continue

    _cache_set(month, key, buys)
    return buys


# ---------------------------------------------------------------------------
# Sector median gross margins (Damodaran NYU data)
# ---------------------------------------------------------------------------

SECTOR_GROSS_MARGINS = {
    "Technology": 0.55,
    "Healthcare": 0.55,
    "Consumer Discretionary": 0.35,
    "Consumer Staples": 0.35,
    "Industrials": 0.30,
    "Materials": 0.25,
    "Energy": 0.35,
    "Financials": 0.50,
    "Real Estate": 0.40,
    "Communication Services": 0.50,
    "Utilities": 0.35,
    "Unknown": 0.35,
}

SECTOR_RECOVERY_RATES = {
    "Technology": 5,
    "Communication Services": 4,
    "Consumer Discretionary": 3,
    "Healthcare": 3,
    "Industrials": 3,
    "Consumer Staples": 3,
    "Materials": 2,
    "Energy": 1,
    "Financials": 1,
    "Real Estate": 2,
    "Utilities": 2,
    "Unknown": 2,
}


def get_sector_gross_margin_median(sector: str) -> float:
    return SECTOR_GROSS_MARGINS.get(sector, SECTOR_GROSS_MARGINS["Unknown"])


def get_sector_recovery_score(sector: str) -> int:
    return SECTOR_RECOVERY_RATES.get(sector, SECTOR_RECOVERY_RATES["Unknown"])
