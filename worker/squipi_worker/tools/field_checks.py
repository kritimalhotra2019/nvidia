"""field_checks: high-frequency checks on survey data as it comes from the field.

Every check is plain arithmetic on the registered dataset (duplicates, durations, enumerator effects, outliers,
missingness, skip rules, consent, GPS plausibility, progress). Statuses come from fixed thresholds the caller can
set; the wording is a template filled with the computed counts. Respondent values other than ids are never
printed, and key variables that look like personal data are refused. A base-R script that re-runs the main checks
on the same file is written next to the results so field teams can repeat them in their own stack."""
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
from .datasets import detect_pii
from .stats import _load, _col, _save_table
from .impact import _not_pii, _r4, _pct, TRUE_TOKENS, FALSE_TOKENS

DAY_START_HOUR = 6            # interviews that start before 06:00 or after 21:00 are flagged
DAY_END_HOUR = 21
PRODUCTIVITY_FACTOR = 2.0     # enumerator-days above this multiple of the median daily count
MISSING_FACTOR = 2.0          # enumerators above this multiple of the median missing rate
EFFECT_P = 0.01
EFFECT_D = 0.5
GPS_SD = 3.0
GPS_ROUND = 6                 # decimals for "exactly the same point"
MAX_ISSUE_ROWS = 20_000
MAX_DAY_BARS = 60
OPS = ("==", "!=", ">=", "<=", ">", "<", "in")
RULE_RE = re.compile(r"^\s*([A-Za-z_][\w .\-]*?)\s*(==|!=|>=|<=|>|<|\bin\b)\s*(.+?)\s*$")
LITERAL_RE = re.compile(r"""^(?:"[^"]*"|'[^']*'|[\w.\-]+)$""")
SEVERITY = {"duplicate_ids": "fail", "missing_ids": "fail", "duration_too_short": "warn", "duration_too_long": "warn",
            "outside_daily_hours": "warn", "productivity": "warn", "enumerator_effects": "warn", "outliers": "warn",
            "missingness": "warn", "skip_rules": "fail", "consent": "fail", "gps_missing": "warn", "gps_duplicates": "warn",
            "gps_far": "warn", "progress": "pass"}


# ---------- small helpers

def _status(name: str, count: int) -> str:
    return "pass" if count == 0 else SEVERITY.get(name, "warn")


def _check(checks: List[Dict[str, Any]], name: str, count: int, detail: str, **extra: Any) -> Dict[str, Any]:
    row = {"name": name, "status": _status(name, int(count)), "count": int(count), "detail": detail}
    row.update(extra)
    checks.append(row)
    return row


def _issue(issues: List[Dict[str, Any]], check: str, ids: pd.Series, detail: pd.Series | str = "", column: str = "", enumerator: pd.Series | str = "") -> None:
    """Append one issue row per index of `ids`; only ids, enumerator codes and short notes, never other values."""
    if len(issues) >= MAX_ISSUE_ROWS:
        return
    det = detail if isinstance(detail, pd.Series) else pd.Series(detail, index=ids.index)
    enu = enumerator if isinstance(enumerator, pd.Series) else pd.Series(enumerator, index=ids.index)
    for i in ids.index:
        if len(issues) >= MAX_ISSUE_ROWS:
            break
        issues.append({"check": check, "row": int(i) + 2, "id": _id_text(ids.loc[i]), "enumerator": _id_text(enu.loc[i]), "column": column, "detail": str(det.loc[i])})


