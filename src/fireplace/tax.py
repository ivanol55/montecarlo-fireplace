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

import numpy as np

from .case import TaxConfig, WealthTaxConfig


@dataclass
class TaxResult:
    realised_gain: float
    tax: float
    new_cost_basis: float
    gross: float  # gross amount withdrawn (net_needed + tax), capped at market_value


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
        return TaxResult(realised_gain=0.0, tax=0.0, new_cost_basis=cost_basis, gross=0.0)

    brackets = _indexed_brackets(cfg, inflation_factor)

    gain_fraction = max(0.0, (market_value - cost_basis) / market_value)
    if gain_fraction == 0.0:
        # Returning principal — no realised gain, no tax.
        gross = min(net_needed, market_value)
        return TaxResult(
            realised_gain=0.0,
            tax=0.0,
            new_cost_basis=max(0.0, cost_basis - gross),
            gross=gross,
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
    return TaxResult(realised_gain=realised_gain, tax=tax, new_cost_basis=new_cost_basis, gross=gross)


def _marginal_rate(amount: float, brackets: list[tuple[float, float]]) -> float:
    for upper, rate in brackets:
        if amount <= upper:
            return rate
    return brackets[-1][1]


# --- Vectorised equivalents -------------------------------------------------
# The functions below compute the *same* numbers as their scalar counterparts
# above, but over a whole batch of runs at once (each run may carry its own
# inflation-indexed brackets). They exist purely for speed in the Monte Carlo
# hot path; the scalar versions remain the readable reference and are what the
# unit tests pin. `test_tax.py::test_vectorised_matches_scalar` asserts the two
# agree to floating-point tolerance.


def _indexed_bounds(cfg: TaxConfig, inflation_factor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-run (bounds, rates) arrays.

    Returns `bounds` of shape (L, B) — each row is that run's upper bounds,
    scaled by its inflation factor when indexing is on — and `rates` of shape
    (B,). The final bound is +inf and is left unscaled.
    """
    base_bounds = np.array([b for b, _ in cfg.brackets], dtype=float)  # (B,)
    rates = np.array([r for _, r in cfg.brackets], dtype=float)        # (B,)
    inf = np.asarray(inflation_factor, dtype=float)
    if cfg.index_to_inflation:
        bounds = base_bounds[None, :] * inf[:, None]  # inf * np.inf stays inf
    else:
        bounds = np.broadcast_to(base_bounds[None, :], (inf.shape[0], base_bounds.shape[0]))
    return bounds, rates


def progressive_tax_vec(amount: np.ndarray, bounds: np.ndarray, rates: np.ndarray) -> np.ndarray:
    """Vectorised `progressive_tax`. `amount` (L,), `bounds` (L,B), `rates` (B,)."""
    lowers = np.concatenate([np.zeros((bounds.shape[0], 1)), bounds[:, :-1]], axis=1)  # (L,B)
    slab = np.clip(np.minimum(amount[:, None], bounds) - lowers, 0.0, None)
    return (slab * rates[None, :]).sum(axis=1)


def _marginal_rate_vec(amount: np.ndarray, bounds: np.ndarray, rates: np.ndarray) -> np.ndarray:
    """Vectorised `_marginal_rate`: the rate of the first bracket whose upper
    bound is >= amount."""
    idx = np.minimum((amount[:, None] > bounds).sum(axis=1), rates.shape[0] - 1)
    return rates[idx]


def wealth_tax_vec(
    wealth: np.ndarray, cfg: WealthTaxConfig, inflation_factor: np.ndarray
) -> np.ndarray:
    """Annual net-wealth tax per run (Spain Patrimonio / ITSGF).

    Taxable base is `max(0, wealth - allowance)`; the progressive scale in
    `cfg.brackets` is then applied to that base. When `cfg.index_to_inflation`
    is set, both the allowance and the bracket bounds scale by each run's
    cumulative inflation factor — matching the income-tax treatment so the real
    burden stays comparable across years. Returns all-zeros when disabled.
    """
    w = np.asarray(wealth, dtype=float)
    out = np.zeros_like(w)
    if not cfg.enabled or w.size == 0:
        return out
    inf = np.asarray(inflation_factor, dtype=float)
    allowance = cfg.allowance * (inf if cfg.index_to_inflation else np.ones_like(inf))
    base = np.clip(w - allowance, 0.0, None)
    bounds, rates = _indexed_bounds(cfg, inf)  # duck-typed on .brackets/.index_to_inflation
    return progressive_tax_vec(base, bounds, rates)


def gross_up_for_withdrawal_vec(
    net_needed: np.ndarray,
    market_value: np.ndarray,
    cost_basis: np.ndarray,
    cfg: TaxConfig,
    inflation_factor: np.ndarray,
    prior_savings_income: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Batched `gross_up_for_withdrawal`. All inputs are (L,) arrays.

    Returns (gross, tax, new_cost_basis, realised_gain), each (L,). Runs with
    non-positive market value or need deliver zero (matching the scalar guard).
    """
    net = np.asarray(net_needed, dtype=float)
    mv = np.asarray(market_value, dtype=float)
    cb = np.asarray(cost_basis, dtype=float)
    prior = np.asarray(prior_savings_income, dtype=float)
    L = net.shape[0]

    gross = np.zeros(L)
    tax = np.zeros(L)
    realised_gain = np.zeros(L)
    new_cost_basis = cb.copy()

    payable = (mv > 0) & (net > 0)
    if not payable.any():
        return gross, tax, new_cost_basis, realised_gain

    bounds, rates = _indexed_bounds(cfg, inflation_factor)
    gf = np.where(mv > 0, np.clip((mv - cb) / np.where(mv > 0, mv, 1.0), 0.0, None), 0.0)
    base_tax = progressive_tax_vec(prior, bounds, rates)

    # No-gain runs: returning principal, no tax. gross capped at market value.
    no_gain = payable & (gf == 0.0)
    if no_gain.any():
        g = np.minimum(net, mv)
        gross[no_gain] = g[no_gain]
        new_cost_basis[no_gain] = np.clip(cb - g, 0.0, None)[no_gain]

    # Gain runs: Newton-solve for gross s.t. gross - marginal_tax(gross) == net.
    gain = payable & (gf > 0.0)
    if gain.any():
        marginal0 = _marginal_rate_vec(prior, bounds, rates)
        g = net / np.maximum(1e-9, 1.0 - marginal0 * gf)
        for _ in range(10):
            g = np.minimum(g, mv)
            realised = g * gf
            t = progressive_tax_vec(prior + realised, bounds, rates) - base_tax
            net_now = g - t
            marginal = _marginal_rate_vec(prior + realised, bounds, rates)
            denom = 1.0 - marginal * gf
            step = np.where(denom > 1e-9, (net - net_now) / np.where(denom > 1e-9, denom, 1.0), 0.0)
            g = g + step
        g = np.minimum(g, mv)
        realised = g * gf
        t = progressive_tax_vec(prior + realised, bounds, rates) - base_tax
        cost_portion = g * (1.0 - gf)
        gross = np.where(gain, g, gross)
        tax = np.where(gain, t, tax)
        realised_gain = np.where(gain, realised, realised_gain)
        new_cost_basis = np.where(gain, np.clip(cb - cost_portion, 0.0, None), new_cost_basis)

    return gross, tax, new_cost_basis, realised_gain
