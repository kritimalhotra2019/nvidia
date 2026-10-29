import pandas as pd
from squipi_worker.tools.research_analysis import normalize_summarize
from squipi_worker.registry import registry
from tests.test_argument_normalization import setup


def test_argument_names_are_dropped_from_column_lists():
    args, notes = normalize_summarize({"dataset_id": "d", "columns": ["growth_proj", "chart_palette", "style"], "group_by": ["country_iso3_code"]})
    assert args["columns"] == ["growth_proj"] and any("argument names" in n for n in notes)


def test_many_groups_are_ranked_not_refused(tmp_path):
    rows = pd.DataFrame([{"iso": f"C{i:03d}", "year": y, "g": (i % 7) + y * 0.01} for i in range(145) for y in (2000, 2010)])
    ctx, ds = setup(tmp_path, rows)
    res = registry.call("summarize_research_data", {"dataset_id": ds, "charts": False, "columns": ["g"], "group_by": ["iso"]}, ctx)
    groups = {r["group"] for r in res["statistics"] if r["scope"] == "group"}
    assert len(groups) == 31 and any(g.startswith("Other (") for g in groups)
    assert any("145 groups" in n for n in res["argument_notes"])
