"""export_replication_package: turn a journaled research task (or a host objective with its receipts) into a
folder another researcher can re-run.

The package carries the instruction, the inputs with hashes and row counts, the ordered tool calls, the
derived data and outputs the user already holds inside the project, a `reproduce.py` that replays the
successful calls through the worker's own registry, and a best-effort `reproduce.R`. Original source files
outside the project are referenced by path and sha256 only unless `copy_sources` is true; nothing outside
the project directory is ever copied without that flag. Everything is deterministic: no model is involved."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..registry import JobContext, tool, InvalidArguments, NETWORK_TOOLS, registry
from ..project import slug
from ..execution_cache import _output_files
from ..research import ResearchStore, RESEARCH_TOOLS
from ..objectives import ObjectiveStore, digest
from ..security import AccessDenied

SOFTWARE = ["pandas", "numpy", "scipy", "statsmodels", "pyfixest", "scikit-learn", "openpyxl", "pyarrow", "python-docx", "python-pptx", "matplotlib"]
# Journal/host bookkeeping, network retrieval and this exporter itself are not analysis steps.
NOT_REPRODUCIBLE = set(RESEARCH_TOOLS) | set(NETWORK_TOOLS) | {
    "export_replication_package", "read_research_objective", "propose_requirements", "review_objective",
    "research_capabilities", "list_research_skills", "read_research_skill", "learn_from_experience", "recall_lessons"}
LIMIT_KEYS = ("notes", "limits", "limitations", "warnings", "caveats", "assumptions")
DATASET_KEYS = ("dataset_id", "from_dataset_id", "left_dataset_id", "right_dataset_id", "baseline_dataset_id", "followup_dataset_id")
R_AGG = {"mean": "mean(x, na.rm = TRUE)", "median": "median(x, na.rm = TRUE)", "sum": "sum(x, na.rm = TRUE)", "min": "min(x, na.rm = TRUE)",
         "max": "max(x, na.rm = TRUE)", "std": "sd(x, na.rm = TRUE)", "count": "sum(!is.na(x))", "nunique": "length(unique(x[!is.na(x)]))"}
R_CLEAN = {
    "normalize_names": 'names({d}) <- tolower(gsub("^_+|_+$", "", gsub("[^0-9A-Za-z]+", "_", trimws(names({d})))))',
    "trim_whitespace": '{d}[] <- lapply({d}, function(x) if (is.character(x)) trimws(x) else x)',
    "standardize_missing": '{d}[] <- lapply({d}, function(x) {{ if (is.character(x)) x[tolower(trimws(x)) %in% c("", "na", "n/a", "n.a.", "nan", "null", "none", "nil", "-", "--", ".", "?", "missing", "unknown", "not available")] <- NA; x }})',
    "drop_empty_rows": '{d} <- {d}[rowSums(!is.na({d})) > 0, , drop = FALSE]',
    "drop_empty_columns": '{d} <- {d}[, colSums(!is.na({d})) > 0, drop = FALSE]',
    "drop_duplicates": '{d} <- {d}[!duplicated({d}), , drop = FALSE]',
    "coerce_numeric": '{d}[] <- lapply({d}, function(x) {{ if (!is.character(x)) return(x); v <- suppressWarnings(as.numeric(gsub("[,%$€£¥₹ ]", "", x))); if (sum(!is.na(x)) > 0 && mean(!is.na(v[!is.na(x)])) >= 0.95) v else x }})  # approximation of Squipi coerce_numeric',
}

_SCHEMA = {"type": "object", "properties": {
    "task_id": {"type": "string", "minLength": 1, "maxLength": 80, "description": "research task from the journal (read_research_task)"},
    "objective_id": {"type": "string", "minLength": 1, "maxLength": 100, "description": "host objective whose succeeded receipts form the steps; used when task_id is omitted"},
    "include_data": {"type": "boolean", "description": "copy derived datasets (cleaned/joined CSVs inside the project) into data/ (default true)"},
    "copy_sources": {"type": "boolean", "description": "also copy the user's original source files into sources/ (default false: they are referenced by path and sha256 only)"},
    "name": {"type": "string", "minLength": 1, "maxLength": 80, "description": "package folder name (default: from the instruction)"}},
    "required": [], "additionalProperties": False}


# ---------- small helpers

def _sha(path: Path) -> str:
    return digest(path)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _walk_strings(value: Any, key: str = "", out: Optional[List[Tuple[str, str]]] = None) -> List[Tuple[str, str]]:
    out = [] if out is None else out
    if isinstance(value, dict):
        for k, v in value.items():
            _walk_strings(v, str(k), out)
    elif isinstance(value, list):
        for v in value:
            _walk_strings(v, key, out)
    elif isinstance(value, str):
        out.append((key, value))
    return out


def _rewrite(value: Any, mapping: Dict[str, str]) -> Any:
    """Replace absolute paths with package-relative ones where the file was copied. Other strings are untouched."""
    if isinstance(value, dict):
        return {k: _rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite(v, mapping) for v in value]
    if isinstance(value, str) and value in mapping:
        return mapping[value]
    return value


def _scrub(text: str, mapping: Dict[str, str], roots: List[str]) -> str:
    """README text: copied files become package-relative, other known locations become placeholders."""
    for old in sorted(mapping, key=len, reverse=True):
        text = text.replace(old, mapping[old])
    for root, label in roots:
        text = text.replace(root, label)
    return text


def _one_line(result: Dict[str, Any]) -> str:
    text = result.get("summary_text") or result.get("interpretation") or result.get("message") or ""
    if isinstance(text, list):
        text = text[0] if text else ""
    if not text:
        keys = [k for k in ("rows", "n", "dataset_id", "path", "docx_path", "coef") if k in result]
        text = ", ".join(f"{k}={result[k]}" for k in keys) or "(no summary recorded)"
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text[:300] + ("…" if len(text) > 300 else "")


def _software() -> Dict[str, Any]:
    versions: Dict[str, Optional[str]] = {}
    for name in SOFTWARE:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    from .. import __version__
    return {"python": platform.python_version(), "platform": platform.platform(), "squipi_worker": __version__, "packages": versions}


def _snapshot_code(target: Path) -> str:
    """Copy the worker package source (no caches) so reproduce.py runs the same code, and hash it."""
    src = Path(__file__).resolve().parent.parent
    h = hashlib.sha256()
    for p in sorted(src.rglob("*")):
        if p.is_dir() or "__pycache__" in p.parts or p.suffix not in (".py", ".json", ".R"):
            continue
        rel = p.relative_to(src)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        h.update(str(rel).encode()); h.update(p.read_bytes())
    return h.hexdigest()


def _unique(folder: Path, name: str, hint: str) -> Path:
    cand = folder / name
    if cand.exists():
        cand = folder / f"{slug(hint, 20)}_{name}"
    i = 2
    while cand.exists():
        cand = folder / f"{slug(hint, 20)}_{i}_{name}"; i += 1
    return cand


# ---------- the journal slice

def _journal(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if args.get("task_id"):
        rec = ResearchStore(ctx.project.root).read_research_task(args["task_id"])
        task = rec["task"]
        steps = [{"sequence": s["sequence"], "tool": s["tool"], "arguments": s["arguments"], "result": s["result"],
                  "ok": bool(s["ok"]), "created_at": s["created_at"]} for s in rec["steps"]]
        return {"kind": "research_task", "id": task["task_id"], "instruction": task["instruction"], "inputs": task["inputs"],
                "status": task["status"], "summary": task["summary"], "created_at": task["created_at"], "finished_at": task["finished_at"],
                "plan": task["plan"], "steps": steps}
    if args.get("objective_id"):
        snap = ObjectiveStore(ctx.project).snapshot(args["objective_id"])
        steps = [{"sequence": i + 1, "tool": s["tool"], "arguments": s["arguments"], "result": s["result"],
                  "ok": s["state"] == "succeeded", "state": s["state"], "receipt_id": s["step_id"], "created_at": None}
                 for i, s in enumerate(snap["steps"])]
        return {"kind": "objective", "id": snap["objective_id"], "instruction": snap["instructions"][-1], "inputs": [i["id"] for i in snap["inputs"]],
                "status": snap["state"], "summary": "", "created_at": None, "finished_at": None, "plan": [], "steps": steps,
                "requirements": snap["requirements"]}
    raise InvalidArguments("give task_id (a journaled research task) or objective_id")


# ---------- R emission (best effort)

def _rs(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rvec(items: List[Any]) -> str:
    parts = []
    for v in items:
        if isinstance(v, bool):
            parts.append("TRUE" if v else "FALSE")
        elif isinstance(v, (int, float)):
            parts.append(repr(v))
        else:
            parts.append(_rs(v))
    return "c(" + ", ".join(parts) + ")"


def _rname(name: str) -> str:
    return "`" + str(name).replace("`", "") + "`"


class _RWriter:
    def __init__(self, datasets: Dict[str, Dict[str, Any]]):
        self.datasets = datasets
        self.vars: Dict[str, str] = {}
        self.lines: List[str] = []
        self.covered: List[int] = []
        self.partial: List[Tuple[int, str]] = []
        self.skipped: List[Tuple[int, str]] = []

    def var(self, dataset_id: Optional[str]) -> str:
        if not dataset_id:
            return "d"
        if dataset_id not in self.vars:
            self.vars[dataset_id] = f"d{len(self.vars) + 1}"
        return self.vars[dataset_id]

    def _binary(self, d: str, column: str, levels: Optional[Dict[str, Any]], out: str) -> str:
        if levels and levels.get("1") is not None:
            return f'{d}${out} <- as.integer(as.character({d}[[{_rs(column)}]]) == {_rs(levels["1"])})'
        return f'{d}${out} <- as.integer({d}[[{_rs(column)}]] %in% c(1, "1", TRUE, "True", "true", "yes", "Yes"))'

    def step(self, step: Dict[str, Any]) -> None:
        tool_name, a, r, n = step["tool"], step["arguments"] or {}, step["result"] or {}, step["sequence"]
        fn = getattr(self, "t_" + tool_name, None)
        self.lines.append("")
        if fn is None:
            self.lines.append(f"# Step {n}: {tool_name} — no R equivalent emitted; run reproduce.py (tool: {tool_name})")
            self.skipped.append((n, tool_name))
            return
        self.lines.append(f"# Step {n}: {tool_name}")
        fn(a, r, n)

    # -- tools
    def t_import_dataset(self, a, r, n):
        ds_id = r.get("dataset_id")
        entry = self.datasets.get(ds_id) or {}
        v = self.var(ds_id)
        rel = entry.get("package_path")
        fmt = entry.get("format") or ("excel" if str(a.get("path", "")).lower().endswith((".xlsx", ".xlsm", ".xls")) else "csv")
        if rel:
            path = _rs(rel)
        else:
            path = _rs(Path(str(a.get("path", "source"))).name)
            self.lines.append("# This source file is not included in the package (see README, 'Original sources'); point `path` at your copy.")
        if fmt == "csv":
            self.lines.append(f'{v} <- read.csv({path}, check.names = FALSE, stringsAsFactors = FALSE, na.strings = c("", "NA"))')
            self.covered.append(n)
        elif fmt == "excel":
            sheet = f", sheet = {_rs(a['sheet'])}" if a.get("sheet") else ""
            self.lines.append(f'{v} <- as.data.frame(readxl::read_excel({path}{sheet}))  # readxl is a CRAN package, not base R')
            self.covered.append(n)
        else:
            self.lines.append(f'{v} <- as.data.frame(arrow::read_parquet({path}))  # arrow is a CRAN package, not base R')
            self.partial.append((n, "parquet needs the arrow package"))

    def t_clean_dataset(self, a, r, n):
        src, out = self.var(a.get("dataset_id")), self.var(r.get("dataset_id"))
        self.lines.append(f"{out} <- {src}")
        steps = a["steps"] if "steps" in a else list(R_CLEAN) + ["parse_dates", "unify_labels"]
        missing = []
        for s in steps:
            if s in R_CLEAN:
                self.lines.append(R_CLEAN[s].format(d=out))
            else:
                missing.append(s)
        if missing:
            self.lines.append(f"# {', '.join(missing)}: no exact base-R equivalent; the Python run's values come from reproduce.py")
        drop = list(a.get("drop_columns") or [])
        if drop:
            self.lines.append(f"{out} <- {out}[, setdiff(names({out}), {_rvec(drop)}), drop = FALSE]  # drop_columns")
        pii = r.get("pii_columns") or {}
        if a.get("pii") == "drop" and pii:
            self.lines.append(f"{out} <- {out}[, setdiff(names({out}), {_rvec(list(pii))}), drop = FALSE]  # columns Squipi flagged as personal data in the recorded run")
        elif a.get("pii") == "pseudonymize":
            missing.append("pii pseudonymize (salted hashes)")
            self.lines.append("# pii='pseudonymize' uses a project salt; it cannot be reproduced in R — run reproduce.py")
        if a.get("fill_missing") == "median_mode":
            self.lines.append(f"for (col in names({out})) {{ x <- {out}[[col]]; if (all(is.na(x))) next; if (is.numeric(x)) x[is.na(x)] <- median(x, na.rm = TRUE) else x[is.na(x)] <- names(sort(table(x), decreasing = TRUE))[1]; {out}[[col]] <- x }}  # fill_missing median/mode")
        name = Path(str(r.get("name") or "cleaned.csv")).stem
        self.lines.append(f'write.csv({out}, file.path("outputs_r", {_rs(name + ".csv")}), row.names = FALSE)')
        if missing:
            self.partial.append((n, "; ".join(missing)))
        else:
            self.covered.append(n)

    def t_aggregate(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        keys = list(a.get("group_by") or [])
        parts = []
        for i, m in enumerate(a["metrics"], 1):
            fn = R_AGG.get(m["agg"], "mean(x, na.rm = TRUE)")
            label = f"{m['agg']}_{m['column']}"
            if keys:
                self.lines.append(f"a{n}_{i} <- aggregate({d}[[{_rs(m['column'])}]], by = {d}[{_rvec(keys)}], FUN = function(x) {fn}); names(a{n}_{i})[ncol(a{n}_{i})] <- {_rs(label)}")
                parts.append(f"a{n}_{i}")
            else:
                parts.append(f"{_rname(label)} = (function(x) {fn})({d}[[{_rs(m['column'])}]])")
        if keys:
            self.lines.append(f"agg{n} <- Reduce(function(x, y) merge(x, y, by = {_rvec(keys)}, all = TRUE), list({', '.join(parts)}))  # note: R drops groups whose key is NA; Squipi keeps them as '(missing)'")
            sort_by = a.get("sort_by") or f"{a['metrics'][0]['agg']}_{a['metrics'][0]['column']}"
            self.lines.append(f"agg{n} <- agg{n}[order(-agg{n}[[{_rs(sort_by)}]]), , drop = FALSE]")
        else:
            self.lines.append(f"agg{n} <- data.frame({', '.join(parts)}, check.names = FALSE)")
        self.lines.append(f'write.csv(agg{n}, file.path("outputs_r", {_rs(f"aggregate_step{n}.csv")}), row.names = FALSE)')
        self.covered.append(n)

    def _robust_note(self, fit: str, cluster: Optional[str]) -> None:
        if cluster:
            self.lines.append(f"# Cluster-robust SEs as in the recorded run (CRAN packages sandwich + lmtest): lmtest::coeftest({fit}, vcov = sandwich::vcovCL({fit}, cluster = ~{_rname(cluster)}))")
        else:
            self.lines.append(f"# HC1 robust SEs as in the recorded run (CRAN packages sandwich + lmtest): lmtest::coeftest({fit}, vcov = sandwich::vcovHC({fit}, type = \"HC1\"))")

    def t_regress(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        rhs = " + ".join(_rname(p) for p in a["predictors"])
        if a.get("standardize"):
            self.lines.append(f"for (col in {_rvec(a['predictors'])}) if (is.numeric({d}[[col]])) {d}[[col]] <- as.numeric(scale({d}[[col]]))  # standardize = TRUE")
        self.lines.append(f"fit{n} <- lm({_rname(a['outcome'])} ~ {rhs}, data = {d})  # categorical predictors: R uses the first level as reference, Squipi the most common level")
        self.lines.append(f"summary(fit{n})")
        self._robust_note(f"fit{n}", a.get("cluster"))
        self.covered.append(n)

    def t_estimate_treatment_effect(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        self.lines.append(self._binary(d, a["treatment"], r.get("treatment_levels"), f"treated{n}"))
        self.lines.append(f"tapply({d}[[{_rs(a['outcome'])}]], {d}$treated{n}, function(x) c(n = sum(!is.na(x)), mean = mean(x, na.rm = TRUE), sd = sd(x, na.rm = TRUE)))  # arms")
        rhs = [f"treated{n}"] + [_rname(c) for c in (a.get("covariates") or [])] + ([_rname(a["baseline_outcome"])] if a.get("baseline_outcome") else [])
        self.lines.append(f"fit{n} <- lm({_rname(a['outcome'])} ~ {' + '.join(rhs)}, data = {d})  # coefficient on treated{n} = {'adjusted ' if len(rhs) > 1 else ''}difference in means")
        self.lines.append(f"summary(fit{n})")
        self._robust_note(f"fit{n}", a.get("cluster"))
        if a.get("balance_columns") or a.get("covariates"):
            cols = a.get("balance_columns") or a.get("covariates")
            self.lines.append(f"# Balance (standardized differences): sapply({d}[{_rvec(cols)}], function(x) {{ m <- tapply(x, {d}$treated{n}, mean, na.rm = TRUE); s <- tapply(x, {d}$treated{n}, var, na.rm = TRUE); (m[['1']] - m[['0']]) / sqrt((s[['1']] + s[['0']]) / 2) }})")
        self.covered.append(n)

    def t_difference_in_differences(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        self.lines.append(self._binary(d, a["treatment"], r.get("treatment_levels"), f"treated{n}"))
        if a.get("period"):
            self.lines.append(self._binary(d, a["period"], r.get("period_levels"), f"post{n}"))
        elif a.get("time") and a.get("post_periods"):
            self.lines.append(f"{d}$post{n} <- as.integer({d}[[{_rs(a['time'])}]] %in% {_rvec(a['post_periods'])})")
        elif a.get("time") and a.get("treatment_time") is not None:
            tt = a["treatment_time"]
            ref = f"{d}[[{_rs(tt)}]]" if isinstance(tt, str) else repr(tt)
            self.lines.append(f"{d}$post{n} <- as.integer({d}[[{_rs(a['time'])}]] >= {ref})")
        else:
            self.lines.append(f"# post indicator could not be derived from the recorded arguments; run reproduce.py")
            self.skipped.append((n, "difference_in_differences (no post indicator)"))
            return
        rhs = [f"treated{n} * post{n}"] + [_rname(c) for c in (a.get("covariates") or [])]
        self.lines.append(f"fit{n} <- lm({_rname(a['outcome'])} ~ {' + '.join(rhs)}, data = {d})  # the treated{n}:post{n} coefficient is the difference-in-differences estimate")
        self.lines.append(f"summary(fit{n})")
        self._robust_note(f"fit{n}", a.get("cluster") or a.get("unit"))
        if a.get("time") and (a.get("treatment_time") is not None):
            self.lines.append("# The event study (unit and time fixed effects) is not emitted; run reproduce.py or fit it with fixest::feols")
            self.partial.append((n, "event study not emitted"))
        else:
            self.covered.append(n)

    def t_compare_groups(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        y, g = _rs(a["outcome"]), _rs(a["group"])
        sub = f"{d}[{d}[[{g}]] %in% {_rvec(a['levels'])}, ]" if a.get("levels") else d
        self.lines.append(f"cg{n} <- {sub}")
        self.lines.append(f"tapply(cg{n}[[{y}]], cg{n}[[{g}]], function(x) c(n = sum(!is.na(x)), mean = mean(x, na.rm = TRUE), sd = sd(x, na.rm = TRUE), median = median(x, na.rm = TRUE)))")
        self.lines.append(f"if (length(unique(na.omit(cg{n}[[{g}]]))) == 2) {{ print(t.test(cg{n}[[{y}]] ~ cg{n}[[{g}]])); print(wilcox.test(cg{n}[[{y}]] ~ cg{n}[[{g}]])) }} else {{ print(oneway.test(cg{n}[[{y}]] ~ cg{n}[[{g}]])); print(kruskal.test(cg{n}[[{y}]] ~ cg{n}[[{g}]])) }}")
        self.covered.append(n)

    def t_correlate(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        method = a.get("method") or "pearson"
        cols = f"{d}[{_rvec(a['columns'])}]" if a.get("columns") else f"{d}[sapply({d}, is.numeric)]"
        self.lines.append(f"cor{n} <- cor(as.data.frame(lapply({cols}, as.numeric)), method = {_rs(method)}, use = \"pairwise.complete.obs\")")
        self.lines.append(f'write.csv(cor{n}, file.path("outputs_r", {_rs(f"correlations_step{n}.csv")}))')
        self.covered.append(n)

    def t_export_excel(self, a, r, n):
        d = self.var(a.get("dataset_id"))
        name = Path(str(a.get("filename") or r.get("path") or "export.xlsx")).stem
        self.lines.append(f'write.csv({d}, file.path("outputs_r", {_rs(name + ".csv")}), row.names = FALSE)  # the recorded run wrote an .xlsx; in R: writexl::write_xlsx({d}, file.path("outputs_r", {_rs(name + ".xlsx")}))')
        self.covered.append(n)


def _r_script(journal: Dict[str, Any], datasets: Dict[str, Dict[str, Any]], steps: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    w = _RWriter(datasets)
    for s in steps:
        if s["ok"] and s.get("reproducible", True):
            w.step(s)
    head = [
        "# reproduce.R — best-effort R translation of the recorded Squipi steps.",
        f"# Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} from {journal['kind']} {journal['id']}. Run from the package folder: Rscript reproduce.R",
        "# Cleaning (drop columns, missing tokens, duplicates), aggregates and OLS/treatment-effect/difference-in-differences",
        "# regressions use base R and stats. Steps without an R equivalent say 'run reproduce.py'. Robust standard errors need",
        "# the CRAN packages sandwich and lmtest (commented lines). Results may differ in small ways (missing-group handling,",
        "# reference levels, numeric coercion thresholds); reproduce.py is the exact replay.",
        'dir.create("outputs_r", showWarnings = FALSE)']
    coverage = {"covered_steps": sorted(set(x if isinstance(x, int) else x[0] for x in w.covered)),
                "partial_steps": [{"sequence": n, "gap": g} for n, g in w.partial],
                "not_in_r": [{"sequence": n, "tool": t} for n, t in w.skipped]}
    return "\n".join(head + w.lines) + "\n", coverage


# ---------- reproduce.py (a fixed template; it contains no paths)

REPRODUCE_PY = '''#!/usr/bin/env python3
"""Replay the recorded Squipi worker tool calls in order, through the worker's own typed registry.

