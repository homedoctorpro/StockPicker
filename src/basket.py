"""
Turn the month's scored picks into a fixed-dollar buy basket.

Produces an order list (ticker + exact dollar amount) sized to a monthly
budget, plus a CSV you can hand to Fidelity's Basket Portfolios / Stocks by
the Slice. This module NEVER places a trade — it only produces the plan.

Selection policy (default "actionable"):
  - Include every pick rated Watchlist or better (total >= 55).
  - If fewer than `min_positions` qualify, top up with the next-highest-scored
    survivors so a lean month still yields a basket.
Weighting: equal-dollar across the selected names (Fidelity supports
fractional "Stocks by the Slice" orders down to $1).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .scorer import StockScore

DOCS_DIR = Path(__file__).parent.parent / "docs"
WATCHLIST_MIN = 55.0
FIDELITY_MIN_SLICE = 1.00  # Fidelity dollar-based fractional order minimum


@dataclass
class BasketOrder:
    ticker: str
    company: str
    dollars: float
    pct: float
    score: float
    action: str


def select_picks(scores: list[StockScore], policy: str = "actionable",
                 min_positions: int = 3, top_n: int = 5) -> list[StockScore]:
    ranked = sorted(scores, key=lambda s: s.total, reverse=True)
    if policy == "all":
        return ranked
    if policy == "top":
        return ranked[:top_n]
    # "actionable": Watchlist+ , topped up to min_positions if too few qualify
    picks = [s for s in ranked if s.total >= WATCHLIST_MIN]
    if len(picks) < min_positions:
        for s in ranked:
            if s not in picks:
                picks.append(s)
            if len(picks) >= min_positions:
                break
    return picks


def build_basket(scores: list[StockScore], budget: float = 500.0,
                 policy: str = "actionable", min_positions: int = 3,
                 top_n: int = 5) -> list[BasketOrder]:
    picks = select_picks(scores, policy, min_positions, top_n)
    if not picks:
        return []

    # Cap position count so each slice clears Fidelity's $1 minimum.
    max_positions = max(1, int(budget // FIDELITY_MIN_SLICE))
    if len(picks) > max_positions:
        picks = picks[:max_positions]

    n = len(picks)
    base = round(budget / n, 2)
    amounts = [base] * n
    # Push the rounding remainder onto the top-ranked pick so the basket
    # sums to the budget exactly (e.g. 500/3 -> 166.67, 166.67, 166.66).
    drift = round(budget - base * n, 2)
    amounts[0] = round(amounts[0] + drift, 2)

    orders = []
    for s, amt in zip(picks, amounts):
        orders.append(BasketOrder(
            ticker=s.ticker,
            company=s.company_name,
            dollars=amt,
            pct=amt / budget,
            score=s.total,
            action=s.action,
        ))
    return orders


def write_csv(orders: list[BasketOrder], month: str) -> Path | None:
    if not orders:
        return None
    (DOCS_DIR / "reports").mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / "reports" / f"{month}-basket.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Ticker", "Company", "Amount_USD", "Percent", "Score", "Action"])
        for o in orders:
            writer.writerow([o.ticker, o.company, f"{o.dollars:.2f}",
                             f"{o.pct:.4f}", f"{o.score:.1f}", o.action])
    return path
