"""Tests for asset allocation, glide path, and correlated sampling."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fireplace.case import Allocation, Asset, Case, GlidePoint, Stream
from fireplace.config import load_config
from fireplace.returns import load_returns, sample_multicol_paths
from fireplace.simulate import simulate


def test_static_allocation_weights_constant():
    a = Allocation(
        assets=[Asset("s", "msci_world_total"), Asset("b", "global_agg_bond_total")],
        glide=[GlidePoint(age=0, weights={"s": 0.6, "b": 0.4})],
    )
    for age in (30, 50, 90):
        w = a.weights_at(age)
        assert w == {"s": 0.6, "b": 0.4}


def test_glide_path_endpoints_and_interpolation():
    a = Allocation(
        assets=[Asset("s", "msci_world_total"), Asset("b", "global_agg_bond_total")],
        glide=[
            GlidePoint(age=30, weights={"s": 0.9, "b": 0.1}),
            GlidePoint(age=80, weights={"s": 0.4, "b": 0.6}),
        ],
    )
    # Below first / above last → clamped.
    assert a.weights_at(20) == {"s": 0.9, "b": 0.1}
    assert a.weights_at(90) == {"s": 0.4, "b": 0.6}
    # Midpoint → exactly halfway.
    mid = a.weights_at(55)
    assert mid["s"] == pytest.approx(0.65)
    assert mid["b"] == pytest.approx(0.35)


def test_glide_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="sum"):
        Allocation(
            assets=[Asset("s", "msci_world_total"), Asset("b", "global_agg_bond_total")],
            glide=[GlidePoint(age=30, weights={"s": 0.5, "b": 0.4})],
        )


def test_glide_unknown_asset_rejected():
    with pytest.raises(ValueError, match="unknown"):
        Allocation(
            assets=[Asset("s", "msci_world_total")],
            glide=[GlidePoint(age=30, weights={"s": 0.5, "x": 0.5})],
        )


def test_correlated_sampling_uses_same_indices():
    """Two columns sampled together should always reflect the same year."""
    df = load_returns()
    cols = ["msci_world_total", "global_agg_bond_total"]
    by_col, _ = sample_multicol_paths(df, cols, n_runs=200, years=20, seed=11)
    # Reconstruct: every (run, year) pair must come from a real (stocks, bond) row
    # in the intersection of available history.
    valid = df.dropna(subset=cols)
    pairs_in_data = set(zip(valid[cols[0]], valid[cols[1]]))
    pairs_in_sample = set(zip(by_col[cols[0]].ravel(), by_col[cols[1]].ravel()))
    assert pairs_in_sample.issubset(pairs_in_data)


def test_60_40_lower_variance_than_all_stocks():
    """Classic textbook: 60/40 has lower spread than 100% stocks."""
    base = dict(
        name="t", age=40, end_age=80,
        portfolio=200_000, emergency_fund=0,
        inflation_mode="constant", inflation_rate=0.02,
        return_mode="bootstrap", n_runs=2000, seed=3,
        expenses=[Stream(name="c", amount=10_000, start_age=40, end_age=80, kind="expense")],
    )
    stocks = simulate(Case(**{**base, "allocation": Allocation(
        assets=[Asset("s", "msci_world_total")],
        glide=[GlidePoint(age=0, weights={"s": 1.0})],
    )}))
    mixed = simulate(Case(**{**base, "allocation": Allocation(
        assets=[Asset("s", "msci_world_total"), Asset("b", "global_agg_bond_total")],
        glide=[GlidePoint(age=0, weights={"s": 0.6, "b": 0.4})],
    )}))
    # Spread of terminal real wealth.
    stocks_iqr = np.percentile(stocks.wealth_real[:, -1], 75) - np.percentile(
        stocks.wealth_real[:, -1], 25
    )
    mixed_iqr = np.percentile(mixed.wealth_real[:, -1], 75) - np.percentile(
        mixed.wealth_real[:, -1], 25
    )
    assert mixed_iqr < stocks_iqr


def test_yaml_allocation_static_form():
    cfg = Path(__file__).resolve().parent.parent / "examples" / "all_stocks.yaml"
    cases = load_config(cfg)
    assert len(cases) == 1
    alloc = cases[0].resolved_allocation()
    assert [a.name for a in alloc.assets] == ["stocks"]
    assert alloc.weights_at(50) == {"stocks": 1.0}


def test_yaml_allocation_glide_form():
    cfg = Path(__file__).resolve().parent.parent / "examples" / "glide_path.yaml"
    cases = load_config(cfg)
    glide = next(c for c in cases if c.name == "glide_path").resolved_allocation()
    # Weights at age 35 match the first pivot.
    assert glide.weights_at(35) == pytest.approx({"stocks": 0.90, "bonds": 0.10})
    # Weights at age 80 match the last pivot.
    assert glide.weights_at(80) == pytest.approx({"stocks": 0.50, "bonds": 0.50})
    # Beyond last pivot → clamped.
    assert glide.weights_at(90) == pytest.approx({"stocks": 0.50, "bonds": 0.50})


def test_yaml_allocation_replaces_defaults():
    """A scenario's `allocation` block must replace defaults', not merge."""
    cfg = Path(__file__).resolve().parent.parent / "examples" / "glide_path.yaml"
    cases = load_config(cfg)
    by_name = {c.name: c for c in cases}
    # all_stocks scenario should have a 1-asset allocation despite glide_path defaults absent.
    s = by_name["all_stocks"].resolved_allocation()
    assert {a.name for a in s.assets} == {"stocks"}
    # static_60_40 should have only stocks+bonds with static weights.
    s2 = by_name["static_60_40"].resolved_allocation()
    assert {a.name for a in s2.assets} == {"stocks", "bonds"}
    assert len(s2.glide) == 1
