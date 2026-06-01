"""Aggregations on top of a ScenarioReport."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .case import Case, ScenarioReport

# Real (inflation-adjusted) discount rate for the funded-ratio liability PV.
# This is an ASSUMPTION, not a model output — the funded ratio is sensitive to
# it. 2% real is a deliberately conservative "safe-asset" rate (roughly long-run
# real bond yield), so the ratio leans pessimistic vs. discounting at the
# portfolio's own expected real return.
FUNDED_RATIO_REAL_DISCOUNT = 0.02


@dataclass
class Aggregates:
    success_rate: float
    terminal_wealth_p10: float
    terminal_wealth_p50: float
    terminal_wealth_p90: float
    median_failure_age: float | None
    # Sequence-of-returns risk: real annualised return over the first 10 years,
    # split between failed and successful runs (in % per year).
    early_real_return_failed: float | None
    early_real_return_succeeded: float | None
    # Lifetime tax paid (real, base-year EUR), median.
    median_lifetime_tax: float
    median_effective_tax_rate: float
    # Median first-retirement-year withdrawal rate (gross drawdown / investable
    # wealth at the first deficit year). None if no run ever withdraws.
    median_first_year_wr: float | None
    # Median across surviving runs of each run's single most-stretched year:
    # max(withdrawal_rate) over its lifetime. None if no surviving run withdraws.
    median_peak_wr: float | None
    # Severity of failure: among failed runs, the median number of plan-years
    # left unfunded (failure year → horizon end). None if nothing fails.
    median_years_unfunded: float | None
    # Median (over surviving runs) max peak-to-trough drop in real wealth.
    median_max_drawdown: float | None
    # Investable assets at retirement / PV of future real net spending, median
    # across runs. Depends on FUNDED_RATIO_REAL_DISCOUNT. None if no run retires.
    funded_ratio: float | None
    # Legacy: probability terminal real wealth ends at or above the real pot the
    # plan started with ("die at least as rich as you started, in today's EUR").
    prob_legacy_above_start: float | None
    # Median lifetime net-wealth tax paid (real, base-year EUR). 0.0 when the
    # wealth tax is disabled for the case.
    median_lifetime_wealth_tax: float = 0.0


def _annualised_real_return(nominal: np.ndarray, infl: np.ndarray) -> np.ndarray:
    """Per-run geometric annualised real return over the supplied horizon."""
    real = (1 + nominal) / (1 + infl) - 1.0
    growth = np.prod(1 + real, axis=1)
    n_years = real.shape[1]
    return growth ** (1 / n_years) - 1.0


def aggregate(rep: ScenarioReport) -> Aggregates:
    failed_mask = rep.failure_year >= 0
    success = ~failed_mask

    terminal = rep.wealth_real[:, -1]
    p10, p50, p90 = np.percentile(terminal, [10, 50, 90])

    # Failure age (only for failed runs).
    median_failure_age: float | None = None
    if failed_mask.any():
        ages = rep.case.age + rep.failure_year[failed_mask]
        median_failure_age = float(np.median(ages))

    # Sequence risk: average real return over the first min(10, Y) years.
    horizon = min(10, rep.case.years)
    early_failed: float | None = None
    early_succeeded: float | None = None
    if rep.case.years >= 1:
        early_real = _annualised_real_return(
            rep.realised_returns[:, :horizon], rep.realised_inflation[:, :horizon]
        )
        if failed_mask.any():
            early_failed = float(np.mean(early_real[failed_mask]))
        if success.any():
            early_succeeded = float(np.mean(early_real[success]))

    lifetime_tax = rep.tax_paid.sum(axis=1)
    median_tax = float(np.median(lifetime_tax))
    median_wealth_tax = float(np.median(rep.wealth_tax_paid.sum(axis=1)))
    lifetime_withdrawals = (rep.portfolio_withdrawn + rep.ef_withdrawn + rep.tax_paid).sum(axis=1)
    eff_rate = np.divide(
        lifetime_tax, lifetime_withdrawals,
        out=np.zeros_like(lifetime_tax), where=lifetime_withdrawals > 0,
    )
    median_eff_rate = float(np.median(eff_rate))

    # First-retirement-year WR per run = the first finite entry of each row of
    # the per-year WR series. Median across all runs that ever withdraw is the
    # scenario's "Year-1 WR" (the FIRE 4%-rule analogue).
    series = rep.withdrawal_rate
    finite = np.isfinite(series)
    row_has_wr = finite.any(axis=1)
    first_vals = series[np.arange(rep.case.n_runs), finite.argmax(axis=1)][row_has_wr]
    median_wr: float | None = float(np.median(first_vals)) if first_vals.size else None

    # Peak lifetime WR among surviving runs. Failed runs trivially approach 100%
    # as wealth hits zero, so they're excluded — this measures how stretched the
    # plan got in outcomes that *worked*.
    peak_wr = np.full(rep.case.n_runs, np.nan)
    rows = np.flatnonzero(row_has_wr)
    if rows.size:
        peak_wr[rows] = np.nanmax(series[rows], axis=1)
    peak_pool = peak_wr[success & row_has_wr]
    median_peak_wr: float | None = float(np.median(peak_pool)) if peak_pool.size else None

    # Severity of failure: plan-years left unfunded when a run fails.
    median_years_unfunded: float | None = (
        float(np.median(rep.case.years - rep.failure_year[failed_mask]))
        if failed_mask.any() else None
    )

    # Max real-wealth drawdown (peak-to-trough) among surviving runs.
    w = rep.wealth_real
    running_max = np.maximum.accumulate(w, axis=1)
    drawdown = np.divide(
        running_max - w, running_max, out=np.zeros_like(w), where=running_max > 0
    )
    max_dd = drawdown.max(axis=1)
    median_max_drawdown: float | None = (
        float(np.median(max_dd[success])) if success.any() else None
    )

    funded = _funded_ratio(rep, FUNDED_RATIO_REAL_DISCOUNT)

    initial_real = rep.case.portfolio + rep.case.emergency_fund
    prob_legacy: float | None = (
        float((rep.wealth_real[:, -1] >= initial_real).mean()) if initial_real > 0 else None
    )

    return Aggregates(
        success_rate=rep.success_rate,
        terminal_wealth_p10=float(p10),
        terminal_wealth_p50=float(p50),
        terminal_wealth_p90=float(p90),
        median_failure_age=median_failure_age,
        early_real_return_failed=early_failed,
        early_real_return_succeeded=early_succeeded,
        median_lifetime_tax=median_tax,
        median_effective_tax_rate=median_eff_rate,
        median_first_year_wr=median_wr,
        median_peak_wr=median_peak_wr,
        median_years_unfunded=median_years_unfunded,
        median_max_drawdown=median_max_drawdown,
        funded_ratio=funded,
        prob_legacy_above_start=prob_legacy,
        median_lifetime_wealth_tax=median_wealth_tax,
    )


def _retirement_year(rep: ScenarioReport) -> np.ndarray:
    """First deficit (retirement) year index per run; -1 if a run never withdraws."""
    series = rep.withdrawal_rate
    finite = np.isfinite(series)
    has = finite.any(axis=1)
    r = np.where(has, finite.argmax(axis=1), -1)
    return r


def _funded_ratio(rep: ScenarioReport, discount_rate: float) -> float | None:
    """Median across runs of (real investable wealth at retirement) / (PV of
    future real net spending, discounted at `discount_rate`).

    Measured *at each run's retirement year* — funded ratio is only meaningful
    once drawdown begins; computing it mid-accumulation (assets small, decades
    of spending still ahead) would understate funding. Net spending already
    nets out any post-retirement income (e.g. a barista bridge) and pension.
    Both numerator and denominator are real base-year EUR."""
    net_need_real = np.maximum(
        rep.expenses_paid - rep.income_received - rep.pension_received, 0.0
    )
    Y = rep.case.years
    r_all = _retirement_year(rep)
    ratios: list[float] = []
    for i in range(rep.case.n_runs):
        r = int(r_all[i])
        if r < 0:
            continue
        assets = (
            float(rep.wealth_real[i, r - 1]) if r > 0
            else rep.case.portfolio + rep.case.emergency_fund
        )
        if assets <= 0:
            continue
        disc = (1.0 + discount_rate) ** np.arange(Y - r)
        pv = float((net_need_real[i, r:] / disc).sum())
        if pv > 0:
            ratios.append(assets / pv)
    return float(np.median(ratios)) if ratios else None


def lifetime_wr_percentiles(rep: ScenarioReport, qs=(50, 90)) -> dict[int, np.ndarray]:
    """Per-age withdrawal-rate percentiles across runs that are withdrawing and
    still solvent that year (NaN entries excluded). Ages with no withdrawals
    (accumulation / fully income-covered) are NaN so charts show a gap."""
    series = rep.withdrawal_rate
    out: dict[int, np.ndarray] = {}
    for q in qs:
        col = np.full(rep.case.years, np.nan)
        for y in range(rep.case.years):
            v = series[:, y]
            v = v[np.isfinite(v)]
            if v.size:
                col[y] = np.percentile(v, q)
        out[q] = col
    return out


@dataclass
class SustainableSpending:
    multiple: float          # of the case's planned expenses
    target_success: float    # success rate the multiple was solved for
    achieved_success: float  # success at `multiple` (≈ target, modulo MC noise)


def sustainable_spending(
    case: Case, target_success: float = 0.95, max_multiple: float = 3.0, iters: int = 16
) -> SustainableSpending:
    """Largest uniform multiple of the case's planned expenses whose success
    rate is still ≥ `target_success`, found by bisection.

    Re-simulates with the case's own seed, so the result is deterministic for a
    given case — but it is still a root-find on a Monte-Carlo success rate, so it
    carries the same bootstrap noise as any single success figure (≈ a few tenths
    of a percent). It scales *all* expense streams uniformly, including fixed
    costs like a mortgage, so read it as "what multiple of total planned spend is
    survivable", not "what discretionary lifestyle is affordable"."""
    import dataclasses

    from .simulate import simulate

    def success_at(mult: float) -> float:
        scaled = dataclasses.replace(
            case,
            expenses=[dataclasses.replace(s, amount=s.amount * mult) for s in case.expenses],
        )
        return simulate(scaled).success_rate

    lo, hi = 0.0, max_multiple
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        if success_at(mid) >= target_success:
            lo = mid
        else:
            hi = mid
    return SustainableSpending(
        multiple=lo, target_success=target_success, achieved_success=success_at(lo)
    )


