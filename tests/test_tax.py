"""Tests for the Spain savings-tax helpers."""
from __future__ import annotations

import numpy as np
import pytest

from fireplace.case import TaxConfig
from fireplace.tax import (
    gross_up_for_withdrawal,
    gross_up_for_withdrawal_vec,
    progressive_tax,
    progressive_tax_vec,
    tax_on_extra_income,
)


def test_progressive_tax_zero():
    assert progressive_tax(0.0, [(6000, 0.19), (float("inf"), 0.21)]) == 0.0


def test_progressive_tax_first_bracket():
    # 1000€ entirely in 19% bracket
    assert progressive_tax(1000, [(6000, 0.19), (float("inf"), 0.21)]) == pytest.approx(190.0)


def test_progressive_tax_spans_brackets():
    # 10_000€: first 6_000 at 19% + 4_000 at 21% = 1140 + 840 = 1980
    brackets = [(6000, 0.19), (50000, 0.21), (float("inf"), 0.23)]
    assert progressive_tax(10_000, brackets) == pytest.approx(1980.0)


def test_gross_up_no_gain():
    cfg = TaxConfig()
    # All cost basis = no realised gain, no tax.
    res = gross_up_for_withdrawal(
        net_needed=1000.0, market_value=10000.0, cost_basis=10000.0, cfg=cfg,
    )
    assert res.tax == 0.0
    assert res.realised_gain == 0.0
    assert res.new_cost_basis == pytest.approx(9000.0)


def test_gross_up_full_gain():
    # Cost basis 0 → entire withdrawal is realised gain → progressive tax applies.
    cfg = TaxConfig()
    res = gross_up_for_withdrawal(
        net_needed=10_000.0, market_value=100_000.0, cost_basis=0.0, cfg=cfg,
    )
    # gross_g - tax(gross_g) = 10_000.  With first bracket 19%: tax ≈ 19% of gain.
    # gross ≈ 10_000 / 0.81 ≈ 12_345 if all in first bracket.
    assert res.tax == pytest.approx(progressive_tax(res.realised_gain, cfg.brackets))
    assert res.realised_gain - res.tax == pytest.approx(10_000.0, abs=1e-2)


def test_tax_on_extra_stacks_correctly():
    """Tax on `extra` stacked above `prior` matches the bracket-by-bracket math."""
    cfg = TaxConfig()
    # 5_000 at 19% (within first bracket): 950
    assert tax_on_extra_income(5_000, prior=0, cfg=cfg) == pytest.approx(950.0)
    # 5_000 stacked on 5_000: 1_000 at 19% + 4_000 at 21% = 190 + 840 = 1030
    assert tax_on_extra_income(5_000, prior=5_000, cfg=cfg) == pytest.approx(1030.0)


def test_gross_up_with_prior_savings_income_pays_more():
    """Same gain, but stacked above existing savings income → more tax."""
    cfg = TaxConfig()
    base = gross_up_for_withdrawal(
        net_needed=10_000.0, market_value=20_000.0, cost_basis=10_000.0,
        cfg=cfg, prior_savings_income=0.0,
    )
    stacked = gross_up_for_withdrawal(
        net_needed=10_000.0, market_value=20_000.0, cost_basis=10_000.0,
        cfg=cfg, prior_savings_income=4_000.0,
    )
    # Same net delivered, but `stacked` had to push more euros through 21% brackets.
    assert stacked.tax > base.tax
    # gain_fraction = 0.5, so gross = realised_gain / 0.5; net = gross - tax = 10_000 in both.
    assert (base.realised_gain / 0.5) - base.tax == pytest.approx(10_000.0, abs=1e-2)
    assert (stacked.realised_gain / 0.5) - stacked.tax == pytest.approx(10_000.0, abs=1e-2)


def test_gross_up_partial_gain():
    # Half of portfolio is gain.
    cfg = TaxConfig()
    res = gross_up_for_withdrawal(
        net_needed=5_000.0, market_value=20_000.0, cost_basis=10_000.0, cfg=cfg,
    )
    # gain_fraction = 0.5; gross such that gross - tax(0.5*gross) = 5000.
    gross = res.realised_gain / 0.5
    assert gross - res.tax == pytest.approx(5_000.0, abs=1e-2)
    # FIFO basis: cost_basis decreases by the cost portion of gross.
    assert res.new_cost_basis == pytest.approx(10_000.0 - 0.5 * gross, abs=1e-2)


def test_vectorised_matches_scalar():
    """The batched tax path must equal the scalar reference run-by-run, across a
    spread of market values, cost bases, prior savings income, and per-run
    inflation factors (which scale the brackets independently)."""
    cfg = TaxConfig()  # default Spain brackets, inflation-indexed
    rng = np.random.default_rng(0)
    L = 400
    mv = rng.uniform(0.0, 500_000.0, L)
    cb = mv * rng.uniform(0.0, 1.5, L)          # mix of gains and losses
    need = rng.uniform(0.0, 40_000.0, L)
    prior = rng.uniform(0.0, 80_000.0, L)
    inf = rng.uniform(1.0, 2.5, L)

    # progressive_tax_vec vs progressive_tax with per-run indexed brackets.
    from fireplace.tax import _indexed_brackets, _indexed_bounds
    bounds, rates = _indexed_bounds(cfg, inf)
    got = progressive_tax_vec(prior, bounds, rates)
    for i in range(L):
        want = progressive_tax(prior[i], _indexed_brackets(cfg, float(inf[i])))
        assert got[i] == pytest.approx(want, rel=1e-9, abs=1e-6)

    # gross_up_for_withdrawal_vec vs the scalar solver.
    gross, tax, new_cb, realised = gross_up_for_withdrawal_vec(
        net_needed=need, market_value=mv, cost_basis=cb, cfg=cfg,
        inflation_factor=inf, prior_savings_income=prior,
    )
    for i in range(L):
        tr = gross_up_for_withdrawal(
            net_needed=float(need[i]), market_value=float(mv[i]),
            cost_basis=float(cb[i]), cfg=cfg, inflation_factor=float(inf[i]),
            prior_savings_income=float(prior[i]),
        )
        # Scalar guards need>0 and mv>0; vec delivers zero for the rest.
        if need[i] <= 0 or mv[i] <= 0:
            assert gross[i] == 0.0 and tax[i] == 0.0
            continue
        assert gross[i] == pytest.approx(tr.gross, rel=1e-7, abs=1e-4)
        assert tax[i] == pytest.approx(tr.tax, rel=1e-7, abs=1e-4)
        assert new_cb[i] == pytest.approx(tr.new_cost_basis, rel=1e-7, abs=1e-4)
        assert realised[i] == pytest.approx(tr.realised_gain, rel=1e-7, abs=1e-4)
