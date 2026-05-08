# fireplace

Monte Carlo retirement simulator with bucket withdrawals, Spain-aware tax, and
pluggable expense windows. Inspired by [thefire.site](https://es.thefire.site/calculadora-retiro-temprano/),
extended for variance, multi-scenario comparison, and historical-bootstrap returns.

![Scenario comparison report](docs/img/hero.png)

## What it does

For a given case (you, your portfolio, your expense plan) it runs N independent
Monte Carlo paths against bootstrapped historical returns + CPI, applies a
two-bucket withdrawal strategy, models Spain savings-income tax with FIFO
cost basis and bracket stacking, and reports:

- **Success rate** (fraction of runs that don't run out of money before `end_age`)
- **Terminal wealth distribution** (p10 / p50 / p90)
- **Sequence-of-returns risk decomposition** — how the early decade looks in
  failed vs successful runs
- **Withdrawal-source breakdown** (per-year median: portfolio, EF, income, pension)
- **Tax drag** — lifetime tax paid and effective rate on withdrawals

Multiple scenarios share a single YAML file via inheritance, so you can compare
"baseline vs lean-FIRE vs work-five-more-years" in one report.

### Inside the report

Wealth fan chart — median, p25–p75 and p10–p90 bands of real wealth over time:

![Wealth fan chart](docs/img/wealth-fan.png)

Annual cashflows — where each year's spending is sourced from (income, pension,
portfolio, EF) and the tax line on the secondary axis:

![Annual cashflows](docs/img/cashflows.png)

Terminal wealth distribution and the age at which failed runs ran out:

![Terminal wealth and failure-age histograms](docs/img/tails.png)

## Quick start

Install:

```bash
git clone <this repo> && cd montecarlo-fireplace
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"
```

Run a static HTML report:

```bash
fireplace run examples/spain_default.yaml -o out/report.html
open out/report.html
```

Or an interactive Streamlit app — tweak any input and re-run live:

```bash
fireplace streamlit examples/spain_default.yaml
```

![Streamlit app](docs/img/streamlit.png)

List the scenarios defined in a config:

```bash
fireplace list examples/spain_default.yaml
```

## Configuring your case

A YAML file declares `defaults` (a partial Case) and `scenarios` (each a
partial Case overriding defaults). Income and expense streams have explicit
age windows and an inflate flag — that's how the source site models a mortgage
that ends at age 54 or a lifestyle that uplifts in retirement.

Full field reference: [docs/CONFIG.md](docs/CONFIG.md). The example below shows
the most common bits.

```yaml
defaults:
  age: 35
  end_age: 90
  portfolio: 120000
  emergency_fund: 12000
  inflation_mode: constant
  inflation_rate: 0.02             # ECB long-run target
  return_series: msci_world_total  # bootstrap from data/returns_annual.csv
  tax:                             # Spain savings-income brackets
    brackets:
      - [6000, 0.19]
      - [50000, 0.21]
      - [200000, 0.23]
      - [300000, 0.27]
      - [.inf, 0.28]
  withdrawal:
    bucket_threshold: 0.0          # EF kicks in if portfolio return < 0
    ef_target_months: 12
  incomes:
    - { name: salary,   amount: 42000, start_age: 35, end_age: 49, inflate: true,  growth: 0.005 }
  expenses:
    - { name: living,   amount: 18000, start_age: 35, end_age: 90, inflate: true  }
    - { name: mortgage, amount: 12000, start_age: 35, end_age: 54, inflate: false }

scenarios:
  - name: baseline
  - name: lean
    expenses:
      - { name: living, amount: 14000 }    # streams merge by name
  - name: work_5_more
    incomes:
      - { name: salary, end_age: 54 }
```

### Asset allocation

By default a case has one asset (whatever `return_series` points at). The
`allocation` block lets you describe a portfolio as a mix of assets, optionally
with a glide path that shifts weights as you age. Returns are sampled at the
**same year indices** across assets, so historical correlations (e.g. 2008's
equity drawdown paired with that year's bond return) are preserved.

Two forms — static weights:

```yaml
allocation:
  assets:
    stocks: msci_world_total
    bonds:  global_agg_bond_total
  weights:
    stocks: 0.6
    bonds:  0.4
```

…or a glide path (linear interpolation between pivots, clamped at the ends):

```yaml
allocation:
  assets:
    stocks: msci_world_total
    bonds:  global_agg_bond_total
  glide_path:
    - { age: 35, stocks: 0.90, bonds: 0.10 }
    - { age: 50, stocks: 0.70, bonds: 0.30 }
    - { age: 65, stocks: 0.60, bonds: 0.40 }
    - { age: 80, stocks: 0.50, bonds: 0.50 }
```

The simulator assumes **annual rebalancing back to the target weights** with
no tax cost from the rebalance itself — accurate for fondos de inversión held
inside Indexa Capital (Spain's *traspaso* rule lets you move between fondos
without realising gains). Withdrawals still hit Spanish savings-tax via FIFO
basis as before.

Inside scenarios, the `allocation` block is **replaced wholesale** rather than
merged with `defaults` — pick one form per scenario.

Three example configs ship with allocation already wired up:

- [examples/all_stocks.yaml](examples/all_stocks.yaml) — 100% equities forever
- [examples/glide_path.yaml](examples/glide_path.yaml) — three scenarios in
  one report (`all_stocks` vs `static_60_40` vs `glide_path`) so you can see
  how variance, success rate, and tax drag move with allocation.
- [examples/indexa.yaml](examples/indexa.yaml) — the typical Spanish FIRE
  setup: Indexa Capital fondos at three risk profiles (conservative / balanced /
  aggressive) plus a DIY glide-path overlay, all funded against a 12k cuenta
  remunerada at 2.25%, with Seguridad Social pension from age 67.

The glide-path config produces a side-by-side comparison of three allocations:

![Three-allocation scenario comparison from glide_path.yaml](docs/img/comparison.png)

## Spain-specific guide

The model is specifically aimed at the typical Spanish FIRE setup. The two
buckets map to:

- **`portfolio`** = your fondos de inversión (e.g. an Indexa Capital plan).
  Internal rebalancing inside Indexa uses Spain's *traspaso* rule — switching
  between fondos doesn't realise gains, only withdrawals do. The simulator
  rebalances annually back to your target weights at no tax cost, which
  matches Indexa's reality. Withdrawals trigger Spanish savings-income tax on
  the realised-gain portion under FIFO basis.
- **`emergency_fund`** = your cuenta remunerada (Trade Republic, MyInvestor,
  Openbank, Revolut…). Earns nominal interest at `cash_nominal_return`. The
  interest is taxed each year at the same savings-income brackets, and stacks
  with any portfolio gains realised the same year — so taking a big portfolio
  withdrawal in a year your cuenta paid 1k of interest pushes the gain euros
  into a slightly higher bracket, just like in real life.

### Picking `cash_nominal_return` (the cuenta-remunerada rate)

`cash_nominal_return` is the **gross annual yield** your cuenta remunerada
pays. Net of tax is computed for you — don't pre-subtract the 19/21/23%.

The default in [examples/spain_default.yaml](examples/spain_default.yaml) is
**0.0225 (2.25%)**, picked as a representative mid-range gross yield in Spain
in the mid-2020s. Realistic ranges to override with:

| Provider type                                    | Typical gross APY | When to use |
|--------------------------------------------------|--------------------|-------------|
| Cuenta corriente (regular checking)              | 0.00 – 0.50%       | If your "EF" is just sitting in your nómina account. |
| Mainstream bank promo (BBVA, Santander, ING)     | 1.00 – 2.50%       | First 6 months only — drops after, model an average. |
| Trade Republic / Revolut Premium                 | 1.50 – 2.75%       | Variable, tracks ECB ESTR closely. |
| MyInvestor / Openbank / Pibank                   | 1.50 – 3.00%       | More stable promo rates over multi-year horizons. |
| Letras del Tesoro 12m (rolling)                  | 2.00 – 4.00%       | If you ladder Spanish T-bills as your "EF". Less liquid but higher yield, no withholding. |

What to actually plug in: take your bank's current gross APY, multiply by
a "stickiness factor" if it's a promo (e.g. 0.7 for a 12-month promo that
will revert to 0.5%) to get an effective long-run yield. ECB-tracking
accounts move with rates over decades, so picking ~ECB-rate-minus-50bp is a
defensible long-run estimate. Two examples:

- *"My EF earns 2.6% currently at MyInvestor, but rates were near 0% from
  2015–2022."* → use `0.018` (~1.8%) as your long-run plug.
- *"I roll Letras del Tesoro at ~3% gross right now."* → use `0.025` (~2.5%).

The simulator taxes the interest each year at Spanish savings-income brackets
and *stacks it with portfolio gains realised the same year*, so a higher
`cash_nominal_return` doesn't just mean more interest — it also pushes
portfolio-withdrawal euros into higher brackets in the years you do both.

## Modelling notes

- **Returns**: bootstrapped from `data/returns_annual.csv` — two columns,
  MSCI World total return (1970+) and Bloomberg Global Aggregate Bond
  (1990+). Both are what an Indexa Capital portfolio actually holds. Use
  `block_bootstrap` with `block_size: 5` to preserve some autocorrelation.
  See [data/SOURCES.md](data/SOURCES.md) for why pre-1970 US series and
  pre-1999 Spain CPI were intentionally excluded. Refresh recent years from
  Yahoo Finance via `python scripts/fetch_returns.py`.
- **Inflation**: defaults to a **constant 2%** (ECB long-run target). For a
  EUR-domiciled investor whose expenses are in EUR, that's the most
  defensible forward-looking assumption — pre-Eurozone Spanish inflation
  reflects monetary regimes that no longer apply, so we don't bootstrap it.
  If you have your own CSV with a CPI column you trust, set
  `inflation_mode: bootstrap` + `inflation_series: <column>` + `data_file:`.
- **Tax**: each portfolio withdrawal grosses up against Spain's progressive
  savings-income brackets (19/21/23/27/28%), applied to the realised-gain
  portion of the withdrawal under FIFO cost basis. Cuenta-remunerada interest
  is taxed yearly under the same brackets and *stacks* with portfolio gains in
  the same year, so a big portfolio withdrawal in an interest-heavy year pays
  marginally more. Brackets index to inflation by default. Withdrawing
  principal from the cuenta remunerada itself is not taxed.
- **Bucket strategy**: in years where the realised portfolio return is below
  `bucket_threshold`, the simulator first draws `ef_share_in_bad_year × deficit`
  from the EF (capped by EF balance) and tops up from the portfolio. In good
  years it can refill the EF toward `ef_target_months × monthly_expenses`.
- **Real vs nominal**: all internal accounting is nominal; reports are deflated
  to base-year ("real") EUR using each run's realised CPI.
- **Offline reports**: HTML output inlines plotly.js, so it opens with no
  network and is safe to email / commit / archive.

## Limitations (v1)

- Rebalancing inside the portfolio is treated as costless. This is accurate
  for fondos de inversión held inside Indexa Capital (traspaso rule). It is
  *not* accurate if you hold ETFs directly in a Spanish broker — there each
  rebalance trade realises gains. If that's your setup, model it manually by
  adding extra realised gains as expenses, or open an issue.
- No wealth tax (Impuesto sobre el Patrimonio) / IRPF on labour income /
  regional Spain variants
- Pension is a fixed inflation-indexed monthly amount (no SS replacement-rate model)
- Real-estate, mortgage interest, and one-off expenses must be modelled as
  custom expense streams

## Appendix: how the math works

Everything below is implemented in `src/fireplace/`. The pointers in
parentheses tell you where to look. Skip this section unless you want to
audit the numbers or extend the model.

### 1. Bootstrap sampling ([`returns.py`](src/fireplace/returns.py))

Given a historical table of `n` years (rows), we sample year indices
`i₁, …, i_T` from `{0, …, n-1}`:

- **IID bootstrap** (`block_size = 1`): each `i_y` is uniform random over the
  pool. Treats years as independent — fine for equity, wrong-ish for inflation
  and bonds where adjacent years are correlated.
- **Moving-block bootstrap** (`block_size = B`): pick `⌈T/B⌉` start indices
  uniformly in `{0, …, n-B}`, then read consecutive windows of length `B`,
  concatenate, truncate to `T`. Preserves short-run autocorrelation. Standard
  choice for return-and-inflation series; we recommend `B = 5`.

For each `(run, year)` we read **every needed column at the same `i_y`** so
historical correlations are preserved exactly: 2008's MSCI World draw of −40.7%
will always be paired with that year's bond and CPI numbers.

When you specify multiple columns whose history starts in different years
(e.g. `msci_world_total` from 1970, `global_agg_bond_total` from 1990), the
sampler drops rows where any needed column is missing and bootstraps from the
intersection. So a 60/40 stocks/bonds configuration draws from 1990–2023
(where Bloomberg Global Aggregate has data) even though MSCI World goes back
to 1970.

### 2. Multi-asset weighted return ([`simulate.py`](src/fireplace/simulate.py))

Per `(run, year)`, the portfolio's nominal return is the dot product of
target weights and per-asset returns:

```
r(year) = Σᵢ wᵢ(age) · rᵢ(year)
```

This formulation is mathematically equivalent to **costless annual rebalancing
back to target weights**: at year start each asset holds `wᵢ · V` of capital;
each grows by `rᵢ`; at year end you rebalance back to `wᵢ`. Over the year:

```
V(year+1) = V(year) · (1 + Σᵢ wᵢ · rᵢ) = V(year) · (1 + r(year))
```

For Indexa Capital users this matches reality (traspaso rule = no realised
gain on internal rebalancing). For DIY ETF holders in a taxable Spanish broker
it's optimistic — see Limitations.

### 3. Glide-path interpolation ([`case.py:Allocation.weights_at`](src/fireplace/case.py))

Pivots are sorted by age. For age `a` between pivots `(a₁, w₁)` and `(a₂, w₂)`:

```
w(a) = w₁ + (w₂ − w₁) · (a − a₁) / (a₂ − a₁)
```

Below the first pivot or above the last: clamp to the endpoint. Each
component is interpolated independently; if the pivots themselves sum to 1
(validated at construction), interpolated weights also sum to 1.

### 4. FIFO cost basis ([`tax.py`](src/fireplace/tax.py))

Spain mandates FIFO ("primera entrada, primera salida") for fund shares. We
track a single aggregate cost basis `CB` for the portfolio. When selling
gross `G` from a portfolio with market value `V`:

```
gain_fraction = max(0, (V − CB) / V)
realised_gain = G · gain_fraction
cost_portion  = G · (1 − gain_fraction)
new_CB        = CB − cost_portion
```

This is the **proportional** approximation to lot-by-lot FIFO. It's exact
when contributions and growth are uniform; it slightly under-taxes when
recent contributions have unrealised gains compared to older lots, and
slightly over-taxes in the opposite case. For a long-horizon retirement
plan the error is well under the noise from Monte Carlo and bootstrap.

### 5. Progressive savings tax with stacking ([`tax.py`](src/fireplace/tax.py))

Spain's `base imponible del ahorro` combines all savings income — fund-gain
realisations, dividends, interest from cuentas remuneradas, etc. — into one
progressive bracket schedule (currently 19/21/23/27/28%).

`progressive_tax(x, B)` walks the brackets from zero and sums the slabs:

```
progressive_tax(x, [(b₁, r₁), (b₂, r₂), …]) = 
    Σₖ (min(x, bₖ) − bₖ₋₁)⁺ · rₖ      with b₀ = 0
```

When multiple operations happen in the same year (cuenta interest realises
in January, portfolio withdrawal in March, EF refill in November), each
later euro stacks on top of the prior ones. The marginal tax on `extra`
stacked above `prior` is:

```
tax_on_extra(extra | prior) = progressive_tax(prior + extra, B) − progressive_tax(prior, B)
```

This is exact, not an approximation. The simulator maintains a per-run
`year_savings_income` that grows as each operation lands.

### 6. Gross-up Newton iteration ([`tax.py:gross_up_for_withdrawal`](src/fireplace/tax.py))

When the simulator needs `net_needed` of cash and must sell from the
portfolio, it solves for the gross withdrawal `G` such that

```
G − tax_on_extra(G · gain_fraction | prior) = net_needed
```

Define `f(G) = G − [progressive_tax(prior + G · gain_fraction, B) − progressive_tax(prior, B)]`.
Then `f` is piecewise linear in `G`, monotonically increasing, with derivative

```
f'(G) = 1 − marginal_rate(prior + G · gain_fraction) · gain_fraction
```

Newton step: `G ← G + (net_needed − f(G)) / f'(G)`. Converges in under 10
iterations because the function is piecewise linear; the initial guess uses
the marginal rate at `prior`. We cap `G ≤ V` (can't withdraw more than the
portfolio holds).

### 7. Bucket withdrawal strategy ([`simulate.py`](src/fireplace/simulate.py))

In each year, after computing the deficit `D = expenses − income − pension`:

- If `D > 0` (deficit) **and** the portfolio's nominal return that year was
  below `bucket_threshold` **and** EF balance > 0:
  - Take `min(D · ef_share_in_bad_year, EF)` from the cuenta remunerada
  - Take the remainder from the portfolio (with gross-up)
- Otherwise: take it all from the portfolio.

In good years with no deficit, refill EF toward `ef_target_months ·
monthly_expenses` if it's below target, pulling from the portfolio (taxable).

The intuition: drawing the EF in bad years avoids selling depressed equities,
mitigating sequence-of-returns risk. Refilling in good years is mechanical
"sell high".

### 8. Real vs nominal ([`simulate.py`](src/fireplace/simulate.py))

All internal accounting is **nominal**. The cumulative inflation factor for
a run after `y` years is

```
π(y) = Πₜ≤y (1 + cpi_t)
```

Reported real wealth deflates each (run, year) value by its own `π(y)`:

```
V_real(y) = V_nominal(y) / π(y)
```

This means each Monte Carlo path uses *its own realised inflation* to
deflate, not a single shared inflation series. That preserves the
correlation between bad-return years and high-inflation years that
historically tend to come together (1973–74, 2022).

### 9. Sequence-of-returns risk metric ([`report.py`](src/fireplace/report.py))

For each run, compute the geometric annualised real return over the first
`min(10, T)` years:

```
g_run = [Πᵧ₌₀^9 (1 + r_y) / (1 + cpi_y)] ^ (1/10) − 1
```

The report shows the mean of `g_run` separately for runs that *failed*
(ran out of money) vs runs that *succeeded*. A wide gap is evidence that
failures are dominated by bad early decades — which is exactly the case
the bucket strategy and glide path are designed to dampen.

## License

MIT.
