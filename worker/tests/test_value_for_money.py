"""Value-for-money tools checked against hand arithmetic: BCA, CEA league table, SROI, ex-ante projection."""
import json
import math
from pathlib import Path

import pytest

from squipi_worker import tools  # noqa: F401 - registers the tools
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, registry, InvalidArguments


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    base = tmp_path_factory.mktemp("vfm")
    return JobContext(Project(base / "project", AllowList()), lambda *a: None, lambda: False)


def _no_nan(result):
    text = json.dumps(result, default=str)
    assert "NaN" not in text and "Infinity" not in text
    return json.loads(text)


# ---------- benefit_cost_analysis

def test_bca_matches_hand_arithmetic(ctx):
    r = registry.call("benefit_cost_analysis", {"costs": {"by_year": [100, 0, 0], "source": "budget line 4"}, "benefits": {"by_year": [0, 60, 60]}, "discount_rate": 0.05,
                                                "benefit_streams": [{"name": "wages", "by_year": [0, 40, 40], "source": "follow-up survey"}, {"name": "health", "by_year": [0, 20, 20]}]}, ctx)
    pvb = 60 / 1.05 + 60 / 1.05 ** 2
    u = r["unrounded"]
    assert abs(u["pv_benefits"] - pvb) < 1e-6 and abs(u["pv_costs"] - 100) < 1e-9
    assert abs(u["npv"] - (pvb - 100)) < 1e-6 and abs(u["bcr"] - pvb / 100) < 1e-9 and abs(u["roi"] - (pvb - 100) / 100) < 1e-9
    assert r["pv_benefits"] == round(pvb, 2) == 111.56 and r["bcr"] == 1.12 and r["npv"] == 11.56 and r["roi_pct"] == 11.6
    # IRR: -100 + 60 x + 60 x² = 0 with x = 1/(1+r) → x = (−60 + √(3600 + 24000)) / 120 → r ≈ 13.07%
    x = (-60 + math.sqrt(3600 + 24000)) / 120
    irr_hand = 1 / x - 1
    assert abs(u["irr"] - irr_hand) < 1e-6 and abs(r["irr_pct"] - 13.07) < 0.1
    assert r["payback_year"] == 2                                   # cumulative discounted net: -100, -42.86, +11.56
    assert abs(u["break_even_benefit_scale"] - 100 / pvb) < 1e-9 and r["break_even_benefit_scale"] == 0.9
    streams = {s["name"]: s for s in r["benefit_streams"]}
    assert streams["wages"]["share_pct"] == 66.7 and streams["health"]["share_pct"] == 33.3 and streams["wages"]["source"] == "follow-up survey"
    assert streams["health"]["source"] == "no source given" and "benefit stream 'health'" in r["inputs_without_source"]
    assert r["n_inputs_from_user"] == len(r["assumptions"]) and any(a["source"] == "budget line 4" for a in r["assumptions"])
    assert len(r["sensitivity"]) == 4 * 4 and {s["discount_rate_pct"] for s in r["sensitivity"]} == {3.0, 5.0, 8.0, 10.0}
    central5 = next(s for s in r["sensitivity"] if s["scenario"] == "central" and s["discount_rate_pct"] == 5.0)
    assert central5["bcr"] == r["bcr"] and central5["npv"] == r["npv"]
    worst = next(s for s in r["sensitivity"] if s["scenario"].startswith("costs +20% and") and s["discount_rate_pct"] == 10.0)
    assert worst["pv_costs"] == 120.0 and abs(worst["pv_benefits"] - 0.8 * (60 / 1.1 + 60 / 1.21)) < 0.01
    table = r["calculation_table"]
    assert [row["year"] for row in table] == [0, 1, 2] and abs(table[2]["cumulative_pv_net"] - (pvb - 100)) < 1e-6 and table[1]["discount_factor"] == 1 / 1.05
    assert "benefits exceed costs" in r["summary_text"] and "IRR 13.1%" in r["summary_text"] and "payback in year 2" in r["summary_text"]
    assert Path(r["chart_path"]).exists() and Path(r["csv_path"]).exists()
    _no_nan(r)


