"""find_insights: the strongest patterns in a dataset, ranked by effect size, each with its numbers and a chart.

Deterministic throughout: group differences (eta²), correlations (|r|), trends over time (relative change),
concentration (dominant categories), skew/outliers and data-quality issues. The model turns these into prose;
it never invents a finding that is not in this list."""
from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .datasets import read_dataset, analyse_column, SAMPLE_ROWS, MAX_FULL_ROWS, detect_pii
from . import charts
from .document_style import STYLE_SCHEMA, normalize_style
from .meaning import interpret, readings, finding_vars, glossary
from .structure import column_families, duplicate_columns, duplicate_candidates, group_candidates, is_metadata, describe_families, outcome_candidates

MIN_GROUP_N = 5  # fewer observations cannot support an effect-size claim
NOT_CAUSAL = "Association in this sample, not a causal effect."


LONG_MEASURE = ("measure", "indicator", "metric", "variable", "series", "item", "statistic", "kpi", "dataset", "table", "source", "topic", "domain", "category_group", "unit", "units", "frequency")
LONG_VALUE = ("value", "amount", "figure", "val")


def _long_format(df: pd.DataFrame, cols: Dict[str, Dict[str, Any]], numeric: List[str], categorical: List[str]):
    """(measure_column, value_column, other_label_columns) when the table is in long format, else None.
    With a hierarchy of label columns (Dataset > Metric_Group > Measure, plus Unit), the most specific one (most
    distinct values) names the measure; the coarser ones and units describe it and must not be used as groups."""
    vals = [c for c in numeric if str(c).lower() in LONG_VALUE]
    labelish = [c for c in categorical if any(k == str(c).lower() or str(c).lower().endswith("_" + k) or str(c).lower().startswith(k) for k in LONG_MEASURE)]
    if not vals or not labelish:
        return None
    def rank(c: str):
        name = str(c).lower()
        prio = next((i for i, k in enumerate(LONG_MEASURE) if k == name or name.endswith("_" + k) or name.startswith(k)), len(LONG_MEASURE))
        return (-cols[c]["unique"], prio)          # most specific first; on ties "measure" beats "dataset"
    measure = sorted(labelish, key=rank)[0]
    return measure, vals[0], [c for c in labelish if c != measure]


def _eta_squared(y: pd.Series, g: pd.Series) -> float:
    frame = pd.DataFrame({"y": y, "g": g}).dropna()
    counts = frame["g"].value_counts()
    if frame["g"].nunique() < 2 or len(frame) < 20 or counts.min() < 5:
        return 0.0
    grand = frame["y"].mean()
    ss_between = sum(len(sub) * (sub["y"].mean() - grand) ** 2 for _, sub in frame.groupby("g"))
    ss_total = float(((frame["y"] - grand) ** 2).sum())
    return float(ss_between / ss_total) if ss_total > 0 else 0.0


@tool("find_insights",
      "Scan a dataset for its strongest patterns and return them ranked by effect size with numbers and charts: which groups differ most on which outcomes, "
      "the strongest correlations, trends over time, dominant categories, skewed or outlier-heavy variables, and data-quality issues. "
      "Use it for 'what is interesting here', 'key findings', 'insights', or before writing a report. Findings are descriptive, never causal.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "style": STYLE_SCHEMA,
          "outcome": {"type": ["string", "null"], "description": "a numeric column to focus on (default: all numeric columns)"},
          "max_findings": {"type": "integer", "minimum": 3, "maximum": 20, "description": "default 8"},
          "charts": {"type": "boolean", "description": "draw a chart for the top findings (default true)"},
          "exclude": {"type": "array", "items": {"type": "string"}, "maxItems": 20, "description": "column names or words whose findings the user has set aside (e.g. 'age_in_yrs'); they are not reported"}},
       "required": ["dataset_id"], "additionalProperties": False})
