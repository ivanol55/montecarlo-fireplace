"""Spain savings-income tax with FIFO cost basis and bracket stacking.

A withdrawal of `gross` from a portfolio that has cost basis `cb` and current
market value `mv` realises a capital gain proportional to the unrealised gain
fraction:

    gain_fraction = max(0, (mv - cb) / mv)
    realised_gain = gross * gain_fraction

Spain combines all "rendimientos del capital mobiliario" + capital gains into
one savings-income base ("base imponible del ahorro"). Cuenta-remunerada
interest, dividends, and realised fund gains all stack into the same set of
progressive brackets in the same year. We model that via `prior_savings_income`:
the tax on a marginal `realised_gain` is

    progressive(prior + gain) - progressive(prior)

so multiple operations in the same year correctly push later euros into
higher brackets.
"""
from __future__ import annotations

from dataclasses import dataclass

from .case import TaxConfig


@dataclass
class TaxResult:
    realised_gain: float
    tax: float
    new_cost_basis: float


def progressive_tax(amount: float, brackets: list[tuple[float, float]]) -> float:
    """Apply progressive brackets to a positive amount."""
    if amount <= 0:
        return 0.0
    tax = 0.0
    lower = 0.0
    for upper, rate in brackets:
        slab = min(amount, upper) - lower
        if slab > 0:
            tax += slab * rate
        if amount <= upper:
            return tax
        lower = upper
    return tax


def _indexed_brackets(cfg: TaxConfig, inflation_factor: float) -> list[tuple[float, float]]:
    if cfg.index_to_inflation and inflation_factor != 1.0:
        return [(b * inflation_factor if b != float("inf") else b, r) for b, r in cfg.brackets]
    return cfg.brackets


def tax_on_extra_income(
    extra: float, prior: float, cfg: TaxConfig, inflation_factor: float = 1.0
) -> float:
    """Marginal tax on `extra` of savings income stacked on top of `prior`."""
    if extra <= 0:
        return 0.0
    brackets = _indexed_brackets(cfg, inflation_factor)
    return progressive_tax(prior + extra, brackets) - progressive_tax(prior, brackets)


def gross_up_for_withdrawal(
    net_needed: float,
    market_value: float,
    cost_basis: float,
    cfg: TaxConfig,
    inflation_factor: float = 1.0,
    prior_savings_income: float = 0.0,
) -> TaxResult:
    """Find the gross withdrawal `g` such that `g - tax(g) = net_needed`.

    `prior_savings_income` is any savings income already realised this year
    (e.g. cuenta-remunerada interest, prior portfolio withdrawals). Tax on the
    realised gain is computed as the marginal increase over `prior`, so
    bracket-stacking is modelled correctly.

    Solved by Newton iteration; converges in a handful of steps.
    """
    if market_value <= 0 or net_needed <= 0:
        return TaxResult(realised_gain=0.0, tax=0.0, new_cost_basis=cost_basis)

    brackets = _indexed_brackets(cfg, inflation_factor)

    gain_fraction = max(0.0, (market_value - cost_basis) / market_value)
    if gain_fraction == 0.0:
        # Returning principal — no realised gain, no tax.
        gross = min(net_needed, market_value)
        return TaxResult(
            realised_gain=0.0,
            tax=0.0,
            new_cost_basis=max(0.0, cost_basis - gross),
        )

    base_tax = progressive_tax(prior_savings_income, brackets)
    marginal0 = _marginal_rate(prior_savings_income, brackets)
    # Initial guess based on the current marginal rate.
    gross = net_needed / max(1e-9, 1.0 - marginal0 * gain_fraction)
    for _ in range(10):
        gross = min(gross, market_value)
        gain = gross * gain_fraction
        tax = progressive_tax(prior_savings_income + gain, brackets) - base_tax
        net = gross - tax
        if abs(net - net_needed) < 1e-6:
            break
        marginal = _marginal_rate(prior_savings_income + gain, brackets)
        denom = 1.0 - marginal * gain_fraction
        if denom <= 1e-9:
            break
        gross = gross + (net_needed - net) / denom

    gross = min(gross, market_value)
    realised_gain = gross * gain_fraction
    tax = progressive_tax(prior_savings_income + realised_gain, brackets) - base_tax
    cost_portion = gross * (1.0 - gain_fraction)
    new_cost_basis = max(0.0, cost_basis - cost_portion)
    return TaxResult(realised_gain=realised_gain, tax=tax, new_cost_basis=new_cost_basis)


def _marginal_rate(amount: float, brackets: list[tuple[float, float]]) -> float:
    lower = 0.0
    for upper, rate in brackets:
        if amount <= upper:
            return rate
        lower = upper
    return brackets[-1][1]
