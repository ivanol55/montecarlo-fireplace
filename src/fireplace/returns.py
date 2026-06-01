"""Historical-return loading and bootstrap sampling."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd


def _bundled_csv_path() -> Path:
    # Resolve via package resource so it works installed or from source.
    pkg_root = Path(__file__).resolve().parent.parent.parent
    candidate = pkg_root / "data" / "returns_annual.csv"
    if candidate.exists():
        return candidate
    # Installed wheel: fall back to importlib resources.
    return Path(str(files("fireplace").joinpath("..", "data", "returns_annual.csv")))


def load_returns(data_file: str | None = None) -> pd.DataFrame:
    path = Path(data_file) if data_file else _bundled_csv_path()
    df = pd.read_csv(path)
    if "year" not in df.columns:
        raise ValueError(f"{path}: missing `year` column")
    return df.sort_values("year").reset_index(drop=True)


def _sample_indices(
    n_rows: int,
    n_runs: int,
    years: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Indices into the returns table, shape (n_runs, years).

    block_size=1 → IID bootstrap. block_size>1 → moving-block bootstrap, which
    preserves short-run autocorrelation (e.g. inflation persistence).
    """
    if block_size <= 1:
        return rng.integers(0, n_rows, size=(n_runs, years))
    nblocks = (years + block_size - 1) // block_size
    starts = rng.integers(0, n_rows - block_size + 1, size=(n_runs, nblocks))
    offsets = np.arange(block_size)
    return (starts[:, :, None] + offsets[None, None, :]).reshape(n_runs, -1)[:, :years]


def sample_multicol_paths(
    df: pd.DataFrame,
    return_cols: list[str],
    inflation_col: str | None = None,
    n_runs: int = 1000,
    years: int = 60,
    block_size: int = 1,
    seed: int | None = None,
) -> tuple[dict[str, np.ndarray], np.ndarray | None]:
    """Sample multiple return columns at the **same** year indices.

    If `inflation_col` is provided, also samples that column at the same
    indices (and includes it in the dropna filter); otherwise returns `None`
    for the inflation array. The simulator passes `None` when running in
    constant-inflation mode.

    Rows with missing values in any of the requested columns are dropped
    before sampling — different series start in different years (e.g. MSCI
    World begins in 1970, Bloomberg Global Agg in 1990), so the effective
    bootstrap pool is the intersection of available history.

    Sharing indices across columns preserves the historical correlation
    structure (e.g. 2008's bad equity year pairs with that year's bond return).
    """
    if not return_cols:
        raise ValueError("Need at least one return column")
    needed_filter = list(dict.fromkeys([*return_cols, inflation_col]))
    needed_filter = [c for c in needed_filter if c is not None]
    missing = [c for c in needed_filter if c not in df.columns]
    if missing:
        raise ValueError(f"Columns {missing} not in returns CSV ({list(df.columns)})")
    df_filtered = df.dropna(subset=needed_filter).reset_index(drop=True)
    n = len(df_filtered)
    if n == 0:
        raise ValueError(
            f"No rows have data for all of {needed_filter!r}. Check column names and CSV coverage."
        )
    rng = np.random.default_rng(seed)
    idx = _sample_indices(n, n_runs, years, block_size, rng)
    out = {col: df_filtered[col].to_numpy()[idx] for col in return_cols}
    infl = df_filtered[inflation_col].to_numpy()[idx] if inflation_col else None
    return out, infl
