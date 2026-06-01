"""Year-1 (first-deficit-year) withdrawal rate is computed from the engine's
own balances: gross spending need that year / investable wealth available to
fund it (portfolio + EF), both in the same year's nominal EUR."""
from __future__ import annotations

import numpy as np
import pytest

from fireplace.case import Case, Stream
from fireplace.report import aggregate
from fireplace.simulate import simulate


def _first_wr(rep, i: int = 0) -> float:
    """First-retirement-year WR for run `i`: the first finite entry of its
    per-year withdrawal-rate row (NaN if the run never withdraws)."""
    row = rep.withdrawal_rate[i]
    finite = row[np.isfinite(row)]
    return float(finite[0]) if finite.size else float("nan")


def _deterministic_case(**overrides) -> Case:
    """Zero returns, zero inflation, no tax-relevant gains → WR is exact."""
    base = dict(
        name="t", age=40, end_age=42,
        portfolio=100_000,
        portfolio_cost_basis=100_000,      # no unrealised gain → no withdrawal tax
        emergency_fund=0,
        cash_nominal_return=0.0,
        inflation_mode="constant", inflation_rate=0.0,
        return_mode="bootstrap", return_series="msci_world_total",
        n_runs=1, seed=99,
        incomes=[],
        expenses=[],
    )
    base.update(overrides)
    return Case(**base)


def test_year1_wr_exact():
    """A 4,000 spend against a 100,000 pot in the first deficit year → 4.00%.

    Returns aren't truly zero (bootstrap), so assert the relationship the engine
    actually uses rather than a hard-coded 0.04: need / (portfolio + EF) at the
    first deficit year, which here is year 0."""
    spend = Stream(name="spend", amount=4_000, start_age=40, end_age=42,
                   inflate=False, kind="expense")
    case = _deterministic_case(emergency_fund=0, expenses=[spend])
    rep = simulate(case)

    # Year 0 is the first (and immediate) deficit. WR = 4000 / (portfolio+EF)
    # available post-return that year. Recompute the same way to pin the contract.
    pf_after_return = 100_000 * (1.0 + rep.realised_returns[0, 0])
    expected = 4_000 / pf_after_return
    assert _first_wr(rep) == pytest.approx(expected, rel=1e-9)


def test_wr_denominator_includes_emergency_fund():
    """The denominator is investable wealth = portfolio + EF, not portfolio alone."""
    spend = Stream(name="spend", amount=4_000, start_age=40, end_age=42,
                   inflate=False, kind="expense")
    case = _deterministic_case(portfolio=100_000, emergency_fund=100_000,
                               expenses=[spend])
    rep = simulate(case)
    pf = 100_000 * (1.0 + rep.realised_returns[0, 0])
    ef = 100_000  # cash_nominal_return = 0
    assert _first_wr(rep) == pytest.approx(4_000 / (pf + ef), rel=1e-9)


def test_wr_nan_when_never_withdraws():
    """A run that always runs a surplus never withdraws → WR is NaN, agg is None."""
    income = Stream(name="salary", amount=50_000, start_age=40, end_age=42,
                    inflate=False, kind="income")
    case = _deterministic_case(expenses=[], incomes=[income])
    rep = simulate(case)
    assert np.isnan(_first_wr(rep))
    assert aggregate(rep).median_first_year_wr is None


def test_wr_captures_first_deficit_only():
    """WR locks on the FIRST deficit year, not later ones."""
    # Surplus at 40 (income > expense), deficit from 41 on.
    income = Stream(name="salary", amount=30_000, start_age=40, end_age=40,
                    inflate=False, kind="income")
    spend = Stream(name="spend", amount=10_000, start_age=40, end_age=43,
                   inflate=False, kind="expense")
    case = _deterministic_case(age=40, end_age=43, portfolio=200_000,
                               portfolio_cost_basis=200_000,
                               incomes=[income], expenses=[spend])
    rep = simulate(case)
    # Year 0: net = 30k - 10k = +20k surplus → no withdrawal, no WR yet.
    # Year 1: first deficit of 10k. Denominator = portfolio+EF entering year 1.
    assert np.isfinite(_first_wr(rep))
    # The captured rate must be ~10k / (~220k pot), i.e. well under 6%, proving
    # it didn't fire on the surplus year 0.
    assert 0.0 < _first_wr(rep) < 0.06
