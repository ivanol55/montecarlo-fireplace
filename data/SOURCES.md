# Data sources

`returns_annual.csv` contains three annual series for an EUR-domiciled
investor: a global stock total return, a EUR-hedged global bond total return,
and Eurozone consumer-price inflation. All values are decimal fractions
(e.g. `0.10` = +10%).

| Column                  | Series                                                    | Coverage  | Source |
|-------------------------|-----------------------------------------------------------|-----------|--------|
| `msci_world_total`      | MSCI World Net Total Return (developed markets), **EUR unhedged** | 1999–2025 | 1999–2009: MSCI annual factsheets (`M1WO Index`, EUR Net). 2010–2025: IWDA.AS ETF (iShares Core MSCI World UCITS, EUR Acc), via Yahoo Finance. |
| `global_agg_bond_total` | Bloomberg Global Aggregate Bond Index, **EUR hedged**     | 1999–2025 | 1999–2019: Bloomberg index history (`LEGATREH Index` / EUR-hedged variant). 2020–2025: AGGH.MI ETF (iShares Global Aggregate Bond UCITS EUR Hedged), via Yahoo Finance. |
| `eurozone_hicp`         | Eurozone HICP, all-items annual inflation rate            | 1999–2025 | Eurostat dataset `prc_hicp_aind` (geo=EA, unit=RCH_A_AVG, coicop=CP00). Authoritative — fetched directly from Eurostat's JSON API. |

## Why an EUR/Eurozone-HICP regime, and why 1999 onward

The simulator targets a Spanish FIRE setup — typically Indexa Capital fondos
or EUR-domiciled accumulating ETFs (IWDA, AGGH/EUNA), with expenses paid in
EUR. Three things only line up coherently from 1999:

- The **euro existed** as a currency from 1999. Pre-1999 EUR returns are
  reconstructed by vendors using synthetic ECU rates that don't reflect any
  real investor's experience.
- The **Bloomberg Global Aggregate EUR-hedged** index started in 1999. EUR
  hedging on a global bond portfolio is what Indexa actually does (and what
  ETFs like AGGH / EUNA replicate).
- The **ECB monetary regime** began in 1999, targeting ~2% Eurozone HICP.
  Pre-1999 Spain CPI reflects peseta-era policy (autarky shocks, 1970s/80s
  15–25% inflation) that no longer applies.

Earlier versions of this CSV bundled USD-denominated data back to 1970 (and
even US-only series back to 1928). Both were dropped: an EUR investor's
realised return on MSCI World can differ from the USD return by ±10pp/yr from
FX alone, and pre-1999 monetary regimes don't generalise to today.

## Sample size — read this before trusting tail percentiles

The bootstrap pool is 27 rows (1999–2025). With `block_size: 5` that's about
5 effective independent blocks per simulated path. Implications:

- Success-rate **rankings** between scenarios are robust — they share the
  same bootstrap noise.
- **Absolute tail percentiles** (p10 terminal wealth, success rate when below
  ~95%) carry meaningful sampling error. A printed "92% success" should be
  read as "high-80s to mid-90s with this small a pool".
- The pool contains 2008 and 2022 but **no Japan-1990-style lost decade and
  no 1970s stagflation**. The model can't draw a worse event than the worst
  observed year. If you want pessimistic stress tests, supply your own CSV
  via `data_file:` — just keep one regime per simulation, don't mix
  pre-1999 and post-1999 rows in the same bootstrap.

## How the bundled values are sourced and refreshed

Each of the three columns is fetched from its own canonical source by
`scripts/fetch_returns.py`:

- **Stocks** (`msci_world_total`): year-end IWDA.AS closes from Yahoo
  Finance, computed as percentage change of December close (auto-adjusted
  for distributions). ETF inception is late 2009, so the script's
  authoritative coverage starts at 2010. Pre-2010 values come from MSCI
  factsheet best-effort transcriptions and are preserved by the default
  merge mode.
- **Bonds** (`global_agg_bond_total`): same approach via AGGH.MI on Yahoo.
  ETF inception is late 2017, so authoritative coverage starts at 2020.
  Pre-2020 values come from Bloomberg index factsheet transcriptions.
- **Inflation** (`eurozone_hicp`): pulled directly from Eurostat's JSON API
  (`prc_hicp_aind`, Euro area, all-items, annual average rate of change).
  Authoritative for all years 1996+; the script overwrites every overlapping
  year on each refresh.

```
pip install -e ".[fetch]"
python scripts/fetch_returns.py            # default: merge new data
python scripts/fetch_returns.py --full     # rebuild only from authoritative
                                            # sources (drops pre-ETF history)
```

The default `merge` mode is **idempotent** — running it twice in a row on
unchanged upstream data produces no diff. Safe to wire up as a periodic
refresh (e.g. once a quarter, or whenever a year closes).

The `--full` mode is destructive: it discards pre-2010 stocks and pre-2020
bonds because those don't exist in any ETF history. Only use `--full` if
you've added a longer-history data source separately (e.g. a custom CSV
with index-reconstructed pre-ETF values you trust more than the bundled
factsheet transcriptions).

Eurostat's HICP data starts in 1997, so `prc_hicp_aind` returns 1997 and
1998 rows on each fetch. Those rows have no equity/bond data and the
sampler at [src/fireplace/returns.py](../src/fireplace/returns.py) drops
them automatically — they're real Eurostat data, just not usable for the
simulation pool, and they cost nothing.