def test_bca_annual_flows_irr_undefined_and_validation(ctx):
    r = registry.call("benefit_cost_analysis", {"costs": {"annual": 10, "years": 2, "start_year": 0}, "benefits": {"annual": 30, "years": 2}, "discount_rate": 0.0}, ctx)
    assert [row["cost"] for row in r["calculation_table"]] == [10, 10, 0] and [row["benefit"] for row in r["calculation_table"]] == [0, 30, 30]
    assert r["pv_costs"] == 20 and r["pv_benefits"] == 60 and r["bcr"] == 3.0 and r["payback_year"] == 1
    # net flows never negative → no IRR, with a note; never positive → no payback
    n = registry.call("benefit_cost_analysis", {"costs": {"by_year": [10, 0]}, "benefits": {"by_year": [20, 0]}, "discount_rate": 0.05}, ctx)
    assert n["irr_pct"] is None and any("IRR is undefined" in s for s in n["notes"]) and n["payback_year"] == 0
    never = registry.call("benefit_cost_analysis", {"costs": {"by_year": [100, 100]}, "benefits": {"by_year": [0, 10]}, "discount_rate": 0.05}, ctx)
    assert never["payback_year"] is None and any("never" in s for s in never["notes"]) and never["irr_pct"] is None
    with pytest.raises(InvalidArguments, match="costs.by_year\\[1\\] must be >= 0"):
        registry.call("benefit_cost_analysis", {"costs": {"by_year": [100, -5]}, "benefits": {"by_year": [0, 60]}, "discount_rate": 0.05}, ctx)
    with pytest.raises(InvalidArguments, match="must agree"):
        registry.call("benefit_cost_analysis", {"costs": {"by_year": [100]}, "benefits": {"by_year": [0, 60]}, "benefit_streams": [{"name": "a", "by_year": [0, 10]}]}, ctx)
    with pytest.raises(InvalidArguments, match="benefits.*benefit_streams"):
        registry.call("benefit_cost_analysis", {"costs": {"by_year": [100]}}, ctx)
    with pytest.raises(InvalidArguments, match="annual.*by_year"):
        registry.call("benefit_cost_analysis", {"costs": {"source": "x"}, "benefits": {"by_year": [1]}}, ctx)
    _no_nan(n); _no_nan(never)


# ---------- cost_effectiveness_analysis

OPTIONS = [{"name": "A", "total_cost": 100, "effect": 10, "n_beneficiaries": 50, "source": "trial 1"},
           {"name": "B", "total_cost": 150, "effect": 8},                                   # more cost, less effect than A → strictly dominated
           {"name": "C", "total_cost": 200, "effect": 12, "effect_low": 10, "effect_high": 14},  # ICER vs A = 100/2 = 50 > D's 20/4 = 5 → extended dominance
           {"name": "D", "total_cost": 220, "effect": 16}]                                  # after C drops out: ICER vs A = 120/6 = 20


