"""clean_dataset: a conservative, fully logged clean-up that writes a new file and leaves the original alone.

Every step is deterministic and counted, so the change log can be shown and checked. Nothing here imputes
by default: filling missing values is a modelling choice the user has to ask for."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .datasets import read_dataset, _parse_dates, DATE_NAME, ID_NAME, detect_pii

import hashlib
import secrets

# patterns redacted inside free text when pii is pseudonymize/drop
REDACT = [
    ("[email]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("[url]", re.compile(r"https?://\S+")),
    ("[phone]", re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,18}\d)(?!\d)")),
    ("[id-number]", re.compile(r"(?<![\w])\d{9,16}(?![\w])")),
]


def _salt(project) -> str:
    """One secret per project so pseudonyms are stable across runs but cannot be reversed without it."""
    p = project.root / "pseudonym.salt"
    if p.exists():
        return p.read_text("utf-8").strip()
    s = secrets.token_hex(16)
    p.write_text(s, "utf-8")
    return s


def pseudonymize(s: pd.Series, salt: str, prefix: str = "p") -> pd.Series:
    def code(v):
        if pd.isna(v):
            return None
        return prefix + "_" + hashlib.sha256((salt + "|" + str(v).strip().lower()).encode("utf-8")).hexdigest()[:10]
    return s.map(code)


def redact_text(s: pd.Series) -> Tuple[pd.Series, int]:
    nn = s.dropna().astype(str)
    out = nn.copy()
    hits = 0
    for label, rx in REDACT:
        n = out.str.count(rx).sum()
        if n:
            hits += int(n)
            out = out.str.replace(rx, label, regex=True)
    res = s.astype(object).copy()
    res.loc[out.index] = out
    return res, hits

MISSING_TOKENS = {"", "na", "n/a", "n.a.", "nan", "null", "none", "nil", "-", "--", "—", ".", "?", "missing", "#n/a", "#null!", "#ref!", "#value!", "unknown", "not available"}
NUM_CLEAN = re.compile(r"[,\s$€£¥₹%]|(?<=\d)_(?=\d)")
ALL_STEPS = ["normalize_names", "trim_whitespace", "standardize_missing", "drop_empty_rows", "drop_empty_columns",
             "drop_duplicates", "coerce_numeric", "parse_dates", "unify_labels"]
DEFAULT_STEPS = ALL_STEPS


def normalize_name(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z]+", "_", str(name).strip()).strip("_").lower()
    s = re.sub(r"_+", "_", s)
    return s or "column"


def _dedupe(names: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def _coerce_numeric(s: pd.Series) -> Optional[pd.Series]:
    """Object column → float if at least 95% of the non-missing values are numbers once separators and
    currency/percent signs are removed. Returns None when it does not look numeric."""
    nn = s.dropna().astype(str).str.strip()
    if len(nn) == 0:
        return None
    cleaned = nn.str.replace(NUM_CLEAN, "", regex=True)
    # parentheses for negatives: (1,200) → -1200
    neg = cleaned.str.match(r"^\(.*\)$")
    cleaned = cleaned.where(~neg, "-" + cleaned.str.strip("()"))
    num = pd.to_numeric(cleaned, errors="coerce")
    if num.notna().mean() < 0.95:
        return None
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    out.loc[num.index] = num
    return out


@tool("clean_dataset",
      "Make a cleaned copy of a dataset and register it as a new dataset (the original is untouched). Steps, all deterministic and counted in the change log: "
      "normalize_names (snake_case headers), trim_whitespace, standardize_missing (NA, n/a, -, ? … become missing), drop_empty_rows, drop_empty_columns, "
      "drop_duplicates (exact duplicate rows), coerce_numeric (text numbers with commas, currency or % signs become numbers), parse_dates (text dates become ISO dates), "
      "unify_labels (category labels that differ only by case or spacing become one label). fill_missing is off unless asked (median for numbers, most common value for categories). "
      "Personal data: pii='pseudonymize' replaces names/ids/contact details with stable salted codes (same person → same code) and redacts emails, phones and long numbers inside free text; "
      "pii='drop' removes those columns instead. Detected columns can be extended with pii_columns. For selective removal, use pii='keep', drop_columns with exactly the requested names, and steps=[] to preserve the other columns and values.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "output_name": {"type":"string","minLength":1,"maxLength":100,"description":"Exact requested cleaned CSV basename, without a path; use export_excel(filename) for XLSX."},
          "steps": {"type": "array", "items": {"type": "string", "enum": ALL_STEPS}, "description": "which steps to run; omit for defaults; [] skips all automatic cleaning steps"},
          "fill_missing": {"type": "string", "enum": ["none", "median_mode"], "description": "default none"},
          "columns": {"type": ["array", "null"], "items": {"type": "string"}, "description": "restrict coercion/parsing/filling to these columns (default: all)"},
          "pii": {"type": "string", "enum": ["keep", "pseudonymize", "drop"], "description": "what to do with columns that look like personal data (default keep)"},
          "pii_columns": {"type": ["array", "null"], "items": {"type": "string"}, "description": "extra columns to treat as personal data, in addition to the ones detected"},
          "drop_columns": {"type": ["array", "null"], "items": {"type": "string"}, "description": "columns to remove outright"}},
       "required": ["dataset_id"], "additionalProperties": False})
def clean_dataset(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    ds = ctx.project.dataset(args["dataset_id"])
    ctx.project.allow.check_readable(ds.path)
    steps = list(args["steps"] if "steps" in args else DEFAULT_STEPS)
    fill = args.get("fill_missing") or "none"
    only = set(args.get("columns") or [])
    ctx.progress(5, "reading")
    df = read_dataset(ds)
    rows0, cols0 = int(df.shape[0]), int(df.shape[1])
    log: List[Dict[str, Any]] = []

    def note(step: str, count: int, detail: str, column: Optional[str] = None) -> None:
        if count:
            log.append({"step": step, "column": column, "count": int(count), "detail": detail})

    def wanted(col: str) -> bool:
        return not only or col in only

    if "normalize_names" in steps:
        new = _dedupe([normalize_name(c) for c in df.columns])
        changed = [(str(a), b) for a, b in zip(df.columns, new) if str(a) != b]
        df.columns = new
        note("normalize_names", len(changed), "; ".join(f"{a} → {b}" for a, b in changed[:12]) + (" …" if len(changed) > 12 else ""))
        only = {normalize_name(c) for c in only}
    ctx.progress(15, "whitespace and missing tokens")
    obj_cols = [c for c in df.columns if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])]
    if "trim_whitespace" in steps:
        total = 0
        for c in obj_cols:
            s = df[c]
            mask = s.notna()
            stripped = s[mask].astype(str).str.strip()
            changed = int((stripped != s[mask].astype(str)).sum())
            if changed:
                df.loc[mask, c] = stripped
                total += changed
                note("trim_whitespace", changed, "leading/trailing spaces removed", c)
        if total == 0:
            pass
    if "standardize_missing" in steps:
        for c in obj_cols:
            s = df[c]
            mask = s.notna() & s.astype(str).str.strip().str.lower().isin(MISSING_TOKENS)
            n = int(mask.sum())
            if n:
                df.loc[mask, c] = np.nan
                note("standardize_missing", n, "placeholder values (NA, n/a, -, ? …) marked as missing", c)
    ctx.progress(30, "empty rows and columns")
    if "drop_empty_columns" in steps:
        empty = [c for c in df.columns if df[c].isna().all()]
        if empty:
            df = df.drop(columns=empty)
            note("drop_empty_columns", len(empty), ", ".join(map(str, empty[:12])))
    if "drop_empty_rows" in steps:
        before = len(df)
        df = df.dropna(how="all")
        note("drop_empty_rows", before - len(df), "rows with every value missing")
    if "drop_duplicates" in steps:
        before = len(df)
        df = df.drop_duplicates()
        note("drop_duplicates", before - len(df), "exact duplicate rows removed (first occurrence kept)")
    ctx.progress(45, "numbers and dates")
    obj_cols = [c for c in df.columns if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])]
    if "coerce_numeric" in steps:
        for c in obj_cols:
            if not wanted(c) or ID_NAME.search(str(c)):
                continue
            num = _coerce_numeric(df[c])
            if num is not None:
                had_pct = bool(df[c].dropna().astype(str).str.contains("%").mean() > 0.5)
                lost = int(df[c].notna().sum() - num.notna().sum())
                df[c] = num
                note("coerce_numeric", int(num.notna().sum()), "text converted to numbers" + (" (percent signs removed; values are still on the 0–100 scale)" if had_pct else "") + (f"; {lost} unreadable values became missing" if lost else ""), c)
    obj_cols = [c for c in df.columns if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])]
    if "parse_dates" in steps:
        for c in obj_cols:
            if not wanted(c):
                continue
            s = df[c]
            if s.dropna().nunique() <= 2 and not DATE_NAME.search(str(c)):
                continue
            parsed = _parse_dates(s)
            if parsed is not None:
                lost = int(s.notna().sum() - parsed.notna().sum())
                has_time = bool((parsed.dropna().dt.normalize() != parsed.dropna()).any())
                df[c] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d").where(parsed.notna(), None)
                extra = " (day/month order was ambiguous; day first assumed)" if getattr(parsed, "attrs", {}).get("ambiguous_order") else ""
                note("parse_dates", int(parsed.notna().sum()), "text dates rewritten as ISO dates" + extra + (f"; {lost} unreadable values became missing" if lost else ""), c)
    ctx.progress(65, "labels")
    obj_cols = [c for c in df.columns if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])]
    if "unify_labels" in steps:
        for c in obj_cols:
            if not wanted(c):
                continue
            s = df[c]
            nn = s.dropna().astype(str)
            if nn.nunique() > 200 or nn.nunique() <= 1:
                continue
            key = nn.str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
            if key.nunique() == nn.nunique():
                continue
            canon = nn.groupby(key).agg(lambda v: v.value_counts().idxmax())
            mapped = key.map(canon)
            changed = int((mapped != nn).sum())
            if changed:
                df.loc[nn.index, c] = mapped
                note("unify_labels", changed, f"{nn.nunique() - key.nunique()} spelling variants merged into their most common form", c)
    # ---- personal data
    pii_mode = args.get("pii") or "keep"
    drop_cols = [c for c in (args.get("drop_columns") or [])]
    if "normalize_names" in steps:
        drop_cols = [normalize_name(c) for c in drop_cols]
    missing_cols = [c for c in drop_cols if c not in df.columns]
    if missing_cols:
        raise InvalidArguments(f"drop_columns not in the dataset: {missing_cols}")
    if drop_cols:
        df = df.drop(columns=drop_cols)
        note("drop_columns", len(drop_cols), ", ".join(drop_cols))
    pii_found: Dict[str, str] = {}
    if pii_mode != "keep":
        ctx.progress(72, "personal data")
        extra = [normalize_name(c) if "normalize_names" in steps else c for c in (args.get("pii_columns") or [])]
        bad = [c for c in extra if c not in df.columns]
        if bad:
            raise InvalidArguments(f"pii_columns not in the dataset: {bad}")
        for c in df.columns:
            kind = detect_pii(str(c), df[c])
            if kind:
                pii_found[str(c)] = kind
        for c in extra:
            pii_found.setdefault(c, "named by user")
        if pii_mode == "drop":
            cols = list(pii_found)
            if cols:
                df = df.drop(columns=cols)
                note("pii_drop", len(cols), "; ".join(f"{c} ({k.replace('_', ' ')})" for c, k in pii_found.items()))
        else:
            salt = _salt(ctx.project)
            for c, kind in pii_found.items():
                if kind in ("date_of_birth", "coordinates"):
                    # cannot be pseudonymised meaningfully; coarsen instead
                    if kind == "date_of_birth":
                        parsed = pd.to_datetime(df[c], errors="coerce")
                        if parsed.notna().mean() > 0.5:
                            df[c] = parsed.dt.year.astype("Int64")
                            note("pii_coarsen", int(parsed.notna().sum()), "date of birth reduced to birth year", c)
                            continue
                    num = pd.to_numeric(df[c], errors="coerce")
                    if num.notna().mean() > 0.5:
                        df[c] = num.round(1)
                        note("pii_coarsen", int(num.notna().sum()), "coordinates rounded to 0.1 degree (about 10 km)", c)
                        continue
                n = int(df[c].notna().sum())
                df[c] = pseudonymize(df[c], salt, prefix=re.sub(r"[^a-z]", "", kind)[:4] or "p")
                note("pii_pseudonymize", n, f"{kind.replace('_', ' ')} replaced by stable salted codes (same value → same code; not reversible without the project salt)", c)
            # free text: redact emails, phones, urls, long numbers
            for c in df.columns:
                if c in pii_found or not (pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])):
                    continue
                nn = df[c].dropna().astype(str)
                if len(nn) == 0 or nn.str.len().mean() < 20:
                    continue
                red, hits = redact_text(df[c])
                if hits:
                    df[c] = red
                    note("pii_redact_text", hits, "emails, phone numbers, URLs or long id numbers inside the text replaced by [email], [phone], [url], [id-number]", c)
    if fill == "median_mode":
        for c in df.columns:
            if not wanted(c):
                continue
            miss = int(df[c].isna().sum())
            if not miss or miss == len(df):
                continue
            if pd.api.types.is_numeric_dtype(df[c]):
                v = float(df[c].median())
                df[c] = df[c].fillna(v)
                note("fill_missing", miss, f"missing values filled with the median ({v:g}). This is an assumption; say so when reporting.", c)
            else:
                v = df[c].dropna().astype(str).value_counts().idxmax()
                df[c] = df[c].fillna(v)
                note("fill_missing", miss, f"missing values filled with the most common value ('{v}'). This is an assumption; say so when reporting.", c)
    ctx.progress(85, "writing")
    out = ctx.project.new_artifact_dir("clean", ds.name)
    stem = re.sub(r"\.[^.]+$", "", ds.name)
    output_name = args.get('output_name') or f"{stem}_clean"
    if '/' in output_name or '\\' in output_name or output_name in {'.','..'} or any(ord(c)<32 for c in output_name):
        raise InvalidArguments('Use a cleaned-data filename without a directory path')
    if output_name.lower().endswith('.csv'): output_name = output_name[:-4]
    path = out / (output_name + '.csv')
    df.to_csv(path, index=False)
    new = ctx.project.register_derived(path, path.name, int(df.shape[0]), int(df.shape[1]), ds.id, [str(c) for c in df.columns])
    ctx.project.write_json(out / "changes.json", {"from": ds.id, "to": new.id, "steps": steps, "fill_missing": fill, "pii": pii_mode, "pii_columns": pii_found, "log": log})
    cells_changed = sum(e["count"] for e in log if e["step"] not in ("drop_duplicates", "drop_empty_rows", "drop_empty_columns", "normalize_names", "drop_columns", "pii_drop"))
    summary = (f"Cleaned {ds.name}: {rows0:,} → {df.shape[0]:,} rows, {cols0} → {df.shape[1]} columns; "
               f"{len(log)} change{'s' if len(log) != 1 else ''} logged" + (f", {cells_changed:,} cells touched" if cells_changed else "") + ". The original file was not modified.")
    if not log:
        summary = f"{ds.name} needed no cleaning: nothing changed in {rows0:,} rows × {cols0} columns. A copy was still saved."
    if pii_found:
        summary += f" Personal data handled in {len(pii_found)} column{'s' if len(pii_found) != 1 else ''} ({pii_mode})."
    res = {"dataset_id": new.id, "from_dataset_id": ds.id, "pii_mode": pii_mode, "pii_columns": pii_found, "name": new.name, "path": str(path), "rows_before": rows0, "rows_after": int(df.shape[0]),
           "cols_before": cols0, "cols_after": int(df.shape[1]), "columns": [str(c) for c in df.columns], "changes": log,
           "summary_text": summary, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}
    ctx.project.audit({"tool": "clean_dataset", "dataset": ds.id, "new_dataset": new.id, "changes": len(log), "artifact": str(out)})
    ctx.progress(100, "done")
    return res
