"""Command-line entry point: load a config, run sims, write an HTML report."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import click

from .config import load_config
from .narrate import result_block
from .render_html import render
from .report import aggregate, spend_to_zero, sustainable_spending
from .simulate import simulate


@click.group()
def main() -> None:
    """fireplace — Monte Carlo retirement simulator."""


@main.command()
@click.argument("config", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o", "--out", default="out/report.html", type=click.Path(path_type=Path),
    help="Where to write the HTML report.",
)
@click.option(
    "--scenario", "only", multiple=True,
    help="Run only the named scenario(s). May be passed multiple times.",
)
@click.option("--quiet", is_flag=True, help="Suppress per-scenario progress output.")
@click.option(
    "--solve", is_flag=True,
    help="Also solve each scenario's sustainable spending multiple (re-simulates; slower).",
)
@click.option(
    "--dwz", is_flag=True,
    help="Also solve each scenario's die-with-zero spend multiple — the spend that "
         "drives median terminal wealth to zero (re-simulates; slower).",
)
def run(
    config: Path, out: Path, only: tuple[str, ...], quiet: bool, solve: bool, dwz: bool
) -> None:
    """Run all scenarios in CONFIG and write a single HTML report."""
    cases = load_config(config)
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.name in wanted]
        if not cases:
            click.echo(f"No scenarios match {sorted(wanted)}", err=True)
            sys.exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    reports = []
    sustainable = {}
    dwz_solved = {}
    for case in cases:
        t0 = time.perf_counter()
        rep = simulate(case)
        if solve:
            sustainable[case.name] = sustainable_spending(case)
        if dwz:
            dwz_solved[case.name] = spend_to_zero(case)
        if not quiet:
            agg = aggregate(rep)
            dur = time.perf_counter() - t0
            wr = f"{agg.median_first_year_wr * 100:5.2f}%" if agg.median_first_year_wr is not None else "  n/a"
            peak = f"{agg.median_peak_wr * 100:5.2f}%" if agg.median_peak_wr is not None else "  n/a"
            funded = f"{agg.funded_ratio:4.2f}x" if agg.funded_ratio is not None else " n/a"
            spend = f"  spend {sustainable[case.name].multiple:.2f}x" if solve else ""
            dwz_str = ""
            if dwz:
                z = dwz_solved[case.name]
                dwz_str = f"  dwz {z.multiple:.2f}x@{z.achieved_success * 100:.0f}%"
            click.echo(
                f"  {case.name:<24} success {agg.success_rate * 100:5.1f}%  "
                f"WR {wr}  peak {peak}  funded {funded}  "
                f"term p50 {agg.terminal_wealth_p50:>12,.0f}{spend}{dwz_str}  ({dur:.2f}s)"
            )
        reports.append(rep)
    path = render(reports, out, sustainable=sustainable or None, dwz=dwz_solved or None)
    click.echo(f"\nWrote {path}")


@main.command()
@click.argument("config", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--scenario", "only", multiple=True,
    help="Summarise only the named scenario(s). May be passed multiple times.",
)
@click.option(
    "--solve/--no-solve", default=True,
    help="Include the sustainable-spending multiple (re-simulates; slower). Default on.",
)
def summary(config: Path, only: tuple[str, ...], solve: bool) -> None:
    """Print the deterministic, templated Result block for each scenario.

    This is the AI-free way to refresh the prose summaries: same numbers in,
    same text out. Pipe it into a file or paste it into the config's
    descriptions — it never goes stale relative to the model."""
    cases = load_config(config)
    if only:
        cases = [c for c in cases if c.name in set(only)]
    for case in cases:
        rep = simulate(case)
        agg = aggregate(rep)
        spend = sustainable_spending(case) if solve else None
        click.echo(f"\n=== {case.name} ===")
        click.echo(result_block(agg, n_runs=case.n_runs, currency=case.currency, spend=spend))


@main.command(name="list")
@click.argument("config", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def list_scenarios(config: Path) -> None:
    """List scenarios declared in CONFIG."""
    cases = load_config(config)
    for c in cases:
        click.echo(f"{c.name:<24} (age {c.age}–{c.end_age}, {c.n_runs} runs)")


@main.command()
@click.argument("config", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def streamlit(config: Path) -> None:
    """Launch the interactive Streamlit app on the given config."""
    import shutil

    if not shutil.which("streamlit"):
        click.echo("streamlit not installed. Install with: pip install -e '.[ui]'", err=True)
        sys.exit(1)
    import subprocess

    app = Path(__file__).parent / "streamlit_app.py"
    subprocess.run(["streamlit", "run", str(app), "--", str(config)], check=False)


if __name__ == "__main__":
    main()
