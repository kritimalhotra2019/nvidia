"""Impact evaluation: estimate_treatment_effect, difference_in_differences, attrition_check, lee_bounds.

Everything is computed by numpy/scipy (and pyfixest when it imports) and returned with sample sizes and
uncertainty. The wording in `interpretation` is a fixed template filled with the computed numbers; whether
a difference may be read as an effect depends on the `design` the caller declares, never on the model."""
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .. import svg
from .datasets import detect_pii
from .stats import _load, _col, _design, _save_table, _office_png

SMALL_ARM = 30
MDE_MULTIPLIER = 2.8   # (z_0.975 + z_0.80): the smallest true effect this design would detect 80% of the time


def _precision(estimate: Optional[float], ci_low: Optional[float], ci_high: Optional[float], se: Optional[float],
               n_per_arm: Dict[str, int], unit: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Says in words when an interval is wider than the estimate it surrounds, with the approximate minimum
    detectable effect at this sample size and a pointer to plan_research_power. Returns (fields, note)."""
    if estimate is None or ci_low is None or ci_high is None or se is None or not np.isfinite([estimate, ci_low, ci_high, se]).all():
        return None, None
    width = float(ci_high) - float(ci_low)
    mde = MDE_MULTIPLIER * float(se)
    fields = {"ci_width": _r4(width), "ci_width_to_estimate": (_r4(width / abs(estimate)) if estimate else None), "mde_80pct_power": _r4(mde),
              "n_per_arm": {k: int(v) for k, v in n_per_arm.items()}}
    arms = " and ".join(f"{v:,} {k}" for k, v in n_per_arm.items())
    if abs(float(estimate)) < width:
        unit_text = f" {unit}" if unit else ""
        note = (f"Precision: the 95% interval ({_r4(width)}{unit_text} wide) is wider than the estimate itself with {arms} units, so the size of the "
                f"{'effect' if True else 'difference'} is poorly pinned down; a true effect smaller than about {_r4(mde)}{unit_text} would usually go undetected at this "
                f"sample size. Use plan_research_power to see the sample needed for the effect you care about.")
        return fields, note
    return fields, None
IMBALANCE = 0.25
MAX_EVENT_PERIODS = 30
MAX_FALLBACK_UNITS = 2000
TRUE_TOKENS = {"1", "1.0", "true", "t", "yes", "y", "treated", "treatment", "treat", "intervention", "observed", "followed", "post", "after"}
FALSE_TOKENS = {"0", "0.0", "false", "f", "no", "n", "control", "comparison", "untreated", "attrited", "lost", "pre", "before"}

DESIGN_TEXT = {
    "randomized": "Because assignment was randomised, this difference can be read as the causal effect of the treatment in this sample.",
    "quasi-experimental": "The design is quasi-experimental: the difference is an effect only if the identifying assumption of the design (comparable groups after adjustment) holds.",
    "observational": "This is an association unless the assignment was random or otherwise justified; selection into treatment can explain part or all of it.",
}
PARALLEL_TRENDS = ("Difference-in-differences identifies the effect only if the treated and comparison groups would have followed parallel trends "
                   "without the treatment. Check pre-treatment trends (the event-study pre-period coefficients, when available, should be near zero).")
MONOTONICITY = ("Lee (2009) bounds assume monotonicity: the treatment moves response in one direction only, so every unit that responds in the "
                "low-response arm would also have responded in the high-response arm. The arm with the higher response rate is trimmed by the excess "
                "response share from the top (lower bound) and from the bottom (upper bound) of its outcome distribution.")


# ---------- helpers

def _r4(v: Any) -> Optional[float]:
    """4 significant figures; None for missing."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return float(f"{f:.4g}")


def _pct(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    return round(float(v) * 100, 1)


def _not_pii(df: pd.DataFrame, c: str, what: str) -> str:
    kind = detect_pii(str(c), df[c])
    if kind:
        raise InvalidArguments(f"{what} '{c}' looks like personal data ({kind.replace('_', ' ')}); its values are never printed, so it cannot be used here")
    return c


def _binary(df: pd.DataFrame, c: str, what: str) -> Tuple[pd.Series, Dict[str, str]]:
    """Map a 0/1, true/false or two-level column to a float 1/0 series (NaN where missing)."""
    s = df[c]
    if pd.api.types.is_bool_dtype(s):
        return s.astype(float), {"1": "True", "0": "False"}
    nn = s.dropna()
    if pd.api.types.is_numeric_dtype(s):
        vals = sorted(set(float(v) for v in nn.unique()))
        if len(vals) == 2 and vals == [0.0, 1.0]:
            return pd.to_numeric(s, errors="coerce").astype(float), {"1": "1", "0": "0"}
        if len(vals) == 2:
            lo, hi = vals
            return s.map({lo: 0.0, hi: 1.0}).astype(float), {"1": str(hi), "0": str(lo)}
        raise InvalidArguments(f"{what} '{c}' must be binary (0/1, true/false or two levels); it has {len(vals)} distinct values: "
                               f"{', '.join(str(v) for v in vals[:6])}{'…' if len(vals) > 6 else ''}")
    labels = nn.astype(str).str.strip()
    levels = list(labels.value_counts().index)
    if len(levels) != 2:
        raise InvalidArguments(f"{what} '{c}' must be binary (0/1, true/false or two levels); it has {len(levels)} levels: "
                               f"{', '.join(sorted(levels)[:6])}{'…' if len(levels) > 6 else ''}")
    low = {lv.lower(): lv for lv in levels}
    one = next((low[k] for k in low if k in TRUE_TOKENS), None)
    zero = next((low[k] for k in low if k in FALSE_TOKENS), None)
    if one is None and zero is not None:
        one = next(lv for lv in levels if lv != zero)
    elif one is not None and zero is None:
        zero = next(lv for lv in levels if lv != one)
    elif one is None and zero is None:
        zero, one = sorted(levels)       # deterministic: the lexicographically larger level is coded 1
    out = pd.Series(np.nan, index=df.index, dtype=float)
    out[labels.index[labels == one]] = 1.0
    out[labels.index[labels == zero]] = 0.0
    return out, {"1": one, "0": zero}


def _cluster_ids(df: pd.DataFrame, idx: pd.Index, cluster: str) -> np.ndarray:
    g = df.loc[idx, cluster]
    if g.isna().any():
        raise InvalidArguments(f"cluster column '{cluster}' has missing values in the analysis rows")
    groups = g.astype(str).to_numpy()
    if len(np.unique(groups)) < 2:
        raise InvalidArguments(f"cluster column '{cluster}' has fewer than 2 groups; cluster-robust standard errors need at least 2 clusters")
    return groups


def _fit(X: np.ndarray, y: np.ndarray, groups: Optional[np.ndarray]) -> Dict[str, Any]:
    """OLS with HC1 (default) or cluster-robust (CR1) standard errors. Returns arrays + the t distribution df."""
    from scipy import stats as st
    n, k = X.shape
    with np.errstate(all="ignore"):
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        XtX_inv = np.linalg.pinv(X.T @ X)
    dof = max(1, n - k)
    if groups is not None:
        uniq, inv = np.unique(groups, return_inverse=True)
        G = len(uniq)
        scores = np.zeros((G, k))
        np.add.at(scores, inv, X * resid[:, None])
        meat = scores.T @ scores
        adj = (G / (G - 1)) * ((n - 1) / dof)
        with np.errstate(all="ignore"):
            cov = adj * XtX_inv @ meat @ XtX_inv
        dof_t = G - 1
        se_kind = f"cluster-robust ({G} clusters)"
    else:
        with np.errstate(all="ignore"):
            meat = (X * (resid ** 2)[:, None]).T @ X
            cov = (n / dof) * XtX_inv @ meat @ XtX_inv
        dof_t = dof
        se_kind = "HC1 robust"
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    tval = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    pval = 2 * st.t.sf(np.abs(tval), dof_t)
    tcrit = float(st.t.ppf(0.975, dof_t))
    return {"beta": beta, "se": se, "t": tval, "p": pval, "tcrit": tcrit, "n": n, "k": k, "dof_t": dof_t, "se_kind": se_kind}


def _term(fit: Dict[str, Any], i: int, name: str) -> Dict[str, Any]:
    b, s = float(fit["beta"][i]), float(fit["se"][i])
    p = fit["p"][i]
    return {"term": name, "coef": _r4(b), "se": _r4(s), "ci_low": _r4(b - fit["tcrit"] * s), "ci_high": _r4(b + fit["tcrit"] * s),
            "p": (None if np.isnan(p) else _r4(p)), "n": int(fit["n"])}


def _welch(a: np.ndarray, b: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(difference a-b, standardized difference, Welch p). Pooled SD = sqrt((var_a + var_b)/2)."""
    from scipy import stats as st
    if len(a) < 2 or len(b) < 2:
        return None, None, None
    diff = float(a.mean() - b.mean())
    sp = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
    std = diff / sp if sp > 0 else 0.0
    if a.var(ddof=1) == 0 and b.var(ddof=1) == 0:
        return diff, std, None
    p = float(st.ttest_ind(a, b, equal_var=False).pvalue)
    return diff, std, (None if np.isnan(p) else p)


def _balance_rows(df: pd.DataFrame, treat: pd.Series, cols: Sequence[str], label_a: str = "treated", label_b: str = "control") -> Tuple[List[Dict[str, Any]], List[str]]:
    """One row per numeric column (categoricals with ≤ 10 levels become one share row per level)."""
    rows: List[Dict[str, Any]] = []
    notes: List[str] = []
    for c in cols:
        s = df[c]
        num = pd.to_numeric(s, errors="coerce")
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s) or num.notna().mean() > 0.95:
            series = {str(c): num.astype(float)}
        else:
            cats = s.astype(object).where(s.notna(), None).astype(str).where(s.notna(), None)
            levels = cats.dropna().value_counts()
            if len(levels) > 10:
                notes.append(f"'{c}' has {len(levels)} categories and was left out of the balance table")
                continue
            series = {f"{c} = {lv}": (cats == lv).astype(float).where(cats.notna(), np.nan) for lv in levels.index}
        for name, v in series.items():
            ok = v.notna() & treat.notna()
            a = v[ok & (treat == 1)].to_numpy(float)
            b = v[ok & (treat == 0)].to_numpy(float)
            diff, std, p = _welch(a, b)
            rows.append({"column": name, f"{label_a}_mean": _r4(a.mean()) if len(a) else None, f"{label_a}_n": int(len(a)),
                         f"{label_b}_mean": _r4(b.mean()) if len(b) else None, f"{label_b}_n": int(len(b)),
                         "difference": _r4(diff), "std_diff": _r4(std), "p": _r4(p),
                         "flag": ("imbalanced" if std is not None and abs(std) > IMBALANCE else "balanced") if std is not None else "n/a"})
    return rows, notes


def _arm_stats(y: np.ndarray) -> Dict[str, Any]:
    from scipy import stats as st
    n = len(y)
    m = float(y.mean()) if n else None
    sd = float(y.std(ddof=1)) if n > 1 else None
    if n > 1 and sd is not None:
        se = sd / math.sqrt(n); tcrit = float(st.t.ppf(0.975, n - 1))
        lo, hi = m - tcrit * se, m + tcrit * se
    else:
        lo = hi = None
    return {"n": int(n), "mean": _r4(m), "sd": _r4(sd), "ci_low": _r4(lo), "ci_high": _r4(hi)}


def _frame(df: pd.DataFrame, treat: pd.Series, outcome: str, covariates: List[str], extra: Dict[str, pd.Series]) -> Tuple[pd.DataFrame, str]:
    """Working copy with the binary treatment (and other derived columns) added under reserved names."""
    d = df.copy()
    d["__treat"] = treat
    for k, v in extra.items():
        d[k] = v
    return d, "__treat"


def _covariates(df: pd.DataFrame, names: Optional[Sequence[str]], outcome: str, forbidden: Sequence[str]) -> List[str]:
    covs = [_col(df, c, "covariate") for c in (names or [])]
    for c in covs:
        if c == outcome or c in forbidden:
            raise InvalidArguments(f"'{c}' cannot be both a covariate and the outcome/treatment/period column")
    if len(set(covs)) != len(covs):
        raise InvalidArguments("covariates must be distinct")
    return covs


def _fit_design(d: pd.DataFrame, outcome: str, predictors: List[str], cluster: Optional[str]) -> Tuple[Dict[str, Any], List[str], pd.Index]:
    X, y, names, _ = _design(d, outcome, predictors, False)
    missing = [p for p in predictors if p.startswith("__") and p not in X.columns]
    if missing:
        raise InvalidArguments("the treatment (or period) indicator is constant in the complete rows; nothing to compare")
    groups = _cluster_ids(d, X.index, cluster) if cluster else None
    fit = _fit(X.to_numpy(float), y.to_numpy(float), groups)
    return fit, names, X.index


def _idx(names: List[str], term: str) -> int:
    try:
        return names.index(term)
    except ValueError:
        raise InvalidArguments(f"term '{term}' dropped from the design (constant after removing missing rows)")


# ---------- estimate_treatment_effect

@tool("estimate_treatment_effect",
      "Compare a numeric outcome between a binary treatment and control arm: means and n per arm, the unadjusted difference with HC1 (or cluster-robust) "
      "standard error, 95% CI and p-value, a covariate-adjusted OLS estimate (optionally ANCOVA with the baseline outcome), and a balance table "
      "(standardized differences; |d| > 0.25 flagged). `design` decides whether the wording says effect or association; the default is observational.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string", "description": "numeric outcome column"},
          "treatment": {"type": "string", "description": "binary column: 0/1, true/false or two levels"},
          "covariates": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 15},
          "cluster": {"type": ["string", "null"], "description": "column to cluster standard errors by (e.g. village)"},
          "baseline_outcome": {"type": ["string", "null"], "description": "baseline value of the outcome for an ANCOVA-style adjustment"},
          "balance_columns": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 30, "description": "columns for the balance table (default: covariates + baseline_outcome)"},
          "design": {"type": "string", "enum": ["randomized", "quasi-experimental", "observational"], "description": "how units were assigned (default observational)"}},
       "required": ["dataset_id", "outcome", "treatment"], "additionalProperties": False})
def estimate_treatment_effect(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment")
    if tcol == y:
        raise InvalidArguments("the outcome cannot also be the treatment")
    design = args.get("design") or "observational"
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    baseline = _col(df, args["baseline_outcome"], "baseline_outcome") if args.get("baseline_outcome") else None
    covs = _covariates(df, args.get("covariates"), y, [tcol, baseline or ""])
    if baseline and baseline in (y, tcol):
        raise InvalidArguments("baseline_outcome must differ from the outcome and the treatment")
    bal_cols = [_col(df, c, "balance column") for c in args["balance_columns"]] if args.get("balance_columns") else covs + ([baseline] if baseline else [])
    bal_cols = [c for c in dict.fromkeys(bal_cols) if c not in (y, tcol)]
    for c in bal_cols:
        _not_pii(df, c, "balance column")
    treat, levels = _binary(df, tcol, "treatment")
    ctx.progress(20, "comparing arms")
    d, T = _frame(df, treat, y, covs, {})
    yv = pd.to_numeric(d[y], errors="coerce")
    ok = yv.notna() & treat.notna()
    arms = {"treated": _arm_stats(yv[ok & (treat == 1)].to_numpy(float)), "control": _arm_stats(yv[ok & (treat == 0)].to_numpy(float))}
    if arms["treated"]["n"] < 2 or arms["control"]["n"] < 2:
        raise InvalidArguments(f"each arm needs at least 2 observed outcomes (treated n = {arms['treated']['n']}, control n = {arms['control']['n']})")
    fit_u, names_u, _ = _fit_design(d, y, [T], cluster)
    unadj = _term(fit_u, _idx(names_u, T), "treatment")
    adjusted = None
    adj_preds = [T] + covs + ([baseline] if baseline else [])
    if len(adj_preds) > 1:
        ctx.progress(45, "adjusting")
        fit_a, names_a, _ = _fit_design(d, y, adj_preds, cluster)
        adjusted = _term(fit_a, _idx(names_a, T), "treatment")
        adjusted["adjusted_for"] = covs + ([baseline] if baseline else [])
        adjusted["specification"] = "ANCOVA (outcome on treatment, baseline outcome and covariates)" if baseline else "OLS with covariates"
    ctx.progress(65, "balance table")
    balance, notes = _balance_rows(df, treat, bal_cols)
    imbalanced = [r["column"] for r in balance if r["flag"] == "imbalanced"]
    small = [k for k, v in arms.items() if v["n"] < SMALL_ARM]
    out = ctx.project.new_artifact_dir("treatment-effect", f"{y}-by-{tcol}")
    labels = [f"treated ({levels['1']})", f"control ({levels['0']})"]
    vals = [arms["treated"]["mean"], arms["control"]["mean"]]
    lows = [a["ci_low"] if a["ci_low"] is not None else a["mean"] for a in (arms["treated"], arms["control"])]
    highs = [a["ci_high"] if a["ci_high"] is not None else a["mean"] for a in (arms["treated"], arms["control"])]
    chart = svg.dots(f"Mean {y} by arm", labels, vals, lows, highs, subtitle=f"dot = mean, bar = 95% CI · n = {arms['treated']['n'] + arms['control']['n']:,}" + (" · sampled" if sampled else ""), zero_line=False)
    chart_path = str(ctx.project.write_text(out / "arms.svg", chart))
    _office_png(chart_path, "dots", f"Mean {y} by arm", labels, vals, lows, highs)
    csv_path = _save_table(ctx, out, "balance", pd.DataFrame(balance)) if balance else None
    est = adjusted or unadj
    word = "effect" if design == "randomized" else "difference"
    sig = "unlikely to be chance alone" if (est["p"] is not None and est["p"] < 0.05) else "could be chance"
    interp = [f"Mean {y} is {arms['treated']['mean']} in the treated arm (n = {arms['treated']['n']:,}) and {arms['control']['mean']} in the control arm (n = {arms['control']['n']:,}). "
              f"The {'covariate-adjusted ' if adjusted else ''}{word} is {est['coef']} (95% CI {est['ci_low']} to {est['ci_high']}, {fit_u['se_kind']} SE, p = {est['p']}): {sig}.",
              DESIGN_TEXT[design]]
    if balance:
        interp.append(f"Balance: {len(balance) - len(imbalanced)} of {len(balance)} baseline measures have |standardized difference| ≤ {IMBALANCE}" +
                      (f"; imbalanced: {', '.join(imbalanced[:6])}." if imbalanced else "."))
    if small:
        interp.append(f"Caution: the {' and '.join(small)} arm has fewer than {SMALL_ARM} observations; the estimate and its p-value are fragile.")
    precision, precision_note = _precision(est["coef"], est["ci_low"], est["ci_high"], est["se"], {"treated": arms["treated"]["n"], "control": arms["control"]["n"]})
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    if sampled:
        interp.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "estimate_treatment_effect", "dataset": ds.id, "outcome": y, "treatment": tcol, "n": unadj["n"], "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "treatment": tcol, "treatment_levels": levels, "design": design, "n": unadj["n"], "arms": arms,
            "unadjusted": unadj, "adjusted": adjusted, "se_kind": fit_u["se_kind"], "cluster": cluster, "balance": balance,
            "imbalanced_columns": imbalanced, "small_arms": small, "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Mean {y} per arm with 95% confidence intervals; {word} {est['coef']} (95% CI {est['ci_low']} to {est['ci_high']}).",
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- difference_in_differences

def _event_study(d: pd.DataFrame, y: str, unit: str, tcol_num: pd.Series, rel: pd.Series, treat: pd.Series, cluster: Optional[str], notes: List[str]) -> Dict[str, Any]:
    """Treated × relative-period coefficients with unit and time fixed effects, reference period -1."""
    work = pd.DataFrame({"y": pd.to_numeric(d[y], errors="coerce"), "unit": d[unit].astype(str), "time": tcol_num, "treat": treat, "rel_time": rel})
    work["rel_time"] = work["rel_time"].where(work["treat"] == 1, -1)   # controls carry no event dummies; the reference bin keeps them in the FE sample
    work = work.dropna()
    work["rel_time"] = work["rel_time"].round().astype(int)
    periods = sorted(int(p) for p in work.loc[work["treat"] == 1, "rel_time"].unique())
    if -1 not in periods:
        raise InvalidArguments("the event study needs observations in the period just before treatment (relative time -1) as the reference")
    if len(periods) > MAX_EVENT_PERIODS:
        raise InvalidArguments(f"{len(periods)} relative periods is too many for an event study; aggregate time first")
    if len(periods) < 3:
        raise InvalidArguments("an event study needs at least three relative periods (one before -1, the reference, and one after)")
    clus = cluster or unit
    if clus != unit:
        work["cluster"] = d.loc[work.index, clus].astype(str)
    else:
        work["cluster"] = work["unit"]
    if work["cluster"].nunique() < 2:
        raise InvalidArguments(f"cluster column '{clus}' has fewer than 2 groups")
    rows: List[Dict[str, Any]] = []
    engine = None
    try:
        import pyfixest as pf  # noqa: WPS433
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = pf.feols("y ~ i(rel_time, treat, ref=-1) | unit + time", data=work, vcov={"CRV1": "cluster"})
        coef, se, pv, ci = m.coef(), m.se(), m.pvalue(), m.confint()
        for name in coef.index:
            mt = re.search(r"::(-?\d+(?:\.\d+)?):", str(name))
            if not mt:
                continue
            k = int(float(mt.group(1)))
            rows.append({"rel_time": k, "coef": _r4(coef[name]), "se": _r4(se[name]), "ci_low": _r4(ci.loc[name].iloc[0]), "ci_high": _r4(ci.loc[name].iloc[1]), "p": _r4(pv[name])})
        engine = "pyfixest feols (unit + time fixed effects, CRV1 by " + clus + ")"
    except ImportError:
        notes.append("pyfixest is not installed; the event study used statsmodels OLS with unit and time dummies instead")
    except InvalidArguments:
        raise
    except Exception as e:  # noqa: BLE001 - fall back and say so
        notes.append(f"pyfixest raised {type(e).__name__}; the event study used statsmodels OLS with unit and time dummies instead")
        rows = []
    if not rows:
        import statsmodels.api as sm
        if work["unit"].nunique() > MAX_FALLBACK_UNITS:
            raise InvalidArguments(f"the fallback event study dummy-codes units and supports at most {MAX_FALLBACK_UNITS:,} units")
        X = pd.DataFrame(index=work.index)
        for k in periods:
            if k != -1:
                X[f"rel{k}"] = ((work["rel_time"] == k) & (work["treat"] == 1)).astype(float)
        X = pd.concat([X, pd.get_dummies(work["unit"], prefix="u", drop_first=True, dtype=float), pd.get_dummies(work["time"].astype(str), prefix="t", drop_first=True, dtype=float)], axis=1)
        X = sm.add_constant(X, has_constant="add")
        res = sm.OLS(work["y"].to_numpy(float), X.to_numpy(float)).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(work["cluster"])[0]})
        ci = res.conf_int()
        for j, nm in enumerate(X.columns):
            if nm.startswith("rel"):
                rows.append({"rel_time": int(nm[3:]), "coef": _r4(res.params[j]), "se": _r4(res.bse[j]), "ci_low": _r4(ci[j][0]), "ci_high": _r4(ci[j][1]), "p": _r4(res.pvalues[j])})
        engine = "statsmodels OLS with unit and time dummies (cluster-robust by " + clus + ")"
    rows.sort(key=lambda r: r["rel_time"])
    pre = [r for r in rows if r["rel_time"] < -1]
    pre_ok = all(r["se"] and abs(r["coef"]) < 2 * r["se"] for r in pre) if pre else None
    return {"engine": engine, "reference_period": -1, "n": int(len(work)), "units": int(work["unit"].nunique()), "clusters": int(work["cluster"].nunique()),
            "coefficients": rows, "pre_periods_near_zero": pre_ok}


@tool("difference_in_differences",
      "Two-way difference-in-differences: outcome ~ treated + post + treated×post (+ covariates) with HC1 or cluster-robust SEs (cluster on `unit` when given), "
      "the 2×2 table of group × period means, and the parallel-trends caveat. With a numeric `time` column and `treatment_time` it also fits an event study "
      "(treated × relative-period coefficients, unit and time fixed effects, reference period -1) with a chart.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string"},
          "treatment": {"type": "string", "description": "binary treatment-group column"},
          "period": {"type": ["string", "null"], "description": "binary post column (1 = after treatment); omit when giving `time` with `post_periods` or `treatment_time`"},
          "time": {"type": ["string", "null"], "description": "time/period column (numeric for an event study)"},
          "post_periods": {"type": ["array", "null"], "items": {"type": ["string", "number"]}, "minItems": 1, "maxItems": 100, "description": "values of `time` that count as post"},
          "treatment_time": {"type": ["number", "string", "null"], "description": "first treated period: a number, or the name of a column holding it per unit"},
          "covariates": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 15},
          "cluster": {"type": ["string", "null"], "description": "column to cluster SEs by (default: `unit` when given, else HC1)"},
          "unit": {"type": ["string", "null"], "description": "panel id column (enables unit fixed effects in the event study)"}},
       "required": ["dataset_id", "outcome", "treatment"], "additionalProperties": False})
def difference_in_differences(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment")
    unit = _col(df, args["unit"], "unit") if args.get("unit") else None
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else unit
    timecol = _col(df, args["time"], "time") if args.get("time") else None
    period = _col(df, args["period"], "period") if args.get("period") else None
    tt = args.get("treatment_time")
    tt_col = None
    if isinstance(tt, str):
        tt_col = _col(df, tt, "treatment_time")
    notes: List[str] = []
    treat, levels = _binary(df, tcol, "treatment")
    time_num = pd.to_numeric(df[timecol], errors="coerce") if timecol else None
    if period:
        post, _ = _binary(df, period, "period")
    elif timecol and args.get("post_periods"):
        want = {str(v) for v in args["post_periods"]}
        post = df[timecol].astype(str).isin(want).astype(float).where(df[timecol].notna(), np.nan)
        # "post_periods: [2025]" on a 2023–2026 panel almost always means "from 2025 onward"; treating the later,
        # unlisted periods as pre-treatment would bias the estimate towards zero. When the time column is numeric
        # and such later periods exist, they are counted as post and the result says so.
        if time_num is not None and time_num.notna().mean() >= 0.95:
            listed = pd.to_numeric(pd.Series(list(want)), errors="coerce").dropna()
            if len(listed):
                later = time_num[(time_num > listed.max()) & (post == 0)]
                if len(later):
                    post = (time_num >= listed.min()).astype(float).where(time_num.notna(), np.nan)
                    notes.append(f"post_periods listed {sorted(want)}; later periods {sorted(set(int(v) if float(v).is_integer() else v for v in later.unique()))} "
                                 "were also counted as post (treatment is assumed to persist). Pass `period` as an explicit 0/1 column to override.")
    elif timecol and tt is not None:
        if time_num.notna().mean() < 0.95:
            raise InvalidArguments(f"time column '{timecol}' must be numeric to compare with treatment_time")
        thr = pd.to_numeric(df[tt_col], errors="coerce") if tt_col else float(tt)
        post = (time_num >= thr).astype(float).where(time_num.notna(), np.nan)
    else:
        raise InvalidArguments("give `period` (a binary post column), or `time` with `post_periods`, or `time` with `treatment_time`")
    if post.dropna().nunique() < 2:
        raise InvalidArguments("the post indicator is constant; difference-in-differences needs both pre and post observations")
    covs = _covariates(df, args.get("covariates"), y, [tcol, period or "", timecol or "", unit or ""])
    ctx.progress(20, "fitting the interaction model")
    d, T = _frame(df, treat, y, covs, {"__post": post, "__did": treat * post})
    fit, names, idx = _fit_design(d, y, [T, "__post", "__did"] + covs, cluster)
    did = _term(fit, _idx(names, "__did"), "treated × post")
    terms = {"treated": _term(fit, _idx(names, T), "treated"), "post": _term(fit, _idx(names, "__post"), "post"), "did": did}
    yv = pd.to_numeric(d[y], errors="coerce")
    cells = []
    for g, gl in ((1, "treated"), (0, "control")):
        for p, pl in ((0, "pre"), (1, "post")):
            v = yv[idx][(treat[idx] == g) & (post[idx] == p)].to_numpy(float)
            cells.append({"group": gl, "period": pl, "n": int(len(v)), "mean": _r4(v.mean()) if len(v) else None})
    cm = {(c["group"], c["period"]): c["mean"] for c in cells}
    if any(c["n"] == 0 for c in cells):
        raise InvalidArguments("one of the four group × period cells is empty: " + ", ".join(f"{c['group']}/{c['period']} n = {c['n']}" for c in cells))
    change = {"treated": _r4(cm[("treated", "post")] - cm[("treated", "pre")]), "control": _r4(cm[("control", "post")] - cm[("control", "pre")])}
    event = None
    chart_path = None
    if timecol and tt is not None and time_num is not None and time_num.dropna().nunique() > 2:
        if not unit:
            notes.append("event study skipped: it needs `unit` (panel id) for unit fixed effects")
        else:
            ctx.progress(55, "event study")
            thr = pd.to_numeric(df[tt_col], errors="coerce") if tt_col else float(tt)
            rel = time_num - thr
            try:
                event = _event_study(d, y, unit, time_num, rel, treat, cluster, notes)
            except InvalidArguments as e:
                notes.append(f"event study skipped: {e}")
    elif timecol and tt is None and time_num is not None and time_num.dropna().nunique() > 2:
        notes.append("more than two periods: give `treatment_time` (and `unit`) for an event study")
    ctx.progress(80, "writing")
    out = ctx.project.new_artifact_dir("did", f"{y}-{tcol}")
    csv_path = _save_table(ctx, out, "cells", pd.DataFrame(cells))
    if event and event["coefficients"]:
        rows = event["coefficients"]
        labs = [f"t = {r['rel_time']:+d}" for r in rows]
        chart = svg.dots(f"{y}: event-study coefficients", labs, [r["coef"] for r in rows], [r["ci_low"] for r in rows], [r["ci_high"] for r in rows],
                         subtitle=f"treated × relative period, reference t = -1 · {event['engine']} · n = {event['n']:,}")
        chart_path = str(ctx.project.write_text(out / "event_study.svg", chart))
        _office_png(chart_path, "dots", f"{y}: event-study coefficients", labs, [r["coef"] for r in rows], [r["ci_low"] for r in rows], [r["ci_high"] for r in rows])
        _save_table(ctx, out, "event_study", pd.DataFrame(rows))
    else:
        labs = ["treated × post", "post", "treated"]
        body = [did, terms["post"], terms["treated"]]
        chart = svg.dots(f"{y}: difference-in-differences", labs, [c["coef"] for c in body], [c["ci_low"] for c in body], [c["ci_high"] for c in body],
                         subtitle=f"OLS, {fit['se_kind']} SEs, 95% CIs · n = {fit['n']:,}")
        chart_path = str(ctx.project.write_text(out / "did.svg", chart))
        _office_png(chart_path, "dots", f"{y}: difference-in-differences", labs, [c["coef"] for c in body], [c["ci_low"] for c in body], [c["ci_high"] for c in body])
    sig = "unlikely to be chance alone" if (did["p"] is not None and did["p"] < 0.05) else "could be chance"
    interp = [f"{y} changed by {change['treated']} in the treated group and {change['control']} in the control group between the pre and post periods. "
              f"The difference-in-differences estimate is {did['coef']} (95% CI {did['ci_low']} to {did['ci_high']}, {fit['se_kind']} SE, p = {did['p']}, n = {fit['n']:,}): {sig}.",
              PARALLEL_TRENDS]
    did_arms = {c["group"]: 0 for c in cells}
    for c in cells:
        did_arms[c["group"]] = did_arms.get(c["group"], 0) + int(c["n"])
    precision, precision_note = _precision(did["coef"], did["ci_low"], did["ci_high"], did["se"], did_arms)
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    if event:
        pre = [r for r in event["coefficients"] if r["rel_time"] < -1]
        if pre:
            interp.append(("Pre-treatment event-study coefficients are all within 2 SE of zero, consistent with parallel trends before treatment." if event["pre_periods_near_zero"]
                           else "At least one pre-treatment event-study coefficient differs from zero by more than 2 SE: the parallel-trends assumption is in doubt.")
                          + f" ({len(pre)} pre-period{'s' if len(pre) != 1 else ''}, {len(event['coefficients']) - len(pre)} post-period coefficients, reference t = -1.)")
    if sampled:
        interp.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "difference_in_differences", "dataset": ds.id, "outcome": y, "treatment": tcol, "n": fit["n"], "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "treatment": tcol, "treatment_levels": levels, "period": period, "time": timecol, "unit": unit, "cluster": cluster,
            "n": fit["n"], "se_kind": fit["se_kind"], "did": did, "coefficients": terms, "covariates": covs, "cells": cells, "change": change,
            "event_study": event, "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": (f"Event-study coefficients for {y} (treated × relative period, 95% CIs, reference t = -1)." if event else
                        f"Difference-in-differences coefficients for {y} with 95% CIs; treated × post = {did['coef']}."),
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- attrition_check

@tool("attrition_check",
      "Attrition between baseline and follow-up: the overall rate, the rate per treatment arm with the difference, its HC1 or cluster-robust SE and p-value, "
      "rates by subgroup × arm when `subgroup` is given, and a comparison of baseline characteristics between stayers and attriters (means, standardized differences).",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "followup_indicator": {"type": "string", "description": "binary column, 1 = observed at follow-up"},
          "treatment": {"type": "string", "description": "binary treatment column"},
          "subgroup": {"type": ["string", "null"], "description": "column with up to 12 levels (e.g. baseline wealth quintile)"},
          "baseline_columns": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 30},
          "cluster": {"type": ["string", "null"]}},
       "required": ["dataset_id", "followup_indicator", "treatment"], "additionalProperties": False})
def attrition_check(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    fcol = _col(df, args["followup_indicator"], "followup_indicator")
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment")
    if fcol == tcol:
        raise InvalidArguments("followup_indicator and treatment must be different columns")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    sub = _not_pii(df, _col(df, args["subgroup"], "subgroup"), "subgroup") if args.get("subgroup") else None
    base_cols = [_col(df, c, "baseline column") for c in (args.get("baseline_columns") or []) if c]
    base_cols = [c for c in dict.fromkeys(base_cols) if c not in (fcol, tcol)]
    for c in base_cols:
        _not_pii(df, c, "baseline column")
    try:
        observed, flevels = _binary(df, fcol, "followup_indicator")
        derived_note = (f"'{fcol}' is a 0/1 column with {int(df[fcol].isna().sum())} missing values; its zeros were read as not observed. If it is an endline outcome, "
                        f"pass the outcome column itself: rows missing at endline then count as attrited.") if df[fcol].isna().sum() > 0 else None
    except InvalidArguments:
        # The model often names the follow-up outcome itself (endline sales, missing for non-respondents) rather than a
        # 0/1 indicator; a column with gaps is then read as observed = non-missing, and the result says so.
        if df[fcol].isna().sum() == 0:
            raise InvalidArguments(f"followup_indicator '{fcol}' is neither binary nor a follow-up column with missing values; pass a 0/1 column or the endline outcome")
        observed = df[fcol].notna().astype(float); flevels = {"1": "non-missing", "0": "missing"}
        derived_note = f"follow-up read as '{fcol}' being non-missing ({int(observed.sum())} observed of {len(observed)})"
    treat, levels = _binary(df, tcol, "treatment")
    attrit = 1.0 - observed
    ok = attrit.notna() & treat.notna()
    if ok.sum() < 4:
        raise InvalidArguments("fewer than 4 rows with both a follow-up indicator and a treatment value")
    ctx.progress(25, "rates")
    n_all = int(ok.sum())
    overall = float(attrit[ok].mean())
    by_arm = {}
    for g, gl in ((1, "treated"), (0, "control")):
        v = attrit[ok & (treat == g)]
        by_arm[gl] = {"n": int(len(v)), "attrited": int(v.sum()), "rate_pct": _pct(v.mean()) if len(v) else None}
    if by_arm["treated"]["n"] == 0 or by_arm["control"]["n"] == 0:
        raise InvalidArguments("both arms need observations to compare attrition")
    d = df.copy(); d["__attrit"] = attrit; d["__treat"] = treat
    fit, names, _ = _fit_design(d, "__attrit", ["__treat"], cluster)
    diff = _term(fit, _idx(names, "__treat"), "treated − control attrition")
    diff_pct = {"difference_pct_points": _pct(fit["beta"][_idx(names, "__treat")]), "se_pct_points": _pct(fit["se"][_idx(names, "__treat")]),
                "ci_low_pct_points": _pct(diff["ci_low"]), "ci_high_pct_points": _pct(diff["ci_high"]), "p": diff["p"], "se_kind": fit["se_kind"]}
    subgroups = None
    if sub:
        ctx.progress(45, "subgroups")
        s = df[sub].astype(object).where(df[sub].notna(), None).astype(str).where(df[sub].notna(), None)
        lv = s[ok].dropna().value_counts()
        if len(lv) > 12:
            raise InvalidArguments(f"subgroup '{sub}' has {len(lv)} levels; at most 12 are supported")
        subgroups = []
        for level in lv.index:
            m = ok & (s == level)
            row = {"subgroup": level, "n": int(m.sum()), "rate_pct": _pct(attrit[m].mean())}
            for g, gl in ((1, "treated"), (0, "control")):
                v = attrit[m & (treat == g)]
                row[f"{gl}_n"] = int(len(v)); row[f"{gl}_rate_pct"] = _pct(v.mean()) if len(v) else None
            row["difference_pct_points"] = (None if row["treated_rate_pct"] is None or row["control_rate_pct"] is None else round(row["treated_rate_pct"] - row["control_rate_pct"], 1))
            subgroups.append(row)
    baseline = None
    notes: List[str] = []
    if derived_note:
        notes.append(derived_note)
    if base_cols:
        ctx.progress(65, "stayers vs attriters")
        baseline, notes = _balance_rows(df[ok], attrit[ok], base_cols, label_a="attriters", label_b="stayers")
    differs = [r["column"] for r in (baseline or []) if r["flag"] == "imbalanced"]
    ctx.progress(85, "writing")
    out = ctx.project.new_artifact_dir("attrition", f"{fcol}-by-{tcol}")
    rows = [{"group": k, **v} for k, v in by_arm.items()]
    csv_path = _save_table(ctx, out, "attrition_by_arm", pd.DataFrame(rows))
    if subgroups:
        _save_table(ctx, out, "attrition_by_subgroup", pd.DataFrame(subgroups))
    if baseline:
        _save_table(ctx, out, "stayers_vs_attriters", pd.DataFrame(baseline))
    labs = ["treated", "control"]
    vals = [by_arm["treated"]["rate_pct"], by_arm["control"]["rate_pct"]]
    chart = svg.bars("Attrition rate by arm (%)", labs, vals, subtitle=f"share not observed at follow-up · n = {n_all:,}" + (" · sampled" if sampled else ""), value_fmt=lambda v: f"{v:.1f}%")
    chart_path = str(ctx.project.write_text(out / "attrition.svg", chart))
    _office_png(chart_path, "bars", "Attrition rate by arm (%)", labs, vals)
    sig = diff["p"] is not None and diff["p"] < 0.05
    interp = [f"{_pct(overall)}% of {n_all:,} units were not observed at follow-up: {by_arm['treated']['rate_pct']}% in the treated arm (n = {by_arm['treated']['n']:,}) "
              f"and {by_arm['control']['rate_pct']}% in the control arm (n = {by_arm['control']['n']:,}), a difference of {diff_pct['difference_pct_points']} points "
              f"(95% CI {diff_pct['ci_low_pct_points']} to {diff_pct['ci_high_pct_points']}, {fit['se_kind']} SE, p = {diff['p']}).",
              ("Attrition differs between arms, so the follow-up samples are no longer comparable by randomisation alone; treatment-effect estimates on respondents can be "
               "biased. Report bounds (lee_bounds) or model the attrition." if sig else
               "Differential attrition is not statistically distinguishable from zero at the 5% level; the arms lost units at similar rates. Bias from attrition is still "
               "possible if different kinds of units left each arm.")]
    precision, precision_note = _precision(diff_pct["difference_pct_points"], diff_pct["ci_low_pct_points"], diff_pct["ci_high_pct_points"],
                                           (diff["se"] * 100 if diff.get("se") is not None else None),
                                           {"treated": by_arm["treated"]["n"], "control": by_arm["control"]["n"]}, unit="points")
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    if baseline:
        interp.append(f"Attriters differ from stayers (|standardized difference| > {IMBALANCE}) on {len(differs)} of {len(baseline)} baseline measures" +
                      (f": {', '.join(differs[:6])}." if differs else ".") + (" Attrition is selective on these characteristics." if differs else ""))
    if sampled:
        interp.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "attrition_check", "dataset": ds.id, "followup": fcol, "treatment": tcol, "n": n_all, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "followup_indicator": fcol, "followup_levels": flevels, "treatment": tcol, "treatment_levels": levels, "cluster": cluster,
            "n": n_all, "overall_rate_pct": _pct(overall), "by_arm": by_arm, "difference": {**diff, **diff_pct}, "subgroup": sub, "by_subgroup": subgroups,
            "baseline_comparison": baseline, "selective_on": differs, "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Share of units not observed at follow-up, by arm (n = {n_all:,}).",
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- lee_bounds

def _lee(yt: np.ndarray, yc: np.ndarray, nt: int, nc: int) -> Optional[Tuple[float, float, float]]:
    """(lower, upper, trimming proportion) given respondents' outcomes and the arm sizes (respondents + non-respondents)."""
    if len(yt) == 0 or len(yc) == 0 or nt == 0 or nc == 0:
        return None
    rt, rc = len(yt) / nt, len(yc) / nc
    hi, lo_arm, sign = (yt, yc, 1.0) if rt >= rc else (yc, yt, -1.0)   # the high-response arm is trimmed
    q_hi, q_lo = max(rt, rc), min(rt, rc)
    p = (q_hi - q_lo) / q_hi if q_hi > 0 else 0.0
    if p <= 0:
        a = b = float(hi.mean())
    else:
        srt = np.sort(hi)
        cut_top = float(np.quantile(srt, 1 - p))
        cut_bot = float(np.quantile(srt, p))
        top_trim = srt[srt <= cut_top]       # drops the top p share → lower mean
        bot_trim = srt[srt >= cut_bot]       # drops the bottom p share → higher mean
        a = float(top_trim.mean()) if len(top_trim) else float(srt.mean())
        b = float(bot_trim.mean()) if len(bot_trim) else float(srt.mean())
    other = float(lo_arm.mean())
    e1 = sign * (a - other)
    e2 = sign * (b - other)
    return min(e1, e2), max(e1, e2), p


@tool("lee_bounds",
      "Lee (2009) trimming bounds on a treatment effect under differential attrition. The arm with the higher response rate is trimmed by the excess response "
      "share from the top and from the bottom of its outcome distribution; the two trimmed differences are the lower and upper bounds. Bootstrap SEs "
      "(200 draws, seed 0; 100 when n > 20,000; cluster bootstrap when `cluster` is given). Refuses when response rates are equal or an arm has < 20 respondents.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string", "description": "outcome observed for respondents only (missing for non-respondents) unless followup_indicator is given"},
          "treatment": {"type": "string", "description": "binary treatment column"},
          "followup_indicator": {"type": ["string", "null"], "description": "binary column, 1 = responded; default: outcome is non-missing"},
          "cluster": {"type": ["string", "null"], "description": "cluster column for a cluster bootstrap"}},
       "required": ["dataset_id", "outcome", "treatment"], "additionalProperties": False})
