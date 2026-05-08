# Data sources

`returns_annual.csv` contains three annual series for an EUR-domiciled
investor: a global stock total return, a EUR-hedged global bond total return,
and Eurozone consumer-price inflation. All values are decimal fractions
(e.g. `0.10` = +10%).

| Column                  | Series                                                    | Coverage  | Source |
|-------------------------|-----------------------------------------------------------|-----------|--------|
| `msci_world_total`      | MSCI World Net Total Return (developed markets), **EUR unhedged** | 1999–2024 | MSCI annual factsheets (`M1WO Index`, EUR Net). |
| `global_agg_bond_total` | Bloomberg Global Aggregate Bond Index, **EUR hedged**     | 1999–2024 | Bloomberg index history (`LEGATREH Index` / EUR-hedged variant). |
| `eurozone_hicp`         | Eurozone HICP, all-items annual inflation rate            | 1999–2024 | Eurostat (`prc_hicp_aind`, all-items index, annual average). |

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

The bootstrap pool is 26 rows (1999–2024). With `block_size: 5` that's about
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

## How the bundled values were obtained

The bundled values are best-effort transcriptions from the public sources
cited in the table above (MSCI factsheets, Bloomberg index history, Eurostat
HICP releases). They are appropriate for educational and planning use but
small revisions (±0.1–0.3pp on individual years) are possible vs. the latest
vendor-published values. To verify or refresh, the `fetch_returns.py` script
pulls EUR-denominated ETF proxies from Yahoo Finance (IWDA for MSCI World,
AGGH for EUR-hedged Global Aggregate). Those ETFs only go back to ~2009 and
~2017 respectively, so the script merges new years over the bundled values
rather than replacing them:

```
pip install -e ".[fetch]"
python scripts/fetch_returns.py
```

For HICP there is no Yahoo proxy; refresh manually from Eurostat
(`prc_hicp_aind`) if you want the most current annual rate.