def test_cea_league_table_dominance_and_icers(ctx):
    r = registry.call("cost_effectiveness_analysis", {"options": OPTIONS, "outcome_unit": "school years", "willingness_to_pay": 30, "willingness_to_pay_source": "ministry threshold"}, ctx)
    league = {row["option"]: row for row in r["league_table"]}
    assert [row["option"] for row in r["league_table"]] == ["B", "A", "C", "D"]          # sorted by effect
    assert league["B"]["dominance"] == "strictly dominated by A" and r["strictly_dominated"] == ["B"]
    assert league["C"]["dominance"].startswith("extended dominance") and "50.0" in league["C"]["dominance"] and "5.0" in league["C"]["dominance"] and r["extended_dominated"] == ["C"]
    assert league["A"]["icer"] is None and league["A"]["icer_vs"].startswith("reference")
    assert league["D"]["icer"] == 20.0 and league["D"]["icer_vs"] == "A" and league["D"]["incremental_cost"] == 120.0 and league["D"]["incremental_effect"] == 6.0
    assert r["frontier"] == ["A", "D"] and r["unrounded"]["icer"]["D"] == 20.0
    opts = {o["option"]: o for o in r["options"]}
    assert r["comparator"] == "A" and opts["A"]["icer_vs_comparator"] == "comparator"
    assert opts["C"]["icer_vs_comparator"] == 50.0 and opts["D"]["icer_vs_comparator"] == 20.0 and opts["B"]["icer_vs_comparator"] == -25.0
    assert opts["A"]["cost_per_unit"] == 10.0 and opts["C"]["cost_per_unit"] == 16.67 and opts["D"]["cost_per_unit"] == 13.75
    assert opts["A"]["cost_per_beneficiary"] == 2.0 and opts["A"]["effect_per_beneficiary"] == 0.2 and opts["B"]["cost_per_beneficiary"] is None
    assert opts["C"]["cost_per_unit_low"] == round(200 / 14, 2) and opts["C"]["cost_per_unit_high"] == 20.0 and opts["A"]["cost_per_unit_low"] is None
    assert {k: v["net_monetary_benefit"] for k, v in opts.items()} == {"A": 200.0, "B": 90.0, "C": 160.0, "D": 260.0}
    assert opts["A"]["source"] == "trial 1" and opts["B"]["source"] == "no source given" and "option 'B'" in r["inputs_without_source"]
    assert any(a["input"] == "willingness_to_pay" and a["source"] == "ministry threshold" for a in r["assumptions"])
    assert "D" in r["summary_text"] and "20.0" in r["summary_text"] and "Strictly dominated" in r["interpretation"][1] and "C" in r["interpretation"][1]
    assert Path(r["chart_path"]).exists() and Path(r["csv_path"]).exists()
    # explicit comparator
    c = registry.call("cost_effectiveness_analysis", {"options": OPTIONS, "outcome_unit": "school years", "comparator": "D"}, ctx)
    co = {o["option"]: o for o in c["options"]}
    assert co["D"]["icer_vs_comparator"] == "comparator" and co["A"]["icer_vs_comparator"] == 20.0 and co["A"]["net_monetary_benefit"] is None
    _no_nan(r); _no_nan(c)


def test_cea_refuses_bad_options(ctx):
    with pytest.raises(InvalidArguments, match="effect of option 'B' must be > 0"):
        registry.call("cost_effectiveness_analysis", {"options": [{"name": "A", "total_cost": 1, "effect": 1}, {"name": "B", "total_cost": 1, "effect": 0}], "outcome_unit": "u"}, ctx)
    with pytest.raises(InvalidArguments, match="total_cost of option 'A' must be > 0"):
        registry.call("cost_effectiveness_analysis", {"options": [{"name": "A", "total_cost": -1, "effect": 1}, {"name": "B", "total_cost": 1, "effect": 1}], "outcome_unit": "u"}, ctx)
    with pytest.raises(InvalidArguments, match="at least 2"):
        registry.call("cost_effectiveness_analysis", {"options": [{"name": "A", "total_cost": 1, "effect": 1}], "outcome_unit": "u"}, ctx)
    with pytest.raises(InvalidArguments, match="distinct"):
        registry.call("cost_effectiveness_analysis", {"options": [{"name": "A", "total_cost": 1, "effect": 1}, {"name": "A", "total_cost": 2, "effect": 2}], "outcome_unit": "u"}, ctx)
    with pytest.raises(InvalidArguments, match="comparator 'Z'"):
        registry.call("cost_effectiveness_analysis", {"options": OPTIONS, "outcome_unit": "u", "comparator": "Z"}, ctx)
    with pytest.raises(InvalidArguments, match="effect_low ≤ effect ≤ effect_high"):
        registry.call("cost_effectiveness_analysis", {"options": [{"name": "A", "total_cost": 1, "effect": 1, "effect_low": 2, "effect_high": 3}, {"name": "B", "total_cost": 1, "effect": 1}], "outcome_unit": "u"}, ctx)


# ---------- sroi_analysis

