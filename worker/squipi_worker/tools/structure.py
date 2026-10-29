"""Dataset structure that a column-by-column scan misses: repeated member blocks (name_1 … name_8),
duplicate or derived columns, and the column that splits the sample into treatment and control.

All deterministic. profile_dataset reports them; find_insights uses them so a survey export with a
thousand roster columns yields findings about households, not "age_1 and age_in_yrs_1 move together".
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

FAMILY = re.compile(r"^(?P<stem>.*?[A-Za-z])[_ .\-]?(?P<index>\d{1,2})$")
GROUP_NAMES = re.compile(r"(^|_)(status|treatment|treat|arm|group|condition|assignment|assigned|intervention|allocation|cohort_arm|trt)($|_)", re.I)
METADATA = re.compile(r"^(duration|start_?time|end_?time|submission_?date|device_?id|subscriber_?id|sim_?id|device_?phone_?num|username|instance_?id|form_?version|key|uuid)$", re.I)
DUPLICATE_R = 0.98
MIN_FAMILY = 3


def column_families(columns: List[str]) -> Dict[str, List[str]]:
    """{stem: [members in index order]} for stems with at least MIN_FAMILY numbered members."""
    found: Dict[str, List[Tuple[int, str]]] = {}
    for c in columns:
        m = FAMILY.match(str(c))
        if not m:
            continue
        found.setdefault(m.group("stem"), []).append((int(m.group("index")), str(c)))
    out: Dict[str, List[str]] = {}
    for stem, members in found.items():
        if len(members) >= MIN_FAMILY:
            out[stem] = [c for _, c in sorted(members)]
    return out


def duplicate_candidates(df: pd.DataFrame, cols: Dict[str, Dict[str, Any]]) -> List[str]:
    """Columns worth checking for duplication: numeric ones, plus id-labelled integer columns that repeat
    values (a household size named *_num is a quantity the id heuristic mislabels, not an identifier)."""
    out: List[str] = []
    n = max(1, len(df))
    for c, info in cols.items():
        if info.get("pii"):
            continue
        if info.get("role") == "numeric":
            out.append(c)
        elif info.get("role") == "id" and pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique(dropna=True) <= 0.5 * n:
            out.append(c)
    return out


def family_members(columns: List[str]) -> set:
    return {c for members in column_families(columns).values() for c in members}


def duplicate_columns(df: pd.DataFrame, numeric: List[str], max_columns: int = 400) -> List[Dict[str, Any]]:
    """Pairs of numeric columns that carry the same information: identical values, or |r| ≥ DUPLICATE_R.
    Each entry names the duplicate and the column it duplicates (the earlier one in the file)."""
    cols = [c for c in numeric if c in df.columns][:max_columns]
    if len(cols) < 2:
        return []
    num = df[cols].apply(pd.to_numeric, errors="coerce")
    keep = [c for c in cols if num[c].notna().sum() >= 10 and num[c].nunique(dropna=True) > 1]
    if len(keep) < 2:
        return []
    corr = num[keep].corr(min_periods=10)
    out: List[Dict[str, Any]] = []
    seen_dup: set = set()
    for i, a in enumerate(keep):
        for b in keep[i + 1:]:
            if b in seen_dup:
                continue
            r = corr.loc[a, b]
            if pd.isna(r) or abs(r) < DUPLICATE_R:
                continue
            pair = num[[a, b]].dropna()
            identical = bool(len(pair) and np.allclose(pair[a].values, pair[b].values, rtol=0, atol=1e-9))
            out.append({"column": b, "duplicate_of": a, "r": round(float(r), 4), "identical": identical})
            seen_dup.add(b)
    return out


def group_candidates(df: pd.DataFrame, cols: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Columns that look like a treatment/arm/status assignment: a group-like name and 2 to 4 levels
    that each hold at least 5% of the rows. Ordered with the most even split first."""
    out: List[Dict[str, Any]] = []
    n = len(df)
    for c, info in cols.items():
        if info.get("pii") or info.get("role") in ("id", "date", "text", "empty"):
            continue
        if not GROUP_NAMES.search(str(c)):
            continue
        vc = df[c].dropna().astype(str).value_counts()
        if not 2 <= len(vc) <= 4 or n == 0 or float(vc.iloc[-1]) / n < 0.05:
            continue
        levels = {str(k): int(v) for k, v in vc.items()}
        out.append({"column": c, "levels": levels, "balance": round(float(vc.iloc[-1] / vc.iloc[0]), 3)})
    out.sort(key=lambda g: -g["balance"])
    return out


