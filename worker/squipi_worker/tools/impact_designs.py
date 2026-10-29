"""Impact-evaluation designs: randomized_trial, regression_discontinuity, matching_estimate, instrumental_variables.

Everything is computed by numpy/scipy/statsmodels/sklearn (and pyfixest when it imports) and returned with sample sizes
and uncertainty. Every result carries a `design` line that names the assumption carrying the causal claim
(randomisation; continuity at the cutoff; selection on observables; exclusion restriction + relevance). The wording in
`interpretation` is a fixed template filled with the computed numbers."""
from __future__ import annotations

import math
import time
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .. import svg
from .stats import _load, _col, _design, _save_table, _office_png
from .impact import (_r4, _pct, _not_pii, _binary, _cluster_ids, _term, _precision, _balance_rows, _covariates, MDE_MULTIPLIER, IMBALANCE)

Z975 = 1.959963984540054
FEW_CLUSTERS = 20
MAX_SUBGROUP_LEVELS = 10
MAX_STRATA = 30
RI_DRAWS, RI_DRAWS_LARGE, LARGE_N = 1000, 300, 20000
BOOT_REPS, BOOT_REPS_LARGE = 200, 100
MATCH_BALANCE = 0.1
WEAK_T = 3.0
WEAK_F, STRONG_F = 10.0, 104.0

RCT_DESIGN = ("Randomised assignment supports a causal reading provided randomisation was implemented as described: the assignment column must be "
              "the original random allocation (intention to treat), not actual take-up.")
RD_DESIGN = ("Regression discontinuity identifies the effect at the cutoff under continuity: without the treatment the outcome would change smoothly "
             "through the cutoff, so units just below and just above are comparable. This needs a running variable that units cannot precisely "
             "manipulate; the estimate is local to the cutoff and does not transfer to units far from it.")
MATCH_DESIGN = ("Matching identifies the effect only under selection on observables: every confounder that drives both treatment and outcome is among "
                "the covariates (conditional independence) and treated units have comparable controls (overlap). Confounding by unobserved "
                "characteristics is not removed by matching.")
IV_DESIGN = ("Instrumental variables identify the effect under two assumptions: relevance (the instruments move the endogenous variable, checked by the "
             "first-stage F) and the exclusion restriction (the instruments affect the outcome only through the endogenous variable and are as good as "
             "randomly assigned), which the data cannot test.")


# ---------- estimation helpers (same HC1 / CR1 sandwich as impact._fit, with the covariance kept)

def _sandwich(Xs: np.ndarray, resid: np.ndarray, bread: np.ndarray, groups: Optional[np.ndarray], n: int, k: int) -> Tuple[np.ndarray, int, str]:
    dof = max(1, n - k)
    if groups is not None:
        uniq, inv = np.unique(groups, return_inverse=True)
        G = len(uniq)
        scores = np.zeros((G, k))
        np.add.at(scores, inv, Xs * resid[:, None])
        meat = scores.T @ scores
        adj = (G / max(1, G - 1)) * ((n - 1) / dof)
        with np.errstate(all="ignore"):
            cov = adj * bread @ meat @ bread
        return cov, max(1, G - 1), f"cluster-robust ({G} clusters)"
    with np.errstate(all="ignore"):
        meat = (Xs * (resid ** 2)[:, None]).T @ Xs
        cov = (n / dof) * bread @ meat @ bread
    return cov, dof, "HC1 robust"


def _pack(beta: np.ndarray, cov: np.ndarray, n: int, k: int, dof_t: int, se_kind: str) -> Dict[str, Any]:
    from scipy import stats as st
    se = np.sqrt(np.maximum(np.diag(cov), 0))
    tval = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    pval = 2 * st.t.sf(np.abs(tval), dof_t)
    return {"beta": beta, "se": se, "t": tval, "p": pval, "tcrit": float(st.t.ppf(0.975, dof_t)), "n": int(n), "k": int(k), "dof_t": int(dof_t),
            "se_kind": se_kind, "cov": cov}


