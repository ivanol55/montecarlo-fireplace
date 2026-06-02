# AGENTS.md — working in fireplace

`fireplace` is a Monte Carlo **retirement / FIRE simulator** with a Spain-aware
tax model. You give it a *case* (your age, portfolio, income & expense windows)
as YAML; it runs N independent paths against bootstrapped historical returns +
inflation, applies a two-bucket withdrawal strategy, models savings-income tax
(FIFO basis + bracket stacking), and emits an interactive HTML report. Multiple
scenarios live in one YAML via inheritance so you can compare them in one report.

This file tells an agent how to navigate, run, and extend the project. The
human-facing tour is in [README.md](README.md); the full YAML field reference is
in [docs/CONFIG.md](docs/CONFIG.md). When in doubt, those two win over this file.

## Mental model

Pipeline, one module per stage — keep changes inside the right one:

```
YAML ──load_config──▶ Case ──simulate──▶ ScenarioReport ──aggregate──▶ Aggregates
                                                │                          │
                                          render_html ◀───────── narrate (prose verdict)
```

| Stage | File | Entry symbol |
|-------|------|--------------|
| Load + merge YAML | [src/fireplace/config.py](src/fireplace/config.py) | `load_config(path) -> list[Case]` |
| Dataclasses | [src/fireplace/case.py](src/fireplace/case.py) | `Case`, `ScenarioReport`, `Stream`, `Allocation` |
| Bootstrap returns/CPI | [src/fireplace/returns.py](src/fireplace/returns.py) | `sample_multicol_paths(...)` |
| Monte Carlo engine | [src/fireplace/simulate.py](src/fireplace/simulate.py) | `simulate(case) -> ScenarioReport` |
| Spain tax | [src/fireplace/tax.py](src/fireplace/tax.py) | `gross_up_for_withdrawal_vec(...)` |
| Metrics + solvers | [src/fireplace/report.py](src/fireplace/report.py) | `aggregate`, `sustainable_spending`, `spend_to_zero` |
| Deterministic prose | [src/fireplace/narrate.py](src/fireplace/narrate.py) | `result_block(...)` |
| HTML/Plotly output | [src/fireplace/render_html.py](src/fireplace/render_html.py) | `render(...)` |
| Streamlit UI | [src/fireplace/streamlit_app.py](src/fireplace/streamlit_app.py) | — |
| CLI | [src/fireplace/cli.py](src/fireplace/cli.py) | `main` (`run`/`summary`/`list`/`streamlit`) |

Tech: Python ≥3.10, numpy + pandas + pyyaml + plotly + jinja2 + click. Optional
extras: `ui` (streamlit), `dev` (pytest, ruff), `fetch` (yfinance). Historical
data lives in [data/returns_annual.csv](data/returns_annual.csv) (1999–2025;
methodology in [data/SOURCES.md](data/SOURCES.md)).

## Setup & commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui,dev]"          # ui+dev extras; add ,fetch to refresh data

fireplace run examples/spain_default.yaml -o out/report.html   # static HTML report
fireplace run cfg.yaml --solve --dwz                           # + spending solvers (slower, re-sims)
fireplace run cfg.yaml --scenario baseline --scenario lean     # subset of scenarios
fireplace summary cfg.yaml [--no-solve]                        # deterministic prose blocks → stdout
fireplace list cfg.yaml                                        # scenario names, ages, run counts
fireplace streamlit cfg.yaml                                   # interactive UI with live re-runs