SROI_ARGS = {"investment": 1000, "discount_rate": 0.05, "outcomes": [
    {"stakeholder": "participants", "outcome": "jobs", "quantity": 10, "financial_proxy": 50, "proxy_source": "wage data", "deadweight_pct": 20, "attribution_pct": 10},
    {"stakeholder": "state", "outcome": "benefit savings", "quantity": 5, "financial_proxy": 100, "deadweight_pct": 10, "displacement_pct": 10, "duration_years": 2, "drop_off_pct": 50}]}


def test_sroi_worked_example(ctx):
    r = registry.call("sroi_analysis", SROI_ARGS, ctx)
    pv1 = 10 * 50 * 0.8 * 0.9 / 1.05                                         # 360 in year 1, one year
    y1 = 5 * 100 * 0.9 * 0.9                                                 # 405 in year 1
    pv2 = y1 / 1.05 + y1 * 0.5 / 1.05 ** 2                                   # drop-off 50% in year 2
    ratio = (pv1 + pv2) / 1000
    u = r["unrounded"]
    assert abs(u["pv_by_outcome"][0] - pv1) < 1e-6 and abs(u["pv_by_outcome"][1] - pv2) < 1e-6
    assert abs(u["sroi_ratio"] - ratio) < 1e-6 and r["sroi_ratio"] == round(ratio, 2) == 0.91 and r["ratio_text"] == "0.91 : 1"
    # conservative: every adjustment worsened by 10 points
    c1 = 500 * 0.7 * 0.9 * 0.8 / 1.05
    c2y1 = 500 * 0.8 * 0.8 * 0.9
    c2 = c2y1 / 1.05 + c2y1 * 0.4 / 1.05 ** 2
    assert abs(u["conservative_ratio"] - (c1 + c2) / 1000) < 1e-6 and r["conservative_ratio_text"].endswith(" : 1")
    outs = {o["outcome"]: o for o in r["outcomes"]}
    assert outs["jobs"]["gross_value"] == 500.0 and outs["jobs"]["impact_year_1"] == 360.0 and outs["benefit savings"]["impact_year_1"] == 405.0
    assert outs["benefit savings"]["duration_years"] == 2
    assert r["calculation_table"][1]["pv_by_year"] == [float(f"{y1 / 1.05:.6g}"), float(f"{y1 * 0.5 / 1.05 ** 2:.6g}")]   # year 1 and year 2 discounted
    assert any(n == "proxy for benefit savings (state) has no stated source" for n in r["notes"]) and "no stated source" in r["interpretation"][-1]
    assert r["by_stakeholder"][0]["stakeholder"] == "state" and abs(r["by_stakeholder"][0]["share_pct"] - round(pv2 / (pv1 + pv2) * 100, 1)) < 1e-9
    assert sum(s["share_pct"] for s in r["by_stakeholder"]) == pytest.approx(100, abs=0.11)
    sens = {(s["outcome"], s["change"]): s["sroi_ratio"] for s in r["sensitivity"]}
    assert sens[("jobs", "proxy +25%")] == round((pv1 * 1.25 + pv2) / 1000, 2) and sens[("jobs", "proxy −25%")] == round((pv1 * 0.75 + pv2) / 1000, 2)
    assert sens[("jobs", "deadweight +10 points")] == round((10 * 50 * 0.7 * 0.9 / 1.05 + pv2) / 1000, 2) and ("(all)", "all deadweight −10 points") in sens
    assert len(r["sensitivity"]) == 2 * 4 + 2 and any("SROI Network" in s or "Guide to Social Return" in s for s in r["interpretation"])
    assert r["pv_investment"] == 1000.0 and Path(r["chart_path"]).exists() and Path(r["csv_path"]).exists()
    _no_nan(r)


