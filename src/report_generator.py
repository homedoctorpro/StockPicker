"""Generate the monthly HTML report from scored stocks."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .scorer import StockScore

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
DOCS_DIR = Path(__file__).parent.parent / "docs"


def generate(
    scores: list[StockScore],
    month: str,
    num_screened: int,
    num_disqualified: int,
) -> Path:
    """Render HTML report and write to docs/reports/YYYY-MM.html. Returns path."""
    DOCS_DIR.mkdir(exist_ok=True)
    (DOCS_DIR / "reports").mkdir(exist_ok=True)

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    month_label = datetime.strptime(month, "%Y-%m").strftime("%B %Y")

    html = template.render(
        month=month,
        month_label=month_label,
        generated_at=generated_at,
        scores=scores,
        num_screened=num_screened,
        num_disqualified=num_disqualified,
        num_scored=len(scores),
        strong_buys=[s for s in scores if s.total >= 75],
        watchlist=[s for s in scores if 55 <= s.total < 75],
    )

    report_path = DOCS_DIR / "reports" / f"{month}.html"
    report_path.write_text(html, encoding="utf-8")
    print(f"[report] Written to {report_path}")

    _update_index(month, month_label)
    return report_path


def _update_index(new_month: str, new_month_label: str) -> None:
    index_path = DOCS_DIR / "index.html"

    existing_links = []
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        import re
        existing_links = re.findall(r'href="reports/([\d-]+)\.html"', content)

    all_months = sorted(set(existing_links + [new_month]), reverse=True)

    link_items = []
    for m in all_months:
        label = datetime.strptime(m, "%Y-%m").strftime("%B %Y")
        link_items.append(f'        <li><a href="reports/{m}.html">{label} Report</a></li>')

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>StockPicker — Bounce-Back Reports</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px; color: #1a1a1a; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 0.25rem; }}
    p {{ color: #666; margin-top: 0; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ margin: 12px 0; }}
    a {{ color: #0066cc; text-decoration: none; font-size: 1.1rem; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>Bounce-Back Candidates</h1>
  <p>Monthly analysis of US stocks down 75%+ that show recovery potential.</p>
  <ul>
{chr(10).join(link_items)}
  </ul>
</body>
</html>"""

    index_path.write_text(index_html, encoding="utf-8")
