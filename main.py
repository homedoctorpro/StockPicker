"""
StockPicker — Monthly Bounce-Back Candidate Report

Usage:
  python main.py                     # Run for current month
  python main.py --month 2026-04     # Run for specific month
  python main.py --fallback          # Use Russell 3000 fallback screener
  python main.py --tickers AAPL,TSLA # Score specific tickers (testing)
"""
import argparse
import sys
from datetime import datetime

from src.screener import get_candidates
from src.disqualifier import run_disqualifier
from src.scorer import score_all
from src.report_generator import generate
from src.basket import build_basket, write_csv


def main():
    parser = argparse.ArgumentParser(description="Monthly bounce-back stock report")
    parser.add_argument("--month", default=datetime.today().strftime("%Y-%m"),
                        help="Month to run (YYYY-MM). Defaults to current month.")
    parser.add_argument("--fallback", action="store_true",
                        help="Use Russell 3000 fallback screener instead of Finviz")
    parser.add_argument("--tickers", default="",
                        help="Comma-separated tickers to score directly (skips screening)")
    parser.add_argument("--min-cap", type=int, default=300,
                        help="Minimum market cap in $M (default: 300)")
    parser.add_argument("--budget", type=float, default=500.0,
                        help="Monthly buy-basket budget in $ (default: 500; 0 disables)")
    args = parser.parse_args()

    month = args.month
    print(f"\n{'='*60}")
    print(f"  StockPicker — {month}")
    print(f"{'='*60}\n")

    # Step 1: Screen
    if args.tickers:
        candidates = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        print(f"[main] Using provided tickers: {candidates}")
    else:
        print("[main] Step 1: Screening...")
        candidates = get_candidates(min_cap_mm=args.min_cap, use_fallback=args.fallback)

    if not candidates:
        print("[main] No candidates found. Exiting.")
        sys.exit(0)

    # A "down 75%+, >$300M cap" screen yields tens of names, not thousands.
    # If we got an implausible count the screen failed — fail loudly rather
    # than spend hours fetching data and publishing a garbage report.
    if not args.tickers and len(candidates) > 500:
        print(f"[main] ERROR: {len(candidates)} candidates is implausible for this "
              f"screen — screening failed. Aborting without generating a report.")
        sys.exit(1)

    # Step 2: Disqualify
    print(f"\n[main] Step 2: Disqualifying {len(candidates)} candidates...")
    passing, disq_results = run_disqualifier(candidates, month)
    num_disqualified = len(candidates) - len(passing)

    if not passing:
        print("[main] All candidates disqualified. Generating empty report.")
        report_path = generate([], month, len(candidates), num_disqualified)
        print(f"\n[main] Done — report: {report_path}")
        return

    # Step 3: Score
    print(f"\n[main] Step 3: Scoring {len(passing)} candidates...")
    scores = score_all(passing, month)

    # Step 4: Build monthly buy basket (plan only — never places a trade)
    basket = []
    if args.budget > 0 and scores:
        basket = build_basket(scores, budget=args.budget)
        csv_path = write_csv(basket, month)
        print(f"\n[main] Basket ({len(basket)} positions, ${args.budget:.0f}): {csv_path}")
        for o in basket:
            print(f"    {o.ticker:8s}  ${o.dollars:7.2f}  ({o.pct*100:4.1f}%)  {o.action}")

    # Step 5: Generate report
    print(f"\n[main] Step 5: Generating report...")
    report_path = generate(scores, month, len(candidates), num_disqualified,
                           basket=basket, budget=args.budget)

    # Summary
    print(f"\n{'='*60}")
    print(f"  Report: {report_path}")
    print(f"  Screened: {len(candidates)}  |  Disqualified: {num_disqualified}  |  Scored: {len(scores)}")
    strong = [s for s in scores if s.total >= 75]
    watch = [s for s in scores if 55 <= s.total < 75]
    print(f"  Strong Buy: {len(strong)}  |  Watchlist: {len(watch)}")
    if strong:
        print(f"\n  TOP PICKS:")
        for s in strong[:5]:
            print(f"    {s.ticker:8s}  {s.total:5.1f}/100  {s.action}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
