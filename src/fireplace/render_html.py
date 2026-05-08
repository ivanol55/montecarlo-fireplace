"""Self-contained HTML report (Plotly) for one or more scenarios."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from .case import ScenarioReport
from .report import Aggregates, aggregate, wealth_percentiles, withdrawal_breakdown


PALETTE = ["#227093", "#218c74", "#fb5053", "#7d5fff", "#fac532", "#34495e"]


def _wealth_fan_figure(rep: ScenarioReport) -> go.Figure:
    pcts = wealth_percentiles(rep, qs=(10, 25, 50, 75, 90))
    ages = list(range(rep.case.age, rep.case.age + rep.case.years))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ages, y=pcts[10], name="p10", line=dict(color="#cccccc"), showlegend=False))
    fig.add_trace(
        go.Scatter(
            x=ages, y=pcts[90], name="p10–p90", line=dict(color="#cccccc"),
            fill="tonexty", fillcolor="rgba(34,112,147,0.15)",
        )
    )
    fig.add_trace(go.Scatter(x=ages, y=pcts[25], name="p25", line=dict(color="#888"), showlegend=False))
    fig.add_trace(
        go.Scatter(
            x=ages, y=pcts[75], name="p25–p75", line=dict(color="#888"),
            fill="tonexty", fillcolor="rgba(34,112,147,0.30)",
        )
    )
    fig.add_trace(go.Scatter(x=ages, y=pcts[50], name="median", line=dict(color="#227093", width=3)))
    fig.update_layout(
        title=f"Wealth fan chart (real {rep.case.currency}) — {rep.case.name}",
        xaxis_title="Age",
        yaxis_title=f"Real wealth ({rep.case.currency}, base-year)",
        height=420,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def _withdrawal_figure(rep: ScenarioReport) -> go.Figure:
    bd = withdrawal_breakdown(rep)
    ages = list(range(rep.case.age, rep.case.age + rep.case.years))
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(x=ages, y=bd["emergency_fund"], name="From EF", marker_color="#34495e"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(x=ages, y=bd["portfolio"], name="From portfolio (net)", marker_color="#227093"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(x=ages, y=bd["pension"], name="Pension", marker_color="#2cba9b"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Bar(x=ages, y=bd["income"], name="Income", marker_color="#218c74"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=ages, y=bd["expenses"], name="Expenses", line=dict(color="#fb5053", width=2)),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=ages, y=bd["tax"], name="Tax (real)", line=dict(color="#fac532", dash="dot")),
        secondary_y=True,
    )
    fig.update_layout(
        barmode="stack",
        title="Annual cashflows (real, median across runs)",
        xaxis_title="Age",
        height=420,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_yaxes(title_text=f"{rep.case.currency}/year", secondary_y=False)
    fig.update_yaxes(title_text="Tax", secondary_y=True, showgrid=False)
    return fig


def _terminal_histogram(rep: ScenarioReport) -> go.Figure:
    terminal = rep.wealth_real[:, -1]
    fig = go.Figure(
        go.Histogram(x=terminal, nbinsx=60, marker_color="#227093", name="Terminal wealth")
    )
    fig.update_layout(
        title=f"Terminal real wealth distribution — success: {rep.success_rate * 100:.1f}%",
        xaxis_title=f"Real wealth at end ({rep.case.currency})",
        yaxis_title="Runs",
        height=320,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def _failure_histogram(rep: ScenarioReport) -> go.Figure | None:
    failed = rep.failure_year[rep.failure_year >= 0]
    if len(failed) == 0:
        return None
    ages = rep.case.age + failed
    fig = go.Figure(go.Histogram(x=ages, nbinsx=30, marker_color="#fb5053"))
    fig.update_layout(
        title=f"When runs run out of money (n={len(failed)})",
        xaxis_title="Age at failure",
        yaxis_title="Runs",
        height=300,
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def _summary_card(agg: Aggregates, currency: str) -> str:
    def eur(x: float) -> str:
        if abs(x) >= 1e6:
            return f"{x / 1e6:.2f} M {currency}"
        if abs(x) >= 1e3:
            return f"{x / 1e3:.0f}k {currency}"
        return f"{x:.0f} {currency}"

    rows = [
        ("Success rate", f"{agg.success_rate * 100:.1f}%"),
        ("Terminal wealth p10 / p50 / p90",
         f"{eur(agg.terminal_wealth_p10)} / {eur(agg.terminal_wealth_p50)} / {eur(agg.terminal_wealth_p90)}"),
        ("Median lifetime tax (real)", eur(agg.median_lifetime_tax)),
        ("Median effective tax rate (on withdrawals)",
         f"{agg.median_effective_tax_rate * 100:.1f}%"),
    ]
    if agg.median_failure_age is not None:
        rows.append(("Median failure age", f"{agg.median_failure_age:.0f}"))
    if agg.early_real_return_failed is not None and agg.early_real_return_succeeded is not None:
        rows.append((
            "Early-decade real return (succeeded vs failed)",
            f"{agg.early_real_return_succeeded * 100:+.2f}% / "
            f"{agg.early_real_return_failed * 100:+.2f}%"
        ))
    table = "\n".join(
        f"<tr><td>{k}</td><td><strong>{v}</strong></td></tr>" for k, v in rows
    )
    return f"<table class='summary'>{table}</table>"


def render(reports: list[ScenarioReport], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    blocks = []
    for rep in reports:
        agg = aggregate(rep)
        wealth = _wealth_fan_figure(rep).to_html(full_html=False, include_plotlyjs=False)
        flows = _withdrawal_figure(rep).to_html(full_html=False, include_plotlyjs=False)
        terminal = _terminal_histogram(rep).to_html(full_html=False, include_plotlyjs=False)
        fail = _failure_histogram(rep)
        fail_html = (
            fail.to_html(full_html=False, include_plotlyjs=False)
            if fail is not None
            else "<p><em>No runs failed.</em></p>"
        )
        summary = _summary_card(agg, rep.case.currency)
        blocks.append(
            f"""
            <section class="scenario">
              <h2>{rep.case.name}</h2>
              <p class="desc">{rep.case.description or ''}</p>
              {summary}
              {wealth}
              {flows}
              <div class="grid-2">{terminal}{fail_html}</div>
            </section>
            """
        )

    # Comparison table across scenarios.
    comp_rows = []
    for rep in reports:
        agg = aggregate(rep)
        comp_rows.append(
            f"<tr><td>{rep.case.name}</td>"
            f"<td>{agg.success_rate * 100:.1f}%</td>"
            f"<td>{agg.terminal_wealth_p10:,.0f}</td>"
            f"<td>{agg.terminal_wealth_p50:,.0f}</td>"
            f"<td>{agg.terminal_wealth_p90:,.0f}</td>"
            f"<td>{agg.median_lifetime_tax:,.0f}</td>"
            "</tr>"
        )
    comparison = (
        "<h2>Scenario comparison</h2>"
        "<table class='compare'><thead><tr>"
        "<th>Scenario</th><th>Success</th><th>Term. p10</th><th>Term. p50</th>"
        "<th>Term. p90</th><th>Median lifetime tax</th>"
        "</tr></thead><tbody>"
        + "\n".join(comp_rows)
        + "</tbody></table>"
        if len(reports) > 1
        else ""
    )

    plotly_js = get_plotlyjs()
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>fireplace report</title>
  <script>{plotly_js}</script>
  <style>
    body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 24px; max-width: 1200px; margin: 0 auto; color: #1f2937; }}
    h1 {{ color: #227093; }}
    h2 {{ border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
    .summary {{ border-collapse: collapse; margin: 12px 0 24px; }}
    .summary td {{ padding: 6px 14px; border-bottom: 1px dashed #e5e7eb; }}
    .summary td:first-child {{ color: #6b7280; }}
    .compare {{ border-collapse: collapse; width: 100%; margin: 12px 0 32px; }}
    .compare th, .compare td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; text-align: left; }}
    .compare th {{ background: #f3f4f6; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .scenario {{ margin-bottom: 48px; }}
    .desc {{ color: #6b7280; margin-top: -4px; }}
    footer {{ color: #9ca3af; font-size: 12px; margin-top: 48px; }}
  </style>
</head>
<body>
  <h1>fireplace report</h1>
  <p>Monte Carlo retirement simulator. {len(reports)} scenario{'s' if len(reports) != 1 else ''}, {reports[0].case.n_runs} runs each.</p>
  {comparison}
  {''.join(blocks)}
  <footer>Generated by <code>fireplace</code>. Real values are deflated to the base year using realised CPI per run.</footer>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    return out_path