Run from the package folder with Squipi's research-env python:  python reproduce.py
It uses the worker code snapshot in code/ (falls back to an importable squipi_worker), reads steps.json
and manifest.json, maps the recorded dataset ids to the packaged data files, and writes everything it
produces under run/ (run/project/artifacts holds the regenerated outputs; run/results.json is the log).
Exit code 0 means every reproducible recorded step ran again without error.
"""
import copy
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if (HERE / "code" / "squipi_worker").is_dir():
    sys.path.insert(0, str(HERE / "code"))
try:
    from squipi_worker import tools  # noqa: F401  (importing registers every tool)
    from squipi_worker.registry import registry, JobContext
    from squipi_worker.project import Project
    from squipi_worker.security import AllowList
except ImportError as exc:  # pragma: no cover
    sys.exit("The Squipi worker package is not importable (%s). Use Squipi's research-env python or set PYTHONPATH to the worker folder." % exc)

ID_KEYS = ("dataset_id", "analysis_id", "document_id", "draft_id", "snapshot_id")


def rewrite(value, mapping):
    """Recorded ids and paths become the ones produced in this run."""
    if isinstance(value, dict):
        return {k: rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v, mapping) for v in value]
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        for old, new in mapping.items():
            if old and "/" in old and value.startswith(old + "/"):
                return new + value[len(old):]
    return value


def learn(old, new, mapping):
    """Pair a recorded result with the fresh one to map ids, paths and folders."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key, v in old.items():
            w = new.get(key)
            if isinstance(v, str) and isinstance(w, str) and v and v != w and (key in ID_KEYS or key.endswith("_id") or key == "path" or key.endswith("_path") or key.endswith("_dir")):
                mapping[v] = w
            elif isinstance(v, (dict, list)):
                learn(v, w, mapping)
    elif isinstance(old, list) and isinstance(new, list):
        for a, b in zip(old, new):
            learn(a, b, mapping)


