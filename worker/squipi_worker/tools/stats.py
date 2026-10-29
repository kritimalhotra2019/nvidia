"""Descriptive and inferential statistics: aggregate, correlate, compare_groups, regress.

Everything is computed by pandas/numpy/scipy and returned with its sample sizes and uncertainty.
The wording in `interpretation` is a fixed template filled with the computed numbers: it says
"associated with", never "causes". Nothing here is a causal estimate without a design."""
from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .. import svg
from .datasets import read_dataset, ID_NAME, SAMPLE_ROWS, MAX_FULL_ROWS

AGGS = ["count", "sum", "mean", "median", "min", "max", "std", "nunique"]
MIN_GROUP_N = 5  # a group needs at least this many observations before a mean or test is reported
NOT_CAUSAL = "Descriptive: this is an association in this sample, not a causal effect."


def _load(ctx: JobContext, dataset_id: str) -> Tuple[pd.DataFrame, Any, bool]:
    ds = ctx.project.dataset(dataset_id)
    ctx.project.allow.check_readable(ds.path)
    sampled = (ds.rows or 0) > MAX_FULL_ROWS
    df = read_dataset(ds, nrows=SAMPLE_ROWS if sampled else None)
    return df, ds, sampled


def _col(df: pd.DataFrame, name: str, what: str = "column") -> str:
    if name in df.columns:
        return name
    low = {str(c).lower(): c for c in df.columns}
    if name.lower() in low:
        return low[name.lower()]
    raise InvalidArguments(f"{what} '{name}' is not in this dataset. Columns: {', '.join(map(str, df.columns[:40]))}")


def _numeric(df: pd.DataFrame, c: str) -> pd.Series:
    s = pd.to_numeric(df[c], errors="coerce")
    if s.notna().sum() == 0:
        raise InvalidArguments(f"'{c}' has no numeric values")
    return s.astype(float)


def _fmt(v: float) -> str:
    return svg._fmt(v)


def _save_table(ctx: JobContext, out: Path, name: str, table: pd.DataFrame) -> str:
    p = out / f"{name}.csv"
    table.to_csv(p, index=False)
    return str(p)


def _office_png(path,kind,title,labels,values,lows=None,highs=None):
    """Office charts must not depend on a macOS thumbnail service being available."""
    from .charts import _mpl
    plt=_mpl(None);fig,ax=plt.subplots(figsize=(8,4.5))
    if kind=='heatmap':
        im=ax.imshow(np.array([[np.nan if v is None else v for v in row] for row in values]),vmin=-1,vmax=1,cmap='RdBu_r')
        ax.set_xticks(range(len(labels)),labels,rotation=45,ha='right');ax.set_yticks(range(len(labels)),labels);fig.colorbar(im,ax=ax)
    elif kind=='bars':
        ax.bar(range(len(values)),values,color='#347EA6');ax.set_xticks(range(len(labels)),labels,rotation=25,ha='right')
    else:
        for i,value in enumerate(values):
            ax.plot(value,i,'o',color='#347EA6')
            if lows[i] is not None and highs[i] is not None:ax.plot([lows[i],highs[i]],[i,i],color='#347EA6')
        ax.set_yticks(range(len(labels)),labels);ax.set_xlabel('Estimate with 95% confidence interval')
    ax.set_title(title);fig.tight_layout();fig.savefig(Path(path).with_suffix('.png'),dpi=160);plt.close(fig)


# ---------- aggregate

# Spellings a small model uses for the eight metrics (27 Oct 2026: "value_counts" and "count_distinct"
# cost fourteen rounds). Frequency words with no grouping become a count per category of that column.
AGG_ALIASES = {
    "avg": "mean", "average": "mean", "mean_value": "mean", "arithmetic_mean": "mean",
    "med": "median", "middle": "median",
    "n": "count", "size": "count", "rows": "count", "row_count": "count", "count_rows": "count", "number": "count", "num": "count", "len": "count", "length": "count", "n_rows": "count",
    "total": "sum", "sum_total": "sum", "add": "sum",
    "sd": "std", "stdev": "std", "stddev": "std", "std_dev": "std", "standard_deviation": "std", "stdv": "std",
    "minimum": "min", "lowest": "min", "smallest": "min", "maximum": "max", "highest": "max", "largest": "max",
    "nunique": "nunique", "unique": "nunique", "distinct": "nunique", "n_unique": "nunique", "n_distinct": "nunique", "count_distinct": "nunique", "distinct_count": "nunique",
    "unique_count": "nunique", "countdistinct": "nunique", "cardinality": "nunique", "number_of_unique": "nunique", "unique_values": "nunique", "distinct_values": "nunique",
}
FREQUENCY_WORDS = frozenset({"value_counts", "valuecounts", "frequency", "frequencies", "freq", "distribution", "histogram", "counts", "count_by", "count_per", "tabulate", "tabulation", "breakdown", "share", "proportion", "percent", "percentage", "pct"})