def lee_bounds(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment")
    if y == tcol:
        raise InvalidArguments("the outcome cannot also be the treatment")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    fcol = _col(df, args["followup_indicator"], "followup_indicator") if args.get("followup_indicator") else None
    treat, levels = _binary(df, tcol, "treatment")
    yv = pd.to_numeric(df[y], errors="coerce")
    if fcol:
        resp, _ = _binary(df, fcol, "followup_indicator")
        resp = (resp == 1) & yv.notna()
    else:
        resp = yv.notna()
    ok = treat.notna()
    if cluster:
        groups_all = _cluster_ids(df, df.index[ok], cluster)
    tr = treat[ok].to_numpy(float); yy = yv[ok].to_numpy(float); rr = resp[ok].to_numpy(bool)
    nt, nc = int((tr == 1).sum()), int((tr == 0).sum())
    yt, yc = yy[(tr == 1) & rr], yy[(tr == 0) & rr]
    if nt == 0 or nc == 0:
        raise InvalidArguments("both arms need observations")
    if len(yt) < 20 or len(yc) < 20:
        raise InvalidArguments(f"each arm needs at least 20 respondents (treated {len(yt)}, control {len(yc)})")
    rt, rc = len(yt) / nt, len(yc) / nc
    if abs(rt - rc) < 1e-9:
        raise InvalidArguments("response rates are equal in the two arms; no trimming is needed, use estimate_treatment_effect")
    ctx.progress(20, "trimming")
    naive = float(yt.mean() - yc.mean())
    lower, upper, p = _lee(yt, yc, nt, nc)
    trimmed_arm = "treated" if rt > rc else "control"
    n_total = nt + nc
    reps = 100 if n_total > 20000 else 200
    rng = np.random.default_rng(0)
    ctx.progress(40, f"bootstrap ({reps} draws)")
    lows: List[float] = []; highs: List[float] = []
    if cluster:
        uniq, inv = np.unique(groups_all, return_inverse=True)
        members = [np.flatnonzero(inv == g) for g in range(len(uniq))]
    for r in range(reps):
        if r % 50 == 0:
            ctx.check()
        if cluster:
            pick = rng.integers(0, len(uniq), len(uniq))
            idx = np.concatenate([members[g] for g in pick])
        else:
            idx = rng.integers(0, n_total, n_total)
        t_b, y_b, r_b = tr[idx], yy[idx], rr[idx]
        res = _lee(y_b[(t_b == 1) & r_b], y_b[(t_b == 0) & r_b], int((t_b == 1).sum()), int((t_b == 0).sum()))
        if res is None:
            continue
        lows.append(res[0]); highs.append(res[1])
    se_low = float(np.std(lows, ddof=1)) if len(lows) > 1 else None
    se_high = float(np.std(highs, ddof=1)) if len(highs) > 1 else None
    z = 1.959963984540054
    ci_low = lower - z * se_low if se_low is not None else None
    ci_high = upper + z * se_high if se_high is not None else None
    ctx.progress(85, "writing")
    out = ctx.project.new_artifact_dir("lee-bounds", f"{y}-by-{tcol}")
    labs = ["naive difference (respondents)", "Lee lower bound", "Lee upper bound"]
    vals = [naive, lower, upper]
    lo_b = [naive, ci_low if ci_low is not None else lower, upper - z * se_high if se_high is not None else upper]
    hi_b = [naive, lower + z * se_low if se_low is not None else lower, ci_high if ci_high is not None else upper]
    chart = svg.dots(f"{y}: Lee bounds on the treatment effect", labs, vals, lo_b, hi_b,
                     subtitle=f"trimming {_pct(p)}% of the {trimmed_arm} arm · {reps} bootstrap draws{' (cluster)' if cluster else ''} · respondents n = {len(yt) + len(yc):,}")
    chart_path = str(ctx.project.write_text(out / "lee_bounds.svg", chart))
    _office_png(chart_path, "dots", f"{y}: Lee bounds", labs, vals, lo_b, hi_b)
    table = pd.DataFrame([{"quantity": "naive_difference", "estimate": naive, "se": None}, {"quantity": "lower_bound", "estimate": lower, "se": se_low}, {"quantity": "upper_bound", "estimate": upper, "se": se_high}])
    csv_path = _save_table(ctx, out, "bounds", table)
    excludes_zero = (lower > 0 and (ci_low is None or ci_low > 0)) or (upper < 0 and (ci_high is None or ci_high < 0))
    interp = [f"Response was {_pct(rt)}% in the treated arm (n = {nt:,}) and {_pct(rc)}% in the control arm (n = {nc:,}); trimming {_pct(p)}% of the {trimmed_arm} arm's respondents "
              f"bounds the treatment effect on {y} between {_r4(lower)} and {_r4(upper)} (naive difference among respondents {_r4(naive)}; bootstrap SEs {_r4(se_low)} and {_r4(se_high)}; "
              f"outer 95% interval {_r4(ci_low)} to {_r4(ci_high)}).",
              ("The whole interval, including sampling uncertainty, lies on one side of zero: the sign of the effect is robust to differential attrition under monotonicity."
               if excludes_zero else "The interval includes zero once sampling uncertainty is added: the sign of the effect is not robust to differential attrition."),
              MONOTONICITY]
    notes = [MONOTONICITY, f"Bootstrap: {reps} draws, seed 0" + (f", resampling {len(uniq)} clusters of '{cluster}'" if cluster else ", resampling units") + (" (100 draws because n > 20,000)" if reps == 100 else "") + "."]
    if len(lows) < reps:
        notes.append(f"{reps - len(lows)} bootstrap draws had an empty arm and were skipped.")
    if sampled:
        notes.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "lee_bounds", "dataset": ds.id, "outcome": y, "treatment": tcol, "n": n_total, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "treatment": tcol, "treatment_levels": levels, "cluster": cluster, "n": n_total,
            "response": {"treated": {"n": nt, "respondents": int(len(yt)), "rate_pct": _pct(rt)}, "control": {"n": nc, "respondents": int(len(yc)), "rate_pct": _pct(rc)}},
            "trimmed_arm": trimmed_arm, "trimming_proportion_pct": _pct(p), "naive_difference": _r4(naive),
            "lower_bound": _r4(lower), "upper_bound": _r4(upper), "se_lower": _r4(se_low), "se_upper": _r4(se_high), "ci_low": _r4(ci_low), "ci_high": _r4(ci_high),
            "bootstrap_reps": reps, "bootstrap_completed": len(lows), "sign_robust": bool(excludes_zero), "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Lee (2009) bounds on the effect of {tcol} on {y}: {_r4(lower)} to {_r4(upper)}, with bootstrap 95% whiskers.",
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}
