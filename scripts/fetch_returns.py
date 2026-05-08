"""Refresh data/returns_annual.csv from Yahoo Finance via yfinance.

Pulls two annual total-return series matching what an EUR-domiciled
Indexa Capital / IWDA / AGGH investor actually holds:

  - msci_world_total       MSCI World Net TR EUR (unhedged), via IWDA.AS
                           (iShares Core MSCI World UCITS ETF, EUR Acc)
  - global_agg_bond_total  Bloomberg Global Aggregate EUR-hedged, via AGGH.MI
                           (iShares Global Aggregate Bond UCITS ETF EUR Hedged)

ETF-derived data is short — IWDA starts late 2009, AGGH starts 2017 — so
this script merges new years over the bundled values rather than replacing
them by default. Bundled history goes back to 1999 (the start of the euro
+ Bloomberg EUR-hedged + ECB regime); see data/SOURCES.md.

Inflation (`eurozone_hicp`) is **not** fetched here because Yahoo doesn't
publish it. Refresh manually from Eurostat (`prc_hicp_aind`, all-items
annual rate) if you want the most current year.

Usage:
    pip install -e ".[fetch]"
    python scripts/fetch_returns.py                # merge new years only
    python scripts/fetch_returns.py --full         # replace whole CSV (loses pre-ETF history)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf


# Yahoo Finance tickers for EUR-denominated UCITS ETFs. These are what an
# Indexa-style Spanish investor actually holds; their EUR returns include
# the FX effect (for IWDA) or the cost of EUR-hedging (for AGGH).
EQUITY_TICKER = "IWDA.AS"   # iShares Core MSCI World UCITS ETF (Acc, EUR)
BOND_TICKER = "AGGH.MI"     # iShares Global Aggregate Bond UCITS ETF EUR Hedged


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
    msci = fetch_annual_total_return(EQUITY_TICKER, "msci_world_total")
    bond = fetch_annual_total_return(BOND_TICKER, "global_agg_bond_total")
    fresh = pd.concat([msci, bond], axis=1).sort_index()
    fresh.index.name = "year"

    target = Path(__file__).resolve().parent.parent / "data" / "returns_annual.csv"

    if args.full:
        out = fresh
        print("WARN: --full does not refresh `eurozone_hicp` — that column "
              "will be missing. Restore it manually from Eurostat or run "
              "without --full.")
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
            # New years won't have HICP from Yahoo; leave NaN until the user
            # backfills from Eurostat. The simulator drops NaN rows anyway.
            merged = pd.concat([merged, fresh.loc[new_years]]).sort_index()
        out = merged

    out.to_csv(target)
    print(f"\nWrote {len(out)} rows ({out.index.min()}–{out.index.max()}) to {target}")
    print("Non-null counts per column:")
    for col, n in out.notna().sum().items():
        print(f"  {col:<24} {n}")
    if "eurozone_hicp" in out.columns:
        missing_hicp = out.index[out["eurozone_hicp"].isna()]
        if len(missing_hicp) > 0:
            print(f"\nNOTE: eurozone_hicp missing for {list(missing_hicp)}.")
            print("Refresh from Eurostat `prc_hicp_aind` (all-items annual).")


if __name__ == "__main__":
    main()