def dataset_ids(value, found=None):
    found = [] if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str) and (k == "dataset_id" or k.endswith("_dataset_id")):
                found.append(v)
            elif k == "dataset_ids" and isinstance(v, list):
                found.extend(x for x in v if isinstance(x, str))
            else:
                dataset_ids(v, found)
    elif isinstance(value, list):
        for v in value:
            dataset_ids(v, found)
    return found


def main():
    manifest = json.loads((HERE / "manifest.json").read_text("utf-8"))
    steps = json.loads((HERE / "steps.json").read_text("utf-8"))["steps"]
    datasets = manifest.get("datasets", {})
    run = HERE / "run"
    if run.exists():
        shutil.rmtree(run)
    allow = AllowList()
    allow.allow(HERE)
    project = Project(run / "project", allow)
    ctx = JobContext(project, lambda pct, note: None, lambda: False)
    mapping, log, failures = {}, [], 0

    def candidates(ds_id, recorded_path=None):
        entry = datasets.get(ds_id) or {}
        out = []
        if entry.get("package_path"):
            out.append(HERE / entry["package_path"])
        if entry.get("original_path"):
            out.append(Path(entry["original_path"]))
        if recorded_path:
            out.append(HERE / recorded_path if not Path(recorded_path).is_absolute() else Path(recorded_path))
        return [p for p in out if p.is_file()]

    def ensure_dataset(ds_id):
        """Import the packaged copy of a recorded dataset when the step that produced it did not run here."""
        if ds_id in mapping:
            return True
        for p in candidates(ds_id):
            allow.allow(p)
            fresh = registry.call("import_dataset", {"path": str(p)}, ctx)
            mapping[ds_id] = fresh["dataset_id"]
            log.append({"tool": "import_dataset", "ok": True, "note": "imported packaged copy %s for recorded dataset %s" % (p.name, ds_id)})
            return True
        return False

    for step in steps:
        if not step.get("ok") or not step.get("reproducible", True):
            continue
        args = copy.deepcopy(step.get("arguments") or {})
        entry = {"sequence": step["sequence"], "tool": step["tool"]}
        try:
            if step["tool"] == "import_dataset":
                found = candidates((step.get("result") or {}).get("dataset_id"), args.get("path"))
                if not found:
                    raise FileNotFoundError("source file is not in the package and was not found at its recorded location")
                allow.allow(found[0])
                args["path"] = str(found[0])
            else:
                for ds_id in dataset_ids(args):
                    if not ensure_dataset(ds_id):
                        raise FileNotFoundError("recorded dataset %s is not available in the package" % ds_id)
                args = rewrite(args, mapping)
            result = registry.call(step["tool"], args, ctx)
            learn(step.get("result") or {}, result, mapping)
            entry.update({"ok": True, "summary": result.get("summary_text", "")[:300] if isinstance(result.get("summary_text"), str) else "",
                          "outputs": [v for k, v in result.items() if isinstance(v, str) and (k == "path" or k.endswith("_path")) and Path(v).is_file()]})
        except Exception as exc:  # noqa: BLE001 - report every failure and keep going
            failures += 1
            entry.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
        log.append(entry)
        print("step %s %s: %s" % (entry.get("sequence", "-"), entry["tool"], "ok" if entry.get("ok") else entry.get("error")))
    files = sorted(str(p.relative_to(run)) for p in run.rglob("*") if p.is_file())
    (run / "results.json").write_text(json.dumps({"steps": log, "failures": failures, "id_map": mapping, "files": files}, indent=2, default=str), "utf-8")
    print("%d step(s) replayed, %d failed; outputs under %s" % (sum(1 for e in log if "sequence" in e), failures, run))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------- README

