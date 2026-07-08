"""Render the backtest summary as a self-contained HTML report with inline SVG charts."""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

DOCS_DIR = Path(__file__).parent.parent / "docs"

GREEN = "#16a34a"
RED = "#dc2626"
SLATE = "#64748b"
BLUE = "#2563eb"


def _pct(x: float, plus: bool = True) -> str:
    if x is None:
        return "—"
    s = f"{x:+.1%}" if plus else f"{x:.1%}"
    return s


def _bar_dist(dist: list[dict]) -> str:
    """Horizontal histogram of 12m return buckets."""
    if not dist:
        return ""
    maxn = max(d["n"] for d in dist) or 1
    rows = []
    row_h, gap, label_w, bar_w = 26, 6, 130, 520
    total_h = len(dist) * (row_h + gap)
    for i, d in enumerate(dist):
        y = i * (row_h + gap)
        w = (d["n"] / maxn) * bar_w
        # green for positive buckets, red for negative, gray at zero crossing
        neg = d["label"].startswith(("≤", "-")) and "0 to" not in d["label"]
        color = RED if neg else GREEN
        rows.append(
            f'<text x="{label_w - 8}" y="{y + row_h/2 + 4}" text-anchor="end" '
            f'font-size="12" fill="#475569">{html.escape(d["label"])}</text>'
            f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{row_h}" rx="3" fill="{color}" opacity="0.82"/>'
            f'<text x="{label_w + w + 6:.1f}" y="{y + row_h/2 + 4}" font-size="11.5" '
            f'fill="#334155" font-weight="600">{d["n"]}</text>'
        )
    return (f'<svg viewBox="0 0 {label_w + bar_w + 60} {total_h}" width="100%" '
            f'style="max-width:720px" role="img">{"".join(rows)}</svg>')


def _grouped_year_chart(by_year: list[dict]) -> str:
    """Grouped bars: strategy median vs SPY median 12m return, per year."""
    if not by_year:
        return ""
    W, H = 760, 300
    pad_l, pad_b, pad_t = 44, 46, 20
    plot_w, plot_h = W - pad_l - 12, H - pad_b - pad_t
    vals = [v for d in by_year for v in (d["median"], d["spy_median"])]
    vmax = max(0.5, max(vals)); vmin = min(-0.5, min(vals))
    span = vmax - vmin

    def y_of(v):
        return pad_t + (vmax - v) / span * plot_h

    zero_y = y_of(0)
    n = len(by_year)
    group_w = plot_w / n
    bw = min(26, group_w / 2.6)
    parts = [f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{W-12}" y2="{zero_y:.1f}" stroke="#cbd5e1" stroke-width="1"/>']
    # gridlines / y labels
    for gv in (vmax, (vmax+vmin)/2, vmin):
        gy = y_of(gv)
        parts.append(f'<text x="{pad_l-8}" y="{gy+4:.1f}" text-anchor="end" font-size="10.5" fill="#94a3b8">{gv:+.0%}</text>')
    for i, d in enumerate(by_year):
        cx = pad_l + i * group_w + group_w / 2
        for j, (val, color) in enumerate([(d["median"], BLUE), (d["spy_median"], SLATE)]):
            x = cx - bw + j * bw
            yv = y_of(val)
            top = min(yv, zero_y); h = abs(yv - zero_y)
            parts.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw-2:.1f}" height="{h:.1f}" rx="2" fill="{color}" opacity="0.9"/>')
        parts.append(f'<text x="{cx:.1f}" y="{H-pad_b+16}" text-anchor="middle" font-size="10.5" fill="#64748b">{str(d["year"])[2:]}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{H-pad_b+30}" text-anchor="middle" font-size="9" fill="#cbd5e1">n={d["n"]}</text>')
    legend = (f'<rect x="{pad_l}" y="2" width="11" height="11" rx="2" fill="{BLUE}"/>'
              f'<text x="{pad_l+16}" y="11" font-size="11" fill="#475569">Strategy median</text>'
              f'<rect x="{pad_l+130}" y="2" width="11" height="11" rx="2" fill="{SLATE}"/>'
              f'<text x="{pad_l+146}" y="11" font-size="11" fill="#475569">SPY median</text>')
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:760px" role="img">'
            f'{legend}{"".join(parts)}</svg>')