@dataclass
class SpendToZero:
    """Die-with-zero sizing: the largest uniform multiple of the case's planned
    expenses that still leaves the `target_percentile`-th terminal-wealth
    percentile at or above `target_legacy` (real base-year EUR).

    Targeting the *median* (p50 → 0) is the literal "die with zero": it's the
    spend at which half the runs end with nothing — which means half ran out
    *before* death. So `achieved_success` is the honest price of the multiple
    and is reported next to it. Like `sustainable_spending`, it scales every
    expense stream uniformly (mortgage included), so read it as "multiple of
    total planned spend", not "affordable discretionary lifestyle"."""

    multiple: float
    target_percentile: int
    target_legacy: float
    achieved_success: float
    median_terminal: float


def spend_to_zero(
    case: Case,
    target_percentile: int = 50,
    target_legacy: float = 0.0,
    max_multiple: float = 6.0,
    iters: int = 18,
) -> SpendToZero:
    """Bisection on the spend multiple. The chosen terminal-wealth percentile is
    monotonically decreasing in the multiple (more spending → less left), so we
    seek the largest multiple whose percentile is still ≥ `target_legacy`.

    Re-simulates with the case's own seed, so it's deterministic for a given
    case — but it inherits the same bootstrap noise as any single MC figure."""
    import dataclasses

    from .simulate import simulate

    def sim_at(mult: float) -> ScenarioReport:
        scaled = dataclasses.replace(
            case,
            expenses=[dataclasses.replace(s, amount=s.amount * mult) for s in case.expenses],
        )
        return simulate(scaled)

    lo, hi = 0.0, max_multiple
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        terminal = sim_at(mid).wealth_real[:, -1]
        if float(np.percentile(terminal, target_percentile)) > target_legacy:
            lo = mid
        else:
            hi = mid
    rep = sim_at(lo)
    return SpendToZero(
        multiple=lo,
        target_percentile=target_percentile,
        target_legacy=target_legacy,
        achieved_success=rep.success_rate,
        median_terminal=float(np.median(rep.wealth_real[:, -1])),
    )


def wealth_percentiles(rep: ScenarioReport, qs=(10, 25, 50, 75, 90)) -> dict[int, np.ndarray]:
    return {q: np.percentile(rep.wealth_real, q, axis=0) for q in qs}


def withdrawal_breakdown(rep: ScenarioReport) -> dict[str, np.ndarray]:
    """Per-year median withdrawal sources across runs (real EUR)."""
    return {
        "portfolio": np.median(rep.portfolio_withdrawn, axis=0),
        "emergency_fund": np.median(rep.ef_withdrawn, axis=0),
        "income": np.median(rep.income_received, axis=0),
        "pension": np.median(rep.pension_received, axis=0),
        "expenses": np.median(rep.expenses_paid, axis=0),
        "tax": np.median(rep.tax_paid, axis=0),
    }
