"""Interactive Streamlit app: tweak case inputs, re-run, see variance live.

Run via:
    streamlit run src/fireplace/streamlit_app.py -- examples/spain_default.yaml
or:
    fireplace streamlit examples/spain_default.yaml
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fireplace.case import Case, CurvePoint, Stream
from fireplace.config import load_config
from fireplace.render_html import _terminal_histogram, _wealth_fan_figure, _withdrawal_figure
from fireplace.report import aggregate
from fireplace.simulate import simulate


def _argv_config_path() -> Path | None:
    # Streamlit forwards args after `--`. Find a YAML path in argv.
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.suffix in (".yaml", ".yml") and p.exists():
            return p
    return None


def _editable_streams(label: str, streams: list[Stream], key_prefix: str) -> list[Stream]:
    df = pd.DataFrame(
        [
            {
                "name": s.name,
                "amount": s.amount,
                "start_age": s.start_age,
                "end_age": s.end_age,
                "inflate": s.inflate,
                "growth": s.growth,
                "discretionary": s.discretionary,
            }
            for s in streams
        ]
    )
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, key=f"{key_prefix}_editor",
        column_config={
            "amount": st.column_config.NumberColumn(format="%.0f"),
            "growth": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    out: list[Stream] = []
    kind = streams[0].kind if streams else ("expense" if "expense" in label.lower() else "income")
    for _, row in edited.iterrows():
        if not row.get("name") or pd.isna(row.get("name")):
            continue
        try:
            out.append(
                Stream(
                    name=str(row["name"]),
                    amount=float(row["amount"]),
                    start_age=int(row["start_age"]),
                    end_age=int(row["end_age"]),
                    inflate=bool(row["inflate"]),
                    growth=float(row.get("growth") or 0.0),
                    kind=kind,  # type: ignore[arg-type]
                    discretionary=bool(row.get("discretionary", False)),
                )
            )
        except (ValueError, TypeError):
            continue
    return out


def _edit_case(base: Case) -> Case:
    c = deepcopy(base)
    with st.sidebar:
        st.subheader("General")
        c.age = st.number_input("Current age", 18, 80, c.age)
        c.end_age = st.number_input("Plan until age", c.age + 1, 110, c.end_age)
        c.portfolio = st.number_input("Portfolio (€)", 0.0, 1e9, float(c.portfolio), step=1000.0)
        c.emergency_fund = st.number_input(
            "Emergency fund (€)", 0.0, 1e8, float(c.emergency_fund), step=500.0
        )
        c.cash_nominal_return = (
            st.number_input("Cash nominal return (%)", -5.0, 10.0, c.cash_nominal_return * 100, step=0.1)
            / 100
        )

        st.subheader("Returns")
        alloc = c.resolved_allocation()
        if c.allocation is None:
            # No explicit allocation — let the user pick a single series.
            c.return_series = st.selectbox(
                "Return series",
                ["msci_world_total", "global_agg_bond_total"],
                index=["msci_world_total", "global_agg_bond_total"].index(c.return_series)
                if c.return_series in ("msci_world_total", "global_agg_bond_total")
                else 0,
            )
        else:
            # Show a read-only summary of the loaded allocation.
            asset_lines = ", ".join(f"{a.name}={a.series}" for a in alloc.assets)
            st.caption(f"**Allocation:** {asset_lines}")
            if len(alloc.glide) == 1:
                wstr = ", ".join(f"{k}={v:.0%}" for k, v in alloc.glide[0].weights.items())
                st.caption(f"**Static weights:** {wstr}")
            else:
                st.caption(f"**Glide path:** {len(alloc.glide)} pivots")
                st.dataframe(
                    pd.DataFrame(
                        [{"age": p.age, **p.weights} for p in alloc.glide]
                    ),
                    hide_index=True, use_container_width=True,
                )
            st.caption("_Edit the YAML to change allocation — not editable here yet._")
        c.return_mode = st.selectbox(
            "Bootstrap mode", ["bootstrap", "block_bootstrap"],
            index=["bootstrap", "block_bootstrap"].index(c.return_mode),
        )
        c.block_size = st.slider("Block size (years)", 1, 10, c.block_size)

        st.subheader("Inflation")
        c.inflation_mode = st.selectbox(
            "Mode", ["constant", "bootstrap"],
            index=["constant", "bootstrap"].index(c.inflation_mode),
        )
        c.inflation_rate = (
            st.number_input("Constant rate (%)", 0.0, 15.0, c.inflation_rate * 100, step=0.1) / 100
        )

        st.subheader("Withdrawal policy")
        c.withdrawal.bucket_threshold = (
            st.number_input(
                "EF kicks in below return (%)", -20.0, 20.0,
                c.withdrawal.bucket_threshold * 100, step=0.5,
            )
            / 100
        )
        c.withdrawal.ef_target_months = st.slider(
            "EF target (months of expenses)", 0, 36, int(c.withdrawal.ef_target_months)
        )

        st.subheader("Spending behaviour")
        st.caption("Both act only on expense rows marked `discretionary`.")
        sc = c.spending_curve
        sc.enabled = st.checkbox("Spending smile (age curve)", sc.enabled)
        if sc.enabled:
            piv_df = pd.DataFrame(
                [{"age": p.age, "factor": p.factor} for p in sc.pivots]
                or [{"age": c.age, "factor": 1.0}]
            )
            edited_piv = st.data_editor(
                piv_df, num_rows="dynamic", use_container_width=True, key="curve_pivots",
                column_config={"factor": st.column_config.NumberColumn(format="%.2f")},
            )
            pts: list[CurvePoint] = []
            for _, r in edited_piv.iterrows():
                try:
                    pts.append(CurvePoint(age=int(r["age"]), factor=float(r["factor"])))
                except (ValueError, TypeError):
                    continue
            sc.pivots = pts
        ds = c.dynamic_spending
        ds.enabled = st.checkbox("Dynamic guardrails (Guyton-Klinger)", ds.enabled)
        if ds.enabled:
            ds.upper_guard = st.number_input("Cut when WR > × initial", 1.0, 3.0, ds.upper_guard, step=0.05)
            ds.lower_guard = st.number_input("Raise when WR < × initial", 0.1, 1.0, ds.lower_guard, step=0.05)
            ds.cut = st.number_input("Cut size (fraction)", 0.0, 0.5, ds.cut, step=0.01)
            ds.bump = st.number_input("Raise size (fraction)", 0.0, 0.5, ds.bump, step=0.01)
            ds.floor = st.number_input("Floor × baseline", 0.1, 1.0, ds.floor, step=0.05)
            ds.ceiling = st.number_input("Ceiling × baseline", 1.0, 4.0, ds.ceiling, step=0.05)

        st.subheader("Pension")
        c.pension.monthly_amount = st.number_input(
            "Monthly amount (€)", 0.0, 1e5, float(c.pension.monthly_amount), step=50.0
        )
        c.pension.start_age = st.number_input(
            "Start age", 50, 75, int(c.pension.start_age)
        )

        st.subheader("Wealth tax")
        wt = c.wealth_tax
        wt.enabled = st.checkbox("Enable net-wealth tax (Patrimonio/ITSGF)", wt.enabled)
        if wt.enabled:
            wt.allowance = st.number_input(
                "Allowance (€)", 0.0, 5e6, float(wt.allowance), step=50000.0
            )
            wt.index_to_inflation = st.checkbox(
                "Index allowance & brackets to inflation", wt.index_to_inflation
            )
            st.caption(f"{len(wt.brackets)} bracket(s) loaded from YAML — edit there to change the scale.")

        st.subheader("Stress (what-if regime)")
        sr = c.stress
        sr.enabled = st.checkbox("Inject a deterministic stress window", sr.enabled)
        if sr.enabled:
            sr.start_age = st.number_input("Stress start age", c.age, c.end_age, int(sr.start_age))
            sr.years = st.slider("Stress length (years)", 1, 30, int(sr.years))
            sr.annual_inflation = (
                st.number_input("Stress inflation (%)", 0.0, 30.0, sr.annual_inflation * 100, step=0.5) / 100
            )
            sr.annual_nominal_return = (
                st.number_input("Stress nominal return (%)", -30.0, 30.0, sr.annual_nominal_return * 100, step=0.5)
                / 100
            )
            st.caption("Conditional, imposed on all runs. E.g. 7% / 0% = a 1970s-style stagflation decade.")

        st.subheader("Monte Carlo")
        c.n_runs = st.select_slider(
            "Runs", options=[500, 1000, 2000, 5000, 10000], value=c.n_runs
        )
        seed_in = st.number_input("Seed (0 = random)", 0, 999999, c.seed or 0)
        c.seed = None if seed_in == 0 else int(seed_in)

    st.markdown("##### Income streams")
    c.incomes = _editable_streams("incomes", c.incomes, "income")
    st.markdown("##### Expense streams")
    c.expenses = _editable_streams("expenses", c.expenses, "expense")
    return c


def main() -> None:
    st.set_page_config(page_title="fireplace", layout="wide", page_icon="🔥")
    st.title("🔥 fireplace — Monte Carlo retirement simulator")

    cfg_path = _argv_config_path()
    if cfg_path is None:
        st.error(
            "Pass a YAML config path. E.g. "
            "`streamlit run src/fireplace/streamlit_app.py -- examples/spain_default.yaml`"
        )
        return

    cases = load_config(cfg_path)
    names = [c.name for c in cases]
    chosen = st.selectbox("Scenario (load as starting point)", names)
    base = next(c for c in cases if c.name == chosen)

    edited = _edit_case(base)

    with st.spinner("Simulating..."):
        rep = simulate(edited)
        agg = aggregate(rep)

    show_wtax = agg.median_lifetime_wealth_tax > 0
    cols = st.columns(5 if show_wtax else 4)
    cols[0].metric("Success rate", f"{agg.success_rate * 100:.1f}%")
    cols[1].metric("Terminal wealth (p50)", f"{agg.terminal_wealth_p50:,.0f} {edited.currency}")
    cols[2].metric("Terminal wealth (p10)", f"{agg.terminal_wealth_p10:,.0f} {edited.currency}")
    cols[3].metric("Median lifetime income tax", f"{agg.median_lifetime_tax:,.0f} {edited.currency}")
    if show_wtax:
        cols[4].metric("Median lifetime wealth tax", f"{agg.median_lifetime_wealth_tax:,.0f} {edited.currency}")

    st.plotly_chart(_wealth_fan_figure(rep), use_container_width=True)
    st.plotly_chart(_withdrawal_figure(rep), use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(_terminal_histogram(rep), use_container_width=True)
    with c2:
        if (rep.failure_year >= 0).any():
            ages = edited.age + rep.failure_year[rep.failure_year >= 0]
            fig = go.Figure(go.Histogram(x=ages, nbinsx=30, marker_color="#fb5053"))
            fig.update_layout(
                title=f"Failure ages (n={len(ages)})",
                xaxis_title="Age", yaxis_title="Runs", height=320,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.success("No failed runs in this configuration.")

    if agg.early_real_return_failed is not None:
        st.markdown(
            "##### Sequence-of-returns risk\n"
            f"- Successful runs averaged **{agg.early_real_return_succeeded * 100:+.2f}%** real "
            "return over the first decade.\n"
            f"- Failed runs averaged **{agg.early_real_return_failed * 100:+.2f}%** real return "
            "over the first decade.\n"
            "Bigger gap → more of the failure is driven by bad early years."
        )


if __name__ == "__main__":
    main()