def _feature_table(feat: dict) -> str:
    """One characteristic → a table of buckets with a beat-SPY bar (0–50% scale)."""
    rows = ""
    for b in feat["buckets"]:
        bar_w = min(1.0, b["pct_beat_spy"] / 0.50) * 120
        med_color = GREEN if b["median"] >= 0 else RED
        rows += f"""
        <tr>
          <td>{html.escape(b['label'])}</td>
          <td style="text-align:right;color:#94a3b8">{b['n']:,}</td>
          <td style="text-align:right;color:{med_color};font-weight:600">{b['median']:+.0%}</td>
          <td style="text-align:right">{b['pct_doubled']:.0%}</td>
          <td><div style="display:flex;align-items:center;gap:6px">
            <div style="height:9px;width:{bar_w:.0f}px;background:{BLUE};border-radius:2px;opacity:.85"></div>
            <span style="font-size:0.8rem;color:#475569">{b['pct_beat_spy']:.0%}</span></div></td>
        </tr>"""
    return f"""
    <div style="margin-bottom:18px">
      <div style="font-weight:700;font-size:0.9rem;color:#334155;margin-bottom:6px">{html.escape(feat['name'])}</div>
      <table class="data" style="font-size:0.82rem">
        <thead><tr><th>Bucket</th><th style="text-align:right">n</th><th style="text-align:right">Median 12m</th><th style="text-align:right">Doubled</th><th>Beat SPY</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""


def _winners_section(wn: dict) -> str:
    if not wn:
        return ""
    # top rebounders table
    tr = ""
    for r in wn["top_rebounders"][:12]:
        tr += (f'<tr><td style="font-weight:700">{html.escape(r["ticker"])}</td>'
               f'<td style="color:#64748b">{r["month"]}</td>'
               f'<td style="text-align:right;color:{RED}">{r["perf52w"]:+.0%}</td>'
               f'<td style="text-align:right;color:{GREEN};font-weight:700">+{r["ret_12m"]*100:,.0f}%</td></tr>')
    n2020 = sum(1 for r in wn["top_rebounders"] if r["month"].startswith("2020"))
    from collections import Counter
    common = Counter(r["ticker"] for r in wn["top_rebounders"]).most_common(1)[0]

    feat_html = "".join(_feature_table(f) for f in wn["features"])
    stab = wn["stabilization"]

    # Pull the headline separators by feature name (order-independent).
    by_name = {f["name"]: f["buckets"] for f in wn["features"]}
    liq = by_name["Liquidity (60-day median $ volume)"]
    depth = by_name["Depth of decline at entry"]
    capf = by_name["Estimated market cap at entry"]
    liq_hi, liq_lo = liq[-1]["pct_beat_spy"], liq[0]["pct_beat_spy"]
    depth_shallow, depth_deep = depth[0]["pct_beat_spy"], depth[-1]["pct_beat_spy"]
    cap_small, cap_big = capf[0]["pct_beat_spy"], capf[-1]["pct_beat_spy"]

    return f"""
  <div class="section-title">Were there winners? Yes — but few, and clustered</div>
  <div class="section-sub">A "winner" here is a position that actually rebounded, not just one that fell less.</div>
  <div class="stats" style="margin:0 0 16px;display:flex;gap:16px;flex-wrap:wrap">
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 18px">
      <div style="font-size:1.4rem;font-weight:800;color:{GREEN}">{wn['n_doubled']:,}</div>
      <div style="font-size:0.72rem;color:#166534;text-transform:uppercase">Doubled in 12m ({wn['pct_doubled']:.1%})</div></div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 18px">
      <div style="font-size:1.4rem;font-weight:800;color:{GREEN}">{wn['pct_up50']:.1%}</div>
      <div style="font-size:0.72rem;color:#166534;text-transform:uppercase">Gained ≥50%</div></div>
    <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:12px 18px">
      <div style="font-size:1.4rem;font-weight:800;color:#334155">+{wn['best_ret']*100:,.0f}%</div>
      <div style="font-size:0.72rem;color:#64748b;text-transform:uppercase">Best single outcome</div></div>
  </div>
  <div class="card">
    <table class="data">
      <thead><tr><th>Ticker</th><th>Entry</th><th style="text-align:right">Down (52w)</th><th style="text-align:right">12m return</th></tr></thead>
      <tbody>{tr}</tbody>
    </table>
    <p style="font-size:0.82rem;color:#64748b;margin-top:10px">
      The tail is real but <strong>concentrated</strong>: {n2020} of the top 12 rebounds began in 2020 (the COVID
      crash-and-snap-back), and {html.escape(common[0])} alone appears {common[1]} times. A handful of biotech and
      crypto-adjacent names in one regime drive most of the upside — not a repeatable, diversified edge.</p>
  </div>

  <div class="section-title">What characterized the winners?</div>
  <div class="section-sub">Only features knowable <em>at entry</em> (price/volume). Bar = share of positions that beat SPY over 12 months.</div>
  <div class="card">{feat_html}</div>

  <div class="verdict warn">
    <strong>The pattern is consistent — but not enough to flip the strategy.</strong> Survivors clustered where you'd
    expect quality to hide: <strong>liquidity</strong> (&gt;$100M/day traded beat SPY {liq_hi:.0%} of the time vs
    {liq_lo:.0%} for the illiquid microcaps), <strong>shallower declines</strong>
    (down 75–80% beat SPY {depth_shallow:.0%} vs {depth_deep:.0%} for the down-90%+ wreckage), and
    <strong>smaller-but-not-tiny caps</strong> ($300M–1B beat SPY {cap_small:.0%} vs {cap_big:.0%} for &gt;$5B). Waiting for the price to
    reclaim its 200-day average helped too, but only {stab['stabilizing']['n']:,} entries ever qualified. Even the
    best-characterised subgroup still beat the index less than half the time — the takeaway is a <em>filter to avoid the
    worst</em> (illiquid, down-90%+, high-vol names), not a recipe that turns the screen into a winner.</div>