def _id_text(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse_dt(df: pd.DataFrame, c: Optional[str]) -> Optional[pd.Series]:
    if not c:
        return None
    s = df[c]
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, errors="coerce")
    try:
        return pd.to_datetime(s, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return pd.to_datetime(s, errors="coerce")


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _refuse_pii_id(df: pd.DataFrame, c: str, what: str) -> str:
    kind = detect_pii(str(c), df[c])
    if kind in ("name", "phone", "email", "national_id", "address"):
        raise InvalidArguments(f"{what} '{c}' looks like {kind.replace('_', ' ')}; use a respondent code, not personal data, because ids are printed in the issues table")
    return c


def _parse_literal(tok: str) -> Any:
    tok = tok.strip()
    if not LITERAL_RE.match(tok):
        raise InvalidArguments(f"skip rule value '{tok}' is not a number, a quoted string or a bare word")
    if (tok[0] == tok[-1]) and tok[0] in "\"'" and len(tok) >= 2:
        return tok[1:-1]
    try:
        return float(tok)
    except ValueError:
        return tok


def parse_rule(rule: Any, df: pd.DataFrame) -> Dict[str, Any]:
    """'<column> <op> <value>' with op in ==, !=, >, <, >=, <=, in [..]; one column, literal values only. Never eval."""
    if not isinstance(rule, dict) or not isinstance(rule.get("if"), str) or not isinstance(rule.get("then_missing"), list) or not rule["then_missing"]:
        raise InvalidArguments("each skip rule needs {\"if\": \"<column> <op> <value>\", \"then_missing\": [\"column\", ...]}")
    m = RULE_RE.match(rule["if"])
    if not m:
        raise InvalidArguments(f"skip rule '{rule['if']}' is not '<column> <op> <value>' with op one of {', '.join(OPS)} (use == not =)")
    col_text, op, val_text = m.group(1).strip(), m.group(2), m.group(3).strip()
    col = _col(df, col_text, "skip rule column")
    if op == "in":
        if not (val_text.startswith("[") and val_text.endswith("]")):
            raise InvalidArguments(f"skip rule '{rule['if']}': 'in' needs a bracketed list like in [1, 2]")
        values = [_parse_literal(t) for t in val_text[1:-1].split(",") if t.strip()]
        if not values:
            raise InvalidArguments(f"skip rule '{rule['if']}': the list after 'in' is empty")
    else:
        values = [_parse_literal(val_text)]
    then = [_col(df, c, "skip rule then_missing column") for c in rule["then_missing"]]
    return {"column": col, "op": op, "values": values, "then_missing": then, "text": rule["if"]}


def _condition(df: pd.DataFrame, r: Dict[str, Any]) -> pd.Series:
    s = df[r["column"]]
    vals = r["values"]
    numeric = all(isinstance(v, float) for v in vals)
    if numeric:
        x = pd.to_numeric(s, errors="coerce") if not pd.api.types.is_numeric_dtype(s) else s.astype(float)
        if x.notna().sum() == 0 and s.notna().sum() > 0:
            numeric = False
    if numeric:
        v = vals[0]
        if r["op"] == "in":
            out = x.isin(vals)
        else:
            out = {"==": x == v, "!=": x != v, ">": x > v, "<": x < v, ">=": x >= v, "<=": x <= v}[r["op"]]
        return out & x.notna()
    if r["op"] in (">", "<", ">=", "<="):
        raise InvalidArguments(f"skip rule '{r['text']}': '{r['op']}' needs a number; '{r['column']}' is compared as text")
    t = s.astype(str).str.strip().where(s.notna(), None)
    want = [str(v) if not isinstance(v, float) else (str(int(v)) if v.is_integer() else str(v)) for v in vals]
    if r["op"] == "in":
        out = t.isin(want)
    elif r["op"] == "==":
        out = t == want[0]
    else:
        out = (t != want[0]) & t.notna()
    return out.fillna(False).astype(bool)


def _haversine_km(lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float) -> np.ndarray:
    r = 6371.0
    p1, p2 = np.radians(lat), np.radians(lat0)
    dphi, dl = np.radians(lat - lat0), np.radians(lon - lon0)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _yes_no(s: pd.Series) -> Tuple[pd.Series, int]:
    """1 for yes/true/1, 0 for no/false/0, NaN for missing; the second value counts tokens that are neither."""
    if pd.api.types.is_bool_dtype(s):
        return s.astype(float), 0
    if pd.api.types.is_numeric_dtype(s):
        x = s.astype(float)
        out = pd.Series(np.nan, index=s.index, dtype=float)
        out[x == 1] = 1.0
        out[x == 0] = 0.0
        return out, int((x.notna() & ~x.isin([0, 1])).sum())
    t = s.astype(str).str.strip().str.lower().where(s.notna(), None)
    out = pd.Series(np.nan, index=s.index, dtype=float)
    out[t.isin(TRUE_TOKENS)] = 1.0
    out[t.isin(FALSE_TOKENS)] = 0.0
    return out, int((t.notna() & ~t.isin(TRUE_TOKENS | FALSE_TOKENS)).sum())


# ---------- R script

def _r_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _r_vec(items: List[str]) -> str:
    return "c(" + ", ".join(_r_string(x) for x in items) + ")" if items else "character(0)"


def r_script(path: str, sheet: Optional[str], id_col: str, enum_col: Optional[str], dur_col: Optional[str], start_col: Optional[str], end_col: Optional[str],
             key_vars: List[str], min_minutes: float, max_minutes: float, outlier_sd: float) -> str:
    lines = [
        f"# field_checks.R - written by Squipi on {time.strftime('%Y-%m-%d')}. Base R only, no dplyr.",
        "# Reproduces these checks on the same file: duplicate_ids, missing_ids, duration_too_short, duration_too_long, outliers, missingness.",
        "# Run: Rscript field_checks.R",
        "",
        f"path <- {_r_string(path)}",
        f"sheet <- {_r_string(sheet) if sheet else 'NULL'}",
        f"id_col <- {_r_string(id_col)}",
        f"enum_col <- {_r_string(enum_col) if enum_col else 'NULL'}",
        f"dur_col <- {_r_string(dur_col) if dur_col else 'NULL'}",
        f"start_col <- {_r_string(start_col) if start_col else 'NULL'}",
        f"end_col <- {_r_string(end_col) if end_col else 'NULL'}",
        f"key_vars <- {_r_vec(key_vars)}",
        f"min_minutes <- {min_minutes}",
        f"max_minutes <- {max_minutes}",
        f"outlier_sd <- {outlier_sd}",
        "",
        "need <- function(pkg) if (!requireNamespace(pkg, quietly = TRUE)) stop(paste0(\"install.packages('\", pkg, \"') first\"))",
        "ext <- tolower(tools::file_ext(path))",
        "data <- switch(ext,",
        "  csv = read.csv(path, stringsAsFactors = FALSE, check.names = FALSE),",
        "  txt = read.csv(path, stringsAsFactors = FALSE, check.names = FALSE),",
        "  tsv = read.delim(path, stringsAsFactors = FALSE, check.names = FALSE),",
        "  xlsx = { need(\"readxl\"); as.data.frame(if (is.null(sheet)) readxl::read_excel(path) else readxl::read_excel(path, sheet = sheet)) },",
        "  dta = { need(\"haven\"); as.data.frame(haven::read_dta(path)) },",
        "  sav = { need(\"haven\"); as.data.frame(haven::read_sav(path)) },",
        "  zsav = { need(\"haven\"); as.data.frame(haven::read_sav(path)) },",
        "  por = { need(\"haven\"); as.data.frame(haven::read_por(path)) },",
        "  parquet = { need(\"arrow\"); as.data.frame(arrow::read_parquet(path)) },",
        "  stop(paste(\"unsupported file type:\", ext)))",
        "cat(\"rows:\", nrow(data), \" columns:\", ncol(data), \"\\n\\n\")",
        "",
        "# ---- duplicate_ids and missing_ids",
        "ids <- data[[id_col]]",
        "missing_id <- is.na(ids) | trimws(as.character(ids)) == \"\"",
        "dup <- (duplicated(ids) | duplicated(ids, fromLast = TRUE)) & !missing_id",
        "cat(\"duplicate_ids:\", length(unique(ids[dup])), \"ids appear more than once (\", sum(dup), \"rows )\\n\")",
        "if (any(dup)) print(sort(table(ids[dup]), decreasing = TRUE))",
        "cat(\"missing_ids:\", sum(missing_id), \"\\n\\n\")",
        "",
        "# ---- duration_too_short and duration_too_long (minutes)",
        "minutes <- NULL",
        "if (!is.null(dur_col)) {",
        "  minutes <- suppressWarnings(as.numeric(data[[dur_col]]))",
        "} else if (!is.null(start_col) && !is.null(end_col)) {",
        "  st <- as.POSIXct(data[[start_col]], tz = \"UTC\"); en <- as.POSIXct(data[[end_col]], tz = \"UTC\")",
        "  minutes <- as.numeric(difftime(en, st, units = \"mins\"))",
        "}",
        "if (!is.null(minutes)) {",
        "  short <- !is.na(minutes) & minutes < min_minutes",
        "  long <- !is.na(minutes) & minutes > max_minutes",
        "  cat(\"duration_too_short:\", sum(short), \"interviews under\", min_minutes, \"minutes\\n\")",
        "  if (any(short)) print(data[short, c(id_col, enum_col), drop = FALSE])",
        "  cat(\"duration_too_long:\", sum(long), \"interviews over\", max_minutes, \"minutes\\n\")",
        "  if (any(long)) print(data[long, c(id_col, enum_col), drop = FALSE])",
        "  cat(\"duration median:\", round(median(minutes, na.rm = TRUE), 1), \"minutes\\n\\n\")",
        "}",
        "",
        "# ---- outliers: |z| > outlier_sd (mean/SD) and the robust version |x - median| / (1.4826 * MAD) > outlier_sd",
        "for (v in key_vars) {",
        "  x <- suppressWarnings(as.numeric(data[[v]]))",
        "  z <- (x - mean(x, na.rm = TRUE)) / sd(x, na.rm = TRUE)",
        "  rz <- (x - median(x, na.rm = TRUE)) / mad(x, na.rm = TRUE)   # mad() already applies the 1.4826 constant",
        "  sd_out <- !is.na(z) & abs(z) > outlier_sd",
        "  mad_out <- !is.na(rz) & is.finite(rz) & abs(rz) > outlier_sd",
        "  cat(\"outliers in\", v, \":\", sum(sd_out), \"by SD,\", sum(mad_out), \"by MAD\\n\")",
        "  if (any(sd_out | mad_out)) print(data[sd_out | mad_out, c(id_col, enum_col), drop = FALSE])",
        "}",
        "cat(\"\\n\")",
        "",
        "# ---- missingness: overall and by enumerator (flag enumerators above 2x the median rate)",
        "for (v in key_vars) {",
        "  miss <- is.na(data[[v]]) | trimws(as.character(data[[v]])) == \"\"",
        "  cat(\"missingness in\", v, \":\", round(100 * mean(miss), 1), \"% overall\\n\")",
        "  if (!is.null(enum_col)) {",
        "    by <- tapply(miss, data[[enum_col]], mean)",
        "    flagged <- names(by)[!is.na(by) & by > 2 * median(by, na.rm = TRUE) & by > 0]",
        "    print(round(100 * by, 1))",
        "    cat(\"  flagged enumerators:\", if (length(flagged)) paste(flagged, collapse = \", \") else \"none\", \"\\n\")",
        "  }",
        "}",
        "",
    ]
    return "\n".join(lines)


# ---------- the tool

@tool("field_checks",
      "High-frequency checks on survey data from the field: duplicate and missing ids, interview duration, interviews outside daily hours, "
      "productivity per enumerator per day, enumerator effects on key variables (one-way ANOVA + standardized spread), outliers (SD and MAD), "
      "missingness overall and by enumerator, skip-rule violations, consent, GPS plausibility and progress against the expected sample. "
      "Returns one row per check (pass/warn/fail with counts), an issues table with ids, next steps in plain words, two charts and a base-R script "
      "that reruns the main checks on the same file. Ids and enumerator codes are the only row-level values printed.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "id_column": {"type": "string", "description": "unique respondent / household id"},
          "enumerator_column": {"type": ["string", "null"], "description": "enumerator code (not a name)"},
          "date_column": {"type": ["string", "null"], "description": "interview date; the start time is used when absent"},
          "start_time": {"type": ["string", "null"], "description": "interview start timestamp column"},
          "end_time": {"type": ["string", "null"], "description": "interview end timestamp column"},
          "duration_column": {"type": ["string", "null"], "description": "interview duration in minutes (instead of start/end)"},
          "gps_lat": {"type": ["string", "null"]},
          "gps_lon": {"type": ["string", "null"]},
          "consent_column": {"type": ["string", "null"], "description": "binary consent (1/0, yes/no, true/false)"},
          "key_variables": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 30, "description": "numeric outcome columns checked for outliers, enumerator effects and missingness"},
          "skip_rules": {"type": ["array", "null"], "maxItems": 50, "items": {"type": "object", "properties": {
              "if": {"type": "string", "description": "'<column> <op> <value>' with op one of ==, !=, >, <, >=, <=, in [a, b]"},
              "then_missing": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, "required": ["if", "then_missing"]}},
          "expected_sample": {"type": ["integer", "null"], "minimum": 1},
          "max_duration_minutes": {"type": "number", "default": 180},
          "min_duration_minutes": {"type": "number", "default": 10},
          "outlier_sd": {"type": "number", "default": 4}},
       "required": ["dataset_id", "id_column"], "additionalProperties": False},
      examples=[{"dataset_id": "ds_1a2b3c4d", "id_column": "hh_id", "enumerator_column": "enum_id", "start_time": "start", "end_time": "end",
                 "key_variables": ["consumption"], "skip_rules": [{"if": "has_business == 0", "then_missing": ["business_income"]}], "expected_sample": 1000}])