pytest tests/ -v                    # full suite
pytest tests/test_simulate.py -k zero_expenses
ruff check src tests                # lint (line-length 100, target py310)
```

`--solve` = largest uniform spend multiple still clearing 95% success.
`--dwz` = spend multiple that drives **median** terminal wealth to zero.
Both re-simulate per scenario, so they're noticeably slower.

## Authoring a use case (YAML)

A config has two top-level keys: **`defaults`** (a partial Case shared by every
scenario) and **`scenarios`** (a list of partial Cases, each a *diff* over
defaults). Write the full skeleton once in `defaults`; keep each scenario minimal.

Start from a shipped example rather than from scratch:

- [examples/spain_default.yaml](examples/spain_default.yaml) — baseline / lean / late / pension
- [examples/glide_path.yaml](examples/glide_path.yaml) — allocation strategies side by side
- [examples/indexa.yaml](examples/indexa.yaml) — Indexa Capital + Seguridad Social
- [examples/all_stocks.yaml](examples/all_stocks.yaml) — 100% equities
- [examples/spending_evolution.yaml](examples/spending_evolution.yaml) — spending smile + Guyton-Klinger guardrails

Minimal shape:

```yaml
defaults:
  age: 40
  end_age: 90
  currency: EUR
  portfolio: 500000
  emergency_fund: 24000
  cash_nominal_return: 0.0225

  return_mode: block_bootstrap     # or `bootstrap` (IID); block preserves autocorrelation
  block_size: 5
  return_series: msci_world_total  # single-asset; OR set `allocation:` for multi-asset
  inflation_mode: bootstrap
  inflation_series: eurozone_hicp

  tax:                             # Spain savings-income brackets
    brackets: [[6000,0.19],[50000,0.21],[200000,0.23],[300000,0.27],[.inf,0.28]]
    index_to_inflation: true       # the engine uprates brackets; never hand-compute them

  withdrawal:                      # two-bucket FIRE defaults
    bucket_threshold: 0.0          # draw EF in any nominal-loss year
    ef_target_months: 12
    ef_share_in_bad_year: 1.0

  n_runs: 5000
  seed: 42

  incomes:
    - { name: salary, amount: 45000, start_age: 40, end_age: 49, inflate: true, growth: 0.005 }
  expenses:
    - { name: living,   amount: 24000, start_age: 40, end_age: 90, inflate: true, discretionary: true }
    - { name: mortgage, amount: 12000, start_age: 40, end_age: 54, inflate: false }   # fixed nominal

scenarios:
  - name: baseline
    description: |
      Free-text notes. Safe to paste `fireplace summary` output here.
  - name: lean
    expenses:
      - { name: living, amount: 18000 }   # merged by name — only this field changes