"""


def _edgar_section(ed: dict) -> str:
    """Point-in-time fundamental-scorer replay: does fundamental quality separate winners?"""
    if not ed:
        return ""
    q = ed["quintiles"]
    qrows = ""
    for b in q:
        bar_w = min(1.0, b["pct_beat_spy"] / 0.50) * 120
        mc = GREEN if b["median"] >= 0 else RED
        qrows += f"""
        <tr><td style="font-weight:600">{html.escape(b['label'])}</td>
          <td style="text-align:right;color:#94a3b8">{b['n']:,}</td>
          <td style="text-align:right;color:{mc};font-weight:600">{b['median']:+.0%}</td>
          <td style="text-align:right">{b['pct_doubled']:.0%}</td>
          <td><div style="display:flex;align-items:center;gap:6px">
            <div style="height:9px;width:{bar_w:.0f}px;background:{BLUE};border-radius:2px;opacity:.85"></div>
            <span style="font-size:0.8rem;color:#475569">{b['pct_beat_spy']:.0%}</span></div></td></tr>"""

    dq = ed["disqualifier"]
    passed, flagged = dq["passed"], dq["disqualified"]
    corr = ed["score_corr"]
    top = q[-1]["median"] if q else 0
    bot = q[0]["median"] if q else 0
    spread = top - bot
    # honest verdict on whether the scorer added signal
    if spread > 0.10 and corr > 0.05:
        verdict = (f"<strong>The fundamental scoring carried real signal.</strong> The best-scored quintile "
                   f"(median {top:+.0%}) beat the worst ({bot:+.0%}) by {spread*100:.0f} points, and the "
                   f"disqualifier's surviving names ({passed['median']:+.0%}) meaningfully outperformed the "
                   f"flagged ones ({flagged['median']:+.0%}). Fundamentals didn't make the raw screen a winner, "
                   f"but they ranked in the right direction — the pipeline adds value as a quality filter.")
        cls = ""
    elif spread > 0.05:
        verdict = (f"<strong>Weak but directionally-correct signal.</strong> Best-scored quintile {top:+.0%} vs "
                   f"worst {bot:+.0%} (spread {spread*100:.0f}pts); score/return correlation {corr:+.2f}. The "
                   f"fundamental scorer nudges toward better names but the effect is small against the strategy's "
                   f"dominant losses.")
        cls = "warn"
    else:
        verdict = (f"<strong>Fundamentals barely separated winners from losers.</strong> Best-scored quintile "
                   f"{top:+.0%} vs worst {bot:+.0%} (spread {spread*100:.0f}pts, correlation {corr:+.2f}), and "
                   f"the disqualifier's passed set ({passed['median']:+.0%}) was no better than the flagged set "
                   f"({flagged['median']:+.0%}). On this evidence the fundamental scoring does not add durable "
                   f"alpha — the outcome is driven by price/liquidity factors, not the balance sheet.")
        cls = "warn"

    return f"""
  <div class="section-title">Does the fundamental scoring actually add alpha?</div>
  <div class="section-sub">Point-in-time replay: each cohort's Survivability, gross-margin and valuation sub-scores
    rebuilt from SEC EDGAR XBRL as filed on/before the entry date (no lookahead). {ed['n_positions']:,} positions
    scored ({ed['coverage']:.0%} coverage). Bar = share that beat SPY.</div>
  <div class="card">
    <div style="font-weight:700;font-size:0.9rem;color:#334155;margin-bottom:6px">Forward return by fundamental-score quintile</div>
    <table class="data" style="font-size:0.84rem">
      <thead><tr><th>Fundamental score</th><th style="text-align:right">n</th><th style="text-align:right">Median 12m</th><th style="text-align:right">Doubled</th><th>Beat SPY</th></tr></thead>
      <tbody>{qrows}</tbody>
    </table>
    <div style="font-weight:700;font-size:0.9rem;color:#334155;margin:18px 0 6px">Disqualifier replay — would-be passed vs flagged</div>
    <table class="data" style="font-size:0.84rem">
      <thead><tr><th></th><th style="text-align:right">n</th><th style="text-align:right">Median 12m</th><th style="text-align:right">Beat SPY</th><th style="text-align:right">Doubled</th></tr></thead>
      <tbody>
        <tr><td style="font-weight:600;color:{GREEN}">Passed fundamental checks</td><td style="text-align:right">{passed['n']:,}</td><td style="text-align:right">{passed['median']:+.0%}</td><td style="text-align:right">{passed['pct_beat_spy']:.0%}</td><td style="text-align:right">{passed['pct_doubled']:.0%}</td></tr>
        <tr><td style="font-weight:600;color:{RED}">Flagged (no rev / crash / neg margin / &lt;3mo cash)</td><td style="text-align:right">{flagged['n']:,}</td><td style="text-align:right">{flagged['median']:+.0%}</td><td style="text-align:right">{flagged['pct_beat_spy']:.0%}</td><td style="text-align:right">{flagged['pct_doubled']:.0%}</td></tr>
      </tbody>
    </table>
  </div>
  <div class="verdict {cls}">{verdict}
    <br><span style="font-size:0.82rem;opacity:0.85">Covers the 53 of 100 points that are fundamentally reconstructable
    (Survivability, gross margin, P/S, P/B). The Catalyst category — short interest, insider buys, analyst moves —
    needs point-in-time FINRA/market feeds and is not replayed here.</span></div>
