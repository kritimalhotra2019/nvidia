import pandas as pd
from squipi_worker.registry import registry
from tests.test_argument_normalization import setup
from squipi_worker.tools.research_analysis import _entity_labels


def test_entity_labels_map_ids_to_codes():
    df = pd.DataFrame({"country_id": [800, 800, 356, 356, 834], "iso": ["UGA", "UGA", "IND", "IND", "TZA"], "g": [6.7, 6.8, 6.3, 6.2, 6.0]})
    m = _entity_labels(df, "country_id")
    assert m and m["column"] == "iso" and m["map"][800] == "UGA"
    assert _entity_labels(df, "iso") is None


def test_brief_has_narrative_and_labels(tmp_path):
    rows = []
    for i in range(30):
        for y in (2000, 2002, 2004, 2006, 2008, 2010):
            rows.append({"country_id": 100 + i, "iso": f"C{i:02d}", "year": y, "growth": (i % 7) + y * 0.001, "eci": (i % 5) / 10})
    ctx, ds = setup(tmp_path, pd.DataFrame(rows))
    res = registry.call("summarize_research_data", {"dataset_id": ds, "charts": False, "columns": ["growth", "eci"], "group_by": ["country_id"]}, ctx)
    groups = [r["group"] for r in res["statistics"] if r["scope"] == "group"]
    assert any(g.startswith("C") and "(1" in g for g in groups), groups[:3]
    brief = registry.call("write_research_brief", {"analysis_id": res["analysis_id"], "title": "Growth by country"}, ctx)
    from docx import Document
    text = "\n".join(p.text for p in Document(brief["path"]).paragraphs)
    assert "What the data is" in text and "What stands out" in text
    assert "In plain words" in text and "Specialist reading" in text
    assert "units unspecified" not in text
