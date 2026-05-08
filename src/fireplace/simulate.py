"""Monte Carlo engine.

Year-by-year accounting in **nominal** terms; deflated to base-year real
values for reporting. Order of operations within a year:

  1. Sample this year's nominal portfolio return and CPI.
  2. Apply portfolio return to market value (cost basis unchanged).
  3. Apply EF nominal return.
  4. Compute nominal income, pension, expenses for this year.
  5. Net cashflow = income + pension − expenses.
       positive → contribute to portfolio (cost basis += contribution)
       negative → withdraw under the bucket policy:
           - If this year's portfolio return < threshold and EF > 0:
               take `ef_share_in_bad_year` × deficit from EF (capped),
               rest from portfolio (gross-up for Spain savings-tax).
           - Otherwise: from portfolio. In good years, refill EF toward target.
  6. If wealth can't cover the deficit → record failure for this run.

Reported wealth is real (deflated by cumulative CPI factor)."""
from __future__ import annotations

import numpy as np

from .case import Case, ScenarioReport, Stream, TaxConfig
from .returns import load_returns, sample_multicol_paths
from .tax import gross_up_for_withdrawal


def _stream_amount(s: Stream, year_offset: int, infl_factor: np.ndarray) -> np.ndarray:
    base = s.amount * (1.0 + s.growth) ** year_offset
    return base * (infl_factor if s.inflate else np.ones_like(infl_factor))


