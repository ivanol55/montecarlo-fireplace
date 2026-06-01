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
           - Otherwise: from portfolio.
  6. If this year's portfolio return ≥ threshold (a "good" year) and EF is
     below `ef_target_months × monthly_expenses`, top up the EF from the
     portfolio. Fires both during accumulation (after a surplus contribution)
     and during retirement (after a deficit withdrawal) — the standard FIRE
     2-bucket policy treats the cash bucket as regenerating insurance, not
     a one-shot buffer. Tax stacking is handled by the same realised-gain
     ledger that the deficit withdrawal already wrote to.
  7. If wealth can't cover the deficit → record failure for this run.

Reported wealth is real (deflated by cumulative CPI factor)."""
from __future__ import annotations

import numpy as np

from .case import Case, ScenarioReport, Stream, TaxConfig
from .returns import load_returns, sample_multicol_paths
from .tax import (
    _indexed_bounds,
    gross_up_for_withdrawal_vec,
    progressive_tax_vec,
    wealth_tax_vec,
)


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

    Returns (net_delivered, tax_paid) arrays, length len(runs).

    `gross_up_for_withdrawal_vec` is the single source of truth for the tax, the
    new cost basis, and the realised gain (which stacks onto this year's
    savings-income ledger for bracket purposes). Runs are mutually independent
    within a call, so the whole batch is solved with one vectorised pass."""
    gross, tax, new_cb, realised = gross_up_for_withdrawal_vec(
        net_needed=net_needed,
        market_value=portfolio[runs],
        cost_basis=cost_basis[runs],
        cfg=tax_cfg,
        inflation_factor=infl_factor[runs],
        prior_savings_income=prior_savings_income[runs],
    )
    portfolio[runs] -= gross
    cost_basis[runs] = new_cb
    prior_savings_income[runs] += realised
    return gross - tax, tax


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

    # Dynamic-spending state (inert unless case.dynamic_spending.enabled).
    disc_factor = np.ones(n)        # per-run discretionary multiplier (ratchets)
    init_wr = np.full(n, np.nan)    # WR fixed at each run's first retirement year
    retired = np.zeros(n, dtype=bool)

    wealth_real = np.zeros((n, Y))
    portfolio_w = np.zeros((n, Y))
    ef_w = np.zeros((n, Y))
    income_r = np.zeros((n, Y))
    pension_r = np.zeros((n, Y))
    expenses_r = np.zeros((n, Y))
    tax_r = np.zeros((n, Y))
    wealth_tax_r = np.zeros((n, Y))

    # Per run-year withdrawal rate (deficit / investable wealth pre-withdrawal).
    # NaN in surplus years and once a run is insolvent. The first non-NaN entry
    # of each row is that run's year-1 (first-retirement) WR; the full series
    # feeds lifetime-WR bands and the peak-WR metric.
    wr_series = np.full((n, Y), np.nan)

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
        # Split expenses into fixed and discretionary. The spending curve (a
        # deterministic age multiplier — the "smile") and the dynamic guardrails
        # (a per-run, portfolio-reactive multiplier) flex only the discretionary
        # part; fixed costs (mortgage, upkeep, care) are never touched.
        fixed_exp = np.zeros(n)
        disc_exp = np.zeros(n)
        for s in case.expenses:
            if s.active(age):
                amt = _stream_amount(s, y, infl_factor)
                if s.discretionary:
                    disc_exp = disc_exp + amt
                else:
                    fixed_exp = fixed_exp + amt
        disc_exp = disc_exp * case.spending_curve.factor_at(age)

        expenses = fixed_exp + disc_exp * disc_factor
        net = income + pension - expenses

        # Dynamic guardrails: fix each run's reference WR on its first deficit
        # (retirement) year, then in later years nudge the discretionary
        # multiplier as the current WR drifts from that reference — cut when the
        # pot is stretched, raise when it's flush. Inert until enabled, but the
        # retirement bookkeeping is harmless either way.
        avail_now = portfolio + ef
        deficit_now = (net < 0) & (~failed)
        safe_avail = np.where(avail_now > 0, avail_now, 1.0)
        wr_now = np.where(deficit_now & (avail_now > 0), -net / safe_avail, np.nan)
        newly_retired = deficit_now & (~retired)
        init_wr[newly_retired] = wr_now[newly_retired]
        retired |= newly_retired
        ds = case.dynamic_spending
        if ds.enabled:
            adj = deficit_now & retired & (~newly_retired) & np.isfinite(init_wr) & (avail_now > 0)
            disc_factor[adj & (wr_now > ds.upper_guard * init_wr)] *= (1.0 - ds.cut)
            disc_factor[adj & (wr_now < ds.lower_guard * init_wr)] *= (1.0 + ds.bump)
            np.clip(disc_factor, ds.floor, ds.ceiling, out=disc_factor)
            expenses = fixed_exp + disc_exp * disc_factor
            net = income + pension - expenses

        # Surplus → contribute to portfolio.
        contrib_mask = (net > 0) & (~failed)
        if contrib_mask.any():
            portfolio[contrib_mask] += net[contrib_mask]
            cost_basis[contrib_mask] += net[contrib_mask]

        # Deficit → withdraw under bucket policy.
        deficit_mask = (net < 0) & (~failed)
        net_need = np.where(deficit_mask, -net, 0.0)

        # Per-year withdrawal rate: (gross need this year) / (investable wealth
        # available to fund it — portfolio + EF, post-return and pre-withdrawal).
        # Numerator and denominator are both in this year's nominal EUR, so the
        # ratio is inflation-invariant. The first non-NaN entry of each run's row
        # is its "Year-1 WR" — the FIRE 4%-rule analogue, computed from the
        # engine's own balances rather than reverse-engineered from the chart.
        avail = portfolio + ef
        active = deficit_mask & (avail > 0)
        wr_series[active, y] = net_need[active] / avail[active]

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

        # Refill EF in good years (regardless of deficit, not failed).
        # Firing in deficit years too means the EF regenerates during
        # retirement instead of being a one-shot buffer — matches Kitces /
        # Pfau 2-bucket policy. The realised-gain ledger
        # (`year_savings_income`) handles tax stacking with any deficit
        # withdrawal that already happened earlier this year.
        good_year = ret >= threshold
        refill_mask = good_year & (~failed) & (portfolio > 0)
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
        # same.)
        ef_tax_mask = (ef_interest > 0) & (~failed)
        if ef_tax_mask.any():
            idx = np.flatnonzero(ef_tax_mask)
            bounds, rates = _indexed_bounds(case.tax, infl_factor[idx])
            tax_int = progressive_tax_vec(ef_interest[idx], bounds, rates)
            # Cap so EF can't go negative; if it would, draw shortfall from
            # portfolio (rare), floored at zero.
            pay_from_ef = np.minimum(tax_int, ef[idx])
            ef[idx] -= pay_from_ef
            remainder = tax_int - pay_from_ef
            draw_pf = np.where((remainder > 0) & (portfolio[idx] > 0), remainder, 0.0)
            portfolio[idx] = np.maximum(0.0, portfolio[idx] - draw_pf)
            tax_paid[idx] += tax_int

        # Annual net-wealth tax (Spain Patrimonio / ITSGF), assessed on year-end
        # net financial wealth above the allowance. Paid from the portfolio,
        # falling back to the EF if the portfolio is short. It only bites above
        # the allowance, so low-wealth runs are untouched and it never pushes a
        # run negative. Kept in its own ledger (`wealth_tax_r`) so it doesn't
        # distort the savings-income effective-tax-rate metric.
        wtax = np.zeros(n)
        if case.wealth_tax.enabled:
            live = ~failed
            if live.any():
                idx = np.flatnonzero(live)
                wt = wealth_tax_vec(
                    portfolio[idx] + ef[idx], case.wealth_tax, infl_factor[idx]
                )
                pay_pf = np.minimum(wt, portfolio[idx])
                portfolio[idx] -= pay_pf
                rem = wt - pay_pf
                pay_ef = np.minimum(rem, ef[idx])
                ef[idx] -= pay_ef
                wtax[idx] = pay_pf + pay_ef

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
        wealth_tax_r[:, y] = wtax * deflate

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
        withdrawal_rate=wr_series,
        wealth_tax_paid=wealth_tax_r,
    )
