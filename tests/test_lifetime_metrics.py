"""Lifetime-WR, peak-WR, failure-severity, drawdown, funded-ratio, legacy and
sustainable-spending metrics — pinned against hand-computable cases."""
from __future__ import annotations

import numpy as np
import pytest

from fireplace.case import Case, Stream
from fireplace.report import (
    FUNDED_RATIO_REAL_DISCOUNT,
    aggregate,
    lifetime_wr_percentiles,
    spend_to_zero,
    sustainable_spending,
)
from fireplace.simulate import simulate


def _det_case(**overrides) -> Case:
    """Deterministic-ish: constant zero inflation, no withdrawal tax."""
    base = dict(
        name="t", age=40, end_age=44,
        portfolio=100_000, portfolio_cost_basis=100_000,
        emergency_fund=0, cash_nominal_return=0.0,
        inflation_mode="constant", inflation_rate=0.0,
        return_mode="bootstrap", return_series="msci_world_total",
        n_runs=1, seed=99, incomes=[], expenses=[],
    )
    base.update(overrides)
    return Case(**base)


def test_median_first_year_wr_is_first_finite_of_series():
    """aggregate's year-1 WR is derived from withdrawal_rate: for a single run it
    equals the first finite entry of that run's per-year WR row."""
    spend = Stream(name="s", amount=4_000, start_age=40, end_age=44, inflate=False, kind="expense")
    rep = simulate(_det_case(expenses=[spend]))
    row = rep.withdrawal_rate[0]
    first_finite = row[np.isfinite(row)][0]
    assert aggregate(rep).median_first_year_wr == pytest.approx(first_finite, rel=1e-9)


def test_peak_wr_is_max_over_lifetime():
    """Peak WR (single run) is the max of that run's per-year withdrawal rates."""
    spend = Stream(name="s", amount=4_000, start_age=40, end_age=44, inflate=False, kind="expense")
    rep = simulate(_det_case(expenses=[spend]))
    agg = aggregate(rep)
    expected = np.nanmax(rep.withdrawal_rate[0])
    # Single surviving run → median peak == that run's peak.
    assert agg.median_peak_wr == pytest.approx(expected, rel=1e-9)
    # Peak ≥ year-1 (more drawn down later as the pot shrinks).
    first_wr = rep.withdrawal_rate[0][np.isfinite(rep.withdrawal_rate[0])][0]
    assert agg.median_peak_wr >= first_wr - 1e-12


def test_years_unfunded_severity():
    """A run that fails partway leaves (horizon - failure_year) plan-years short."""
    # Spend far more than the pot can sustain → guaranteed early failure.
    spend = Stream(name="s", amount=60_000, start_age=40, end_age=44, inflate=False, kind="expense")
    rep = simulate(_det_case(portfolio=80_000, expenses=[spend]))
    agg = aggregate(rep)
    assert rep.failure_year[0] >= 0  # it failed
    expected = rep.case.years - rep.failure_year[0]
    assert agg.median_years_unfunded == pytest.approx(expected)


def test_funded_ratio_known_value():
    """Funded ratio = assets / PV(real deficits) at the discount rate.

    Retire immediately at 40 with a 100k pot and a flat 10k real deficit for
    5 years (ages 40-44). PV = sum 10k/(1+d)^t for t=0..4."""
    spend = Stream(name="s", amount=10_000, start_age=40, end_age=44, inflate=False, kind="expense")
    # No-gain pot so withdrawals are untaxed; income 0; pension 0.
    rep = simulate(_det_case(portfolio=100_000, portfolio_cost_basis=100_000, expenses=[spend]))
    agg = aggregate(rep)
    d = FUNDED_RATIO_REAL_DISCOUNT
    pv = sum(10_000 / (1 + d) ** t for t in range(5))
    # Retirement year is 0 → assets = initial pot = 100k.
    assert agg.funded_ratio == pytest.approx(100_000 / pv, rel=1e-6)


def test_legacy_prob_bounds():
    """Leaving ≥ starting pot is a probability in [0, 1]; with tiny spend and a
    multi-year horizon it should be high."""
    spend = Stream(name="s", amount=1_000, start_age=40, end_age=44, inflate=False, kind="expense")
    rep = simulate(_det_case(n_runs=200, expenses=[spend]))
    agg = aggregate(rep)
    assert 0.0 <= agg.prob_legacy_above_start <= 1.0


def test_lifetime_wr_percentiles_shape_and_gaps():
    """Percentile series has one entry per year; surplus/accumulation years NaN."""
    income = Stream(name="sal", amount=50_000, start_age=40, end_age=40, inflate=False, kind="income")
    spend = Stream(name="s", amount=10_000, start_age=40, end_age=44, inflate=False, kind="expense")
    rep = simulate(_det_case(n_runs=50, incomes=[income], expenses=[spend]))
    pct = lifetime_wr_percentiles(rep, qs=(50, 90))
    assert pct[50].shape == (rep.case.years,)
    # Year 0: income 50k > spend 10k → surplus → no WR → NaN.
    assert np.isnan(pct[50][0])
    # Year 1: deficit → defined.
    assert np.isfinite(pct[50][1])


def test_sustainable_spending_brackets_target():
    """The solved multiple sits at the boundary: success ≥ target at `multiple`."""
    spend = Stream(name="s", amount=20_000, start_age=40, end_age=44, inflate=False, kind="expense")
    case = _det_case(n_runs=300, portfolio=400_000, expenses=[spend])
    res = sustainable_spending(case, target_success=0.90, iters=12)
    assert 0.0 <= res.multiple <= 3.0
    assert res.achieved_success >= 0.90 - 1e-9


def test_spend_to_zero_drives_median_terminal_down():
    """The solved multiple leaves median terminal wealth ≈ the target legacy,
    and it spends strictly more than the conservative sustainable multiple."""
    spend = Stream(name="s", amount=20_000, start_age=40, end_age=44, inflate=False, kind="expense")
    case = _det_case(n_runs=300, portfolio=400_000, expenses=[spend])
    dwz = spend_to_zero(case, target_percentile=50, target_legacy=0.0, iters=16)
    # Median terminal wealth is pulled down to roughly the target (within MC/
    # bisection slack), and driving the median to zero means ~half fail.
    assert dwz.median_terminal < 50_000
    assert dwz.achieved_success <= 0.6
    # Spending the median to zero is strictly more aggressive than holding 90%.
    safe = sustainable_spending(case, target_success=0.90, iters=12)
    assert dwz.multiple > safe.multiple