def test_sroi_fraction_inputs_horizon_and_validation(ctx):
    args = json.loads(json.dumps(SROI_ARGS))
    args["outcomes"][0]["deadweight_pct"] = 0.2                                  # fraction, read as 20% with a note
    f = registry.call("sroi_analysis", args, ctx)
    base = registry.call("sroi_analysis", SROI_ARGS, ctx)
    assert abs(f["unrounded"]["sroi_ratio"] - base["unrounded"]["sroi_ratio"]) < 1e-9 and any("read as a fraction (20%)" in n for n in f["notes"])
    explicit = json.loads(json.dumps(SROI_ARGS)); explicit["pct_scale"] = "fraction"
    for o in explicit["outcomes"]:
        for k in ("deadweight_pct", "attribution_pct", "displacement_pct", "drop_off_pct"):
            if k in o:
                o[k] = o[k] / 100
    e = registry.call("sroi_analysis", explicit, ctx)
    assert abs(e["unrounded"]["sroi_ratio"] - base["unrounded"]["sroi_ratio"]) < 1e-9 and not any("read as a fraction" in n for n in e["notes"])
    h = registry.call("sroi_analysis", {**SROI_ARGS, "years": 1}, ctx)
    assert abs(h["unrounded"]["pv_by_outcome"][1] - 405 / 1.05) < 1e-6 and any("truncated" in n for n in h["notes"])
    inv = registry.call("sroi_analysis", {**SROI_ARGS, "investment": {"by_year": [500, 525], "source": "grant letter"}}, ctx)
    assert abs(inv["unrounded"]["pv_investment"] - (500 + 525 / 1.05)) < 1e-9 and any(a["source"] == "grant letter" for a in inv["assumptions"])
    bad = json.loads(json.dumps(SROI_ARGS)); bad["outcomes"][0]["deadweight_pct"] = 120
    with pytest.raises(InvalidArguments, match="deadweight_pct.*between 0 and 100"):
        registry.call("sroi_analysis", bad, ctx)
    bad["outcomes"][0]["deadweight_pct"] = -5
    with pytest.raises(InvalidArguments, match="between 0 and 100"):
        registry.call("sroi_analysis", bad, ctx)
    bad["outcomes"][0]["deadweight_pct"] = 20; bad["outcomes"][0]["financial_proxy"] = 0
    with pytest.raises(InvalidArguments, match="financial_proxy of outcome 1 \\('jobs'\\) must be > 0"):
        registry.call("sroi_analysis", bad, ctx)
    with pytest.raises(InvalidArguments, match="investment must be > 0"):
        registry.call("sroi_analysis", {**SROI_ARGS, "investment": 0}, ctx)
    _no_nan(f); _no_nan(inv)


# ---------- ex_ante_impact_projection

EX_ANTE = {"target_population": 1000, "take_up_rate": 0.5, "effect_per_beneficiary": 2, "outcome_unit": "school years", "value_per_unit": 10,
           "cost_per_beneficiary": 100, "fixed_costs": 5000, "discount_rate": 0.05, "effect_source": "RCT in district X"}


