"""Refresh data/returns_annual.csv from Yahoo Finance via yfinance.

Pulls two annual total-return series matching Indexa Capital's main holdings:

  - msci_world_total       MSCI World (developed markets), via URTH
  - global_agg_bond_total  Bloomberg Global Aggregate Bond, via BNDW

ETF-derived data is short (URTH starts 2012, BNDW starts 2018), so this
script only refreshes recent years and leaves older bundled rows intact
unless `--full` is passed (use carefully — pre-ETF history is lost).

Inflation is intentionally not fetched — the simulator defaults to a
constant ECB-target rate; see README "Modelling notes" for rationale.

Usage:
    pip install -e ".[fetch]"
    python scripts/fetch_returns.py                # merge new years only
    python scripts/fetch_returns.py --full         # replace whole CSV
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf


def fetch_annual_total_return(ticker: str, label: str) -> pd.Series:
    """Annual total return derived from monthly adjusted close.

    yfinance's `auto_adjust=True` already incorporates dividends and splits
    into the close price, so percentage change of December close gives total
    return per calendar year. Years where December isn't yet observed are
    dropped (e.g. running this in May would otherwise emit a bogus YTD value).
    """
    print(f"  yfinance: {ticker}")
    hist = yf.Ticker(ticker).history(period="max", interval="1mo", auto_adjust=True)
    if hist.empty:
        sys.exit(f"yfinance returned empty history for {ticker}")
    hist = hist.dropna(subset=["Close"]).copy()
    hist["year"] = hist.index.year
    hist["month"] = hist.index.month
    # Keep only years where we actually have a December observation.
    years_with_december = set(hist.loc[hist["month"] == 12, "year"])
    hist = hist[hist["year"].isin(years_with_december)]
    last_per_year = hist.groupby("year")["Close"].last()
    annual_return = last_per_year.pct_change().dropna()
    annual_return.name = label
    return annual_return.round(4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full", action="store_true",
        help="Replace the entire CSV with Yahoo data (loses pre-ETF history). "
             "Default merges new years only.",
    )
    args = parser.parse_args()

    print("Fetching annual total returns from Yahoo (via yfinance)...")
    msci = fetch_annual_total_return("URTH", "msci_world_total")
    bond = fetch_annual_total_return("BNDW", "global_agg_bond_total")
    fresh = pd.concat([msci, bond], axis=1).sort_index()
    fresh.index.name = "year"

    target = Path(__file__).resolve().parent.parent / "data" / "returns_annual.csv"

    if args.full:
        out = fresh
    else:
        existing = pd.read_csv(target).set_index("year")
        merged = existing.copy()
        for col in ("msci_world_total", "global_agg_bond_total"):
            if col in fresh.columns:
                # Only overwrite where fresh is non-NaN — otherwise we'd nuke
                # bundled values just because Yahoo doesn't go back that far.
                non_null_years = fresh.index[fresh[col].notna()]
                overlap = non_null_years.intersection(existing.index)
                if len(overlap) > 0:
                    merged.loc[overlap, col] = fresh.loc[overlap, col]
        new_years = fresh.index.difference(existing.index)
        if len(new_years) > 0:
            merged = pd.concat([merged, fresh.loc[new_years]]).sort_index()
        out = merged

    out.to_csv(target)
    print(f"\nWrote {len(out)} rows ({out.index.min()}–{out.index.max()}) to {target}")
    print("Non-null counts per column:")
    for col, n in out.notna().sum().items():
        print(f"  {col:<24} {n}")


if __name__ == "__main__":
    main()
