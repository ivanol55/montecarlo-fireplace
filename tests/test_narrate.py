"""The templated summary is a pure function of the numbers — deterministic and
threshold-driven, with no AI in the loop."""
from __future__ import annotations



from fireplace.narrate import _fire_rules, result_block, verdict
from fireplace.report import Aggregates


def _agg(**overrides) -> Aggregates:
    base = dict(
        success_rate=0.95,
        terminal_wealth_p10=100_000.0,
        terminal_wealth_p50=5_000_000.0,
        terminal_wealth_p90=40_000_000.0,
        median_failure_age=None,
        early_real_return_failed=None,
        early_real_return_succeeded=None,
        median_lifetime_tax=100_000.0,
        median_effective_tax_rate=0.16,
        median_first_year_wr=0.02,
        median_peak_wr=0.027,
        median_years_unfunded=None,
        median_max_drawdown=0.6,
        funded_ratio=2.2,
        prob_legacy_above_start=0.9,
    )
    base.update(overrides)
    return Aggregates(**base)


def test_determinism():
    """Same numbers in → byte-identical text out."""
    a = _agg()
    assert result_block(a, n_runs=5000) == result_block(a, n_runs=5000)


def test_fire_rule_thresholds():
    assert _fire_rules(0.030) == "passes both the 4% and 3.5% rules"
    assert _fire_rules(0.038) == "passes the 4% rule, fails the 3.5% rule"
    assert _fire_rules(0.045) == "fails the 4% rule"
    assert _fire_rules(None) == "no withdrawals are modeled"


def test_confidence_labels():
    assert "High-confidence" in verdict(_agg(success_rate=0.97))
    assert "Solid" in verdict(_agg(success_rate=0.91))
    assert "comfort threshold" in verdict(_agg(success_rate=0.86))
    assert "Below the 85%" in verdict(_agg(success_rate=0.80))


def test_binding_constraint_flag():
    """Comfortable WR but sub-95% success → success is named the binding constraint."""
    v = verdict(_agg(success_rate=0.84, median_first_year_wr=0.025))
    assert "binding constraint" in v
    # High success → no binding-constraint caveat even with a low WR.
    assert "binding constraint" not in verdict(_agg(success_rate=0.98, median_first_year_wr=0.025))


def test_bottom_decile_language():
    assert "bottom decile keeps capital" in verdict(_agg(terminal_wealth_p10=50_000.0))
    assert "bottom decile is depleted" in verdict(_agg(terminal_wealth_p10=0.0))


def test_block_contains_key_figures():
    block = result_block(_agg(success_rate=0.904, median_first_year_wr=0.0216,
                              median_peak_wr=0.0273, funded_ratio=2.22), n_runs=5000)
    assert "Success 90.4%" in block
    assert "Year-1 WR 2.16%" in block
    assert "Peak WR 2.73%" in block
    assert "2.22x" in block
    assert "Verdict:" in block


def test_failure_line_present_when_failures():
    block = result_block(
        _agg(success_rate=0.84, median_failure_age=64, median_years_unfunded=27), n_runs=5000
    )
    assert "median failure age 64" in block
    assert "27 plan-years short" in block