def find_insights(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    style = normalize_style(args.get("style"))
    t0 = time.time()
    ds = ctx.project.dataset(args["dataset_id"])
    ctx.project.allow.check_readable(ds.path)
    sampled = (ds.rows or 0) > MAX_FULL_ROWS
    ctx.progress(5, "reading")
    df = read_dataset(ds, nrows=SAMPLE_ROWS if sampled else None)
    n = len(df)
    overrides = ds.overrides or {}
    ctx.progress(15, "classifying columns")
    cols = {str(c): analyse_column(str(c), df[c], n, overrides.get(str(c), {}).get("role")) for c in df.columns}
    # Structure first (28 Oct 2026): repeated member blocks are per-member fields of one record, duplicate
    # columns are one variable, and a status/arm column is the grouping the user means by "treatment and control".
    families = column_families([str(c) for c in df.columns])
    members = {c for ms in families.values() for c in ms}
    numeric = [c for c, info in cols.items() if info["role"] == "numeric" and not info.get("pii") and "few_distinct_values" not in info["flags"]]
    # Duplicates are found among every numeric candidate, member fields included: with outcome="Age_1" the scan
    # otherwise reported Age_1 vs Age_in_yrs_1 again (28 Oct, Ollama gate run).
    dups = duplicate_columns(df, duplicate_candidates(df, cols))
    duplicates = {d["column"] for d in dups}
    if isinstance(args.get("outcome"), str):
        original = next((d["duplicate_of"] for d in dups if d["column"] == args["outcome"]), None)
        if original:
            args["outcome"] = original
    numeric = [c for c in numeric if c not in duplicates and not is_metadata(c)]
    cols = {c: info for c, info in cols.items() if c not in duplicates}
    group_cols = [g["column"] for g in group_candidates(df, cols)]
    coded = [c for c, info in cols.items() if info["role"] == "numeric" and "few_distinct_values" in info["flags"]]
    categorical = [c for c, info in cols.items() if info["role"] in ("categorical", "boolean") and not info.get("pii") and 2 <= info["unique"] <= 20] + coded
    categorical = group_cols + [c for c in categorical if c not in group_cols and c not in members]
    # A date of birth is an attribute of a person, not a time axis: "food insecurity fell 75% from 1937 to 2008"
    # (27 Oct 2026, head_dob) is a cohort pattern misread as a trend. Personal-data dates are never axes either.
    dates = [c for c, info in cols.items() if info["role"] == "date" and not info.get("pii")
             and not re.search(r"(^|_)(dob|birth\w*|born|age_at|anniversary)($|_)|date_?of_?birth|birth_?date", str(c), re.I)]
    # a Year / Round / Wave column of small integers is a time axis too
    year_like = [c for c, info in cols.items() if c not in dates and info["role"] == "numeric" and re.search(r"(^|_)(year|yr|period|wave|round|quarter|month)($|_)", str(c), re.I)
                 and pd.to_numeric(df[c], errors="coerce").dropna().nunique() >= 3]
    time_cols = dates + year_like
    categorical = [c for c in categorical if c not in year_like]
    numeric = [c for c in numeric if c not in year_like]
    if args.get("outcome"):
        oc = charts._col(df, args["outcome"]).name
        if oc not in numeric and oc in coded:
            numeric = numeric + [oc]           # a numeric column with few distinct values (days 0–30) is still an outcome when named
            categorical = [c for c in categorical if c != oc]
        if oc not in numeric:
            raise InvalidArguments(f"'{oc}' is not a numeric outcome column (numeric columns: {', '.join(numeric[:15])})")
        outcomes = [oc]
    else:
        # Record-level columns first; a member block is scanned only when nothing else is numeric.
        record_level = [c for c in numeric if c not in members]
        preferred = [c for c in outcome_candidates(df, cols) if c in record_level]
        outcomes = (preferred + [c for c in record_level if c not in preferred] or numeric)[:8]
    if not args.get("outcome"):
        numeric = [c for c in numeric if c not in members] or numeric
    findings: List[Dict[str, Any]] = []
    if families:
        findings.append({"kind": "structure", "score": 0.04, "title": f"{len(families)} repeated column families describe household members",
                         "detail": describe_families(families) + " Member-level statistics need the table reshaped to one row per member.",
                         "numbers": {"families": {k: len(v) for k, v in list(families.items())[:20]}}, "chart": None, "interpretation": ""})

    # Long format (one "measure" column naming what each row's "value" is): comparing values across measures is
    # meaningless, so analyse each measure on its own and never mix them.
    long_fmt = _long_format(df, cols, numeric, categorical)
    if long_fmt:
        mcol, vcol, describers = long_fmt
        categorical = [c for c in categorical if c != mcol and c not in describers]
        measures = df[mcol].dropna().astype(str).value_counts()
        measures = [m for m in measures.index[:8] if measures[m] >= 6]
        for m in measures:
            sub = df[df[mcol].astype(str) == m]
            yv = pd.to_numeric(sub[vcol], errors="coerce")
            label = str(m)[:60]
            for g in categorical:
                gv = sub[g].astype(str).where(sub[g].notna(), None)
                if gv.dropna().nunique() < 2:
                    continue
                eta = _eta_squared(yv, gv)
                if eta < 0.02:
                    continue
                means = pd.DataFrame({"y": yv, "g": gv}).dropna().groupby("g")["y"].agg(["mean", "count"]).sort_values("mean", ascending=False)
                small = means[means["count"] < MIN_GROUP_N]
                means = means[means["count"] >= MIN_GROUP_N]
                if len(means) < 2:
                    continue
                hi, lo = means.index[0], means.index[-1]
                findings.append({
                    "kind": "group_difference", "score": eta * 0.9, "outcome": label, "by": g, "measure": label,
                    "excluded_small_groups": [{"group": str(i), "n": int(r["count"])} for i, r in small.iterrows()],
                    "title": f"{label} differs by {g}" + (" (large)" if eta >= 0.14 else " (medium)" if eta >= 0.06 else " (small)"),
                    "detail": f"{label} averages {charts._fmt(float(means.loc[hi, 'mean']))} for {g} = {hi} (n = {int(means.loc[hi, 'count']):,}) versus {charts._fmt(float(means.loc[lo, 'mean']))} for {lo} (n = {int(means.loc[lo, 'count']):,}); {g} explains {100 * eta:.0f}% of its variation (eta²).",
                    "numbers": {"eta_squared": round(eta, 4), "groups": [{"group": str(i), "mean": float(r["mean"]), "n": int(r["count"])} for i, r in means.head(12).iterrows()]},
                    "chart": ("bar", g, vcol, None, sub, label), "interpretation": NOT_CAUSAL})
            for d in time_cols[:1]:
                tv = pd.to_datetime(sub[d], errors="coerce") if d in dates else pd.to_numeric(sub[d], errors="coerce")
                frame = pd.DataFrame({"p": tv, "y": yv}).dropna()
                if frame["p"].nunique() < 3:
                    continue
                series = frame.groupby("p")["y"].mean().sort_index()
                first, last = float(series.iloc[0]), float(series.iloc[-1])
                if first == 0 or abs((last - first) / abs(first)) < 0.05:
                    continue
                change = (last - first) / abs(first)
                lab0 = series.index[0].date() if d in dates else series.index[0]
                lab1 = series.index[-1].date() if d in dates else series.index[-1]
                findings.append({
                    "kind": "trend", "score": min(1.0, abs(change)) * 0.8, "outcome": label, "over": d, "measure": label,
                    "title": f"{label} {'rose' if change > 0 else 'fell'} {abs(change) * 100:.0f}% from {lab0} to {lab1}",
                    "detail": f"{label} went from {charts._fmt(first)} ({lab0}) to {charts._fmt(last)} ({lab1}) across {len(series)} periods.",
                    "numbers": {"first": first, "last": last, "relative_change": round(change, 4), "periods": int(len(series))},
                    "chart": ("line", d, vcol, None, sub, label), "interpretation": "A time trend; it does not say why."})
        # the across-measure comparisons below would only restate that measures differ
        outcomes = [c for c in outcomes if c != vcol]
        if not outcomes and not findings:
            findings.append({"kind": "note", "score": 0.05, "title": f"Long-format table: {mcol} names what each {vcol} is",
                             "detail": f"{len(measures)} measures with enough rows were analysed separately; no group explained much of any of them.", "numbers": {}, "chart": None, "interpretation": ""})

    # 1. group differences
    skipped: List[str] = []
    ctx.progress(30, "group differences")
    for y in outcomes:
        yv = pd.to_numeric(df[y], errors="coerce")
        for g in categorical:
            if g == y:
                continue
            gv = df[g].astype(str).where(df[g].notna(), None)
            eta = _eta_squared(yv, gv)
            if eta < 0.02:
                continue
            means = pd.DataFrame({"y": yv, "g": gv}).dropna().groupby("g")["y"].agg(["mean", "count"]).sort_values("mean", ascending=False)
            # Groups below MIN_GROUP_N cannot support an effect-size claim; drop them from the
            # comparison and say so, rather than reporting a "large" difference built on 2 rows.
            small = means[means["count"] < MIN_GROUP_N]
            means = means[means["count"] >= MIN_GROUP_N]
            if len(means) < 2:
                if len(small):
                    skipped.append(f"{y} by {g}: {len(small)} group(s) with fewer than {MIN_GROUP_N} observations ({', '.join(str(i) for i in small.index[:4])}) were not compared.")
                continue
            hi, lo = means.index[0], means.index[-1]
            findings.append({
                "kind": "group_difference", "score": eta, "outcome": y, "by": g,
                "excluded_small_groups": [{"group": str(i), "n": int(r["count"])} for i, r in small.iterrows()],
                "title": f"{y} differs by {g}" + (" (large)" if eta >= 0.14 else " (medium)" if eta >= 0.06 else " (small)"),
                "detail": f"Mean {y} is {charts._fmt(float(means.loc[hi, 'mean']))} for {g} = {hi} (n = {int(means.loc[hi, 'count']):,}) versus {charts._fmt(float(means.loc[lo, 'mean']))} for {lo} (n = {int(means.loc[lo, 'count']):,}); {g} explains {100 * eta:.0f}% of the variation in {y} (eta²).",
                "numbers": {"eta_squared": round(eta, 4), "groups": [{"group": str(i), "mean": float(r["mean"]), "n": int(r["count"])} for i, r in means.head(12).iterrows()]},
                "chart": ("bar", g, y, None), "interpretation": NOT_CAUSAL})

    # 2. correlations
    ctx.progress(45, "correlations")
    if len(numeric) >= 2:
        num = df[numeric].apply(pd.to_numeric, errors="coerce")
        corr = num.corr(min_periods=30)
        for i, a in enumerate(numeric):
            for b in numeric[i + 1:]:
                if args.get("outcome") and args["outcome"] not in (a,b):
                    continue
                r = corr.loc[a, b]
                if pd.isna(r) or abs(r) < 0.3:
                    continue
                nn = int(num[[a, b]].dropna().shape[0])
                findings.append({
                    "kind": "correlation", "score": abs(float(r)) * 0.9, "a": a, "b": b,
                    "title": f"{a} and {b} move together" if r > 0 else f"{a} and {b} move in opposite directions",
                    "detail": f"Pearson r = {r:+.2f} across {nn:,} rows: a {'strong' if abs(r) >= 0.5 else 'moderate'} {'positive' if r > 0 else 'negative'} association.",
                    "numbers": {"r": round(float(r), 3), "n": nn}, "chart": ("scatter", a, b, None), "interpretation": NOT_CAUSAL})

    # 3. trends over time
    ctx.progress(60, "trends")
    for d in time_cols[:1]:
        is_date = d in dates
        dv = pd.to_datetime(df[d], errors="coerce") if is_date else pd.to_numeric(df[d], errors="coerce")
        if dv.notna().sum() < 10 or dv.dropna().nunique() < 3:
            continue
        per, per_name = charts._period(dv.dropna()) if is_date else (None, str(d))
        for y in outcomes[:6]:
            if y == d:
                continue
            yv = pd.to_numeric(df[y], errors="coerce")
            frame = pd.DataFrame({"p": dv.dt.to_period(per) if is_date else dv, "y": yv}).dropna()
            if frame["p"].nunique() < 3:
                continue
            series = frame.groupby("p")["y"].mean()
            first, last = float(series.iloc[0]), float(series.iloc[-1])
            if first == 0:
                continue
            change = (last - first) / abs(first)
            xs = np.arange(len(series)); slope = float(np.polyfit(xs, series.values, 1)[0])
            if abs(change) < 0.05:
                continue
            findings.append({
                "kind": "trend", "score": min(1.0, abs(change)) * 0.8, "outcome": y, "over": d,
                "title": f"{y} {'rose' if change > 0 else 'fell'} {abs(change) * 100:.0f}% over the period",
                "detail": f"Mean {y} went from {charts._fmt(first)} ({series.index[0]}) to {charts._fmt(last)} ({series.index[-1]}), about {charts._fmt(slope)} per {per_name} on a straight-line fit, over {len(series)} {per_name}s.",
                "numbers": {"first": first, "last": last, "relative_change": round(change, 4), "slope_per_period": slope, "periods": int(len(series))},
                "chart": ("line", d, y, None), "interpretation": "A time trend; it does not say why."})

    # 4. concentration in categories
    ctx.progress(70, "categories")
    concentration_signatures = {}
    for g in categorical:
        if g in coded or cols[g]["unique"] <= 2:          # a yes/no split is not a finding
            continue
        vc = df[g].dropna().astype(str).value_counts()
        if len(vc) < 3:
            continue
        share = float(vc.iloc[0] / vc.sum())
        if share >= 0.6:
            # Same row partition with different labels is one descriptive signal,
            # not two independent discoveries. Include missingness in the partition.
            codes = pd.factorize(df[g], sort=False)[0]
            signature = tuple(int(v) for v in codes)
            if signature in concentration_signatures:
                concentration_signatures[signature].setdefault("equivalent_columns", []).append(g)
                continue
            concentration_signatures[signature] = finding = {"equivalent_columns": []}
            findings.append({"kind": "concentration", "score": (share - 0.5) * 0.5, "column": g,
                             "title": f"Most rows are {g} = {vc.index[0]}",
                             "detail": f"{vc.index[0]} accounts for {share * 100:.0f}% of rows ({int(vc.iloc[0]):,} of {int(vc.sum()):,}); the other {len(vc) - 1} values share the rest. Comparisons against small groups will be noisy.",
                             "equivalent_columns": finding["equivalent_columns"], "numbers": {"share": round(share, 4), "top": str(vc.index[0]), "n": int(vc.iloc[0]), "denominator": int(vc.sum()), "denominator_definition": "non-missing values in this column"}, "chart": ("bar", g, None, None), "interpretation": ""})

    # 5. skew and outliers
    for y in numeric[:10]:
        st = cols[y].get("stats") or {}
        med, mean = st.get("median"), st.get("mean")
        if med and mean and med > 0 and mean / med >= 1.5:
            findings.append({"kind": "skew", "score": min(1.0, mean / med - 1) * 0.5, "column": y,
                             "title": f"{y} is skewed: a few large values pull the average up",
                             "detail": f"Mean {charts._fmt(mean)} versus median {charts._fmt(med)}; the top 5% of rows are above {charts._fmt(st.get('p95', 0))}. Report the median, or log-transform before modelling.",
                             "numbers": {"mean": mean, "median": med, "p95": st.get("p95")}, "chart": ("histogram", y, None, None), "interpretation": ""})
        elif st.get("outliers_iqr3", 0) and st["outliers_iqr3"] / max(1, n) > 0.01:
            findings.append({"kind": "outliers", "score": 0.3, "column": y,
                             "title": f"{y} has {st['outliers_iqr3']:,} extreme values",
                             "detail": f"{st['outliers_iqr3']:,} rows ({100 * st['outliers_iqr3'] / n:.1f}%) sit beyond three times the middle spread. Check whether they are errors or genuine before averaging.",
                             "numbers": {"outliers": int(st["outliers_iqr3"]), "share": round(st["outliers_iqr3"] / n, 4)}, "chart": ("histogram", y, None, None), "interpretation": ""})

    # 6. data quality that would bias everything above
    quality: List[Dict[str, Any]] = []
    for c, info in cols.items():
        if info["missing_pct"] >= 30:
            quality.append({"column": c, "issue": f"{info['missing_pct']:.0f}% missing", "action": "check why before using it; results describe only the rows that have a value"})
        if info.get("pii"):
            quality.append({"column": c, "issue": f"possible personal data ({info['pii'].replace('_', ' ')})", "action": "validate the field meaning and sample values locally before deciding whether redaction is appropriate; do not remove a field solely on this heuristic"})
        if "case_or_space_variants" in info["flags"]:
            quality.append({"column": c, "issue": "labels spelled inconsistently", "action": "clean_dataset (unify_labels) so groups are not split"})
    dup = int(df.duplicated().sum())
    if dup:
        quality.append({"column": None, "issue": f"{dup:,} duplicate rows", "action": "clean_dataset (drop_duplicates) unless repeats are real"})
    for d in dups[:12]:
        quality.append({"column": d["column"], "issue": f"duplicates {d['duplicate_of']} (r = {d['r']:+.2f})", "action": "use one of the two; their correlation is not a finding"})

    # Findings the user has set aside stay out of the list, whatever their score.
    excluded_terms = [str(x).strip().lower() for x in (args.get("exclude") or []) if str(x).strip()]
    excluded_count = 0
    if excluded_terms:
        def mentions_excluded(f: Dict[str, Any]) -> bool:
            fields = " ".join(str(f.get(k) or "") for k in ("outcome", "column", "a", "b", "by", "over", "title")).lower()
            return any(t in fields for t in excluded_terms)
        kept = [f for f in findings if not mentions_excluded(f)]
        excluded_count = len(findings) - len(kept)
        findings = kept

    findings.sort(key=lambda f: -f["score"])
    seen = set(); unique = []
    for f in findings:
        key = (f["kind"], f.get("outcome") or f.get("column") or f.get("a"), f.get("by") or f.get("b") or f.get("over"))
        if key in seen:
            continue
        seen.add(key); unique.append(f)
    total_findings = len(unique)
    findings = unique[: int(args.get("max_findings") or 8)]

    ctx.progress(80, "drawing")
    out = ctx.project.new_artifact_dir("insights", ds.name)
    if args.get("charts", True):
        for i, f in enumerate(findings[:6]):
            ctx.check()
            spec = f.pop("chart", None)
            if not spec:
                continue
            kind, x, y, g = spec[:4]
            frame = spec[4] if len(spec) > 4 else df
            label = spec[5] if len(spec) > 5 else None
            try:
                r = charts.draw(out, kind, frame, x, y, g, "mean", None, 12, (f"{label} by {x}" if label and kind == "bar" else (f"{label} over {x}" if label else None)),
                                None, f"Source: {ds.name}" + (f" · {label}" if label else ""), None, True, stem=f"{i + 1}-{kind}-{x}", style=style)
                f["chart_path"], f["png_path"], f["caption"] = r["chart_path"], r["png_path"], r["caption"]
            except InvalidArguments:
                continue
    for f in findings:
        f.pop("chart", None)
        f["score"] = round(float(f["score"]), 3)
        # Plain words with every finding: what each variable is and what the pattern could and could not mean.
        f["reading"] = readings(f, cols, NOT_CAUSAL)
        f["interpretation"] = interpret(f, cols, NOT_CAUSAL)
        f["meaning"] = glossary(finding_vars(f), cols, df, limit=4)
    summary = (f"{len(findings)} findings in {ds.name}." if total_findings <= len(findings) else f"First {len(findings)} of {total_findings} findings in {ds.name}.")
    if excluded_count:
        summary += f" {excluded_count} finding{'s' if excluded_count != 1 else ''} set aside as requested ({', '.join(excluded_terms[:4])})." + (f" Strongest: {findings[0]['title']}: {findings[0]['detail']}" if findings else " Nothing stood out: no group explains much of any outcome, no strong correlations, no trends.")
    if quality:
        summary += f" {len(quality)} data-quality issue{'s' if len(quality) != 1 else ''} to fix first."
    res = {"dataset_id": ds.id, "name": ds.name, "rows": n, "findings": findings, "data_quality": quality, "summary_text": summary,
           "columns_used": {"numeric": numeric[:20], "categorical": categorical[:20], "dates": dates},
           "glossary": glossary(list(dict.fromkeys(v for f in findings for v in finding_vars(f))) + [c for c in numeric + categorical + dates if c not in {v for f in findings for v in finding_vars(f)}], cols, df, limit=16),
           "limits": ["Effect sizes: eta² ≥ 0.14 large, ≥ 0.06 medium, ≥ 0.02 small; |r| ≥ 0.5 strong, ≥ 0.3 moderate.",
                      f"Groups with fewer than {MIN_GROUP_N} observations are excluded from group comparisons.",
                      "Many comparisons were screened; some 'small' patterns are expected by chance alone, so treat them as leads, not results.",
                      NOT_CAUSAL] + skipped[:6] + (["Computed on a sample of the rows."] if sampled else []),
           "findings_total": total_findings, "findings_shown": len(findings), "excluded_findings": excluded_count,
           "column_families": {k: len(v) for k, v in families.items()}, "duplicate_columns": dups[:20], "group_columns": group_cols,
           "sampled": sampled, "artifact_dir": str(out), "style_applied": style, "elapsed_s": round(time.time() - t0, 2)}
    ctx.project.write_json(out / "insights.json", res)
    ctx.project.audit({"tool": "find_insights", "dataset": ds.id, "findings": len(findings), "artifact": str(out)})
    ctx.progress(100, "done")
    return res