def _ols(X: np.ndarray, y: np.ndarray, groups: Optional[np.ndarray], weights: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """OLS (or WLS when `weights` is given) with HC1 or cluster-robust (CR1) standard errors; mirrors impact._fit and keeps `cov`."""
    X = np.asarray(X, float); y = np.asarray(y, float)
    if weights is not None:
        sw = np.sqrt(np.asarray(weights, float))
        X = X * sw[:, None]; y = y * sw
    n, k = X.shape
    with np.errstate(all="ignore"):
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        bread = np.linalg.pinv(X.T @ X)
    cov, dof_t, kind = _sandwich(X, resid, bread, groups, n, k)
    out = _pack(beta, cov, n, k, dof_t, kind)
    with np.errstate(all="ignore"):
        out["resid"] = resid; out["rss"] = float(resid @ resid)
    return out


def _iv(X: np.ndarray, Z: np.ndarray, y: np.ndarray, groups: Optional[np.ndarray], weights: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """Two-stage least squares. X = [exogenous (with intercept), endogenous], Z = [exogenous, instruments]. The sandwich uses the
    projected regressors X̂ = P_Z X and the structural residual y − Xβ (the IV sandwich, not the naive second-stage OLS SE)."""
    X = np.asarray(X, float); Z = np.asarray(Z, float); y = np.asarray(y, float)
    if weights is not None:
        sw = np.sqrt(np.asarray(weights, float))
        X = X * sw[:, None]; Z = Z * sw[:, None]; y = y * sw
    n, k = X.shape
    with np.errstate(all="ignore"):
        Xhat = Z @ (np.linalg.pinv(Z.T @ Z) @ (Z.T @ X))
        bread = np.linalg.pinv(Xhat.T @ X)
        beta = bread @ (Xhat.T @ y)
        resid = y - X @ beta
        bread_s = np.linalg.pinv(Xhat.T @ Xhat)
    cov, dof_t, kind = _sandwich(Xhat, resid, bread_s, groups, n, k)
    out = _pack(beta, cov, n, k, dof_t, kind)
    out["resid"] = resid
    return out


def _wald(fit: Dict[str, Any], idx: Sequence[int]) -> Tuple[Optional[float], Optional[float]]:
    """Joint test that the coefficients at `idx` are zero: F = W/q with the robust covariance; p from F(q, dof_t)."""
    from scipy import stats as st
    idx = list(idx)
    if not idx:
        return None, None
    b = fit["beta"][idx]
    V = fit["cov"][np.ix_(idx, idx)]
    with np.errstate(all="ignore"):
        W = float(b @ np.linalg.pinv(V) @ b)
    if not math.isfinite(W):
        return None, None
    F = W / len(idx)
    return F, float(st.f.sf(F, len(idx), fit["dof_t"]))


def _lincomb(fit: Dict[str, Any], c: np.ndarray) -> Tuple[float, float]:
    b = float(c @ fit["beta"])
    v = float(c @ fit["cov"] @ c)
    return b, math.sqrt(max(v, 0.0))


def _bh(ps: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Benjamini–Hochberg q-values (step-up); None stays None."""
    valid = [(i, p) for i, p in enumerate(ps) if p is not None and math.isfinite(p)]
    out: List[Optional[float]] = [None] * len(ps)
    m = len(valid)
    if m == 0:
        return out
    order = sorted(valid, key=lambda t: t[1])
    prev = 1.0
    for rank in range(m, 0, -1):
        i, p = order[rank - 1]
        prev = min(prev, p * m / rank)
        out[i] = prev
    return out


def _estimate(fit: Dict[str, Any], i: int, name: str) -> Dict[str, Any]:
    return _term(fit, i, name)


def _z_estimate(name: str, est: float, se: Optional[float], n: int) -> Dict[str, Any]:
    from scipy import stats as st
    if se is None or not math.isfinite(se) or se <= 0:
        return {"term": name, "coef": _r4(est), "se": None, "ci_low": None, "ci_high": None, "p": None, "n": int(n)}
    z = est / se
    return {"term": name, "coef": _r4(est), "se": _r4(se), "ci_low": _r4(est - Z975 * se), "ci_high": _r4(est + Z975 * se), "p": _r4(2 * st.norm.sf(abs(z))), "n": int(n)}


def _sig(p: Optional[float]) -> str:
    return "unlikely to be chance alone" if (p is not None and p < 0.05) else "could be chance"


def _categorical(df: pd.DataFrame, c: str, prefix: str) -> pd.Series:
    s = df[c]
    out = s.astype(object).where(s.notna(), None).astype(str).where(s.notna(), None)
    return (prefix + out).where(s.notna(), None)   # the prefix stops _design reading "1"/"2" blocks as a numeric predictor


def _numeric_outcome(df: pd.DataFrame, c: str) -> pd.Series:
    s = pd.to_numeric(df[c], errors="coerce")
    if s.notna().sum() < 4:
        raise InvalidArguments(f"outcome '{c}' has fewer than 4 numeric values")
    return s.astype(float)


# ---------- randomized_trial

def _rct_design(d: pd.DataFrame, outcome: str, preds: List[str], cluster: Optional[str]) -> Tuple[Dict[str, Any], List[str], pd.Index]:
    X, yv, names, _ = _design(d, outcome, preds, False)
    if "__treat" not in X.columns:
        raise InvalidArguments("the assignment indicator is constant in the complete rows; nothing to compare")
    groups = _cluster_ids(d, X.index, cluster) if cluster else None
    return _ols(X.to_numpy(float), yv.to_numpy(float), groups), names, X.index


def _permuter(T: np.ndarray, groups: Optional[np.ndarray], strata: Optional[np.ndarray], notes: List[str]):
    """Returns (draw(rng) -> permuted assignment, description). Clusters are permuted as blocks; strata permute within block."""
    n = len(T)
    if groups is not None:
        uniq, inv = np.unique(groups, return_inverse=True)
        first = np.full(len(uniq), -1)
        for i in range(n):
            if first[inv[i]] < 0:
                first[inv[i]] = i
        cT = T[first]
        if np.all(cT[inv] == T):
            cS = strata[first] if strata is not None else None
            pos = [np.flatnonzero(cS == s) for s in np.unique(cS)] if cS is not None else [np.arange(len(uniq))]
            def draw(rng):
                out = cT.copy()
                for p in pos:
                    out[p] = cT[rng.permutation(p)]
                return out[inv]
            return draw, f"assignment re-drawn across {len(uniq)} clusters" + (" within strata" if strata is not None else "")
        notes.append("assignment varies within clusters, so randomization inference re-draws it across units, not clusters")
    pos = [np.flatnonzero(strata == s) for s in np.unique(strata)] if strata is not None else [np.arange(n)]
    def draw_units(rng):
        out = T.copy()
        for p in pos:
            out[p] = T[rng.permutation(p)]
        return out
    return draw_units, "assignment re-drawn across units" + (" within strata" if strata is not None else "")


def _ri_pvalue(X: np.ndarray, y: np.ndarray, t_idx: int, draws: int, drawer, ctx: JobContext) -> Tuple[float, float, int]:
    """Permutation p-value for the coefficient on the assignment column via Frisch–Waugh–Lovell on the other columns."""
    W = np.delete(X, t_idx, axis=1)
    T = X[:, t_idx].copy()
    with np.errstate(all="ignore"):        # Accelerate BLAS raises spurious matmul warnings on macOS
        WtW_inv = np.linalg.pinv(W.T @ W)
        def resid(v):
            return v - W @ (WtW_inv @ (W.T @ v))
        yt = resid(y)
        def coef(t):
            tt = resid(t)
            den = float(tt @ tt)
            return float(tt @ yt) / den if den > 0 else 0.0
        obs = coef(T)
        rng = np.random.default_rng(0)
        count = 0
        for r in range(draws):
            if r % 100 == 0:
                ctx.check()
            if abs(coef(drawer(rng))) >= abs(obs) - 1e-12:
                count += 1
    return (count + 1) / (draws + 1), obs, count


@tool("randomized_trial",
      "Analyse a randomised controlled trial: balance table with a joint test, intention-to-treat effect unadjusted and covariate/ANCOVA-adjusted "
      "(HC1 or cluster-robust SEs; block fixed effects with `strata`), treatment-on-the-treated / LATE by 2SLS when a `compliance` (take-up) column is "
      "given, heterogeneity by subgroup with interaction tests, Benjamini–Hochberg and Bonferroni adjustment across `outcomes`, a randomization-inference "
      "p-value for the primary effect, and the post-hoc minimum detectable effect. Refuses a non-binary assignment column.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string", "description": "primary numeric outcome"},
          "treatment": {"type": "string", "description": "binary random assignment column (0/1, true/false or two levels)"},
          "covariates": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 15},
          "baseline_outcome": {"type": ["string", "null"], "description": "baseline value of the outcome (ANCOVA adjustment)"},
          "cluster": {"type": ["string", "null"], "description": "cluster column for a cluster-randomised trial (cluster-robust SEs, cluster-level permutations)"},
          "strata": {"type": ["string", "null"], "description": "block / stratum column used in the randomisation (adds block fixed effects)"},
          "compliance": {"type": ["string", "null"], "description": "binary actual take-up column; gives the TOT / LATE by 2SLS with assignment as the instrument"},
          "subgroups": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 6, "description": "columns (≤ 10 levels each) for heterogeneity"},
          "outcomes": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 20, "description": "additional outcomes for multiple-testing adjustment"},
          "randomization_inference": {"type": "boolean", "description": "permutation p-value for the primary ITT (default true; 1000 draws, seed 0; 300 when n > 20,000)"}},
       "required": ["dataset_id", "outcome", "treatment"], "additionalProperties": False})
def randomized_trial(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment")
    if tcol == y:
        raise InvalidArguments("the outcome cannot also be the treatment")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    strata = _not_pii(df, _col(df, args["strata"], "strata"), "strata") if args.get("strata") else None
    compliance = _not_pii(df, _col(df, args["compliance"], "compliance"), "compliance") if args.get("compliance") else None
    baseline = _col(df, args["baseline_outcome"], "baseline_outcome") if args.get("baseline_outcome") else None
    if baseline and baseline in (y, tcol):
        raise InvalidArguments("baseline_outcome must differ from the outcome and the treatment")
    if compliance and compliance in (y, tcol):
        raise InvalidArguments("compliance must differ from the outcome and the treatment")
    covs = _covariates(df, args.get("covariates"), y, [tcol, baseline or "", strata or "", compliance or ""])
    for c in covs:
        _not_pii(df, c, "covariate")
    subgroups = []
    for s in args.get("subgroups") or []:
        sc = _not_pii(df, _col(df, s, "subgroup"), "subgroup")
        if sc in (y, tcol):
            raise InvalidArguments(f"subgroup '{sc}' cannot be the outcome or the treatment")
        if sc not in subgroups:
            subgroups.append(sc)
    extra_outcomes = []
    for o in args.get("outcomes") or []:
        oc = _col(df, o, "outcome")
        if oc != y and oc != tcol and oc not in extra_outcomes:
            extra_outcomes.append(oc)
    notes: List[str] = []
    treat, levels = _binary(df, tcol, "treatment")
    d = df.copy()
    d["__treat"] = treat
    d["__out"] = _numeric_outcome(df, y)
    strata_terms: List[str] = []
    if strata:
        d["__strata"] = _categorical(df, strata, "block ")
        nb = d["__strata"].dropna().nunique()
        if nb > MAX_STRATA:
            raise InvalidArguments(f"strata '{strata}' has {nb} blocks; at most {MAX_STRATA} are supported")
        if nb < 2:
            raise InvalidArguments(f"strata '{strata}' has a single block; omit it")
        strata_terms = ["__strata"]
    ctx.progress(10, "intention-to-treat")
    fit_u, names_u, idx_u = _rct_design(d, "__out", ["__treat"] + strata_terms, cluster)
    itt_u = _estimate(fit_u, names_u.index("__treat"), "ITT (unadjusted)")
    adj_extras = covs + ([baseline] if baseline else [])
    itt_a = None
    fit_p, names_p, idx_p = fit_u, names_u, idx_u
    if adj_extras:
        fit_a, names_a, idx_a = _rct_design(d, "__out", ["__treat"] + adj_extras + strata_terms, cluster)
        itt_a = _estimate(fit_a, names_a.index("__treat"), "ITT (adjusted)")
        itt_a["adjusted_for"] = adj_extras + ([strata] if strata else [])
        itt_a["specification"] = ("ANCOVA (outcome on assignment, baseline outcome and covariates" if baseline else "OLS with covariates") + (", block fixed effects)" if strata else (")" if baseline else ""))
        fit_p, names_p, idx_p = fit_a, names_a, idx_a
    primary = itt_a or itt_u
    primary_spec = ["__treat"] + adj_extras + strata_terms
    n_clusters = int(d.loc[idx_p, cluster].astype(str).nunique()) if cluster else None
    if cluster and n_clusters < FEW_CLUSTERS:
        notes.append(f"Only {n_clusters} clusters: cluster-robust standard errors are unreliable with fewer than {FEW_CLUSTERS} clusters; "
                     "the randomization-inference p-value is the safer guide.")
    tv = treat[idx_p]
    arms = {"treated": {"n": int((tv == 1).sum()), "mean": _r4(d.loc[idx_p, "__out"][tv == 1].mean())},
            "control": {"n": int((tv == 0).sum()), "mean": _r4(d.loc[idx_p, "__out"][tv == 0].mean())}}
    if arms["treated"]["n"] < 2 or arms["control"]["n"] < 2:
        raise InvalidArguments(f"each arm needs at least 2 observed outcomes (treated n = {arms['treated']['n']}, control n = {arms['control']['n']})")
    # balance
    ctx.progress(25, "balance")
    bal_cols = [c for c in dict.fromkeys(covs + ([baseline] if baseline else [])) if c not in (y, tcol)]
    balance, bnotes = _balance_rows(df, treat, bal_cols)
    notes.extend(bnotes)
    joint_p = None
    if bal_cols:
        try:
            Xb, yb, nb_names, _ = _design(d, "__treat", bal_cols, False)
            gb = _cluster_ids(d, Xb.index, cluster) if cluster else None
            fb = _ols(Xb.to_numpy(float), yb.to_numpy(float), gb)
            _, joint_p = _wald(fb, [i for i, nm in enumerate(nb_names) if nm != "(intercept)"])
        except InvalidArguments as e:
            notes.append(f"joint balance test skipped: {e}")
    imbalanced = [r["column"] for r in balance if r["flag"] == "imbalanced"]
    # TOT / LATE
    tot = None
    if compliance:
        ctx.progress(35, "treatment on the treated")
        takeup, _ = _binary(df, compliance, "compliance")
        d["__takeup"] = takeup
        Xall, yall, nall, _ = _design(d, "__out", ["__takeup", "__treat"] + adj_extras + strata_terms, False)
        if "__takeup" not in Xall.columns or "__treat" not in Xall.columns:
            raise InvalidArguments("take-up or assignment is constant in the complete rows; no first stage")
        g_t = _cluster_ids(d, Xall.index, cluster) if cluster else None
        X = Xall.drop(columns="__treat"); Z = Xall.drop(columns="__takeup")
        xi = list(X.columns).index("__takeup")
        fs = _ols(Z.to_numpy(float), Xall["__takeup"].to_numpy(float), g_t)
        zi = list(Z.columns).index("__treat")
        first = _estimate(fs, zi, "first stage: take-up on assignment")
        ivf = _iv(X.to_numpy(float), Z.to_numpy(float), yall.to_numpy(float), g_t)
        late = _estimate(ivf, xi, "TOT / LATE (2SLS)")
        tu = takeup[Xall.index]; tv2 = treat[Xall.index]
        rate_t = float(tu[tv2 == 1].mean()) if (tv2 == 1).any() else None
        rate_c = float(tu[tv2 == 0].mean()) if (tv2 == 0).any() else None
        tot = {"estimate": late, "first_stage": first, "takeup_treated_pct": _pct(rate_t), "takeup_control_pct": _pct(rate_c),
               "compliance_share_pct": _pct(first["coef"]), "first_stage_t": _r4(fs["t"][zi]), "weak_first_stage": bool(abs(float(fs["t"][zi])) < WEAK_T) if np.isfinite(fs["t"][zi]) else True,
               "method": "2SLS with random assignment as the instrument for take-up (IV sandwich SE); the LATE is the effect among compliers"}
        if tot["weak_first_stage"]:
            notes.append(f"Weak first stage: assignment moves take-up with t = {tot['first_stage_t']} (< {WEAK_T:g}); the TOT / LATE is imprecise and may be biased.")
    # heterogeneity
    hetero: List[Dict[str, Any]] = []
    if subgroups:
        ctx.progress(50, "heterogeneity")
        for s in subgroups:
            d["__sub"] = _categorical(df, s, "g:")
            nl = d["__sub"].dropna().nunique()
            if nl > MAX_SUBGROUP_LEVELS:
                raise InvalidArguments(f"subgroup '{s}' has {nl} levels; at most {MAX_SUBGROUP_LEVELS} are supported")
            if nl < 2:
                notes.append(f"subgroup '{s}' has a single level and was skipped"); continue
            Xh, yh, nh, _ = _design(d, "__out", ["__treat", "__sub"] + adj_extras + strata_terms, False)
            if "__treat" not in Xh.columns:
                raise InvalidArguments("the assignment indicator is constant in the complete rows")
            sub_cols = [c for c in Xh.columns if c.startswith("__sub[")]
            for c in sub_cols:
                Xh[f"__treat×{c}"] = Xh["__treat"] * Xh[c]
            names_h = list(Xh.columns)
            gh = _cluster_ids(d, Xh.index, cluster) if cluster else None
            fh = _ols(Xh.to_numpy(float), yh.to_numpy(float), gh)
            inter_idx = [names_h.index(f"__treat×{c}") for c in sub_cols]
            Fj, pj = _wald(fh, inter_idx)
            present = d.loc[Xh.index, "__sub"]
            counts = present.value_counts()
            ref = next((lv for lv in counts.index if f"__sub[{lv}]" not in Xh.columns), counts.index[0])
            ti = names_h.index("__treat")
            for lv in counts.index:
                c = np.zeros(len(names_h)); c[ti] = 1.0
                if lv != ref:
                    c[names_h.index(f"__treat×__sub[{lv}]")] = 1.0
                b, se = _lincomb(fh, c)
                from scipy import stats as st
                tval = b / se if se > 0 else float("nan")
                m = present == lv
                tl = treat[Xh.index][m]
                hetero.append({"subgroup": s, "level": str(lv)[2:], "n_treated": int((tl == 1).sum()), "n_control": int((tl == 0).sum()),
                               "effect": _r4(b), "se": _r4(se), "ci_low": _r4(b - fh["tcrit"] * se), "ci_high": _r4(b + fh["tcrit"] * se),
                               "p": _r4(2 * st.t.sf(abs(tval), fh["dof_t"])) if math.isfinite(tval) else None,
                               "interaction_p": _r4(pj), "interaction_F": _r4(Fj), "reference_level": str(ref)[2:]})
        d.drop(columns="__sub", inplace=True, errors="ignore")
    # multiple outcomes
    ctx.progress(65, "outcomes")
    outcome_rows: List[Dict[str, Any]] = []
    for oc in [y] + extra_outcomes:
        d["__out_k"] = pd.to_numeric(df[oc], errors="coerce").astype(float)
        try:
            fk, nk, _ = _rct_design(d, "__out_k", primary_spec, cluster)
            e = _estimate(fk, nk.index("__treat"), oc)
            outcome_rows.append({"outcome": oc, "primary": oc == y, **{k: v for k, v in e.items() if k != "term"}})
        except InvalidArguments as ex:
            notes.append(f"outcome '{oc}' skipped: {ex}")
    d.drop(columns="__out_k", inplace=True, errors="ignore")
    m = len(outcome_rows)
    qs = _bh([r["p"] for r in outcome_rows])
    for r, q in zip(outcome_rows, qs):
        r["q_bh"] = _r4(q)
        r["p_bonferroni"] = _r4(min(1.0, r["p"] * m)) if r["p"] is not None else None
        r["significant_bh_5pct"] = bool(q is not None and q < 0.05)
    # randomization inference
    ri = None
    if args.get("randomization_inference", True):
        n_p = int(fit_p["n"])
        draws = RI_DRAWS_LARGE if n_p > LARGE_N else RI_DRAWS
        ctx.progress(75, f"randomization inference ({draws} draws)")
        Xp, yp, _, _ = _design(d, "__out", primary_spec, False)
        gp = d.loc[Xp.index, cluster].astype(str).to_numpy() if cluster else None
        sp = d.loc[Xp.index, "__strata"].astype(str).to_numpy() if strata else None
        drawer, scheme = _permuter(Xp["__treat"].to_numpy(float), gp, sp, notes)
        p_ri, obs, count = _ri_pvalue(Xp.to_numpy(float), yp.to_numpy(float), list(Xp.columns).index("__treat"), draws, drawer, ctx)
        ri = {"p": _r4(p_ri), "draws": draws, "seed": 0, "scheme": scheme, "observed": _r4(obs), "draws_at_least_as_extreme": int(count),
              "method": "two-sided permutation test of the primary ITT coefficient; p = (count + 1) / (draws + 1)"}
        if draws == RI_DRAWS_LARGE:
            notes.append(f"Randomization inference used {RI_DRAWS_LARGE} draws because n > {LARGE_N:,}.")
    # outputs
    ctx.progress(88, "writing")
    out = ctx.project.new_artifact_dir("rct", f"{y}-by-{tcol}")
    labs = [r["outcome"] for r in outcome_rows]
    chart = svg.dots(f"Intention-to-treat effect by outcome", labs, [r["coef"] for r in outcome_rows], [r["ci_low"] for r in outcome_rows], [r["ci_high"] for r in outcome_rows],
                     subtitle=f"{'adjusted ' if itt_a else ''}ITT, {fit_p['se_kind']} SEs, 95% CIs · n = {fit_p['n']:,}" + (" · sampled" if sampled else ""))
    chart_path = str(ctx.project.write_text(out / "itt.svg", chart))
    _office_png(chart_path, "dots", "Intention-to-treat effect by outcome", labs, [r["coef"] for r in outcome_rows], [r["ci_low"] for r in outcome_rows], [r["ci_high"] for r in outcome_rows])
    hetero_chart = None
    if hetero:
        hl = [f"{r['subgroup']} = {r['level']}" for r in hetero]
        hc = svg.dots(f"{y}: ITT by subgroup", hl, [r["effect"] for r in hetero], [r["ci_low"] for r in hetero], [r["ci_high"] for r in hetero],
                      subtitle="effect per level with 95% CI; interaction p per subgroup in the table")
        hetero_chart = str(ctx.project.write_text(out / "heterogeneity.svg", hc))
        _office_png(hetero_chart, "dots", f"{y}: ITT by subgroup", hl, [r["effect"] for r in hetero], [r["ci_low"] for r in hetero], [r["ci_high"] for r in hetero])
        _save_table(ctx, out, "heterogeneity", pd.DataFrame(hetero))
    csv_path = _save_table(ctx, out, "outcomes", pd.DataFrame(outcome_rows))
    if balance:
        _save_table(ctx, out, "balance", pd.DataFrame(balance))
    mde = MDE_MULTIPLIER * primary["se"] if primary["se"] is not None else None
    precision, precision_note = _precision(primary["coef"], primary["ci_low"], primary["ci_high"], primary["se"], {"treated": arms["treated"]["n"], "control": arms["control"]["n"]})
    interp = [f"Mean {y} is {arms['treated']['mean']} in the assigned-to-treatment arm (n = {arms['treated']['n']:,}) and {arms['control']['mean']} in the control arm "
              f"(n = {arms['control']['n']:,}). The {'adjusted ' if itt_a else ''}intention-to-treat effect is {primary['coef']} (95% CI {primary['ci_low']} to {primary['ci_high']}, "
              f"{fit_p['se_kind']} SE, p = {primary['p']}): {_sig(primary['p'])}.", RCT_DESIGN]
    if tot:
        interp.append(f"Take-up was {tot['takeup_treated_pct']}% among those assigned to treatment and {tot['takeup_control_pct']}% among controls (compliance share "
                      f"{tot['compliance_share_pct']} points, first-stage t = {tot['first_stage_t']}). The treatment-on-the-treated / LATE is {tot['estimate']['coef']} "
                      f"(95% CI {tot['estimate']['ci_low']} to {tot['estimate']['ci_high']}, p = {tot['estimate']['p']}): the effect for compliers, not for everyone.")
    if balance:
        interp.append(f"Balance: {len(balance) - len(imbalanced)} of {len(balance)} baseline measures have |standardized difference| ≤ {IMBALANCE}" +
                      (f"; imbalanced: {', '.join(imbalanced[:6])}" if imbalanced else "") + (f"; joint test p = {_r4(joint_p)}." if joint_p is not None else "."))
    for s in subgroups:
        rows = [r for r in hetero if r["subgroup"] == s]
        if rows:
            pj = rows[0]["interaction_p"]
            interp.append(f"Heterogeneity by {s}: " + "; ".join(f"{r['level']} {r['effect']} (95% CI {r['ci_low']} to {r['ci_high']})" for r in rows) +
                          f". Interaction p = {pj}: {'the effect differs across levels' if pj is not None and pj < 0.05 else 'no evidence that the effect differs across levels'}.")
    if m > 1:
        kept = sum(1 for r in outcome_rows if r["significant_bh_5pct"])
        interp.append(f"Across {m} outcomes, {kept} remain significant at a 5% false discovery rate (Benjamini–Hochberg); the Bonferroni column is the stricter family-wise correction.")
    if ri:
        interp.append(f"Randomization inference ({ri['draws']} re-draws of the assignment, {ri['scheme']}) gives p = {ri['p']} for the primary ITT.")
    interp.append(f"Post-hoc minimum detectable effect at 80% power: about {_r4(mde)} ({MDE_MULTIPLIER} × SE).")
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    if cluster and n_clusters < FEW_CLUSTERS:
        interp.append(notes[0] if notes and notes[0].startswith("Only") else f"Only {n_clusters} clusters.")
    if sampled:
        interp.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "randomized_trial", "dataset": ds.id, "outcome": y, "treatment": tcol, "n": fit_p["n"], "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "treatment": tcol, "treatment_levels": levels, "design": RCT_DESIGN, "n": int(fit_p["n"]), "arms": arms,
            "cluster": cluster, "n_clusters": n_clusters, "strata": strata, "se_kind": fit_p["se_kind"], "itt_unadjusted": itt_u, "itt_adjusted": itt_a, "itt": primary,
            "balance": balance, "balance_joint_p": _r4(joint_p), "imbalanced_columns": imbalanced, "tot": tot, "heterogeneity": hetero, "outcomes": outcome_rows,
            "randomization_inference": ri, "mde_80pct_power": _r4(mde), "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Intention-to-treat effect of {tcol} on {', '.join(labs)} with 95% confidence intervals; primary ITT {primary['coef']} (95% CI {primary['ci_low']} to {primary['ci_high']}).",
            "chart_path": chart_path, "heterogeneity_chart_path": hetero_chart, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- regression_discontinuity

def _rd_fit(work: pd.DataFrame, c: float, h: float, kernel: str, order: int, covs: List[str], cluster: Optional[str], fuzzy: bool) -> Dict[str, Any]:
    """Local polynomial on each side of `c` within |running − c| ≤ h (full interaction of side × polynomial), kernel-weighted least squares."""
    x = work["__run"] - c
    inside = x.abs() <= h
    w = (1 - x.abs() / h) if kernel == "triangular" else pd.Series(1.0, index=work.index)
    inside &= w > 0
    sub = work[inside].copy()
    sub["__x"] = x[inside]
    sub["__above"] = (sub["__x"] >= 0).astype(float)
    sub["__above_x"] = sub["__above"] * sub["__x"]
    poly = ["__x", "__above_x"]
    if order == 2:
        sub["__x2"] = sub["__x"] ** 2; sub["__above_x2"] = sub["__above"] * sub["__x2"]
        poly += ["__x2", "__above_x2"]
    n_left, n_right = int((sub["__above"] == 0).sum()), int((sub["__above"] == 1).sum())
    if n_left < order + 2 or n_right < order + 2:
        raise InvalidArguments(f"too few observations within the bandwidth {_r4(h)} around {_r4(c)} (left {n_left}, right {n_right}); widen the bandwidth")
    preds = (["__above", "__treat"] if fuzzy else ["__above"]) + poly + covs
    X, yv, names, _ = _design(sub, "__out", preds, False)
    if "__above" not in X.columns or (fuzzy and "__treat" not in X.columns):
        raise InvalidArguments("the above-cutoff indicator (or the treatment) is constant within the bandwidth")
    groups = _cluster_ids(sub, X.index, cluster) if cluster else None
    wt = w[X.index].to_numpy(float)
    res: Dict[str, Any] = {"n_left": n_left, "n_right": n_right, "n": int(len(X)), "bandwidth": h}
    if fuzzy:
        Xs = X.drop(columns="__above"); Z = X.drop(columns="__treat")
        fs = _ols(Z.to_numpy(float), X["__treat"].to_numpy(float), groups, wt)
        zi = list(Z.columns).index("__above")
        res["first_stage"] = _estimate(fs, zi, "first stage: treatment jump at the cutoff")
        res["first_stage_t"] = _r4(fs["t"][zi])
        ivf = _iv(Xs.to_numpy(float), Z.to_numpy(float), yv.to_numpy(float), groups, wt)
        res["fit"] = ivf
        res["estimate"] = _estimate(ivf, list(Xs.columns).index("__treat"), "fuzzy RD (2SLS, jump in outcome / jump in treatment)")
        rf = _ols(Z.to_numpy(float), yv.to_numpy(float), groups, wt)
        res["reduced_form"] = _estimate(rf, zi, "reduced form: outcome jump at the cutoff")
        res["lines_fit"] = (rf, list(Z.columns))
    else:
        f = _ols(X.to_numpy(float), yv.to_numpy(float), groups, wt)
        res["fit"] = f
        res["estimate"] = _estimate(f, names.index("__above"), "sharp RD (jump at the cutoff)")
        res["lines_fit"] = (f, names)
    return res


def _rd_lines(fit: Dict[str, Any], names: List[str], h: float, order: int, c: float) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    b = {nm: float(v) for nm, v in zip(names, fit["beta"])}
    def f(x):
        above = 1.0 if x >= 0 else 0.0
        v = b.get("(intercept)", 0.0) + b.get("__above", 0.0) * above + b.get("__x", 0.0) * x + b.get("__above_x", 0.0) * above * x
        if order == 2:
            v += b.get("__x2", 0.0) * x * x + b.get("__above_x2", 0.0) * above * x * x
        return v
    left = [(c + x, f(x)) for x in np.linspace(-h, -1e-9, 25)]
    right = [(c + x, f(x)) for x in np.linspace(0, h, 25)]
    return left, right


def _rd_svg(title: str, subtitle: str, bx: List[float], by: List[float], left: List[Tuple[float, float]], right: List[Tuple[float, float]], c: float,
            xlabel: str, ylabel: str, w: int = 560, h: int = 360) -> str:
    L, R, T, B = 60, 18, 54, 48
    pw, ph = w - L - R, h - T - B
    xs = bx + [p[0] for p in left + right]; ys = by + [p[1] for p in left + right]
    if not xs:
        return svg._frame(w, h, title, "", subtitle)
    xt, yt = svg._nice_ticks(min(xs), max(xs), 6), svg._nice_ticks(min(ys), max(ys), 5)
    xlo, xhi = min(min(xs), xt[0]), max(max(xs), xt[-1]); ylo, yhi = min(min(ys), yt[0]), max(max(ys), yt[-1])
    xs_, ys_ = (xhi - xlo) or 1, (yhi - ylo) or 1
    px = lambda x: L + (x - xlo) / xs_ * pw
    py = lambda y: T + ph - (y - ylo) / ys_ * ph
    body = []
    for t in yt:
        body.append(f'<line x1="{L}" y1="{py(t):.1f}" x2="{L + pw}" y2="{py(t):.1f}" stroke="{svg.GRID}"/>')
        body.append(f'<text x="{L - 8}" y="{py(t) + 4:.1f}" text-anchor="end" fill="{svg.MUTED}">{svg._fmt(t)}</text>')
    for t in xt:
        body.append(f'<text x="{px(t):.1f}" y="{T + ph + 16}" text-anchor="middle" fill="{svg.MUTED}">{svg._fmt(t)}</text>')
    body.append(f'<line x1="{px(c):.1f}" y1="{T}" x2="{px(c):.1f}" y2="{T + ph}" stroke="{svg.INK}" stroke-opacity="0.5" stroke-dasharray="4 3"/>')
    for pts in (left, right):
        if pts:
            path = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)
            body.append(f'<polyline points="{path}" fill="none" stroke="{svg.DEEP}" stroke-width="2"/>')
    for x, y in zip(bx, by):
        body.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.2" fill="{svg.GOLD}" stroke="{svg.INK}" stroke-width="0.6"><title>{svg._fmt(x)}: {svg._fmt(y)}</title></circle>')
    body.append(f'<text x="{L + pw / 2:.0f}" y="{h - 8}" text-anchor="middle" fill="{svg.MUTED}">{svg._esc(xlabel)}</text>')
    body.append(f'<text transform="translate(14 {T + ph / 2:.0f}) rotate(-90)" text-anchor="middle" fill="{svg.MUTED}">{svg._esc(ylabel)}</text>')
    return svg._frame(w, h, title, "".join(body), subtitle)


def _rd_png(path: str, title: str, bx, by, left, right, c: float, xlabel: str, ylabel: str) -> None:
    from pathlib import Path
    from .charts import _mpl
    plt = _mpl(None); fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.scatter(bx, by, color="#ffd166", edgecolor="#35285e", linewidth=0.6, zorder=3, label="bin means")
    for pts in (left, right):
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color="#5b4ca3", linewidth=2)
    ax.axvline(c, color="#35285e", alpha=0.5, linestyle="--")
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel); ax.set_title(title); fig.tight_layout()
    fig.savefig(Path(path).with_suffix(".png"), dpi=160); plt.close(fig)


@tool("regression_discontinuity",
      "Regression discontinuity: local polynomial (order 1 or 2) fitted separately on each side of the cutoff within a bandwidth with triangular or uniform "
      "kernel weights; the jump at the cutoff with HC1 or cluster-robust SE, 95% CI and p; fuzzy RD by 2SLS when a binary `treatment` column is given "
      "(above-cutoff indicator as the instrument, first-stage jump and weak-first-stage warning); sensitivity at 0.5×, 1× and 2× the bandwidth; placebo "
      "cutoffs at the 25th and 75th percentiles; a simple density (manipulation) check; and a chart of binned means with the fitted lines.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string", "description": "numeric outcome column"},
          "running": {"type": "string", "description": "running (forcing) variable; treatment switches at the cutoff"},
          "cutoff": {"type": "number"},
          "treatment": {"type": ["string", "null"], "description": "binary actual treatment column for a fuzzy design; omit for a sharp design"},
          "bandwidth": {"type": ["number", "null"], "description": "half-width around the cutoff; default: 1 SD of the running variable within ±2 SD of the cutoff"},
          "kernel": {"type": ["string", "null"], "enum": ["triangular", "uniform", None], "description": "default triangular"},
          "polynomial_order": {"type": ["integer", "null"], "minimum": 1, "maximum": 2, "description": "1 (local linear, default) or 2"},
          "cluster": {"type": ["string", "null"]},
          "covariates": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 15}},
       "required": ["dataset_id", "outcome", "running", "cutoff"], "additionalProperties": False})
def regression_discontinuity(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    run = _col(df, args["running"], "running")
    if run == y:
        raise InvalidArguments("the running variable cannot also be the outcome")
    cutoff = float(args["cutoff"])
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment") if args.get("treatment") else None
    if tcol in (y, run):
        raise InvalidArguments("treatment must differ from the outcome and the running variable")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    covs = _covariates(df, args.get("covariates"), y, [run, tcol or ""])
    for c in covs:
        _not_pii(df, c, "covariate")
    kernel = args.get("kernel") or "triangular"
    order = int(args.get("polynomial_order") or 1)
    if order not in (1, 2):
        raise InvalidArguments("polynomial_order must be 1 or 2")
    bw = args.get("bandwidth")
    if bw is not None and (not math.isfinite(float(bw)) or float(bw) <= 0):
        raise InvalidArguments("bandwidth must be a positive number")
    notes: List[str] = []
    work = pd.DataFrame(index=df.index)
    work["__out"] = _numeric_outcome(df, y)
    work["__run"] = pd.to_numeric(df[run], errors="coerce").astype(float)
    if work["__run"].notna().sum() < 10:
        raise InvalidArguments(f"running variable '{run}' needs at least 10 numeric values")
    for c in covs:
        work[c] = df[c]
    if cluster:
        work[cluster] = df[cluster]
    fuzzy = tcol is not None
    if fuzzy:
        work["__treat"], levels = _binary(df, tcol, "treatment")
    else:
        levels = None
    ok = work["__out"].notna() & work["__run"].notna() & (work["__treat"].notna() if fuzzy else True)
    work = work[ok]
    r = work["__run"]
    if (r < cutoff).sum() == 0 or (r >= cutoff).sum() == 0:
        raise InvalidArguments(f"the running variable has no observations on one side of the cutoff {_r4(cutoff)} (min {_r4(r.min())}, max {_r4(r.max())})")
    if bw is None:
        s_all = float(r.std(ddof=1))
        window = (r - cutoff).abs() <= 2 * s_all
        h = float(r[window].std(ddof=1)) if window.sum() > 2 else s_all
        if not math.isfinite(h) or h <= 0:
            raise InvalidArguments("could not pick a bandwidth (the running variable has no spread near the cutoff); pass `bandwidth`")
        rule = f"rule of thumb: 1 SD of the running variable among observations within ±2 SD of the cutoff ({_r4(h)}); not a data-driven optimal bandwidth, pass `bandwidth` to override"
    else:
        h = float(bw); rule = "given by the caller"
    # The rule-of-thumb width is always reported, so a caller's bandwidth can be judged against it: a bandwidth of 1 on a
    # 0–100 score (27 Oct, scholarship case) fits 99 observations and reports a jump of 0.70 in a share.
    s_all = float(r.std(ddof=1)); window = (r - cutoff).abs() <= 2 * s_all
    h_rule = float(r[window].std(ddof=1)) if window.sum() > 2 else s_all
    ctx.progress(20, "local polynomial at the cutoff")
    main = _rd_fit(work, cutoff, h, kernel, order, covs, cluster, fuzzy)
    est = main["estimate"]
    narrow = bw is not None and math.isfinite(h_rule) and h_rule > 0 and (h < 0.25 * h_rule or min(main["n_left"], main["n_right"]) < 50)
    if narrow:
        notes.append(f"Narrow bandwidth: {_r4(h)} is {'under a quarter of' if h < 0.25 * h_rule else 'small against'} the rule-of-thumb width {_r4(h_rule)} and leaves "
                     f"{main['n_left']} and {main['n_right']} observations on either side; the estimate is unstable. Re-run without `bandwidth` (rule of thumb) or with a width near {_r4(h_rule)}.")
    if fuzzy and main["first_stage_t"] is not None and abs(main["first_stage_t"]) < WEAK_T:
        notes.append(f"Weak first stage: the treatment probability jumps by {main['first_stage']['coef']} at the cutoff with t = {main['first_stage_t']} (< {WEAK_T:g}); "
                     "the fuzzy RD estimate is imprecise and may be biased.")
    ctx.progress(40, "sensitivity")
    sens = []
    for mult in (0.5, 1.0, 2.0):
        try:
            rr = _rd_fit(work, cutoff, h * mult, kernel, order, covs, cluster, fuzzy)
            sens.append({"bandwidth_multiplier": mult, "bandwidth": _r4(h * mult), "estimate": rr["estimate"]["coef"], "se": rr["estimate"]["se"],
                         "ci_low": rr["estimate"]["ci_low"], "ci_high": rr["estimate"]["ci_high"], "p": rr["estimate"]["p"], "n_left": rr["n_left"], "n_right": rr["n_right"]})
        except InvalidArguments as e:
            sens.append({"bandwidth_multiplier": mult, "bandwidth": _r4(h * mult), "estimate": None, "se": None, "ci_low": None, "ci_high": None, "p": None,
                         "n_left": None, "n_right": None, "note": str(e)})
    ctx.progress(55, "placebo cutoffs")
    placebos = []
    for q, label in ((0.25, "25th percentile"), (0.75, "75th percentile")):
        pc = float(r.quantile(q))
        if abs(pc - cutoff) < 1e-12:
            placebos.append({"cutoff": _r4(pc), "where": label, "estimate": None, "note": "coincides with the real cutoff"}); continue
        side = work[(r < cutoff)] if pc < cutoff else work[(r > cutoff)]
        try:
            pr = _rd_fit(side, pc, h, kernel, order, covs, cluster, False)
            e = pr["estimate"]
            placebos.append({"cutoff": _r4(pc), "where": label, "side": "below the real cutoff" if pc < cutoff else "above the real cutoff", "estimate": e["coef"],
                             "se": e["se"], "ci_low": e["ci_low"], "ci_high": e["ci_high"], "p": e["p"], "n_left": pr["n_left"], "n_right": pr["n_right"],
                             "within_2se_of_zero": bool(e["se"] is not None and abs(e["coef"]) < 2 * e["se"])})
        except InvalidArguments as e:
            placebos.append({"cutoff": _r4(pc), "where": label, "estimate": None, "note": str(e)})
    # density check
    from scipy import stats as st
    bin_w = h / 5
    below = int(((r >= cutoff - bin_w) & (r < cutoff)).sum()); above = int(((r >= cutoff) & (r < cutoff + bin_w)).sum())
    dp = float(st.binomtest(below, below + above, 0.5).pvalue) if below + above > 0 else None
    density = {"bin_width": _r4(bin_w), "n_just_below": below, "n_just_above": above, "p": _r4(dp), "flagged": bool(dp is not None and dp < 0.05),
               "method": "simple density check: binomial test that counts just below and just above the cutoff are equal (McCrary-style idea, not the full McCrary or Cattaneo–Jansson–Ma test)"}
    # chart: binned means within ±2h and the fitted lines within ±h
    ctx.progress(75, "chart")
    lines_fit, lines_names = main["lines_fit"]
    if covs:
        try:
            plain = _rd_fit(work, cutoff, h, kernel, order, [], cluster, False)
            lines_fit, lines_names = plain["lines_fit"]
        except InvalidArguments:
            pass
    left, right = _rd_lines(lines_fit, lines_names, h, order, cutoff)
    span = work[(r - cutoff).abs() <= 2 * h]
    bx, by = [], []
    for lo_e, hi_e in ((cutoff - 2 * h, cutoff), (cutoff, cutoff + 2 * h)):
        edges = np.linspace(lo_e, hi_e, 11)
        for i in range(10):
            last = lo_e == cutoff and i == 9          # the outermost right bin keeps its upper edge
            m = (span["__run"] >= edges[i]) & ((span["__run"] <= edges[i + 1]) if last else (span["__run"] < edges[i + 1]))
            if m.sum() > 0:
                bx.append(float(span.loc[m, "__run"].mean())); by.append(float(span.loc[m, "__out"].mean()))
    out = ctx.project.new_artifact_dir("rd", f"{y}-at-{run}")
    title = f"{y} around the cutoff in {run}"
    sub = f"{'fuzzy' if fuzzy else 'sharp'} RD · jump {est['coef']} (95% CI {est['ci_low']} to {est['ci_high']}) · bandwidth {_r4(h)}, {kernel} kernel, order {order}" + (" · sampled" if sampled else "")
    chart = _rd_svg(title, sub, bx, by, left, right, cutoff, run, y)
    chart_path = str(ctx.project.write_text(out / "rd.svg", chart))
    _rd_png(chart_path, title, bx, by, left, right, cutoff, run, y)
    csv_path = _save_table(ctx, out, "sensitivity", pd.DataFrame(sens))
    _save_table(ctx, out, "placebos", pd.DataFrame(placebos))
    _save_table(ctx, out, "binned_means", pd.DataFrame({"running_bin_mean": bx, "outcome_mean": by}))
    precision, precision_note = _precision(est["coef"], est["ci_low"], est["ci_high"], est["se"], {"left of cutoff": main["n_left"], "right of cutoff": main["n_right"]})
    stable = all(s.get("estimate") is not None and est["se"] and abs(s["estimate"] - est["coef"]) < 2 * est["se"] for s in sens)
    plac_ok = all(p.get("within_2se_of_zero") for p in placebos if p.get("estimate") is not None)
    interp = [f"At the cutoff {_r4(cutoff)} in {run}, {y} jumps by {est['coef']} (95% CI {est['ci_low']} to {est['ci_high']}, {main['fit']['se_kind']} SE, p = {est['p']}; "
              f"{main['n_left']:,} observations within the bandwidth on the left and {main['n_right']:,} on the right; bandwidth {_r4(h)}, {kernel} kernel, polynomial order {order}): "
              f"{_sig(est['p'])}." + (f" WARNING, narrow bandwidth (rule of thumb {_r4(h_rule)}): treat this estimate as unstable." if narrow else ""), RD_DESIGN]
    if fuzzy:
        fsj = main["first_stage"]
        interp.append(f"Fuzzy design: the probability of treatment jumps by {fsj['coef']} at the cutoff (first-stage t = {main['first_stage_t']}); the reduced-form outcome jump is "
                      f"{main['reduced_form']['coef']} and the 2SLS estimate above is the effect for units whose treatment is switched by crossing the cutoff (compliers).")
    interp.append(("The estimate is stable across half, full and double bandwidth (all within 2 SE of the main estimate)." if stable else
                   "The estimate moves by more than 2 SE across half, full and double bandwidth: the result depends on the bandwidth.") +
                  (" Placebo cutoffs show no jump (within 2 SE of zero)." if plac_ok else " At least one placebo cutoff shows a jump larger than 2 SE: the running variable may have other discontinuities."))
    interp.append(f"Density check: {below} observations just below and {above} just above the cutoff (bin width {_r4(bin_w)}), p = {_r4(dp)}: " +
                  ("counts differ, which can signal manipulation of the running variable." if density["flagged"] else "no sign of bunching at the cutoff."))
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    notes.append(f"Bandwidth: {rule}.")
    notes.append(density["method"] + ".")
    if sampled:
        notes.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "regression_discontinuity", "dataset": ds.id, "outcome": y, "running": run, "cutoff": cutoff, "n": main["n"], "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "running": run, "cutoff": cutoff, "treatment": tcol, "treatment_levels": levels, "design": RD_DESIGN,
            "kind": "fuzzy" if fuzzy else "sharp", "estimate": est, "n": main["n"], "n_left": main["n_left"], "n_right": main["n_right"], "bandwidth": _r4(h), "rule_of_thumb_bandwidth": _r4(h_rule), "narrow_bandwidth": bool(narrow),
            "bandwidth_rule": rule, "kernel": kernel, "polynomial_order": order, "cluster": cluster, "covariates": covs, "se_kind": main["fit"]["se_kind"],
            "first_stage": main.get("first_stage"), "first_stage_t": main.get("first_stage_t"), "reduced_form": main.get("reduced_form"),
            "weak_first_stage": (bool(main["first_stage_t"] is not None and abs(main["first_stage_t"]) < WEAK_T) if fuzzy else None),
            "sensitivity": sens, "placebos": placebos, "density_check": density, "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Binned means of {y} against {run} with local polynomial fits on each side of the cutoff {_r4(cutoff)}; jump {est['coef']} (95% CI {est['ci_low']} to {est['ci_high']}).",
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- matching_estimate

def _propensity(X: np.ndarray, t: np.ndarray) -> Tuple[np.ndarray, str]:
    """Logistic propensity score on the design (with intercept). statsmodels Logit; sklearn LogisticRegression when it fails or separates."""
    engine = "statsmodels Logit"
    p = None
    try:
        import statsmodels.api as sm
        from statsmodels.tools.sm_exceptions import PerfectSeparationWarning, ConvergenceWarning
        with warnings.catch_warnings():
            warnings.simplefilter("error", PerfectSeparationWarning)
            warnings.simplefilter("ignore", ConvergenceWarning)
            warnings.simplefilter("ignore", RuntimeWarning)
            res = sm.Logit(t, X).fit(disp=0, maxiter=200)
        p = np.asarray(res.predict(X), float)
        if not np.all(np.isfinite(res.params)) or np.abs(res.params).max() > 25 or p.min() < 1e-8 or p.max() > 1 - 1e-8:
            raise ValueError("separation")
    except Exception:  # noqa: BLE001 - separation or convergence failure: ridge-regularised fallback, reported in `notes`
        from sklearn.linear_model import LogisticRegression
        Xc = X[:, 1:] if X.shape[1] > 1 else X
        mu, sd = Xc.mean(axis=0), Xc.std(axis=0); sd[sd == 0] = 1
        lr = LogisticRegression(C=1.0, max_iter=2000).fit((Xc - mu) / sd, t)
        p = lr.predict_proba((Xc - mu) / sd)[:, 1]
        engine = "sklearn LogisticRegression (L2, C = 1) because the statsmodels Logit separated or failed to converge"
    return np.clip(p, 1e-6, 1 - 1e-6), engine


def _whiten(Xc: np.ndarray) -> np.ndarray:
    """Transform so Euclidean distance equals the Mahalanobis distance on the covariates."""
    mu = Xc.mean(axis=0)
    S = np.cov(Xc - mu, rowvar=False, ddof=1) if Xc.shape[1] > 1 else np.array([[np.var(Xc, ddof=1)]])
    S = np.atleast_2d(S)
    vals, vecs = np.linalg.eigh(S)
    vals = np.where(vals > 1e-10 * max(vals.max(), 1e-12), vals, np.inf)
    with np.errstate(all="ignore"):
        W = vecs @ np.diag(1 / np.sqrt(vals)) @ vecs.T
        return (Xc - mu) @ W


def _match(F_from: np.ndarray, F_to: np.ndarray, k: int, caliper: Optional[float], replacement: bool, order: np.ndarray) -> List[List[int]]:
    """For each row of F_from (in `order`), up to k nearest rows of F_to within the caliper. Returns a list aligned with F_from (empty = unmatched)."""
    n_from, n_to = len(F_from), len(F_to)
    matches: List[List[int]] = [[] for _ in range(n_from)]
    if replacement:
        from sklearn.neighbors import NearestNeighbors
        kk = min(k, n_to)
        nn = NearestNeighbors(n_neighbors=kk).fit(F_to)
        dist, ind = nn.kneighbors(F_from)
        for i in range(n_from):
            matches[i] = [int(j) for dd, j in zip(dist[i], ind[i]) if caliper is None or dd <= caliper]
        return matches
    used = np.zeros(n_to, bool)
    for i in order:
        if used.all():
            break
        with np.errstate(all="ignore"):
            d2 = ((F_to - F_from[i]) ** 2).sum(axis=1)
        d2[used] = np.inf
        cand = np.argsort(d2, kind="stable")[:k]
        chosen = [int(j) for j in cand if np.isfinite(d2[j]) and (caliper is None or math.sqrt(d2[j]) <= caliper)]
        for j in chosen:
            used[j] = True
        matches[i] = chosen
    return matches


def _same_arm_variance(F: np.ndarray, yv: np.ndarray) -> np.ndarray:
    """σ²(x) per unit from its nearest same-arm neighbour: (Y_i − Y_l(i))² / 2 (Abadie–Imbens 2006 style, one match)."""
    from sklearn.neighbors import NearestNeighbors
    if len(F) < 2:
        return np.full(len(F), float(np.var(yv)) if len(yv) else 0.0)
    nn = NearestNeighbors(n_neighbors=2).fit(F)
    _, ind = nn.kneighbors(F)
    other = np.where(ind[:, 0] == np.arange(len(F)), ind[:, 1], ind[:, 0])
    return (yv - yv[other]) ** 2 / 2


def _matching_core(Xd: np.ndarray, yv: np.ndarray, t: np.ndarray, method: str, k: int, caliper_arg: Optional[float], replacement: bool, estimand: str,
                   want_variance: bool = True) -> Dict[str, Any]:
    """One full matching run on arrays: propensity (always, for the overlap summary), features, matches, estimate and the approximate variance."""
    p, engine = _propensity(Xd, t)
    lp = np.log(p / (1 - p))
    if method == "propensity":
        F = lp[:, None]
        caliper = float(caliper_arg) if caliper_arg is not None else 0.2 * float(np.std(lp, ddof=1))
        cal_text = f"{_r4(caliper)} on the logit of the propensity score" + ("" if caliper_arg is not None else " (0.2 × its SD)")
    else:
        F = _whiten(Xd[:, 1:])
        caliper = float(caliper_arg) if caliper_arg is not None else None
        cal_text = (f"{_r4(caliper)} in Mahalanobis distance" if caliper is not None else "none (nearest neighbours in Mahalanobis distance without a caliper)")
    ti, ci = np.flatnonzero(t == 1), np.flatnonzero(t == 0)
    order_t = np.argsort(-lp[ti], kind="stable")
    m_t = _match(F[ti], F[ci], k, caliper, replacement, order_t)
    m_c = _match(F[ci], F[ti], k, caliper, True, np.argsort(lp[ci], kind="stable")) if estimand == "ATE" else None
    n = len(t)
    K = np.zeros(n)                      # how often each unit serves as a match, weighted by 1/M of the unit it matches
    tau_t = np.full(len(ti), np.nan)
    for a, ms in enumerate(m_t):
        if ms:
            tau_t[a] = yv[ti[a]] - yv[ci[ms]].mean()
            K[ci[ms]] += 1.0 / len(ms)
    matched_t = ~np.isnan(tau_t)
    res: Dict[str, Any] = {"propensity": p, "logit": lp, "engine": engine, "caliper": caliper, "caliper_text": cal_text, "matched_treated": int(matched_t.sum()),
                           "n_treated": int(len(ti)), "n_control": int(len(ci)), "K": K, "matched_control_units": int((K[ci] > 0).sum())}
    if estimand == "ATT":
        if matched_t.sum() == 0:
            raise InvalidArguments("no treated unit found a control within the caliper; widen the caliper or drop it")
        est = float(np.nanmean(tau_t))
        wt = np.zeros(n); wt[ti[matched_t]] = 1.0; wc = np.zeros(n); wc[ci] = K[ci]
    else:
        tau_c = np.full(len(ci), np.nan)
        for a, ms in enumerate(m_c):
            if ms:
                tau_c[a] = yv[ti[ms]].mean() - yv[ci[a]]
                K[ti[ms]] += 1.0 / len(ms)
        matched_c = ~np.isnan(tau_c)
        res["matched_control"] = int(matched_c.sum())
        taus = np.concatenate([tau_t[matched_t], tau_c[matched_c]])
        if len(taus) == 0:
            raise InvalidArguments("no unit found a match within the caliper; widen the caliper or drop it")
        est = float(taus.mean())
        wt = np.zeros(n); wt[ti[matched_t]] = 1.0 + K[ti[matched_t]]; wc = np.zeros(n); wc[ci[matched_c]] = 1.0 + K[ci[matched_c]]
    res.update({"estimate": est, "w_treated": wt, "w_control": wc})
    if want_variance:
        sig2 = np.zeros(n)
        sig2[ti] = _same_arm_variance(F[ti], yv[ti]); sig2[ci] = _same_arm_variance(F[ci], yv[ci])
        if estimand == "ATT":
            N = matched_t.sum()
            var = (sig2[ti[matched_t]].sum() + ((K[ci] ** 2) * sig2[ci]).sum()) / N ** 2
        else:
            keep = np.concatenate([ti[matched_t], ci[~np.isnan(tau_c)]])
            N = len(keep)
            var = (((1.0 + K[keep]) ** 2) * sig2[keep]).sum() / N ** 2
        res["se"] = math.sqrt(max(var, 0.0))
    return res


def _std_diffs(Xc: np.ndarray, t: np.ndarray, wt: np.ndarray, wc: np.ndarray) -> Tuple[List[float], List[float]]:
    """Standardized differences before (unweighted) and after (match-weighted) matching, same pooled-SD denominator from the full sample."""
    before, after = [], []
    for j in range(Xc.shape[1]):
        a, b = Xc[t == 1, j], Xc[t == 0, j]
        sp = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2) if len(a) > 1 and len(b) > 1 else 0.0
        before.append((a.mean() - b.mean()) / sp if sp > 0 else 0.0)
        if wt.sum() > 0 and wc.sum() > 0:
            ma = float((wt * Xc[:, j]).sum() / wt.sum()); mb = float((wc * Xc[:, j]).sum() / wc.sum())
            after.append((ma - mb) / sp if sp > 0 else 0.0)
        else:
            after.append(float("nan"))
    return before, after


def _overlap_svg(title: str, subtitle: str, edges: np.ndarray, ct: np.ndarray, cc: np.ndarray, w: int = 560, h: int = 300) -> str:
    L, R, T, B = 50, 18, 54, 40
    pw, ph = w - L - R, h - T - B
    nb = len(ct); vmax = max(1, int(max(ct.max(), cc.max())))
    bw = pw / nb
    body = []
    for t in svg._nice_ticks(0, vmax, 4):
        yy = T + ph - t / vmax * ph
        body.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{L + pw}" y2="{yy:.1f}" stroke="{svg.GRID}"/>')
        body.append(f'<text x="{L - 8}" y="{yy + 4:.1f}" text-anchor="end" fill="{svg.MUTED}">{svg._fmt(t)}</text>')
    for i in range(nb):
        x0 = L + i * bw
        for off, cnt, col, lab in ((0.08, ct[i], svg.DEEP, "treated"), (0.52, cc[i], svg.GOLD, "control")):
            bh = cnt / vmax * ph
            body.append(f'<rect x="{x0 + off * bw:.1f}" y="{T + ph - bh:.1f}" width="{bw * 0.4:.1f}" height="{bh:.1f}" rx="2" fill="{col}"><title>{lab} {svg._fmt(edges[i])}–{svg._fmt(edges[i + 1])}: {int(cnt)}</title></rect>')
        if i % 2 == 0:
            body.append(f'<text x="{x0:.1f}" y="{T + ph + 14}" text-anchor="middle" fill="{svg.MUTED}">{svg._fmt(edges[i])}</text>')
    body.append(f'<text x="{L + pw:.1f}" y="{T + ph + 14}" text-anchor="middle" fill="{svg.MUTED}">{svg._fmt(edges[-1])}</text>')
    body.append(f'<rect x="{L + pw - 150}" y="{T - 6}" width="10" height="10" fill="{svg.DEEP}"/><text x="{L + pw - 136}" y="{T + 3}" fill="{svg.MUTED}">treated</text>')
    body.append(f'<rect x="{L + pw - 80}" y="{T - 6}" width="10" height="10" fill="{svg.GOLD}"/><text x="{L + pw - 66}" y="{T + 3}" fill="{svg.MUTED}">control</text>')
    body.append(f'<text x="{L + pw / 2:.0f}" y="{h - 6}" text-anchor="middle" fill="{svg.MUTED}">propensity score</text>')
    return svg._frame(w, h, title, "".join(body), subtitle)


def _love_svg(title: str, subtitle: str, labels: List[str], before: List[float], after: List[float], w: int = 560) -> str:
    n = len(labels); row = 24; h = 70 + n * row + 30
    L = 18 + min(200, max((len(str(l)) for l in labels), default=4) * 6.5); R = 24; pw = w - L - R
    vals = [v for v in before + after if v is not None and math.isfinite(v)]
    lo, hi = min(vals + [-MATCH_BALANCE]), max(vals + [MATCH_BALANCE]); span = (hi - lo) or 1; lo -= span * 0.05; hi += span * 0.05; span = hi - lo
    xpos = lambda v: L + (v - lo) / span * pw
    body = []
    for t in svg._nice_ticks(lo, hi, 6):
        if lo <= t <= hi:
            body.append(f'<line x1="{xpos(t):.1f}" y1="54" x2="{xpos(t):.1f}" y2="{54 + n * row}" stroke="{svg.GRID}"/>')
            body.append(f'<text x="{xpos(t):.1f}" y="{62 + n * row}" text-anchor="middle" fill="{svg.MUTED}">{svg._fmt(t)}</text>')
    for v in (-MATCH_BALANCE, 0.0, MATCH_BALANCE):
        body.append(f'<line x1="{xpos(v):.1f}" y1="54" x2="{xpos(v):.1f}" y2="{54 + n * row}" stroke="{svg.INK}" stroke-opacity="{0.6 if v == 0 else 0.35}" stroke-dasharray="3 3"/>')
    for i, (lab, b, a) in enumerate(zip(labels, before, after)):
        yy = 54 + i * row + row / 2
        body.append(f'<text x="{L - 8}" y="{yy + 4:.1f}" text-anchor="end" fill="{svg.INK}">{svg._esc(str(lab)[:30])}</text>')
        if b is not None and math.isfinite(b):
            body.append(f'<circle cx="{xpos(b):.1f}" cy="{yy:.1f}" r="4.5" fill="none" stroke="{svg.DEEP}" stroke-width="1.6"><title>{svg._esc(lab)} before: {b:.3f}</title></circle>')
        if a is not None and math.isfinite(a):
            body.append(f'<circle cx="{xpos(a):.1f}" cy="{yy:.1f}" r="4.5" fill="{svg.GOLD}" stroke="{svg.INK}" stroke-width="0.6"><title>{svg._esc(lab)} after: {a:.3f}</title></circle>')
    body.append(f'<text x="{L + pw / 2:.0f}" y="{h - 6}" text-anchor="middle" fill="{svg.MUTED}">standardized difference · hollow = before, filled = after matching · dashed = ±{MATCH_BALANCE}</text>')
    return svg._frame(w, h, title, "".join(body), subtitle)


def _matching_pngs(overlap_path: str, love_path: str, edges, ct, cc, labels, before, after) -> None:
    from pathlib import Path
    from .charts import _mpl
    plt = _mpl(None)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    centers = (edges[:-1] + edges[1:]) / 2; wd = (edges[1] - edges[0]) * 0.4
    ax.bar(centers - wd / 2, ct, width=wd, color="#5b4ca3", label="treated"); ax.bar(centers + wd / 2, cc, width=wd, color="#ffd166", label="control")
    ax.set_xlabel("propensity score"); ax.set_ylabel("units"); ax.set_title("Propensity-score overlap"); ax.legend(); fig.tight_layout()
    fig.savefig(Path(overlap_path).with_suffix(".png"), dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ys = np.arange(len(labels))
    ax.scatter(before, ys, facecolors="none", edgecolors="#5b4ca3", label="before"); ax.scatter(after, ys, color="#ffd166", edgecolors="#35285e", label="after")
    for v in (-MATCH_BALANCE, 0, MATCH_BALANCE):
        ax.axvline(v, color="#35285e", alpha=0.4, linestyle="--")
    ax.set_yticks(ys, labels); ax.set_xlabel("standardized difference"); ax.set_title("Covariate balance before and after matching"); ax.legend(); fig.tight_layout()
    fig.savefig(Path(love_path).with_suffix(".png"), dpi=160); plt.close(fig)


@tool("matching_estimate",
      "Matching estimate of a treatment effect under selection on observables: logistic propensity score (statsmodels Logit, sklearn fallback) with "
      "nearest-neighbour 1:k matching within a caliper, or Mahalanobis nearest neighbours on the covariates; ATT (default) or ATE (both directions, "
      "with replacement); Abadie–Imbens-style approximate SE (cluster bootstrap when `cluster` is given), 95% CI and p; balance before and after "
      "(standardized differences, |d| > 0.1 counted), common-support summary, propensity overlap and love plots.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string"},
          "treatment": {"type": "string", "description": "binary treatment column"},
          "covariates": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
          "method": {"type": ["string", "null"], "enum": ["propensity", "mahalanobis", None], "description": "default propensity"},
          "k": {"type": ["integer", "null"], "minimum": 1, "maximum": 5, "description": "matches per unit (default 1)"},
          "caliper": {"type": ["number", "null"], "description": "default 0.2 × SD of the logit of the propensity score; Mahalanobis: none unless given"},
          "replacement": {"type": ["boolean", "null"], "description": "match with replacement (default true)"},
          "estimand": {"type": ["string", "null"], "enum": ["ATT", "ATE", None], "description": "ATT (default) or ATE (needs replacement)"},
          "cluster": {"type": ["string", "null"], "description": "cluster column; switches the SE to a cluster bootstrap"}},
       "required": ["dataset_id", "outcome", "treatment", "covariates"], "additionalProperties": False})
def matching_estimate(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    tcol = _not_pii(df, _col(df, args["treatment"], "treatment"), "treatment")
    if tcol == y:
        raise InvalidArguments("the outcome cannot also be the treatment")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    covs = _covariates(df, args["covariates"], y, [tcol])
    for c in covs:
        _not_pii(df, c, "covariate")
    method = args.get("method") or "propensity"
    k = int(args.get("k") or 1)
    if not 1 <= k <= 5:
        raise InvalidArguments("k must be between 1 and 5")
    caliper_arg = args.get("caliper")
    if caliper_arg is not None and float(caliper_arg) <= 0:
        raise InvalidArguments("caliper must be a positive number")
    replacement = True if args.get("replacement") is None else bool(args["replacement"])
    estimand = args.get("estimand") or "ATT"
    if estimand == "ATE" and not replacement:
        raise InvalidArguments("estimand ATE matches in both directions and needs replacement: true")
    notes: List[str] = []
    treat, levels = _binary(df, tcol, "treatment")
    d = df.copy(); d["__out"] = _numeric_outcome(df, y); d["__treat"] = treat
    X, yv, names, kinds = _design(d, "__out", covs, False)
    keep = treat[X.index].notna()
    X, yv = X[keep], yv[keep]
    t = treat[X.index].to_numpy(float)
    if (t == 1).sum() < 5 or (t == 0).sum() < 5:
        raise InvalidArguments(f"each arm needs at least 5 complete rows (treated {int((t == 1).sum())}, control {int((t == 0).sum())})")
    Xd = X.to_numpy(float); yarr = yv.to_numpy(float)
    cov_names = [nm for nm in names if nm != "(intercept)"]
    ctx.progress(20, "matching")
    core = _matching_core(Xd, yarr, t, method, k, caliper_arg, replacement, estimand)
    est = core["estimate"]
    n = len(t)
    se_method = "Abadie–Imbens (2006)-style approximate SE: match-frequency weights with σ²(x) from one same-arm nearest neighbour; treats the effect as homogeneous across matched units"
    se = core["se"]
    if cluster:
        groups = _cluster_ids(d, X.index, cluster)
        uniq, inv = np.unique(groups, return_inverse=True)
        reps = BOOT_REPS_LARGE if n > LARGE_N else BOOT_REPS
        ctx.progress(40, f"cluster bootstrap ({reps} draws)")
        members = [np.flatnonzero(inv == g) for g in range(len(uniq))]
        rng = np.random.default_rng(0)
        ests = []
        for r in range(reps):
            if r % 25 == 0:
                ctx.check()
            pick = rng.integers(0, len(uniq), len(uniq))
            idx = np.concatenate([members[g] for g in pick])
            tb = t[idx]
            if (tb == 1).sum() < 2 or (tb == 0).sum() < 2:
                continue
            try:
                ests.append(_matching_core(Xd[idx], yarr[idx], tb, method, k, caliper_arg, replacement, estimand, want_variance=False)["estimate"])
            except InvalidArguments:
                continue
        se = float(np.std(ests, ddof=1)) if len(ests) > 1 else None
        se_method = f"cluster bootstrap over {len(uniq)} clusters of '{cluster}' ({reps} draws, seed 0, {len(ests)} completed; units re-scored and re-matched each draw)"
        if reps == BOOT_REPS_LARGE:
            notes.append(f"Bootstrap used {BOOT_REPS_LARGE} draws because n > {LARGE_N:,}.")
    effect = _z_estimate(estimand, est, se, int(core["matched_treated"] + (core.get("matched_control") or core["matched_control_units"])))
    # naive comparison and balance
    naive = float(yarr[t == 1].mean() - yarr[t == 0].mean())
    Xc = Xd[:, 1:]
    before, after = _std_diffs(Xc, t, core["w_treated"], core["w_control"])
    balance = [{"covariate": nm, "std_diff_before": _r4(b), "std_diff_after": _r4(a), "improved": bool(math.isfinite(a) and abs(a) < abs(b))}
               for nm, b, a in zip(cov_names, before, after)]
    n_imb_before = sum(1 for b in before if abs(b) > MATCH_BALANCE)
    n_imb_after = sum(1 for a in after if math.isfinite(a) and abs(a) > MATCH_BALANCE)
    p = core["propensity"]
    pt, pc = p[t == 1], p[t == 0]
    in_support = float(((pt >= pc.min()) & (pt <= pc.max())).mean())
    support = {"n_treated": core["n_treated"], "n_control": core["n_control"], "treated_matched": core["matched_treated"],
               "treated_dropped": core["n_treated"] - core["matched_treated"], "treated_matched_pct": _pct(core["matched_treated"] / core["n_treated"]),
               "control_units_used": core["matched_control_units"], "treated_within_control_score_range_pct": _pct(in_support),
               "propensity_range_treated": [_r4(pt.min()), _r4(pt.max())], "propensity_range_control": [_r4(pc.min()), _r4(pc.max())]}
    if estimand == "ATE":
        support["control_matched"] = core["matched_control"]; support["control_dropped"] = core["n_control"] - core["matched_control"]
    # charts
    ctx.progress(80, "writing")
    out = ctx.project.new_artifact_dir("matching", f"{y}-by-{tcol}")
    edges = np.linspace(0, 1, 11)
    ct, _ = np.histogram(pt, bins=edges); cc, _ = np.histogram(pc, bins=edges)
    overlap = _overlap_svg("Propensity-score overlap", f"{core['engine'].split(' (')[0]} · treated n = {len(pt):,}, control n = {len(pc):,}" + (" · sampled" if sampled else ""), edges, ct, cc)
    chart_path = str(ctx.project.write_text(out / "overlap.svg", overlap))
    love = _love_svg("Covariate balance before and after matching", f"{n_imb_before} of {len(cov_names)} covariates |d| > {MATCH_BALANCE} before, {n_imb_after} after", cov_names, before, after)
    love_path = str(ctx.project.write_text(out / "balance.svg", love))
    _matching_pngs(chart_path, love_path, edges, ct, cc, cov_names, before, after)
    csv_path = _save_table(ctx, out, "balance", pd.DataFrame(balance))
    precision, precision_note = _precision(effect["coef"], effect["ci_low"], effect["ci_high"], effect["se"], {"treated": core["matched_treated"], "control": core["matched_control_units"]})
    how = (f"{k} nearest control{'s' if k > 1 else ''} per treated unit on the propensity score{' with' if replacement else ' without'} replacement, caliper {core['caliper_text']}"
           if method == "propensity" else f"{k} nearest neighbour{'s' if k > 1 else ''} in Mahalanobis distance on the covariates{' with' if replacement else ' without'} replacement, caliper {core['caliper_text']}")
    interp = [f"The naive difference in {y} between treated and control is {_r4(naive)}. After matching ({how}), the {estimand} is {effect['coef']} "
              f"(95% CI {effect['ci_low']} to {effect['ci_high']}, SE {effect['se']}, p = {effect['p']}; {core['matched_treated']:,} of {core['n_treated']:,} treated units matched to "
              f"{core['matched_control_units']:,} distinct controls): {_sig(effect['p'])}.", MATCH_DESIGN,
              f"Balance: {n_imb_before} of {len(cov_names)} covariates had |standardized difference| > {MATCH_BALANCE} before matching and {n_imb_after} after" +
              (f" (largest after: {_r4(max(abs(a) for a in after if math.isfinite(a)))})." if any(math.isfinite(a) for a in after) else "."),
              f"Common support: {support['treated_matched_pct']}% of treated units found a match; {support['treated_within_control_score_range_pct']}% lie within the control group's propensity-score range" +
              (f"; {support['treated_dropped']} treated units were dropped, so the estimate describes the matched treated, not all treated." if support["treated_dropped"] else ".")]
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    notes.append(f"Standard error: {se_method}.")
    notes.append(f"Propensity score: {core['engine']}." + (" The score is used for the overlap summary; the matching itself is on Mahalanobis distance." if method == "mahalanobis" else ""))
    if not replacement:
        notes.append("Without replacement the treated units are matched greedily in descending order of the propensity score; later units get the controls left over.")
        if core["n_treated"] > core["n_control"] * 0.8:
            notes.append(f"The control pool ({core['n_control']:,}) is not much larger than the treated group ({core['n_treated']:,}); without replacement many treated units "
                         "get distant matches and the estimate stays confounded. Prefer replacement: true or a caliper.")
    if sampled:
        notes.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "matching_estimate", "dataset": ds.id, "outcome": y, "treatment": tcol, "n": n, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "treatment": tcol, "treatment_levels": levels, "design": MATCH_DESIGN, "method": method, "estimand": estimand, "k": k,
            "replacement": replacement, "caliper": _r4(core["caliper"]), "cluster": cluster, "n": n, "estimate": effect, "se_method": se_method, "naive_difference": _r4(naive),
            "balance": balance, "imbalanced_before": n_imb_before, "imbalanced_after": n_imb_after, "common_support": support, "propensity_engine": core["engine"],
            "covariate_kinds": kinds, "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Propensity-score overlap between treated and control units; matched {estimand} on {y} = {effect['coef']} (95% CI {effect['ci_low']} to {effect['ci_high']}).",
            "chart_path": chart_path, "balance_chart_path": love_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- instrumental_variables

def _pyfixest_iv(work: pd.DataFrame, exog: List[str], endog: List[str], inst: List[str], cluster: bool) -> Dict[str, Dict[str, float]]:
    import pyfixest as pf  # noqa: WPS433
    rhs = " + ".join(exog) if exog else "1"
    formula = f"y ~ {rhs} | {' + '.join(endog)} ~ {' + '.join(inst)}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = pf.feols(formula, data=work, vcov=({"CRV1": "cl"} if cluster else "hetero"))
    coef, se, pv, ci = m.coef(), m.se(), m.pvalue(), m.confint()
    out = {}
    for name in coef.index:
        out[str(name)] = {"coef": float(coef[name]), "se": float(se[name]), "p": float(pv[name]), "ci_low": float(ci.loc[name].iloc[0]), "ci_high": float(ci.loc[name].iloc[1])}
    return out


@tool("instrumental_variables",
      "Two-stage least squares: outcome on 1–3 endogenous regressors instrumented by at least as many instruments, plus exogenous covariates; HC1 or "
      "cluster-robust SEs (pyfixest `feols`, manual 2SLS with the IV sandwich as fallback); first-stage table with F per endogenous (weak below 10; "
      "> 104 noted for one instrument), Wu–Hausman endogeneity test, Sargan over-identification test when instruments exceed endogenous, OLS comparison, "
      "LATE wording when the endogenous variable is binary, and a coefficient chart.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "outcome": {"type": "string"},
          "endogenous": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
          "instruments": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
          "exogenous": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 15, "description": "exogenous covariates (included in both stages)"},
          "cluster": {"type": ["string", "null"]}},
       "required": ["dataset_id", "outcome", "endogenous", "instruments"], "additionalProperties": False})
def instrumental_variables(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    df, ds, sampled = _load(ctx, args["dataset_id"])
    y = _col(df, args["outcome"], "outcome")
    endog = [_not_pii(df, _col(df, c, "endogenous"), "endogenous") for c in args["endogenous"]]
    inst = [_not_pii(df, _col(df, c, "instrument"), "instrument") for c in args["instruments"]]
    exog = _covariates(df, args.get("exogenous"), y, endog + inst)
    for c in exog:
        _not_pii(df, c, "exogenous covariate")
    if len(set(endog)) != len(endog) or len(set(inst)) != len(inst):
        raise InvalidArguments("endogenous and instrument columns must be distinct")
    if y in endog or y in inst:
        raise InvalidArguments("the outcome cannot be an endogenous variable or an instrument")
    if set(endog) & set(inst):
        raise InvalidArguments(f"{sorted(set(endog) & set(inst))} cannot be both endogenous and an instrument")
    if len(inst) < len(endog):
        raise InvalidArguments(f"{len(endog)} endogenous variables need at least {len(endog)} instruments; {len(inst)} given (order condition)")
    cluster = _col(df, args["cluster"], "cluster") if args.get("cluster") else None
    notes: List[str] = []
    d = df.copy(); d["__out"] = _numeric_outcome(df, y)
    X, yv, names, kinds = _design(d, "__out", exog + endog + inst, False)
    for c in endog:
        if c not in X.columns:
            raise InvalidArguments(f"endogenous variable '{c}' must be a numeric column that varies in the complete rows" + (f" (it is {kinds.get(c)})" if kinds.get(c, "").startswith("categorical") else ""))
    inst_cols = [c for c in X.columns if c in inst or any(str(c).startswith(f"{z}[") for z in inst)]
    if len(inst_cols) < len(endog):
        raise InvalidArguments("after dummy coding, fewer instrument columns than endogenous variables remain")
    exog_cols = [c for c in X.columns if c not in endog and c not in inst_cols]       # includes (intercept)
    Xs = X[exog_cols + endog]; Z = X[exog_cols + inst_cols]
    groups = _cluster_ids(d, X.index, cluster) if cluster else None
    yarr = yv.to_numpy(float)
    ctx.progress(20, "two-stage least squares")
    manual = _iv(Xs.to_numpy(float), Z.to_numpy(float), yarr, groups)
    engine = "manual 2SLS (numpy) with the IV sandwich " + manual["se_kind"]
    coefs: Dict[str, Dict[str, Any]] = {}
    try:
        work = pd.DataFrame({"y": yarr}, index=X.index)
        safe = {}
        for i, c in enumerate(exog_cols):
            if c != "(intercept)":
                safe[c] = f"w{i}"; work[f"w{i}"] = X[c].to_numpy(float)
        for i, c in enumerate(endog):
            safe[c] = f"x{i}"; work[f"x{i}"] = X[c].to_numpy(float)
        for i, c in enumerate(inst_cols):
            safe[c] = f"z{i}"; work[f"z{i}"] = X[c].to_numpy(float)
        if cluster:
            work["cl"] = groups
        res = _pyfixest_iv(work, [safe[c] for c in exog_cols if c != "(intercept)"], [safe[c] for c in endog], [safe[c] for c in inst_cols], bool(cluster))
        back = {v: k for k, v in safe.items()}; back["Intercept"] = "(intercept)"
        for nm, v in res.items():
            coefs[back.get(nm, nm)] = {"term": back.get(nm, nm), "coef": _r4(v["coef"]), "se": _r4(v["se"]), "ci_low": _r4(v["ci_low"]), "ci_high": _r4(v["ci_high"]), "p": _r4(v["p"]), "n": int(manual["n"])}
        if any(c not in coefs for c in endog) or any(not np.isfinite([coefs[c]["coef"] or np.nan, coefs[c]["se"] or np.nan]).all() for c in endog):
            raise RuntimeError("pyfixest returned no finite coefficient for an endogenous variable")
        engine = "pyfixest feols 2SLS, vcov " + ("CRV1 by " + cluster if cluster else "hetero (HC1)")
    except ImportError:
        notes.append("pyfixest is not installed; the 2SLS was computed manually with the IV sandwich standard errors")
        coefs = {}
    except Exception as e:  # noqa: BLE001 - fall back and say so
        notes.append(f"pyfixest raised {type(e).__name__}; the 2SLS was computed manually with the IV sandwich standard errors")
        coefs = {}
    if not coefs:
        coefs = {nm: _estimate(manual, i, nm) for i, nm in enumerate(list(Xs.columns))}
    # diagnostics on the same rows (numpy)
    ctx.progress(50, "first stage and tests")
    Zarr, Xa = Z.to_numpy(float), Xs.to_numpy(float)
    exog_arr = X[exog_cols].to_numpy(float)
    first_stage, vhats = [], []
    from scipy import stats as st
    inst_idx = [list(Z.columns).index(c) for c in inst_cols]
    for c in endog:
        xj = X[c].to_numpy(float)
        fs = _ols(Zarr, xj, groups)
        rs = _ols(exog_arr, xj, None)
        q, dfr = len(inst_cols), max(1, len(xj) - Zarr.shape[1])
        F = ((rs["rss"] - fs["rss"]) / q) / (fs["rss"] / dfr) if fs["rss"] > 0 else float("inf")
        F_rob, p_rob = _wald(fs, inst_idx)
        partial_r2 = 1 - fs["rss"] / rs["rss"] if rs["rss"] > 0 else None
        vhats.append(fs["resid"])
        row = {"endogenous": c, "F": _r4(F), "F_p": _r4(float(st.f.sf(F, q, dfr))) if math.isfinite(F) else None, "F_robust": _r4(F_rob), "F_robust_p": _r4(p_rob),
               "partial_r2": _r4(partial_r2), "n_instruments": q, "weak": bool(F < WEAK_F),
               "instruments": [{"term": z, "coef": _r4(fs["beta"][i]), "se": _r4(fs["se"][i]), "p": _r4(fs["p"][i])} for z, i in zip(inst_cols, inst_idx)]}
        first_stage.append(row)
    weak = [r["endogenous"] for r in first_stage if r["weak"]]
    V = np.column_stack(vhats)
    wh = _ols(np.column_stack([Xa, V]), yarr, groups)
    _, wu_p = _wald(wh, list(range(Xa.shape[1], Xa.shape[1] + V.shape[1])))
    sargan = None
    if len(inst_cols) > len(endog):
        u = manual["resid"]
        aux = _ols(Zarr, u, None)
        tss = float(((u - u.mean()) ** 2).sum())
        r2 = 1 - aux["rss"] / tss if tss > 0 else 0.0
        stat = len(u) * r2; dof = len(inst_cols) - len(endog)
        sargan = {"statistic": _r4(stat), "df": dof, "p": _r4(float(st.chi2.sf(stat, dof))), "method": "Sargan (homoskedastic) n·R² of the 2SLS residuals on the instruments; not robust to clustering"}
    ols = _ols(Xa, yarr, groups)
    ols_terms = {nm: _estimate(ols, i, nm) for i, nm in enumerate(list(Xs.columns))}
    binary = all(set(np.unique(X[c].to_numpy(float))) <= {0.0, 1.0} for c in endog)
    rows = []
    for c in endog:
        iv_c, o_c, fsr = coefs[c], ols_terms[c], next(r for r in first_stage if r["endogenous"] == c)
        rows.append({"endogenous": c, "iv_coef": iv_c["coef"], "iv_se": iv_c["se"], "iv_ci_low": iv_c["ci_low"], "iv_ci_high": iv_c["ci_high"], "iv_p": iv_c["p"],
                     "ols_coef": o_c["coef"], "ols_se": o_c["se"], "ols_p": o_c["p"], "first_stage_F": fsr["F"], "weak_instruments": fsr["weak"]})
    ctx.progress(85, "writing")
    out = ctx.project.new_artifact_dir("iv", f"{y}-on-{'-'.join(endog)}")
    labs, vals, lows, highs = [], [], [], []
    for r in rows:
        labs += [f"{r['endogenous']} (2SLS)", f"{r['endogenous']} (OLS)"]
        vals += [r["iv_coef"], r["ols_coef"]]; lows += [r["iv_ci_low"], ols_terms[r["endogenous"]]["ci_low"]]; highs += [r["iv_ci_high"], ols_terms[r["endogenous"]]["ci_high"]]
    chart = svg.dots(f"{y}: 2SLS and OLS coefficients", labs, vals, lows, highs, subtitle=f"{engine} · 95% CIs · n = {manual['n']:,}" + (" · sampled" if sampled else ""))
    chart_path = str(ctx.project.write_text(out / "iv.svg", chart))
    _office_png(chart_path, "dots", f"{y}: 2SLS and OLS coefficients", labs, vals, lows, highs)
    csv_path = _save_table(ctx, out, "coefficients", pd.DataFrame(rows))
    main = coefs[endog[0]]
    precision, precision_note = _precision(main["coef"], main["ci_low"], main["ci_high"], main["se"], {"observations": int(manual["n"])})
    fs0 = first_stage[0]
    word = "local average treatment effect (LATE) for compliers, the units whose treatment status the instrument changes" if binary else "effect"
    interp = [f"Instrumenting {', '.join(endog)} with {', '.join(inst)}, the 2SLS {word} of {endog[0]} on {y} is {main['coef']} (95% CI {main['ci_low']} to {main['ci_high']}, "
              f"{manual['se_kind'] if 'manual' in engine else engine.split('vcov ')[-1]} SE, p = {main['p']}, n = {manual['n']:,}) against an OLS estimate of {ols_terms[endog[0]]['coef']}: {_sig(main['p'])}.",
              IV_DESIGN + " Read the 2SLS estimate as causal only if you can argue the exclusion restriction for this instrument.",
              f"First stage: F = {fs0['F']} for {endog[0]} ({fs0['n_instruments']} instrument{'s' if fs0['n_instruments'] > 1 else ''}, partial R² = {fs0['partial_r2']}): " +
              (f"weak (below {WEAK_F:g}); 2SLS is biased towards OLS and the confidence interval is unreliable." if fs0["weak"] else
               (f"strong; above {STRONG_F:g}, the rule of thumb (Lee, McCrary, Moreira and Porter 2022) under which the usual 2SLS t-test keeps its size with one instrument."
                if (fs0["F"] is not None and fs0["F"] > STRONG_F and fs0["n_instruments"] == 1 and len(endog) == 1) else f"above the rule-of-thumb threshold of {WEAK_F:g}.")),
              f"Wu–Hausman test p = {_r4(wu_p)}: " + ("OLS and 2SLS differ, consistent with endogeneity of the regressor." if (wu_p is not None and wu_p < 0.05) else
                                                     "no evidence that OLS and 2SLS differ; OLS may be adequate if the instrument is valid.")]
    if sargan:
        interp.append(f"Sargan over-identification test p = {sargan['p']} ({sargan['df']} extra instrument{'s' if sargan['df'] > 1 else ''}): " +
                      ("the instruments give inconsistent estimates; at least one is likely invalid." if (sargan["p"] is not None and sargan["p"] < 0.05) else
                       "no evidence against the over-identifying restrictions (this cannot validate the instruments jointly)."))
    if len(endog) > 1 and len(weak) < len(endog):
        interp.append("Other first stages: " + "; ".join(f"{r['endogenous']} F = {r['F']}" for r in first_stage[1:]) + ".")
    if precision_note:
        interp.append(precision_note); notes.append(precision_note)
    if weak:
        notes.append(f"Weak instruments for {', '.join(weak)} (first-stage F < {WEAK_F:g}).")
    notes.append(f"Engine: {engine}.")
    if sampled:
        notes.append(f"Computed on the first {len(df):,} rows.")
    ctx.project.audit({"tool": "instrumental_variables", "dataset": ds.id, "outcome": y, "endogenous": endog, "instruments": inst, "n": manual["n"], "artifact": str(out)})
    ctx.progress(100, "done")
    return {"dataset_id": ds.id, "outcome": y, "endogenous": endog, "instruments": inst, "exogenous": exog, "cluster": cluster, "design": IV_DESIGN, "n": int(manual["n"]),
            "engine": engine, "se_kind": manual["se_kind"], "binary_endogenous": bool(binary), "estimate": main, "coefficients": [coefs[c] for c in list(Xs.columns) if c in coefs],
            "table": rows, "ols": [ols_terms[c] for c in list(Xs.columns)], "first_stage": first_stage, "weak_instruments": weak, "wu_hausman_p": _r4(wu_p),
            "overidentification": sargan, "precision": precision, "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"2SLS and OLS coefficients of {', '.join(endog)} on {y} with 95% confidence intervals; 2SLS {main['coef']} (95% CI {main['ci_low']} to {main['ci_high']}).",
            "chart_path": chart_path, "csv_path": csv_path, "sampled": sampled, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}
