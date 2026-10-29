"""Trial design: randomize_assignment (seeded, stratified, clustered, optional re-randomisation) and pre_analysis_plan (Word + JSON).

The assignment is a deterministic function of the sampling frame, the stated seed and the declared design; the returned
`reproduce_python` snippet re-creates it with numpy alone. The pre-analysis plan copies every figure from the arguments
(power through `plan_research_power`); anything it was not given goes into `checklist`, never into the document as a number."""
from __future__ import annotations

import json
import math
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .. import svg
from .impact import _r4, _not_pii, _binary, _fit, IMBALANCE
from .stats import _load, _col, _save_table, _office_png
from .research_analysis import plan_research_power

MAX_BLOCK = 100
MAX_ARMS = 10
MAX_RERANDOMIZE = 1000
MAX_CHART_BARS = 40
RERANDOMISATION_NOTE = ("Re-randomisation: the reported assignment is the best-balanced of several seeded draws, not a single random draw. "
                        "Report the number of draws and the criterion in the trial record, and prefer randomisation inference "
                        "(re-drawing under the same rule) for p-values; model-based standard errors are conservative under re-randomisation (Morgan and Rubin, 2012).")
REGISTRATION_FIELDS = ["Registry (AEA RCT Registry / OSF) and registration id", "Trial title and short title", "Principal investigators and affiliations",
                       "Country / locations and start and end dates", "Intervention description and start date", "Primary and secondary outcomes (end points)",
                       "Experimental design and randomisation method and unit", "Planned sample size (units, clusters, arms)", "Power calculation inputs",
                       "IRB / ethics approval and number", "Analysis plan document upload (this file)", "Data availability statement"]


# ---------- randomisation helpers

def _block(allocation: Sequence[float]) -> List[int]:
    """The smallest block in which every arm's share is a whole number of units, as a list of arm indices."""
    fr = [Fraction(float(a)).limit_denominator(1000) for a in allocation]
    size = 1
    for f in fr:
        size = size * f.denominator // math.gcd(size, f.denominator)
    if size > MAX_BLOCK:
        raise InvalidArguments(f"allocation {list(allocation)} needs a block of {size} units; use shares expressible in a block of at most {MAX_BLOCK} (e.g. 0.5/0.5, 1/3 each, 0.6/0.4)")
    block: List[int] = []
    for i, f in enumerate(fr):
        block.extend([i] * int(f * size))
    return block


def _draw(groups: List[Tuple[str, List[str]]], block: List[int], rng: np.random.Generator) -> Dict[str, int]:
    """Permuted-block assignment within each stratum. Units are shuffled, full blocks are shuffled copies of `block`,
    and the last partial block is a random subset of it without replacement. Mirrors `reproduce_python` exactly."""
    arm_of: Dict[str, int] = {}
    for _key, units in groups:
        n = len(units)
        perm = [units[i] for i in rng.permutation(n)]
        seq: List[int] = []
        for start in range(0, n, len(block)):
            size = min(len(block), n - start)
            seq.extend(rng.permutation(block).tolist() if size == len(block) else rng.choice(block, size, replace=False).tolist())
        arm_of.update(zip(perm, seq))
    return arm_of


def _std_diff(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 2 or len(b) < 2:
        return None
    sp = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
    return float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0


def _max_abs_d(arm_idx: np.ndarray, cols: Dict[str, np.ndarray], n_arms: int) -> Optional[float]:
    worst = None
    for v in cols.values():
        for i in range(n_arms):
            for j in range(i + 1, n_arms):
                d = _std_diff(v[arm_idx == i], v[arm_idx == j])
                if d is not None and (worst is None or abs(d) > worst):
                    worst = abs(d)
    return worst


def _joint_test(y: np.ndarray, cols: Dict[str, np.ndarray], groups: Optional[np.ndarray]) -> Dict[str, Any]:
    """Regress the arm indicator on the balance columns; F test that every slope is zero (HC1 or cluster-robust)."""
    import statsmodels.api as sm
    X = np.column_stack([np.ones(len(y))] + [cols[c] for c in cols])
    ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
    k = X.shape[1] - 1
    if ok.sum() < k + 3:
        return {"f": None, "p": None, "n": int(ok.sum()), "df": k, "note": "too few complete rows for the joint test"}
    try:
        if groups is not None:
            res = sm.OLS(y[ok], X[ok]).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(groups[ok])[0]})
        else:
            res = sm.OLS(y[ok], X[ok]).fit(cov_type="HC1")
        R = np.eye(k + 1)[1:]
        ft = res.f_test(R)
        return {"f": _r4(float(np.squeeze(ft.fvalue))), "p": _r4(float(ft.pvalue)), "n": int(ok.sum()), "df": k,
                "se_kind": f"cluster-robust ({len(np.unique(groups[ok]))} clusters)" if groups is not None else "HC1 robust"}
    except Exception as e:  # noqa: BLE001 - singular designs: say so instead of failing the assignment
        return {"f": None, "p": None, "n": int(ok.sum()), "df": k, "note": f"joint test not computed ({type(e).__name__})"}


