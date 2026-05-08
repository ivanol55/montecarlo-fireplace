"""Refresh data/returns_annual.csv from authoritative sources.

Pulls all three columns the simulator uses, each from its own canonical
source:

  - msci_world_total       MSCI World Net TR EUR (unhedged), via IWDA.AS
                           (iShares Core MSCI World UCITS ETF, EUR Acc) on
                           Yahoo Finance. ETF inception 2009; pre-2009 years
                           in the bundled CSV come from MSCI factsheets.
  - global_agg_bond_total  Bloomberg Global Aggregate EUR-hedged, via AGGH.MI
                           (iShares Global Aggregate Bond UCITS ETF EUR
                           Hedged) on Yahoo. ETF inception 2017; earlier
                           years from Bloomberg index history.
  - eurozone_hicp          Eurozone HICP all-items annual rate of change,
                           from Eurostat dataset `prc_hicp_aind` (geo=EA,
                           unit=RCH_A_AVG, coicop=CP00). Available 1996+.

ETF-derived data is short, so by default the script merges new years over
the bundled values rather than replacing them. HICP from Eurostat covers
all years 1996+ and is treated as authoritative — every overlapping HICP
value in the CSV is replaced with the Eurostat figure.

Usage:
    pip install -e ".[fetch]"
    python scripts/fetch_returns.py                # merge new years (default)
    python scripts/fetch_returns.py --full         # replace whole CSV (loses pre-ETF history for stocks/bonds)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import yfinance as yf


# Yahoo Finance tickers for EUR-denominated UCITS ETFs. These are what an
# Indexa-style Spanish investor actually holds; their EUR returns include
# the FX effect (for IWDA) or the cost of EUR-hedging (for AGGH).
EQUITY_TICKER = "IWDA.AS"   # iShares Core MSCI World UCITS ETF (Acc, EUR)
BOND_TICKER = "AGGH.MI"     # iShares Global Aggregate Bond UCITS ETF EUR Hedged

# Eurostat JSON API for annual HICP rate of change.
EUROSTAT_HICP_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
    "data/prc_hicp_aind?geo=EA&unit=RCH_A_AVG&coicop=CP00"
)


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


def fetch_eurostat_hicp() -> pd.Series:
    """Eurozone HICP annual rate of change from Eurostat.

    Returns a Series indexed by year, values as decimal fractions
    (e.g. 0.021 for 2.1%). Eurostat publishes the rate as a percentage;
    we divide by 100 to match the CSV convention.
    """
    print(f"  eurostat: prc_hicp_aind (Euro area, all-items, annual avg)")
    try:
        with urllib.request.urlopen(EUROSTAT_HICP_URL, timeout=30) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        sys.exit(f"Failed to fetch Eurostat HICP: {e}")

    # JSON-stat format: `dimension.time.category.index` maps year-string → row index;
    # `value` maps row-index-string → number. Some years can be missing if
    # Eurostat hasn't published yet, so we tolerate gaps.
    time_index = payload.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    values = payload.get("value", {})
    if not time_index or not values:
        sys.exit("Eurostat returned an unexpected payload structure")

    rows: dict[int, float] = {}
    for year_str, idx in time_index.items():
        v = values.get(str(idx))
        if v is None:
            continue
        rows[int(year_str)] = round(v / 100.0, 4)

    series = pd.Series(rows, name="eurozone_hicp").sort_index()
    series.index.name = "year"
    return series


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full", action="store_true",
        help="Replace the entire CSV with fetched data (loses pre-ETF history "
             "for stocks/bonds). Default merges fetched years over existing.",
    )
    args = parser.parse_args()

    print("Fetching annual returns...")
    msci = fetch_annual_total_return(EQUITY_TICKER, "msci_world_total")
    bond = fetch_annual_total_return(BOND_TICKER, "global_agg_bond_total")
    hicp = fetch_eurostat_hicp()
    fresh = pd.concat([msci, bond, hicp], axis=1).sort_index()
    fresh.index.name = "year"

    target = Path(__file__).resolve().parent.parent / "data" / "returns_annual.csv"

    if args.full:
        out = fresh
    else:
        existing = pd.read_csv(target).set_index("year")
        merged = existing.copy()
        # Stocks/bonds: only overwrite where fresh data exists (preserves
        # bundled pre-ETF history). HICP: overwrite everywhere fresh has
        # data, since Eurostat is authoritative for all 1996+ years.
        for col in ("msci_world_total", "global_agg_bond_total", "eurozone_hicp"):
            if col not in fresh.columns:
                continue
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
    # Surface any remaining gaps so the user knows what to investigate.
    for col in ("msci_world_total", "global_agg_bond_total", "eurozone_hicp"):
        if col in out.columns:
            missing = out.index[out[col].isna()]
            if len(missing) > 0:
                print(f"\nNOTE: {col} missing for {list(missing)}.")


if __name__ == "__main__":
    main()
