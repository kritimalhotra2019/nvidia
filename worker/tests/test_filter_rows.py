"""A subset by plain conditions, registered with lineage, so 'exclude those who dropped out and redo' can run (28 Oct)."""
import numpy as np, pandas as pd, pytest
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments
import squipi_worker.tools  # noqa: F401


def setup(tmp_path):
    p = Project(tmp_path / "project", AllowList()); ctx = JobContext(p, lambda *a: None, lambda: False)
    rng = np.random.default_rng(1); n = 120; arm = np.array(["treatment"] * 60 + ["control"] * 60)
    dropped = (rng.random(n) < 0.2).astype(int)
    df = pd.DataFrame({"id": range(n), "arm": arm, "dropped_out": dropped, "weight": np.where(dropped, np.nan, rng.normal(5, 0.4, n)).round(2), "age": rng.integers(18, 40, n)})
    path = tmp_path / "rct.csv"; df.to_csv(path, index=False); p.allow.allow(path)
    return ctx, registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"], df


def test_exclude_dropouts_then_analyse_the_subset(tmp_path):
    ctx, ds, df = setup(tmp_path)
    sub = registry.call("filter_rows", {"dataset_id": ds, "conditions": [{"column": "dropped_out", "op": "eq", "value": 1}], "exclude": True}, ctx)
    assert sub["rows"] == int((df.dropped_out == 0).sum()) and sub["from_dataset_id"] == ds and "Excluded rows where dropped_out eq 1" in sub["summary_text"]
    res = registry.call("compare_groups", {"dataset_id": sub["dataset_id"], "outcome": "weight", "group": "arm"}, ctx)
    assert res["dataset_id"] == sub["dataset_id"]
    lineage = {d.id for d in ctx.project.dataset_lineage(sub["dataset_id"])}
    assert ds in lineage


def test_keep_conditions_combine_and_match_loosely(tmp_path):
    ctx, ds, df = setup(tmp_path)
    sub = registry.call("filter_rows", {"dataset_id": ds, "conditions": [{"column": "Arm", "op": "in", "values": ["Treatment"]}, {"column": "age", "op": "ge", "value": 30}]}, ctx)
    assert sub["rows"] == int(((df.arm == "treatment") & (df.age >= 30)).sum())
    miss = registry.call("filter_rows", {"dataset_id": ds, "conditions": [{"column": "weight", "op": "not_missing"}]}, ctx)
    assert miss["rows"] == int(df.weight.notna().sum())
    with pytest.raises(InvalidArguments):
        registry.call("filter_rows", {"dataset_id": ds, "conditions": [{"column": "arm", "op": "eq", "value": "placebo"}]}, ctx)


def test_non_finite_statistics_do_not_void_a_result(tmp_path):
    from squipi_worker.registry import _finite
    assert _finite({"a": float("inf"), "b": [1.0, float("nan"), {"c": np.float64("inf")}], "d": 2}) == {"a": None, "b": [1.0, None, {"c": None}], "d": 2}
