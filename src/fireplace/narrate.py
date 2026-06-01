"""Deterministic, templated text summaries — the computed replacement for
hand-written 'Result:' prose.

No AI, no drift: feed an `Aggregates` (and optional `SustainableSpending`) and
get the same text every time for the same numbers. The qualitative "verdict" is
rule-based (success-rate thresholds, the 4%/3.5% FIRE rules, a WR-vs-success
binding-constraint check), so the editorial read stays in sync with the model
instead of depending on someone re-typing it after each run.
"""
from __future__ import annotations

from .report import Aggregates, SustainableSpending

# Success-rate thresholds for the qualitative confidence label.
_HIGH_CONFIDENCE = 0.95
_SOLID = 0.90
_COMFORT_THRESHOLD = 0.85


def _eur(x: float, currency: str) -> str:
    if abs(x) >= 1e6:
        return f"{x / 1e6:.2f}M {currency}"
    if abs(x) >= 1e3:
        return f"{x / 1e3:.0f}k {currency}"
    return f"{x:.0f} {currency}"


def _fire_rules(wr: float | None) -> str:
    """Which FIRE withdrawal-rate rules a year-1 WR clears."""
    if wr is None:
        return "no withdrawals are modeled"
    if wr <= 0.035:
        return "passes both the 4% and 3.5% rules"
    if wr <= 0.04:
        return "passes the 4% rule, fails the 3.5% rule"
    return "fails the 4% rule"


def verdict(agg: Aggregates) -> str:
    """One-to-three sentence qualitative read, derived purely from the numbers."""
    s = agg.success_rate
    parts: list[str] = []
    if s >= _HIGH_CONFIDENCE:
        parts.append(f"High-confidence ({s * 100:.1f}% success).")
    elif s >= _SOLID:
        parts.append(f"Solid ({s * 100:.1f}% success), above the 85% comfort threshold.")
    elif s >= _COMFORT_THRESHOLD:
        parts.append(f"Just above the 85% comfort threshold ({s * 100:.1f}% success).")
    else:
        parts.append(f"Below the 85% comfort threshold ({s * 100:.1f}% success).")

    wr = agg.median_first_year_wr
    if wr is not None:
        parts.append(f"Year-1 WR {_fire_rules(wr)}.")
        # WR looks fine but the plan still isn't a lock → success is what binds.
        if wr <= 0.035 and s < _HIGH_CONFIDENCE:
            parts.append(
                "Sequence/longevity risk (the success rate), not the withdrawal "
                "rate, is the binding constraint."
            )

    if agg.terminal_wealth_p10 > 0:
        parts.append("Even the bottom decile keeps capital.")
    else:
        parts.append("The bottom decile is depleted.")
    return " ".join(parts)


def result_block(
    agg: Aggregates,
    *,
    n_runs: int,
    currency: str = "EUR",
    spend: SustainableSpending | None = None,
) -> str:
    """Render the full computed 'Result' block as plain text.

    This is the deterministic stand-in for the YAML's hand-written Result prose:
    run `fireplace summary <config>` to regenerate it after any model change."""
    p10 = _eur(agg.terminal_wealth_p10, currency)
    p50 = _eur(agg.terminal_wealth_p50, currency)
    p90 = _eur(agg.terminal_wealth_p90, currency)

    wr = f"{agg.median_first_year_wr * 100:.2f}%" if agg.median_first_year_wr is not None else "n/a"
    peak = f"{agg.median_peak_wr * 100:.2f}%" if agg.median_peak_wr is not None else "n/a"
    funded = f"{agg.funded_ratio:.2f}x" if agg.funded_ratio is not None else "n/a"
    legacy = (
        f"{agg.prob_legacy_above_start * 100:.0f}%"
        if agg.prob_legacy_above_start is not None else "n/a"
    )
    dd = f"{agg.median_max_drawdown * 100:.0f}%" if agg.median_max_drawdown is not None else "n/a"

    lines = [
        f"Result ({n_runs:,} runs, auto-generated — `fireplace summary`):",
        f"  Success {agg.success_rate * 100:.1f}%    Year-1 WR {wr}    Peak WR {peak}",
        f"  Terminal real p10/p50/p90: {p10} / {p50} / {p90}",
    ]

    line4 = f"  Funded ratio @ retirement {funded} (2% real)"
    if spend is not None:
        line4 += (
            f"    Sustainable spend {spend.multiple:.2f}x planned "
            f"@ {spend.target_success * 100:.0f}% success"
        )
    lines.append(line4)
    lines.append(f"  Legacy: {legacy} chance of ending >= starting pot    Max drawdown {dd} (median)")

    if agg.median_lifetime_wealth_tax > 0:
        lines.append(
            f"  Wealth tax (Patrimonio/ITSGF): median lifetime "
            f"{_eur(agg.median_lifetime_wealth_tax, currency)} (real)."
        )

    if agg.median_failure_age is not None:
        short = (
            f", ~{agg.median_years_unfunded:.0f} plan-years short"
            if agg.median_years_unfunded is not None else ""
        )
        lines.append(
            f"  Failures: {(1 - agg.success_rate) * 100:.1f}% of runs; "
            f"median failure age {agg.median_failure_age:.0f}{short}."
        )
    else:
        lines.append("  Failures: none.")

    lines.append(f"  Verdict: {verdict(agg)}")
    return "\n".join(lines)
