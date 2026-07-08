"""
Generate a curated thematic basket (order list + Fidelity-ready CSV + page).

Usage:
  python theme_basket.py                                  # vibecoding, $500
  python theme_basket.py --theme vibecoding-2nd-order --budget 300
"""
import argparse
import sys

from src.themes import THEMES, build_theme_basket, write_theme_csv, render_theme_page

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="vibecoding-2nd-order", choices=list(THEMES))
    ap.add_argument("--budget", type=float, default=500.0)
    args = ap.parse_args()

    orders = build_theme_basket(args.theme, args.budget)
    csv_path = write_theme_csv(args.theme, orders, args.budget)
    page = render_theme_page(args.theme, orders, args.budget)

    print(f"Theme: {THEMES[args.theme]['title']}  (${args.budget:.0f}/mo)")
    for o in orders:
        print(f"  {o.ticker:6s} {o.sleeve:26s} ${o.dollars:7.2f}  ({o.weight*100:4.1f}%)")
    print(f"  {'TOTAL':6s} {'':26s} ${sum(o.dollars for o in orders):7.2f}")
    print(f"CSV:  {csv_path}")
    print(f"Page: {page}")


if __name__ == "__main__":
    main()
