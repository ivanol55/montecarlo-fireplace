"""Typed case definition — what a single retirement scenario looks like."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np


@dataclass
class Asset:
    """An asset class backed by one column of the returns CSV."""

    name: str
    series: str  # e.g. "msci_world_total", "global_agg_bond_total"


@dataclass
class GlidePoint:
    """A pivot in a glide path: target weights at a given age."""

    age: int
    weights: dict[str, float]  # asset_name -> target weight, must sum to 1.0


@dataclass
class Allocation:
    """Multi-asset portfolio with optional glide path.

    `glide` has one or more pivots. With one pivot, weights are static.
    With multiple, weights are linearly interpolated between pivots and
    clamped at the endpoints.
    """

    assets: list[Asset]
    glide: list[GlidePoint]

    def __post_init__(self) -> None:
        if not self.assets:
            raise ValueError("Allocation needs at least one asset")
        if not self.glide:
            raise ValueError("Allocation needs at least one glide point")
        names = {a.name for a in self.assets}
        for gp in self.glide:
            extra = set(gp.weights) - names
            if extra:
                raise ValueError(f"Glide point at age {gp.age} references unknown assets: {sorted(extra)}")
            missing = names - set(gp.weights)
            if missing:
                # Fill missing with zero so YAML can omit zero-weight assets.
                for n in missing:
                    gp.weights[n] = 0.0
            total = sum(gp.weights.values())
            if abs(total - 1.0) > 1e-3:
                raise ValueError(f"Glide weights at age {gp.age} sum to {total:.4f}, not 1.0")
        self.glide.sort(key=lambda p: p.age)

    @classmethod
    def single(cls, series: str, name: str = "portfolio") -> "Allocation":
        return cls(
            assets=[Asset(name=name, series=series)],
            glide=[GlidePoint(age=0, weights={name: 1.0})],
        )

    @property
    def series_columns(self) -> list[str]:
        return [a.series for a in self.assets]

    def weights_at(self, age: int) -> dict[str, float]:
        """Linearly interpolate target weights for a given age."""
        if len(self.glide) == 1 or age <= self.glide[0].age:
            return dict(self.glide[0].weights)
        if age >= self.glide[-1].age:
            return dict(self.glide[-1].weights)
        for i in range(len(self.glide) - 1):
            lo, hi = self.glide[i], self.glide[i + 1]
            if lo.age <= age <= hi.age:
                t = (age - lo.age) / (hi.age - lo.age)
                return {
                    a.name: lo.weights[a.name] + t * (hi.weights[a.name] - lo.weights[a.name])
                    for a in self.assets
                }
        return dict(self.glide[-1].weights)  # unreachable


@dataclass
class Stream:
    """A cash flow active over a closed age window, with optional inflation indexing.

    `amount` is in the *base year* (year 0 of the simulation). If `inflate` is True,
    the stream grows with realised inflation each year.
    """

    name: str
    amount: float                  # annual amount in base-year currency
    start_age: int
    end_age: int                   # inclusive
    inflate: bool = True
    growth: float = 0.0            # extra real growth on top of inflation (e.g. raises)
    kind: Literal["expense", "income"] = "expense"

    def active(self, age: int) -> bool:
        return self.start_age <= age <= self.end_age


@dataclass
class TaxConfig:
    """Spain savings-income brackets. Brackets are (upper_bound_eur, marginal_rate).

    The last bracket should have upper_bound = float('inf').
    """

    brackets: list[tuple[float, float]] = field(
        default_factory=lambda: [
            (6_000.0, 0.19),
            (50_000.0, 0.21),
            (200_000.0, 0.23),
            (300_000.0, 0.27),
            (float("inf"), 0.28),
        ]
    )
    # When True, brackets are indexed to inflation each simulated year.
    index_to_inflation: bool = True


@dataclass
class WithdrawalPolicy:
    """How to draw down across the emergency fund and the portfolio."""

    # Pull from EF when the prior-year portfolio nominal return was below this.
    # Default 0.0 = "draw EF in any nominal-loss year".
    bucket_threshold: float = 0.0
    # Refill EF from portfolio in good years up to `ef_target_months * monthly_expenses`.
    ef_target_months: float = 12.0
    # Years where bucket triggers, what fraction of the year's net need can come from EF
    # (capped by EF balance). 1.0 = drain EF first; 0.5 = half EF, half portfolio.
    ef_share_in_bad_year: float = 1.0


@dataclass
class Pension:
    """Government / private pension paid from `start_age` onward (inflation-indexed)."""

    monthly_amount: float = 0.0
    start_age: int = 67
    inflate: bool = True


@dataclass
class Case:
    name: str
    description: str = ""
    age: int = 35
    end_age: int = 90
    currency: str = "EUR"

    # Initial state (base-year currency).
    portfolio: float = 0.0
    emergency_fund: float = 0.0
    portfolio_cost_basis: float | None = None  # if None, assume = portfolio

    # Cashflows.
    incomes: list[Stream] = field(default_factory=list)
    expenses: list[Stream] = field(default_factory=list)
    pension: Pension = field(default_factory=Pension)

    # Inflation: either a constant rate (default — see ECB target) or "bootstrap"
    # to draw from a CPI series in the data file. The bundled CSV no longer
    # includes a CPI column because pre-Eurozone Spanish inflation came from a
    # monetary regime that no longer applies; ECB-targeted ~2% is the sensible
    # forward-looking expectation. Override `data_file` + `inflation_series` if
    # you have a CSV with a CPI column you trust.
    inflation_mode: Literal["constant", "bootstrap"] = "constant"
    inflation_rate: float = 0.02                 # ECB long-run target
    inflation_series: str | None = None          # CSV column for bootstrap mode

    # Returns: which series and how to draw.
    return_mode: Literal["bootstrap", "block_bootstrap"] = "bootstrap"
    return_series: str = "msci_world_total"     # only used if `allocation` is None
    allocation: Allocation | None = None        # explicit multi-asset / glide path
    block_size: int = 5                          # only used for block_bootstrap
    data_file: str | None = None                 # path; None = bundled CSV
    cash_nominal_return: float = 0.0             # EF nominal return (HYSA-like; default 0%)

    tax: TaxConfig = field(default_factory=TaxConfig)
    withdrawal: WithdrawalPolicy = field(default_factory=WithdrawalPolicy)

    # Monte Carlo.
    n_runs: int = 5000
    seed: int | None = 42

    @property
    def years(self) -> int:
        return self.end_age - self.age + 1

    def resolved_allocation(self) -> Allocation:
        """Return `allocation` if set, otherwise a single-asset fallback from `return_series`."""
        return self.allocation or Allocation.single(self.return_series)


@dataclass
class ScenarioReport:
    """Aggregated output of a single scenario across N Monte Carlo runs."""

    case: Case
    success_rate: float
    # Shape (n_runs, years) of real (base-year) total wealth at year-end.
    wealth_real: np.ndarray
    # Withdrawal source per run-year, real EUR. Same shape.
    portfolio_withdrawn: np.ndarray
    ef_withdrawn: np.ndarray
    income_received: np.ndarray
    pension_received: np.ndarray
    expenses_paid: np.ndarray
    tax_paid: np.ndarray
    realised_returns: np.ndarray                  # nominal portfolio returns
    realised_inflation: np.ndarray
    failure_year: np.ndarray                      # int per run; -1 if never failed
