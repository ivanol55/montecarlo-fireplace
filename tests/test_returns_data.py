"""Tests for the historical returns CSV and the multi-column sampler."""
from __future__ import annotations

import numpy as np
import pytest

from fireplace.returns import load_returns, sample_multicol_paths


def test_bundled_csv_has_eur_columns():
    df = load_returns()
    for col in ("msci_world_total", "global_agg_bond_total", "eurozone_hicp"):
        assert col in df.columns, f"missing column {col!r}"


def test_bundled_csv_has_no_us_only_columns():
    """US-only and pre-Eurozone columns were intentionally removed: they
    represent monetary regimes that don't apply to a EUR-domiciled investor."""
    df = load_returns()
    for col in ("sp500_total", "us10y_total", "us_cpi", "spain_cpi"):
        assert col not in df.columns, f"removed column {col!r} re-appeared"


def test_pool_starts_in_1999():
    """Euro inception + Bloomberg EUR-hedged inception + ECB regime all
    start in 1999. Earlier years should not exist."""
    df = load_returns()
    assert df["year"].min() == 1999
    for col in ("msci_world_total", "global_agg_bond_total", "eurozone_hicp"):
        assert df[col].notna().all(), f"{col} has gaps inside the 1999+ pool"


def test_sampler_works_with_inflation_col():
    """Sampling all three columns at shared indices should yield no NaNs and
    preserve historical pairings."""
    df = load_returns()
    by_col, infl = sample_multicol_paths(
        df,
        return_cols=["msci_world_total", "global_agg_bond_total"],
        inflation_col="eurozone_hicp",
        n_runs=200,
        years=20,
        seed=42,
    )
    assert not np.isnan(by_col["msci_world_total"]).any()
    assert not np.isnan(by_col["global_agg_bond_total"]).any()
    assert infl is not None and not np.isnan(infl).any()
    # Index sharing: each (run, year) triple must be a real historical row.
    valid = df.dropna(subset=["msci_world_total", "global_agg_bond_total", "eurozone_hicp"])
    real_triples = set(zip(
        valid["msci_world_total"], valid["global_agg_bond_total"], valid["eurozone_hicp"]
    ))
    sampled_triples = set(zip(
        by_col["msci_world_total"].ravel(),
        by_col["global_agg_bond_total"].ravel(),
        infl.ravel(),
    ))
    assert sampled_triples.issubset(real_triples)


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