def test_ex_ante_deterministic_without_ranges(ctx):
    r = registry.call("ex_ante_impact_projection", EX_ANTE, ctx)
    u = r["unrounded"]
    assert u["beneficiaries"] == 500 and u["total_outcome_units"] == 1000 and u["total_costs"] == 5000 + 100 * 500 == 55000
    assert u["pv_benefits"] == 10 * 1000 / 1.05 and u["npv"] == 10 * 1000 / 1.05 - 55000 and u["bcr"] == (10 * 1000 / 1.05) / 55000
    assert r["cost_per_beneficiary"] == 110.0 and r["cost_per_outcome_unit"] == 55.0 and r["pv_benefits"] == 9523.81 and r["bcr"] == 0.17
    assert u["break_even_effect_per_beneficiary"] == pytest.approx(55000 / (500 * 10 / 1.05), rel=1e-12)   # = 11.55
    assert u["break_even_take_up_rate"] is None and any("break-even take-up is undefined" in n for n in r["notes"])
    assert r["simulation"] is None and r["sensitivity"] == [] and r["ranged_inputs"] == [] and any("deterministic" in n for n in r["notes"])
    assert r["interpretation"][0].startswith("This is an ex-ante projection") and "not an evaluation result" in r["interpretation"][0]
    assert any(a["input"] == "effect_per_beneficiary" and a["source"] == "RCT in district X" for a in r["assumptions"]) and "take_up_rate" in r["inputs_without_source"]
    assert Path(r["chart_path"]).exists() and Path(r["csv_path"]).exists()
    # with a higher value the break-even take-up exists: fixed / (pop × (value × effect / 1.05 − cpb))
    v = registry.call("ex_ante_impact_projection", {**EX_ANTE, "value_per_unit": 200}, ctx)
    assert v["unrounded"]["break_even_take_up_rate"] == pytest.approx(5000 / (1000 * (200 * 2 / 1.05 - 100)), rel=1e-12)
    # multi-year with decay: units = 500 × 2 × (1 + 0.9 + 0.81); PV uses (0.9^(k-1))/(1.05^k)
    m = registry.call("ex_ante_impact_projection", {**EX_ANTE, "years": 3, "effect_decay_pct": 10}, ctx)
    assert m["unrounded"]["total_outcome_units"] == pytest.approx(1000 * (1 + 0.9 + 0.81)) and m["total_outcome_units"] == 2710
    assert m["unrounded"]["pv_benefits"] == pytest.approx(10 * 1000 * (1 / 1.05 + 0.9 / 1.05 ** 2 + 0.81 / 1.05 ** 3))
    no_value = registry.call("ex_ante_impact_projection", {k: v for k, v in EX_ANTE.items() if k != "value_per_unit"}, ctx)
    assert no_value["pv_benefits"] is None and no_value["bcr"] is None and no_value["total_outcome_units"] == 1000
    _no_nan(r); _no_nan(m); _no_nan(no_value)


def test_ex_ante_ranges_give_intervals_and_are_reproducible(ctx):
    args = {**EX_ANTE, "take_up_low": 0.3, "take_up_high": 0.7, "effect_low": 1.0, "effect_high": 3.5, "value_low": 5, "value_high": 20, "simulations": 3000}
    r = registry.call("ex_ante_impact_projection", args, ctx)
    s = r["simulation"]
    assert s["simulations"] == 3000 and s["seed"] == 0 and r["ranged_inputs"] == ["take_up_rate", "effect_per_beneficiary", "value_per_unit"]
    for key, central in (("beneficiaries_90", r["beneficiaries"]), ("total_outcome_units_90", r["total_outcome_units"]), ("total_costs_90", r["total_costs"]),
                         ("pv_benefits_90", r["pv_benefits"]), ("npv_90", r["npv"]), ("bcr_90", r["bcr"]), ("roi_90", r["roi"])):
        lo, hi = s[key]
        assert lo < hi and lo <= central <= hi, (key, lo, central, hi)
    assert 0 <= s["p_bcr_above_1"] <= 1 and 0 <= s["p_npv_positive"] <= 1 and s["p_bcr_above_1"] == s["p_npv_positive"]
    again = registry.call("ex_ante_impact_projection", args, ctx)
    assert again["simulation"] == s and again["sensitivity"] == r["sensitivity"]
    tornado = {t["input"]: t for t in r["sensitivity"]}
    assert set(tornado) == {"take_up_rate", "effect_per_beneficiary", "value_per_unit"} and [t["swing"] for t in r["sensitivity"]] == sorted((t["swing"] for t in r["sensitivity"]), reverse=True)
    # tornado rows are exact: NPV at take-up 0.3 holding effect 2 and value 10 central
    assert tornado["take_up_rate"]["npv_at_low"] == round(10 * 300 * 2 / 1.05 - (5000 + 100 * 300), 2)
    assert "90% interval for BCR" in r["interpretation"][2] and "P(BCR > 1)" in r["interpretation"][2] and "bcr_distribution" in r["chart_path"]
    # normal draws from a standard error; effect may go negative, results stay finite JSON
    n = registry.call("ex_ante_impact_projection", {**EX_ANTE, "effect_se": 1.5, "simulations": 500}, ctx)
    assert n["simulation"]["total_outcome_units_90"][0] < 1000 < n["simulation"]["total_outcome_units_90"][1] and n["ranged_inputs"] == ["effect_per_beneficiary"]
    assert n["sensitivity"][0]["low"] == pytest.approx(2 - 1.6448536269514722 * 1.5, rel=1e-5)
    # only take-up varies and fixed costs are zero → BCR identical in every draw; no histogram, a note instead
    flat = registry.call("ex_ante_impact_projection", {"target_population": 1000, "take_up_rate": 0.5, "take_up_low": 0.3, "take_up_high": 0.7, "effect_per_beneficiary": 2,
                                                      "outcome_unit": "u", "value_per_unit": 200, "cost_per_beneficiary": 100}, ctx)
    assert flat["simulation"]["bcr_90"][0] == flat["simulation"]["bcr_90"][1] == flat["bcr"] and "outcome_range" in flat["chart_path"]
    assert any("same in every draw" in n for n in flat["notes"]) and flat["simulation"]["beneficiaries_90"][0] < 500 < flat["simulation"]["beneficiaries_90"][1]
    _no_nan(flat)
    only_units = registry.call("ex_ante_impact_projection", {k: v for k, v in args.items() if not k.startswith("value")}, ctx)
    assert only_units["simulation"]["bcr_90"] is None if "bcr_90" in only_units["simulation"] else True
    assert "outcome_range" in only_units["chart_path"] and only_units["sensitivity"][0]["total_outcome_units_at_low"] is not None
    _no_nan(r); _no_nan(n); _no_nan(only_units)


