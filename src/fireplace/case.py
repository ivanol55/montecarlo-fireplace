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
    # Discretionary expenses are the ones the spending curve and the dynamic
    # guardrails are allowed to flex. Fixed costs (mortgage, upkeep, care) keep
    # `discretionary=False` so they're never cut. Ignored for income streams.
    discretionary: bool = False

    def active(self, age: int) -> bool:
        return self.start_age <= age <= self.end_age


@dataclass
class CurvePoint:
    """A pivot in a spending curve: a real multiplier at a given age."""

    age: int
    factor: float


@dataclass
class SpendingCurve:
    """Deterministic age-based real multiplier on *discretionary* spending — the
    empirical 'retirement spending smile' (Bernicke / Blanchett): higher in the
    go-go years, tapering through the slow-go/no-go years. Late-life care is
    modelled separately as a non-discretionary stream, so this curve captures
    only the discretionary decline (the care line provides the upward tail).

    Applies identically to every run, interpolated linearly between pivots and
    clamped at the endpoints. Independent of the dynamic guardrails — the two
    multiply together when both are on."""

    enabled: bool = False
    pivots: list[CurvePoint] = field(default_factory=list)

    def factor_at(self, age: int) -> float:
        if not self.enabled or not self.pivots:
            return 1.0
        pts = sorted(self.pivots, key=lambda p: p.age)
        if age <= pts[0].age:
            return pts[0].factor
        if age >= pts[-1].age:
            return pts[-1].factor
        for lo, hi in zip(pts, pts[1:]):
            if lo.age <= age <= hi.age:
                t = (age - lo.age) / (hi.age - lo.age)
                return lo.factor + t * (hi.factor - lo.factor)
        return pts[-1].factor