def _balance_table(arm_idx: np.ndarray, arms: List[str], cols: Dict[str, np.ndarray], groups: Optional[np.ndarray]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, v in cols.items():
        for i in range(len(arms)):
            for j in range(i + 1, len(arms)):
                sel = (arm_idx == i) | (arm_idx == j)
                a, b = v[arm_idx == i], v[arm_idx == j]
                a, b = a[np.isfinite(a)], b[np.isfinite(b)]
                d = _std_diff(a, b)
                p = None
                ok = sel & np.isfinite(v)
                if len(a) >= 2 and len(b) >= 2 and (a.var(ddof=1) > 0 or b.var(ddof=1) > 0):
                    X = np.column_stack([np.ones(int(ok.sum())), (arm_idx[ok] == i).astype(float)])
                    try:
                        fit = _fit(X, v[ok], groups[ok] if groups is not None else None)
                        p = None if np.isnan(fit["p"][1]) else float(fit["p"][1])
                    except Exception:  # noqa: BLE001
                        p = None
                rows.append({"column": name, "comparison": f"{arms[i]} vs {arms[j]}", f"mean_{arms[i]}": _r4(a.mean()) if len(a) else None, f"n_{arms[i]}": int(len(a)),
                             f"mean_{arms[j]}": _r4(b.mean()) if len(b) else None, f"n_{arms[j]}": int(len(b)),
                             "difference": _r4(a.mean() - b.mean()) if len(a) and len(b) else None, "std_diff": _r4(d), "p": _r4(p),
                             "flag": ("imbalanced" if abs(d) > IMBALANCE else "balanced") if d is not None else "n/a"})
    return rows


def _read_call(ds: Any) -> str:
    if ds.format == "excel":
        return f"pd.read_excel({str(ds.path)!r}, sheet_name={(ds.sheet if ds.sheet is not None else 0)!r})"
    if ds.format == "parquet":
        return f"pd.read_parquet({str(ds.path)!r})"
    return f"pd.read_csv({str(ds.path)!r}, skipinitialspace=True)"


def _reproduce_snippet(ds: Any, seed: int, chosen: int, draws: int, arms: List[str], block: List[int], id_col: str, strata: List[str], cluster: Optional[str]) -> str:
    return f'''# Re-creates the arm assignment from the sampling frame and seed {seed} (numpy for the randomness; pandas only reads the file).
import numpy as np, pandas as pd
SEED = {seed}; CHOSEN_DRAW = {chosen}  # draw {chosen + 1} of {draws}; draws are consecutive from one generator seeded once
ARMS = {arms!r}; BLOCK = {block!r}; ID = {id_col!r}; STRATA = {strata!r}; CLUSTER = {cluster!r}
frame = {_read_call(ds)}
unit = frame[CLUSTER if CLUSTER else ID].astype(str)
key = frame[STRATA].astype(str).agg(" | ".join, axis=1) if STRATA else pd.Series("all", index=frame.index)
groups = {{}}
for k, u in sorted(set(zip(key, unit))):
    groups.setdefault(k, []).append(u)
rng = np.random.default_rng(SEED)
for _ in range(CHOSEN_DRAW + 1):
    arm_of = {{}}
    for k in sorted(groups):
        us = groups[k]; n = len(us)
        perm = [us[i] for i in rng.permutation(n)]
        seq = []
        for start in range(0, n, len(BLOCK)):
            size = min(len(BLOCK), n - start)
            seq.extend(rng.permutation(BLOCK).tolist() if size == len(BLOCK) else rng.choice(BLOCK, size, replace=False).tolist())
        arm_of.update(zip(perm, seq))
frame["arm"] = [ARMS[arm_of[u]] for u in unit]
print(frame["arm"].value_counts())
'''


# ---------- randomize_assignment

def normalize_randomize(args: Dict[str, Any]):
    """0.33 × 3 = 0.99 is three equal arms, not an error (28 Oct): shares within 0.02 of 1 are scaled and reported."""
    notes: List[str] = []
    alloc = args.get("allocation")
    if isinstance(alloc, list) and alloc and all(isinstance(a, (int, float)) and not isinstance(a, bool) and a > 0 for a in alloc):
        total = float(sum(alloc))
        if abs(total - 1.0) <= 0.02 and total != 1.0:
            args["allocation"] = [round(float(a) / total, 6) for a in alloc]
            notes.append(f"Allocation shares summed to {total:.2f}; they were scaled to 1.")
    return args, notes


@tool("randomize_assignment",
      "Randomly assign the units of a sampling frame to arms with a stated seed (required, so the draw is reproducible by design): permuted blocks within strata, "
      "whole clusters when `cluster_column` is given, optional re-randomisation that keeps the best-balanced of several draws (labelled as such). "
      "Registers '<frame> assignment.csv' (id, strata, cluster, arm), returns counts per arm and per stratum × arm, a balance table (standardized differences, "
      "joint F test with cluster-robust SE when clustered), the procedure in words and a numpy snippet that re-creates the assignment.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string", "description": "the sampling frame: one row per unit (or per unit within clusters)"},
          "id_column": {"type": "string", "description": "unique unit identifier"},
          "arms": {"type": ["array", "null"], "items": {"type": "string", "minLength": 1}, "minItems": 2, "maxItems": MAX_ARMS, "description": "arm names (default treatment, control)"},
          "allocation": {"type": ["array", "null"], "items": {"type": "number", "minimum": 0, "maximum": 1}, "description": "share per arm, summing to 1 (default equal)"},
          "strata": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 5, "description": "columns to block on; randomisation happens within each stratum"},
          "cluster_column": {"type": ["string", "null"], "description": "assign whole clusters (e.g. schools); strata must be constant within a cluster"},
          "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "description": "random seed stated by the user; required"},
          "balance_columns": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 30, "description": "numeric or binary columns to check after assignment"},
          "rerandomize_max": {"type": "integer", "minimum": 0, "maximum": MAX_RERANDOMIZE, "description": "0 = one draw; > 0 = keep the draw with the smallest max |standardized difference| on balance_columns"},
          "allow_small_strata": {"type": "boolean", "description": "warn instead of refusing when a stratum has fewer units than arms (default false)"}},
       "required": ["dataset_id", "id_column", "seed"], "additionalProperties": False}, normalize=normalize_randomize)
