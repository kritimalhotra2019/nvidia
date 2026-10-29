"""Impact-evaluation designs (randomized_trial, regression_discontinuity, matching_estimate, instrumental_variables) on seeded synthetic data with known truths."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from squipi_worker import tools  # noqa: F401 - registers the tools
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments

SEED = 11
ITT, COMPLIANCE = 4.0, 0.7            # TOT = ITT / compliance ≈ 5.714
RD_JUMP, RD_CUTOFF, RD_COMPLIANCE = 3.0, 50.0, 0.6
MATCH_EFFECT = 2.0
IV_EFFECT = 1.5


def _rct(rng, n=2000):
    block = rng.integers(0, 4, n)
    treatment = rng.integers(0, 2, n)
    female = rng.integers(0, 2, n)
    age = rng.normal(40, 10, n)
    baseline = rng.normal(50, 10, n)
    takeup = np.where(treatment == 1, (rng.random(n) < COMPLIANCE).astype(int), 0)
    tot = ITT / COMPLIANCE
    effect = tot + (tot / 2) * np.where(female == 1, 1.0, -1.0)      # men ≈ 2.86, women ≈ 8.57 for compliers → ITT 2 / 6, average 4
    y1 = 20 + 0.5 * baseline + effect * takeup + rng.normal(0, 5, n)
    y2 = 10 + 3.0 * treatment + rng.normal(0, 5, n)
    y_null = 5 + rng.normal(0, 5, n)
    village = rng.integers(0, 40, n)
    v_treat = rng.integers(0, 2, 40)[village]
    v_effect = rng.normal(0, 2, 40)[village]
    yc = 20 + v_effect + ITT * v_treat + rng.normal(0, 5, n)
    return pd.DataFrame({"id": np.arange(n), "block": block, "treatment": treatment, "female": female, "age": age, "baseline": baseline, "takeup": takeup,
                         "y1": y1, "y2": y2, "y_null": y_null, "village": village, "village_treat": v_treat, "yc": yc, "arm3": rng.choice(["a", "b", "c"], n)})


def _rd(rng, n=3000):
    r = rng.uniform(0, 100, n)
    x = r - RD_CUTOFF
    above = (r >= RD_CUTOFF).astype(int)
    smooth = 10 + 0.1 * x + 0.002 * x ** 2
    y = smooth + RD_JUMP * above + rng.normal(0, 2, n)
    d = above * (rng.random(n) < RD_COMPLIANCE).astype(int)
    yf = smooth + RD_JUMP * d + rng.normal(0, 2, n)
    return pd.DataFrame({"score": r, "y": y, "treated": d, "yf": yf, "region": rng.integers(0, 25, n)})


def _match(rng, n=2000):
    x1, x2 = rng.normal(0, 1, n), rng.normal(0, 1, n)
    p = 1 / (1 + np.exp(-(0.8 * x1 + 0.8 * x2)))
    t = (rng.random(n) < p).astype(int)
    y = MATCH_EFFECT * t + 2 * x1 + 2 * x2 + rng.normal(0, 1, n)
    return pd.DataFrame({"x1": x1, "x2": x2, "t": t, "y": y, "site": rng.integers(0, 30, n)})


def _iv(rng, n=2000):
    z = rng.normal(0, 1, n); u = rng.normal(0, 1, n); z2 = rng.normal(0, 1, n)
    x = 0.5 * z + 0.3 * z2 + u + 0.5 * rng.normal(0, 1, n)
    x_weak = 0.05 * z + u + 0.5 * rng.normal(0, 1, n)
    w = rng.normal(0, 1, n)
    y = IV_EFFECT * x + 1.5 * u + 0.5 * w + rng.normal(0, 1, n)
    y_weak = IV_EFFECT * x_weak + 1.5 * u + rng.normal(0, 1, n)
    d = (x > 0).astype(int)
    y_bin = 2.0 * d + 1.0 * u + rng.normal(0, 1, n)
    return pd.DataFrame({"z": z, "z2": z2, "x": x, "x_weak": x_weak, "w": w, "y": y, "y_weak": y_weak, "d": d, "y_bin": y_bin, "firm": rng.integers(0, 50, n)})


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    rng = np.random.default_rng(SEED)
    base = tmp_path_factory.mktemp("designs")
    p = Project(base / "project", AllowList())
    ctx = JobContext(p, lambda *a: None, lambda: False)
    frames = {"rct": _rct(rng), "rd": _rd(rng), "match": _match(rng), "iv": _iv(rng)}
    ids = {}
    for k, frame in frames.items():
        path = base / f"{k}.csv"
        frame.to_csv(path, index=False)
        p.allow.allow(path)
        ids[k] = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    return {"ctx": ctx, "ids": ids, "frames": frames, "base": base}


def _json_ok(r):
    text = json.dumps(r, default=str)
    back = json.loads(text)
    assert back["summary_text"] and back["interpretation"] and back["design"] and back["n"] > 0
    assert "NaN" not in text and "Infinity" not in text
    return back


# ---------- randomized_trial

def test_rct_recovers_itt_tot_heterogeneity_and_adjusts_outcomes(world):
    ctx, ds = world["ctx"], world["ids"]["rct"]
    r = registry.call("randomized_trial", {"dataset_id": ds, "outcome": "y1", "treatment": "treatment", "covariates": ["age", "female"], "baseline_outcome": "baseline",
                                           "strata": "block", "compliance": "takeup", "subgroups": ["female"], "outcomes": ["y2", "y_null"]}, ctx)
    u, a = r["itt_unadjusted"], r["itt_adjusted"]
    assert abs(u["coef"] - ITT) < 2 * u["se"] and abs(a["coef"] - ITT) < 2 * a["se"] and a["se"] < u["se"]
    assert a["specification"].startswith("ANCOVA") and "block fixed effects" in a["specification"] and r["itt"] == a
    tot = r["tot"]
    assert abs(tot["estimate"]["coef"] - ITT / COMPLIANCE) < 2 * tot["estimate"]["se"]
    assert abs(tot["takeup_treated_pct"] - 70) < 4 and tot["takeup_control_pct"] == 0 and abs(tot["compliance_share_pct"] - 70) < 4 and not tot["weak_first_stage"]
    assert tot["estimate"]["coef"] > a["coef"]                     # TOT scales the ITT up by 1/compliance
    bal = {row["column"]: row for row in r["balance"]}
    assert set(bal) == {"age", "female", "baseline"} and all(row["flag"] == "balanced" for row in bal.values()) and r["balance_joint_p"] > 0.01
    het = {row["level"]: row for row in r["heterogeneity"]}
    assert set(het) == {"0", "1"} and het["1"]["effect"] > het["0"]["effect"] and het["0"]["interaction_p"] < 0.01
    assert abs(het["1"]["effect"] - 6.0) < 2.5 * het["1"]["se"] and abs(het["0"]["effect"] - 2.0) < 2.5 * het["0"]["se"]
    outs = {row["outcome"]: row for row in r["outcomes"]}
    assert set(outs) == {"y1", "y2", "y_null"} and outs["y1"]["primary"] and outs["y1"]["coef"] == a["coef"]
    assert outs["y1"]["significant_bh_5pct"] and outs["y2"]["significant_bh_5pct"] and not outs["y_null"]["significant_bh_5pct"]
    for row in outs.values():
        assert row["q_bh"] >= row["p"] - 1e-9 and row["p_bonferroni"] >= row["q_bh"] - 1e-9 and row["p_bonferroni"] <= 1
    # BH by hand on the three raw p-values
    ps = sorted((row["p"], row["outcome"]) for row in outs.values())
    q_hand = {}
    prev = 1.0
    for rank in range(3, 0, -1):
        p, name = ps[rank - 1]
        prev = min(prev, p * 3 / rank)
        q_hand[name] = prev
    for name, q in q_hand.items():
        assert outs[name]["q_bh"] == pytest.approx(q, rel=1e-3, abs=1e-6)
    ri = r["randomization_inference"]
    assert ri["draws"] == 1000 and ri["p"] < 0.01 and ri["p"] >= 0.0009 and "within strata" in ri["scheme"]
    assert r["mde_80pct_power"] == pytest.approx(2.8 * a["se"], rel=1e-3)
    assert "randomisation was implemented" in r["design"] and r["design"] in r["interpretation"]
    assert not any("Precision:" in n for n in r["notes"])
    assert Path(r["chart_path"]).exists() and Path(r["heterogeneity_chart_path"]).exists() and Path(r["csv_path"]).exists()
    _json_ok(r)


def test_rct_cluster_randomised_permutes_clusters(world):
    ctx, ds = world["ctx"], world["ids"]["rct"]
    r = registry.call("randomized_trial", {"dataset_id": ds, "outcome": "yc", "treatment": "village_treat", "cluster": "village"}, ctx)
    e = r["itt"]
    assert r["se_kind"].startswith("cluster-robust (40 clusters)") and r["n_clusters"] == 40 and r["itt_adjusted"] is None
    assert abs(e["coef"] - ITT) < 2 * e["se"]
    assert "across 40 clusters" in r["randomization_inference"]["scheme"] and r["randomization_inference"]["p"] < 0.05
    assert not any("Only" in n for n in r["notes"])
    # few clusters: warning
    small = world["frames"]["rct"][world["frames"]["rct"]["village"] < 8]
    path = world["base"] / "few_clusters.csv"
    small.to_csv(path, index=False); ctx.project.allow.allow(path)
    sid = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    f = registry.call("randomized_trial", {"dataset_id": sid, "outcome": "yc", "treatment": "village_treat", "cluster": "village", "randomization_inference": False}, ctx)
    assert any("fewer than 20 clusters" in n for n in f["notes"]) and f["randomization_inference"] is None
    _json_ok(r)


def test_rct_refuses_bad_inputs(world):
    ctx, ds = world["ctx"], world["ids"]["rct"]
    with pytest.raises(InvalidArguments, match="binary.*a, b, c"):
        registry.call("randomized_trial", {"dataset_id": ds, "outcome": "y1", "treatment": "arm3"}, ctx)
    one = world["frames"]["rct"].copy(); one["one_cluster"] = 1
    path = world["base"] / "one_cluster.csv"; one.to_csv(path, index=False); ctx.project.allow.allow(path)
    sid = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    with pytest.raises(InvalidArguments, match="fewer than 2 groups"):
        registry.call("randomized_trial", {"dataset_id": sid, "outcome": "y1", "treatment": "treatment", "cluster": "one_cluster"}, ctx)
    with pytest.raises(InvalidArguments, match="outcome cannot also be the treatment"):
        registry.call("randomized_trial", {"dataset_id": ds, "outcome": "treatment", "treatment": "treatment"}, ctx)


# ---------- regression_discontinuity

def test_sharp_rd_recovers_the_jump(world):
    ctx, ds = world["ctx"], world["ids"]["rd"]
    r = registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF}, ctx)
    e = r["estimate"]
    assert r["kind"] == "sharp" and abs(e["coef"] - RD_JUMP) < 2 * e["se"] and e["p"] < 0.001
    assert r["bandwidth"] > 0 and "rule of thumb" in r["bandwidth_rule"] and r["kernel"] == "triangular" and r["polynomial_order"] == 1
    assert r["n_left"] > 50 and r["n_right"] > 50 and r["n"] == r["n_left"] + r["n_right"]
    assert [s["bandwidth_multiplier"] for s in r["sensitivity"]] == [0.5, 1.0, 2.0]
    assert all(abs(s["estimate"] - RD_JUMP) < 2.5 * s["se"] for s in r["sensitivity"])
    assert r["sensitivity"][1]["estimate"] == e["coef"]
    assert len(r["placebos"]) == 2 and all(p["within_2se_of_zero"] for p in r["placebos"])
    assert r["placebos"][0]["cutoff"] < RD_CUTOFF < r["placebos"][1]["cutoff"]
    assert not r["density_check"]["flagged"] and r["density_check"]["n_just_below"] > 0 and "simple density check" in r["density_check"]["method"]
    assert "continuity" in r["design"] and r["design"] in r["interpretation"]
    assert Path(r["chart_path"]).exists() and Path(r["chart_path"]).with_suffix(".png").exists() and Path(r["csv_path"]).exists()
    # explicit bandwidth, uniform kernel, quadratic, clustered
    q = registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF, "bandwidth": 20, "kernel": "uniform",
                                                   "polynomial_order": 2, "cluster": "region"}, ctx)
    assert q["bandwidth"] == 20 and q["bandwidth_rule"] == "given by the caller" and q["se_kind"].startswith("cluster-robust (25 clusters)")
    assert abs(q["estimate"]["coef"] - RD_JUMP) < 2.5 * q["estimate"]["se"]
    _json_ok(r); _json_ok(q)


def test_fuzzy_rd_scales_by_the_first_stage(world):
    ctx, ds = world["ctx"], world["ids"]["rd"]
    r = registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "yf", "running": "score", "cutoff": RD_CUTOFF, "treatment": "treated"}, ctx)
    e, fs = r["estimate"], r["first_stage"]
    assert r["kind"] == "fuzzy" and abs(fs["coef"] - RD_COMPLIANCE) < 2 * fs["se"] and r["first_stage_t"] > 3 and r["weak_first_stage"] is False
    assert abs(e["coef"] - RD_JUMP) < 2 * e["se"]
    assert abs(r["reduced_form"]["coef"] - RD_JUMP * RD_COMPLIANCE) < 2 * r["reduced_form"]["se"]
    assert e["coef"] == pytest.approx(r["reduced_form"]["coef"] / fs["coef"], rel=0.02)      # Wald ratio
    assert any("compliers" in s for s in r["interpretation"])
    _json_ok(r)


def test_rd_refuses_bad_inputs(world):
    ctx, ds = world["ctx"], world["ids"]["rd"]
    with pytest.raises(InvalidArguments, match="bandwidth must be a positive"):
        registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF, "bandwidth": 0}, ctx)
    with pytest.raises(InvalidArguments, match="bandwidth must be a positive"):
        registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF, "bandwidth": -3}, ctx)
    with pytest.raises(InvalidArguments, match="polynomial_order"):
        registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF, "polynomial_order": 3}, ctx)
    with pytest.raises(InvalidArguments, match="one side of the cutoff"):
        registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": 500}, ctx)


# ---------- matching_estimate

def test_matching_removes_confounding_and_improves_balance(world):
    ctx, ds = world["ctx"], world["ids"]["match"]
    r = registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1", "x2"]}, ctx)
    e = r["estimate"]
    assert r["naive_difference"] > 3.0                                   # confounded
    assert abs(e["coef"] - MATCH_EFFECT) < 2 * e["se"] and e["se"] > 0 and e["p"] < 0.001
    assert r["imbalanced_before"] == 2 and r["imbalanced_after"] < r["imbalanced_before"]
    assert all(row["improved"] for row in r["balance"]) and all(abs(row["std_diff_after"]) < 0.1 for row in r["balance"])
    cs = r["common_support"]
    assert cs["treated_matched"] <= cs["n_treated"] and cs["treated_matched_pct"] > 90 and cs["control_units_used"] > 0
    assert r["method"] == "propensity" and r["estimand"] == "ATT" and r["k"] == 1 and r["replacement"] is True and r["caliper"] > 0
    assert "Abadie" in r["se_method"] and r["propensity_engine"].startswith("statsmodels")
    assert "selection on observables" in r["design"] and r["design"] in r["interpretation"]
    assert Path(r["chart_path"]).exists() and Path(r["balance_chart_path"]).exists() and Path(r["chart_path"]).with_suffix(".png").exists()
    # variants: ATE, k = 3, Mahalanobis, no replacement, cluster bootstrap
    ate = registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1", "x2"], "estimand": "ATE", "k": 3}, ctx)
    assert abs(ate["estimate"]["coef"] - MATCH_EFFECT) < 2.5 * ate["estimate"]["se"] and "control_matched" in ate["common_support"]
    mah = registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1", "x2"], "method": "mahalanobis"}, ctx)
    assert abs(mah["estimate"]["coef"] - MATCH_EFFECT) < 2.5 * mah["estimate"]["se"] and mah["imbalanced_after"] == 0
    assert "Mahalanobis" in mah["interpretation"][0] and mah["caliper"] is None and any("Mahalanobis distance" in n for n in mah["notes"])
    # without replacement the control pool (981) is smaller than the treated group (1,019): greedy matching cannot remove the confounding and the tool says so
    nr = registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1", "x2"], "replacement": False}, ctx)
    assert any("greedily" in n for n in nr["notes"]) and any("Prefer replacement" in n for n in nr["notes"]) and nr["replacement"] is False
    assert abs(nr["estimate"]["coef"] - MATCH_EFFECT) < abs(nr["naive_difference"] - MATCH_EFFECT)     # closer than naive, but not unbiased
    cb = registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1", "x2"], "cluster": "site"}, ctx)
    assert cb["estimate"]["coef"] == e["coef"] and "cluster bootstrap" in cb["se_method"] and cb["estimate"]["se"] > 0
    for res in (r, ate, mah, cb):
        _json_ok(res)


def test_matching_refuses_bad_inputs(world):
    ctx, ds = world["ctx"], world["ids"]["match"]
    with pytest.raises(InvalidArguments, match="<= 5"):
        registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1"], "k": 6}, ctx)
    with pytest.raises(InvalidArguments, match="replacement"):
        registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1"], "estimand": "ATE", "replacement": False}, ctx)
    with pytest.raises(InvalidArguments, match="caliper must be"):
        registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": ["x1"], "caliper": 0}, ctx)
    with pytest.raises(InvalidArguments, match="at least 1"):
        registry.call("matching_estimate", {"dataset_id": ds, "outcome": "y", "treatment": "t", "covariates": []}, ctx)


# ---------- instrumental_variables

def test_iv_corrects_ols_bias_with_a_strong_instrument(world):
    ctx, ds = world["ctx"], world["ids"]["iv"]
    r = registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["x"], "instruments": ["z"], "exogenous": ["w"]}, ctx)
    e, o = r["estimate"], r["table"][0]
    assert r["engine"].startswith("pyfixest") and abs(e["coef"] - IV_EFFECT) < 2 * e["se"]
    assert o["ols_coef"] > IV_EFFECT + 0.5 and abs(o["ols_coef"] - 2.5) < 0.3           # OLS biased upward by the common shock
    fs = r["first_stage"][0]
    assert fs["F"] > 10 and not fs["weak"] and r["weak_instruments"] == [] and fs["n_instruments"] == 1 and 0 < fs["partial_r2"] < 1
    assert r["wu_hausman_p"] < 0.01 and r["overidentification"] is None
    assert any("exclusion restriction" in s for s in r["interpretation"]) and "exclusion restriction" in r["design"]
    assert fs["F"] > 104 and any("104" in s for s in r["interpretation"])
    assert Path(r["chart_path"]).exists() and Path(r["csv_path"]).exists()
    # over-identified with two instruments, clustered: Sargan reported, estimate still right
    o2 = registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["x"], "instruments": ["z", "z2"], "cluster": "firm"}, ctx)
    assert o2["overidentification"]["df"] == 1 and 0 <= o2["overidentification"]["p"] <= 1 and o2["se_kind"].startswith("cluster-robust (50 clusters)")
    assert abs(o2["estimate"]["coef"] - IV_EFFECT) < 2 * o2["estimate"]["se"] and o2["engine"].startswith("pyfixest")
    # binary endogenous: LATE wording
    b = registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y_bin", "endogenous": ["d"], "instruments": ["z"]}, ctx)
    assert b["binary_endogenous"] and "LATE" in b["summary_text"]
    _json_ok(r); _json_ok(o2); _json_ok(b)


def test_iv_weak_instrument_warns_and_fallback_matches_pyfixest(world, monkeypatch):
    ctx, ds = world["ctx"], world["ids"]["iv"]
    w = registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y_weak", "endogenous": ["x_weak"], "instruments": ["z"]}, ctx)
    assert w["first_stage"][0]["F"] < 10 and w["first_stage"][0]["weak"] and w["weak_instruments"] == ["x_weak"]
    assert any("Weak instruments" in n for n in w["notes"]) and any("weak (below 10)" in s for s in w["interpretation"])
    strong = registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["x"], "instruments": ["z"], "exogenous": ["w"]}, ctx)
    import sys, types
    fake = types.ModuleType("pyfixest")
    def boom(*a, **k):
        raise RuntimeError("simulated pyfixest failure")
    fake.feols = boom
    monkeypatch.setitem(sys.modules, "pyfixest", fake)
    m = registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["x"], "instruments": ["z"], "exogenous": ["w"]}, ctx)
    assert m["engine"].startswith("manual 2SLS") and any("pyfixest raised RuntimeError" in n for n in m["notes"])
    assert m["estimate"]["coef"] == pytest.approx(strong["estimate"]["coef"], rel=1e-3)
    assert m["estimate"]["se"] == pytest.approx(strong["estimate"]["se"], rel=0.02)        # IV sandwich (HC1) agrees with pyfixest hetero
    _json_ok(w); _json_ok(m)


def test_iv_refuses_bad_inputs(world):
    ctx, ds = world["ctx"], world["ids"]["iv"]
    with pytest.raises(InvalidArguments, match="order condition"):
        registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["x", "w"], "instruments": ["z"]}, ctx)
    with pytest.raises(InvalidArguments, match="both endogenous and an instrument"):
        registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["x"], "instruments": ["x"]}, ctx)
    with pytest.raises(InvalidArguments, match="outcome cannot"):
        registry.call("instrumental_variables", {"dataset_id": ds, "outcome": "y", "endogenous": ["y"], "instruments": ["z"]}, ctx)


def test_designs_are_registered_and_deterministic(world):
    ctx, ids = world["ctx"], world["ids"]
    for name in ("randomized_trial", "regression_discontinuity", "matching_estimate", "instrumental_variables"):
        assert registry.get(name) is not None and registry.get(name).needs_model is False
    a = registry.call("randomized_trial", {"dataset_id": ids["rct"], "outcome": "y2", "treatment": "treatment"}, ctx)
    b = registry.call("randomized_trial", {"dataset_id": ids["rct"], "outcome": "y2", "treatment": "treatment"}, ctx)
    assert a["randomization_inference"]["p"] == b["randomization_inference"]["p"] and a["itt"] == b["itt"]


def test_rd_flags_a_narrow_caller_bandwidth(world):
    ctx, ds = world["ctx"], world["ids"]["rd"]
    wide = _json_ok(registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF}, ctx))
    assert wide["narrow_bandwidth"] is False and wide["rule_of_thumb_bandwidth"] > 0
    narrow = _json_ok(registry.call("regression_discontinuity", {"dataset_id": ds, "outcome": "y", "running": "score", "cutoff": RD_CUTOFF, "bandwidth": 1}, ctx))
    assert narrow["narrow_bandwidth"] is True and "WARNING, narrow bandwidth" in narrow["summary_text"] and any("Narrow bandwidth" in n for n in narrow["notes"])
