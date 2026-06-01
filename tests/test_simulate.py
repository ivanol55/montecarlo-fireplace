"""Behavioural tests for the simulator."""
from __future__ import annotations

import numpy as np

from fireplace.case import Case, CurvePoint, DynamicSpending, Pension, SpendingCurve, Stream
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


def _disc_case(**overrides) -> Case:
    """A run that retires immediately (pure deficit) with a single discretionary
    expense, so spending mechanisms have something to act on."""
    base = dict(
        name="d", age=60, end_age=75,
        portfolio=300_000, portfolio_cost_basis=300_000,
        emergency_fund=0, cash_nominal_return=0.0,
        inflation_mode="constant", inflation_rate=0.0,
        return_mode="bootstrap", return_series="msci_world_total",
        n_runs=400, seed=7, incomes=[], pension=Pension(),
        expenses=[Stream(
            name="living", amount=15_000, start_age=60, end_age=75,
            inflate=False, kind="expense", discretionary=True,
        )],
    )
    base.update(overrides)
    return Case(**base)


def test_spending_curve_scales_only_discretionary():
    """A flat 0.5× curve halves discretionary spend → less is withdrawn, so the
    plan ends richer than the same case with no curve."""
    flat = simulate(_disc_case())
    curve = SpendingCurve(enabled=True, pivots=[CurvePoint(age=60, factor=0.5)])
    halved = simulate(_disc_case(spending_curve=curve))
    assert np.median(halved.wealth_real[:, -1]) > np.median(flat.wealth_real[:, -1])
    # A non-discretionary expense is immune to the curve.
    fixed_expense = [Stream(
        name="fixed", amount=15_000, start_age=60, end_age=75,
        inflate=False, kind="expense", discretionary=False,
    )]
    flat_fixed = simulate(_disc_case(expenses=fixed_expense))
    curved_fixed = simulate(_disc_case(expenses=fixed_expense, spending_curve=curve))
    assert np.allclose(flat_fixed.wealth_real, curved_fixed.wealth_real)


def test_dynamic_guardrails_lift_success_and_cut_peak_wr():
    """Guardrails that cut spending when the pot is stretched should never lower
    the success rate and should reduce the median peak withdrawal rate."""
    static = simulate(_disc_case())
    dyn = simulate(_disc_case(dynamic_spending=DynamicSpending(
        enabled=True, upper_guard=1.1, lower_guard=0.9, cut=0.15, bump=0.10,
        floor=0.4, ceiling=1.5,
    )))
    assert dyn.success_rate >= static.success_rate
    a_static, a_dyn = aggregate(static), aggregate(dyn)
    if a_static.median_peak_wr is not None and a_dyn.median_peak_wr is not None:
        assert a_dyn.median_peak_wr <= a_static.median_peak_wr + 1e-9


def test_load_example_config_runs(tmp_path):
    """The shipped example loads, validates, and produces a non-zero report."""
    from pathlib import Path

    # Project root from tests/ → ../examples/spain_default.yaml
    example = Path(__file__).resolve().parent.parent / "examples" / "spain_default.yaml"
    cases = load_config(example)
    # Cap runs for test speed.
    for c in cases:
        c.n_runs = 100
    rep = simulate(cases[0])
    agg = aggregate(rep)
    assert 0.0 <= agg.success_rate <= 1.0
    assert rep.wealth_real.shape == (100, cases[0].years)
