"""Tests for the historical returns CSV and the multi-column sampler."""
from __future__ import annotations

import numpy as np
import pytest

from fireplace.returns import load_returns, sample_multicol_paths


def test_bundled_csv_has_world_weighted_columns():
    df = load_returns()
    for col in ("msci_world_total", "global_agg_bond_total"):
        assert col in df.columns, f"missing column {col!r}"


def test_bundled_csv_has_no_us_only_columns():
    """US-only columns were intentionally removed: pre-1970 represented a
    different economic regime that doesn't apply to a EUR-domiciled investor."""
    df = load_returns()
    for col in ("sp500_total", "us10y_total", "us_cpi", "spain_cpi"):
        assert col not in df.columns, f"removed column {col!r} re-appeared"


def test_msci_world_starts_in_1970():
    """MSCI World inception. Earlier years should not exist; 1970+ populated."""
    df = load_returns()
    assert df["year"].min() == 1970
    assert df["msci_world_total"].notna().all()


def test_global_bond_starts_in_1990():
    """Bond series only has data from 1990 onward."""
    df = load_returns()
    pre = df[df["year"] < 1990]
    post = df[df["year"] >= 1990]
    assert pre["global_agg_bond_total"].isna().all()
    assert post["global_agg_bond_total"].notna().all()


def test_sampler_skips_rows_with_nan_in_required_columns():
    """Sampling msci_world_total + global_agg_bond should never read pre-1990 rows."""
    df = load_returns()
    by_col, _ = sample_multicol_paths(
        df,
        return_cols=["msci_world_total", "global_agg_bond_total"],
        inflation_col=None,
        n_runs=200,
        years=20,
        seed=42,
    )
    assert not np.isnan(by_col["msci_world_total"]).any()
    assert not np.isnan(by_col["global_agg_bond_total"]).any()


def test_sampler_intersection_when_columns_have_different_history():
    """msci_world_total starts 1970, global_agg_bond starts 1990 → pool = 1990+."""
    df = load_returns()
    cols = ["msci_world_total", "global_agg_bond_total"]
    by_col, _ = sample_multicol_paths(df, cols, n_runs=200, years=10, seed=42)
    valid = df.dropna(subset=cols)
    assert (valid["year"] >= 1990).all()
    real_pairs = set(zip(valid[cols[0]], valid[cols[1]]))
    sampled_pairs = set(zip(by_col[cols[0]].ravel(), by_col[cols[1]].ravel()))
    assert sampled_pairs.issubset(real_pairs)


def test_sampler_works_without_inflation_col():
    """Constant-inflation simulations don't need a CPI column at all."""
    df = load_returns()
    by_col, infl = sample_multicol_paths(
        df,
        return_cols=["msci_world_total"],
        inflation_col=None,
        n_runs=50,
        years=30,
        seed=1,
    )
    assert infl is None
    assert by_col["msci_world_total"].shape == (50, 30)


def test_sampler_raises_on_unknown_column():
    df = load_returns()
    with pytest.raises(ValueError, match="not in returns CSV"):
        sample_multicol_paths(
            df,
            return_cols=["nonexistent_column"],
            inflation_col=None,
            n_runs=10,
            years=5,
        )
