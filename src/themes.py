"""
Curated thematic baskets — a personal conviction sleeve, separate from the
PhoenixPicks screen. Produces the same artifact set (an order list + a
Fidelity-ready CSV) plus a standalone page, so a themed bet is sized and
systematic rather than impulsive.

NOT investment advice. Equal-weight within each sleeve; sleeve weights encode
the conviction tilt. Names are chosen for thesis exposure, not valuation.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DOCS_DIR = Path(__file__).parent.parent / "docs"


THEMES = {
    "vibecoding-2nd-order": {
        "title": "Vibecoding — Second-Order Basket",
        "favicon": "🛠️",
        "thesis": ("When the cost of writing code collapses, value migrates to code's "
                   "complements — the things you need more of once software is nearly free "
                   "to build: compute to run it, security to protect the flood of it, and "
                   "power to fuel it. This basket buys the complements, not the coding tools."),
        "sleeves": [
            {"name": "Consumption infrastructure", "weight": 0.40,
             "rationale": "Cheap coding means more software gets built and run. Seat-based "
                          "SaaS gets squeezed; usage-metered infra thrives on the volume.",
             "holdings": [
                 {"ticker": "NET", "name": "Cloudflare", "note": "edge + inference, usage-based"},
                 {"ticker": "DDOG", "name": "Datadog", "note": "observability scales with # of apps"},
                 {"ticker": "MDB", "name": "MongoDB", "note": "consumption database for new apps"},
                 {"ticker": "SNOW", "name": "Snowflake", "note": "data platform, usage-metered"},
             ]},
            {"name": "Security & verification", "weight": 0.30,
             "rationale": "AI writes the code faster than humans review it — more bugs, more "
                          "vulnerabilities, a bigger attack surface. Someone has to secure it.",
             "holdings": [
                 {"ticker": "CRWD", "name": "CrowdStrike", "note": "endpoint + runtime security"},
                 {"ticker": "PANW", "name": "Palo Alto Networks", "note": "platform security"},
                 {"ticker": "ZS", "name": "Zscaler", "note": "zero-trust access"},
             ]},
            {"name": "Power & cooling", "weight": 0.30,
             "rationale": "The compounding physical bottleneck: more software everywhere → more "
                          "datacenters → more electricity and heat to move.",
             "holdings": [
                 {"ticker": "CEG", "name": "Constellation Energy", "note": "datacenter power"},
                 {"ticker": "VST", "name": "Vistra", "note": "independent power producer"},
                 {"ticker": "VRT", "name": "Vertiv", "note": "datacenter cooling & power distribution"},
             ]},
        ],
        # No allocation — track whether the disruption side of the thesis is playing out.
        "watchlist": [
            {"ticker": "TEAM", "name": "Atlassian", "note": "seat-based dev SaaS — disruption risk"},
            {"ticker": "GTLB", "name": "GitLab", "note": "seat-based, though has AI (Duo)"},
            {"ticker": "ACN", "name": "Accenture", "note": "IT services / labor arbitrage"},
            {"ticker": "INFY", "name": "Infosys", "note": "offshore code labor"},
            {"ticker": "CTSH", "name": "Cognizant", "note": "IT services"},
        ],
    }
}


@dataclass
class ThemeOrder:
    ticker: str
    name: str
    sleeve: str
    note: str
    weight: float
    dollars: float


def build_theme_basket(theme_key: str, budget: float = 500.0) -> list[ThemeOrder]:
    theme = THEMES[theme_key]
    orders: list[ThemeOrder] = []
    for sleeve in theme["sleeves"]:
        holdings = sleeve["holdings"]
        per_name_w = sleeve["weight"] / len(holdings)
        for h in holdings:
            orders.append(ThemeOrder(
                ticker=h["ticker"], name=h["name"], sleeve=sleeve["name"],
                note=h["note"], weight=per_name_w, dollars=round(budget * per_name_w, 2)))
    # Fix any rounding drift onto the first holding so the basket sums exactly.
    drift = round(budget - sum(o.dollars for o in orders), 2)
    if orders:
        orders[0].dollars = round(orders[0].dollars + drift, 2)
    return orders


def write_theme_csv(theme_key: str, orders: list[ThemeOrder], budget: float) -> Path:
    (DOCS_DIR / "baskets").mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / "baskets" / f"{theme_key}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Ticker", "Company", "Sleeve", "Amount_USD", "Percent"])
        for o in orders:
            w.writerow([o.ticker, o.name, o.sleeve, f"{o.dollars:.2f}", f"{o.weight:.4f}"])
    return path


def render_theme_page(theme_key: str, orders: list[ThemeOrder], budget: float) -> Path:
    import html as _h
    theme = THEMES[theme_key]
    by_sleeve = {s["name"]: s for s in theme["sleeves"]}

    sleeve_html = ""
    for sname, s in by_sleeve.items():
        sos = [o for o in orders if o.sleeve == sname]
        subtotal = sum(o.dollars for o in sos)
        rows = ""
        for o in sos:
            rows += f"""
            <tr>
              <td style="font-weight:700">{o.ticker}</td>
              <td>{_h.escape(o.name)}</td>
              <td style="color:#64748b;font-size:0.82rem">{_h.escape(o.note)}</td>
              <td style="text-align:right;font-weight:700">${o.dollars:.2f}</td>
              <td style="text-align:right;color:#64748b">{o.weight*100:.1f}%</td>
            </tr>"""
        sleeve_html += f"""
        <div class="sleeve">
          <div class="sleeve-head">
            <span class="sleeve-name">{_h.escape(sname)}</span>
            <span class="sleeve-sub">${subtotal:.0f} · {s['weight']*100:.0f}%</span>
          </div>
          <p class="rationale">{_h.escape(s['rationale'])}</p>
          <table class="t">
            <thead><tr><th>Ticker</th><th>Company</th><th>Why</th><th style="text-align:right">Buy</th><th style="text-align:right">Wt</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    watch_rows = ""
    for wl in theme["watchlist"]:
        watch_rows += (f'<tr><td style="font-weight:700">{wl["ticker"]}</td>'
                       f'<td>{_h.escape(wl["name"])}</td>'
                       f'<td style="color:#64748b;font-size:0.82rem">{_h.escape(wl["note"])}</td></tr>')

    total = sum(o.dollars for o in orders)
    html_out = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_h.escape(theme['title'])}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:system-ui,-apple-system,sans-serif; background:#f8fafc; color:#1a1a1a; }}
  .header {{ background:#0f172a; color:#fff; padding:30px 40px; }}
  .header h1 {{ font-size:1.6rem; font-weight:800; }}
  .header .thesis {{ color:#cbd5e1; font-size:0.92rem; margin-top:10px; max-width:760px; line-height:1.55; }}
  .header .budget {{ color:#94a3b8; font-size:0.85rem; margin-top:12px; }}
  .container {{ max-width:940px; margin:0 auto; padding:28px 24px 60px; }}
  .sleeve {{ background:#fff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,.08); padding:20px 22px; margin-bottom:18px; overflow-x:auto; }}
  .sleeve-head {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .sleeve-name {{ font-weight:800; font-size:1.05rem; color:#0f172a; }}
  .sleeve-sub {{ font-weight:700; color:#2563eb; }}
  .rationale {{ color:#475569; font-size:0.86rem; margin:6px 0 12px; line-height:1.5; }}
  table.t {{ width:100%; border-collapse:collapse; font-size:0.88rem; }}
  table.t th {{ text-align:left; font-size:0.7rem; text-transform:uppercase; letter-spacing:.04em; color:#64748b; padding:8px 10px; background:#f1f5f9; }}
  table.t td {{ padding:10px; border-top:1px solid #e5e7eb; }}
  .total {{ display:flex; justify-content:space-between; align-items:center; background:#0f172a; color:#fff; border-radius:12px; padding:16px 22px; margin-bottom:22px; }}
  .total .n {{ font-size:1.5rem; font-weight:800; }}
  .how {{ background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:16px 20px; font-size:0.88rem; line-height:1.6; color:#1e3a8a; margin-bottom:18px; }}
  .watch {{ background:#fff; border-radius:12px; box-shadow:0 1px 4px rgba(0,0,0,.08); padding:20px 22px; margin-bottom:18px; }}
  .watch h2 {{ font-size:1rem; color:#334155; margin-bottom:4px; }}
  .watch p {{ color:#64748b; font-size:0.84rem; margin-bottom:12px; }}
  .caveat {{ background:#fffbeb; border:1px solid #fde68a; border-radius:10px; padding:16px 20px; font-size:0.84rem; line-height:1.6; color:#713f12; }}
  footer {{ text-align:center; color:#94a3b8; font-size:0.75rem; padding:28px; }}
  footer a {{ color:#94a3b8; }}
</style></head><body>
<div class="header">
  <h1>🛠️ {_h.escape(theme['title'])}</h1>
  <div class="thesis">{_h.escape(theme['thesis'])}</div>
  <div class="budget">Monthly dollar-cost-average budget: <strong>${budget:.0f}</strong> · {len(orders)} positions · equal-weight within each sleeve</div>
</div>
<div class="container">
  <div class="total"><span>Monthly basket total</span><span class="n">${total:.2f}</span></div>
  {sleeve_html}
  <div class="how">
    <strong>How to run it:</strong> download <a href="{theme_key}.csv">{theme_key}.csv</a> and either build a
    <strong>Fidelity Basket Portfolio</strong> from these symbols/dollar amounts (one-click monthly rebalance), or
    set a <strong>recurring monthly investment</strong> on each via Stocks by the Slice. Rerun the generator to
    resize to a different budget.
  </div>
  <div class="watch">
    <h2>Disruption watchlist — no allocation</h2>
    <p>The other side of the thesis: if vibecoding really wins, these get squeezed. Track them to see if it's playing out before committing more.</p>
    <table class="t"><thead><tr><th>Ticker</th><th>Company</th><th>Why it's at risk</th></tr></thead><tbody>{watch_rows}</tbody></table>
  </div>
  <div class="caveat">
    <strong>Not investment advice.</strong> This is a concentrated, single-theme conviction tilt — it has a fat left
    tail (the same lesson the PhoenixPicks backtest hammered). The power and security names have already run hard, so
    you may be paying up; a correct thesis still doesn't guarantee a good entry price. Size it as satellite money around
    an index core (e.g. your VTI), not the core itself. Names are picked for thesis exposure, not valuation.
  </div>
  <footer><a href="../index.html">← PhoenixPicks</a> · thesis basket · not advice</footer>
</div></body></html>"""
    (DOCS_DIR / "baskets").mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / "baskets" / f"{theme_key}.html"
    path.write_text(html_out, encoding="utf-8")
    return path
