"""YAML config loader with multi-scenario inheritance.

A config file declares a `defaults` block (a partial Case) and a list of
`scenarios`, each of which is a partial Case overriding the defaults.
Streams (incomes, expenses) are *merged by name*: scenarios can add new
streams or override fields of an existing one (e.g. just bump the amount).
"""
from __future__ import annotations

import copy
from dataclasses import fields
from pathlib import Path

import yaml

from .case import (
    Allocation,
    Asset,
    Case,
    CurvePoint,
    DynamicSpending,
    GlidePoint,
    Pension,
    SpendingCurve,
    Stream,
    StressRegime,
    TaxConfig,
    WealthTaxConfig,
    WithdrawalPolicy,
)

# Top-level keys that should be replaced (not deep-merged) when a scenario overrides defaults.
_REPLACE_KEYS = {"allocation"}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge; lists of dicts with `name` key are merged by name.

    Keys in `_REPLACE_KEYS` are replaced wholesale, not merged.
    """
    out = copy.deepcopy(base)
    for key, val in override.items():
        if key in _REPLACE_KEYS:
            out[key] = copy.deepcopy(val)
            continue
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        elif (
            key in out
            and isinstance(out[key], list)
            and isinstance(val, list)
            and out[key]
            and isinstance(out[key][0], dict)
            and "name" in out[key][0]
        ):
            by_name: dict[str, dict] = {item["name"]: copy.deepcopy(item) for item in out[key]}
            for item in val:
                if not isinstance(item, dict) or "name" not in item:
                    by_name[item.get("name", id(item))] = item  # best-effort
                    continue
                if item["name"] in by_name:
                    by_name[item["name"]] = _deep_merge(by_name[item["name"]], item)
                else:
                    by_name[item["name"]] = item
            out[key] = list(by_name.values())
        else:
            out[key] = val
    return out


def _build_streams(items: list[dict] | None, kind: str) -> list[Stream]:
    if not items:
        return []
    out = []
    for raw in items:
        out.append(
            Stream(
                name=raw["name"],
                amount=float(raw["amount"]),
                start_age=int(raw["start_age"]),
                end_age=int(raw["end_age"]),
                inflate=bool(raw.get("inflate", True)),
                growth=float(raw.get("growth", 0.0)),
                kind=kind,  # type: ignore[arg-type]
                discretionary=bool(raw.get("discretionary", False)),
            )
        )
    return out


def _build_allocation(raw: dict) -> Allocation:
    """Parse the `allocation` YAML block into an Allocation object.

    Two forms accepted:

      allocation:
        assets:
          stocks: msci_world_total
          bonds:  global_agg_bond_total
        weights:                         # static
          stocks: 0.8
          bonds:  0.2

      allocation:
        assets:
          stocks: msci_world_total
          bonds:  global_agg_bond_total
        glide_path:                      # one entry per pivot age
          - { age: 35, stocks: 0.90, bonds: 0.10 }
          - { age: 60, stocks: 0.60, bonds: 0.40 }
    """
    if "assets" not in raw or not isinstance(raw["assets"], dict):
        raise ValueError("`allocation.assets` must be a mapping name -> series")
    assets = [Asset(name=k, series=v) for k, v in raw["assets"].items()]

    has_weights = "weights" in raw and raw["weights"]
    has_glide = "glide_path" in raw and raw["glide_path"]
    if has_weights and has_glide:
        raise ValueError("`allocation`: set either `weights` (static) or `glide_path`, not both")
    if not has_weights and not has_glide:
        if len(assets) == 1:
            return Allocation(
                assets=assets,
                glide=[GlidePoint(age=0, weights={assets[0].name: 1.0})],
            )
        raise ValueError("`allocation` with multiple assets needs `weights` or `glide_path`")

    if has_weights:
        return Allocation(
            assets=assets,
            glide=[GlidePoint(age=0, weights={k: float(v) for k, v in raw["weights"].items()})],
        )

    glide = []
    for raw_pt in raw["glide_path"]:
        if "age" not in raw_pt:
            raise ValueError(f"glide_path entry missing `age`: {raw_pt}")
        age = int(raw_pt["age"])
        weights = {k: float(v) for k, v in raw_pt.items() if k != "age"}
        glide.append(GlidePoint(age=age, weights=weights))
    return Allocation(assets=assets, glide=glide)


def _build_case(name: str, raw: dict) -> Case:
    raw = copy.deepcopy(raw)
    raw["name"] = name
    raw["incomes"] = _build_streams(raw.get("incomes"), "income")
    raw["expenses"] = _build_streams(raw.get("expenses"), "expense")
    if "allocation" in raw and isinstance(raw["allocation"], dict):
        raw["allocation"] = _build_allocation(raw["allocation"])
    if "pension" in raw and isinstance(raw["pension"], dict):
        raw["pension"] = Pension(**raw["pension"])
    if "tax" in raw and isinstance(raw["tax"], dict):
        if "brackets" in raw["tax"]:
            raw["tax"]["brackets"] = [
                (float("inf") if b[0] in ("inf", None) else float(b[0]), float(b[1]))
                for b in raw["tax"]["brackets"]
            ]
        raw["tax"] = TaxConfig(**raw["tax"])
    if "wealth_tax" in raw and isinstance(raw["wealth_tax"], dict):
        if "brackets" in raw["wealth_tax"]:
            raw["wealth_tax"]["brackets"] = [
                (float("inf") if b[0] in ("inf", None) else float(b[0]), float(b[1]))
                for b in raw["wealth_tax"]["brackets"]
            ]
        raw["wealth_tax"] = WealthTaxConfig(**raw["wealth_tax"])
    if "withdrawal" in raw and isinstance(raw["withdrawal"], dict):
        raw["withdrawal"] = WithdrawalPolicy(**raw["withdrawal"])
    if "spending_curve" in raw and isinstance(raw["spending_curve"], dict):
        sc = raw["spending_curve"]
        raw["spending_curve"] = SpendingCurve(
            enabled=bool(sc.get("enabled", False)),
            pivots=[
                CurvePoint(age=int(p["age"]), factor=float(p["factor"]))
                for p in sc.get("pivots", [])
            ],
        )
    if "dynamic_spending" in raw and isinstance(raw["dynamic_spending"], dict):
        raw["dynamic_spending"] = DynamicSpending(**raw["dynamic_spending"])
    if "stress" in raw and isinstance(raw["stress"], dict):
        raw["stress"] = StressRegime(**raw["stress"])
    valid = {f.name for f in fields(Case)}
    extra = set(raw) - valid
    if extra:
        raise ValueError(f"Unknown case fields for scenario {name!r}: {sorted(extra)}")
    return Case(**raw)


def load_config(path: str | Path) -> list[Case]:
    """Load a YAML file → list of Case objects (one per scenario)."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    defaults = doc.get("defaults", {}) or {}
    scenarios = doc.get("scenarios") or []
    if not scenarios:
        raise ValueError(f"{path}: no scenarios defined")
    cases: list[Case] = []
    for s in scenarios:
        if not isinstance(s, dict) or "name" not in s:
            raise ValueError(f"{path}: each scenario must be a dict with a `name`")
        merged = _deep_merge(defaults, s)
        name = merged.pop("name")
        # Resolve relative data_file paths against the YAML's directory.
        if merged.get("data_file"):
            df = Path(merged["data_file"])
            if not df.is_absolute():
                merged["data_file"] = str((path.parent / df).resolve())
        cases.append(_build_case(name, merged))
    return cases
