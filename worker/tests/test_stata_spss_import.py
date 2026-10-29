"""Stata (.dta) import through pandas and the SPSS path, which needs pyreadstat (optional)."""
import json
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from squipi_worker import tools  # noqa: F401 - registers the tools
from squipi_worker.project import Project, Dataset
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments
from squipi_worker.tools import datasets as D

N = 120


def _ctx(tmp_path):
    allow = AllowList()
    return JobContext(Project(tmp_path / "project", allow), lambda *a: None, lambda: False), allow


def _stata_file(path: Path) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    df = pd.DataFrame({
        "hh_id": np.arange(1, N + 1),
        "region": pd.Categorical(rng.choice(["North", "South", "East"], N)),
        "income": rng.normal(1000, 150, N).round(1),
        "hh_size": rng.integers(1, 8, N),
    })
    df.to_stata(path, write_index=False, version=118,
                variable_labels={"income": "Monthly household income (SAR)", "region": "Region of residence", "hh_size": "Household size"})
    return df


def test_stata_import_profile_and_labels(tmp_path):
    ctx, allow = _ctx(tmp_path)
    path = tmp_path / "survey.dta"
    truth = _stata_file(path)
    allow.allow(path)
    imp = registry.call("import_dataset", {"path": str(path)}, ctx)
    assert imp["format"] == "stata" and imp["rows"] == N and imp["cols"] == 4
    assert imp["variable_labels"] == {"income": "Monthly household income (SAR)", "region": "Region of residence", "hh_size": "Household size"}
    assert imp["value_labels"] == {"region": ["East", "North", "South"]}
    assert imp["labelled_columns"] == 3 and imp["notes"] == []
    ds = ctx.project.dataset(imp["dataset_id"])
    assert ds.format == "stata" and ds.rows == N
    # value labels survive as pandas categories; the sidecar rides in df.attrs
    df = D.read_dataset(ds)
    assert str(df["region"].dtype) == "category" and set(df["region"].cat.categories) == {"North", "South", "East"}
    assert list(df["region"].astype(str)) == list(truth["region"].astype(str))
    assert df.attrs["variable_labels"]["income"] == "Monthly household income (SAR)"
    # nrows sampling path behaves like CSV: first n rows, labels still attached
    head = D.read_dataset(ds, nrows=7)
    assert len(head) == 7 and list(head["hh_id"]) == list(range(1, 8)) and head.attrs["variable_labels"]["hh_size"] == "Household size"
    prof = registry.call("profile_dataset", {"dataset_id": ds.id}, ctx)
    assert prof["rows"] == N and prof["format"] == "stata"
    assert prof["variable_labels"]["region"] == "Region of residence"
    by_name = {c["name"]: c for c in prof["columns"]}
    assert by_name["income"]["label"] == "Monthly household income (SAR)" and by_name["income"]["role"] == "numeric"
    assert by_name["region"]["role"] == "categorical" and "label" not in by_name["hh_id"]
    assert sorted(k for k, _ in by_name["region"]["top_values"]) == ["East", "North", "South"]
    json.dumps(prof, default=str)
    assert Path(prof["report_path"]).exists()


def test_stata_label_collision_falls_back_to_codes(tmp_path):
    ctx, allow = _ctx(tmp_path)
    path = tmp_path / "coded.dta"
    pd.DataFrame({"id": [1, 2, 3, 4], "answer": [1, 2, 3, 1]}).to_stata(path, write_index=False, version=118,
                                                                       value_labels={"answer": {1: "yes", 2: "yes", 3: "no"}})
    allow.allow(path)
    imp = registry.call("import_dataset", {"path": str(path)}, ctx)
    assert imp["rows"] == 4 and imp["notes"] and "not unique" in imp["notes"][0]
    df = D.read_dataset(ctx.project.dataset(imp["dataset_id"]))
    assert pd.api.types.is_integer_dtype(df["answer"]) and list(df["answer"]) == [1, 2, 3, 1]
    prof = registry.call("profile_dataset", {"dataset_id": imp["dataset_id"]}, ctx)
    assert any(f["flag"] == "value_labels_not_unique" for f in prof["quality_flags"])


def test_unsupported_extension_names_every_supported_format(tmp_path):
    with pytest.raises(InvalidArguments, match=r"CSV, Excel \(\.xlsx\), Parquet, Stata \(\.dta\) and SPSS"):
        D.detect_format(Path("notes.json"))
    assert D.detect_format(Path("a.DTA")) == "stata"
    assert D.detect_format(Path("a.zsav")) == "spss" and D.detect_format(Path("a.por")) == "spss"


def test_spss_path_needs_pyreadstat_or_reads_the_file(tmp_path):
    ctx, allow = _ctx(tmp_path)
    path = tmp_path / "survey.sav"
    have = importlib.util.find_spec("pyreadstat") is not None
    if not have:
        path.write_bytes(b"$FL2@(#) SPSS DATA FILE placeholder")   # content is irrelevant: the import must refuse before reading
        allow.allow(path)
        with pytest.raises(InvalidArguments, match="SPSS files need the pyreadstat package"):
            registry.call("import_dataset", {"path": str(path)}, ctx)
        with pytest.raises(InvalidArguments, match="pyreadstat"):
            D.read_dataset(Dataset(id="x", name="survey.sav", path=str(path), format="spss"))
        return
    import pyreadstat
    df = pd.DataFrame({"hh_id": np.arange(1, 11), "region": [1, 2, 1, 3, 2, 1, 3, 2, 1, 1], "income": np.linspace(900, 1100, 10)})
    pyreadstat.write_sav(df, str(path), column_labels=["Household id", "Region of residence", "Monthly income"],
                         variable_value_labels={"region": {1: "North", 2: "South", 3: "East"}})
    allow.allow(path)
    imp = registry.call("import_dataset", {"path": str(path)}, ctx)
    assert imp["format"] == "spss" and imp["rows"] == 10 and imp["variable_labels"]["income"] == "Monthly income"
    full = D.read_dataset(ctx.project.dataset(imp["dataset_id"]))
    assert str(full["region"].dtype) == "category" and set(full["region"].astype(str)) == {"North", "South", "East"}
    assert len(D.read_dataset(ctx.project.dataset(imp["dataset_id"]), nrows=4)) == 4
    prof = registry.call("profile_dataset", {"dataset_id": imp["dataset_id"]}, ctx)
    assert prof["variable_labels"]["region"] == "Region of residence"
