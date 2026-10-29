"""A survey export with repeated member blocks, duplicate columns and a status column (28 Oct 2026).

Mechanisms, not a fix for one file: numbered column families are per-member fields of one record;
a column that duplicates another is one variable; a status/arm column is the grouping "treatment and
control" refers to; findings the user set aside stay out; an unknown argument spelling is repaired
or ignored with a note, never refused.
"""
import numpy as np
import pandas as pd
import pytest

from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments, tolerate_unknown_keys
from squipi_worker.tools.structure import column_families, duplicate_columns, group_candidates


def roster(n=200, seed=7):
    rng = np.random.default_rng(seed)
    status = np.array(["treatment"] * (n // 2) + ["control"] * (n - n // 2))
    cols = {"HH_Code": [f"H{i:04d}" for i in range(n)], "district": rng.choice(["A", "B", "C"], n), "status": status,
            "hh_mem_num": rng.integers(2, 9, n)}
    cols["Name_of_HH_count"] = cols["hh_mem_num"]; cols["Emp_status1_count"] = cols["hh_mem_num"]
    for i in range(1, 7):
        present = cols["hh_mem_num"] >= i
        age = np.where(present, rng.integers(1, 80, n), np.nan)
        cols[f"Name_{i}"] = np.where(present, [f"m{i}-{k}" for k in range(n)], None)
        cols[f"Age_{i}"] = age; cols[f"Age_in_yrs_{i}"] = age
        cols[f"Gender_{i}"] = np.where(present, rng.choice(["m", "f"], n), None)
    tr = status == "treatment"
    cols["monthly_consumption"] = (rng.lognormal(8.9, 0.3, n) * np.where(tr, 1.15, 1)).round()
    cols["food_insecure_days"] = np.clip(rng.poisson(4.5, n) - np.where(tr, 1.5, 0), 0, 30)
    cols["savings"] = (rng.lognormal(7, 0.6, n) * np.where(tr, 1.2, 1)).round()
    return pd.DataFrame(cols)


def setup(tmp_path, frame):
    p = Project(tmp_path / "project", AllowList()); ctx = JobContext(p, lambda *a: None, lambda: False)
    path = tmp_path / "roster.csv"; frame.to_csv(path, index=False); p.allow.allow(path)
    return ctx, registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]


def test_structure_helpers_find_families_duplicates_and_the_group_column():
    df = roster()
    fam = column_families(list(df.columns))
    assert set(fam) == {"Name", "Age", "Age_in_yrs", "Gender"} and fam["Age"] == [f"Age_{i}" for i in range(1, 7)]
    dups = duplicate_columns(df, ["hh_mem_num", "Name_of_HH_count", "Emp_status1_count", "monthly_consumption", "savings"])
    assert {(d["column"], d["duplicate_of"]) for d in dups} == {("Name_of_HH_count", "hh_mem_num"), ("Emp_status1_count", "hh_mem_num")}
    assert all(d["identical"] for d in dups)
    from squipi_worker.tools.datasets import analyse_column
    cols = {c: analyse_column(c, df[c], len(df)) for c in df.columns}
    groups = group_candidates(df, cols)
    assert groups and groups[0]["column"] == "status" and set(groups[0]["levels"]) == {"treatment", "control"}


def test_profile_reports_families_duplicates_and_group_candidate(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    prof = registry.call("profile_dataset", {"dataset_id": ds}, ctx)
    assert prof["column_families"]["Age"] == [f"Age_{i}" for i in range(1, 7)]
    assert {d["column"] for d in prof["duplicate_columns"]} >= {"Name_of_HH_count", "Emp_status1_count"}
    assert prof["group_candidates"][0]["column"] == "status"
    assert "repeated column families" in prof["summary_text"] and "Possible treatment/group column: status" in prof["summary_text"]
    assert any(f["flag"] == "duplicate_column" for f in prof["quality_flags"])


def test_insights_skip_tautologies_and_member_blocks_and_lead_with_the_group_column(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("find_insights", {"dataset_id": ds, "charts": False, "max_findings": 12}, ctx)
    titles = [f["title"] for f in res["findings"]]
    assert not any("Age_in_yrs" in t or "_count and" in t for t in titles), titles
    assert not any(abs(f.get("numbers", {}).get("r", 0)) >= 0.98 for f in res["findings"]), titles
    assert any(f["kind"] == "group_difference" and f["by"] == "status" for f in res["findings"]), titles
    assert any(f["kind"] == "structure" for f in res["findings"])
    assert res["group_columns"] == ["status"] and res["column_families"]["Age"] == 6
    assert any("duplicates hh_mem_num" in q["issue"] for q in res["data_quality"])


def test_insights_exclusions_and_counts(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    everything = registry.call("find_insights", {"dataset_id": ds, "charts": False, "max_findings": 20}, ctx)
    assert everything["findings_total"] == everything["findings_shown"] >= 3 and everything["summary_text"].startswith(f"{everything['findings_shown']} findings")
    full = registry.call("find_insights", {"dataset_id": ds, "charts": False, "max_findings": 3}, ctx)
    assert full["findings_shown"] == min(3, everything["findings_total"]) and full["findings_total"] == everything["findings_total"]
    if full["findings_total"] > 3:
        assert full["summary_text"].startswith("First 3 of")
    two = registry.call("find_insights", {"dataset_id": ds, "charts": False, "max_findings": 3, "outcome": None}, ctx)
    assert two["findings_total"] >= 3
    res = registry.call("find_insights", {"dataset_id": ds, "charts": False, "exclude": ["food_insecure"]}, ctx)
    assert not any("food_insecure" in f["title"].lower() for f in res["findings"])
    assert res["excluded_findings"] >= 1 and "set aside as requested" in res["summary_text"]


def test_unknown_argument_is_renamed_or_ignored_with_a_note():
    schema = {"type": "object", "properties": {"dataset_id": {"type": "string"}, "columns": {"type": "array"}, "group_by": {"type": "array"}},
              "required": ["dataset_id"], "additionalProperties": False}
    out, notes = tolerate_unknown_keys(schema, {"dataset_id": "d", "column": ["a"], "design": "rct"})
    assert out == {"dataset_id": "d", "columns": ["a"]}
    assert any("'column' was read as 'columns'" in n for n in notes) and any("'design' was ignored" in n for n in notes)
    out, notes = tolerate_unknown_keys(schema, {"dataset_id": "d", "groupby": ["x"]})
    assert out["group_by"] == ["x"]
    with pytest.raises(InvalidArguments):
        tolerate_unknown_keys(schema, {"dataset_id": "d", "allow_network": True})     # a gate is never spelled past
    path_schema = {"type": "object", "properties": {"path": {"type": "string"}, "dataset_id": {"type": "string"}}, "required": ["path"], "additionalProperties": False}
    out, notes = tolerate_unknown_keys(path_schema, {"file_path": "/tmp/x.csv"})
    assert out == {"path": "/tmp/x.csv"} and any("'file_path' was read as 'path'" in n for n in notes)
    out, notes = tolerate_unknown_keys(path_schema, {"path": "/tmp/x.csv", "dataset": "d1"})
    assert out == {"path": "/tmp/x.csv", "dataset_id": "d1"}
    same, none = tolerate_unknown_keys(schema, {"dataset_id": "d"})
    assert same == {"dataset_id": "d"} and none == []


def test_unknown_argument_through_the_registry_runs_and_reports(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("find_insights", {"dataset_id": ds, "charts": False, "design": "rct"}, ctx)
    assert any("'design' was ignored" in n for n in res["argument_notes"]) and "Argument adjustments" in res["summary_text"]
    with pytest.raises(InvalidArguments):
        registry.call("find_insights", {"charts": False}, ctx)       # a missing required argument is still refused


def test_analysis_requirement_is_met_by_the_union_of_calls_on_the_source(tmp_path):
    """Four separate compare_groups calls covered every column the contract named (28 Oct, RCT held-out case);
    the review must not demand that one call carry them all."""
    from squipi_worker.objectives import ObjectiveStore
    rng = np.random.default_rng(3); n = 120
    arm = np.array(["treatment"] * 60 + ["control"] * 60)
    frame = pd.DataFrame({"arm": arm, "baseline_weight_kg": rng.normal(3, 0.3, n).round(2),
                          "endline_weight_kg": (rng.normal(5, 0.4, n) + np.where(arm == "treatment", 0.3, 0)).round(2),
                          "exclusive_bf_6m": (rng.random(n) < np.where(arm == "treatment", 0.6, 0.4)).astype(int)})
    ctx, ds = setup(tmp_path, frame)
    store = ObjectiveStore(ctx.project); oid = "objective-union-1"
    store.open(oid, "Compare endline weight and exclusive breastfeeding between treatment and control, and check baseline balance.", [ds])
    store.requirements(oid, [{"id": "c1", "kind": "analysis", "quote": "Compare endline weight and exclusive breastfeeding between treatment and control, and check baseline balance.",
                              "description": "compare by arm", "columns": ["endline_weight_kg", "exclusive_bf_6m", "baseline_weight_kg"], "groups": ["arm"], "source_ids": [ds]}])
    ctx.objective_id = oid
    assert not store.review(oid)["passed"]
    for col in ("endline_weight_kg", "exclusive_bf_6m", "baseline_weight_kg"):
        args = {"dataset_id": ds, "outcome": col, "group": "arm"}
        receipt = store.begin(oid, "compare_groups", args)
        store.finish_step(receipt, registry.call("compare_groups", args, ctx), True)
    review = store.review(oid)
    assert any(c["id"] == "c1" and c["passed"] for c in review["checks"]), review


def test_profile_names_candidate_outcomes_before_member_fields(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    prof = registry.call("profile_dataset", {"dataset_id": ds}, ctx)
    cands = prof["outcome_candidates"]
    assert cands[:3] and set(cands[:3]) <= {"monthly_consumption", "food_insecure_days", "savings"}, cands
    assert not any(c.startswith("Age_") or c.endswith("_count") for c in cands), cands
    assert "Candidate outcome columns" in prof["summary_text"]


def test_rate_without_a_label_runs_and_names_itself(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("calculate_dataset_rate", {"dataset_id": ds, "numerator_filters": [{"column": "status", "values": ["treatment"]}]}, ctx)
    assert res.get("label", "").startswith("share with status in treatment") or "share with status" in res["summary_text"]
    assert any("No label was given" in n for n in res["argument_notes"])


def test_insights_with_a_member_outcome_still_skip_duplicate_pairs(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("find_insights", {"dataset_id": ds, "charts": False, "outcome": "Age_1"}, ctx)
    titles = [f["title"] for f in res["findings"]]
    assert not any("Age_in_yrs" in t for t in titles), titles
    assert not any(abs(f.get("numbers", {}).get("r", 0)) >= 0.98 for f in res["findings"]), titles
    dup = registry.call("find_insights", {"dataset_id": ds, "charts": False, "outcome": "Age_in_yrs_1"}, ctx)
    assert not any("Age_in_yrs" in f["title"] for f in dup["findings"]), [f["title"] for f in dup["findings"]]


def test_compare_groups_takes_several_outcomes_and_summarize_sets_identifiers_aside(tmp_path):
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("compare_groups", {"dataset_id": ds, "outcome": ["monthly_consumption", "savings"], "group": "status"}, ctx)
    assert len(res["comparisons"]) == 2 and res["outcomes"] == ["monthly_consumption", "savings"] and "status" in res["summary_text"].lower() or "treatment" in res["summary_text"].lower()
    one = registry.call("compare_groups", {"dataset_id": ds, "outcome": ["savings"], "group": "status"}, ctx)
    assert "comparisons" not in one and "savings" in one["summary_text"]
    summ = registry.call("summarize_research_data", {"dataset_id": ds, "columns": ["HH_Code", "monthly_consumption"], "group_by": ["status"]}, ctx)
    assert any("HH_Code" in n and "set aside" in n for n in summ["argument_notes"])
    with pytest.raises(InvalidArguments):
        registry.call("summarize_research_data", {"dataset_id": ds, "columns": ["HH_Code"]}, ctx)


def test_a_single_name_where_a_list_is_declared_is_read_as_a_list(tmp_path):
    from squipi_worker.registry import coerce_shapes
    schema = {"type": "object", "properties": {"columns": {"type": "array", "items": {"type": "string"}}, "outcome": {"type": ["string", "array"]}, "dataset_id": {"type": "string"}}}
    out, notes = coerce_shapes(schema, {"columns": "monthly_consumption", "outcome": "x", "dataset_id": "d"})
    assert out["columns"] == ["monthly_consumption"] and out["outcome"] == "x" and notes
    out, _ = coerce_shapes(schema, {"columns": "a, b ,c"})
    assert out["columns"] == ["a", "b", "c"]
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("summarize_research_data", {"dataset_id": ds, "columns": "monthly_consumption", "group_by": "status"}, ctx)
    assert any("read as a list" in n for n in res["argument_notes"]) and res["rows"] == 200


def test_soft_requirements_are_notes_and_hard_ones_decide(tmp_path):
    """An inferred analysis/chart row never fails the review; a named file or an explicit chart does (28 Oct)."""
    from squipi_worker.objectives import ObjectiveStore
    ctx, ds = setup(tmp_path, roster())
    store = ObjectiveStore(ctx.project); oid = "objective-soft-1"
    store.open(oid, "analyze this dataset and deliver key insights", [ds])
    store.requirements(oid, [{"id": "soft-analysis", "kind": "analysis", "quote": "deliver key insights", "description": "Perform statistical analysis", "columns": ["monthly_consumption", "savings", "food_insecure_days"], "source_ids": [ds]},
                             {"id": "soft-chart", "kind": "chart", "quote": "deliver key insights", "description": "Visualize", "columns": ["savings"], "source_ids": [ds]},
                             {"id": "hard-source", "kind": "source_used", "quote": "this dataset", "description": "Use required input", "source_ids": [ds]}])
    ctx.objective_id = oid
    receipt = store.begin(oid, "aggregate", {"dataset_id": ds, "metrics": [{"column": "savings", "agg": "mean"}], "group_by": ["status"]})
    store.finish_step(receipt, registry.call("aggregate", {"dataset_id": ds, "metrics": [{"column": "savings", "agg": "mean"}], "group_by": ["status"]}, ctx), True)
    review = store.review(oid)
    kinds = {c["id"]: c for c in review["checks"]}
    assert kinds["hard-source"]["hard"] and kinds["hard-source"]["passed"]
    assert not kinds["soft-chart"]["hard"] and not kinds["soft-chart"]["passed"]
    assert review["passed"] and all(i["requirement"] != "soft-chart" for i in review["issues"]) and any(n["requirement"] == "soft-chart" for n in review["notes"])
    oid2 = "objective-hard-chart"
    store.open(oid2, "deliver key insights with one chart of savings by status", [ds]); ctx.objective_id = oid2
    store.requirements(oid2, [{"id": "hard-chart", "kind": "chart", "quote": "with one chart of savings by status", "description": "one chart", "columns": ["savings"], "source_ids": [ds]}])
    assert any(c["id"] == "hard-chart" and c["hard"] and not c["passed"] for c in store.review(oid2)["checks"])


def test_summary_over_the_column_cap_keeps_outcomes_first(tmp_path):
    """28 Oct: a 21-column summary took the first twelve in file order and never reached wellbeing or food security."""
    rng = np.random.default_rng(9); n = 120
    cols = {f"demo_{i}": rng.integers(1, 9, n) for i in range(1, 12)}
    cols.update({"id_code": [f"H{i}" for i in range(n)], "wellbeing_score": rng.normal(6, 1.5, n).round(1), "food_insecure_days": rng.integers(0, 15, n), "monthly_income": rng.normal(9000, 2000, n).round()})
    ctx, ds = setup(tmp_path, pd.DataFrame(cols))
    names = [c for c in cols if c != "id_code"]
    res = registry.call("summarize_research_data", {"dataset_id": ds, "columns": names}, ctx)
    summarised = {r["variable"] for r in res["statistics"]}
    assert {"wellbeing_score", "food_insecure_days", "monthly_income"} <= summarised, summarised
    assert any("12 of 14 requested columns were summarised" in n for n in res["argument_notes"]), res["argument_notes"]


def test_brief_report_writes_every_finding(tmp_path):
    """28 Oct: detail 'brief' kept only charted sections, so distribution charts crowded out 7 of 8 findings."""
    from docx import Document
    ctx, ds = setup(tmp_path, roster())
    res = registry.call("report_dataset", {"dataset_id": ds, "format": "docx", "detail": "brief"}, ctx)
    assert res["findings_written"] == res["findings"] and "written in the document" in res["summary_text"]
    doc = Document(res["docx_path"])
    heads = [p.text for p in doc.paragraphs if p.style.name.lower().startswith("heading")]
    assert sum(1 for h in heads if h[:2].rstrip(".").isdigit() or h[:1].isdigit()) == res["findings"]