"""


def render(summary: dict) -> Path:
    w = summary["windows"]
    generated = datetime.now().strftime("%B %d, %Y")
    drop = summary["drop_pct"]; cap = summary["min_cap_mm"]

    # headline uses the 12m window
    h12 = w.get("12m", {})

    def stat_card(label, value, sub=""):
        return (f'<div class="stat"><div class="n">{value}</div>'
                f'<div class="l">{label}</div>'
                + (f'<div class="sub">{sub}</div>' if sub else "") + '</div>')

    window_rows = ""
    for label in ("3m", "6m", "12m"):
        s = w.get(label)
        if not s:
            continue
        edge = s["median"] - s["spy_median"]
        edge_color = GREEN if edge >= 0 else RED
        window_rows += f"""
        <tr>
          <td style="font-weight:700">{label}</td>
          <td>{s['n']:,}</td>
          <td>{_pct(s['median'])}</td>
          <td style="color:{SLATE}">{_pct(s['spy_median'])}</td>
          <td style="color:{edge_color};font-weight:600">{_pct(edge)}</td>
          <td>{_pct(s['pct_beat_spy'], plus=False)}</td>
          <td>{_pct(s['pct_pos'], plus=False)}</td>
          <td style="color:{RED}">{_pct(s['pct_lost_half'], plus=False)}</td>
          <td style="color:{GREEN}">{_pct(s['pct_doubled'], plus=False)}</td>
          <td style="color:{RED}">{_pct(s['median_pessimistic'])}</td>
        </tr>"""

    dist_svg = _bar_dist(summary.get("dist_12m", []))
    year_svg = _grouped_year_chart(summary.get("by_year", []))
    winners_html = _winners_section(summary.get("winners", {}))
    edgar_html = _edgar_section(summary.get("edgar", {}))

    n_delisted = h12.get("n_delisted", 0)
    n_positions = h12.get("n", 0)

    verdict = _verdict(w)

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PhoenixPicks — Strategy Backtest</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #1a1a1a; }}
  .header {{ background: #0f172a; color: white; padding: 30px 40px; }}
  .header h1 {{ font-size: 1.7rem; font-weight: 800; }}
  .header .meta {{ color: #94a3b8; font-size: 0.85rem; margin-top: 6px; }}
  .stats {{ display: flex; gap: 20px; margin-top: 20px; flex-wrap: wrap; }}
  .stat {{ background: rgba(255,255,255,0.08); border-radius: 8px; padding: 12px 18px; min-width: 130px; }}
  .stat .n {{ font-size: 1.5rem; font-weight: 800; }}
  .stat .l {{ font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }}
  .stat .sub {{ font-size: 0.72rem; color: #cbd5e1; margin-top: 4px; }}
  .container {{ max-width: 1000px; margin: 0 auto; padding: 32px 24px 60px; }}
  .section-title {{ font-size: 1.1rem; font-weight: 700; color: #374151; margin: 40px 0 8px; }}
  .section-sub {{ color: #64748b; font-size: 0.88rem; margin-bottom: 18px; }}
  .card {{ background: white; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); padding: 24px; margin-bottom: 8px; overflow-x: auto; }}
  table.data {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; }}
  table.data th {{ background: #f1f5f9; text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; color: #64748b; padding: 9px 10px; white-space: nowrap; }}
  table.data td {{ padding: 10px; border-top: 1px solid #e5e7eb; white-space: nowrap; }}
  .verdict {{ background: #ecfdf5; border-left: 4px solid {GREEN}; border-radius: 8px; padding: 18px 22px; font-size: 0.95rem; line-height: 1.6; color: #14532d; }}
  .verdict.warn {{ background: #fef2f2; border-color: {RED}; color: #7f1d1d; }}
  .caveat {{ background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px; padding: 18px 22px; font-size: 0.86rem; line-height: 1.65; color: #713f12; }}
  .caveat h3 {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; color: #92400e; }}
  .caveat ul {{ margin: 0 0 0 18px; }}
  .caveat li {{ margin-bottom: 6px; }}
  footer {{ text-align: center; color: #94a3b8; font-size: 0.75rem; padding: 32px; }}
  footer a {{ color: #94a3b8; }}
</style>
</head>
<body>
<div class="header">
  <h1>🔥 PhoenixPicks — Does It Actually Work?</h1>
  <div class="meta">Generated {generated} &nbsp;·&nbsp; US stocks down ≥{abs(drop):.0f}% (52-week), est. market cap &gt;${cap}M &nbsp;·&nbsp; {summary['n_cohorts']} monthly cohorts</div>
  <div class="stats">
    {stat_card("Cohort entries", f"{summary['n_memberships']:,}", f"{summary['n_unique']:,} unique tickers")}
    {stat_card("Avg / month", f"{summary['avg_cohort']:.0f}", "stocks meeting screen")}
    {stat_card("12m median", _pct(h12.get('median')), f"vs SPY {_pct(h12.get('spy_median'))}")}
    {stat_card("Beat SPY (12m)", _pct(h12.get('pct_beat_spy'), plus=False), "of positions")}
    {stat_card("Lost ≥50% (12m)", _pct(h12.get('pct_lost_half'), plus=False), "of positions")}
  </div>
</div>

<div class="container">

  <div class="verdict {'warn' if not verdict['positive'] else ''}">{verdict['text']}</div>

  <div class="section-title">Forward returns by holding period</div>
  <div class="section-sub">Each row: buy every stock in every monthly cohort, hold for the period, equal-weighted. "Delisted-adjusted" replaces any position that stopped trading mid-window with −100%.</div>
  <div class="card">
    <table class="data">
      <thead><tr>
        <th>Hold</th><th>Positions</th><th>Median</th><th>SPY median</th><th>Edge</th>
        <th>Beat SPY</th><th>Positive</th><th>Lost ≥50%</th><th>Doubled+</th><th>Delisted-adj median</th>
      </tr></thead>
      <tbody>{window_rows}</tbody>
    </table>
  </div>

  <div class="section-title">Distribution of 12-month outcomes</div>
  <div class="section-sub">How the individual positions actually landed — the shape matters more than the average.</div>
  <div class="card">{dist_svg}</div>

  <div class="section-title">Strategy vs SPY, by cohort year (12-month median)</div>
  <div class="section-sub">Does the edge persist across regimes, or is it one or two lucky years?</div>
  <div class="card">{year_svg}</div>

  {winners_html}

  {edgar_html}

  <div class="section-title">Read this before trusting the numbers</div>
  <div class="caveat">
    <h3>Survivorship bias — the big one</h3>
    <p>The universe is <strong>today's listed US common stocks</strong>. A company that fell 75%+ and later went bankrupt or was taken under is largely <em>absent</em> from the data, so the surviving winners are over-represented. In this run the delisted-adjustment caught <strong>{n_delisted} mid-window delistings out of {n_positions:,} positions</strong> — effectively zero — which is itself the tell: the dataset is built from names that are <em>still listed today</em>, so the losers that went to zero were never in it to begin with. That makes the "Delisted-adjusted" column nearly identical to the raw one and means <strong>even these grim numbers are an optimistic ceiling, not a floor.</strong></p>
    <h3 style="margin-top:14px">Other limitations</h3>
    <ul>
      <li>Market cap at each historical date is estimated as <em>current cap × (price then ÷ price now)</em>, assuming constant share count. Buybacks and dilution distort this; delisted names fall back to a $2M median dollar-volume liquidity filter.</li>
      <li>Prices are split/dividend-adjusted (Yahoo auto-adjust). No transaction costs, slippage, or bid-ask spread — real small-cap execution is worse.</li>
      <li>Equal-weighted, no position sizing or stop-losses.</li>
      <li><strong>The 100-point fundamental scorer is not replayed.</strong> It reads yfinance <code>.info</code> (short interest, analyst ratings, P/S, P/B) and financial statements, which are only served as <em>today's</em> snapshot — scoring a 2019 cohort with 2026 fundamentals would be lookahead bias. The "what characterized the winners" analysis therefore uses only <em>point-in-time price/volume features</em> that were genuinely observable on each entry date. A faithful fundamental replay is possible but needs point-in-time data (EDGAR XBRL for statements, FINRA for short interest) — a larger build.</li>
      <li>Overlapping cohorts share market regimes, so monthly results are not independent; treat per-year medians as the honest unit.</li>
    </ul>
  </div>

  <footer>PhoenixPicks backtest · price data via Yahoo Finance · <a href="index.html">back to reports</a></footer>
</div>
</body>
</html>"""

    DOCS_DIR.mkdir(exist_ok=True)
    out = DOCS_DIR / "backtest.html"
    out.write_text(html_out, encoding="utf-8")
    return out