def _readme(journal: Dict[str, Any], steps: List[Dict[str, Any]], datasets: Dict[str, Dict[str, Any]], originals: List[Dict[str, Any]],
            outputs: List[Dict[str, Any]], missing: List[Dict[str, Any]], software: Dict[str, Any], limitations: List[Dict[str, Any]],
            mapping: Dict[str, str], roots: List[Tuple[str, str]], r_cov: Dict[str, Any], title: str) -> str:
    L: List[str] = [f"# Replication package: {title}", "",
                    f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by Squipi (`export_replication_package`) from {journal['kind'].replace('_', ' ')} `{journal['id']}`"
                    + (f" (status: {journal['status']})" if journal.get("status") else "") + ".", "",
                    "## What was asked", ""]
    L += ["> " + line for line in journal["instruction"].strip().splitlines()] or ["> (no instruction text)"]
    if journal.get("summary"):
        L += ["", "Recorded outcome: " + journal["summary"].strip()]
    L += ["", "## Inputs", "", "| Dataset | Name | Role | In package | Rows | Columns | SHA-256 |", "|---|---|---|---|---|---|---|"]
    for ds_id, d in datasets.items():
        L.append(f"| {ds_id} | {d['name']} | {d['role']} | {d['package_path'] or '— (referenced only)'} | {d['rows'] if d['rows'] is not None else '?'} | {d['cols'] if d['cols'] is not None else '?'} | {d['sha256'] or 'unavailable'} |")
    if not datasets:
        L.append("| — | no datasets were referenced by the recorded steps | | | | | |")
    L += ["", "## Steps", "", "Successful steps are replayed by `reproduce.py` in this order. Failed steps are kept for the record and skipped."]
    for s in steps:
        flag = "" if s["ok"] else " — FAILED in the recorded run"
        if s["ok"] and not s.get("reproducible", True):
            flag = " — bookkeeping/retrieval step, not replayed"
        args = _scrub(json.dumps(s["arguments"], ensure_ascii=False, separators=(",", ":")), mapping, roots)
        if len(args) > 600:
            args = args[:600] + "…"
        L += ["", f"{s['sequence']}. `{s['tool']}`{flag}", f"   - arguments: `{args}`", f"   - result: {_scrub(_one_line(s['result']), mapping, roots)}"]
    L += ["", "## Outputs", ""]
    for o in outputs:
        L.append(f"- `{o['package_path']}` (step {o['sequence']}, {o['tool']})")
    if not outputs:
        L.append("- no output files were found on disk for the successful steps")
    if missing:
        L += ["", "Recorded outputs that were no longer on disk and could not be copied:"]
        L += [f"- {Path(m['original_path']).name} (step {m['sequence']}, {m['tool']})" for m in missing]
    L += ["", "## How to reproduce", "",
          "- Python (exact replay): from this folder run `python reproduce.py` with Squipi's research-env python. It uses the worker code snapshot in `code/`, "
          "re-runs every successful step through the worker's typed registry, and writes regenerated outputs under `run/`.",
          "- R (best effort): `Rscript reproduce.R`. " + ("Steps " + ", ".join(map(str, r_cov["covered_steps"])) + " have base-R equivalents" if r_cov["covered_steps"] else "No step has a base-R equivalent")
          + ("; partial: " + ", ".join("step %s (%s)" % (p["sequence"], p["gap"]) for p in r_cov["partial_steps"]) if r_cov["partial_steps"] else "")
          + ("; not in R: " + ", ".join("step %s (%s)" % (p["sequence"], p["tool"]) for p in r_cov["not_in_r"]) if r_cov["not_in_r"] else "") + ".",
          "- `steps.json` is the journal slice (tool, arguments, result, ok); `manifest.json` lists every file with its SHA-256 and the recorded source locations.",
          "", "## Software", "", f"- Python {software['python']} on {software['platform']}", f"- squipi_worker {software['squipi_worker']} (source snapshot in `code/`, sha256 {software.get('worker_code_sha256', '')[:16]}…)"]
    L += [f"- {name} {version}" if version else f"- {name}: not installed" for name, version in software["packages"].items()]
    L += ["", "## Limitations recorded in results", ""]
    L += [f"- step {l['sequence']} ({l['tool']}): {l['text']}" for l in limitations] or ["- none recorded by the tools that ran"]
    L += ["", "## Original sources", "",
          "Files the user gave Squipi that live outside the project directory. They are referenced here by location and SHA-256; "
          "they were copied into `sources/` only if the package was built with `copy_sources`."]
    L += [f"- {o['dataset_id']}: `{o['path']}` — sha256 {o['sha256'] or 'unavailable'}" + (f" — copied to `{o['package_path']}`" if o.get("package_path") else "") for o in originals] or ["- none"]
    return "\n".join(L) + "\n"