def _withdraw_from_portfolio(
    runs: np.ndarray,
    net_needed: np.ndarray,
    portfolio: np.ndarray,
    cost_basis: np.ndarray,
    infl_factor: np.ndarray,
    tax_cfg: TaxConfig,
    prior_savings_income: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Withdraw `net_needed` from each run in `runs`. Mutates portfolio,
    cost_basis, and prior_savings_income (the realised gain is added).

    Returns (net_delivered, tax_paid) arrays, length len(runs)."""
    net_delivered = np.zeros(len(runs))
    tax_paid = np.zeros(len(runs))
    for k, i in enumerate(runs):
        need = float(net_needed[k])
        if need <= 0 or portfolio[i] <= 0:
            continue
        tr = gross_up_for_withdrawal(
            net_needed=need,
            market_value=float(portfolio[i]),
            cost_basis=float(cost_basis[i]),
            cfg=tax_cfg,
            inflation_factor=float(infl_factor[i]),
            prior_savings_income=float(prior_savings_income[i]),
        )
        gain_fraction = 0.0
        if portfolio[i] > 0:
            gain_fraction = max(0.0, (portfolio[i] - cost_basis[i]) / portfolio[i])
        gross = (tr.realised_gain / gain_fraction) if gain_fraction > 0 else need
        gross = min(gross, float(portfolio[i]))
        if gain_fraction > 0:
            realised_gain = gross * gain_fraction
            from .tax import _indexed_brackets, progressive_tax
            brackets = _indexed_brackets(tax_cfg, float(infl_factor[i]))
            prior = float(prior_savings_income[i])
            tax_amt = progressive_tax(prior + realised_gain, brackets) - progressive_tax(prior, brackets)
            cost_portion = gross * (1.0 - gain_fraction)
            new_cb = max(0.0, float(cost_basis[i]) - cost_portion)
            prior_savings_income[i] = prior + realised_gain
        else:
            tax_amt = 0.0
            new_cb = max(0.0, float(cost_basis[i]) - gross)
        portfolio[i] -= gross
        cost_basis[i] = new_cb
        net_delivered[k] = gross - tax_amt
        tax_paid[k] = tax_amt
    return net_delivered, tax_paid


def simulate(case: Case) -> ScenarioReport:
    Y = case.years
    df = load_returns(case.data_file)
    block = case.block_size if case.return_mode == "block_bootstrap" else 1
    alloc = case.resolved_allocation()
    series_cols = list({a.series for a in alloc.assets})
    inflation_col = case.inflation_series if case.inflation_mode == "bootstrap" else None
    by_series, cpi = sample_multicol_paths(
        df,
        return_cols=series_cols,
        inflation_col=inflation_col,
        n_runs=case.n_runs,
        years=Y,
        block_size=block,
        seed=case.seed,
    )
    if cpi is None:
        cpi = np.full((case.n_runs, Y), case.inflation_rate)
    # Per-asset return path (n_runs, years), in the order of alloc.assets.
    per_asset = np.stack([by_series[a.series] for a in alloc.assets], axis=-1)  # (R, Y, A)
    # Pre-compute weighted return per (run, year) — assumes annual rebalancing.
    weights_per_year = np.array(
        [[alloc.weights_at(case.age + y)[a.name] for a in alloc.assets] for y in range(Y)]
    )  # (Y, A)
    nominal_returns = np.einsum("rya,ya->ry", per_asset, weights_per_year)

    n = case.n_runs
    cb0 = case.portfolio if case.portfolio_cost_basis is None else case.portfolio_cost_basis

    portfolio = np.full(n, float(case.portfolio))
    cost_basis = np.full(n, float(cb0))
    ef = np.full(n, float(case.emergency_fund))
    failure_year = np.full(n, -1, dtype=int)
    failed = np.zeros(n, dtype=bool)
    infl_factor = np.ones(n)

    wealth_real = np.zeros((n, Y))
    portfolio_w = np.zeros((n, Y))
    ef_w = np.zeros((n, Y))
    income_r = np.zeros((n, Y))
    pension_r = np.zeros((n, Y))
    expenses_r = np.zeros((n, Y))
    tax_r = np.zeros((n, Y))

    threshold = case.withdrawal.bucket_threshold
    ef_share = case.withdrawal.ef_share_in_bad_year
    ef_target_months = case.withdrawal.ef_target_months

    for y in range(Y):
        age = case.age + y
        ret = nominal_returns[:, y]
        inf = cpi[:, y]

        # Grow balances.
        portfolio *= (1.0 + ret)
        ef_interest = ef * case.cash_nominal_return     # taxable savings income
        ef = ef + ef_interest
        infl_factor = infl_factor * (1.0 + inf)
        # Per-run cumulative savings income for the year. Cuenta-remunerada
        # interest goes in first; portfolio realised gains stack on top.
        year_savings_income = ef_interest.copy()

        # Cashflows.
        income = np.zeros(n)
        for s in case.incomes:
            if s.active(age):
                income += _stream_amount(s, y, infl_factor)
        pension = np.zeros(n)
        if case.pension.monthly_amount > 0 and age >= case.pension.start_age:
            annual = case.pension.monthly_amount * 12.0
            pension += annual * (infl_factor if case.pension.inflate else np.ones_like(infl_factor))
        expenses = np.zeros(n)
        for s in case.expenses:
            if s.active(age):
                expenses += _stream_amount(s, y, infl_factor)

        net = income + pension - expenses

        # Surplus → contribute to portfolio.
        contrib_mask = (net > 0) & (~failed)
        if contrib_mask.any():
            portfolio[contrib_mask] += net[contrib_mask]
            cost_basis[contrib_mask] += net[contrib_mask]

        # Deficit → withdraw under bucket policy.
        deficit_mask = (net < 0) & (~failed)
        net_need = np.where(deficit_mask, -net, 0.0)

        from_ef = np.zeros(n)
        from_pf = np.zeros(n)
        tax_paid = np.zeros(n)

        bad_year = ret < threshold
        ef_leg = deficit_mask & bad_year & (ef > 0)
        if ef_leg.any():
            wanted = net_need[ef_leg] * ef_share
            taken = np.minimum(wanted, ef[ef_leg])
            from_ef[ef_leg] = taken
            ef[ef_leg] -= taken

        remaining = net_need - from_ef
        pf_leg = deficit_mask & (remaining > 1e-9)
        if pf_leg.any():
            runs = np.flatnonzero(pf_leg)
            delivered, taxes = _withdraw_from_portfolio(
                runs, remaining[pf_leg], portfolio, cost_basis, infl_factor, case.tax,
                year_savings_income,
            )
            from_pf[runs] = delivered
            tax_paid[runs] += taxes

        delivered = from_ef + from_pf
        shortfall = net_need - delivered
        new_fail = deficit_mask & (shortfall > 1.0) & (~failed)
        if new_fail.any():
            failure_year[new_fail] = y
            failed[new_fail] = True
            portfolio[new_fail] = 0.0
            ef[new_fail] = 0.0
            cost_basis[new_fail] = 0.0

        # Refill EF in good years (no deficit, not failed).
        good_year = ret >= threshold
        refill_mask = good_year & (~failed) & (~deficit_mask) & (portfolio > 0)
        if refill_mask.any():
            monthly_exp = expenses / 12.0
            target = monthly_exp * ef_target_months
            need = np.maximum(target - ef, 0.0)
            pull_mask = refill_mask & (need > 0)
            if pull_mask.any():
                runs = np.flatnonzero(pull_mask)
                pull_need = np.minimum(need[pull_mask], portfolio[pull_mask])
                delivered, taxes = _withdraw_from_portfolio(
                    runs, pull_need, portfolio, cost_basis, infl_factor, case.tax,
                    year_savings_income,
                )
                ef[runs] += delivered
                tax_paid[runs] += taxes

        # Tax cuenta-remunerada interest. EF interest sat at the *bottom* of the
        # bracket stack this year; tax it at the marginal rate over zero, then
        # subtract from EF. (We taxed the gains stacked on top of `ef_interest`
        # already; if we stacked the order the other way, total would be the
        # same.) The progressive() function is vectorised below.
        if (ef_interest > 0).any():
            from .tax import progressive_tax, _indexed_brackets
            for i in np.flatnonzero(ef_interest > 0):
                if failed[i]:
                    continue
                brackets_i = _indexed_brackets(case.tax, float(infl_factor[i]))
                tax_int = progressive_tax(float(ef_interest[i]), brackets_i)
                # Cap so EF can't go negative; if it would, draw shortfall from portfolio (rare).
                pay_from_ef = min(tax_int, float(ef[i]))
                ef[i] -= pay_from_ef
                remainder = tax_int - pay_from_ef
                if remainder > 0 and portfolio[i] > 0:
                    portfolio[i] = max(0.0, portfolio[i] - remainder)
                tax_paid[i] += tax_int

        # Record (deflated to real base-year EUR).
        deflate = 1.0 / infl_factor
        total_wealth = (portfolio + ef) * deflate
        total_wealth[failed] = 0.0
        wealth_real[:, y] = total_wealth
        portfolio_w[:, y] = from_pf * deflate
        ef_w[:, y] = from_ef * deflate
        income_r[:, y] = income * deflate
        pension_r[:, y] = pension * deflate
        expenses_r[:, y] = expenses * deflate
        tax_r[:, y] = tax_paid * deflate

    success_rate = float((failure_year < 0).mean())
    return ScenarioReport(
        case=case,
        success_rate=success_rate,
        wealth_real=wealth_real,
        portfolio_withdrawn=portfolio_w,
        ef_withdrawn=ef_w,
        income_received=income_r,
        pension_received=pension_r,
        expenses_paid=expenses_r,
        tax_paid=tax_r,
        realised_returns=nominal_returns,
        realised_inflation=cpi,
        failure_year=failure_year,
    )


def simulate_many(cases: list[Case]) -> list[ScenarioReport]:
    return [simulate(c) for c in cases]