def _verdict(w: dict) -> dict:
    """One-paragraph plain-English read of the 12m result."""
    s = w.get("12m")
    if not s:
        return {"positive": False, "text": "Not enough forward data to judge."}
    edge = s["median"] - s["spy_median"]
    beat = s["pct_beat_spy"]
    pess = s["median_pessimistic"]
    positive = edge > 0 and pess > s["spy_median"] * 0.5
    if edge > 0 and pess >= 0:
        txt = (f"<strong>Mixed-to-positive.</strong> Over 12 months the median beaten-down stock returned "
               f"{s['median']:+.1%} vs SPY's {s['spy_median']:+.1%} — an edge of {edge:+.1%}, with "
               f"{beat:.0%} of positions beating the index. Even after marking delisted names to −100%, the "
               f"median stays at {pess:+.1%}. The catch: {s['pct_lost_half']:.0%} of positions still lost half "
               f"their value, so the average hides a wide, risky spread — and survivorship bias inflates the upside.")
    elif edge > 0:
        txt = (f"<strong>Fragile edge.</strong> The raw median ({s['median']:+.1%}) beats SPY ({s['spy_median']:+.1%}), "
               f"but once delisted positions are marked to −100% the median collapses to {pess:+.1%}. The apparent "
               f"edge is largely survivorship bias: the winners are visible, many of the losers have vanished from the data.")
    else:
        txt = (f"<strong>No edge.</strong> The median beaten-down stock returned {s['median']:+.1%} over 12 months vs "
               f"SPY's {s['spy_median']:+.1%}. Buying stocks simply because they fell 75% did not, on this data, beat "
               f"just holding the index — and that's <em>before</em> accounting for the delisted losers missing from the sample.")
    return {"positive": positive, "text": txt}