@dataclass
class DynamicSpending:
    """Guyton-Klinger-style spending guardrails on *discretionary* spending.

    Each retirement year, compare the current withdrawal rate to the rate set
    in the first retirement year. If it has risen past `upper_guard`× that
    initial rate, cut discretionary spending by `cut`; if it has fallen below
    `lower_guard`×, raise it by `bump`. The per-run multiplier ratchets across
    years and is clamped to [`floor`, `ceiling`].

    Raising in good states is what lets a plan spend its right-tail surplus
    instead of dying rich; cutting in bad states is what protects the downside.
    `bump` (not `raise`) avoids the Python keyword."""

    enabled: bool = False
    upper_guard: float = 1.2
    lower_guard: float = 0.8
    cut: float = 0.10
    bump: float = 0.10
    floor: float = 0.5
    ceiling: float = 1.5


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
class WealthTaxConfig:
    """Annual net-wealth tax — Spain's Impuesto sobre el Patrimonio plus the
    solidarity surtax on large fortunes (ITSGF).

    Assessed each year on net *financial* wealth (portfolio + emergency fund)
    above `allowance`, then a progressive scale on the excess. The primary
    residence is excluded, which lines up with both how this model holds the
    home (a mortgage expense stream, not a portfolio asset) and the ~300k
    vivienda-habitual exemption.

    HIGHLY region-dependent. Madrid and (pre-ITSGF) Andalucía bonificate
    Patrimonio to ~zero; the ITSGF then reclaims it only above ~3M. The default
    scale below approximates the combined burden for a *non-bonificated*
    taxpayer (state scale with the solidarity top rates folded in). To model a
    fully-bonificated region, set `enabled: false`. `brackets` are
    (upper_bound_of_excess, marginal_rate); the last bound is +inf. Bounds are
    measured on the base AFTER subtracting `allowance`.
    """

    enabled: bool = False
    allowance: float = 700_000.0
    brackets: list[tuple[float, float]] = field(
        default_factory=lambda: [
            (167_129.45, 0.002),
            (334_252.88, 0.003),
            (668_499.75, 0.005),
            (1_336_999.51, 0.009),
            (2_673_999.01, 0.013),
            (5_347_998.03, 0.017),
            (10_695_996.06, 0.021),
            (float("inf"), 0.035),
        ]
    )
    # Index the allowance and bracket bounds to inflation each simulated year,
    # mirroring the income-tax treatment.
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
class StressRegime:
    """A deterministic 'what-if' regime overlaid on top of the bootstrap — a bad
    outcome the historical data cannot generate on its own (e.g. 1970s-style
    stagflation, absent from a 1997-2025 sample).

    When enabled, the sampled nominal portfolio return and CPI are OVERRIDDEN
    with fixed stress values for a window of `years` starting at `start_age`.
    Every year outside the window keeps its real bootstrapped draw, so the
    historical data is left completely intact except inside the stress window.

    The window is imposed on *every* run, so the resulting success rate is
    CONDITIONAL — "if this regime hits at this age, would the plan survive?" —
    not a probability-weighted forecast. It's a worst-timed stress lens, not a
    prediction. Default disabled: a case with no stress block behaves exactly as
    before, and the real data is never touched."""

    enabled: bool = False
    start_age: int = 42                  # when the bad regime begins
    years: int = 10                      # how long it lasts
    annual_inflation: float = 0.07       # nominal CPI per year in the window
    annual_nominal_return: float = 0.0   # nominal portfolio return per year (→ deeply negative real)


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

    # Inflation: either bootstrap (default — sample CPI from the data file at the
    # *same row index* as that year's return, preserving the historical pairing
    # between bad-return and high-inflation years like 2008 or 2022) or a
    # constant rate. The bundled CSV ships Eurozone HICP from 1999 onward —
    # that's the single Eurozone monetary regime under ECB management, which
    # is the relevant forward-looking distribution for a EUR investor.
    inflation_mode: Literal["constant", "bootstrap"] = "bootstrap"
    inflation_rate: float = 0.02                 # only used in constant mode
    inflation_series: str | None = "eurozone_hicp"  # CSV column for bootstrap mode

    # Returns: which series and how to draw.
    return_mode: Literal["bootstrap", "block_bootstrap"] = "bootstrap"
    return_series: str = "msci_world_total"     # only used if `allocation` is None
    allocation: Allocation | None = None        # explicit multi-asset / glide path
    block_size: int = 5                          # only used for block_bootstrap
    data_file: str | None = None                 # path; None = bundled CSV
    cash_nominal_return: float = 0.0             # EF nominal return (HYSA-like; default 0%)

    tax: TaxConfig = field(default_factory=TaxConfig)
    wealth_tax: WealthTaxConfig = field(default_factory=WealthTaxConfig)
    withdrawal: WithdrawalPolicy = field(default_factory=WithdrawalPolicy)
    # How discretionary spending evolves: a deterministic age curve (the
    # spending smile) and/or portfolio-reactive guardrails. Both default off, so
    # a case with neither behaves exactly as a flat-real plan.
    spending_curve: SpendingCurve = field(default_factory=SpendingCurve)
    dynamic_spending: DynamicSpending = field(default_factory=DynamicSpending)
    # Optional deterministic stress regime (off by default). Never alters the
    # bootstrap outside its own age window.
    stress: StressRegime = field(default_factory=StressRegime)

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
    tax_paid: np.ndarray                          # savings-income tax (real)
    realised_returns: np.ndarray                  # nominal portfolio returns
    realised_inflation: np.ndarray
    failure_year: np.ndarray                      # int per run; -1 if never failed
    # Per run-year withdrawal rate (deficit / investable wealth pre-withdrawal);
    # NaN outside deficit years and after insolvency. Shape (n_runs, years). The
    # first finite entry of each row is that run's first-retirement-year WR.
    withdrawal_rate: np.ndarray
    # Annual net-wealth tax paid per run-year, real EUR. Same shape. All zeros
    # when `case.wealth_tax.enabled` is False. Kept separate from `tax_paid`
    # because it isn't a tax on withdrawals.
    wealth_tax_paid: np.ndarray