def normalize_aggregate(args: Dict[str, Any]):
    notes: List[str] = []
    metrics = args.get("metrics")
    if not isinstance(metrics, list):
        return args, notes
    group_by = args.get("group_by") if isinstance(args.get("group_by"), list) else []
    frequency_columns: List[str] = []
    cleaned: List[Any] = []
    for m in metrics:
        if not isinstance(m, dict) or not isinstance(m.get("agg"), str):
            cleaned.append(m); continue
        raw = m["agg"]; key = re.sub(r"[\s\-]+", "_", raw.strip().lower())
        if key in AGGS:
            cleaned.append({**m, "agg": key}); continue
        if key in AGG_ALIASES:
            cleaned.append({**m, "agg": AGG_ALIASES[key]}); notes.append(f"'{raw}' was read as {AGG_ALIASES[key]}.")
        elif key in FREQUENCY_WORDS and isinstance(m.get("column"), str):
            frequency_columns.append(m["column"]); cleaned.append({**m, "agg": "__frequency__"})
        else:
            cleaned.append(m)
    if frequency_columns:
        distinct = list(dict.fromkeys(frequency_columns))
        if not group_by and len(distinct) == 1:
            # "value_counts of AP result" is a count of rows per category: group by that column.
            col = distinct[0]
            args["group_by"] = [col]
            cleaned = [{**m, "agg": "count"} if m.get("agg") == "__frequency__" else m for m in cleaned]
            notes.append(f"A frequency of {col} is a count per category: the table is grouped by {col}; the frequency metric is a row count.")
        else:
            cleaned = [{**m, "agg": "nunique"} if m.get("agg") == "__frequency__" else m for m in cleaned]
            notes.append("Frequency metrics on " + ", ".join(distinct) + " were read as distinct counts; a per-category frequency table needs one aggregate call grouped by that column, or summarize_research_data with categorical_columns.")
    # A column asked for twice with the same metric is one metric.
    seen = set(); unique: List[Any] = []
    for m in cleaned:
        sig = (m.get("column"), m.get("agg")) if isinstance(m, dict) else id(m)
        if sig in seen: continue
        seen.add(sig); unique.append(m)
    if len(unique) < len(cleaned): notes.append("Duplicate metrics were merged.")
    args["metrics"] = unique
    return args, notes


@tool("aggregate",
      "Compute overall statistics (omit group_by or use []) or group by up to three columns. Metrics: count, sum, mean, median, min, max, std, nunique. "
      "Returns the table (up to `top` groups), writes it as CSV and draws a bar chart of the first metric when there is one grouping column.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 3, "description": "Omit or [] for the whole dataset. Never group by an ID to obtain an overall mean."},
          "metrics": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object", "properties": {
              "column": {"type": "string"}, "agg": {"type": "string", "enum": AGGS}}, "required": ["column", "agg"]}},
          "sort_by": {"type": ["string", "null"], "description": "metric label to sort by, e.g. mean_income (default: first metric, descending)"},
          "top": {"type": "integer", "minimum": 1, "maximum": 200, "description": "max groups to return (default 20)"}},
       "required": ["dataset_id", "metrics"], "additionalProperties": False}, normalize=normalize_aggregate)
