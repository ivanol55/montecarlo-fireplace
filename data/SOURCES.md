# Data sources

`returns_annual.csv` contains two annual nominal total-return series — the
ones an Indexa-Capital-style EUR-domiciled investor actually holds. All
values are decimal fractions (e.g. `0.10` = +10%). Where a series didn't
exist for a given year, the cell is empty and the bootstrap sampler skips
that row when that column is needed.

| Column                  | Series                                              | Coverage | Source |
|-------------------------|-----------------------------------------------------|----------|--------|
| `msci_world_total`      | MSCI World total return (developed markets), USD    | 1970–2023 | MSCI End-of-Day index data (public annual returns). |
| `global_agg_bond_total` | Bloomberg Global Aggregate Bond Index total return  | 1990–2023 | Bloomberg published index history. |

## Why only these two

Earlier versions of this CSV bundled US-only series back to 1928 (S&P 500,
10y Treasury, US CPI) and a Spain CPI series back to 1970. Both were removed
because they represent **economic regimes that don't apply to a Spanish
investor today**:

- **1928–1969 US data** is from the gold standard, Bretton Woods, and pre-EU
  Spain — different monetary regime, different exchange-rate dynamics, and
  not what an Indexa Capital portfolio is exposed to.
- **Pre-1999 Spain CPI** reflects peseta-era monetary policy (autarky shocks,
  1970s/80s 15–25% inflation). Post-1999 Spain inflation is just Eurozone
  inflation managed by the ECB targeting 2%. The pre-Eurozone history would
  pull the Monte Carlo's inflation distribution toward a regime that no
  longer exists.

Sample size is the genuine cost. With MSCI World 1970–2023 (54 years) and
Bloomberg Global Aggregate 1990–2023 (34 years), the bond-containing
bootstrap pool is the intersection (34 years). With `block_size: 5` that's
~7 independent blocks, which is thin. If you want longer-history stress
tests you can add columns to a custom CSV via `data_file:` in your YAML —
just don't mix regimes within the same simulation.

## Inflation

The simulator defaults to a constant 2% inflation rate (ECB long-run
target). For a EUR-domiciled investor with EUR expenses, that's the most
defensible forward-looking assumption. To bootstrap inflation instead,
provide a CSV with your own CPI column via `data_file:` and set
`inflation_mode: bootstrap` + `inflation_series: <column>` in your case.

## How the bundled values were obtained

Best-effort transcriptions from the public sources cited above. The values
are appropriate for educational and planning use but may differ from the
latest source revision by small amounts due to historical re-statements or
index methodology changes.

To refresh recent years from Yahoo Finance ETF proxies (URTH for MSCI World,
BNDW for Global Aggregate Bond):

```
pip install -e ".[fetch]"
python3 scripts/fetch_returns.py
```

By default the script merges recent ETF-era years into the existing CSV,
preserving older bundled values. Pass `--full` to replace entirely (this
loses pre-2012 history because URTH only goes back that far).
