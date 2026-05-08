"""Command-line entry point: load a config, run sims, write an HTML report."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import click

from .config import load_config
from .render_html import render
from .report import aggregate
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
def run(config: Path, out: Path, only: tuple[str, ...], quiet: bool) -> None:
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
    for case in cases:
        t0 = time.perf_counter()
        rep = simulate(case)
        if not quiet:
            agg = aggregate(rep)
            dur = time.perf_counter() - t0
            click.echo(
                f"  {case.name:<24} success {agg.success_rate * 100:5.1f}%  "
                f"term p50 {agg.terminal_wealth_p50:>12,.0f}  ({dur:.2f}s)"
            )
        reports.append(rep)
    path = render(reports, out)
    click.echo(f"\nWrote {path}")


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
