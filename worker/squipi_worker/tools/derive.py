"""Derivable arguments (28 Oct 2026). A required argument that the data already determines is filled from the
dataset and reported, never refused: the group column of a comparison, the treatment column of an estimator, the
outcome when none was named, a chart's kind from its columns' roles, the post/time column of a DiD, the follow-up
indicator of an attrition check. One rule for every provider; every fill is listed in `argument_notes`.

Across today's 150 runs the top refusals were exactly these ("missing required 'group'", "'label'", "'kind'",
"give `period` or `time`"), each costing a model round of 60–190 s on a local brain."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..registry import JobContext
from .datasets import read_dataset, analyse_column, SAMPLE_ROWS
from .structure import group_candidates, outcome_candidates

TIME_NAME = re.compile(r"(^|_)(year|yr|period|wave|round|quarter|month|time)($|_)", re.I)
POST_NAME = re.compile(r"(^|_)(post|after|endline|follow_?up|later)($|_)", re.I)
FOLLOWUP_NAME = re.compile(r"(^|_)(observed|follow_?up|followed|endline|complete[d]?|retained|in_sample)($|_)", re.I)


def _frame(ctx: JobContext, dataset_id: Optional[str]):
    if not isinstance(dataset_id, str) or not dataset_id:
        return None, None
    try:
        ds = ctx.project.dataset(dataset_id)
        ctx.project.allow.check_readable(ds.path)
        df = read_dataset(ds, nrows=SAMPLE_ROWS)
    except Exception:
        return None, None
    n = len(df)
    overrides = ds.overrides or {}
    cols = {str(c): analyse_column(str(c), df[c], n, overrides.get(str(c), {}).get("role")) for c in df.columns}
    return df, cols


def _missing(args: Dict[str, Any], key: str) -> bool:
    v = args.get(key)
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def _binary(df: pd.DataFrame, c: str) -> bool:
    return df[c].dropna().nunique() == 2


def _fill_group(args, df, cols, key, notes, label):
    if not _missing(args, key):
        return
    groups = group_candidates(df, cols)
    if groups:
        args[key] = groups[0]["column"]
        levels = ", ".join(f"{k} {v:,}" for k, v in groups[0]["levels"].items())
        notes.append(f"No {label} was given; {groups[0]['column']} ({levels}) is used.")


def _fill_outcome(args, df, cols, key, notes):
    if not _missing(args, key):
        return
    cands = outcome_candidates(df, cols)
    if cands:
        args[key] = cands[0]
        notes.append(f"No outcome was given; {cands[0]} (the first candidate outcome column) is used.")


def derive_compare_groups(args, ctx):
    notes: List[str] = []
    df, cols = _frame(ctx, args.get("dataset_id"))
    if df is None: return args, notes
    _fill_group(args, df, cols, "group", notes, "group column")
    _fill_outcome(args, df, cols, "outcome", notes)
    return args, notes


def derive_estimator(args, ctx):
    notes: List[str] = []
    df, cols = _frame(ctx, args.get("dataset_id"))
    if df is None: return args, notes
    _fill_group(args, df, cols, "treatment", notes, "treatment column")
    _fill_outcome(args, df, cols, "outcome", notes)
    return args, notes


def derive_attrition(args, ctx):
    notes: List[str] = []
    df, cols = _frame(ctx, args.get("dataset_id"))
    if df is None: return args, notes
    _fill_group(args, df, cols, "treatment", notes, "treatment column")
    same = isinstance(args.get("followup_indicator"), str) and args.get("followup_indicator") == args.get("treatment")
    if _missing(args, "followup_indicator") or same:
        cand = [c for c in df.columns if FOLLOWUP_NAME.search(str(c)) and _binary(df, c)]
        if cand:
            args["followup_indicator"] = cand[0]
            notes.append(("The follow-up indicator was the treatment column; " if same else "No follow-up indicator was given; ") + f"{cand[0]} is used (1 = observed at follow-up).")
    return args, notes


def derive_did(args, ctx):
    notes: List[str] = []
    df, cols = _frame(ctx, args.get("dataset_id"))
    if df is None: return args, notes
    _fill_group(args, df, cols, "treatment", notes, "treatment column")
    _fill_outcome(args, df, cols, "outcome", notes)
    if _missing(args, "period") and _missing(args, "time"):
        post = [c for c in df.columns if POST_NAME.search(str(c)) and _binary(df, c)]
        if post:
            args["period"] = post[0]; notes.append(f"No post period was given; the binary column {post[0]} marks the post period.")
        else:
            times = [c for c in df.columns if TIME_NAME.search(str(c)) and pd.to_numeric(df[c], errors="coerce").dropna().nunique() >= 2]
            if times:
                t = times[0]; periods = sorted(pd.to_numeric(df[t], errors="coerce").dropna().unique().tolist())
                args["time"] = t; args["post_periods"] = [p if p != int(p) else int(p) for p in periods[1:]]
                notes.append(f"No post period was given; {t} is the time column and every period after the first ({periods[0]:g}) counts as post.")
    return args, notes


def derive_chart(args, ctx):
    notes: List[str] = []
    if not _missing(args, "kind"):
        return args, notes
    df, cols = _frame(ctx, args.get("dataset_id"))
    x, y = args.get("x"), args.get("y")
    if df is None or not isinstance(x, str) or x not in cols:
        return args, notes
    rx = cols[x]["role"]; ry = cols[y]["role"] if isinstance(y, str) and y in cols else None
    if rx == "date" or (rx == "numeric" and TIME_NAME.search(x)):
        kind = "line" if ry == "numeric" else "bar"
    elif rx == "numeric" and ry == "numeric":
        kind = "scatter"
    elif rx == "numeric" and ry is None:
        kind = "histogram"
    else:
        kind = "bar"
    args["kind"] = kind
    notes.append(f"No chart kind was given; a {kind} chart fits {x}" + (f" against {y}" if ry else "") + ".")
    return args, notes


def derive_randomize(args, ctx):
    """The unit id column of a randomisation is the dataset's identifier column when none is named."""
    notes: List[str] = []
    if not _missing(args, "id_column"):
        return args, notes
    df, cols = _frame(ctx, args.get("dataset_id"))
    if df is None: return args, notes
    ids = [c for c, info in cols.items() if info.get("role") == "id" and df[c].nunique(dropna=True) == df[c].notna().sum()]
    if ids:
        args["id_column"] = ids[0]; notes.append(f"No id column was given; {ids[0]} identifies the units.")
    return args, notes


DERIVERS = {
    "randomize_assignment": derive_randomize,
    "compare_groups": derive_compare_groups,
    "estimate_treatment_effect": derive_estimator,
    "randomized_trial": derive_estimator,
    "attrition_check": derive_attrition,
    "difference_in_differences": derive_did,
    "create_chart": derive_chart,
}


def derive_arguments(tool: str, args: Dict[str, Any], ctx: JobContext) -> Tuple[Dict[str, Any], List[str]]:
    fn = DERIVERS.get(tool)
    if fn is None or not isinstance(args, dict):
        return args, []
    try:
        return fn(dict(args), ctx)
    except Exception as e:                      # a derivation must never turn a clear refusal into a crash
        return args, [f"(argument derivation skipped: {type(e).__name__})"]
