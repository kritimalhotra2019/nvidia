"""v3 items (28 Oct): sibling sheets, named coded outcomes, paired columns, near-one shares, canonical filter values."""
import numpy as np, pandas as pd, pytest
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry
import squipi_worker.tools  # noqa: F401
from squipi_worker.tools.structure import paired_columns


def ctx_for(tmp_path):
    p = Project(tmp_path / "project", AllowList()); return JobContext(p, lambda *a: None, lambda: False)


def test_workbook_sheets_become_sibling_datasets(tmp_path):
    ctx = ctx_for(tmp_path); path = tmp_path / "survey.xlsx"
    base = pd.DataFrame({"id": range(50), "g": ["f", "m"] * 25, "employed": [0, 1] * 25}); end = pd.DataFrame({"id": range(50), "employed": [1, 0] * 25})
    with pd.ExcelWriter(path) as w:
        base.to_excel(w, sheet_name="baseline", index=False); end.to_excel(w, sheet_name="endline", index=False); pd.DataFrame({"note": ["read me"]}).to_excel(w, sheet_name="README", index=False)
    ctx.project.allow.allow(path)
    r = registry.call("import_dataset", {"path": str(path)}, ctx)
    sibs = r["sibling_datasets"]
    assert any(s["sheet"] == "endline" and s["rows"] == 50 for s in sibs) and "registered too" in r["summary_text"]
    assert not any(s["sheet"] == "README" for s in sibs)          # a one-row note sheet is not data
    one = registry.call("import_dataset", {"path": str(path), "sheet": "endline"}, ctx)
    assert one["sibling_datasets"] == [] and one["sheet"] == "endline"


def test_named_outcome_with_few_values_is_accepted(tmp_path):
    ctx = ctx_for(tmp_path); path = tmp_path / "d.csv"
    rng = np.random.default_rng(2); n = 150
    pd.DataFrame({"status": ["treatment", "control"] * 75, "food_insecure_days": rng.integers(0, 8, n), "income": rng.normal(100, 10, n)}).to_csv(path, index=False)
    ctx.project.allow.allow(path); ds = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    res = registry.call("find_insights", {"dataset_id": ds, "charts": False, "outcome": "food_insecure_days"}, ctx)
    assert "food_insecure_days" in res["columns_used"]["numeric"]


def test_paired_columns_and_profile_line(tmp_path):
    assert {p["measure"] for p in paired_columns(["enrol_rate_r1", "enrol_rate_r2", "income_baseline", "income_endline", "age", "score_pre", "score_post", "x_r1"])} == {"enrol_rate", "income", "score"}
    ctx = ctx_for(tmp_path); path = tmp_path / "p.csv"
    pd.DataFrame({"school": range(40), "enrol_rate_r1": np.linspace(0.4, 0.8, 40), "enrol_rate_r2": np.linspace(0.5, 0.9, 40)}).to_csv(path, index=False)
    ctx.project.allow.allow(path); ds = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    prof = registry.call("profile_dataset", {"dataset_id": ds}, ctx)
    assert prof["paired_columns"][0]["first"] == "enrol_rate_r1" and "Paired measures" in prof["summary_text"]


def test_shares_near_one_are_scaled(tmp_path):
    ctx = ctx_for(tmp_path); path = tmp_path / "v.csv"
    pd.DataFrame({"village": [f"V{i}" for i in range(90)], "district": ["N", "S", "E"] * 30}).to_csv(path, index=False)
    ctx.project.allow.allow(path); ds = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    res = registry.call("randomize_assignment", {"dataset_id": ds, "arms": ["A", "B", "C"], "allocation": [0.33, 0.33, 0.33], "seed": 7, "strata": "district"}, ctx)
    assert any("scaled to 1" in n for n in res["argument_notes"])


def test_filter_values_are_canonical(tmp_path):
    from squipi_worker.tools.filtering import normalize_filter
    a, _ = normalize_filter({"conditions": [{"column": "d", "op": "eq", "value": False}, {"column": "e", "op": "in", "values": ["1", "Yes", " Treatment "]}]})
    assert a["conditions"][0]["value"] == 0 and a["conditions"][1]["values"] == [1, 1, "treatment"]
