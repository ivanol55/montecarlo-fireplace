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

| Field        | Type             | Description                                                  |
|--------------|------------------|--------------------------------------------------------------|
| `tax`        | TaxConfig        | Spain savings-income brackets. See [Tax](#tax).              |
| `withdrawal` | WithdrawalPolicy | Bucket strategy. See [Withdrawal policy](#withdrawal-policy).|

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