def field_checks(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    n = int(len(df))
    if n == 0:
        raise InvalidArguments("the dataset has no rows")
    idc = _refuse_pii_id(df, _col(df, args["id_column"], "id_column"), "id_column")
    opt = lambda k, what: (_col(df, args[k], what) if args.get(k) else None)  # noqa: E731
    enum = opt("enumerator_column", "enumerator_column")
    if enum:
        _not_pii(df, enum, "enumerator_column")
    datec, startc, endc, durc = opt("date_column", "date_column"), opt("start_time", "start_time"), opt("end_time", "end_time"), opt("duration_column", "duration_column")
    latc, lonc, consc = opt("gps_lat", "gps_lat"), opt("gps_lon", "gps_lon"), opt("consent_column", "consent_column")
    if (latc is None) != (lonc is None):
        raise InvalidArguments("gps_lat and gps_lon must be given together")
    keys = [_not_pii(df, _col(df, k, "key variable"), "key variable") for k in (args.get("key_variables") or [])]
    keys = list(dict.fromkeys(keys))
    if idc in keys:
        raise InvalidArguments("the id column cannot be a key variable")
    rules = [parse_rule(r, df) for r in (args.get("skip_rules") or [])]
    min_min = float(args.get("min_duration_minutes", 10) or 10)
    max_min = float(args.get("max_duration_minutes", 180) or 180)
    sd_k = float(args.get("outlier_sd", 4) or 4)
    expected = args.get("expected_sample")
    if min_min >= max_min:
        raise InvalidArguments("min_duration_minutes must be below max_duration_minutes")
    if sd_k <= 0:
        raise InvalidArguments("outlier_sd must be positive")

    checks: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    tables: Dict[str, List[Dict[str, Any]]] = {}
    skipped: List[Dict[str, str]] = []
    next_steps: List[str] = []
    ids = df[idc]
    enum_s = df[enum].astype(str).where(df[enum].notna(), None) if enum else pd.Series("", index=df.index)

    # ---- ids
    ctx.progress(10, "ids")
    id_text = ids.astype(str).str.strip()
    missing_id = ids.isna() | (id_text == "")
    _check(checks, "missing_ids", int(missing_id.sum()), f"{_plural(int(missing_id.sum()), 'row')} without an id")
    _issue(issues, "missing_ids", ids[missing_id], "id is blank", idc, enum_s[missing_id])
    dup_mask = ids.duplicated(keep=False) & ~missing_id
    dup_ids = int(ids[dup_mask].nunique())
    rep = _check(checks, "duplicate_ids", dup_ids, f"{_plural(dup_ids, 'id')} appear more than once ({int(dup_mask.sum()):,} rows involved)", rows=int(dup_mask.sum()))
    counts = ids[dup_mask].map(ids[dup_mask].value_counts())
    _issue(issues, "duplicate_ids", ids[dup_mask], counts.map(lambda c: f"id appears {int(c)} times"), idc, enum_s[dup_mask])
    if dup_ids:
        next_steps.append(f"Check the {_plural(dup_ids, 'id')} that appear more than once ({rep['rows']} rows) and keep one record for each.")
    if int(missing_id.sum()):
        next_steps.append(f"Add ids to the {_plural(int(missing_id.sum()), 'row')} that have none.")

    # ---- duration
    ctx.progress(20, "durations")
    start = _parse_dt(df, startc)
    end = _parse_dt(df, endc)
    minutes: Optional[pd.Series] = None
    if durc:
        minutes = pd.to_numeric(df[durc], errors="coerce").astype(float)
    elif start is not None and end is not None:
        minutes = (end - start).dt.total_seconds() / 60.0
    if minutes is not None:
        short = minutes.notna() & (minutes < min_min)
        long_ = minutes.notna() & (minutes > max_min)
        med = float(minutes.median()) if minutes.notna().any() else None
        _check(checks, "duration_too_short", int(short.sum()), f"{_plural(int(short.sum()), 'interview')} under {min_min:g} minutes (median {med:.0f} minutes)" if med is not None else "no readable durations",
               median_minutes=_r4(med), missing=int(minutes.isna().sum()))
        _check(checks, "duration_too_long", int(long_.sum()), f"{_plural(int(long_.sum()), 'interview')} over {max_min:g} minutes", median_minutes=_r4(med))
        _issue(issues, "duration_too_short", ids[short], minutes[short].map(lambda m: f"{m:.1f} minutes"), durc or f"{endc} - {startc}", enum_s[short])
        _issue(issues, "duration_too_long", ids[long_], minutes[long_].map(lambda m: f"{m:.0f} minutes"), durc or f"{endc} - {startc}", enum_s[long_])
        if int(short.sum()):
            next_steps.append(f"Call back the {_plural(int(short.sum()), 'interview')} with a duration under {min_min:g} minutes.")
        if int(long_.sum()):
            next_steps.append(f"Ask about the {_plural(int(long_.sum()), 'interview')} that took over {max_min:g} minutes (left open, or interrupted?).")
    else:
        skipped.append({"check": "duration", "reason": "needs duration_column or start_time and end_time"})

    # ---- daily hours
    if start is not None and start.notna().any():
        tod = start.dt.hour * 60 + start.dt.minute
        if (tod.dropna() != 0).mean() > 0.5:
            outside = start.notna() & ((start.dt.hour < DAY_START_HOUR) | (start.dt.hour >= DAY_END_HOUR))
            _check(checks, "outside_daily_hours", int(outside.sum()), f"{_plural(int(outside.sum()), 'interview')} started before {DAY_START_HOUR:02d}:00 or after {DAY_END_HOUR:02d}:00")
            _issue(issues, "outside_daily_hours", ids[outside], start[outside].dt.strftime("started %H:%M"), startc, enum_s[outside])
            if int(outside.sum()):
                next_steps.append(f"Confirm the {_plural(int(outside.sum()), 'interview')} recorded outside {DAY_START_HOUR:02d}:00-{DAY_END_HOUR:02d}:00 (device clock, or entered later?).")
        else:
            skipped.append({"check": "outside_daily_hours", "reason": f"'{startc}' has dates without a time of day"})
    else:
        skipped.append({"check": "outside_daily_hours", "reason": "needs start_time with a time of day"})

    # ---- dates: progress and productivity
    ctx.progress(30, "progress")
    dates = _parse_dt(df, datec) if datec else start
    day = dates.dt.normalize() if dates is not None else None
    valid_ids = int(ids[~missing_id].nunique())
    if day is not None and day.notna().any():
        by_day = day.value_counts().sort_index()
        tables["by_date"] = [{"date": d.strftime("%Y-%m-%d"), "interviews": int(c)} for d, c in by_day.items()]
        first, last = by_day.index[0].strftime("%Y-%m-%d"), by_day.index[-1].strftime("%Y-%m-%d")
        detail = f"{n:,} rows, {valid_ids:,} unique ids, {len(by_day)} days from {first} to {last}, {by_day.mean():.1f} per day"
    else:
        detail = f"{n:,} rows, {valid_ids:,} unique ids"
        skipped.append({"check": "interviews_per_day", "reason": "needs date_column or start_time"})
    share = (valid_ids / expected) if expected else None
    if expected:
        detail += f"; {_pct(share)}% of the expected {int(expected):,}"
    _check(checks, "progress", valid_ids, detail, expected_sample=(int(expected) if expected else None), share_achieved=_r4(share), unique_ids=valid_ids)
    if expected and valid_ids < expected and day is not None and day.notna().any():
        pace = valid_ids / max(1, day.nunique())
        next_steps.append(f"{valid_ids:,} of {int(expected):,} expected interviews are in ({_pct(share)}%); at {pace:.0f} a day the rest takes about {math.ceil((expected - valid_ids) / max(pace, 1e-9))} more days.")
    if enum and day is not None and day.notna().any():
        ed = pd.DataFrame({"e": enum_s, "d": day}).dropna()
        per = ed.groupby(["e", "d"]).size()
        med_daily = float(per.median())
        limit = PRODUCTIVITY_FACTOR * med_daily
        flagged = per[per > limit]
        by_enum = per.groupby(level=0).agg(["mean", "max", "count"])
        tables["productivity"] = [{"enumerator": str(e), "days": int(r["count"]), "mean_per_day": _r4(r["mean"]), "max_per_day": int(r["max"]),
                                   "flagged_days": int((per.loc[e] > limit).sum())} for e, r in by_enum.iterrows()]
        _check(checks, "productivity", int(len(flagged)), f"{_plural(int(len(flagged)), 'enumerator-day')} above {PRODUCTIVITY_FACTOR:g} x the median of {med_daily:g} interviews a day (max {int(per.max())})",
               median_per_day=_r4(med_daily), threshold=_r4(limit))
        for (e, d), c in flagged.items():
            issues.append({"check": "productivity", "row": "", "id": "", "enumerator": str(e), "column": datec or startc, "detail": f"{int(c)} interviews on {d.strftime('%Y-%m-%d')}"})
        if len(flagged):
            who = ", ".join(sorted({str(e) for e, _ in flagged.index})[:5])
            next_steps.append(f"Look at the {_plural(int(len(flagged)), 'day')} with more than {limit:g} interviews by one enumerator ({who}): too many to be real?")
    elif enum:
        skipped.append({"check": "productivity", "reason": "needs date_column or start_time"})
    else:
        skipped.append({"check": "productivity", "reason": "needs enumerator_column"})

    # ---- key variables: enumerator effects, outliers, missingness
    ctx.progress(45, "key variables")
    if keys:
        from scipy import stats as st
        eff_rows, out_rows, miss_rows = [], [], []
        n_eff = n_out = n_miss = 0
        for k in keys:
            ctx.check()
            x = pd.to_numeric(df[k], errors="coerce").astype(float)
            v = x.dropna()
            # outliers
            mean, sd = (float(v.mean()), float(v.std(ddof=1))) if len(v) > 1 else (None, None)
            medv = float(v.median()) if len(v) else None
            mad = float((v - medv).abs().median()) * 1.4826 if len(v) else None
            z = (x - mean) / sd if sd else pd.Series(np.nan, index=x.index)
            rz = (x - medv) / mad if mad else pd.Series(np.nan, index=x.index)
            sd_out = z.notna() & (z.abs() > sd_k)
            mad_out = rz.notna() & (rz.abs() > sd_k)
            out_rows.append({"column": k, "n": int(len(v)), "mean": _r4(mean), "sd": _r4(sd), "median": _r4(medv), "mad_scaled": _r4(mad),
                             "sd_outliers": int(sd_out.sum()), "mad_outliers": int(mad_out.sum()), "min": _r4(v.min()) if len(v) else None, "max": _r4(v.max()) if len(v) else None})
            n_out += int(sd_out.sum())
            either = sd_out | mad_out
            how = pd.Series(np.where(sd_out & mad_out, "beyond SD and MAD limits", np.where(sd_out, "beyond SD limit", "beyond MAD limit")), index=x.index)
            _issue(issues, "outliers", ids[either], how[either] + z[either].map(lambda q: f" (z = {q:.1f})" if pd.notna(q) else ""), k, enum_s[either])
            # missingness
            miss = x.isna()
            row = {"column": k, "missing": int(miss.sum()), "missing_pct": _pct(miss.mean()), "flagged_enumerators": []}
            if enum:
                by = pd.DataFrame({"e": enum_s, "m": miss}).dropna(subset=["e"]).groupby("e")["m"].agg(["mean", "size"])
                med_rate = float(by["mean"].median())
                bad = by[(by["mean"] > MISSING_FACTOR * med_rate) & (by["mean"] > 0)]
                row["median_enumerator_pct"] = _pct(med_rate)
                row["by_enumerator"] = {str(e): _pct(r["mean"]) for e, r in by.iterrows()}
                row["flagged_enumerators"] = [str(e) for e in bad.index]
                n_miss += int(len(bad))
                for e, r in bad.iterrows():
                    issues.append({"check": "missingness", "row": "", "id": "", "enumerator": str(e), "column": k,
                                   "detail": f"{_pct(r['mean'])}% missing vs median {_pct(med_rate)}%"})
                    next_steps.append(f"Ask enumerator {e} why '{k}' is missing in {_pct(r['mean'])}% of their interviews (others: {_pct(med_rate)}%).")
            miss_rows.append(row)
            # enumerator effects
            if enum:
                g = pd.DataFrame({"e": enum_s, "x": x}).dropna()
                groups = [grp["x"].to_numpy(float) for _, grp in g.groupby("e") if len(grp) >= 2]
                if len(groups) >= 2 and sum(len(a) for a in groups) >= 4:
                    means = g.groupby("e")["x"].agg(["mean", "size", "var"])
                    within = float(np.nanmean(means.loc[means["size"] >= 2, "var"]))
                    pooled = math.sqrt(within) if within and within > 0 else 0.0
                    spread = float(means["mean"].max() - means["mean"].min())
                    d = spread / pooled if pooled > 0 else 0.0
                    with np.errstate(all="ignore"):
                        p = float(st.f_oneway(*groups).pvalue) if pooled > 0 else None
                    grand = float(g["x"].mean())
                    dev = ((means["mean"] - grand) / pooled) if pooled > 0 else (means["mean"] * 0)
                    worst = str(dev.abs().idxmax())
                    flag = bool(p is not None and p < EFFECT_P and abs(d) > EFFECT_D)
                    eff_rows.append({"column": k, "p": _r4(p), "spread_d": _r4(d), "enumerators": int(len(means)), "highest": str(means["mean"].idxmax()),
                                     "highest_mean": _r4(means["mean"].max()), "lowest": str(means["mean"].idxmin()), "lowest_mean": _r4(means["mean"].min()),
                                     "most_different": worst, "most_different_d": _r4(float(dev.loc[worst])), "flag": flag})
                    if flag:
                        n_eff += 1
                        issues.append({"check": "enumerator_effects", "row": "", "id": "", "enumerator": worst, "column": k,
                                       "detail": f"mean differs from the others by {float(dev.loc[worst]):.2f} SD (ANOVA p = {p:.2g})"})
                        next_steps.append(f"Review enumerator {worst}: their mean '{k}' sits {abs(float(dev.loc[worst])):.1f} SD from the overall mean (ANOVA p = {p:.2g}); sit in on an interview or re-interview a few of their respondents.")
        tables["outliers"] = out_rows
        tables["missingness"] = miss_rows
        _check(checks, "outliers", n_out, f"{_plural(n_out, 'value')} beyond {sd_k:g} SD across {len(keys)} key variable{'s' if len(keys) != 1 else ''} ({sum(r['mad_outliers'] for r in out_rows):,} by the robust MAD rule)",
               mad_outliers=int(sum(r["mad_outliers"] for r in out_rows)))
        if n_out:
            next_steps.append(f"Check the {_plural(n_out, 'value')} beyond {sd_k:g} SD in the issues table: typing errors, unit mix-ups or real cases?")
        if enum:
            tables["enumerator_effects"] = eff_rows
            _check(checks, "enumerator_effects", n_eff, f"{_plural(n_eff, 'key variable')} where enumerator means differ (ANOVA p < {EFFECT_P} and spread > {EFFECT_D} SD)")
            _check(checks, "missingness", n_miss, f"{_plural(n_miss, 'enumerator-variable pair')} with a missing rate above {MISSING_FACTOR:g} x the median; overall missing: " +
                   ", ".join(f"{r['column']} {r['missing_pct']}%" for r in miss_rows))
        else:
            _check(checks, "missingness", 0, "overall missing: " + ", ".join(f"{r['column']} {r['missing_pct']}%" for r in miss_rows))
            skipped.append({"check": "enumerator_effects", "reason": "needs enumerator_column"})
    else:
        skipped.append({"check": "outliers, enumerator_effects, missingness", "reason": "needs key_variables"})

    # ---- skip rules
    ctx.progress(65, "skip rules")
    if rules:
        viol_any = pd.Series(False, index=df.index)
        rule_rows = []
        for r in rules:
            cond = _condition(df, r)
            filled = pd.Series(False, index=df.index)
            for c in r["then_missing"]:
                s = df[c]
                present = s.notna() & (s.astype(str).str.strip() != "")
                filled |= present
            viol = cond & filled
            viol_any |= viol
            rule_rows.append({"rule": r["text"], "then_missing": r["then_missing"], "rows_where_condition_holds": int(cond.sum()), "violations": int(viol.sum())})
            _issue(issues, "skip_rules", ids[viol], f"{', '.join(r['then_missing'])} filled although {r['text']}", r["column"], enum_s[viol])
        tables["skip_rules"] = rule_rows
        _check(checks, "skip_rules", int(viol_any.sum()), f"{_plural(int(viol_any.sum()), 'interview')} break at least one of {len(rules)} skip rule{'s' if len(rules) != 1 else ''}")
        for r in rule_rows:
            if r["violations"]:
                next_steps.append(f"Fix the {_plural(r['violations'], 'interview')} where {', '.join(r['then_missing'])} is filled although {r['rule']} (form logic or data entry).")
    else:
        skipped.append({"check": "skip_rules", "reason": "no skip_rules given"})

    # ---- consent
    if consc:
        yn, _odd = _yes_no(df[consc])
        missing_c = df[consc].isna() | (df[consc].astype(str).str.strip() == "")
        refused = yn == 0
        unknown = df[consc].notna() & yn.isna() & ~missing_c
        total = int(missing_c.sum() + refused.sum())
        _check(checks, "consent", total, f"{_plural(int(missing_c.sum()), 'interview')} without a recorded consent, {int(refused.sum()):,} refused" +
               (f", {int(unknown.sum()):,} with an unrecognised value" if int(unknown.sum()) else ""), missing=int(missing_c.sum()), refused=int(refused.sum()), unrecognised=int(unknown.sum()))
        _issue(issues, "consent", ids[missing_c], "consent not recorded", consc, enum_s[missing_c])
        _issue(issues, "consent", ids[refused], "consent refused but interview present", consc, enum_s[refused])
        if int(missing_c.sum()):
            next_steps.append(f"Record or re-confirm consent for the {_plural(int(missing_c.sum()), 'interview')} without it; do not analyse them until then.")
        if int(refused.sum()):
            next_steps.append(f"Remove the {_plural(int(refused.sum()), 'interview')} where consent was refused.")
    else:
        skipped.append({"check": "consent", "reason": "needs consent_column"})

    # ---- GPS
    ctx.progress(78, "gps")
    if latc:
        lat = pd.to_numeric(df[latc], errors="coerce").astype(float)
        lon = pd.to_numeric(df[lonc], errors="coerce").astype(float)
        bad = lat.isna() | lon.isna() | (lat.abs() > 90) | (lon.abs() > 180)
        _check(checks, "gps_missing", int(bad.sum()), f"{_plural(int(bad.sum()), 'interview')} without usable coordinates")
        _issue(issues, "gps_missing", ids[bad], "coordinates missing or out of range", f"{latc}/{lonc}", enum_s[bad])
        ok = ~bad & ~missing_id
        pts = pd.DataFrame({"lat": lat[ok].round(GPS_ROUND), "lon": lon[ok].round(GPS_ROUND), "id": ids[ok].astype(str)})
        grp = pts.groupby(["lat", "lon"])["id"].nunique()
        shared = grp[grp > 1]
        dup_pts = pts.set_index(["lat", "lon"]).index.isin(shared.index) if len(shared) else np.zeros(len(pts), dtype=bool)
        dup_idx = pts.index[dup_pts]
        _check(checks, "gps_duplicates", int(len(dup_idx)), f"{_plural(int(len(dup_idx)), 'interview')} share exactly the same point with a different id ({_plural(len(shared), 'point')})")
        _issue(issues, "gps_duplicates", ids[dup_idx], "same coordinates as another id", f"{latc}/{lonc}", enum_s[dup_idx])
        far_idx = pd.Index([])
        if ok.sum() >= 3:
            lat0, lon0 = float(lat[ok].mean()), float(lon[ok].mean())
            dist = pd.Series(_haversine_km(lat[ok].to_numpy(), lon[ok].to_numpy(), lat0, lon0), index=lat[ok].index)
            limit = float(dist.mean() + GPS_SD * dist.std(ddof=1)) if len(dist) > 1 else float("inf")
            far = dist > limit
            far_idx = dist.index[far]
            _check(checks, "gps_far", int(far.sum()), f"{_plural(int(far.sum()), 'interview')} more than {limit:.1f} km from the cluster centre (mean + {GPS_SD:g} SD of the distance; median distance {dist.median():.1f} km)",
                   limit_km=_r4(limit), median_km=_r4(float(dist.median())))
            _issue(issues, "gps_far", ids[far_idx], dist[far].map(lambda d: f"{d:.1f} km from the centre"), f"{latc}/{lonc}", enum_s[far_idx])
        else:
            skipped.append({"check": "gps_far", "reason": "fewer than 3 usable points"})
        if int(bad.sum()):
            next_steps.append(f"Capture coordinates again for the {_plural(int(bad.sum()), 'interview')} without them.")
        if len(dup_idx):
            next_steps.append(f"Check the {_plural(int(len(dup_idx)), 'interview')} that share one GPS point with a different id (same household entered twice, or a copied form?).")
        if len(far_idx):
            next_steps.append(f"Verify the location of the {_plural(int(len(far_idx)), 'interview')} far from the cluster centre.")
    else:
        skipped.append({"check": "gps", "reason": "needs gps_lat and gps_lon"})

    # ---- outputs
    ctx.progress(88, "writing")
    out = ctx.project.new_artifact_dir("field-checks", ds.name)
    issues_df = pd.DataFrame(issues, columns=["check", "row", "id", "enumerator", "column", "detail"])
    issues_csv = _save_table(ctx, out, "issues", issues_df)
    checks_csv = _save_table(ctx, out, "checks", pd.DataFrame([{k: v for k, v in c.items() if not isinstance(v, (dict, list))} for c in checks]))
    charts: List[Dict[str, Any]] = []
    if tables.get("by_date"):
        rows_bd = tables["by_date"]
        if len(rows_bd) > MAX_DAY_BARS:
            wk = pd.Series({pd.Timestamp(r["date"]): r["interviews"] for r in rows_bd}).resample("W").sum()
            labels, values, unit = [d.strftime("%Y-%m-%d") for d in wk.index], [int(v) for v in wk.values], "week"
        else:
            labels, values, unit = [r["date"] for r in rows_bd], [r["interviews"] for r in rows_bd], "day"
        s = svg.bars(f"Interviews per {unit}", labels, values, subtitle=f"{n:,} rows · {valid_ids:,} unique ids" + (f" · expected {int(expected):,}" if expected else ""))
        p_ = ctx.project.write_text(out / "interviews_per_day.svg", s)
        charts.append({"id": "interviews_per_day", "path": str(p_), "title": f"Interviews per {unit}",
                       "caption": f"One bar per {unit}; its length is how many interviews were recorded. Gaps and spikes show where the field plan and the data differ."})
    if minutes is not None and minutes.notna().sum() >= 2:
        v = minutes.dropna().clip(lower=0, upper=max(max_min * 1.5, float(minutes.quantile(0.99))))
        k_bins = int(min(24, max(8, round(math.sqrt(len(v))))))
        cnt, edges = np.histogram(v, bins=k_bins)
        s = svg.histogram("How long interviews took (minutes)", [float(e) for e in edges], [int(c) for c in cnt],
                          subtitle=f"median {float(minutes.median()):.0f} min · limits {min_min:g}-{max_min:g} min · {int(minutes.isna().sum())} without a duration")
        p_ = ctx.project.write_text(out / "duration.svg", s)
        charts.append({"id": "duration", "path": str(p_), "title": "How long interviews took (minutes)",
                       "caption": f"Interviews grouped by duration; bars below {min_min:g} minutes are suspiciously short, bars above {max_min:g} minutes suspiciously long."})
    script = r_script(ds.path, ds.sheet, idc, enum, durc, startc, endc, keys, min_min, max_min, sd_k)
    script_path = str(ctx.project.write_text(out / "field_checks.R", script))

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    passed = [c for c in checks if c["status"] == "pass"]
    summary = f"{n:,} rows checked: {len(failed)} check{'s' if len(failed) != 1 else ''} failed, {len(warned)} warned, {len(passed)} passed."
    interp = [summary]
    for c in failed + warned:
        interp.append(f"{c['name'].replace('_', ' ').replace('gps', 'GPS').capitalize().replace('Gps', 'GPS')} ({c['status']}): {c['detail']}.")
    if not failed and not warned:
        interp.append("Nothing in these checks points to a field problem; they look only at what was given, so the checks that were skipped still need their columns.")
    if skipped:
        interp.append("Skipped: " + "; ".join(f"{s_['check']} ({s_['reason']})" for s_ in skipped) + ".")
    if sampled:
        interp.append(f"Computed on the first {n:,} rows.")
    if len(issues) >= MAX_ISSUE_ROWS:
        interp.append(f"The issues table stops at {MAX_ISSUE_ROWS:,} rows.")
    if not next_steps:
        next_steps.append("No action needed from these checks; rerun them after the next batch of interviews.")
    ctx.project.audit({"tool": "field_checks", "dataset": ds.id, "rows": n, "failed": len(failed), "warned": len(warned), "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "rows": n, "unique_ids": valid_ids, "id_column": idc, "enumerator_column": enum, "key_variables": keys,
            "thresholds": {"min_duration_minutes": min_min, "max_duration_minutes": max_min, "outlier_sd": sd_k, "daily_hours": [DAY_START_HOUR, DAY_END_HOUR],
                           "productivity_factor": PRODUCTIVITY_FACTOR, "missing_factor": MISSING_FACTOR, "effect_p": EFFECT_P, "effect_d": EFFECT_D, "gps_sd": GPS_SD},
            "checks": checks, "failed": [c["name"] for c in failed], "warned": [c["name"] for c in warned], "skipped": skipped,
            "tables": tables, "issues": len(issues), "issues_csv": issues_csv, "checks_csv": checks_csv,
            "charts": charts, "chart_path": charts[0]["path"] if charts else None, "script_r": script_path,
            "summary_text": summary, "interpretation": interp, "next_steps": next_steps,
            "caption": charts[0]["caption"] if charts else "", "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}
