"""field_checks on a synthetic field survey (seed 5): 800 interviews by 8 enumerators over 20 days with known injected problems."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from squipi_worker import tools  # noqa: F401 - registers the tools
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments
from squipi_worker.tools.field_checks import parse_rule

N, ENUMS, DAYS, PER_DAY = 800, 8, 20, 5
SHORT_ROWS = list(range(0, 10))            # 10 interviews under 10 minutes
NIGHT_ROWS = [10, 11, 12]                  # 3 interviews started at 23:00
OUTLIER_ROWS = [20, 120, 420]              # 3 asset values about 5 SD above the mean, one each for E1, E2, E5
SKIP_ROWS = list(range(30, 35))            # 5 skip-rule violations
CONSENT_ROWS = [40, 41]                    # 2 missing consents
FAR_ROWS = [50, 51, 52, 53]                # 4 GPS points 2 degrees away
SAME_POINT_ROWS = [60, 61]                 # 2 different ids on one GPS point
DUP_SOURCE_ROWS = [100, 101, 102, 105, 106, 107]   # 6 ids duplicated once each
INFLATED_ENUM = "E3"                       # +0.75 SD on consumption and 3x missingness
BURST_ROWS = list(range(775, 795))         # E8's days 15-18 moved onto day 3 (25 interviews that day)


def survey() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    base = pd.Timestamp("2026-10-01")
    rows = []
    for e in range(ENUMS):
        for d in range(DAYS):
            for j in range(PER_DAY):
                rows.append({"hh_id": len(rows) + 1, "enum_id": f"E{e + 1}", "day": d,
                             "start": base + pd.Timedelta(days=d, hours=8 + 2 * j, minutes=int(rng.integers(0, 50)))})
    df = pd.DataFrame(rows)
    minutes = np.clip(rng.normal(45, 10, N), 12, 120)
    minutes[SHORT_ROWS] = rng.uniform(3, 8, len(SHORT_ROWS))
    df.loc[NIGHT_ROWS, "start"] = df.loc[NIGHT_ROWS, "start"].dt.normalize() + pd.Timedelta(hours=23)
    df["end"] = df["start"] + pd.to_timedelta(np.round(minutes * 60), unit="s")
    df["consumption"] = rng.normal(1000, 200, N).round(1)
    df.loc[df["enum_id"] == INFLATED_ENUM, "consumption"] += 150
    for e in range(ENUMS):                                   # exactly 5 missing per enumerator, 15 for E3
        idx = df.index[df["enum_id"] == f"E{e + 1}"]
        k = 15 if f"E{e + 1}" == INFLATED_ENUM else 5
        df.loc[idx[20:20 + k], "consumption"] = np.nan
    df["assets"] = rng.normal(50, 12, N).round(1)
    df.loc[OUTLIER_ROWS, "assets"] = 116.0
    df["has_business"] = rng.integers(0, 2, N)
    df["business_income"] = np.where(df["has_business"] == 1, rng.normal(300, 50, N).round(0), np.nan)
    df.loc[SKIP_ROWS, "has_business"] = 0
    df.loc[SKIP_ROWS, "business_income"] = 250.0
    df["consent"] = 1
    df.loc[CONSENT_ROWS, "consent"] = np.nan
    df["lat"] = 24.47 + rng.normal(0, 0.03, N)
    df["lon"] = 39.61 + rng.normal(0, 0.03, N)
    df.loc[FAR_ROWS, "lat"] += 2.0
    df.loc[SAME_POINT_ROWS, ["lat", "lon"]] = [24.4712345, 39.6112345]
    df["respondent_name"] = [f"Person {i}" for i in range(N)]
    df.loc[BURST_ROWS, "start"] = df.loc[BURST_ROWS, "start"] - pd.to_timedelta(df.loc[BURST_ROWS, "day"] - 3, unit="D")
    df.loc[BURST_ROWS, "end"] = df.loc[BURST_ROWS, "start"] + pd.to_timedelta(np.round(minutes[BURST_ROWS] * 60), unit="s")
    df["duration_min"] = (df["end"] - df["start"]).dt.total_seconds() / 60
    df["date"] = df["start"].dt.strftime("%Y-%m-%d")
    df = pd.concat([df, df.loc[DUP_SOURCE_ROWS]], ignore_index=True)
    return df.drop(columns="day")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("field")
    p = Project(base / "project", AllowList())
    ctx = JobContext(p, lambda *a: None, lambda: False)
    df = survey()
    path = base / "field_survey.csv"
    df.to_csv(path, index=False)
    p.allow.allow(path)
    ds = registry.call("import_dataset", {"path": str(path)}, ctx)["dataset_id"]
    return {"ctx": ctx, "ds": ds, "df": df, "path": path}


ARGS = {"id_column": "hh_id", "enumerator_column": "enum_id", "start_time": "start", "end_time": "end", "gps_lat": "lat", "gps_lon": "lon",
        "consent_column": "consent", "key_variables": ["consumption", "assets"],
        "skip_rules": [{"if": "has_business == 0", "then_missing": ["business_income"]},
                       {"if": "has_business in [0, 2]", "then_missing": ["business_income"]},
                       {"if": "assets > 100000", "then_missing": ["consumption"]}],
        "expected_sample": 1000}


def test_every_injected_problem_is_counted(world):
    ctx, ds = world["ctx"], world["ds"]
    r = registry.call("field_checks", {"dataset_id": ds, **ARGS}, ctx)
    c = {row["name"]: row for row in r["checks"]}
    assert r["rows"] == N + len(DUP_SOURCE_ROWS) and r["unique_ids"] == N
    assert c["duplicate_ids"]["count"] == 6 and c["duplicate_ids"]["rows"] == 12 and c["duplicate_ids"]["status"] == "fail"
    assert c["missing_ids"]["count"] == 0 and c["missing_ids"]["status"] == "pass"
    assert c["duration_too_short"]["count"] == 10 and c["duration_too_short"]["status"] == "warn"
    assert c["duration_too_long"]["count"] == 0 and c["duration_too_long"]["status"] == "pass"
    assert c["outside_daily_hours"]["count"] == 3 and c["outside_daily_hours"]["status"] == "warn"
    assert c["productivity"]["count"] == 1 and c["productivity"]["median_per_day"] == 5
    prod = {row["enumerator"]: row for row in r["tables"]["productivity"]}
    assert prod["E8"]["max_per_day"] == 25 and prod["E8"]["flagged_days"] == 1 and prod["E1"]["flagged_days"] == 0
    assert c["enumerator_effects"]["count"] == 1 and c["enumerator_effects"]["status"] == "warn"
    eff = {row["column"]: row for row in r["tables"]["enumerator_effects"]}
    assert eff["consumption"]["flag"] is True and eff["consumption"]["most_different"] == INFLATED_ENUM and eff["consumption"]["p"] < 0.01 and eff["consumption"]["spread_d"] > 0.5
    assert eff["assets"]["flag"] is False
    assert abs(c["outliers"]["count"] - 3) <= 1 and c["outliers"]["mad_outliers"] >= 3
    outl = {row["column"]: row for row in r["tables"]["outliers"]}
    assert outl["assets"]["sd_outliers"] == 3 and outl["consumption"]["sd_outliers"] == 0
    assert c["missingness"]["count"] == 1
    miss = {row["column"]: row for row in r["tables"]["missingness"]}
    assert miss["consumption"]["flagged_enumerators"] == [INFLATED_ENUM] and miss["consumption"]["by_enumerator"][INFLATED_ENUM] > 2 * miss["consumption"]["median_enumerator_pct"]
    assert miss["assets"]["flagged_enumerators"] == []
    assert c["skip_rules"]["count"] == 5 and c["skip_rules"]["status"] == "fail"
    rules = {row["rule"]: row for row in r["tables"]["skip_rules"]}
    assert rules["has_business == 0"]["violations"] == 5 and rules["assets > 100000"]["violations"] == 0
    assert rules["has_business in [0, 2]"]["violations"] == 5 and rules["has_business in [0, 2]"]["rows_where_condition_holds"] == rules["has_business == 0"]["rows_where_condition_holds"]
    assert c["consent"]["count"] == 2 and c["consent"]["missing"] == 2 and c["consent"]["refused"] == 0 and c["consent"]["status"] == "fail"
    assert c["gps_missing"]["count"] == 0 and c["gps_duplicates"]["count"] == 2 and abs(c["gps_far"]["count"] - 4) <= 1
    assert c["progress"]["status"] == "pass" and c["progress"]["share_achieved"] == 0.8 and c["progress"]["unique_ids"] == N
    assert len(r["tables"]["by_date"]) == DAYS and sum(row["interviews"] for row in r["tables"]["by_date"]) == N + 6
    assert set(r["failed"]) == {"duplicate_ids", "skip_rules", "consent"}
    assert r["summary_text"].startswith(f"{N + 6:,} rows checked: 3 checks failed")
    assert any("Call back" in s and "10 interviews" in s for s in r["next_steps"])
    assert any(INFLATED_ENUM in s and "missing" in s for s in r["next_steps"])
    assert any("1,000" in s or "1000" in s for s in r["next_steps"])
    # issues table carries ids, never names or values of respondents
    issues = pd.read_csv(r["issues_csv"], dtype=str, keep_default_na=False)
    assert Path(r["issues_csv"]).exists() and r["issues"] == len(issues)
    short_ids = sorted(int(i) for i in issues.loc[issues["check"] == "duration_too_short", "id"])
    assert short_ids == [i + 1 for i in SHORT_ROWS]
    assert sorted(set(int(i) for i in issues.loc[issues["check"] == "duplicate_ids", "id"])) == [i + 1 for i in DUP_SOURCE_ROWS]
    assert sorted(int(i) for i in issues.loc[issues["check"] == "consent", "id"]) == [i + 1 for i in CONSENT_ROWS]
    skip_issues = issues[issues["check"] == "skip_rules"]
    assert sorted(set(int(i) for i in skip_issues["id"])) == [i + 1 for i in SKIP_ROWS] and len(skip_issues) == 10   # two rules catch the same 5 rows
    assert skip_issues["detail"].str.contains("business_income filled although").all()
    assert sorted(int(i) for i in issues.loc[issues["check"] == "gps_far", "id"]) == [i + 1 for i in FAR_ROWS]
    assert "Person" not in Path(r["issues_csv"]).read_text() and "Person" not in json.dumps(r)
    # R script, charts, JSON
    script = Path(r["script_r"]).read_text()
    assert Path(r["script_r"]).name == "field_checks.R" and str(world["path"]) in script
    for name in ("duplicate_ids", "missing_ids", "duration_too_short", "duration_too_long", "outliers", "missingness"):
        assert name in script
    assert "dplyr" not in script.replace("no dplyr", "") and "library(" not in script
    assert [ch["id"] for ch in r["charts"]] == ["interviews_per_day", "duration"] and all(Path(ch["path"]).exists() for ch in r["charts"])
    assert r["chart_path"] == r["charts"][0]["path"] and r["caption"]
    json.dumps(r)                                     # strictly JSON-serialisable, no default


def test_duration_column_and_date_column_path(world):
    ctx, ds = world["ctx"], world["ds"]
    r = registry.call("field_checks", {"dataset_id": ds, "id_column": "hh_id", "enumerator_column": "enum_id", "duration_column": "duration_min",
                                       "date_column": "date", "min_duration_minutes": 10, "max_duration_minutes": 60}, ctx)
    c = {row["name"]: row for row in r["checks"]}
    assert c["duration_too_short"]["count"] == 10 and c["duration_too_long"]["count"] > 0
    assert c["productivity"]["count"] == 1 and "outside_daily_hours" in {s["check"] for s in r["skipped"]}
    assert {s["check"] for s in r["skipped"]} >= {"skip_rules", "consent", "gps"}
    assert "missingness" not in c and "outliers" not in c
    json.dumps(r)


def test_skip_rule_parser_is_strict(world):
    ctx, ds, df = world["ctx"], world["ds"], world["df"]
    for bad in ("has_business = 0", "has_business == 0 or assets > 1", "has_business in 0", "has_business in []", "__import__('os')",
                "has_business == __import__('os').system('x')", "assets > ten"):
        with pytest.raises(InvalidArguments):
            registry.call("field_checks", {"dataset_id": ds, "id_column": "hh_id", "skip_rules": [{"if": bad, "then_missing": ["business_income"]}]}, ctx)
    with pytest.raises(InvalidArguments, match="not in this dataset"):
        parse_rule({"if": "nope == 1", "then_missing": ["assets"]}, df)
    with pytest.raises(InvalidArguments, match="then_missing"):
        parse_rule({"if": "has_business == 0", "then_missing": []}, df)
    r = parse_rule({"if": "enum_id in [E1, \"E2\", 'E3']", "then_missing": ["assets"]}, df)
    assert r["op"] == "in" and r["values"] == ["E1", "E2", "E3"] and r["column"] == "enum_id"
    r = parse_rule({"if": "assets >= 12.5", "then_missing": ["consumption"]}, df)
    assert r["op"] == ">=" and r["values"] == [12.5]


def test_personal_data_is_refused_where_values_would_print(world):
    ctx, ds = world["ctx"], world["ds"]
    with pytest.raises(InvalidArguments, match="personal data"):
        registry.call("field_checks", {"dataset_id": ds, "id_column": "hh_id", "key_variables": ["respondent_name"]}, ctx)
    with pytest.raises(InvalidArguments, match="respondent code"):
        registry.call("field_checks", {"dataset_id": ds, "id_column": "respondent_name"}, ctx)
    with pytest.raises(InvalidArguments, match="together"):
        registry.call("field_checks", {"dataset_id": ds, "id_column": "hh_id", "gps_lat": "lat"}, ctx)
