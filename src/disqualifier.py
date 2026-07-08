"""
Red-flag disqualification checks. Any single flag removes a stock from scoring.
"""
import time
from dataclasses import dataclass, field

from .data_fetcher import (
    get_financials,
    get_recent_filings,
    get_technicals,
    get_ticker_info,
    search_filing_text,
)

# Backtest-derived floors (July 2026): the illiquid (<$1M/day) and deepest-
# decline (worse than -90%) buckets beat SPY only ~14% and ~10% of the time
# and dominated the losses. Screen them out up front.
MIN_DOLLAR_VOL = 1_000_000
MAX_DECLINE_PCT = -90.0


@dataclass
class DisqualResult:
    ticker: str
    passed: bool
    flags: list[str] = field(default_factory=list)


def _get_revenue_trend(financials: dict) -> tuple[float | None, float | None]:
    """Return (latest_annual_revenue, prior_annual_revenue) from income statement."""
    income = financials.get("income_stmt", {})
    revenue_row = None
    for key in ("Total Revenue", "Revenue", "Net Revenue"):
        if key in income:
            revenue_row = income[key]
            break
    if not revenue_row:
        return None, None

    cols = sorted(revenue_row.keys(), reverse=True)
    values = [revenue_row[c] for c in cols if revenue_row[c] is not None]
    if len(values) >= 2:
        return values[0], values[1]
    if len(values) == 1:
        return values[0], None
    return None, None


def _get_gross_margin(financials: dict) -> float | None:
    income = financials.get("income_stmt", {})
    revenue_row = None
    gross_row = None
    for key in ("Total Revenue", "Revenue"):
        if key in income:
            revenue_row = income[key]
            break
    for key in ("Gross Profit", "Gross Income"):
        if key in income:
            gross_row = income[key]
            break
    if not revenue_row or not gross_row:
        return None
    cols = sorted(revenue_row.keys(), reverse=True)
    for col in cols:
        rev = revenue_row.get(col)
        gp = gross_row.get(col)
        if rev and rev > 0 and gp is not None:
            return gp / rev
    return None


def _get_cash_runway_months(financials: dict) -> float | None:
    """Estimate months of cash runway from balance sheet and cash flow."""
    bs = financials.get("balance_sheet", {})
    cf = financials.get("cashflow", {})

    cash_row = None
    for key in ("Cash And Cash Equivalents", "Cash", "Cash Cash Equivalents And Short Term Investments"):
        if key in bs:
            cash_row = bs[key]
            break

    burn_row = None
    for key in ("Operating Cash Flow", "Free Cash Flow", "Net Cash Provided By Operating Activities"):
        if key in cf:
            burn_row = cf[key]
            break

    if not cash_row or not burn_row:
        return None

    cash_cols = sorted(cash_row.keys(), reverse=True)
    cash = next((cash_row[c] for c in cash_cols if cash_row[c] is not None), None)

    burn_cols = sorted(burn_row.keys(), reverse=True)
    recent_burns = [burn_row[c] for c in burn_cols[:4] if burn_row[c] is not None and burn_row[c] < 0]

    if cash is None or not recent_burns:
        return None

    avg_quarterly_burn = abs(sum(recent_burns) / len(recent_burns))
    if avg_quarterly_burn == 0:
        return float("inf")

    return (cash / avg_quarterly_burn) * 3  # quarterly burn → monthly


def check(ticker: str, month: str) -> DisqualResult:
    result = DisqualResult(ticker=ticker, passed=True)
    info = get_ticker_info(ticker, month)
    financials = get_financials(ticker, month)

    # 1. No revenue (pre-revenue) — but an empty income statement means the
    # data fetch failed (rate limit, delisting), which is not the same claim.
    rev_latest, rev_prior = _get_revenue_trend(financials)
    if not financials.get("income_stmt"):
        result.flags.append("No financial data available (fetch failed or delisted)")
    elif rev_latest is None or rev_latest <= 0:
        result.flags.append("No revenue (pre-revenue company)")

    # 2. Revenue declining >50% YoY
    if rev_latest is not None and rev_prior is not None and rev_prior > 0:
        rev_decline = (rev_latest - rev_prior) / rev_prior
        if rev_decline < -0.50:
            result.flags.append(f"Revenue declined {rev_decline:.0%} YoY (>50% threshold)")

    # 3. Negative gross margin
    gm = _get_gross_margin(financials)
    if gm is not None and gm < 0:
        result.flags.append(f"Negative gross margin ({gm:.1%})")

    # 4. Cash runway <3 months
    runway = _get_cash_runway_months(financials)
    if runway is not None and runway < 3:
        result.flags.append(f"Cash runway ~{runway:.1f} months (<3 month threshold)")

    # 5. Going-concern audit opinion (EDGAR full-text search)
    has_going_concern = search_filing_text(ticker, month, "going concern", "10-K")
    if not has_going_concern:
        has_going_concern = search_filing_text(ticker, month, "going concern", "10-Q")
    if has_going_concern:
        result.flags.append("Going-concern opinion in recent 10-K/10-Q")

    # 6. Chapter 7 bankruptcy (8-K)
    has_ch7 = search_filing_text(ticker, month, "chapter 7", "8-K")
    if has_ch7:
        result.flags.append("Chapter 7 bankruptcy filing detected")

    # 7. Active SEC enforcement (litigation release)
    has_enforcement = search_filing_text(ticker, month, "securities fraud", "8-K")
    if has_enforcement:
        result.flags.append("Potential SEC enforcement action (securities fraud in 8-K)")

    # 8 & 9. Backtest-derived "avoid the worst" floors (liquidity, decline depth)
    tech = get_technicals(ticker, month)
    dvol = tech.get("median_dollar_vol_60d")
    if dvol is not None and dvol < MIN_DOLLAR_VOL:
        result.flags.append(f"Illiquid: ${dvol/1e6:.2f}M/day median volume (<$1M floor — worst backtest bucket)")

    decline = tech.get("decline_52w_pct")
    if decline is not None and decline <= MAX_DECLINE_PCT:
        result.flags.append(f"Deepest-decile decline ({decline:.0f}% 52-wk) — down-90%+ names rarely recover")

    if result.flags:
        result.passed = False

    return result


def run_disqualifier(tickers: list[str], month: str, delay: float = 0.5) -> tuple[list[str], list[DisqualResult]]:
    """
    Run all checks. Returns (passing_tickers, all_results).
    """
    passing = []
    all_results = []
    for ticker in tickers:
        print(f"[disqualifier] Checking {ticker}...")
        r = check(ticker, month)
        all_results.append(r)
        if r.passed:
            passing.append(ticker)
        else:
            print(f"[disqualifier] DISQUALIFIED {ticker}: {'; '.join(r.flags)}")
        time.sleep(delay)

    print(f"[disqualifier] {len(passing)}/{len(tickers)} passed")
    return passing, all_results