def aggregate(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    ctx.progress(20, "grouping")
    keys = [_col(df, k, "group_by column") for k in args.get("group_by",[])]
    labels: List[str] = []
    share_columns: List[str] = []
    series: Dict[str, pd.Series] = {}
    g = df.groupby(keys, dropna=False, observed=True) if keys else None
    for m in args["metrics"]:
        c = _col(df, m["column"])
        agg = m["agg"]
        label = f"{agg}_{c}"
        if not keys:
            values=df[c] if agg in ("count","nunique") else pd.to_numeric(df[c],errors="coerce")
            series[label]=pd.Series([getattr(values,agg)()])
        elif agg in ("count", "nunique"):
            series[label] = g[c].count() if agg == "count" else g[c].nunique()
            if agg == "count":
                # The share the model would otherwise work out in prose (and the answer check would then
                # remove): percent of all non-missing values of the column, a tool result like any other.
                total = int(df[c].count())
                share_label = f"share_pct_{c}"
                series[share_label] = (100.0 * series[label] / total).round(2) if total else series[label] * float("nan")
                share_columns.append(share_label)
        else:
            if not pd.api.types.is_numeric_dtype(df[c]):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            series[label] = getattr(g[c], agg)()
        labels.append(label)
    table = pd.DataFrame(series).reset_index(drop=not keys)
    for k in keys:
        table[k] = table[k].astype(object).where(table[k].notna(), "(missing)")
    n_groups = int(len(table))
    sort_by = args.get("sort_by") or labels[0]
    if sort_by not in table.columns:
        raise InvalidArguments(f"sort_by must be one of {labels}")
    table = table.sort_values(sort_by, ascending=False, kind="mergesort")
    top = int(args.get("top") or 20)
    shown = table.head(top)
    ctx.progress(70, "writing")
    out = ctx.project.new_artifact_dir("aggregate", f"{'-'.join(map(str, keys))}")
    csv_path = _save_table(ctx, out, "table", table)
    chart = None
    if len(keys) == 1 and n_groups >= 2:
        lab = [str(x)[:28] for x in shown[keys[0]]]
        vals = [float(v) if pd.notna(v) else 0.0 for v in shown[labels[0]]]
        s = svg.bars(f"{labels[0].replace('_', ' of ', 1)} by {keys[0]}", lab, vals,
                     subtitle=f"{n_groups:,} groups" + (f", top {top} shown" if n_groups > top else "") + (" · sampled" if sampled else ""))
        chart = str(ctx.project.write_text(out / "chart.svg", s))
        _office_png(chart,'bars',f'{labels[0]} by {keys[0]}',lab,vals)
    rows = [[(None if (isinstance(v, float) and math.isnan(v)) else (v.item() if hasattr(v, "item") else v)) for v in r] for r in shown.itertuples(index=False)]
    first = shown.iloc[0] if len(shown) else None
    summary = (f"{len(table):,} groups by {' × '.join(map(str, keys))}" if keys else "Whole-dataset statistics") + f"; {', '.join(labels)}."
    if share_columns:
        summary += f" {', '.join(share_columns)}: each group's percent of all non-missing values of that column (all groups together, not only those shown)."
    group_sizes = {}
    if keys:
        try:
            sizes = df.groupby(keys, dropna=False, observed=True).size()
            group_sizes = {(k if isinstance(k, tuple) else (k,)): int(v) for k, v in sizes.items()}
        except Exception:  # noqa: BLE001 - sizes are advisory
            group_sizes = {}
    def _n(row):
        key = tuple(("(missing)" if (isinstance(v, float) and math.isnan(v)) else v) for v in (row[k] for k in keys))
        for k, v in group_sizes.items():
            if tuple(("(missing)" if (isinstance(x, float) and math.isnan(x)) else x) for x in k) == key: return v
        return None
    if first is not None and len(keys) == 1:
        n_first = _n(first)
        summary += f" Highest {sort_by}: {first[keys[0]]} ({_fmt(float(first[sort_by])) if pd.notna(first[sort_by]) else 'n/a'}" + (f", n = {n_first}" if n_first is not None else "") + ")."
        last = table.iloc[-1]
        n_last = _n(last)
        summary += f" Lowest: {last[keys[0]]} ({_fmt(float(last[sort_by])) if pd.notna(last[sort_by]) else 'n/a'}" + (f", n = {n_last}" if n_last is not None else "") + ")."
    tiny = [k for k, v in group_sizes.items() if v < MIN_GROUP_N]
    if tiny:
        summary += f" Caution: {len(tiny)} group(s) have fewer than {MIN_GROUP_N} rows ({', '.join(' × '.join(map(str, k)) for k in tiny[:5])}); their statistics are not reliable for comparison."
    if sampled:
        summary += f" Computed on the first {len(df):,} rows."
    ctx.project.audit({"tool": "aggregate", "dataset": ds.id, "groups": n_groups, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "group_by": keys, "metrics": labels, "n_groups": n_groups, "shown": int(len(shown)),
            "table": {"columns": [str(c) for c in shown.columns], "rows": rows}, "csv_path": csv_path, "chart_path": chart,
            "summary_text": summary, "small_groups": [{"group": " × ".join(map(str, k)), "n": v} for k, v in group_sizes.items() if v < MIN_GROUP_N],
            "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- correlate

@tool("correlate",
      "Correlation matrix (Pearson or Spearman) between numeric columns, with the strongest pairs listed and a heatmap. "
      "Identifier-like columns are skipped. Correlation is association, not causation.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "columns": {"type": ["array", "null"], "items": {"type": "string"}, "description": "numeric columns to include (default: all numeric, up to max_columns)"},
          "method": {"type": "string", "enum": ["pearson", "spearman"]},
          "max_columns": {"type": "integer", "minimum": 2, "maximum": 30}},
       "required": ["dataset_id"], "additionalProperties": False})
def correlate(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    method = args.get("method") or "pearson"
    maxc = int(args.get("max_columns") or 15)
    if args.get("columns"):
        cols = [_col(df, c) for c in args["columns"]]
        num = df[cols].apply(pd.to_numeric, errors="coerce")
    else:
        num = df.select_dtypes(include="number").copy()
        num = num[[c for c in num.columns if not ID_NAME.search(str(c))]]
    num = num.loc[:, num.nunique(dropna=True) > 1]
    if num.shape[1] < 2:
        raise InvalidArguments("need at least two numeric, non-constant columns to correlate")
    skipped = []
    if num.shape[1] > maxc:
        keep = num.var().sort_values(ascending=False).index[:maxc]
        skipped = [str(c) for c in num.columns if c not in keep]
        num = num[keep]
    ctx.progress(40, "computing")
    corr = num.corr(method=method, min_periods=10)
    n_pairs = num.notna().astype(int).T.dot(num.notna().astype(int))
    labels = [str(c) for c in corr.columns]
    pairs = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            r = corr.iloc[i, j]
            if pd.notna(r):
                pairs.append({"a": labels[i], "b": labels[j], "r": round(float(r), 3), "n": int(n_pairs.iloc[i, j])})
    pairs.sort(key=lambda p: -abs(p["r"]))
    ctx.progress(75, "drawing")
    out = ctx.project.new_artifact_dir("correlate", ds.name)
    matrix = [[(None if pd.isna(v) else round(float(v), 3)) for v in row] for row in corr.values]
    chart = str(ctx.project.write_text(out / "heatmap.svg", svg.heatmap(f"{method.title()} correlation", labels, matrix,
                                                                             subtitle=f"{len(labels)} columns · {len(df):,} rows" + (" (sampled)" if sampled else ""))))
    csv_path = str(ctx.project.write_text(out / "matrix.csv", corr.round(4).to_csv()))
    _office_png(chart,'heatmap',f'{method.title()} correlation',labels,matrix)

    def strength(r: float) -> str:
        a = abs(r)
        return "very weak" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong" if a < 0.7 else "very strong"

    top = pairs[:5]
    summary = f"{method.title()} correlations between {len(labels)} numeric columns."
    if top:
        summary += " Strongest: " + "; ".join(f"{p['a']} and {p['b']} r = {p['r']:+.2f} ({strength(p['r'])}, {'positive' if p['r'] > 0 else 'negative'}, n = {p['n']:,})" for p in top[:3]) + "."
    summary += " " + NOT_CAUSAL
    if skipped:
        summary += f" {len(skipped)} columns with the lowest variance were left out to keep the matrix readable."
    ctx.project.audit({"tool": "correlate", "dataset": ds.id, "columns": len(labels), "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "method": method, "columns": labels, "matrix": matrix, "pairs": pairs[:40], "skipped": skipped,
            "chart_path": chart, "csv_path": csv_path, "summary_text": summary, "interpretation": [
                f"r ranges from -1 to +1. Values near 0 mean no linear relationship; |r| ≥ 0.5 is a moderate-to-strong one.",
                NOT_CAUSAL], "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- compare_groups

def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = len(a), len(b)
    sp = math.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / max(1, na + nb - 2))
    return float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0


@tool("compare_groups",
      "Compare an outcome between groups. Numeric outcome: per-group n, mean, sd, median with 95% confidence intervals; two groups → difference in means with CI, "
      "Welch t-test, Mann–Whitney U and Cohen's d; more groups → one-way ANOVA and Kruskal–Wallis. Categorical outcome: cross-tab with chi-square and Cramér's V. "
      "Draws the group means with confidence intervals. Differences are descriptive unless the groups were randomly assigned.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": ["string", "array"], "items": {"type": "string"}, "maxItems": 8, "description": "the column being compared (or a list of columns, compared one by one)"},
          "group": {"type": "string", "description": "the column that defines the groups"},
          "levels": {"type": ["array", "null"], "items": {"type": "string"}, "minItems": 2, "maxItems": 12, "description": "which group values to compare (default: all, up to 12 most common)"}},
       "required": ["dataset_id", "outcome", "group"], "additionalProperties": False})