OUTCOME_WORDS = re.compile(r"(consum|expend|income|earning|wage|revenue|profit|saving|asset|yield|harvest|loss|price|score|test|attend|enrol|complet|employ|health|weight|height|bmi|anaem|days|insecur|diet|food|water|sanit|distance|time_to|satisf|wellbeing|well_being|happy|trust|vaccin|visit|birth|mortal|pass|grade|read|math|literacy|numeracy|output|product|sales|cost)", re.I)


def outcome_candidates(df: pd.DataFrame, cols: Dict[str, Dict[str, Any]], limit: int = 8) -> List[str]:
    """Record-level numeric columns that read like outcomes, for a request that names none: not ids, dates, metadata,
    roster members, duplicates or near-constant codes. Named-like-an-outcome first, then by spread; deterministic."""
    members = family_members(list(df.columns))
    dups = {d["column"] for d in duplicate_columns(df, duplicate_candidates(df, cols))}
    scored: List[Tuple[int, float, str]] = []
    for c, info in cols.items():
        if info.get("role") != "numeric" or info.get("pii") or c in members or c in dups or is_metadata(c):
            continue
        if "few_distinct_values" in info.get("flags", []) and not OUTCOME_WORDS.search(str(c)):
            continue
        v = pd.to_numeric(df[c], errors="coerce").dropna()
        if len(v) < 10 or v.nunique() < 3:
            continue
        spread = float(v.std() / abs(v.mean())) if v.mean() else 0.0
        scored.append((0 if OUTCOME_WORDS.search(str(c)) else 1, -spread, c))
    scored.sort()
    return [c for _, _, c in scored[:limit]]


PAIR_SUFFIX = re.compile(r"^(?P<stem>.+?)[_ ]?(?P<tag>r1|r2|round1|round2|baseline|endline|midline|pre|post|before|after|t0|t1|w1|w2|y1|y2|bl|el)$", re.I)
PAIR_ORDER = {"r1": 0, "round1": 0, "baseline": 0, "pre": 0, "before": 0, "t0": 0, "w1": 0, "y1": 0, "bl": 0,
              "r2": 1, "round2": 1, "endline": 1, "midline": 1, "post": 1, "after": 1, "t1": 1, "w2": 1, "y2": 1, "el": 1}


def paired_columns(columns: List[str]) -> List[Dict[str, str]]:
    """Columns that are the same measure at two times (enrol_rate_r1 / enrol_rate_r2, income_baseline / income_endline):
    the change between them is what 'improved most' or 'difference between rounds' means."""
    by_stem: Dict[str, Dict[int, str]] = {}
    for c in columns:
        m = PAIR_SUFFIX.match(str(c))
        if not m:
            continue
        order = PAIR_ORDER.get(m.group("tag").lower())
        if order is None:
            continue
        by_stem.setdefault(m.group("stem").lower(), {})[order] = str(c)
    return [{"measure": stem, "first": pair[0], "second": pair[1]} for stem, pair in by_stem.items() if 0 in pair and 1 in pair]


def is_metadata(name: str) -> bool:
    return bool(METADATA.match(str(name)))


def describe_families(families: Dict[str, List[str]]) -> str:
    if not families:
        return ""
    stems = sorted(families, key=lambda s: -len(families[s]))
    widest = len(families[stems[0]])
    shown = ", ".join(f"{s}_1…{len(families[s])}" for s in stems[:8]) + (", …" if len(stems) > 8 else "")
    return f"{len(stems)} repeated column families (up to {widest} numbered members each: {shown}); these are per-member fields of one record, not separate variables."