```

### Merge semantics (the #1 thing to get right)

- **Scalars & nested objects** (`tax`, `withdrawal`, `pension`, `stress`, …) → **deep-merged** with defaults.
- **`incomes` / `expenses`** → **merged by `name`**. A scenario stream with a matching
  `name` overrides only the fields it lists; new names are appended. Keep names stable.
- **`allocation`** → **replaced wholesale**, never merged. If a scenario sets `allocation`,
  it must be complete. Don't expect a single weight to bleed through from defaults.

Stream fields: `name`, `amount`, `start_age`, `end_age`, `inflate`, `growth`,
and (expenses only) `discretionary`. See [docs/CONFIG.md](docs/CONFIG.md) for the
exhaustive list including `spending_curve`, `dynamic_spending`, `pension`,
`wealth_tax`, and `stress`.

## How the engine behaves (assumptions that bite)

- **Everything is computed, never persisted.** Results live only in the generated
  report / `Aggregates`; there is no sync command and YAML never caches numbers.
  Re-run to get fresh figures. `fireplace summary` is the *only* way to refresh the
  prose — same numbers in, same text out (no AI drift). Don't hand-edit numbers into
  YAML descriptions and expect them to stay true.
- **Bootstrap pool is small (~27 rows; ~5 effective blocks/path at block_size=5).**
  Tail metrics carry sampling error — don't over-read 91% vs 89% success; it's noise.
- **Returns and CPI are sampled at the same row index**, so 2008's crash pairs with
  2008's inflation. You can't model an uncorrelated drawdown — historically false anyway.
- **`stress` is a conditional what-if overlay, not a probability-weighted forecast.**
  Use it to inject regimes the bootstrap can't generate (stagflation, lost decade).
  "Success given stress" answers "if this hits at this age, do I survive?".
- **Only `discretionary: true` expenses flex** with `spending_curve` / `dynamic_spending`.
  Fixed costs (mortgage, care, upkeep) must stay `discretionary: false`. If both a
  spending curve and guardrails are on, their multipliers compound.
- **Income streams and pension are entered net** — there's no IRPF model. Tax is only
  on savings income (interest + realised gains). Cash return is gross; tax is added for you.
- **Rebalancing is costless** — correct for Indexa fondos (traspaso), optimistic for
  DIY ETFs in a taxable Spanish broker where each rebalance realises gains.
- **Pension is a flat inflation-indexed monthly amount**, not a contributions model.
  For an early retiree, model it pessimistically (often `0`) and stress both ways —
  base reguladora draws on the last 25 years and carencia rules are easy to miss.

## Walking a user through a new use case

When a user asks you to "build my plan" or "create a use case", you're acting as
a *modelling assistant*, not just a YAML typist. The job is to extract a faithful
picture of their finances, translate it into a `Case`, and — crucially — keep them
honest about what the output does and doesn't mean. Work in this order.

**0. Frame it first (one sentence).** Tell the user up front: this is a planning
*model*, not advice, and its numbers are estimates with real uncertainty. You are
not a licensed financial advisor; for anything binding they should confirm tax and
pension specifics with a *gestor* / *asesor fiscal*. Say this once, plainly, then
get to work — don't bury the rest of the session in disclaimers.

**1. Interview — gather inputs conversationally.** Don't dump the whole schema on
them. Ask in plain language and map their answers to fields yourself:

| Ask them… | Maps to | Notes / defaults to fall back on |
|-----------|---------|----------------------------------|
| Current age, and how long to plan for | `age`, `end_age` | Default `end_age` 90; suggest 95–100 for longevity safety, not the median. |
| Invested portfolio today (fondos/ETFs) | `portfolio` | Market value, base-year EUR. |
| Have you got unrealised gains? Roughly what did you pay in? | `portfolio_cost_basis` | Omit → assumes basis = value (zero gains, **under**-states future tax). Ask if the pot is old. |
| Cash buffer / cuenta remunerada | `emergency_fund`, `cash_nominal_return` | Cash yield is *gross*; tax is added for them. |
| Salary / side income, and until what age | `incomes` (stream) | Enter **net** of IRPF — there's no labour-income tax model. Use `growth` for real raises. |
| Yearly spending, split into fixed vs flexible | `expenses` (streams) | Mark only the genuinely cuttable ones `discretionary: true`. Mortgage/care/upkeep stay fixed. Mortgage usually `inflate: false`. |
| Big one-offs (new roof, car, college, paying off mortgage) | extra `expenses` streams | Model as their own age-windowed stream. |
| Pension expectations | `pension` | **Handle with care — see warnings.** Default to a conservative figure, often `0`. |
| How invested — Indexa-style fund, stocks/bonds split, glide path? | `return_series` or `allocation` | Single fund → `return_series`. Mix → `allocation` weights. De-risking with age → `glide_path`. |

Fill anything they don't know with the documented defaults and **tell them what you
assumed** rather than interrogating them to exhaustion. A rough plan they can react
to beats a perfect interview.

**2. Propose scenarios, not a single answer.** The tool's whole point is comparison.
Always build at least a baseline plus a stress. Good default trio:

- `baseline` — their stated plan as-is.
- a **lean/downside** — lower spend, or `pension.monthly_amount: 0`, or earlier retirement.
- a **stress** — a `stress` block (e.g. stagflation decade at retirement age) and/or
  longevity to `end_age: 100`.

Put the shared skeleton in `defaults`; express each scenario as a minimal diff
(remember the merge rules in the section above).

**3. Run, read, and translate the numbers.** `fireplace run cfg.yaml -o out/report.html`,
optionally `--solve --dwz`. Then explain the headline metrics in human terms:
success rate (fraction of paths not running dry), year-1 WR vs the 4%-rule analogue,
funded ratio (>1 = covered on conservative assumptions), and the sustainable-spend
multiple. Use `fireplace summary` for the deterministic prose — never hand-write
result numbers, and never edit numbers into the YAML `description`; they go stale
the moment an input changes (results are computed, never persisted).

**4. Iterate.** Change one lever at a time so the user can see what moved the needle
(spend, retirement age, allocation, EF size). Suggest `fireplace streamlit cfg.yaml`
when they want to twiddle knobs live.

### Warnings you must give (don't let these slide)

These are the places where a confident-looking number misleads. Surface the relevant
ones proactively — the user usually won't know to ask.

- **Not advice, and uncertainty is real.** Frame outputs as ranges, not promises.
  A "92% success" is a model artefact, not a guarantee.
- **Small data pool → noisy tails.** Returns bootstrap from ~27 years (1999–2025).
  Don't oversell a 91% vs 89% difference — it's within noise. Rank scenarios; don't
  treat single percentages as precise. The data also *cannot* produce a 1970s
  stagflation decade — that's exactly what the `stress` block is for, so use it.
- **Pension is the biggest trap for early retirees.** The naive "I'll get the minimum
  pension" is usually wrong: *carencia específica* (2 of the last 15 years contributed)
  can forfeit the contributory pension entirely if they stop early, and the *base
  reguladora* (last 25 years) comes out low. Default to a pessimistic `monthly_amount`
  (often `0`), model an optimistic ceiling as a *separate* scenario, and treat pension
  as a bonus, not a foundation. Tell them to verify with a gestor.
- **Income/pension are entered net; there's no IRPF model.** If they hand you gross
  salary, convert (or flag that you didn't). Only savings-income tax (gains, interest,
  dividends) is modelled.
- **Costless rebalancing assumption.** Accurate for Indexa-style fondos (traspaso),
  **optimistic** for DIY ETFs in a taxable broker where each rebalance realises gains.
  Ask which they hold; warn if it's the latter.
- **Wealth tax is region-dependent and off by default.** Madrid/bonificated → leave it
  off. Catalonia and others → enable with the correct regional scale, and tell them to
  confirm it with a gestor before trusting the euros.
- **Discretionary flexing only cuts what's flagged.** If they expect spending to flex
  in bad years, the relevant streams must be `discretionary: true` — otherwise the plan
  is rigidly flat-real and looks riskier than they intend (or vice versa).
- **The home isn't a portfolio asset here.** It's modelled as mortgage/upkeep expense
  streams; primary residence is excluded from wealth tax accordingly. Don't add the
  house value to `portfolio`.
- **Stress success is conditional, not a forecast.** "Survives the stress" means "if
  this regime hits at this age" — it isn't probability-weighted. Say so.

## Conventions for changes

- Match the existing module's style; keep new logic in the stage it belongs to
  (don't compute tax in `simulate.py`, don't aggregate in `render_html.py`).
- The Monte Carlo core is vectorised over runs with numpy — prefer array ops to
  per-run Python loops in hot paths.
- Add a behavioural test in [tests/](tests/) for any engine change. The suite leans on
  invariants (zero expenses never fail; huge expenses always fail; more capital ⇒ more
  terminal wealth; guardrails lift success and cut peak WR; stress lowers success and is
  inert when off) rather than golden numbers. Use a fixed `seed` for determinism.
- New Streamlit knobs go in [src/fireplace/streamlit_app.py](src/fireplace/streamlit_app.py)
  and should drive a live re-simulation; mirror the YAML field they expose.
- Run `pytest` and `ruff check` before declaring done.
