"""Behavioural tests for the simulator."""
from __future__ import annotations

import numpy as np

from fireplace.case import Case, Pension, Stream
from fireplace.config import load_config
from fireplace.simulate import simulate
from fireplace.report import aggregate


def _trivial_case(**overrides) -> Case:
    base = dict(
        name="t",
        age=60,
        end_age=65,
        portfolio=200_000,
        emergency_fund=10_000,
        cash_nominal_return=0.0,
        inflation_mode="constant",
        inflation_rate=0.0,
        return_mode="bootstrap",
        return_series="msci_world_total",
        n_runs=200,
        seed=1,
        incomes=[],
        expenses=[
            Stream(
                name="cost", amount=10_000, start_age=60, end_age=65,
                inflate=False, growth=0.0, kind="expense",
            )
        ],
        pension=Pension(),
    )
    base.update(overrides)
    return Case(**base)


def test_zero_expenses_never_fail():
    """If you spend nothing and have any portfolio, no run should fail."""
    case = _trivial_case(expenses=[])
    rep = simulate(case)
    assert rep.success_rate == 1.0
    # Wealth never zero either.
    assert (rep.wealth_real > 0).all()


def test_huge_expenses_always_fail():
    case = _trivial_case(
        expenses=[
            Stream(
                name="huge", amount=1_000_000, start_age=60, end_age=65,
                inflate=False, growth=0.0, kind="expense",
            )
        ],
        portfolio=10_000,
        emergency_fund=0,
    )
    rep = simulate(case)
    assert rep.success_rate == 0.0
    # All runs flagged with a failure year.
    assert (rep.failure_year >= 0).all()


def test_terminal_wealth_increases_with_starting_portfolio():
    """Strict monotonicity sanity check (median terminal vs starting capital)."""
    a = simulate(_trivial_case(portfolio=200_000))
    b = simulate(_trivial_case(portfolio=400_000))
    assert (
        np.median(b.wealth_real[:, -1]) > np.median(a.wealth_real[:, -1])
    )


def test_load_example_config_runs(tmp_path):
    """The shipped example loads, validates, and produces a non-zero report."""
    cfg_path = (
        # Project root from tests/ → ../examples/spain_default.yaml
        __file__
    )
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "examples" / "spain_default.yaml"
    cases = load_config(example)
    # Cap runs for test speed.
    for c in cases:
        c.n_runs = 100
    rep = simulate(cases[0])
    agg = aggregate(rep)
    assert 0.0 <= agg.success_rate <= 1.0
    assert rep.wealth_real.shape == (100, cases[0].years)