def randomize_assignment(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    seed = args.get("seed")
    if seed is None or isinstance(seed, bool) or not isinstance(seed, int):
        raise InvalidArguments("seed is required: state the integer seed so the assignment can be reproduced; the tool never picks one")
    df, ds, sampled = _load(ctx, args["dataset_id"])
    if sampled:
        raise InvalidArguments(f"the frame has {ds.rows:,} rows, above the full-analysis limit; a randomisation must cover every unit, so filter the frame first")
    id_col = _col(df, args["id_column"], "id_column")
    ids = df[id_col]
    if ids.isna().any():
        raise InvalidArguments(f"id_column '{id_col}' has {int(ids.isna().sum())} missing values; every unit needs an id")
    dup = ids.astype(str).duplicated()
    cluster = _col(df, args["cluster_column"], "cluster_column") if args.get("cluster_column") else None
    if dup.any() and not cluster:
        ex = ids.astype(str)[dup].unique()[:5].tolist()
        raise InvalidArguments(f"id_column '{id_col}' is not unique ({int(dup.sum())} duplicate rows, e.g. {', '.join(ex)}); de-duplicate the frame or name a cluster_column")
    if dup.any() and cluster:
        raise InvalidArguments(f"id_column '{id_col}' is not unique ({int(dup.sum())} duplicate rows); the frame needs one row per unit even when clusters are randomised")
    arms = [str(a).strip() for a in (args.get("arms") or ["treatment", "control"])]
    if len(set(arms)) != len(arms) or any(not a for a in arms):
        raise InvalidArguments("arms must be distinct non-empty names")
    allocation = [float(a) for a in (args.get("allocation") or [1.0 / len(arms)] * len(arms))]
    if len(allocation) != len(arms):
        raise InvalidArguments(f"allocation has {len(allocation)} shares for {len(arms)} arms")
    if any(a <= 0 for a in allocation) or abs(sum(allocation) - 1.0) > 1e-4:
        raise InvalidArguments(f"allocation shares must be positive and sum to 1 (they sum to {sum(allocation):.4f})")
    block = _block(allocation)
    strata = [_not_pii(df, _col(df, c, "stratum column"), "stratum column") for c in (args.get("strata") or [])]
    if len(set(strata)) != len(strata) or id_col in strata:
        raise InvalidArguments("strata must be distinct columns other than the id")
    bal_cols = [_not_pii(df, _col(df, c, "balance column"), "balance column") for c in (args.get("balance_columns") or [])]
    if id_col in bal_cols:
        raise InvalidArguments("the id column cannot be a balance column")
    if cluster and (cluster == id_col or cluster in strata):
        raise InvalidArguments("cluster_column must differ from the id and the strata")
    if cluster and df[cluster].isna().any():
        raise InvalidArguments(f"cluster_column '{cluster}' has missing values; every unit needs a cluster")
    for c in strata:
        if df[c].isna().any():
            raise InvalidArguments(f"stratum column '{c}' has {int(df[c].isna().sum())} missing values; fill or drop them before randomising")
    rerand = int(args.get("rerandomize_max") or 0)
    if rerand > 0 and not bal_cols:
        raise InvalidArguments("rerandomize_max needs balance_columns to judge the draws on")
    notes: List[str] = []
    # ---- units and strata (strings, sorted: the assignment depends on the frame's content, not its row order)
    unit = df[cluster].astype(str) if cluster else ids.astype(str)
    key = df[strata].astype(str).agg(" | ".join, axis=1) if strata else pd.Series("all", index=df.index)
    if cluster:
        per_cluster = pd.DataFrame({"u": unit, "k": key}).groupby("u")["k"].nunique()
        bad = per_cluster[per_cluster > 1]
        if len(bad):
            raise InvalidArguments(f"strata must be constant within a cluster; {len(bad)} clusters span several strata (e.g. {', '.join(map(str, bad.index[:5]))})")
    groups_map: Dict[str, List[str]] = {}
    for k, u in sorted(set(zip(key, unit))):
        groups_map.setdefault(k, []).append(u)
    groups = [(k, groups_map[k]) for k in sorted(groups_map)]
    small = [(k, len(us)) for k, us in groups if len(us) < len(arms)]
    if small:
        text = "; ".join(f"'{k}' has {n}" for k, n in small[:6])
        if not args.get("allow_small_strata"):
            raise InvalidArguments(f"{len(small)} strata have fewer units than arms ({len(arms)}): {text}. Merge strata, or set allow_small_strata to proceed with a warning")
        notes.append(f"Warning: {len(small)} strata have fewer units than arms ({text}); those strata cannot hold every arm, so the overall allocation is only approximate there.")
    # ---- balance columns as float arrays over rows
    cols: Dict[str, np.ndarray] = {}
    for c in bal_cols:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            cols[c] = pd.to_numeric(s, errors="coerce").astype(float).to_numpy()
        else:
            cols[c] = _binary(df, c, "balance column")[0].to_numpy(float)
    unit_arr = unit.to_numpy()
    ctx.progress(20, "drawing")
    rng = np.random.default_rng(seed)
    n_draws = max(1, rerand)
    best: Optional[Dict[str, int]] = None
    best_d: Optional[float] = None
    first_d: Optional[float] = None
    chosen = 0
    for k in range(n_draws):
        if k % 50 == 0:
            ctx.check()
        arm_of = _draw(groups, block, rng)
        if n_draws == 1:
            best, chosen = arm_of, 0
            break
        arm_idx = np.fromiter((arm_of[u] for u in unit_arr), dtype=int, count=len(unit_arr))
        d = _max_abs_d(arm_idx, cols, len(arms))
        if k == 0:
            first_d = d
        if d is None:
            raise InvalidArguments("re-randomisation needs balance columns with at least two observed values per arm")
        if best is None or d < best_d:
            best, best_d, chosen = arm_of, d, k
    assert best is not None
    arm_idx = np.fromiter((best[u] for u in unit_arr), dtype=int, count=len(unit_arr))
    arm_names = np.array(arms)[arm_idx]
    ctx.progress(60, "balance")
    groups_arr = unit_arr if cluster else None
    balance = _balance_table(arm_idx, arms, cols, groups_arr) if cols else []
    joint = []
    if cols:
        ref = len(arms) - 1
        for i in range(len(arms) - 1):
            sel = (arm_idx == i) | (arm_idx == ref)
            y = (arm_idx[sel] == i).astype(float)
            jt = _joint_test(y, {c: v[sel] for c, v in cols.items()}, groups_arr[sel] if groups_arr is not None else None)
            joint.append({"comparison": f"{arms[i]} vs {arms[ref]}", **jt})
    imbalanced = sorted({r["column"] for r in balance if r["flag"] == "imbalanced"})
    # ---- counts
    counts_per_arm = {a: int((arm_names == a).sum()) for a in arms}
    clusters_per_arm = ({a: int(len({u for u, i in best.items() if arms[i] == a})) for a in arms}) if cluster else None
    strat_rows: List[Dict[str, Any]] = []
    for k, us in groups:
        row: Dict[str, Any] = {"stratum": k}
        in_k = (key == k).to_numpy()
        for a in arms:
            row[a] = int(((arm_names == a) & in_k).sum())
        row["total"] = int(in_k.sum())
        if cluster:
            row["clusters"] = len(us)
        strat_rows.append(row)
    # ---- outputs
    out = ctx.project.new_artifact_dir("randomisation", Path(ds.name).stem)
    assignment = pd.DataFrame({id_col: ids.to_numpy()})
    for c in strata:
        assignment[c] = df[c].to_numpy()
    if cluster:
        assignment[cluster] = df[cluster].to_numpy()
    assignment["arm"] = arm_names
    name = f"{Path(ds.name).stem} assignment.csv"
    path = out / name
    assignment.to_csv(path, index=False)
    new = ctx.project.register_derived(path, name, int(len(assignment)), int(assignment.shape[1]), ds.id, [str(c) for c in assignment.columns])
    counts_df = pd.DataFrame(strat_rows)
    counts_path = _save_table(ctx, out, "counts", counts_df)
    csv_path = _save_table(ctx, out, "balance", pd.DataFrame(balance)) if balance else counts_path
    if len(groups) * len(arms) <= MAX_CHART_BARS:
        labels = [f"{r['stratum']} · {a}" for r in strat_rows for a in arms]
        values = [r[a] for r in strat_rows for a in arms]
        title = "Units assigned per stratum and arm" if strata else "Units assigned per arm"
    else:
        labels, values, title = arms, [counts_per_arm[a] for a in arms], "Units assigned per arm"
        notes.append(f"{len(groups)} strata × {len(arms)} arms is too many bars; the chart shows arms only (the per-stratum counts are in counts.csv).")
    chart = svg.bars(title, labels, values, subtitle=f"seed {seed} · n = {len(df):,}" + (f" · {sum(len(us) for _, us in groups):,} clusters" if cluster else ""), value_fmt=lambda v: f"{int(v):,}")
    chart_path = str(ctx.project.write_text(out / "assignment.svg", chart))
    _office_png(chart_path, "bars", title, labels, values)
    # ---- words
    alloc_text = ", ".join(f"{a} {p:.0%}" if abs(p * 100 - round(p * 100)) < 1e-9 else f"{a} {p:.1%}" for a, p in zip(arms, allocation))
    unit_word = f"clusters ('{cluster}')" if cluster else f"units ('{id_col}')"
    procedure = (f"Seeded permuted-block randomisation (numpy default_rng, seed {seed}). {unit_word.capitalize()} were listed as strings and sorted"
                 + (f" within strata defined by {', '.join(strata)} ({len(groups)} strata, processed in sorted order)" if strata else " (no strata)")
                 + f"; within each stratum the units were shuffled and assigned in blocks of {len(block)} ({alloc_text}), each block a random permutation of the arm pattern "
                 f"and a final partial block a random subset of it, so every stratum's counts stay within one block of the allocation.")
    if cluster:
        procedure += f" All rows of a cluster share its arm ({len(df):,} rows in {sum(len(us) for _, us in groups):,} clusters)."
    rerandomisation = None
    if n_draws > 1:
        rerandomisation = {"draws": n_draws, "chosen_draw": chosen + 1, "criterion": "smallest max |standardized difference| across balance_columns and arm pairs",
                           "first_draw_max_abs_std_diff": _r4(first_d), "chosen_max_abs_std_diff": _r4(best_d)}
        procedure += (f" Re-randomisation: {n_draws} consecutive draws were taken from the same generator and draw {chosen + 1} was kept because its "
                      f"max |standardized difference| ({_r4(best_d)}) was the smallest (first draw: {_r4(first_d)}).")
        notes.append(RERANDOMISATION_NOTE)
    snippet = _reproduce_snippet(ds, seed, chosen, n_draws, arms, block, id_col, strata, cluster)
    ctx.project.write_text(out / "reproduce_assignment.py", snippet)
    count_text = ", ".join(f"{a} {counts_per_arm[a]:,}" for a in arms)
    interp = [f"{len(df):,} units" + (f" in {sum(len(us) for _, us in groups):,} clusters" if cluster else "") + f" were assigned to {len(arms)} arms ({count_text}) with seed {seed}"
              + (f" within {len(groups)} strata" if strata else "") + "."]
    if balance:
        interp.append(f"Balance: {len(balance) - sum(r['flag'] == 'imbalanced' for r in balance)} of {len(balance)} comparisons have |standardized difference| ≤ {IMBALANCE}"
                      + (f"; imbalanced: {', '.join(imbalanced[:6])}." if imbalanced else ".")
                      + "".join(f" Joint F test {j['comparison']}: p = {j['p']}." for j in joint if j.get("p") is not None))
    if rerandomisation:
        interp.append(f"This is a re-randomised assignment: the best-balanced of {n_draws} draws (max |d| {_r4(best_d)} versus {_r4(first_d)} on the first draw).")
    interp.append("Any imbalance seen here arose by chance under the stated seed; the design, seed and snippet are what make the assignment reproducible, so record them with the trial.")
    ctx.project.audit({"tool": "randomize_assignment", "dataset": ds.id, "seed": seed, "arms": arms, "n": int(len(df)), "assignment": new.id, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": new.id, "dataset_name": new.name, "frame_dataset_id": ds.id, "assignment_path": str(path), "id_column": id_col, "arms": arms,
            "allocation": [_r4(a) for a in allocation], "block_size": len(block), "strata": strata, "cluster_column": cluster, "seed": seed, "n": int(len(df)),
            "n_units": sum(len(us) for _, us in groups), "counts_per_arm": counts_per_arm, "clusters_per_arm": clusters_per_arm, "counts_per_stratum_arm": strat_rows,
            "balance": balance, "joint_test": joint, "imbalanced_columns": imbalanced, "rerandomisation": rerandomisation, "procedure": procedure,
            "reproduce_python": snippet, "reproduce_path": str(out / "reproduce_assignment.py"), "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"{title.replace('Units', 'Rows') if cluster else title} (seed {seed}); total n = {len(df):,}.", "chart_path": chart_path,
            "csv_path": csv_path, "counts_path": counts_path, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- pre_analysis_plan

OUTCOME_SCHEMA = {"type": "array", "items": {"type": "object", "properties": {
    "name": {"type": "string", "minLength": 1}, "column": {"type": ["string", "null"]}, "direction": {"type": ["string", "null"], "description": "e.g. higher is better"},
    "unit": {"type": ["string", "null"]}, "description": {"type": ["string", "null"]}}, "required": ["name"], "additionalProperties": False}, "maxItems": 30}


def _or(v: Any, dash: str = "—") -> Any:
    return dash if v is None or v == "" or v == [] else v


def _deff(power: Dict[str, Any], checklist: List[str], cluster_level: bool) -> Tuple[Optional[float], str]:
    if power.get("design_effect") is not None:
        return float(power["design_effect"]), "design_effect as given"
    icc, m = power.get("icc"), power.get("cluster_size")
    if icc is not None and m is not None:
        return 1.0 + (float(m) - 1.0) * float(icc), f"1 + (cluster_size − 1) × ICC = 1 + ({m} − 1) × {icc}"
    if cluster_level:
        checklist.append("power: the randomisation is at cluster level but no ICC and cluster_size (or design_effect) were given, so the power calculation treats units as independent")
    return None, "none (independent units)"


@tool("pre_analysis_plan",
      "Write a pre-analysis plan as a Word document (numbered sections: background, hypotheses, outcomes table, randomisation, sample and power table, estimation, "
      "multiple testing, heterogeneity, data rules, deviations log, AEA/OSF registration checklist) with a JSON sidecar of the plan. Every figure is copied from the "
      "arguments; power numbers come from plan_research_power; whatever is missing is listed in `checklist` rather than filled in.",
      {"type": "object", "properties": {
          "title": {"type": "string", "minLength": 1, "maxLength": 150},
          "background": {"type": ["string", "null"], "description": "context, the question and why it matters"},
          "treatment": {"type": ["string", "null"], "description": "what the treatment arm receives, when and for how long"},
          "primary_outcomes": OUTCOME_SCHEMA,
          "secondary_outcomes": OUTCOME_SCHEMA,
          "randomisation": {"type": ["object", "null"], "properties": {
              "level": {"type": ["string", "null"], "description": "unit or cluster (name the cluster, e.g. school)"},
              "strata": {"type": ["array", "null"], "items": {"type": "string"}},
              "arms": {"type": ["array", "null"], "items": {"type": "string"}},
              "allocation": {"type": ["array", "null"], "items": {"type": "number"}},
              "seed": {"type": ["integer", "null"]},
              "method": {"type": ["string", "null"], "description": "e.g. permuted blocks within strata; from randomize_assignment's `procedure`"}}, "additionalProperties": False},
          "sample": {"type": ["object", "null"], "properties": {
              "n": {"type": ["integer", "null"], "minimum": 1}, "clusters": {"type": ["integer", "null"], "minimum": 1},
              "expected_attrition_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 90},
              "eligibility": {"type": ["string", "null"]}}, "additionalProperties": False},
          "hypotheses": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 30},
          "estimation": {"type": ["object", "null"], "properties": {
              "estimator": {"type": ["string", "null"], "enum": ["ITT", "TOT", "ANCOVA", None]},
              "covariates": {"type": ["array", "null"], "items": {"type": "string"}},
              "clustering": {"type": ["string", "null"], "description": "level standard errors are clustered at"},
              "multiple_testing": {"type": ["string", "null"], "enum": ["BH", "Bonferroni", "none", None]},
              "heterogeneity": {"type": ["array", "null"], "items": {"type": "string"}, "description": "pre-specified subgroup dimensions"},
              "notes": {"type": ["string", "null"]}}, "additionalProperties": False},
          "power": {"type": ["object", "null"], "properties": {
              "mde": {"type": ["number", "null"], "minimum": 0, "description": "minimum detectable effect in outcome units"},
              "effect": {"type": ["number", "null"], "minimum": 0, "description": "expected effect (used as the MDE when mde is absent)"},
              "sd": {"type": ["number", "null"], "minimum": 0}, "alpha": {"type": ["number", "null"], "minimum": 0.001, "maximum": 0.2},
              "power": {"type": ["number", "null"], "minimum": 0.5, "maximum": 0.999}, "icc": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
              "cluster_size": {"type": ["number", "null"], "minimum": 1}, "design_effect": {"type": ["number", "null"], "minimum": 1},
              "outcome": {"type": ["string", "null"], "description": "which outcome the power inputs refer to"}}, "additionalProperties": False},
          "analysis_data_rules": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 40, "description": "exclusion, outlier and missing-data rules"},
          "timeline": {"type": ["object", "null"], "properties": {
              "baseline": {"type": ["string", "null"]}, "intervention": {"type": ["string", "null"]}, "endline": {"type": ["string", "null"]},
              "analysis": {"type": ["string", "null"]}, "stopping_rules": {"type": ["string", "null"]}}, "additionalProperties": False},
          "filename": {"type": ["string", "null"], "description": "override for the .docx name"}},
       "required": ["title", "primary_outcomes"], "additionalProperties": False})
def pre_analysis_plan(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from .export import _write_docx, office_filename
    from .document_style import normalize_style
    from ..templates import resolve_template
    t0 = time.time()
    title = args["title"].strip()
    checklist: List[str] = []
    notes: List[str] = []
    primary = args.get("primary_outcomes") or []
    secondary = args.get("secondary_outcomes") or []
    rand = args.get("randomisation") or {}
    sample = args.get("sample") or {}
    est = args.get("estimation") or {}
    power = args.get("power") or {}
    rules = args.get("analysis_data_rules") or []
    timeline = args.get("timeline") or {}
    hyps = args.get("hypotheses") or []
    if not primary:
        raise InvalidArguments("at least one primary outcome is needed; a plan without a primary outcome cannot be registered")
    names = [o["name"] for o in primary + secondary]
    if len(set(names)) != len(names):
        raise InvalidArguments("outcome names must be distinct across primary and secondary outcomes")
    # ---- checklist of what was not given
    if not args.get("background"):
        checklist.append("background: no study background or research question given")
    if not args.get("treatment"):
        checklist.append("treatment: no description of what the treatment arm receives")
    if not hyps:
        checklist.append("hypotheses: none stated")
    if not secondary:
        checklist.append("secondary outcomes: none specified")
    for kind, outs in (("primary", primary), ("secondary", secondary)):
        for o in outs:
            if not o.get("column"):
                checklist.append(f"{kind} outcome '{o['name']}': no data column named")
            if not o.get("direction"):
                checklist.append(f"{kind} outcome '{o['name']}': no direction of the expected effect")
            if not o.get("unit"):
                checklist.append(f"{kind} outcome '{o['name']}': no unit of measurement")
    cluster_level = bool(rand.get("level")) and str(rand.get("level")).strip().lower() != "unit"
    if not rand.get("level"):
        checklist.append("randomisation: level (unit or cluster) not stated")
    if not rand.get("arms"):
        checklist.append("randomisation: arms not listed")
    if rand.get("arms") and rand.get("allocation") and len(rand["arms"]) != len(rand["allocation"]):
        raise InvalidArguments("randomisation.allocation must have one share per arm")
    if rand.get("allocation") and abs(sum(rand["allocation"]) - 1) > 1e-6:
        raise InvalidArguments("randomisation.allocation shares must sum to 1")
    if rand.get("seed") is None:
        checklist.append("randomisation: no seed recorded (run randomize_assignment with a stated seed and copy it here)")
    if not rand.get("method"):
        checklist.append("randomisation: method not described (copy `procedure` from randomize_assignment)")
    if sample.get("n") is None:
        checklist.append("sample: planned n not given")
    if cluster_level and sample.get("clusters") is None:
        checklist.append("sample: cluster-level design but the number of clusters is not given")
    if sample.get("expected_attrition_pct") is None:
        checklist.append("sample: no attrition allowance (expected_attrition_pct)")
    if not est.get("estimator"):
        checklist.append("estimation: estimator (ITT / TOT / ANCOVA) not chosen")
    if est.get("covariates") is None:
        checklist.append("estimation: covariates not listed (give [] to commit to none)")
    if not est.get("clustering"):
        checklist.append("estimation: standard-error clustering level not stated" + (" although randomisation is at cluster level" if cluster_level else ""))
    if not est.get("multiple_testing"):
        checklist.append("multiple testing: no rule chosen (BH, Bonferroni or none)" + (f" with {len(primary)} primary outcomes" if len(primary) > 1 else ""))
    elif est.get("multiple_testing") == "none" and len(primary) > 1:
        notes.append(f"{len(primary)} primary outcomes with no multiple-testing adjustment: say in the plan why one family-wise error rate is not needed.")
    if est.get("heterogeneity") is None:
        checklist.append("heterogeneity: no pre-specified subgroup dimensions (give [] to commit to none)")
    if not rules:
        checklist.append("data rules: no exclusion, outlier or missing-data rules")
    if not timeline:
        checklist.append("timeline: no baseline / intervention / endline dates or stopping rules")
    # ---- power through plan_research_power (the same arithmetic, not a copy)
    power_result: Optional[Dict[str, Any]] = None
    power_rows: List[List[Any]] = []
    mde = power.get("mde") if power.get("mde") is not None else power.get("effect")
    if mde is None or power.get("sd") is None:
        checklist.append("power: minimum detectable effect (mde or effect) and outcome sd are needed; no power calculation was made")
    else:
        alpha = power.get("alpha") if power.get("alpha") is not None else 0.05
        pw = power.get("power") if power.get("power") is not None else 0.8
        if power.get("alpha") is None:
            notes.append("alpha not given: the conventional 0.05 was used and is shown as a default in the power table.")
        if power.get("power") is None:
            notes.append("power not given: the conventional 0.8 was used and is shown as a default in the power table.")
        deff, deff_text = _deff(power, checklist, cluster_level)
        attr = sample.get("expected_attrition_pct")
        pargs: Dict[str, Any] = {"difference": float(mde), "sd": float(power["sd"]), "alpha": float(alpha), "power": float(pw)}
        if deff is not None:
            pargs["design_effect"] = float(deff)
        if attr:
            pargs["attrition"] = float(attr) / 100.0
        power_result = {"inputs": pargs, **plan_research_power(ctx, pargs)}
        power_rows = [["Outcome", _or(power.get("outcome") or primary[0]["name"])],
                      ["Minimum detectable effect", f"{mde} {primary[0].get('unit') or ''}".strip()], ["Outcome SD", power["sd"]],
                      ["Alpha (two-sided)", f"{alpha}" + (" (default)" if power.get("alpha") is None else "")], ["Power", f"{pw}" + (" (default)" if power.get("power") is None else "")],
                      ["Design effect", f"{_r4(deff)} — {deff_text}" if deff is not None else deff_text],
                      ["Attrition allowance", f"{attr}%" if attr is not None else "none"],
                      ["Independent complete cases per arm", power_result["independent_complete_cases_per_arm"]],
                      ["Recruit per arm", power_result["per_arm"]], ["Recruit in total", power_result["total"]]]
        if sample.get("n") is not None and int(sample["n"]) < power_result["total"]:
            notes.append(f"Planned n ({sample['n']:,}) is below the recruitment total the power inputs imply ({power_result['total']:,}).")
    # ---- sections
    def outcome_table(outs: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"columns": ["Outcome", "Column", "Direction", "Unit", "Description"],
                "rows": [[o["name"], _or(o.get("column")), _or(o.get("direction")), _or(o.get("unit")), _or(o.get("description"))] for o in outs]}
    def bullets(items: Sequence[str], empty: str) -> str:
        return "\n".join(f"- {i}" for i in items) if items else empty
    rand_rows = [["Level", _or(rand.get("level"))], ["Strata", ", ".join(rand.get("strata") or []) or "—"], ["Arms", ", ".join(rand.get("arms") or []) or "—"],
                 ["Allocation", ", ".join(f"{a}: {p}" for a, p in zip(rand.get("arms") or [], rand.get("allocation") or [])) or "—"],
                 ["Seed", _or(rand.get("seed"))], ["Method", _or(rand.get("method"))]]
    sample_rows = [["Planned n", _or(sample.get("n"))], ["Clusters", _or(sample.get("clusters"))], ["Expected attrition", f"{sample['expected_attrition_pct']}%" if sample.get("expected_attrition_pct") is not None else "—"],
                   ["Eligibility", _or(sample.get("eligibility"))]]
    sections: List[Dict[str, Any]] = [
        {"heading": "1. Background", "text": args.get("background") or "Not specified (see checklist)."},
        {"heading": "2. Hypotheses", "text": bullets(hyps, "No hypotheses stated (see checklist).")},
        {"heading": "3. Outcomes", "text": "Primary outcomes:", "table": outcome_table(primary)},
        {"heading": "", "text": "Secondary outcomes:" if secondary else "Secondary outcomes: none specified (see checklist).", **({"table": outcome_table(secondary)} if secondary else {})},
        {"heading": "4. Randomisation", "text": (f"Treatment: {args['treatment']}" if args.get("treatment") else "Treatment: not described (see checklist)."), "table": {"columns": ["Item", "Value"], "rows": rand_rows}},
        {"heading": "5. Sample and power", "text": "Sample:", "table": {"columns": ["Item", "Value"], "rows": sample_rows}},
        {"heading": "", "text": ("Power (computed with plan_research_power: two-sided normal approximation, equal allocation, continuous outcome):" if power_rows
                                 else "Power: no calculation was possible because the minimum detectable effect and outcome SD were not given (see checklist)."),
         **({"table": {"columns": ["Input / result", "Value"], "rows": power_rows}} if power_rows else {})},
        {"heading": "6. Estimation", "text": "\n\n".join([f"Estimator: {_or(est.get('estimator'), 'not chosen (see checklist)')}.",
                                                          "Covariates: " + (", ".join(est["covariates"]) if est.get("covariates") else ("none (pre-committed)" if est.get("covariates") == [] else "not listed (see checklist)")) + ".",
                                                          f"Standard errors clustered at: {_or(est.get('clustering'), 'not stated (see checklist)')}."] + ([est["notes"]] if est.get("notes") else []))},
        {"heading": "7. Multiple testing", "text": {"BH": "Benjamini–Hochberg false discovery rate control within each outcome family.",
                                                     "Bonferroni": "Bonferroni family-wise correction within each outcome family.",
                                                     "none": "No adjustment: each outcome is tested on its own."}.get(est.get("multiple_testing") or "", "No rule chosen (see checklist).")
         + (f" Families: {len(primary)} primary and {len(secondary)} secondary outcomes.")},
        {"heading": "8. Heterogeneity", "text": bullets(est.get("heterogeneity") or [], "No subgroup analyses pre-specified" + (" (committed)." if est.get("heterogeneity") == [] else " (see checklist)."))},
        {"heading": "9. Data rules", "text": bullets(rules, "No exclusion, outlier or missing-data rules stated (see checklist).")},
        {"heading": "10. Timeline and stopping rules", "table": {"columns": ["Milestone", "Value"], "rows": [[k.replace("_", " ").capitalize(), _or(timeline.get(k))] for k in ("baseline", "intervention", "endline", "analysis", "stopping_rules")]}},
        {"heading": "11. Deviations log", "text": "Record every departure from this plan here, with the date, the reason and who approved it. Empty at registration.",
         "table": {"columns": ["Date", "Section", "Deviation", "Reason", "Approved by"], "rows": [["—", "—", "—", "—", "—"]]}},
        {"heading": "12. Registration checklist", "text": "Fields to complete when registering (AEA RCT Registry / OSF):\n\n" + "\n".join(f"- To do: {f}" for f in REGISTRATION_FIELDS)
         + ("\n\nItems this plan still lacks:\n" + "\n".join(f"- {c}" for c in checklist) if checklist else "\n\nEvery item this tool checks was supplied.")},
    ]
    out = ctx.project.new_artifact_dir("pre-analysis-plan", title)
    safe = re.sub(r"[/\\:\x00-\x1f]+", "-", args.get("filename") or f"{title} pre-analysis plan")
    fname = office_filename(safe, "docx")
    ctx.progress(30, "writing")
    template = resolve_template(ctx.project, "docx", None)
    docx_path = _write_docx(ctx, {"title": title, "subtitle": f"Pre-analysis plan · drafted {time.strftime('%Y-%m-%d')}", "sections": sections, "style": normalize_style(None),
                                  "filename": fname, "_template": template}, out, {})
    plan = {k: v for k, v in args.items() if k != "filename"}
    sidecar = {"kind": "squipi_pre_analysis_plan", "version": 1, "title": title, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "plan": plan,
               "power": power_result, "checklist": checklist, "notes": notes, "sections": [s["heading"] for s in sections if s["heading"]], "docx": str(docx_path)}
    json_path = ctx.project.write_json(out / (Path(fname).stem + ".json"), sidecar)
    ctx.project.audit({"tool": "pre_analysis_plan", "title": title, "artifact": str(out), "checklist_items": len(checklist)})
    ctx.progress(100, "done")
    headings = [s["heading"] for s in sections if s["heading"]]
    summary = (f"Wrote {docx_path.name}: {len(headings)} numbered sections, {len(primary)} primary and {len(secondary)} secondary outcomes"
               + (f", power target {power_result['per_arm']:,} per arm ({power_result['total']:,} total)" if power_result else ", no power calculation")
               + f"; {len(checklist)} item(s) still missing" + (" — see checklist." if checklist else "."))
    return {"title": title, "docx_path": str(docx_path), "json_path": str(json_path), "artifact_dir": str(out), "sections": headings, "plan": plan, "power": power_result,
            "checklist": checklist, "notes": notes, "summary_text": summary, "elapsed_s": round(time.time() - t0, 2)}
