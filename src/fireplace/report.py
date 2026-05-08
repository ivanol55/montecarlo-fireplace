"""Aggregations on top of a ScenarioReport."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .case import ScenarioReport


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
    lifetime_withdrawals = (rep.portfolio_withdrawn + rep.ef_withdrawn + rep.tax_paid).sum(axis=1)
    eff_rate = np.where(lifetime_withdrawals > 0, lifetime_tax / lifetime_withdrawals, 0.0)
    median_eff_rate = float(np.median(eff_rate))

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
