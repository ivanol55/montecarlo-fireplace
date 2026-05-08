"""Tests for the Spain savings-tax helpers."""
from __future__ import annotations

import pytest

from fireplace.case import TaxConfig
from fireplace.tax import (
    gross_up_for_withdrawal,
    progressive_tax,
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
