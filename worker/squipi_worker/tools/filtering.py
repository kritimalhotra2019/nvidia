"""filter_rows: a subset of a dataset by plain conditions, registered as a derived dataset with lineage.

28 Oct 2026: "exclude the mothers who dropped out before endline and redo the comparison" had no tool to act on;
the model spent thirteen rounds re-running estimators on the full sample and asking clean_dataset to do what it
cannot. Every analysis tool then runs on the subset's dataset_id; the receipt records what was kept and why."""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .datasets import read_dataset

OPS = ["in", "not_in", "eq", "ne", "gt", "ge", "lt", "le", "is_missing", "not_missing", "contains"]
CONDITION = {"type": "object", "properties": {
    "column": {"type": "string"},
    "op": {"type": "string", "enum": OPS},
    "value": {"type": ["string", "number", "boolean", "null"]},
    "values": {"type": ["array", "null"], "items": {"type": ["string", "number", "boolean"]}, "maxItems": 100}},
    "required": ["column", "op"], "additionalProperties": False}


def _col(df: pd.DataFrame, name: str) -> str:
    if name in df.columns:
        return name
    low = {str(c).lower(): str(c) for c in df.columns}
    key = str(name).lower()
    if key in low:
        return low[key]
    squashed = {re.sub(r"[\s_\-]+", "", k): v for k, v in low.items()}
    if re.sub(r"[\s_\-]+", "", key) in squashed:
        return squashed[re.sub(r"[\s_\-]+", "", key)]
    raise InvalidArguments(f"column '{name}' is not in this dataset. Columns: {', '.join(map(str, df.columns[:40]))}")


def _mask(df: pd.DataFrame, cond: Dict[str, Any]) -> pd.Series:
    c = _col(df, cond["column"]); op = cond["op"]; s = df[c]
    if op == "is_missing": return s.isna()
    if op == "not_missing": return s.notna()
    vals = cond.get("values") if isinstance(cond.get("values"), list) else ([cond.get("value")] if cond.get("value") is not None else [])
    if not vals:
        raise InvalidArguments(f"{op} on '{c}' needs a value")
    if op in ("in", "not_in", "eq", "ne"):
        want = {str(v).strip().lower() for v in vals}
        hit = s.astype(str).str.strip().str.lower().isin(want) & s.notna()
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().any():
            try:
                hit = hit | num.isin([float(v) for v in vals if isinstance(v, (int, float)) or re.fullmatch(r"-?\d+(\.\d+)?", str(v).strip())])
            except Exception:
                pass
        return hit if op in ("in", "eq") else (~hit & s.notna())
    if op == "contains":
        return s.astype(str).str.contains(str(vals[0]), case=False, na=False)
    num = pd.to_numeric(s, errors="coerce"); v = float(vals[0])
    return {"gt": num > v, "ge": num >= v, "lt": num < v, "le": num <= v}[op]


def normalize_filter(args: Dict[str, Any]):
    """Canonical condition values (False → 0, '1' → 1, trimmed lower-case text) so `eq 0` and `eq False` are one
    request and the saved subset is reused (28 Oct: two identical subsets in one turn)."""
    notes: List[str] = []
    conds = args.get("conditions")
    if not isinstance(conds, list):
        return args, notes
    def canon(v):
        if isinstance(v, bool): return int(v)
        if isinstance(v, (int, float)): return v
        if isinstance(v, str):
            t = v.strip()
            if re.fullmatch(r"-?\d+", t): return int(t)
            if re.fullmatch(r"-?\d+\.\d+", t): return float(t)
            if t.lower() in ("true", "yes"): return 1
            if t.lower() in ("false", "no"): return 0
            return t.lower()
        return v
    out = []
    for c in conds:
        if isinstance(c, dict):
            c = dict(c)
            if "value" in c and c["value"] is not None: c["value"] = canon(c["value"])
            if isinstance(c.get("values"), list): c["values"] = [canon(v) for v in c["values"]]
        out.append(c)
    args["conditions"] = out
    return args, notes


@tool("filter_rows",
      "Keep only the rows that meet plain conditions (a subset: 'exclude those who dropped out', 'only the treatment arm', "
      "'households with income above 500') and register the subset as a new dataset with lineage. Then run analyses on the "
      "returned dataset_id. Conditions combine with AND; use `exclude` to drop the matching rows instead.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "conditions": {"type": "array", "minItems": 1, "maxItems": 8, "items": CONDITION},
          "exclude": {"type": "boolean", "description": "drop the rows that match instead of keeping them (default false)"},
          "output_name": {"type": ["string", "null"], "description": "name for the subset file (default <name>_subset)"}},
       "required": ["dataset_id", "conditions"], "additionalProperties": False}, normalize=normalize_filter)
def filter_rows(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    ds = ctx.project.dataset(args["dataset_id"])
    ctx.project.allow.check_readable(ds.path)
    df = read_dataset(ds)
    n0 = len(df)
    mask = pd.Series(True, index=df.index)
    described: List[str] = []
    for cond in args["conditions"]:
        m = _mask(df, cond); mask &= m
        c = _col(df, cond["column"]); op = cond["op"]
        vals = cond.get("values") if isinstance(cond.get("values"), list) else ([cond.get("value")] if cond.get("value") is not None else [])
        described.append(f"{c} {op}" + (f" {', '.join(map(str, vals[:6]))}" if vals else ""))
    keep = ~mask if args.get("exclude") else mask
    sub = df[keep]
    if len(sub) == 0:
        raise InvalidArguments("No rows match these conditions; nothing was written. Conditions: " + "; ".join(described))
    out = ctx.project.new_artifact_dir("subset", ds.name)
    stem = re.sub(r"\.[^.]+$", "", ds.name)
    name = args.get("output_name") or f"{stem}_subset"
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise InvalidArguments("Use a subset filename without a directory path")
    if name.lower().endswith(".csv"): name = name[:-4]
    path = out / (name + ".csv")
    sub.to_csv(path, index=False)
    new = ctx.project.register_derived(path, path.name, int(sub.shape[0]), int(sub.shape[1]), ds.id, [str(c) for c in sub.columns])
    ctx.project.write_json(out / "subset.json", {"from": ds.id, "to": new.id, "conditions": args["conditions"], "exclude": bool(args.get("exclude")), "kept": int(len(sub)), "dropped": int(n0 - len(sub))})
    verb = "Excluded rows where" if args.get("exclude") else "Kept rows where"
    summary = (f"{verb} {' and '.join(described)}: {len(sub):,} of {n0:,} rows kept ({n0 - len(sub):,} dropped). "
               f"The subset is dataset {new.id} ({path.name}); run the analysis on it. The original file is unchanged.")
    ctx.project.audit({"tool": "filter_rows", "dataset": ds.id, "to": new.id, "kept": int(len(sub)), "artifact": str(out)})
    return {"dataset_id": new.id, "from_dataset_id": ds.id, "path": str(path), "csv_path": str(path), "rows": int(len(sub)), "rows_before": int(n0),
            "dropped": int(n0 - len(sub)), "columns": [str(c) for c in sub.columns], "conditions": args["conditions"], "exclude": bool(args.get("exclude")),
            "artifact_dir": str(out), "summary_text": summary, "elapsed_s": round(time.time() - t0, 2)}