def compare_groups(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    # Several outcomes in one call (28 Oct: a list where a name was expected): one comparison per outcome,
    # returned together, so a model that asks for four outcomes gets four answers and not a refusal.
    if isinstance(args.get("outcome"), list):
        names = [o for o in args["outcome"] if isinstance(o, str) and o.strip()]
        if not names:
            raise InvalidArguments("outcome must name at least one column")
        if len(names) == 1:
            args = {**args, "outcome": names[0]}
        else:
            parts = [compare_groups(ctx, {**args, "outcome": o}) for o in names[:8]]
            return {"dataset_id": parts[0]["dataset_id"], "comparisons": parts, "outcomes": names[:8], "group": parts[0].get("group"),
                    "interpretation": [p.get("summary_text", "") for p in parts],
                    "summary_text": " ".join(p.get("summary_text", "") for p in parts),
                    "argument_notes": [f"{len(names[:8])} outcomes were compared one by one."] + ([f"Only the first 8 of {len(names)} outcomes were compared."] if len(names) > 8 else [])}
    from scipy import stats as st
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    gcol = _col(df, args["group"], "group")
    grp = df[gcol].astype(object).where(df[gcol].notna(), None)
    if args.get("levels"):
        want = [str(l) for l in args["levels"]]
        grp = grp.where(grp.astype(str).isin(want), None)
    counts = grp.dropna().astype(str).value_counts()
    if len(counts) < 2:
        raise InvalidArguments(f"'{gcol}' has fewer than two groups to compare" + (" among the levels given" if args.get("levels") else ""))
    levels = list(counts.index[:12])
    mask = grp.astype(str).isin(levels) & grp.notna()
    out = ctx.project.new_artifact_dir("compare", f"{y}-by-{gcol}")
    res: Dict[str, Any] = {"dataset_id": ds.id, "outcome": y, "group": gcol, "levels": levels, "sampled": sampled, "artifact_dir": str(out)}
    ctx.progress(30, "computing")
    numeric_y = pd.to_numeric(df[y], errors="coerce")
    is_numeric = pd.api.types.is_numeric_dtype(df[y]) or numeric_y.notna().mean() > 0.9
    if is_numeric and numeric_y[mask].nunique() > 2:
        groups: Dict[str, np.ndarray] = {}
        rows = []
        too_small: List[str] = []
        for lv in levels:
            v = numeric_y[mask & (grp.astype(str) == lv)].dropna().to_numpy()
            if len(v) < MIN_GROUP_N:
                too_small.append(f"{lv} (n = {len(v)})")
                continue
            groups[lv] = v
            m, sd = float(v.mean()), float(v.std(ddof=1))
            se = sd / math.sqrt(len(v))
            tcrit = float(st.t.ppf(0.975, len(v) - 1))
            rows.append({"group": lv, "n": int(len(v)), "mean": m, "sd": sd, "median": float(np.median(v)), "ci_low": m - tcrit * se, "ci_high": m + tcrit * se})
        if len(groups) < 2:
            raise InvalidArguments(f"fewer than two groups have at least {MIN_GROUP_N} numeric values" + (f"; too small: {', '.join(too_small)}" if too_small else ""))
        res["kind"] = "numeric"
        res["groups"] = rows
        res["excluded_small_groups"] = too_small
        interp: List[str] = []
        if too_small:
            interp.append(f"Groups with fewer than {MIN_GROUP_N} observations were excluded from the comparison: {', '.join(too_small)}.")
        if len(groups) == 2:
            (la, a), (lb, b) = list(groups.items())[:2]
            diff = float(a.mean() - b.mean())
            se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            dfw = (a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)) ** 2 / ((a.var(ddof=1) / len(a)) ** 2 / (len(a) - 1) + (b.var(ddof=1) / len(b)) ** 2 / (len(b) - 1))
            tcrit = float(st.t.ppf(0.975, dfw))
            tt = st.ttest_ind(a, b, equal_var=False)
            mw = st.mannwhitneyu(a, b, alternative="two-sided")
            d = _cohens_d(a, b)
            res["difference"] = {"a": la, "b": lb, "mean_diff": diff, "ci_low": diff - tcrit * se, "ci_high": diff + tcrit * se,
                                 "welch_t": float(tt.statistic), "welch_p": float(tt.pvalue), "df": float(dfw),
                                 "mann_whitney_u": float(mw.statistic), "mann_whitney_p": float(mw.pvalue), "cohens_d": d}
            size = "negligible" if abs(d) < 0.2 else "small" if abs(d) < 0.5 else "medium" if abs(d) < 0.8 else "large"
            sig = "unlikely to be chance alone" if tt.pvalue < 0.05 else "could easily be chance"
            interp.append(f"{y} averages {_fmt(float(a.mean()))} in {la} (n = {len(a):,}) and {_fmt(float(b.mean()))} in {lb} (n = {len(b):,}): a difference of {_fmt(diff)} "
                          f"(95% CI {_fmt(diff - tcrit * se)} to {_fmt(diff + tcrit * se)}), {size} in standardised terms (d = {d:.2f}). Welch p = {tt.pvalue:.3g}: {sig}.")
            if (mw.pvalue < 0.05) != (tt.pvalue < 0.05):
                interp.append("The rank-based test disagrees with the t-test, so skew or outliers matter here; look at the medians too.")
        else:
            an = st.f_oneway(*groups.values())
            kw = st.kruskal(*groups.values())
            res["overall"] = {"anova_f": float(an.statistic), "anova_p": float(an.pvalue), "kruskal_h": float(kw.statistic), "kruskal_p": float(kw.pvalue)}
            hi = max(rows, key=lambda r: r["mean"]); lo = min(rows, key=lambda r: r["mean"])
            interp.append(f"Across {len(rows)} groups, mean {y} ranges from {_fmt(lo['mean'])} ({lo['group']}) to {_fmt(hi['mean'])} ({hi['group']}). "
                          f"One-way ANOVA p = {an.pvalue:.3g}; Kruskal–Wallis p = {kw.pvalue:.3g}.")
        interp.append(NOT_CAUSAL)
        res["interpretation"] = interp
        chart = svg.dots(f"Mean {y} by {gcol}", [r["group"][:28] for r in rows], [r["mean"] for r in rows], [r["ci_low"] for r in rows], [r["ci_high"] for r in rows],
                         subtitle="dot = mean, bar = 95% confidence interval" + (" · sampled" if sampled else ""), zero_line=False)
        res["chart_path"] = str(ctx.project.write_text(out / "means.svg", chart))
        _office_png(res['chart_path'],'dots',f'Mean {y} by {gcol}',[r['group'] for r in rows],[r['mean'] for r in rows],[r['ci_low'] for r in rows],[r['ci_high'] for r in rows])
        res["csv_path"] = _save_table(ctx, out, "groups", pd.DataFrame(rows))
        res["summary_text"] = " ".join(interp[:1]) if interp else ""
    else:
        ct = pd.crosstab(grp[mask].astype(str), df.loc[mask, y].astype(str))
        if ct.shape[1] > 20:
            keep = ct.sum().sort_values(ascending=False).index[:20]
            ct = ct[keep]
        chi2, p, dof, _ = st.chi2_contingency(ct)
        n = int(ct.values.sum())
        k = min(ct.shape) - 1
        v = math.sqrt(chi2 / (n * k)) if n and k else 0.0
        pct = (ct.T / ct.sum(axis=1)).T * 100
        res["kind"] = "categorical"
        res["table"] = {"columns": ["group"] + [str(c) for c in ct.columns], "rows": [[str(i)] + [int(x) for x in r] for i, r in zip(ct.index, ct.values)]}
        res["row_percent"] = {"columns": ["group"] + [str(c) for c in pct.columns], "rows": [[str(i)] + [round(float(x), 1) for x in r] for i, r in zip(pct.index, pct.values)]}
        res["test"] = {"chi2": float(chi2), "p": float(p), "dof": int(dof), "cramers_v": float(v), "n": n}
        strength = "negligible" if v < 0.1 else "weak" if v < 0.3 else "moderate" if v < 0.5 else "strong"
        res["interpretation"] = [f"The distribution of {y} differs across {gcol} groups with chi-square p = {p:.3g}; Cramér's V = {v:.2f} ({strength} association, n = {n:,}).", NOT_CAUSAL]
        res["summary_text"] = res["interpretation"][0]
        res["csv_path"] = str(ctx.project.write_text(out / "crosstab.csv", ct.to_csv()))
        res["chart_path"] = None
    ctx.project.audit({"tool": "compare_groups", "dataset": ds.id, "outcome": y, "group": gcol, "artifact": str(out)})
    res["elapsed_s"] = round(time.time() - t0, 2)
    ctx.progress(100, "done")
    return res


