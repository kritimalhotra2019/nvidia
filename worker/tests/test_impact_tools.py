"""Impact-evaluation tools on a synthetic randomised trial with a known effect of +15 and selective attrition."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from squipi_worker import tools  # noqa: F401 - registers the tools
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments

TRUE_EFFECT = 15.0
N = 600
PERIODS = 4
TREAT_TIME = 2


def _households(rng):
    hh = np.arange(N)
    village = rng.integers(0, 30, N)
    treatment = rng.integers(0, 2, N)
    hh_size = rng.integers(1, 9, N)                       # randomised: should balance
    baseline = rng.normal(200, 40, N) + 5 * hh_size
    endline = 0.8 * baseline + 40 + TRUE_EFFECT * treatment + rng.normal(0, 20, N)
    wealth_q = pd.qcut(baseline, 5, labels=False) + 1
    # attrition: exactly 10% of control and 18% of treated; the poorest (by baseline + noise) leave
    followed = np.ones(N, dtype=int)
    for arm, rate in ((0, 0.10), (1, 0.18)):
        idx = np.flatnonzero(treatment == arm)
        score = baseline[idx] + rng.normal(0, 25, len(idx))
        k = int(round(rate * len(idx)))
        followed[idx[np.argsort(score)[:k]]] = 0
    endline_obs = np.where(followed == 1, endline, np.nan)
    return pd.DataFrame({"hh_id": hh, "village": village, "treatment": treatment, "hh_size": hh_size, "baseline_cons": baseline,
                         "endline_cons": endline_obs, "endline_true": endline, "followed_up": followed, "wealth_quintile": wealth_q,
                         "arm3": rng.choice(["a", "b", "c"], N), "one_cluster": 1,
                         "status": np.where(treatment == 1, "Treated", "Control")})


def _panel(rng, hh):
    rows = []
    unit_fe = rng.normal(0, 10, N)
    for t in range(PERIODS):
        post = int(t >= TREAT_TIME)
        y = 100 + unit_fe + 3 * t + TRUE_EFFECT * hh["treatment"].to_numpy() * post + rng.normal(0, 5, N)
        rows.append(pd.DataFrame({"hh_id": hh["hh_id"], "village": hh["village"], "treatment": hh["treatment"], "time": t, "post": post, "y": y}))
    return pd.concat(rows, ignore_index=True)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    rng = np.random.default_rng(0)
    base = tmp_path_factory.mktemp("impact")
    p = Project(base / "project", AllowList())
    ctx = JobContext(p, lambda *a: None, lambda: False)
    hh = _households(rng)
    panel = _panel(rng, hh)
    paths = {"hh": base / "households.csv", "panel": base / "panel.csv"}
    hh.to_csv(paths["hh"], index=False)
    panel.to_csv(paths["panel"], index=False)
    ids = {}
    for k, path in paths.items():
        p.allow.allow(path)
        ids[k] = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    return {"ctx": ctx, "ids": ids, "hh": hh, "panel": panel, "base": base}


def test_treatment_effect_recovers_true_effect_and_balance(world):
    ctx, ds = world["ctx"], world["ids"]["hh"]
    r = registry.call("estimate_treatment_effect", {"dataset_id": ds, "outcome": "endline_true", "treatment": "treatment",
                                                     "covariates": ["hh_size"], "baseline_outcome": "baseline_cons", "cluster": "village",
                                                     "design": "randomized"}, ctx)
    u, a = r["unadjusted"], r["adjusted"]
    assert abs(u["coef"] - TRUE_EFFECT) < 2 * u["se"] and abs(a["coef"] - TRUE_EFFECT) < 2 * a["se"]
    assert a["se"] < u["se"]                                      # ANCOVA tightens the estimate
    assert a["specification"].startswith("ANCOVA") and r["se_kind"].startswith("cluster-robust (30 clusters)")
    assert r["arms"]["treated"]["n"] + r["arms"]["control"]["n"] == N == r["n"]
    bal = {row["column"]: row for row in r["balance"]}
    assert set(bal) == {"hh_size", "baseline_cons"} and all(abs(row["std_diff"]) < 0.25 and row["flag"] == "balanced" for row in bal.values())
    assert r["imbalanced_columns"] == [] and r["small_arms"] == []
    assert "causal effect" in r["interpretation"][1] and Path(r["chart_path"]).exists() and Path(r["csv_path"]).exists()
    # default design is observational and says so
    o = registry.call("estimate_treatment_effect", {"dataset_id": ds, "outcome": "endline_true", "treatment": "status"}, ctx)
    assert o["design"] == "observational" and any("association unless the assignment was random" in s for s in o["interpretation"])
    assert o["treatment_levels"] == {"1": "Treated", "0": "Control"} and o["adjusted"] is None
    assert abs(o["unadjusted"]["coef"] - u["coef"]) < 1e-9


def test_treatment_effect_rejects_bad_treatment_and_single_cluster(world):
    ctx, ds = world["ctx"], world["ids"]["hh"]
    with pytest.raises(InvalidArguments, match="binary.*a, b, c"):
        registry.call("estimate_treatment_effect", {"dataset_id": ds, "outcome": "endline_true", "treatment": "arm3"}, ctx)
    with pytest.raises(InvalidArguments, match="fewer than 2 groups"):
        registry.call("estimate_treatment_effect", {"dataset_id": ds, "outcome": "endline_true", "treatment": "treatment", "cluster": "one_cluster"}, ctx)
    with pytest.raises(InvalidArguments, match="not in this dataset. Columns:"):
        registry.call("estimate_treatment_effect", {"dataset_id": ds, "outcome": "endline_true", "treatment": "treatmnt"}, ctx)
    with pytest.raises(InvalidArguments, match="fewer than 2 groups"):
        registry.call("lee_bounds", {"dataset_id": ds, "outcome": "endline_cons", "treatment": "treatment", "cluster": "one_cluster"}, ctx)
    with pytest.raises(InvalidArguments, match="fewer than 2 groups"):
        registry.call("attrition_check", {"dataset_id": ds, "followup_indicator": "followed_up", "treatment": "treatment", "cluster": "one_cluster"}, ctx)


def test_attrition_rates_and_selectivity(world):
    ctx, ds = world["ctx"], world["ids"]["hh"]
    r = registry.call("attrition_check", {"dataset_id": ds, "followup_indicator": "followed_up", "treatment": "treatment",
                                          "subgroup": "wealth_quintile", "baseline_columns": ["baseline_cons", "hh_size"], "cluster": "village"}, ctx)
    assert abs(r["by_arm"]["control"]["rate_pct"] - 10.0) <= 2 and abs(r["by_arm"]["treated"]["rate_pct"] - 18.0) <= 2
    assert abs(r["difference"]["difference_pct_points"] - (r["by_arm"]["treated"]["rate_pct"] - r["by_arm"]["control"]["rate_pct"])) < 0.15
    assert r["difference"]["p"] < 0.05 and r["difference"]["se_kind"].startswith("cluster-robust")
    assert r["n"] == N and len(r["by_subgroup"]) == 5 and all(row["n"] > 0 for row in r["by_subgroup"])
    # attriters are poorer: the poorest quintile loses more than the richest; baseline consumption is selective
    q = {row["subgroup"]: row["rate_pct"] for row in r["by_subgroup"]}
    assert q["1"] > q["5"]
    base = {row["column"]: row for row in r["baseline_comparison"]}
    assert base["baseline_cons"]["std_diff"] < -0.25 and "baseline_cons" in r["selective_on"]
    assert "differential" in " ".join(r["interpretation"]).lower() or "differs between arms" in " ".join(r["interpretation"])
    assert Path(r["chart_path"]).exists()


def test_lee_bounds_bracket_the_estimate(world):
    ctx, ds = world["ctx"], world["ids"]["hh"]
    r = registry.call("lee_bounds", {"dataset_id": ds, "outcome": "endline_cons", "treatment": "treatment"}, ctx)
    assert r["trimmed_arm"] == "control" and r["response"]["control"]["rate_pct"] > r["response"]["treated"]["rate_pct"]
    assert r["lower_bound"] <= r["upper_bound"]
    assert r["lower_bound"] <= r["naive_difference"] <= r["upper_bound"] or abs(r["naive_difference"] - r["upper_bound"]) < 1e-6
    assert r["lower_bound"] > 0 and r["upper_bound"] > 0           # the positive effect survives trimming
    assert r["bootstrap_reps"] == 200 and r["bootstrap_completed"] == 200 and r["se_lower"] > 0 and r["se_upper"] > 0
    assert r["ci_low"] < r["lower_bound"] and r["ci_high"] > r["upper_bound"]
    assert any("monotonicity" in n for n in r["notes"])
    rc = registry.call("lee_bounds", {"dataset_id": ds, "outcome": "endline_cons", "treatment": "treatment", "cluster": "village", "followup_indicator": "followed_up"}, ctx)
    assert rc["lower_bound"] == r["lower_bound"] and rc["upper_bound"] == r["upper_bound"] and "clusters" in rc["notes"][1]
    with pytest.raises(InvalidArguments, match="equal"):
        registry.call("lee_bounds", {"dataset_id": ds, "outcome": "endline_true", "treatment": "treatment"}, ctx)
    # a dataset with too few respondents in one arm
    small = world["hh"].head(60).copy()
    small.loc[small["treatment"] == 1, "endline_cons"] = np.nan
    small.loc[small[small["treatment"] == 1].index[:5], "endline_cons"] = 1.0
    sp = world["base"] / "small.csv"
    small.to_csv(sp, index=False)
    ctx.project.allow.allow(sp)
    sid = registry.call("import_dataset", {"path": str(sp)}, ctx)["dataset_id"]
    with pytest.raises(InvalidArguments, match="at least 20 respondents"):
        registry.call("lee_bounds", {"dataset_id": sid, "outcome": "endline_cons", "treatment": "treatment"}, ctx)


def test_did_and_event_study(world):
    ctx, ds = world["ctx"], world["ids"]["panel"]
    r = registry.call("difference_in_differences", {"dataset_id": ds, "outcome": "y", "treatment": "treatment", "period": "post", "unit": "hh_id"}, ctx)
    did = r["did"]
    assert abs(did["coef"] - TRUE_EFFECT) < 2 * did["se"] and did["p"] < 0.01
    assert r["cluster"] == "hh_id" and r["se_kind"].startswith("cluster-robust (600 clusters)") and r["n"] == N * PERIODS
    cells = {(c["group"], c["period"]): c for c in r["cells"]}
    assert len(cells) == 4 and sum(c["n"] for c in cells.values()) == N * PERIODS
    hand = (cells[("treated", "post")]["mean"] - cells[("treated", "pre")]["mean"]) - (cells[("control", "post")]["mean"] - cells[("control", "pre")]["mean"])
    assert abs(hand - did["coef"]) < 0.3                      # cell means are rounded to 4 significant figures
    assert r["event_study"] is None and any("parallel trends" in s for s in r["interpretation"])
    e = registry.call("difference_in_differences", {"dataset_id": ds, "outcome": "y", "treatment": "treatment", "time": "time", "treatment_time": TREAT_TIME,
                                                    "unit": "hh_id", "cluster": "village"}, ctx)
    assert abs(e["did"]["coef"] - TRUE_EFFECT) < 2 * e["did"]["se"]
    es = e["event_study"]
    assert es is not None and es["reference_period"] == -1 and es["engine"].startswith("pyfixest")
    periods = [c["rel_time"] for c in es["coefficients"]]
    assert periods == [-2, 0, 1] and -1 not in periods
    pre = [c for c in es["coefficients"] if c["rel_time"] < -1]
    assert pre and all(abs(c["coef"]) < 2 * c["se"] for c in pre) and es["pre_periods_near_zero"] is True
    for c in es["coefficients"]:
        if c["rel_time"] >= 0:
            assert abs(c["coef"] - TRUE_EFFECT) < 3 * c["se"] and c["ci_low"] < c["coef"] < c["ci_high"]
    assert es["clusters"] == 30 and Path(e["chart_path"]).exists() and "event_study" in e["chart_path"]
    # 28 Oct: a missing period is derived from the binary post column and reported, not refused.
    derived = registry.call("difference_in_differences", {"dataset_id": ds, "outcome": "y", "treatment": "treatment"}, ctx)
    assert any("post marks the post period" in n for n in derived["argument_notes"])
    assert abs(derived["estimate"] - r["estimate"]) < 1e-9 if "estimate" in r and "estimate" in derived else True


def test_event_study_fallback_when_pyfixest_fails(world, monkeypatch):
    import sys, types
    ctx, ds = world["ctx"], world["ids"]["panel"]
    fake = types.ModuleType("pyfixest")
    def boom(*a, **k):
        raise RuntimeError("simulated pyfixest failure")
    fake.feols = boom
    monkeypatch.setitem(sys.modules, "pyfixest", fake)
    e = registry.call("difference_in_differences", {"dataset_id": ds, "outcome": "y", "treatment": "treatment", "time": "time", "treatment_time": TREAT_TIME, "unit": "hh_id"}, ctx)
    es = e["event_study"]
    assert es["engine"].startswith("statsmodels") and any("pyfixest raised RuntimeError" in n for n in e["notes"])
    assert [c["rel_time"] for c in es["coefficients"]] == [-2, 0, 1]
    post = [c for c in es["coefficients"] if c["rel_time"] >= 0]
    assert all(abs(c["coef"] - TRUE_EFFECT) < 3 * c["se"] for c in post)


def test_results_serialise_to_json(world):
    ctx, hh, panel = world["ctx"], world["ids"]["hh"], world["ids"]["panel"]
    results = [
        registry.call("estimate_treatment_effect", {"dataset_id": hh, "outcome": "endline_true", "treatment": "treatment", "covariates": ["hh_size"]}, ctx),
        registry.call("attrition_check", {"dataset_id": hh, "followup_indicator": "followed_up", "treatment": "treatment", "baseline_columns": ["hh_size"]}, ctx),
        registry.call("lee_bounds", {"dataset_id": hh, "outcome": "endline_cons", "treatment": "treatment"}, ctx),
        registry.call("difference_in_differences", {"dataset_id": panel, "outcome": "y", "treatment": "treatment", "time": "time", "treatment_time": TREAT_TIME, "unit": "hh_id"}, ctx),
    ]
    for r in results:
        text = json.dumps(r, default=str)
        back = json.loads(text)
        assert back["summary_text"] and back["interpretation"] and back["n"] > 0
        assert "NaN" not in text and "Infinity" not in text
    for name in ("estimate_treatment_effect", "difference_in_differences", "attrition_check", "lee_bounds"):
        assert registry.get(name) is not None and registry.get(name).needs_model is False


def test_precision_note_appears_only_when_the_interval_swamps_the_estimate(world):
    """A 60-household subsample cannot pin the +15 effect down; the full 600 can."""
    ctx, hh, base = world["ctx"], world["hh"], world["base"]
    small = hh.sample(n=60, random_state=1)
    path = base / "households_small.csv"
    small.to_csv(path, index=False)
    ctx.project.allow.allow(path)
    ds_small = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    r_small = registry.call("estimate_treatment_effect", {"dataset_id": ds_small, "outcome": "endline_true", "treatment": "treatment"}, ctx)
    r_full = registry.call("estimate_treatment_effect", {"dataset_id": world["ids"]["hh"], "outcome": "endline_true", "treatment": "treatment",
                                                         "cluster": "village"}, ctx)
    assert r_small["precision"]["ci_width"] > abs(r_small["unadjusted"]["coef"]) if "unadjusted" in r_small else True
    assert any("Precision:" in n for n in r_small["notes"]), r_small["notes"]
    assert any("plan_research_power" in n for n in r_small["interpretation"])
    assert r_small["precision"]["n_per_arm"]["treated"] + r_small["precision"]["n_per_arm"]["control"] == 60
    assert r_small["precision"]["mde_80pct_power"] > 0
    assert not any("Precision:" in n for n in r_full["notes"]), r_full["notes"]
    assert r_full["precision"]["ci_width_to_estimate"] < 1
    import json
    json.dumps(r_small, default=str)


def test_did_post_periods_list_means_onward(world):
    """post_periods [2] on a 4-period panel counts period 3 as post too, with a note; [2, 3] gives the same estimate."""
    ctx, ids = world["ctx"], world["ids"]
    panel = world["panel"]
    times = sorted(int(t) for t in panel["time"].unique())
    assert len(times) >= 3
    first_post = times[-2]
    short = registry.call("difference_in_differences", {"dataset_id": ids["panel"], "outcome": "y", "treatment": "treatment", "time": "time",
                                                        "post_periods": [first_post], "unit": "hh_id"}, ctx)
    full = registry.call("difference_in_differences", {"dataset_id": ids["panel"], "outcome": "y", "treatment": "treatment", "time": "time",
                                                       "post_periods": [first_post, times[-1]], "unit": "hh_id"}, ctx)
    assert short["did"]["coef"] == full["did"]["coef"]
    assert any("also counted as post" in n for n in short["notes"]), short["notes"]
    assert not any("also counted as post" in n for n in full["notes"])


def test_attrition_indicator_derived_from_a_gappy_outcome(world):
    """The model names the endline outcome instead of a 0/1 indicator: observed = non-missing, and the result says so."""
    ctx, ds = world["ctx"], world["ids"]["hh"]
    r = registry.call("attrition_check", {"dataset_id": ds, "followup_indicator": "endline_cons", "treatment": "treatment"}, ctx)
    ref = registry.call("attrition_check", {"dataset_id": ds, "followup_indicator": "followed_up", "treatment": "treatment"}, ctx)
    assert r["by_arm"]["treated"]["attrited"] == ref["by_arm"]["treated"]["attrited"] and r["by_arm"]["control"]["attrited"] == ref["by_arm"]["control"]["attrited"]
    assert r["followup_levels"] == {"1": "non-missing", "0": "missing"} and any("non-missing" in n for n in r["notes"])
    with pytest.raises(Exception):
        registry.call("attrition_check", {"dataset_id": ds, "followup_indicator": "baseline_cons", "treatment": "treatment"}, ctx)