# ---------- the tool

@tool("export_replication_package",
      "Build a replication package for a journaled research task (task_id) or a host objective (objective_id): README with inputs (sha256, rows), "
      "ordered steps, outputs, software versions and recorded limitations; steps.json; reproduce.py (exact replay through the worker registry); "
      "reproduce.R (best effort); copies of derived data and outputs; manifest.json with hashes; and a zip. Original sources outside the project are "
      "referenced by path and hash only unless copy_sources is true. Deterministic; no model involved.",
      _SCHEMA)
def export_replication_package(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    project = ctx.project
    include_data = args.get("include_data", True)
    copy_sources = bool(args.get("copy_sources", False))
    journal = _journal(ctx, args)
    title = (args.get("name") or re.sub(r"\s+", " ", journal["instruction"]).strip()[:60] or "replication")
    pkg_name = slug(args.get("name") or title, 40) or "replication"
    out = project.new_artifact_dir("replication", pkg_name)
    pkg = out / pkg_name
    for sub in ("data", "sources", "outputs", "code"):
        (pkg / sub).mkdir(parents=True, exist_ok=True)
    notes: List[str] = []
    ctx.progress(5, "reading the journal")

    steps = journal["steps"]
    for s in steps:
        spec = registry.get(s["tool"])
        s["reproducible"] = bool(spec) and not spec.needs_model and s["tool"] not in NOT_REPRODUCIBLE
        if s["ok"] and spec is None:
            notes.append(f"step {s['sequence']}: tool '{s['tool']}' is not registered in this worker; it cannot be replayed")

    # -- datasets: derived copies inside the project, originals referenced (copied only on request)
    mapping: Dict[str, str] = {}
    datasets: Dict[str, Dict[str, Any]] = {}
    originals: List[Dict[str, Any]] = []
    ids: List[str] = list(journal.get("inputs") or [])
    for s in steps:
        for key, value in _walk_strings(s["arguments"]) + _walk_strings(s["result"]):
            if key in DATASET_KEYS or key == "dataset_ids":
                ids.append(value)
    ordered: List[str] = []
    for i in ids:
        try:
            for d in project.dataset_lineage(i):
                if d.id not in ordered:
                    ordered.append(d.id)
        except (KeyError, ValueError):
            if i.startswith("ds_") and i not in ordered:
                notes.append(f"dataset {i} is no longer registered in the project; it is listed without a file")
    ctx.progress(15, "copying data")
    for ds_id in ordered:
        d = project.dataset(ds_id)
        path = Path(d.path)
        derived = _within(path, project.root)
        entry: Dict[str, Any] = {"name": d.name, "role": "derived in project" if derived else "original source", "format": d.format, "sheet": d.sheet,
                                 "rows": d.rows, "cols": d.cols, "derived_from": d.derived_from, "package_path": None, "original_path": None, "sha256": None}
        try:
            readable = project.allow.check_readable(path)
            entry["sha256"] = _sha(readable)
            if entry["rows"] is None and d.format == "csv":
                try:
                    import pandas as pd
                    entry["rows"] = int(len(pd.read_csv(readable, low_memory=False)))
                except Exception:  # noqa: BLE001 - counts are advisory
                    pass
        except (AccessDenied, OSError) as e:
            notes.append(f"dataset {ds_id} ({d.name}) could not be read for hashing: {type(e).__name__}")
            readable = None
        if readable is not None and ((derived and include_data) or (not derived and copy_sources)):
            folder = "data" if derived else "sources"
            dest = _unique(pkg / folder, Path(d.name).name if Path(d.name).suffix else path.name, ds_id)
            shutil.copyfile(readable, dest)
            rel = f"{folder}/{dest.name}"
            entry["package_path"] = rel
            mapping[str(path)] = rel
            mapping[str(readable)] = rel
        if not derived:
            entry["original_path"] = str(path)
            originals.append({"dataset_id": ds_id, "path": str(path), "sha256": entry["sha256"], "package_path": entry["package_path"]})
        datasets[ds_id] = entry

    # -- outputs from successful steps
    ctx.progress(35, "copying outputs")
    outputs: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    for s in steps:
        if not s["ok"]:
            continue
        present = set(_output_files(s["result"]))
        for key, value in _walk_strings(s["result"]):
            if not (key == "path" or key.endswith("_path")) or not value.startswith("/"):
                continue
            if value in mapping:
                continue
            if value in present:
                src = Path(value)
                if not _within(src, project.root):
                    notes.append(f"step {s['sequence']}: output {src.name} lies outside the project and was not copied")
                    continue
                dest = _unique(pkg / "outputs", src.name, f"step{s['sequence']}")
                shutil.copyfile(src, dest)
                rel = f"outputs/{dest.name}"
                mapping[value] = rel
                outputs.append({"sequence": s["sequence"], "tool": s["tool"], "package_path": rel, "original_path": value, "sha256": _sha(dest)})
            elif not Path(value).exists() and value not in {m["original_path"] for m in missing}:
                missing.append({"sequence": s["sequence"], "tool": s["tool"], "original_path": value})
    for m in missing:
        notes.append(f"step {m['sequence']} ({m['tool']}): recorded output {Path(m['original_path']).name} is no longer on disk")

    # -- limitations recorded by the tools
    limitations: List[Dict[str, Any]] = []
    for s in steps:
        if not s["ok"]:
            continue
        for key in LIMIT_KEYS + ("interpretation",):
            value = s["result"].get(key)
            items = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
            if key == "interpretation":
                items = items[1:]   # the first line is the headline number; the rest are the template caveats (design, balance, small arms)
            for item in items:
                if isinstance(item, str) and item.strip():
                    limitations.append({"sequence": s["sequence"], "tool": s["tool"], "text": re.sub(r"\s+", " ", item.strip())[:400]})

    # -- files
    ctx.progress(50, "writing scripts")
    code_sha = _snapshot_code(pkg / "code" / "squipi_worker")
    software = _software(); software["worker_code_sha256"] = code_sha
    packaged_steps = [{**s, "arguments": _rewrite(s["arguments"], mapping), "result": _rewrite(s["result"], mapping)} for s in steps]
    r_text, r_cov = _r_script(journal, datasets, packaged_steps)
    project.write_json(pkg / "steps.json", {"source": {"kind": journal["kind"], "id": journal["id"], "status": journal.get("status")},
                                            "instruction": journal["instruction"], "inputs": journal.get("inputs"), "plan": journal.get("plan"),
                                            "steps": packaged_steps, "path_rewrites": mapping,
                                            "note": "Arguments and results are the recorded journal values; absolute paths were rewritten to package-relative ones where the file was copied."})
    project.write_text(pkg / "reproduce.py", REPRODUCE_PY)
    project.write_text(pkg / "reproduce.R", r_text)
    public = dict(mapping)
    for o in originals:
        public.setdefault(o["path"], f"<original source {o['dataset_id']}: {Path(o['path']).name}>")
    roots = [(str(project.root), "<project>"), (str(Path.home()), "~")]
    project.write_text(pkg / "README.md", _readme(journal, steps, datasets, originals, outputs, missing, software, limitations, public, roots, r_cov, title))
    if r_cov["not_in_r"]:
        notes.append("not reproduced in R: " + ", ".join(f"step {p['sequence']} ({p['tool']})" for p in r_cov["not_in_r"]))
    if r_cov["partial_steps"]:
        notes.append("partially reproduced in R: " + ", ".join(f"step {p['sequence']} ({p['gap']})" for p in r_cov["partial_steps"]))

    ctx.progress(75, "hashing")
    files: Dict[str, Dict[str, Any]] = {}
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            rel = str(p.relative_to(pkg))
            files[rel] = {"sha256": _sha(p), "bytes": p.stat().st_size}
    manifest = {"package": pkg_name, "title": title, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": {"kind": journal["kind"], "id": journal["id"], "status": journal.get("status")},
                "instruction": journal["instruction"], "software": software, "worker_code_sha256": code_sha,
                "datasets": datasets, "original_sources": originals, "outputs": outputs, "outputs_missing": missing,
                "r_coverage": r_cov, "notes": notes, "files": files}
    manifest_path = project.write_json(pkg / "manifest.json", manifest)
    ctx.progress(90, "zipping")
    zip_path = out / f"{pkg_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                z.write(p, str(p.relative_to(out)))
    replayed = sum(1 for s in steps if s["ok"] and s["reproducible"])
    project.audit({"tool": "export_replication_package", "source": journal["id"], "artifact": str(out)})
    ctx.progress(100, "done")
    return {"package_dir": str(pkg), "zip_path": str(zip_path), "readme_path": str(pkg / "README.md"), "steps_path": str(pkg / "steps.json"),
            "reproduce_py_path": str(pkg / "reproduce.py"), "reproduce_r_path": str(pkg / "reproduce.R"), "manifest_path": str(manifest_path),
            "source": {"kind": journal["kind"], "id": journal["id"]}, "file_count": len(files), "step_count": len(steps), "reproducible_step_count": replayed,
            "r_coverage": r_cov, "datasets": {k: {"role": v["role"], "package_path": v["package_path"], "sha256": v["sha256"], "rows": v["rows"]} for k, v in datasets.items()},
            "datasets_copied": sum(1 for v in datasets.values() if v["package_path"]), "sources_referenced_only": sum(1 for o in originals if not o["package_path"]),
            "outputs_copied": len(outputs), "outputs_missing": [m["original_path"] for m in missing], "limitations_recorded": len(limitations), "notes": notes,
            "summary_text": f"Replication package '{pkg_name}' with {len(steps)} recorded steps ({replayed} replayable), {sum(1 for v in datasets.values() if v['package_path'])} data file(s), "
                            f"{len(outputs)} output(s) and {len(files)} files in all; reproduce.py replays the run, reproduce.R covers {len(r_cov['covered_steps'])} step(s). Original sources are referenced by path and hash"
                            + (", and copied" if copy_sources else ", not copied") + ".",
            "elapsed_s": round(time.time() - t0, 2)}
