# YAML config reference

Every field accepted in a fireplace YAML, what it means, and what it defaults
to. Source of truth: [src/fireplace/case.py](../src/fireplace/case.py) (the
dataclasses) and [src/fireplace/config.py](../src/fireplace/config.py) (the
YAML parser).

## Top-level structure

```yaml
defaults:        # optional: partial Case that all scenarios inherit
  ...

scenarios:       # required: list of partial Cases, one per scenario
  - name: baseline
    ...
  - name: lean
    ...
```

Each entry under `scenarios` must be a mapping with a `name`. All other
fields fall back to `defaults`, which falls back to dataclass defaults below.

### Merge semantics

When a scenario overrides `defaults`:

- **Scalars and nested mappings** (e.g. `tax`, `withdrawal`, `pension`) are
  **deep-merged** — overriding a single key inside `withdrawal` keeps the
  others.
- **`incomes` and `expenses`** (lists of streams) are **merged by `name`** —
  re-listing a stream with the same name overrides only the fields you set.
  Streams with new names are appended.
- **`allocation`** is **replaced wholesale** — pick one form (static weights
  or glide path) per scenario; nothing from defaults bleeds through.

Relative `data_file` paths resolve against the YAML's own directory.

## Case fields

All fields are optional unless noted. Defaults are shown in `()`.

### Identity & horizon

| Field         | Type   | Description                                              |
|---------------|--------|----------------------------------------------------------|
| `name`        | string | **Required on each scenario.** Used in report titles.    |
| `description` | string | (`""`) Free-text shown at the top of that scenario block.|
| `age`         | int    | (`35`) Age at simulation start (year 0).                 |
| `end_age`     | int    | (`90`) Age at last simulated year, inclusive.            |
| `currency`    | string | (`"EUR"`) Cosmetic; used in axis labels.                 |

### Initial state (base-year currency)

| Field                   | Type        | Description                                                                 |
|-------------------------|-------------|-----------------------------------------------------------------------------|
| `portfolio`             | float       | (`0`) Starting market value of the portfolio (fondos / ETFs).               |
| `emergency_fund`        | float       | (`0`) Starting balance of the cuenta remunerada / cash bucket.              |
| `portfolio_cost_basis`  | float\|null | (`null` → assume = `portfolio`, i.e. zero unrealised gains at year 0).      |

### Cashflows

