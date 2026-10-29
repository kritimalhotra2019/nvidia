"""Required arguments the data already determines are filled and reported, never refused (28 Oct 2026)."""
import numpy as np
import pandas as pd
import pytest

from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments
import squipi_worker.tools  # noqa: F401  (registers every tool)


def setup(tmp_path, frame, name="d.csv"):
    p = Project(tmp_path / "project", AllowList()); ctx = JobContext(p, lambda *a: None, lambda: False)
    path = tmp_path / name; frame.to_csv(path, index=False); p.allow.allow(path)
    return ctx, registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]


def rct(n=160, seed=4):
    rng = np.random.default_rng(seed); arm = np.array(["treatment"] * (n // 2) + ["control"] * (n - n // 2))
    observed = (rng.random(n) > 0.15).astype(int)
    return pd.DataFrame({"mother_id": [f"M{i}" for i in range(n)], "arm": arm, "district": rng.choice(["A", "B"], n),
                         "observed_endline": observed, "baseline_weight_kg": rng.normal(3, 0.3, n).round(2),
                         "endline_weight_kg": np.where(observed, rng.normal(5, 0.4, n) + np.where(arm == "treatment", 0.3, 0), np.nan).round(2)})


def panel(seed=5):
    rng = np.random.default_rng(seed); rows = []
    for h in range(120):
        tr = h % 2
        for y in (2023, 2024, 2025):
            rows.append({"hh_id": f"H{h}", "village": f"V{h % 10}", "year": y, "treated": tr, "loss_pct": rng.normal(20, 4) - (6 if tr and y >= 2024 else 0)})
    return pd.DataFrame(rows)


def test_comparison_without_a_group_uses_the_assignment_column(tmp_path):
    ctx, ds = setup(tmp_path, rct())
    res = registry.call("compare_groups", {"dataset_id": ds, "outcome": "endline_weight_kg"}, ctx)
    assert res["group"] == "arm" and any("No group column was given; arm" in n for n in res["argument_notes"])


def test_estimator_without_treatment_or_outcome_uses_candidates(tmp_path):
    ctx, ds = setup(tmp_path, rct())
    res = registry.call("estimate_treatment_effect", {"dataset_id": ds}, ctx)
    assert res["treatment"] == "arm" and res["outcome"] in ("endline_weight_kg", "baseline_weight_kg")
    assert any("treatment column" in n for n in res["argument_notes"]) and any("outcome" in n for n in res["argument_notes"])


def test_attrition_check_with_treatment_as_indicator_is_repaired(tmp_path):
    ctx, ds = setup(tmp_path, rct())
    res = registry.call("attrition_check", {"dataset_id": ds, "followup_indicator": "arm", "treatment": "arm"}, ctx)
    assert res["followup_indicator"] == "observed_endline" and any("observed_endline" in n for n in res["argument_notes"])


def test_did_without_a_period_uses_the_time_column(tmp_path):
    ctx, ds = setup(tmp_path, panel())
    res = registry.call("difference_in_differences", {"dataset_id": ds, "outcome": "loss_pct", "treatment": "treated"}, ctx)
    assert any("year is the time column" in n for n in res["argument_notes"]), res["argument_notes"]
    assert "estimate" in res or "effect" in res or res.get("summary_text")


def test_chart_without_a_kind_is_drawn_from_column_roles(tmp_path):
    ctx, ds = setup(tmp_path, rct())
    bar = registry.call("create_chart", {"dataset_id": ds, "x": "arm", "y": "endline_weight_kg"}, ctx)
    assert any("a bar chart fits" in n for n in bar["argument_notes"]) and bar.get("chart_path")
    sc = registry.call("create_chart", {"dataset_id": ds, "x": "baseline_weight_kg", "y": "endline_weight_kg"}, ctx)
    assert any("a scatter chart fits" in n for n in sc["argument_notes"])
    hist = registry.call("create_chart", {"dataset_id": ds, "x": "baseline_weight_kg"}, ctx)
    assert any("a histogram chart fits" in n for n in hist["argument_notes"])


def test_a_truly_missing_argument_is_still_refused(tmp_path):
    ctx, ds = setup(tmp_path, pd.DataFrame({"a": [1, 2, 3, 4, 5, 6] * 5, "b": list("xyzxyz") * 5}))
    with pytest.raises(InvalidArguments):
        registry.call("compare_groups", {"dataset_id": ds, "outcome": "a"}, ctx)     # no group-like column exists