# ---------- regress

def _design(df: pd.DataFrame, outcome: str, predictors: List[str], standardize: bool) -> Tuple[pd.DataFrame, pd.Series, List[str], Dict[str, str]]:
    y = pd.to_numeric(df[outcome], errors="coerce")
    X = pd.DataFrame(index=df.index)
    kinds: Dict[str, str] = {}
    for p in predictors:
        s = df[p]
        num = pd.to_numeric(s, errors="coerce")
        if pd.api.types.is_numeric_dtype(s) or num.notna().mean() > 0.95 and s.dropna().nunique() > 2:
            X[p] = num.astype(float)
            kinds[p] = "numeric"
        else:
            cats = s.astype(object).where(s.notna(), None).astype(str).where(s.notna(), None)
            levels = cats.dropna().value_counts()
            if len(levels) > 30:
                raise InvalidArguments(f"'{p}' has {len(levels)} categories; too many to dummy-code. Pick fewer, or recode it first.")
            if len(levels) < 2:
                raise InvalidArguments(f"'{p}' has a single value and cannot be a predictor")
            base = str(levels.index[0])
            for lv in levels.index[1:]:
                X[f"{p}[{lv}]"] = (cats == lv).astype(float).where(cats.notna(), np.nan)
            kinds[p] = f"categorical (reference: {base})"
    data = pd.concat([y.rename("__y"), X], axis=1).dropna()
    if len(data) < len(X.columns) + 3:
        raise InvalidArguments(f"only {len(data)} complete rows for {len(X.columns)} coefficients; not enough to fit")
    yv = data["__y"]
    Xv = data.drop(columns="__y")
    Xv = Xv.loc[:, Xv.std(ddof=0) > 0]
    if standardize:
        for c in Xv.columns:
            if "[" not in c:
                Xv[c] = (Xv[c] - Xv[c].mean()) / Xv[c].std(ddof=1)
    names = ["(intercept)"] + [str(c) for c in Xv.columns]
    Xv.insert(0, "(intercept)", 1.0)
    return Xv, yv, names, kinds