def test_ex_ante_rejects_bad_inputs(ctx):
    with pytest.raises(InvalidArguments, match="take_up_rate: must be <= 1"):
        registry.call("ex_ante_impact_projection", {**EX_ANTE, "take_up_rate": 1.5}, ctx)
    with pytest.raises(InvalidArguments, match="cost_per_beneficiary: must be >= 0"):
        registry.call("ex_ante_impact_projection", {**EX_ANTE, "cost_per_beneficiary": -1}, ctx)
    with pytest.raises(InvalidArguments, match="effect_low ≤ effect_per_beneficiary ≤ effect_high"):
        registry.call("ex_ante_impact_projection", {**EX_ANTE, "effect_low": 3, "effect_high": 4}, ctx)
    with pytest.raises(InvalidArguments, match="both take_up_low and take_up_high"):
        registry.call("ex_ante_impact_projection", {**EX_ANTE, "take_up_low": 0.1}, ctx)
    with pytest.raises(InvalidArguments, match="not both"):
        registry.call("ex_ante_impact_projection", {**EX_ANTE, "effect_low": 1, "effect_high": 3, "effect_se": 0.2}, ctx)
    with pytest.raises(InvalidArguments, match="target_population must be > 0"):
        registry.call("ex_ante_impact_projection", {**EX_ANTE, "target_population": 0}, ctx)


def test_vfm_tools_registered_and_json_safe(ctx):
    for name in ("benefit_cost_analysis", "cost_effectiveness_analysis", "sroi_analysis", "ex_ante_impact_projection"):
        t = registry.get(name)
        assert t is not None and t.needs_model is False
    results = [registry.call("benefit_cost_analysis", {"costs": {"by_year": [100, 0, 0]}, "benefits": {"by_year": [0, 60, 60]}}, ctx),
               registry.call("cost_effectiveness_analysis", {"options": OPTIONS, "outcome_unit": "school years"}, ctx),
               registry.call("sroi_analysis", SROI_ARGS, ctx),
               registry.call("ex_ante_impact_projection", {**EX_ANTE, "take_up_low": 0.2, "take_up_high": 0.8}, ctx)]
    for r in results:
        back = _no_nan(r)
        assert back["summary_text"] and back["interpretation"] and back["assumptions"] and back["calculation_table"] and back["currency"] == "USD"
        assert isinstance(back["n_inputs_from_user"], int) and isinstance(back["notes"], list) and Path(back["chart_path"]).exists() and Path(back["csv_path"]).exists()
