import pandas as pd
from squipi_worker.tools.meaning import expand_name, variable_meaning, interpret, readings, glossary, _same_measure


def test_lexicon_expands_trade_and_survey_names():
    assert "Economic Complexity Index" in expand_name("eci_hs92") and "1992" in expand_name("eci_hs92")
    assert "rank" in expand_name("eci_rank_sitc") and "SITC" in expand_name("eci_rank_sitc")
    assert "projection" in expand_name("growth_proj")
    assert "Indian rupees" in expand_name("monthly_household_income_inr")


def test_variable_meaning_uses_values_and_missingness():
    s = pd.Series([1.0, 2.0, 3.0, None])
    m = variable_meaning("growth_proj", {"role": "numeric", "missing_pct": 25.0}, s)
    assert "growth" in m and "observed 1 to 3" in m and "25% missing" in m


def test_rank_and_classification_pairs_are_by_construction():
    assert _same_measure("eci_sitc", "eci_rank_sitc") == "rank"
    assert _same_measure("eci_sitc", "eci_hs92") == "classification"
    assert _same_measure("income", "food_insecure_days") is None
    r = interpret({"kind": "correlation", "a": "eci_sitc", "b": "eci_rank_sitc", "numbers": {"r": -0.98}}, {}, "NC")
    assert "by construction" in r and "Specialist reading" in r and "Plain reading" in r
    r = interpret({"kind": "correlation", "a": "eci_sitc", "b": "eci_hs92", "numbers": {"r": 0.97}}, {}, "NC")
    assert "two trade classifications" in r
    r = interpret({"kind": "correlation", "a": "income", "b": "wellbeing", "numbers": {"r": 0.4}}, {}, "NC")
    rd = readings({"kind": "correlation", "a": "income", "b": "wellbeing", "numbers": {"r": 0.4}}, {}, "NC")
    assert "omitted variables" in rd["specialist"] and rd["specialist"].endswith("NC") and "does not show that one causes" in rd["plain"]


def test_trend_and_group_readings_name_the_caveat():
    t = readings({"kind": "trend", "outcome": "eci_hs92", "over": "year"}, {}, "NC")
    assert "composition effect" in t["specialist"] and "without any one of them changing" in t["plain"]
    g = readings({"kind": "group_difference", "outcome": "income", "by": "region", "numbers": {"eta_squared": 0.2}}, {}, "NC")
    assert "confounding" in g["specialist"] and "η² = 0.20" in g["specialist"] and "does not show that region causes" in g["plain"]


def test_glossary_is_bounded():
    cols = {f"v{i}": {"role": "numeric"} for i in range(30)}
    assert len(glossary(list(cols), cols, None, limit=16)) == 16
