"""
Six-category bounce-back scoring rubric. Max 100 points total.

  Survivability       30 pts
  Catalyst Potential  20 pts
  Business Quality    20 pts
  Valuation           15 pts
  Decline Reason      10 pts
  Recovery Comps       5 pts
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .data_fetcher import (
    get_financials,
    get_form4_buys,
    get_price_history,
    get_recommendations,
    get_recent_filings,
    get_sector_gross_margin_median,
    get_sector_recovery_score,
    get_ticker_info,
    search_filing_text,
)


@dataclass
class CategoryScore:
    name: str
    score: float
    max_score: float
    details: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class StockScore:
    ticker: str
    company_name: str
    sector: str
    market_cap_mm: float
    perf_52w_pct: float
    total: float
    categories: list[CategoryScore] = field(default_factory=list)
    key_risks: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)

    @property
    def action(self) -> str:
        if self.total >= 75:
            return "Strong Buy Candidate"
        if self.total >= 55:
            return "Watchlist"
        return "Pass"


# ---------------------------------------------------------------------------
# Helper: extract numeric value from financials row
# ---------------------------------------------------------------------------

def _latest_value(row: dict) -> float | None:
    if not row:
        return None
    cols = sorted(row.keys(), reverse=True)
    for c in cols:
        v = row.get(c)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _revenue_series(income: dict) -> list[float]:
    for key in ("Total Revenue", "Revenue", "Net Revenue"):
        if key in income:
            row = income[key]
            cols = sorted(row.keys(), reverse=True)
            vals = []
            for c in cols:
                v = row.get(c)
                if v is not None:
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        pass
            return vals
    return []


def _cashflow_series(cf: dict, key: str) -> list[float]:
    if key not in cf:
        return []
    row = cf[key]
    cols = sorted(row.keys(), reverse=True)
    vals = []
    for c in cols:
        v = row.get(c)
        if v is not None:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    return vals


# ---------------------------------------------------------------------------
# Category 1: Survivability (30 pts)
# ---------------------------------------------------------------------------

def score_survivability(info: dict, financials: dict) -> CategoryScore:
    bs = financials.get("balance_sheet", {})
    cf = financials.get("cashflow", {})
    income = financials.get("income_stmt", {})
    details = {}
    notes = []

    # 1a. Cash runway (12 pts)
    cash_row = None
    for key in ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments", "Cash"):
        if key in bs:
            cash_row = bs[key]
            break
    cash = _latest_value(cash_row) if cash_row else None

    op_cf_series = _cashflow_series(cf, "Operating Cash Flow") or _cashflow_series(cf, "Net Cash Provided By Operating Activities")
    recent_burns = [v for v in op_cf_series[:4] if v < 0]
    avg_quarterly_burn = abs(sum(recent_burns) / len(recent_burns)) if recent_burns else None

    if cash is not None and avg_quarterly_burn and avg_quarterly_burn > 0:
        runway_months = (cash / avg_quarterly_burn) * 3
    elif cash is not None and (not avg_quarterly_burn or avg_quarterly_burn == 0):
        runway_months = float("inf")  # cash-flow positive
    else:
        runway_months = None

    details["cash_runway_months"] = runway_months
    if runway_months is None:
        runway_score = 4
        notes.append("Cash runway unknown (data unavailable)")
    elif runway_months == float("inf"):
        runway_score = 12
        notes.append("Cash flow positive — no burn")
    elif runway_months >= 24:
        runway_score = 12
    elif runway_months >= 18:
        runway_score = 10
    elif runway_months >= 12:
        runway_score = 7
    elif runway_months >= 6:
        runway_score = 4
    elif runway_months >= 3:
        runway_score = 1
    else:
        runway_score = 0
        notes.append("RISK: Cash runway <3 months")

    # 1b. Debt maturity (10 pts) — approximate via total debt vs assets
    total_debt = None
    for key in ("Total Debt", "Long Term Debt", "Long Term Debt And Capital Lease Obligation"):
        if key in bs:
            total_debt = _latest_value(bs[key])
            if total_debt is not None:
                break

    total_assets = None
    for key in ("Total Assets",):
        if key in bs:
            total_assets = _latest_value(bs[key])
            break

    details["total_debt"] = total_debt
    details["total_assets"] = total_assets

    if total_debt is None:
        debt_score = 7
        notes.append("Debt data unavailable — using neutral score")
    elif total_debt == 0:
        debt_score = 10
        notes.append("No long-term debt")
    else:
        debt_to_assets = total_debt / total_assets if total_assets else None
        details["debt_to_assets"] = debt_to_assets
        if debt_to_assets is None:
            debt_score = 5
        elif debt_to_assets < 0.15:
            debt_score = 9
        elif debt_to_assets < 0.30:
            debt_score = 7
        elif debt_to_assets < 0.50:
            debt_score = 4
        elif debt_to_assets < 0.70:
            debt_score = 2
        else:
            debt_score = 0
            notes.append("RISK: Very high leverage (debt/assets >70%)")

    # 1c. Revenue stability (8 pts)
    rev_series = _revenue_series(income)
    details["revenue_series"] = rev_series[:4]

    if len(rev_series) >= 2:
        latest, prior = rev_series[0], rev_series[1]
        if prior > 0:
            rev_change = (latest - prior) / prior
        else:
            rev_change = 0
        details["revenue_yoy_change"] = rev_change

        if rev_change >= 0.05:
            rev_score = 8
            notes.append(f"Revenue growing {rev_change:+.1%} YoY")
        elif rev_change >= -0.05:
            rev_score = 5
            notes.append("Revenue flat YoY")
        elif rev_change >= -0.15:
            rev_score = 3
            notes.append(f"Revenue declining {rev_change:.1%} YoY (moderate)")
        elif rev_change >= -0.30:
            rev_score = 1
            notes.append(f"Revenue declining {rev_change:.1%} YoY (significant)")
        else:
            rev_score = 0
            notes.append(f"RISK: Revenue declining {rev_change:.1%} YoY")
    else:
        rev_score = 3
        notes.append("Insufficient revenue history")

    total = runway_score + debt_score + rev_score
    return CategoryScore(
        name="Survivability",
        score=total,
        max_score=30,
        details=details,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Category 2: Catalyst Potential (20 pts)
# ---------------------------------------------------------------------------

def score_catalysts(ticker: str, info: dict, month: str) -> CategoryScore:
    details = {}
    notes = []

    # 2a. Short interest (8 pts)
    short_pct = info.get("shortPercentOfFloat") or info.get("shortRatio")
    # shortPercentOfFloat is 0-1 scale, shortRatio is days to cover
    if short_pct is not None and short_pct <= 1.0:
        short_pct_display = short_pct  # already fractional
    elif short_pct is not None:
        short_pct_display = short_pct / 100
    else:
        short_pct_display = None

    details["short_pct_of_float"] = short_pct_display
    if short_pct_display is None:
        short_score = 0
        notes.append("Short interest data unavailable")
    elif short_pct_display >= 0.30:
        short_score = 8
        notes.append(f"High short interest: {short_pct_display:.1%} of float — squeeze potential")
    elif short_pct_display >= 0.20:
        short_score = 6
        notes.append(f"Elevated short interest: {short_pct_display:.1%} of float")
    elif short_pct_display >= 0.10:
        short_score = 3
    else:
        short_score = 0

    # 2b. Insider buying via Form 4 (7 pts)
    buys = get_form4_buys(ticker, month, lookback_days=90)
    total_buy_value = sum(b.get("value", 0) for b in buys)
    num_buyers = len(set(b.get("date", "") for b in buys))  # crude proxy for unique events
    details["insider_buy_value"] = total_buy_value
    details["insider_buy_events"] = len(buys)

    if total_buy_value >= 500_000 and num_buyers >= 2:
        insider_score = 7
        notes.append(f"Strong insider buying: ${total_buy_value:,.0f} across {num_buyers} events")
    elif total_buy_value >= 100_000:
        insider_score = 5
        notes.append(f"Insider buying: ${total_buy_value:,.0f}")
    elif total_buy_value > 0:
        insider_score = 2
        notes.append(f"Small insider purchase: ${total_buy_value:,.0f}")
    else:
        insider_score = 0

    # 2c. Analyst activity (5 pts)
    recs = get_recommendations(ticker, month)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    recent_recs = [r for r in recs if str(r.get("date", "")) >= cutoff]
    details["recent_analyst_actions"] = recent_recs[:5]

    buy_grades = {"Buy", "Strong Buy", "Outperform", "Overweight", "Positive"}
    sell_grades = {"Sell", "Strong Sell", "Underperform", "Underweight", "Negative"}

    analyst_score = 0
    for rec in recent_recs:
        action = rec.get("action", "").lower()
        to_grade = rec.get("to_grade", "")
        from_grade = rec.get("from_grade", "")
        if action == "up" and to_grade in buy_grades:
            analyst_score = max(analyst_score, 5)
            notes.append(f"Analyst upgrade to {to_grade}")
        elif action == "init" and to_grade in buy_grades:
            analyst_score = max(analyst_score, 4)
            notes.append(f"New coverage initiated: {to_grade}")
        elif action == "up":
            analyst_score = max(analyst_score, 2)
        elif action == "down":
            analyst_score = min(analyst_score, -2)
            notes.append(f"Analyst downgrade to {to_grade}")

    total = short_score + insider_score + analyst_score
    return CategoryScore(
        name="Catalyst Potential",
        score=max(0, total),
        max_score=20,
        details=details,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Category 3: Business Quality (20 pts)
# ---------------------------------------------------------------------------

def score_business_quality(info: dict, financials: dict) -> CategoryScore:
    income = financials.get("income_stmt", {})
    cf = financials.get("cashflow", {})
    details = {}
    notes = []

    # 3a. Gross margin vs sector median (8 pts)
    sector = info.get("sector", "Unknown")
    sector_median = get_sector_gross_margin_median(sector)

    gm = None
    rev_row = None
    gp_row = None
    for key in ("Total Revenue", "Revenue"):
        if key in income:
            rev_row = income[key]
            break
    for key in ("Gross Profit", "Gross Income"):
        if key in income:
            gp_row = income[key]
            break

    if rev_row and gp_row:
        cols = sorted(rev_row.keys(), reverse=True)
        for col in cols:
            rev = rev_row.get(col)
            gp = gp_row.get(col)
            if rev and float(rev) > 0 and gp is not None:
                gm = float(gp) / float(rev)
                break

    details["gross_margin"] = gm
    details["sector_median_gm"] = sector_median
    if gm is None:
        gm_score = 3
        notes.append("Gross margin data unavailable")
    else:
        diff = gm - sector_median
        if diff >= 0.20:
            gm_score = 8
            notes.append(f"Gross margin {gm:.1%} — {diff*100:.0f}pp above sector median")
        elif diff >= 0.10:
            gm_score = 6
        elif diff >= -0.10:
            gm_score = 4
        elif diff >= -0.20:
            gm_score = 2
        else:
            gm_score = 0
            notes.append(f"Gross margin {gm:.1%} well below sector median {sector_median:.1%}")

    # 3b. Revenue concentration — approximated from info description text
    # yfinance doesn't expose customer concentration, so we check the info dict
    # for common signals and default to neutral
    biz_summary = info.get("longBusinessSummary", "")
    cust_concentration_detected = any(
        phrase in biz_summary.lower()
        for phrase in ["largest customer", "major customer", "significant customer", "principal customer"]
    )
    details["concentration_signal"] = cust_concentration_detected
    if cust_concentration_detected:
        conc_score = 2
        notes.append("Customer concentration language found in business description")
    else:
        conc_score = 5  # neutral — can't confirm low concentration without 10-K parsing

    # 3c. EBITDA trend (6 pts)
    ebitda_row = income.get("EBITDA") or income.get("Normalized EBITDA")
    if ebitda_row:
        cols = sorted(ebitda_row.keys(), reverse=True)
        ebitda_vals = []
        for c in cols[:4]:
            v = ebitda_row.get(c)
            if v is not None:
                try:
                    ebitda_vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        details["ebitda_series"] = ebitda_vals
        if len(ebitda_vals) >= 2:
            latest_ebitda = ebitda_vals[0]
            prior_ebitda = ebitda_vals[1]
            if latest_ebitda > 0:
                ebitda_score = 6
                notes.append("EBITDA positive")
            elif latest_ebitda > prior_ebitda:
                ebitda_score = 4
                notes.append("EBITDA negative but improving")
            elif latest_ebitda == prior_ebitda:
                ebitda_score = 2
            else:
                ebitda_score = 0
                notes.append("EBITDA worsening")
        else:
            ebitda_score = 2
    else:
        # Use operating income as proxy
        op_income_row = income.get("Operating Income") or income.get("Total Operating Income As Reported")
        if op_income_row:
            v = _latest_value(op_income_row)
            ebitda_score = 4 if (v and v > 0) else 2
        else:
            ebitda_score = 2
            notes.append("EBITDA/operating income data unavailable")

    total = gm_score + conc_score + ebitda_score
    return CategoryScore(
        name="Business Quality",
        score=total,
        max_score=20,
        details=details,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Category 4: Valuation (15 pts)
# ---------------------------------------------------------------------------

def score_valuation(ticker: str, info: dict, financials: dict, month: str) -> CategoryScore:
    income = financials.get("income_stmt", {})
    bs = financials.get("balance_sheet", {})
    details = {}
    notes = []

    market_cap = info.get("marketCap")

    # 4a. P/S vs 3-year historical average (8 pts)
    rev_series = _revenue_series(income)
    current_ps = info.get("priceToSalesTrailing12Months")

    # Compute historical P/S using 3y price history and revenue
    ps_score = 0
    if market_cap and rev_series and len(rev_series) >= 1:
        current_revenue = rev_series[0]
        if current_revenue > 0:
            current_ps_calc = market_cap / current_revenue
            details["current_ps"] = current_ps_calc

            # Historical P/S: use price history and older revenue figures
            price_hist = get_price_history(ticker, month, period="3y")
            if price_hist and len(rev_series) >= 3:
                # Rough average P/S over 3 years: average price / average annual rev
                prices = [p["close"] for p in price_hist]
                avg_price_3y = sum(prices) / len(prices)
                shares = market_cap / (prices[-1] if prices else 1)
                avg_market_cap_3y = avg_price_3y * shares
                avg_rev_3y = sum(rev_series[:3]) / 3
                historical_ps = avg_market_cap_3y / avg_rev_3y if avg_rev_3y > 0 else None
                details["historical_ps_3y"] = historical_ps

                if historical_ps and historical_ps > 0:
                    ratio = current_ps_calc / historical_ps
                    details["ps_vs_historical_ratio"] = ratio
                    if ratio <= 0.20:
                        ps_score = 8
                        notes.append(f"Trading at {ratio:.0%} of 3-year historical P/S — deeply discounted")
                    elif ratio <= 0.35:
                        ps_score = 6
                    elif ratio <= 0.50:
                        ps_score = 4
                    elif ratio <= 0.70:
                        ps_score = 2
                    else:
                        ps_score = 0
                        notes.append(f"P/S at {ratio:.0%} of historical — limited valuation discount")
            else:
                ps_score = 3  # partial data
        else:
            ps_score = 0
    else:
        ps_score = 3
        notes.append("P/S data unavailable")

    # 4b. Price-to-book (7 pts)
    pb = info.get("priceToBook")
    details["price_to_book"] = pb
    if pb is None:
        # Try to compute from balance sheet
        equity_row = bs.get("Stockholders Equity") or bs.get("Total Stockholder Equity")
        if equity_row and market_cap:
            equity = _latest_value(equity_row)
            if equity and equity > 0:
                pb = market_cap / equity

    if pb is None:
        pb_score = 3
        notes.append("Price/book data unavailable")
    elif pb <= 0:
        pb_score = 0
        notes.append("Negative book value — liabilities exceed assets")
    elif pb <= 0.5:
        pb_score = 7
        notes.append(f"Deep value: P/B = {pb:.2f}x (below liquidation value)")
    elif pb <= 0.75:
        pb_score = 5
        notes.append(f"Trading below book: P/B = {pb:.2f}x")
    elif pb <= 1.0:
        pb_score = 3
    elif pb <= 1.5:
        pb_score = 1
    else:
        pb_score = 0

    total = ps_score + pb_score
    return CategoryScore(
        name="Valuation",
        score=total,
        max_score=15,
        details=details,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Category 5: Decline Reason (10 pts)
# ---------------------------------------------------------------------------

SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Energy": "XLE",
    "Financials": "XLF",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Utilities": "XLU",
}


def score_decline_reason(ticker: str, info: dict, month: str) -> CategoryScore:
    import yfinance as yf
    from datetime import datetime, timedelta
    details = {}
    notes = []

    sector = info.get("sector", "Unknown")
    etf = SECTOR_ETFS.get(sector)

    # Check sector ETF performance over same 52-week window
    sector_declined = False
    if etf:
        try:
            end = datetime.today()
            start = end - timedelta(days=370)
            hist = yf.download(etf, start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                               auto_adjust=True, progress=False, threads=False)
            if not hist.empty:
                closes = hist["Close"].dropna()
                sector_perf = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0]
                details["sector_etf"] = etf
                details["sector_etf_perf"] = float(sector_perf.iloc[0]) if hasattr(sector_perf, "iloc") else float(sector_perf)
                if sector_perf <= -0.20:
                    sector_declined = True
                    notes.append(f"Sector ETF ({etf}) also down {sector_perf:.1%} — macro/sector rotation factor")
        except Exception:
            pass

    # Check for executive departure or restatement via 8-K items
    has_exec_departure = search_filing_text(ticker, month, "chief executive", "8-K")
    has_restatement = search_filing_text(ticker, month, "restatement", "8-K")
    details["exec_departure_signal"] = has_exec_departure
    details["restatement_signal"] = has_restatement

    if has_restatement:
        score = 2
        notes.append("Restatement detected in 8-K — accounting risk")
    elif sector_declined:
        score = 10
        notes.append("Likely macro/sector rotation — business may be intact")
    elif has_exec_departure:
        score = 6
        notes.append("Executive departure — new management may be catalyst")
    else:
        score = 5  # neutral, cannot auto-classify
        notes.append("Decline reason unclear from available data — review manually")

    return CategoryScore(
        name="Decline Reason",
        score=score,
        max_score=10,
        details=details,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Category 6: Recovery Comps (5 pts)
# ---------------------------------------------------------------------------

def score_recovery_comps(info: dict) -> CategoryScore:
    sector = info.get("sector", "Unknown")
    score = get_sector_recovery_score(sector)
    return CategoryScore(
        name="Recovery Comps",
        score=score,
        max_score=5,
        details={"sector": sector},
        notes=[f"Sector base rate score for {sector}"],
    )


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score_ticker(ticker: str, month: str) -> StockScore | None:
    info = get_ticker_info(ticker, month)
    if not info:
        return None

    financials = get_financials(ticker, month)

    market_cap = info.get("marketCap", 0) or 0
    market_cap_mm = market_cap / 1_000_000

    # Compute 52-week performance
    price_hist = get_price_history(ticker, month, period="1y")
    if price_hist and len(price_hist) >= 2:
        perf_52w = (price_hist[-1]["close"] - price_hist[0]["close"]) / price_hist[0]["close"] * 100
    else:
        perf_52w = 0.0

    cats = [
        score_survivability(info, financials),
        score_catalysts(ticker, info, month),
        score_business_quality(info, financials),
        score_valuation(ticker, info, financials, month),
        score_decline_reason(ticker, info, month),
        score_recovery_comps(info),
    ]

    total = sum(c.score for c in cats)

    # Collect key risks and catalysts from notes
    risks = []
    catalysts = []
    for cat in cats:
        for note in cat.notes:
            if note.upper().startswith("RISK") or "RISK:" in note:
                risks.append(note)
            elif any(kw in note for kw in ["upgrade", "buying", "squeeze", "positive", "growing", "intact", "catalyst"]):
                catalysts.append(note)

    return StockScore(
        ticker=ticker,
        company_name=info.get("longName", ticker),
        sector=info.get("sector", "Unknown"),
        market_cap_mm=market_cap_mm,
        perf_52w_pct=perf_52w,
        total=round(total, 1),
        categories=cats,
        key_risks=risks,
        catalysts=catalysts,
    )


def score_all(tickers: list[str], month: str, delay: float = 0.5) -> list[StockScore]:
    import time
    results = []
    for ticker in tickers:
        print(f"[scorer] Scoring {ticker}...")
        s = score_ticker(ticker, month)
        if s:
            results.append(s)
            print(f"[scorer] {ticker}: {s.total}/100 ({s.action})")
        time.sleep(delay)

    results.sort(key=lambda x: x.total, reverse=True)
    return results