@tool("regress",
      "Ordinary least squares regression of a numeric outcome on one or more predictors. Categorical predictors are dummy-coded automatically (most common level as reference). "
      "Returns coefficients with heteroskedasticity-robust (HC1) standard errors by default, or cluster-robust when `cluster` is given, with 95% confidence intervals, p-values, "
      "R², n, and a coefficient plot. Coefficients describe association; they are causal only under a research design that justifies it.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string"},
          "predictors": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
          "cluster": {"type": ["string", "null"], "description": "column to cluster standard errors by (e.g. a district or household id)"},
          "robust": {"type": "boolean", "description": "HC1 robust standard errors (default true)"},
          "standardize": {"type": "boolean", "description": "standardise numeric predictors so coefficients are per 1 SD (default false)"}},
       "required": ["dataset_id", "outcome", "predictors"], "additionalProperties": False})
def regress(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from scipy import stats as st
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    yname = _col(df, args["outcome"], "outcome")
    preds = [_col(df, p, "predictor") for p in args["predictors"]]
    if yname in preds:
        raise InvalidArguments("the outcome cannot also be a predictor")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    robust = args.get("robust", True)
    ctx.progress(20, "building the design matrix")
    X, y, names, kinds = _design(df, yname, preds, bool(args.get("standardize")))
    n, k = X.shape
    Xm, ym = X.to_numpy(float), y.to_numpy(float)
    ctx.progress(50, "fitting")
    with np.errstate(all="ignore"):
        beta, *_ = np.linalg.lstsq(Xm, ym, rcond=None)
        resid = ym - Xm @ beta
        XtX_inv = np.linalg.pinv(Xm.T @ Xm)
    dof = n - k
    se_kind = "classical"
    if cluster:
        groups = df.loc[X.index, cluster].astype(str).to_numpy()
        uniq = np.unique(groups)
        meat = np.zeros((k, k))
        for gname in uniq:
            idx = groups == gname
            u = Xm[idx].T @ resid[idx]
            meat += np.outer(u, u)
        G = len(uniq)
        adj = (G / (G - 1)) * ((n - 1) / dof) if G > 1 else 1.0
        with np.errstate(all="ignore"):
            cov = adj * XtX_inv @ meat @ XtX_inv
        se_kind = f"cluster-robust by {cluster} ({G} clusters)"
        dof_t = G - 1
    elif robust:
        with np.errstate(all="ignore"):          # Accelerate's matmul raises spurious fp warnings on some shapes
            meat = (Xm * (resid ** 2)[:, None]).T @ Xm
            cov = (n / dof) * XtX_inv @ meat @ XtX_inv
        se_kind = "HC1 robust"
        dof_t = dof
    else:
        s2 = float(resid @ resid) / dof
        cov = s2 * XtX_inv
        dof_t = dof
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    tval = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    pval = 2 * st.t.sf(np.abs(tval), dof_t)
    tcrit = float(st.t.ppf(0.975, dof_t))
    ss_res = float(resid @ resid)
    ss_tot = float(((ym - ym.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    adj_r2 = 1 - (1 - r2) * (n - 1) / dof if dof > 0 else r2
    coefs = []
    for i, nm in enumerate(names):
        coefs.append({"term": nm, "coef": float(beta[i]), "se": float(se[i]), "t": (None if np.isnan(tval[i]) else float(tval[i])),
                      "p": (None if np.isnan(pval[i]) else float(pval[i])), "ci_low": float(beta[i] - tcrit * se[i]), "ci_high": float(beta[i] + tcrit * se[i])})
    ctx.progress(80, "writing")
    out = ctx.project.new_artifact_dir("regress", f"{yname}-on-{'-'.join(preds)}")
    csv_path = _save_table(ctx, out, "coefficients", pd.DataFrame(coefs))
    body = [c for c in coefs if c["term"] != "(intercept)"]
    chart = svg.dots(f"{yname}: regression coefficients", [c["term"][:30] for c in body], [c["coef"] for c in body], [c["ci_low"] for c in body], [c["ci_high"] for c in body],
                     subtitle=f"OLS, {se_kind} SEs, 95% CIs · n = {n:,} · R² = {r2:.2f}")
    chart_path = str(ctx.project.write_text(out / "coefficients.svg", chart))
    _office_png(chart_path,'dots',f'{yname}: regression coefficients',[c['term'] for c in body],[c['coef'] for c in body],[c['ci_low'] for c in body],[c['ci_high'] for c in body])
    interp: List[str] = []
    for c in body[:6]:
        term = c["term"]
        if "[" in term:
            var, lv = term.split("[", 1); lv = lv.rstrip("]")
            ref = kinds.get(var, "").replace("categorical (reference: ", "").rstrip(")")
            interp.append(f"{var} = {lv} is associated with {_fmt(c['coef'])} {'higher' if c['coef'] >= 0 else 'lower'} {yname} than {ref} (95% CI {_fmt(c['ci_low'])} to {_fmt(c['ci_high'])}, p = {c['p']:.3g}), holding the other predictors fixed.")
        else:
            unit = "one standard deviation" if args.get("standardize") else "one unit"
            interp.append(f"Each {unit} higher {term} is associated with {_fmt(c['coef'])} {'higher' if c['coef'] >= 0 else 'lower'} {yname} (95% CI {_fmt(c['ci_low'])} to {_fmt(c['ci_high'])}, p = {c['p']:.3g}), holding the other predictors fixed.")
    interp.append(f"The model explains {100 * r2:.0f}% of the variation in {yname} (adjusted R² {adj_r2:.2f}) across {n:,} complete rows" + (f"; {len(df) - n:,} rows with missing values were dropped" if len(df) > n else "") + ".")
    interp.append("Coefficients are associations in this sample. Reading them as causal effects needs a design (randomisation, difference-in-differences, instruments, discontinuities) that this model does not have.")
    summary = interp[0] + " " + interp[-2] if interp else ""
    ctx.project.audit({"tool": "regress", "dataset": ds.id, "outcome": yname, "predictors": preds, "n": n, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": yname, "predictors": preds, "predictor_kinds": kinds, "n": n, "dropped_rows": int(len(df) - n), "k": k,
            "r2": r2, "adj_r2": adj_r2, "se_kind": se_kind, "coefficients": coefs, "interpretation": interp, "summary_text": summary,
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


_RATE_FILTER = {"type": "object", "properties": {
    "column": {"type": "string"},
    "values": {"type": "array", "minItems": 1, "maxItems": 100,
               "items": {"type": ["string", "number", "boolean"]}}},
    "required": ["column", "values"], "additionalProperties": False}


def normalize_rate(args: Dict[str, Any]):
    """A missing label is not a reason to refuse a computation (28 Oct: three refusals in one round). The label is
    derived from the numerator filters and reported; the figure itself is unchanged."""
    notes: List[str] = []
    label = args.get("label")
    filters = args.get("numerator_filters")
    if (not isinstance(label, str) or not label.strip()) and isinstance(filters, list) and filters:
        parts = []
        for f in filters:
            if isinstance(f, dict) and isinstance(f.get("column"), str):
                vals = f.get("values") if isinstance(f.get("values"), list) else []
                parts.append(f"{f['column']} in {', '.join(str(v) for v in vals[:4])}" if vals else str(f["column"]))
        if parts:
            args["label"] = ("share with " + " and ".join(parts))[:200]
            notes.append(f"No label was given; the rate is labelled '{args['label']}'.")
    return args, notes


@tool("calculate_dataset_rate",
      "Compute a count and percentage directly from a dataset with an explicit denominator. "
      "Filters are exact value membership, AND across columns; values within one filter are OR. "
      "Numerator is always restricted to eligible denominator rows. Omitted denominator filters mean all rows, including missing values. "
      "Use denominator_nonmissing to exclude missing values explicitly. Never infer eligibility from a column name.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "label": {"type": "string", "minLength": 1, "maxLength": 200},
          "numerator_filters": {"type": "array", "minItems": 1, "maxItems": 4, "items": _RATE_FILTER},
          "denominator_filters": {"type": "array", "maxItems": 4, "items": _RATE_FILTER},
          "denominator_nonmissing": {"type": "array", "maxItems": 10, "items": {"type": "string"}}},
       "required": ["dataset_id", "label", "numerator_filters"], "additionalProperties": False}, normalize=normalize_rate)
def calculate_dataset_rate(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    import hashlib
    df, ds, sampled = _load(ctx, args["dataset_id"])
    if sampled:
        raise InvalidArguments("This dataset exceeds the full-data limit; a population rate cannot be certified from an automatic sample. Use an explicitly prepared subset or another bounded analysis.")
    def mask(filters):
        result = pd.Series(True, index=df.index)
        for f in filters:
            c = _col(df, f["column"])
            unknown = [value for value in f["values"] if not df[c].isin([value]).any()]
            if unknown:
                raise InvalidArguments(f"A requested category is not observed in '{c}'. Inspect its observed values and use exact coding before calculating a rate.")
            result &= df[c].isin(f["values"])
        return result
    eligible = mask(args.get("denominator_filters", []))
    for name in args.get("denominator_nonmissing", []):
        eligible &= df[_col(df, name)].notna()
    denominator = int(eligible.sum())
    if denominator == 0:
        raise InvalidArguments("The requested denominator has zero eligible rows; no rate is defined.")
    numerator = int((eligible & mask(args["numerator_filters"])).sum())
    percentage = 100.0 * numerator / denominator
    digest = hashlib.sha256()
    with Path(ds.path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    result = {"dataset_id": ds.id, "label": args["label"], "source_rows": len(df),
              "numerator": numerator, "denominator": denominator, "percentage": percentage,
              "excluded_rows": len(df) - denominator, "sampled": False,
              "population": {"denominator_filters": args.get("denominator_filters", []),
                             "denominator_nonmissing": args.get("denominator_nonmissing", [])},
              "numerator_filters": args["numerator_filters"],
              "source_sha256": digest.hexdigest(),
              "limits": ["Descriptive row rate, not a population estimate or causal effect. Row identity, coding and eligibility definitions require substantive validation."],
              "summary_text": f"{args['label']}: {numerator:,}/{denominator:,} eligible rows = {percentage:.2f}%; {len(df)-denominator:,} rows excluded by the explicit denominator definition."}
    out = ctx.project.new_artifact_dir("rate", args["label"])
    result["results_path"] = str(out / "rate.json")
    ctx.project.write_json(out / "rate.json", result)
    ctx.project.audit({"tool": "calculate_dataset_rate", "dataset": ds.id, "numerator": numerator, "denominator": denominator})
    return result