| Field      | Type           | Description                                                  |
|------------|----------------|--------------------------------------------------------------|
| `incomes`  | list of Stream | Salary, side-income, etc. See [Stream](#stream).             |
| `expenses` | list of Stream | Living, mortgage, lifestyle uplifts, etc.                    |
| `pension`  | Pension        | Government / private pension. See [Pension](#pension).       |

### Inflation

| Field              | Type                          | Description                                                                                     |
|--------------------|-------------------------------|-------------------------------------------------------------------------------------------------|
| `inflation_mode`   | `"constant"` \| `"bootstrap"` | (`"bootstrap"`) Sample CPI from a CSV column at the same row index as that year's return, or use a constant rate.|
| `inflation_rate`   | float                         | (`0.02`) Used when mode is `constant`. ECB long-run target.                                     |
| `inflation_series` | string\|null                  | (`"eurozone_hicp"`) CSV column name for `bootstrap` mode. The bundled CSV ships Eurozone HICP 1999+; pair with a custom `data_file` for other CPI series.|

### Returns

| Field                 | Type                                  | Description                                                                                              |
|-----------------------|---------------------------------------|----------------------------------------------------------------------------------------------------------|
| `return_mode`         | `"bootstrap"` \| `"block_bootstrap"`  | (`"bootstrap"`) IID resampling vs moving-block. Use block for return + inflation autocorrelation.        |
| `return_series`       | string                                | (`"msci_world_total"`) Column name from `data_file`. Only used when `allocation` is **not** set.         |
| `allocation`          | mapping\|null                         | (`null`) Multi-asset / glide-path portfolio. See [Allocation](#allocation). Replaces `return_series`.     |
| `block_size`          | int                                   | (`5`) Block length for `block_bootstrap`. Ignored for `bootstrap`.                                       |
| `data_file`           | string\|null                          | (`null` → bundled `data/returns_annual.csv`) Path to a CSV; relative paths resolve from the YAML dir.    |
| `cash_nominal_return` | float                                 | (`0.0`) Gross annual yield on the EF (cuenta remunerada). Net of tax is computed for you.                |

### Tax & withdrawal

| Field        | Type             | Description                                                       |
|--------------|------------------|------------------------------------------------------------------|
| `tax`        | TaxConfig        | Spain savings-income brackets. See [Tax](#tax).                   |
| `wealth_tax` | WealthTaxConfig  | Annual net-wealth tax (Patrimonio / ITSGF). Off by default. See [Wealth tax](#wealth-tax). |
| `withdrawal` | WithdrawalPolicy | Bucket strategy. See [Withdrawal policy](#withdrawal-policy).     |

### Spending behaviour

How discretionary spending evolves over retirement. Both default **off**, so a
case with neither set behaves exactly as a flat-real plan. They act only on
expense streams marked `discretionary: true` — fixed costs (mortgage, upkeep,
care) are never flexed. When both are on, their multipliers compound.

| Field              | Type            | Description                                                                         |
|--------------------|-----------------|-------------------------------------------------------------------------------------|
| `spending_curve`   | SpendingCurve   | Deterministic age-based "spending smile". See [Spending curve](#spending-curve).    |
| `dynamic_spending` | DynamicSpending | Guyton-Klinger portfolio-reactive guardrails. See [Dynamic spending](#dynamic-spending). |

### Monte Carlo

| Field    | Type      | Description                                                                                                   |
|----------|-----------|---------------------------------------------------------------------------------------------------------------|
| `n_runs` | int       | (`5000`) Number of independent paths per scenario.                                                            |
| `seed`   | int\|null | (`42`) Random seed for reproducibility. Set to `null` to randomise.                                           |

## Sub-schemas

### Stream

Used inside `incomes` and `expenses`. The `kind` field is set automatically
by the parent key — don't set it yourself.

```yaml
incomes:
  - name: salary
    amount: 42000
    start_age: 35
    end_age: 49
    inflate: true
    growth: 0.005
```

| Field       | Type   | Required | Description                                                                  |
|-------------|--------|----------|------------------------------------------------------------------------------|
| `name`      | string | yes      | Used to merge across `defaults` ↔ scenario.                                  |
| `amount`    | float  | yes      | Annual amount in base-year currency.                                          |
| `start_age` | int    | yes      | First age at which the stream is active.                                      |
| `end_age`   | int    | yes      | Last age at which the stream is active (inclusive).                           |
| `inflate`   | bool   | no       | (`true`) Index the amount to realised inflation each year.                    |
| `growth`    | float  | no       | (`0.0`) Extra real growth per year on top of inflation (e.g. salary raises). |
| `discretionary` | bool | no    | (`false`) **Expenses only.** Marks the stream as flex-able by `spending_curve` and `dynamic_spending`. Fixed costs leave this `false` so they're never cut. Ignored for income streams. |

### Pension

```yaml
pension:
  monthly_amount: 1200
  start_age: 67
  inflate: true
```

| Field            | Type  | Description                                                  |
|------------------|-------|--------------------------------------------------------------|
| `monthly_amount` | float | (`0.0`) Base-year EUR/month. `0` = no pension.               |
| `start_age`      | int   | (`67`) First age at which the pension starts paying.         |
| `inflate`        | bool  | (`true`) Inflation-index the monthly amount each year.       |

### Tax

```yaml
tax:
  brackets:
    - [6000, 0.19]
    - [50000, 0.21]
    - [200000, 0.23]
    - [300000, 0.27]
    - [.inf, 0.28]
  index_to_inflation: true
```

| Field                | Type                       | Description                                                                                                                |
|----------------------|----------------------------|----------------------------------------------------------------------------------------------------------------------------|
| `brackets`           | list of `[upper, rate]`    | Cumulative bracket schedule. Each pair is `(upper bound EUR, marginal rate)`. **Last entry must use `.inf`** as the upper. |
| `index_to_inflation` | bool                       | (`true`) Index bracket boundaries to realised inflation each simulated year.                                               |

### Wealth tax

Spain's annual net-wealth tax — *Impuesto sobre el Patrimonio* plus the
solidarity surtax on large fortunes (*ITSGF*). **Disabled by default.** When
enabled, it's assessed each simulated year on net *financial* wealth (portfolio
+ emergency fund) above `allowance`, with a progressive scale on the excess.
The primary residence is excluded — which matches how this model holds the home
(a mortgage/upkeep expense stream, not a portfolio asset).

This is **highly region-dependent**: Madrid and pre-ITSGF Andalucía bonificate
Patrimonio to ~zero (model that with `enabled: false`); Catalonia is among the
harshest. Verify the current regional scale with a gestor before treating these
euros as exact.

```yaml
wealth_tax:
  enabled: true
  allowance: 700000
  index_to_inflation: true
  brackets:
    - [167129.45,   0.002]
    - [334252.88,   0.003]
    - [668499.75,   0.005]
    - [1336999.51,  0.009]
    - [2673999.01,  0.013]
    - [5347998.03,  0.017]
    - [10695996.06, 0.021]
    - [.inf,        0.035]
```

| Field                | Type                    | Description                                                                                                                            |
|----------------------|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `enabled`            | bool                    | (`false`) Master switch. Off = no wealth tax (fully-bonificated region).                                                              |
| `allowance`          | float                   | (`700000`) Tax-free *mínimo exento* on financial wealth. Subtracted before the scale; bracket bounds are measured on the **excess**. |
| `brackets`           | list of `[upper, rate]` | Progressive scale on the excess over `allowance`. `(upper bound of excess EUR, marginal rate)`. **Last entry must use `.inf`.**       |
| `index_to_inflation` | bool                    | (`true`) Index the allowance and bracket bounds to realised inflation each simulated year, mirroring the income-tax treatment.       |

### Spending curve

A deterministic age-based real multiplier on **discretionary** spending — the
empirical "retirement spending smile" (Bernicke / Blanchett): higher in the
go-go years, tapering through the slow-go / no-go years. It applies identically
to every run, interpolated linearly between pivots and clamped at the endpoints.
Late-life care is modelled separately as a non-discretionary stream, so this
curve captures only the discretionary decline.

```yaml
spending_curve:
  enabled: true
  pivots:
    - { age: 42, factor: 1.20 }
    - { age: 55, factor: 1.10 }
    - { age: 70, factor: 0.90 }
    - { age: 85, factor: 0.80 }
```

| Field     | Type                       | Description                                                                                          |
|-----------|----------------------------|------------------------------------------------------------------------------------------------------|
| `enabled` | bool                       | (`false`) Off = a flat `1.0` multiplier (no smile).                                                  |
| `pivots`  | list of `{age, factor}`    | Real multiplier on discretionary spend at each age. Interpolated linearly between pivots, clamped outside. |

### Dynamic spending

Guyton-Klinger-style spending guardrails on **discretionary** spending. Each
retirement year, the current withdrawal rate is compared to the rate set in the
first retirement year. If it has risen past `upper_guard`× that initial rate,
discretionary spending is cut by `cut`; if it has fallen below `lower_guard`×,
it's raised by `bump`. The per-run multiplier ratchets across years and is
clamped to `[floor, ceiling]`. Raising in good states is what lets a plan spend
its right-tail surplus instead of dying rich; cutting in bad states protects the
downside.

```yaml
dynamic_spending:
  enabled: true
  upper_guard: 1.20
  lower_guard: 0.80
  cut: 0.10
  bump: 0.10
  floor: 0.50
  ceiling: 1.50
```

| Field         | Type  | Description                                                                                          |
|---------------|-------|------------------------------------------------------------------------------------------------------|
| `enabled`     | bool  | (`false`) Off = discretionary spend stays at its planned level.                                      |
| `upper_guard` | float | (`1.2`) Cut when current WR exceeds this multiple of the first-retirement-year WR.                   |
| `lower_guard` | float | (`0.8`) Raise when current WR falls below this multiple of the first-retirement-year WR.             |
| `cut`         | float | (`0.10`) Fractional reduction applied when the upper guardrail trips.                                |
| `bump`        | float | (`0.10`) Fractional increase applied when the lower guardrail trips. (Named `bump`, not `raise`, to avoid the Python keyword.) |
| `floor`       | float | (`0.5`) Lower clamp on the cumulative discretionary multiplier.                                      |
| `ceiling`     | float | (`1.5`) Upper clamp on the cumulative discretionary multiplier.                                      |

### Withdrawal policy

These three values together define a **2-bucket withdrawal strategy** matching
the FIRE-community consensus on where to draw retirement spending from. The
defaults below are used by every shipped YAML; see the README section
[Withdrawal sourcing: FIRE-standard defaults](../README.md#withdrawal-sourcing-fire-standard-defaults)
for the full rationale and links to source material.

```yaml
withdrawal:
  bucket_threshold: 0.0
  ef_target_months: 12
  ef_share_in_bad_year: 1.0
```

| Field                  | Type  | Description                                                                                                                       |
|------------------------|-------|-----------------------------------------------------------------------------------------------------------------------------------|
| `bucket_threshold`     | float | (`0.0`) Pull from EF when the portfolio's realised nominal return is below this. `0.0` = "any nominal-loss year" (FIRE standard). |
| `ef_target_months`     | float | (`12.0`) Target EF size in months of expenses. FIRE consensus: 12 months balances sequence-risk insurance against cash drag.       |
| `ef_share_in_bad_year` | float | (`1.0`) Fraction of the year's net deficit that comes from EF when the bucket triggers (capped by EF balance). `1.0` = drain cash before touching equities. |

### Allocation

Two forms — static weights or glide path. The `allocation` block is replaced
wholesale by scenarios (see [Merge semantics](#merge-semantics)).

**Static weights:**

```yaml
allocation:
  assets:
    stocks: msci_world_total
    bonds:  global_agg_bond_total
  weights:
    stocks: 0.6
    bonds:  0.4
```

**Glide path** (linearly interpolated between pivots; clamped at the ends):

```yaml
allocation:
  assets:
    stocks: msci_world_total
    bonds:  global_agg_bond_total
  glide_path:
    - { age: 35, stocks: 0.90, bonds: 0.10 }
    - { age: 65, stocks: 0.60, bonds: 0.40 }
```

| Field        | Type                             | Description                                                                                                                          |
|--------------|----------------------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `assets`     | mapping `name -> series`         | **Required.** Asset name → CSV column. Names are arbitrary; you'll reference them in `weights` / `glide_path`.                       |
| `weights`    | mapping `name -> float`          | Static weights. Mutually exclusive with `glide_path`. Must sum to 1.0 (within 1e-3).                                                 |
| `glide_path` | list of `{age, <asset weights>}` | Pivots in age order. Each pivot needs an `age` and weights that sum to 1.0. Omitted assets in a pivot default to weight 0.           |

If `allocation` is omitted entirely, `return_series` is used as a single-asset
fallback. With multiple assets defined but no `weights` or `glide_path`, the
config raises an error.

## Minimal valid example

```yaml
scenarios:
  - name: baseline
    age: 40
    end_age: 90
    portfolio: 200000
    expenses:
      - { name: living, amount: 24000, start_age: 40, end_age: 90 }
```

Everything else uses dataclass defaults: 5000 runs, 2% constant inflation,
MSCI World single-asset bootstrap, default Spain tax brackets, EF starts at 0.
