"""Value for money and ex-ante projection: benefit_cost_analysis, cost_effectiveness_analysis, sroi_analysis, ex_ante_impact_projection.

Models propose, code computes. Every monetary proxy, price and effect size is an input supplied by the caller with an optional
`source` string that is echoed back in `assumptions` and the calculation table; nothing here invents a proxy. The wording in
`interpretation` is a fixed template filled with the computed numbers. Money is rounded to 2 decimals (0 above 1e5), ratios to 2,
percentages to 1; `unrounded` carries the full-precision headline figures for machine checks."""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from .. import svg
from .stats import _save_table, _office_png

NO_SOURCE = "no source given"
MAX_YEARS = 200
MAX_CHART_YEARS = 40
DEFAULT_RATES = [0.03, 0.05, 0.08, 0.10]
Z90 = 1.6448536269514722

NOT_EVIDENCE = ("The costs, benefits, proxies and effect sizes are inputs, not estimates made here; the result is only as good as their sources. "
                "A positive return is not evidence that the intervention caused the outcomes.")
SROI_TEXT = ("SROI (Nicholls et al., A Guide to Social Return on Investment, 2012): value = quantity × financial proxy, reduced for deadweight "
             "(what would have happened anyway), displacement (outcomes that moved elsewhere) and attribution (the share due to others), declining by "
             "drop-off each year after the first and discounted to today. The ratio compares the present value of impact with the present value of the "
             "investment; it is not comparable across studies that use different proxies.")
PROJECTION_TEXT = ("This is an ex-ante projection from the stated assumptions (target population, take-up, effect per beneficiary, values and costs), "
                   "not an evaluation result. It tells you what the intervention would return if the assumptions hold; it is no evidence that they do.")


# ---------- helpers

def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _num(v: Any, what: str, *, minimum: Optional[float] = None, maximum: Optional[float] = None, strict_min: bool = False, default: Optional[float] = None) -> float:
    if v is None:
        if default is None:
            raise InvalidArguments(f"{what} is required")
        return float(default)
    if not _finite(v):
        raise InvalidArguments(f"{what} must be a finite number")
    f = float(v)
    if minimum is not None and (f < minimum or (strict_min and f <= minimum)):
        raise InvalidArguments(f"{what} must be {'>' if strict_min else '>='} {minimum:g} (got {v})")
    if maximum is not None and f > maximum:
        raise InvalidArguments(f"{what} must be <= {maximum:g} (got {v})")
    return f


def _money(v: Any) -> Optional[float]:
    if v is None or not _finite(v):
        return None
    f = float(v)
    return int(round(f)) if abs(f) > 1e5 else round(f, 2)


def _ratio(v: Any) -> Optional[float]:
    if v is None or not _finite(v):
        return None
    return round(float(v), 2)


def _pct1(v: Any) -> Optional[float]:
    """Round a value already expressed in percent to 1 decimal."""
    if v is None or not _finite(v):
        return None
    return round(float(v), 1)


def _r6(v: Any) -> Optional[float]:
    if v is None or not _finite(v):
        return None
    return float(f"{float(v):.6g}")


def _src(s: Any) -> str:
    return s.strip() if isinstance(s, str) and s.strip() else NO_SOURCE


def _label(v: Any) -> Any:
    return v


def _assumption(name: str, value: Any, source: Any = None, unit: Optional[str] = None) -> Dict[str, Any]:
    row = {"input": name, "value": value, "source": _src(source)}
    if unit:
        row["unit"] = unit
    return row


def _transparency(assumptions: List[Dict[str, Any]]) -> Dict[str, Any]:
    missing = [a["input"] for a in assumptions if a["source"] == NO_SOURCE]
    return {"n_inputs_from_user": len(assumptions), "inputs_without_source": missing}


def _pv(flows: Sequence[float], rate: float) -> float:
    return float(sum(v / (1.0 + rate) ** t for t, v in enumerate(flows)))


def _pad(a: List[float], n: int) -> List[float]:
    return list(a) + [0.0] * (n - len(a))


def _flow(spec: Any, what: str) -> Tuple[List[float], str]:
    """{"annual": a, "years": N, "start_year": s} → s zeros then N × a; {"by_year": [v0, v1, ...]} as given (year 0 = today)."""
    if not isinstance(spec, dict):
        raise InvalidArguments(f"{what} must be an object with `annual` + `years` or `by_year`")
    if spec.get("by_year") is not None:
        vals = spec["by_year"]
        if not isinstance(vals, list) or not vals or len(vals) > MAX_YEARS + 1:
            raise InvalidArguments(f"{what}.by_year must list 1 to {MAX_YEARS + 1} yearly values (year 0 first)")
        flows = [_num(v, f"{what}.by_year[{i}]", minimum=0) for i, v in enumerate(vals)]
    elif spec.get("annual") is not None:
        a = _num(spec["annual"], f"{what}.annual", minimum=0)
        years = spec.get("years")
        if not isinstance(years, int) or isinstance(years, bool) or years < 1 or years > MAX_YEARS:
            raise InvalidArguments(f"{what}.years must be an integer from 1 to {MAX_YEARS}")
        start = spec.get("start_year", 1)
        if not isinstance(start, int) or isinstance(start, bool) or start < 0 or start > MAX_YEARS:
            raise InvalidArguments(f"{what}.start_year must be an integer from 0 to {MAX_YEARS}")
        flows = [0.0] * start + [a] * years
    else:
        raise InvalidArguments(f"{what} needs either `annual` (with `years`) or `by_year`")
    return flows, _src(spec.get("source"))


def _npv_at(net: Sequence[float], rate: float) -> float:
    with np.errstate(all="ignore"):
        t = np.arange(len(net), dtype=float)
        disc = np.power(1.0 + rate, t)
        if not np.all(np.isfinite(disc)) or np.any(disc == 0):
            return float("nan")
        return float(np.sum(np.asarray(net, dtype=float) / disc))


def _irr(net: Sequence[float]) -> Tuple[Optional[float], Optional[str]]:
    """Bisection on NPV(rate) = 0 over [-0.99, 10]; None with a reason when the sign pattern gives no root."""
    arr = np.asarray(net, dtype=float)
    if not (np.any(arr < 0) and np.any(arr > 0)):
        return None, "IRR is undefined: the net flows do not change sign (no year in which costs exceed benefits, or the reverse)"
    hi, f_hi = 10.0, _npv_at(net, 10.0)
    lo = f_lo = None
    for cand in (-0.99, -0.95, -0.9, -0.75, -0.5, -0.25, 0.0):
        f = _npv_at(net, cand)
        if math.isfinite(f):
            lo, f_lo = cand, f
            break
    if lo is None or not math.isfinite(f_hi):
        return None, "IRR could not be bracketed numerically"
    if f_lo == 0:
        return lo, None
    if f_hi == 0:
        return hi, None
    if (f_lo > 0) == (f_hi > 0):
        return None, "IRR is undefined: NPV(rate) has the same sign at -99% and at 1,000%, so no root lies in that range"
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = _npv_at(net, mid)
        if fm == 0 or (hi - lo) < 1e-12:
            return mid, None
        if (fm > 0) == (f_lo > 0):
            lo, f_lo = mid, fm
        else:
            hi = mid
    note = None
    if int(np.sum(np.diff(np.sign(arr[arr != 0])) != 0)) > 1:
        note = "the net flows change sign more than once, so more than one IRR can exist; the one reported is the first found by bisection"
    return (lo + hi) / 2, note


def _signed_dots(title: str, labels: List[str], values: List[float], subtitle: str) -> str:
    """A dot per year with a bar from zero: reads as a signed bar chart (svg.bars cannot draw negative widths)."""
    lows = [min(0.0, v) for v in values]
    highs = [max(0.0, v) for v in values]
    return svg.dots(title, labels, values, lows, highs, subtitle=subtitle, zero_line=True)


def _plane(title: str, names: List[str], xs: List[float], ys: List[float], frontier: List[int], subtitle: str, xlabel: str, ylabel: str,
           w: int = 560, h: int = 380) -> str:
    """Cost-effectiveness plane: effect on x, cost on y, non-dominated options joined by the efficiency frontier, every point labelled."""
    left, right, top, bottom = 72, 24, 54, 48
    pw, ph = w - left - right, h - top - bottom
    xlo, xhi = 0.0, max(xs) * 1.08 or 1.0
    ylo, yhi = 0.0, max(ys) * 1.08 or 1.0
    xt, yt = svg._nice_ticks(xlo, xhi, 6), svg._nice_ticks(ylo, yhi, 5)
    xhi, yhi = max(xhi, xt[-1]), max(yhi, yt[-1])
    px = lambda x: left + (x - xlo) / (xhi - xlo) * pw
    py = lambda y: top + ph - (y - ylo) / (yhi - ylo) * ph
    body = []
    for t in yt:
        body.append(f'<line x1="{left}" y1="{py(t):.1f}" x2="{left + pw}" y2="{py(t):.1f}" stroke="{svg.GRID}"/>')
        body.append(f'<text x="{left - 8}" y="{py(t) + 4:.1f}" text-anchor="end" fill="{svg.MUTED}">{svg._fmt(t)}</text>')
    for t in xt:
        body.append(f'<text x="{px(t):.1f}" y="{top + ph + 16}" text-anchor="middle" fill="{svg.MUTED}">{svg._fmt(t)}</text>')
    body.append(f'<line x1="{left}" y1="{top + ph}" x2="{left + pw}" y2="{top + ph}" stroke="{svg.INK}" stroke-opacity="0.25"/>')
    if len(frontier) >= 2:
        pts = " ".join(f"{px(xs[i]):.1f},{py(ys[i]):.1f}" for i in frontier)
        body.append(f'<polyline points="{pts}" fill="none" stroke="{svg.DEEP}" stroke-width="1.6" stroke-dasharray="4 3"/>')
    fset = set(frontier)
    for i, nm in enumerate(names):
        col = svg.DEEP if i in fset else svg.GOLD
        body.append(f'<circle cx="{px(xs[i]):.1f}" cy="{py(ys[i]):.1f}" r="5" fill="{col}"><title>{svg._esc(nm)}: effect {svg._fmt(xs[i])}, cost {svg._fmt(ys[i])}</title></circle>')
        body.append(f'<text x="{px(xs[i]) + 8:.1f}" y="{py(ys[i]) - 6:.1f}" fill="{svg.INK}" font-size="11">{svg._esc(str(nm)[:22])}</text>')
    body.append(f'<text x="{left + pw / 2:.0f}" y="{h - 8}" text-anchor="middle" fill="{svg.MUTED}">{svg._esc(xlabel)}</text>')
    body.append(f'<text transform="translate(14 {top + ph / 2:.0f}) rotate(-90)" text-anchor="middle" fill="{svg.MUTED}">{svg._esc(ylabel)}</text>')
    return svg._frame(w, h, title, "".join(body), subtitle)


def _flow_schema(extra: Optional[Dict[str, Any]] = None, required: Optional[List[str]] = None) -> Dict[str, Any]:
    props = {"annual": {"type": ["number", "null"], "description": "the same amount every year"},
             "years": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_YEARS, "description": "number of years the annual amount runs"},
             "start_year": {"type": ["integer", "null"], "minimum": 0, "maximum": MAX_YEARS, "description": "first year of the annual amount (default 1; year 0 = today, undiscounted)"},
             "by_year": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 1, "maxItems": MAX_YEARS + 1, "description": "explicit yearly amounts, year 0 first"},
             "source": {"type": ["string", "null"], "description": "where the figures come from (echoed back)"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


# ---------- benefit_cost_analysis

@tool("benefit_cost_analysis",
      "Benefit-cost analysis (ex post or ex ante) from explicit yearly cost and benefit flows: present values at `discount_rate`, NPV, BCR, ROI, IRR (bisection), "
      "discounted payback year, break-even benefit scale, per-stream PV shares, a sensitivity table over discount rates × (cost overrun / benefit shortfall), "
      "a year-by-year calculation table (CSV) and a chart of discounted net flows. Year 0 = today (undiscounted); `annual` + `years` starts in year 1 unless `start_year` says otherwise. "
      "Every figure is an input: give its `source`.",
      {"type": "object", "properties": {
          "costs": _flow_schema(),
          "benefits": {"anyOf": [_flow_schema(), {"type": "null"}], "description": "total benefits; may be omitted when `benefit_streams` are given"},
          "benefit_streams": {"type": ["array", "null"], "minItems": 1, "maxItems": 20, "items": _flow_schema({"name": {"type": "string", "minLength": 1}}, ["name"]),
                              "description": "named benefit streams that sum to the benefits (each with its own source)"},
          "currency": {"type": "string", "minLength": 1, "maxLength": 12},
          "discount_rate": {"type": "number", "minimum": 0, "maximum": 1},
          "discount_rates": {"type": ["array", "null"], "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 1, "maxItems": 10, "description": "rates for the sensitivity table (default 3%, 5%, 8%, 10%)"},
          "cost_overrun_pct": {"type": "number", "minimum": 0, "maximum": 500, "description": "sensitivity: costs higher by this percent (default 20)"},
          "benefit_shortfall_pct": {"type": "number", "minimum": 0, "maximum": 100, "description": "sensitivity: benefits lower by this percent (default 20)"},
          "price_basis": {"type": ["string", "null"], "description": "e.g. '2025 constant prices'"}},
       "required": ["costs"], "additionalProperties": False})
def benefit_cost_analysis(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    currency = args.get("currency") or "USD"
    rate = _num(args.get("discount_rate"), "discount_rate", minimum=0, maximum=1, default=0.05)
    rates = sorted(set(float(r) for r in (args.get("discount_rates") or DEFAULT_RATES)))
    overrun = _num(args.get("cost_overrun_pct"), "cost_overrun_pct", minimum=0, default=20.0)
    shortfall = _num(args.get("benefit_shortfall_pct"), "benefit_shortfall_pct", minimum=0, maximum=100, default=20.0)
    notes: List[str] = []
    costs, cost_src = _flow(args["costs"], "costs")
    streams_in = args.get("benefit_streams") or []
    streams: List[Tuple[str, List[float], str]] = []
    for i, s in enumerate(streams_in):
        flows, src = _flow(s, f"benefit_streams[{i}]")
        streams.append((str(s.get("name") or f"stream {i + 1}"), flows, src))
    if len({n for n, _, _ in streams}) != len(streams):
        raise InvalidArguments("benefit stream names must be distinct")
    if args.get("benefits") is not None:
        benefits, ben_src = _flow(args["benefits"], "benefits")
    elif streams:
        n = max(len(f) for _, f, _ in streams)
        benefits = [float(sum(_pad(f, n)[t] for _, f, _ in streams)) for t in range(n)]
        ben_src = "sum of benefit_streams"
    else:
        raise InvalidArguments("give `benefits` or `benefit_streams`")
    n = max(len(costs), len(benefits), *(len(f) for _, f, _ in streams)) if streams else max(len(costs), len(benefits))
    costs, benefits = _pad(costs, n), _pad(benefits, n)
    if streams:
        total = [sum(_pad(f, n)[t] for _, f, _ in streams) for t in range(n)]
        gap = max(abs(a - b) for a, b in zip(total, benefits))
        if gap > 1e-6 * max(1.0, sum(benefits)):
            raise InvalidArguments(f"benefit_streams sum to {sum(total):,.2f} over the horizon but `benefits` total {sum(benefits):,.2f}; they must agree")
    if sum(costs) <= 0:
        raise InvalidArguments("costs are zero in every year; a benefit-cost analysis needs a cost")
    ctx.progress(20, "discounting")
    disc = [(1.0 + rate) ** t for t in range(n)]
    pv_c = [c / d for c, d in zip(costs, disc)]
    pv_b = [b / d for b, d in zip(benefits, disc)]
    pvc, pvb = float(sum(pv_c)), float(sum(pv_b))
    npv = pvb - pvc
    bcr = pvb / pvc if pvc > 0 else None
    roi = (pvb - pvc) / pvc if pvc > 0 else None
    net = [b - c for b, c in zip(benefits, costs)]
    irr, irr_note = _irr(net)
    if irr_note:
        notes.append(irr_note)
    cum = np.cumsum([b - c for b, c in zip(pv_b, pv_c)])
    payback = next((t for t, v in enumerate(cum) if v >= -1e-9), None)
    if payback is None:
        notes.append(f"discounted payback: never within the {n - 1}-year horizon (cumulative discounted net flow ends at {npv:,.2f})")
    break_even_scale = pvc / pvb if pvb > 0 else None
    if break_even_scale is None:
        notes.append("break-even benefit scale is undefined because the present value of benefits is zero")
    table = []
    for t in range(n):
        table.append({"year": t, "cost": costs[t], "benefit": benefits[t], "net": net[t], "discount_factor": 1.0 / disc[t],
                      "pv_cost": pv_c[t], "pv_benefit": pv_b[t], "pv_net": pv_b[t] - pv_c[t], "cumulative_pv_net": float(cum[t])})
    stream_rows = []
    for name, f, src in streams:
        pv = _pv(_pad(f, n), rate)
        stream_rows.append({"name": name, "pv": _money(pv), "share_pct": _pct1(pv / pvb * 100) if pvb > 0 else None, "source": src})
    ctx.progress(50, "sensitivity")
    sens = []
    scenarios = [("central", 1.0, 1.0), (f"costs +{overrun:g}%", 1 + overrun / 100, 1.0), (f"benefits −{shortfall:g}%", 1.0, 1 - shortfall / 100),
                 (f"costs +{overrun:g}% and benefits −{shortfall:g}%", 1 + overrun / 100, 1 - shortfall / 100)]
    for r in rates:
        for label, cf, bf in scenarios:
            c_, b_ = _pv(costs, r) * cf, _pv(benefits, r) * bf
            sens.append({"discount_rate_pct": _pct1(r * 100), "scenario": label, "pv_costs": _money(c_), "pv_benefits": _money(b_), "npv": _money(b_ - c_),
                         "bcr": _ratio(b_ / c_) if c_ > 0 else None})
    worst = min((s for s in sens if s["bcr"] is not None), key=lambda s: s["bcr"], default=None)
    best = max((s for s in sens if s["bcr"] is not None), key=lambda s: s["bcr"], default=None)
    ctx.progress(75, "writing")
    out = ctx.project.new_artifact_dir("bca", f"{currency}-{n - 1}y")
    csv_path = _save_table(ctx, out, "calculation", pd.DataFrame(table))
    if sens:
        _save_table(ctx, out, "sensitivity", pd.DataFrame(sens))
    shown = table[:MAX_CHART_YEARS]
    if len(table) > MAX_CHART_YEARS:
        notes.append(f"the chart shows the first {MAX_CHART_YEARS} of {n} years; the CSV has all of them")
    labs = [f"year {r['year']}" for r in shown]
    vals = [r["pv_net"] for r in shown]
    chart = _signed_dots(f"Discounted net flow by year ({currency})", labs, vals, subtitle=f"benefits − costs at {rate * 100:g}% · NPV {svg._fmt(npv)} · BCR {_ratio(bcr)}")
    chart_path = str(ctx.project.write_text(out / "net_flows.svg", chart))
    _office_png(chart_path, "dots", f"Discounted net flow by year ({currency})", labs, vals, [min(0.0, v) for v in vals], [max(0.0, v) for v in vals])
    assumptions = [_assumption("costs", {"by_year": costs}, cost_src, currency), _assumption("benefits", {"by_year": benefits}, ben_src, currency),
                   _assumption("discount_rate", rate, "caller"), _assumption("cost_overrun_pct", overrun, "caller (sensitivity)"),
                   _assumption("benefit_shortfall_pct", shortfall, "caller (sensitivity)")]
    assumptions += [_assumption(f"benefit stream '{name}'", {"by_year": _pad(f, n)}, src, currency) for name, f, src in streams]
    if args.get("price_basis"):
        assumptions.append(_assumption("price_basis", args["price_basis"], "caller"))
    verdict = ("benefits exceed costs" if npv > 0 else "costs exceed benefits" if npv < 0 else "benefits equal costs")
    interp = [f"Over {n - 1} years at a {rate * 100:g}% discount rate, the present value of costs is {currency} {_money(pvc):,} and of benefits {currency} {_money(pvb):,}: "
              f"NPV {currency} {_money(npv):,}, BCR {_ratio(bcr)}, ROI {_pct1(roi * 100) if roi is not None else None}%; {verdict}. "
              + (f"IRR {_pct1(irr * 100)}%. " if irr is not None else "IRR undefined. ")
              + (f"Discounted payback in year {payback}. " if payback is not None else "No discounted payback within the horizon. ")
              + (f"Benefits could fall to {_pct1(break_even_scale * 100)}% of the stated level before BCR drops below 1." if break_even_scale is not None and break_even_scale < 1
                 else f"Benefits would have to rise to {_pct1(break_even_scale * 100)}% of the stated level for BCR to reach 1." if break_even_scale is not None else "")]
    if worst and best:
        interp.append(f"Sensitivity: BCR ranges from {worst['bcr']} ({worst['scenario']}, {worst['discount_rate_pct']}%) to {best['bcr']} ({best['scenario']}, {best['discount_rate_pct']}%)"
                      + ("; it stays above 1 in every scenario." if worst["bcr"] > 1 else "; it falls below 1 in at least one scenario." if best["bcr"] >= 1 else "; it is below 1 in every scenario."))
    if stream_rows:
        top = max(stream_rows, key=lambda s: s["pv"] or 0)
        interp.append(f"The largest benefit stream is '{top['name']}' ({top['share_pct']}% of PV benefits; source: {top['source']}).")
    interp.append(NOT_EVIDENCE)
    ctx.project.audit({"tool": "benefit_cost_analysis", "years": n - 1, "npv": npv, "bcr": bcr, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"currency": currency, "price_basis": args.get("price_basis"), "discount_rate": rate, "horizon_years": n - 1,
            "pv_costs": _money(pvc), "pv_benefits": _money(pvb), "npv": _money(npv), "bcr": _ratio(bcr), "roi": _ratio(roi), "roi_pct": _pct1(roi * 100) if roi is not None else None,
            "irr_pct": _pct1(irr * 100) if irr is not None else None, "payback_year": payback, "break_even_benefit_scale": _ratio(break_even_scale),
            "unrounded": {"pv_costs": pvc, "pv_benefits": pvb, "npv": npv, "bcr": bcr, "roi": roi, "irr": irr, "break_even_benefit_scale": break_even_scale},
            "benefit_streams": stream_rows, "sensitivity": sens, "calculation_table": table, "assumptions": assumptions, **_transparency(assumptions),
            "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Discounted net flow (benefits − costs) per year at {rate * 100:g}%; NPV {currency} {_money(npv):,}, BCR {_ratio(bcr)}.",
            "chart_path": chart_path, "csv_path": csv_path, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- cost_effectiveness_analysis

@tool("cost_effectiveness_analysis",
      "Compare 2–20 options by cost per outcome unit: cost per unit and per beneficiary, a league table sorted by effect with incremental cost-effectiveness ratios "
      "(ICER vs the next less effective non-dominated option), strict and extended dominance flags, ICER vs a comparator (default: the cheapest option), net monetary "
      "benefit when `willingness_to_pay` per unit is given, low/high cost per unit when effect ranges are given, and a cost-effectiveness plane chart. "
      "Costs and effects are inputs: give each option's `source`.",
      {"type": "object", "properties": {
          "options": {"type": "array", "minItems": 2, "maxItems": 20, "items": {"type": "object", "properties": {
              "name": {"type": "string", "minLength": 1, "maxLength": 80},
              "total_cost": {"type": "number"}, "effect": {"type": "number", "description": "total outcome units achieved (e.g. school years, cases averted, DALYs averted)"},
              "n_beneficiaries": {"type": ["number", "null"]}, "effect_low": {"type": ["number", "null"]}, "effect_high": {"type": ["number", "null"]},
              "source": {"type": ["string", "null"]}}, "required": ["name", "total_cost", "effect"], "additionalProperties": False}},
          "outcome_unit": {"type": "string", "minLength": 1, "maxLength": 60},
          "currency": {"type": "string", "minLength": 1, "maxLength": 12},
          "willingness_to_pay": {"type": ["number", "null"], "description": "value of one outcome unit, for net monetary benefit"},
          "willingness_to_pay_source": {"type": ["string", "null"]},
          "comparator": {"type": ["string", "null"], "description": "option name to compute ICERs against (default: the cheapest option)"}},
       "required": ["options", "outcome_unit"], "additionalProperties": False})
def cost_effectiveness_analysis(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    currency = args.get("currency") or "USD"
    unit = args["outcome_unit"]
    opts = args["options"]
    if len(opts) < 2:
        raise InvalidArguments("at least 2 options are needed")
    names = [str(o["name"]).strip() for o in opts]
    if len(set(names)) != len(names):
        raise InvalidArguments("option names must be distinct")
    cost: List[float] = []; eff: List[float] = []; nben: List[Optional[float]] = []; lo: List[Optional[float]] = []; hi: List[Optional[float]] = []; srcs: List[str] = []
    for o, nm in zip(opts, names):
        c = _num(o["total_cost"], f"total_cost of option '{nm}'", minimum=0, strict_min=True)
        e = _num(o["effect"], f"effect of option '{nm}'", minimum=0, strict_min=True)
        nb = _num(o.get("n_beneficiaries"), f"n_beneficiaries of option '{nm}'", minimum=0, strict_min=True) if o.get("n_beneficiaries") is not None else None
        el = _num(o.get("effect_low"), f"effect_low of option '{nm}'", minimum=0, strict_min=True) if o.get("effect_low") is not None else None
        eh = _num(o.get("effect_high"), f"effect_high of option '{nm}'", minimum=0, strict_min=True) if o.get("effect_high") is not None else None
        if (el is None) != (eh is None):
            raise InvalidArguments(f"option '{nm}': give both effect_low and effect_high or neither")
        if el is not None and not (el <= e <= eh):
            raise InvalidArguments(f"option '{nm}': effect_low ≤ effect ≤ effect_high must hold ({el}, {e}, {eh})")
        cost.append(c); eff.append(e); nben.append(nb); lo.append(el); hi.append(eh); srcs.append(_src(o.get("source")))
    wtp = _num(args.get("willingness_to_pay"), "willingness_to_pay", minimum=0, strict_min=True) if args.get("willingness_to_pay") is not None else None
    comp_name = args.get("comparator")
    if comp_name is not None:
        if comp_name not in names:
            raise InvalidArguments(f"comparator '{comp_name}' is not one of the options: {', '.join(names)}")
        comp = names.index(comp_name)
    else:
        comp = min(range(len(opts)), key=lambda i: (cost[i], -eff[i]))
        comp_name = names[comp]
    ctx.progress(25, "dominance")
    n = len(opts)
    order = sorted(range(n), key=lambda i: (eff[i], cost[i]))
    dominance: Dict[int, str] = {}
    for i in range(n):
        for j in range(n):
            if j == i:
                continue
            if cost[j] <= cost[i] and eff[j] >= eff[i] and (cost[j] < cost[i] or eff[j] > eff[i]):
                dominance[i] = f"strictly dominated by {names[j]}"
                break
            if cost[j] == cost[i] and eff[j] == eff[i] and order.index(j) < order.index(i):
                dominance[i] = f"equivalent to {names[j]} (same cost and effect)"
                break
    frontier = [i for i in order if i not in dominance]
    changed = True
    while changed and len(frontier) >= 3:
        changed = False
        icers = [None] + [(cost[b] - cost[a]) / (eff[b] - eff[a]) for a, b in zip(frontier, frontier[1:])]
        for k in range(1, len(frontier) - 1):
            if icers[k] is not None and icers[k + 1] is not None and icers[k] > icers[k + 1]:
                dominance[frontier[k]] = f"extended dominance (ICER {_money(icers[k])} exceeds {_money(icers[k + 1])} of the more effective {names[frontier[k + 1]]})"
                frontier.pop(k)
                changed = True
                break
    league_icer: Dict[int, Optional[float]] = {frontier[0]: None} if frontier else {}
    league_vs: Dict[int, Optional[str]] = {frontier[0]: None} if frontier else {}
    for a, b in zip(frontier, frontier[1:]):
        league_icer[b] = (cost[b] - cost[a]) / (eff[b] - eff[a])
        league_vs[b] = names[a]
    ctx.progress(55, "ratios")
    rows = []
    for i in range(n):
        cpu = cost[i] / eff[i]
        vs_comp = None
        if i != comp and eff[i] != eff[comp]:
            vs_comp = (cost[i] - cost[comp]) / (eff[i] - eff[comp])
        rows.append({"option": names[i], "total_cost": _money(cost[i]), "effect": _r6(eff[i]), "cost_per_unit": _money(cpu),
                     "n_beneficiaries": nben[i], "cost_per_beneficiary": _money(cost[i] / nben[i]) if nben[i] else None,
                     "effect_per_beneficiary": _r6(eff[i] / nben[i]) if nben[i] else None,
                     "cost_per_unit_low": _money(cost[i] / hi[i]) if hi[i] else None, "cost_per_unit_high": _money(cost[i] / lo[i]) if lo[i] else None,
                     "net_monetary_benefit": _money(wtp * eff[i] - cost[i]) if wtp is not None else None,
                     "icer_vs_comparator": (_money(vs_comp) if vs_comp is not None else None) if i != comp else "comparator",
                     "dominance": dominance.get(i, "non-dominated"), "source": srcs[i]})
    by_name = {r["option"]: r for r in rows}
    league = []
    for i in order:
        prev = league_vs.get(i)
        league.append({"rank_by_effect": len(league) + 1, "option": names[i], "effect": _r6(eff[i]), "total_cost": _money(cost[i]), "cost_per_unit": _money(cost[i] / eff[i]),
                       "incremental_cost": _money(cost[i] - cost[names.index(prev)]) if prev else None,
                       "incremental_effect": _r6(eff[i] - eff[names.index(prev)]) if prev else None,
                       "icer": _money(league_icer[i]) if i in league_icer and league_icer[i] is not None else None,
                       "icer_vs": prev if prev else ("reference (least effective non-dominated)" if i in league_icer else None),
                       "dominance": dominance.get(i, "non-dominated")})
    ctx.progress(80, "writing")
    out = ctx.project.new_artifact_dir("cea", unit)
    csv_path = _save_table(ctx, out, "calculation", pd.DataFrame(rows))
    _save_table(ctx, out, "league_table", pd.DataFrame(league))
    chart = _plane(f"Cost-effectiveness plane ({currency} vs {unit})", names, eff, cost, frontier,
                   subtitle=f"dashed line = efficiency frontier (non-dominated options); gold = dominated · comparator: {comp_name}", xlabel=unit, ylabel=f"total cost ({currency})")
    chart_path = str(ctx.project.write_text(out / "ce_plane.svg", chart))
    _office_png(chart_path, "bars", f"Cost per {unit} ({currency})", names, [cost[i] / eff[i] for i in range(n)])
    cheapest_unit = min(range(n), key=lambda i: cost[i] / eff[i])
    most_eff = frontier[-1] if frontier else order[-1]
    assumptions = [_assumption(f"option '{names[i]}'", {"total_cost": cost[i], "effect": eff[i], "n_beneficiaries": nben[i], "effect_low": lo[i], "effect_high": hi[i]}, srcs[i], f"{currency}; {unit}") for i in range(n)]
    if wtp is not None:
        assumptions.append(_assumption("willingness_to_pay", wtp, args.get("willingness_to_pay_source"), f"{currency} per {unit}"))
    dom_strict = [names[i] for i in range(n) if dominance.get(i, "").startswith("strictly")]
    dom_ext = [names[i] for i in range(n) if dominance.get(i, "").startswith("extended")]
    interp = [f"The lowest cost per {unit} is {names[cheapest_unit]} at {currency} {by_name[names[cheapest_unit]]['cost_per_unit']:,} per {unit}; the most effective non-dominated option is "
              f"{names[most_eff]} ({_r6(eff[most_eff])} {unit} for {currency} {_money(cost[most_eff]):,})"
              + (f", with an ICER of {currency} {_money(league_icer[most_eff]):,} per additional {unit} versus {league_vs[most_eff]}." if league_icer.get(most_eff) is not None else ".")]
    if dom_strict or dom_ext:
        interp.append(("Strictly dominated (more cost, less effect than another option): " + ", ".join(dom_strict) + ". " if dom_strict else "")
                      + ("Ruled out by extended dominance (a more effective option buys each unit more cheaply): " + ", ".join(dom_ext) + "." if dom_ext else ""))
    else:
        interp.append("No option is dominated: each step up in effect costs more per additional unit than the last.")
    if wtp is not None:
        best = max(rows, key=lambda r: r["net_monetary_benefit"])
        interp.append(f"At a willingness to pay of {currency} {_money(wtp):,} per {unit}, the highest net monetary benefit is {best['option']} ({currency} {best['net_monetary_benefit']:,}); "
                      + ("options with an ICER below the threshold are worth their extra cost." if any(r['icer'] is not None and r['icer'] <= wtp for r in league) else "no step up the league table has an ICER below the threshold."))
    interp.append(NOT_EVIDENCE)
    ctx.project.audit({"tool": "cost_effectiveness_analysis", "options": n, "unit": unit, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"currency": currency, "outcome_unit": unit, "comparator": comp_name, "willingness_to_pay": wtp, "options": rows, "league_table": league,
            "frontier": [names[i] for i in frontier], "strictly_dominated": dom_strict, "extended_dominated": dom_ext,
            "unrounded": {"icer": {names[i]: league_icer[i] for i in league_icer}, "cost_per_unit": {names[i]: cost[i] / eff[i] for i in range(n)}},
            "calculation_table": rows, "assumptions": assumptions, **_transparency(assumptions), "notes": [], "interpretation": interp, "summary_text": interp[0],
            "caption": f"Cost-effectiveness plane: {unit} (x) against total cost in {currency} (y); the dashed frontier joins non-dominated options.",
            "chart_path": chart_path, "csv_path": csv_path, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- sroi_analysis

def _pct_in(v: Any, what: str, scale: Optional[str], notes: List[str], default: float = 0.0) -> float:
    """Return a share in [0, 1]. Values are percent (0–100) unless `scale` is 'fraction'; without `scale`, a value strictly between 0 and 1 is read as a fraction and noted."""
    f = _num(v, what, default=default)
    if f < 0:
        raise InvalidArguments(f"{what} must be between 0 and 100 percent (got {v})")
    if scale == "fraction":
        if f > 1:
            raise InvalidArguments(f"{what} must be between 0 and 1 when pct_scale is 'fraction' (got {v})")
        return f
    if scale is None and 0 < f < 1:
        notes.append(f"{what} = {v} was read as a fraction ({f * 100:g}%); pass pct_scale to make this explicit")
        return f
    if f > 100:
        raise InvalidArguments(f"{what} must be between 0 and 100 percent (got {v})")
    return f / 100.0


def _sroi_pv(quantity: float, proxy: float, dw: float, disp: float, attr: float, dropoff: float, duration: int, rate: float) -> Tuple[float, float, List[float]]:
    """(gross value, impact in year 1, discounted impact per year 1..duration)."""
    gross = quantity * proxy
    impact1 = gross * (1 - dw) * (1 - disp) * (1 - attr)
    years = [impact1 * (1 - dropoff) ** (k - 1) / (1 + rate) ** k for k in range(1, duration + 1)]
    return gross, impact1, years


@tool("sroi_analysis",
      "Social Return on Investment following the SROI Network guide: for each stakeholder outcome, quantity × financial proxy, reduced for deadweight, displacement and "
      "attribution, declining by drop-off after year 1 over `duration_years`, discounted; present value of impact over present value of investment as 'X : 1'. Also a "
      "conservative ratio (every adjustment worsened by 10 points), a sensitivity table (each proxy ±25%, deadweight ±10 points), value shares by stakeholder, a chart "
      "of PV by outcome and a calculation table (CSV). Proxies are inputs: give `proxy_source` for each; missing sources are flagged in `notes`.",
      {"type": "object", "properties": {
          "investment": {"anyOf": [{"type": "number"}, {"type": "object", "properties": {
              "total": {"type": ["number", "null"]}, "by_year": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 1, "maxItems": MAX_YEARS + 1},
              "source": {"type": ["string", "null"]}}, "additionalProperties": False}], "description": "total input value today, or {by_year: [...]} with year 0 undiscounted"},
          "outcomes": {"type": "array", "minItems": 1, "maxItems": 40, "items": {"type": "object", "properties": {
              "stakeholder": {"type": "string", "minLength": 1, "maxLength": 80}, "outcome": {"type": "string", "minLength": 1, "maxLength": 120},
              "quantity": {"type": "number"}, "financial_proxy": {"type": "number", "description": "value of one unit of the outcome"},
              "proxy_source": {"type": ["string", "null"]}, "duration_years": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_YEARS},
              "deadweight_pct": {"type": ["number", "null"]}, "attribution_pct": {"type": ["number", "null"]}, "displacement_pct": {"type": ["number", "null"]},
              "drop_off_pct": {"type": ["number", "null"], "description": "decline per year after the first"}},
              "required": ["stakeholder", "outcome", "quantity", "financial_proxy"], "additionalProperties": False}},
          "currency": {"type": "string", "minLength": 1, "maxLength": 12},
          "discount_rate": {"type": "number", "minimum": 0, "maximum": 1},
          "years": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_YEARS, "description": "horizon: outcome durations beyond it are truncated"},
          "pct_scale": {"type": ["string", "null"], "enum": ["percent", "fraction", None], "description": "how the *_pct fields are given (default percent; values strictly between 0 and 1 are read as fractions and noted)"}},
       "required": ["investment", "outcomes"], "additionalProperties": False})
def sroi_analysis(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    currency = args.get("currency") or "USD"
    rate = _num(args.get("discount_rate"), "discount_rate", minimum=0, maximum=1, default=0.05)
    horizon = args.get("years")
    scale = args.get("pct_scale")
    notes: List[str] = []
    inv = args["investment"]
    if isinstance(inv, dict):
        inv_src = _src(inv.get("source"))
        if inv.get("by_year") is not None:
            inv_flows = [_num(v, f"investment.by_year[{i}]", minimum=0) for i, v in enumerate(inv["by_year"])]
        else:
            inv_flows = [_num(inv.get("total"), "investment.total", minimum=0, strict_min=True)]
    else:
        inv_flows = [_num(inv, "investment", minimum=0, strict_min=True)]
        inv_src = NO_SOURCE
    pv_inv = _pv(inv_flows, rate)
    if pv_inv <= 0:
        raise InvalidArguments("investment must be positive")
    ctx.progress(20, "valuing outcomes")
    parsed = []
    for i, o in enumerate(args["outcomes"]):
        lab = f"outcome {i + 1} ('{o['outcome']}')"
        q = _num(o["quantity"], f"quantity of {lab}", minimum=0, strict_min=True)
        proxy = _num(o["financial_proxy"], f"financial_proxy of {lab}", minimum=0, strict_min=True)
        dur = o.get("duration_years") or 1
        if horizon and dur > horizon:
            notes.append(f"{lab}: duration {dur} years truncated to the {horizon}-year horizon")
            dur = horizon
        dw = _pct_in(o.get("deadweight_pct"), f"deadweight_pct of {lab}", scale, notes)
        attr = _pct_in(o.get("attribution_pct"), f"attribution_pct of {lab}", scale, notes)
        disp = _pct_in(o.get("displacement_pct"), f"displacement_pct of {lab}", scale, notes)
        drop = _pct_in(o.get("drop_off_pct"), f"drop_off_pct of {lab}", scale, notes)
        src = _src(o.get("proxy_source"))
        if src == NO_SOURCE:
            notes.append(f"proxy for {o['outcome']} ({o['stakeholder']}) has no stated source")
        parsed.append({"stakeholder": str(o["stakeholder"]), "outcome": str(o["outcome"]), "quantity": q, "proxy": proxy, "proxy_source": src, "duration": int(dur),
                       "dw": dw, "attr": attr, "disp": disp, "drop": drop})

    def total_pv(rows, *, proxy_mult: Optional[Dict[int, float]] = None, dw_shift: Optional[Dict[int, float]] = None, worsen: float = 0.0) -> float:
        s = 0.0
        for k, p in enumerate(rows):
            pm = (proxy_mult or {}).get(k, 1.0)
            dws = (dw_shift or {}).get(k, 0.0)
            dw = min(1.0, max(0.0, p["dw"] + dws + worsen)); disp = min(1.0, p["disp"] + worsen); attr = min(1.0, p["attr"] + worsen); drop = min(1.0, p["drop"] + worsen)
            s += sum(_sroi_pv(p["quantity"], p["proxy"] * pm, dw, disp, attr, drop, p["duration"], rate)[2])
        return s

    table = []
    pv_rows = []
    for k, p in enumerate(parsed):
        gross, impact1, years = _sroi_pv(p["quantity"], p["proxy"], p["dw"], p["disp"], p["attr"], p["drop"], p["duration"], rate)
        pv = float(sum(years))
        pv_rows.append(pv)
        table.append({"stakeholder": p["stakeholder"], "outcome": p["outcome"], "quantity": p["quantity"], "financial_proxy": p["proxy"], "proxy_source": p["proxy_source"],
                      "gross_value": gross, "deadweight_pct": p["dw"] * 100, "displacement_pct": p["disp"] * 100, "attribution_pct": p["attr"] * 100,
                      "impact_year_1": impact1, "drop_off_pct": p["drop"] * 100, "duration_years": p["duration"], "pv_impact": pv,
                      "pv_by_year": [_r6(v) for v in years]})
    total = float(sum(pv_rows))
    ratio = total / pv_inv
    conservative = total_pv(parsed, worsen=0.10) / pv_inv
    ctx.progress(55, "sensitivity")
    sens = []
    for k, p in enumerate(parsed):
        for label, kw in ((f"proxy −25%", {"proxy_mult": {k: 0.75}}), (f"proxy +25%", {"proxy_mult": {k: 1.25}}),
                          (f"deadweight +10 points", {"dw_shift": {k: 0.10}}), (f"deadweight −10 points", {"dw_shift": {k: -0.10}})):
            r = total_pv(parsed, **kw) / pv_inv
            sens.append({"outcome": p["outcome"], "stakeholder": p["stakeholder"], "change": label, "sroi_ratio": _ratio(r), "ratio_text": f"{_ratio(r)} : 1"})
    for label, kw in (("all deadweight +10 points", {"dw_shift": {k: 0.10 for k in range(len(parsed))}}), ("all deadweight −10 points", {"dw_shift": {k: -0.10 for k in range(len(parsed))}})):
        r = total_pv(parsed, **kw) / pv_inv
        sens.append({"outcome": "(all)", "stakeholder": "(all)", "change": label, "sroi_ratio": _ratio(r), "ratio_text": f"{_ratio(r)} : 1"})
    shares: Dict[str, float] = {}
    for p, pv in zip(parsed, pv_rows):
        shares[p["stakeholder"]] = shares.get(p["stakeholder"], 0.0) + pv
    by_stakeholder = [{"stakeholder": s, "pv_impact": _money(v), "share_pct": _pct1(v / total * 100) if total > 0 else None} for s, v in sorted(shares.items(), key=lambda kv: -kv[1])]
    ctx.progress(80, "writing")
    out = ctx.project.new_artifact_dir("sroi", f"{len(parsed)}-outcomes")
    flat = [{k: (v if k != "pv_by_year" else "; ".join(str(x) for x in v)) for k, v in row.items()} for row in table]
    csv_path = _save_table(ctx, out, "calculation", pd.DataFrame(flat))
    _save_table(ctx, out, "sensitivity", pd.DataFrame(sens))
    labs = [f"{p['outcome'][:26]} ({p['stakeholder'][:14]})" for p in parsed]
    chart = svg.bars(f"Present value of impact by outcome ({currency})", labs, pv_rows, subtitle=f"SROI {_ratio(ratio)} : 1 · investment PV {svg._fmt(pv_inv)} · {rate * 100:g}% discount rate")
    chart_path = str(ctx.project.write_text(out / "pv_by_outcome.svg", chart))
    _office_png(chart_path, "bars", f"Present value of impact by outcome ({currency})", labs, pv_rows)
    assumptions = [_assumption("investment", {"by_year": inv_flows}, inv_src, currency), _assumption("discount_rate", rate, "caller")]
    for p in parsed:
        assumptions.append(_assumption(f"proxy for '{p['outcome']}' ({p['stakeholder']})", p["proxy"], p["proxy_source"], f"{currency} per unit"))
        assumptions.append(_assumption(f"adjustments for '{p['outcome']}'", {"quantity": p["quantity"], "deadweight_pct": p["dw"] * 100, "displacement_pct": p["disp"] * 100,
                                                                              "attribution_pct": p["attr"] * 100, "drop_off_pct": p["drop"] * 100, "duration_years": p["duration"]}, "caller"))
    lo_s = min(sens, key=lambda s: s["sroi_ratio"]); hi_s = max(sens, key=lambda s: s["sroi_ratio"])
    top = by_stakeholder[0] if by_stakeholder else None
    interp = [f"For every {currency} 1 invested (PV {currency} {_money(pv_inv):,}), the present value of social impact is {currency} {_ratio(ratio)}: SROI {_ratio(ratio)} : 1 "
              f"(PV of impact {currency} {_money(total):,} across {len(parsed)} outcome{'s' if len(parsed) != 1 else ''} at a {rate * 100:g}% discount rate). "
              f"Worsening every adjustment by 10 points gives a conservative ratio of {_ratio(conservative)} : 1.",
              f"Sensitivity: the ratio ranges from {lo_s['sroi_ratio']} : 1 ({lo_s['change']}, {lo_s['outcome']}) to {hi_s['sroi_ratio']} : 1 ({hi_s['change']}, {hi_s['outcome']})."
              + (f" {top['stakeholder']} holds {top['share_pct']}% of the value." if top else ""),
              SROI_TEXT]
    if any(n.startswith("proxy for") for n in notes):
        interp.append("Caution: " + "; ".join(n for n in notes if n.startswith("proxy for")) + ". Unsourced proxies make the ratio unverifiable.")
    ctx.project.audit({"tool": "sroi_analysis", "outcomes": len(parsed), "ratio": ratio, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"currency": currency, "discount_rate": rate, "pv_investment": _money(pv_inv), "pv_impact": _money(total), "sroi_ratio": _ratio(ratio), "ratio_text": f"{_ratio(ratio)} : 1",
            "conservative_ratio": _ratio(conservative), "conservative_ratio_text": f"{_ratio(conservative)} : 1",
            "unrounded": {"pv_investment": pv_inv, "pv_impact": total, "sroi_ratio": ratio, "conservative_ratio": conservative, "pv_by_outcome": pv_rows},
            "outcomes": [{**{k: v for k, v in row.items() if k != "pv_by_year"}, "gross_value": _money(row["gross_value"]), "impact_year_1": _money(row["impact_year_1"]),
                          "pv_impact": _money(row["pv_impact"]), "share_pct": _pct1(row["pv_impact"] / total * 100) if total > 0 else None} for row in table],
            "by_stakeholder": by_stakeholder, "sensitivity": sens, "calculation_table": table, "assumptions": assumptions, **_transparency(assumptions),
            "notes": notes, "interpretation": interp, "summary_text": interp[0],
            "caption": f"Present value of impact per outcome in {currency}; SROI {_ratio(ratio)} : 1 (conservative {_ratio(conservative)} : 1).",
            "chart_path": chart_path, "csv_path": csv_path, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}


# ---------- ex_ante_impact_projection

def _range(args: Dict[str, Any], key: str, central: float, what: str, lo_min: Optional[float] = None, hi_max: Optional[float] = None) -> Optional[Tuple[float, float]]:
    lo, hi = args.get(f"{key}_low"), args.get(f"{key}_high")
    if lo is None and hi is None:
        return None
    if lo is None or hi is None:
        raise InvalidArguments(f"give both {key}_low and {key}_high or neither")
    lo = _num(lo, f"{key}_low", minimum=lo_min); hi = _num(hi, f"{key}_high", maximum=hi_max)
    if not (lo <= central <= hi):
        raise InvalidArguments(f"{key}_low ≤ {what} ≤ {key}_high must hold ({lo}, {central}, {hi})")
    if lo == hi:
        return None
    return lo, hi


def _tri(rng: np.random.Generator, lo: float, mode: float, hi: float, size: int) -> np.ndarray:
    if hi <= lo:
        return np.full(size, mode)
    return rng.triangular(lo, mode, hi, size)


@tool("ex_ante_impact_projection",
      "Project impact and economic return before implementation from stated assumptions: expected beneficiaries (target population × take-up), total outcome units "
      "(effect per beneficiary, decaying by `effect_decay_pct` a year over `years`), total costs (fixed + per beneficiary, year 0), cost per beneficiary and per unit; "
      "with `value_per_unit`: PV benefits, NPV, BCR, ROI, break-even take-up and break-even effect. Low/high (or `effect_se`) inputs drive a seeded Monte Carlo "
      "(triangular / normal draws) that gives 90% intervals, P(BCR > 1), a tornado table and a chart. A projection, not an evaluation result.",
      {"type": "object", "properties": {
          "target_population": {"type": "number", "description": "people (or units) the intervention is offered to"},
          "take_up_rate": {"type": "number", "minimum": 0, "maximum": 1}, "take_up_low": {"type": ["number", "null"], "minimum": 0, "maximum": 1}, "take_up_high": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
          "take_up_source": {"type": ["string", "null"]},
          "effect_per_beneficiary": {"type": "number", "description": "outcome units gained per beneficiary per year"}, "effect_low": {"type": ["number", "null"]}, "effect_high": {"type": ["number", "null"]},
          "effect_se": {"type": ["number", "null"], "minimum": 0, "description": "standard error of the effect (normal draws); alternative to effect_low/high"},
          "effect_source": {"type": ["string", "null"]},
          "outcome_unit": {"type": "string", "minLength": 1, "maxLength": 60},
          "value_per_unit": {"type": ["number", "null"], "minimum": 0, "description": "monetary value of one outcome unit"}, "value_low": {"type": ["number", "null"], "minimum": 0}, "value_high": {"type": ["number", "null"], "minimum": 0},
          "value_source": {"type": ["string", "null"]},
          "cost_per_beneficiary": {"type": "number", "minimum": 0}, "fixed_costs": {"type": ["number", "null"], "minimum": 0}, "cost_source": {"type": ["string", "null"]},
          "years": {"type": ["integer", "null"], "minimum": 1, "maximum": MAX_YEARS}, "effect_decay_pct": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
          "currency": {"type": "string", "minLength": 1, "maxLength": 12}, "discount_rate": {"type": "number", "minimum": 0, "maximum": 1},
          "simulations": {"type": ["integer", "null"], "minimum": 100, "maximum": 20000}},
       "required": ["target_population", "take_up_rate", "effect_per_beneficiary", "outcome_unit", "cost_per_beneficiary"], "additionalProperties": False})
def ex_ante_impact_projection(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.time()
    currency = args.get("currency") or "USD"
    unit = args["outcome_unit"]
    rate = _num(args.get("discount_rate"), "discount_rate", minimum=0, maximum=1, default=0.05)
    pop = _num(args["target_population"], "target_population", minimum=0, strict_min=True)
    tu = _num(args["take_up_rate"], "take_up_rate", minimum=0, maximum=1)
    eff = _num(args["effect_per_beneficiary"], "effect_per_beneficiary")
    cpb = _num(args["cost_per_beneficiary"], "cost_per_beneficiary", minimum=0)
    fixed = _num(args.get("fixed_costs"), "fixed_costs", minimum=0, default=0.0)
    years = int(args.get("years") or 1)
    decay = _num(args.get("effect_decay_pct"), "effect_decay_pct", minimum=0, maximum=100, default=0.0) / 100.0
    value = _num(args.get("value_per_unit"), "value_per_unit", minimum=0) if args.get("value_per_unit") is not None else None
    sims = int(args.get("simulations") or 2000)
    tu_r = _range(args, "take_up", tu, "take_up_rate", 0.0, 1.0)
    eff_r = _range(args, "effect", eff, "effect_per_beneficiary")
    eff_se = _num(args.get("effect_se"), "effect_se", minimum=0) if args.get("effect_se") is not None else None
    if eff_se is not None and eff_r is not None:
        raise InvalidArguments("give effect_low/effect_high or effect_se, not both")
    if eff_se == 0:
        eff_se = None
    val_r = _range(args, "value", value, "value_per_unit", 0.0) if value is not None else None
    if value is None and (args.get("value_low") is not None or args.get("value_high") is not None):
        raise InvalidArguments("value_low/value_high need value_per_unit")
    notes: List[str] = []
    ctx.progress(15, "central projection")
    S = float(sum((1 - decay) ** (k - 1) / (1 + rate) ** k for k in range(1, years + 1)))     # discounted outcome multiplier per unit of first-year effect
    U = float(sum((1 - decay) ** (k - 1) for k in range(1, years + 1)))                       # undiscounted outcome multiplier

    def project(tu_, eff_, val_):
        benef = pop * tu_
        units = benef * eff_ * U
        costs = fixed + cpb * benef
        pvb = (val_ * benef * eff_ * S) if val_ is not None else None
        return benef, units, costs, pvb

    benef, units, costs, pvb = project(tu, eff, value)
    npv = (pvb - costs) if pvb is not None else None
    bcr = (pvb / costs) if (pvb is not None and costs > 0) else None
    roi = ((pvb - costs) / costs) if (pvb is not None and costs > 0) else None
    if costs <= 0:
        notes.append("total costs are zero, so cost per beneficiary, cost per unit, BCR and ROI are undefined")
    be_take_up = be_effect = None
    if value is not None:
        per_benef_pv = value * eff * S
        if per_benef_pv > cpb and pop > 0:
            be_take_up = fixed / (pop * (per_benef_pv - cpb))
        else:
            notes.append(f"break-even take-up is undefined: the discounted value per beneficiary ({_money(per_benef_pv)}) does not exceed the cost per beneficiary ({_money(cpb)}), so no take-up rate breaks even")
        if benef > 0 and value > 0 and S > 0:
            be_effect = costs / (benef * value * S)
        else:
            notes.append("break-even effect is undefined (no beneficiaries or zero value per unit)")
    ranged = [k for k, r in (("take_up_rate", tu_r), ("effect_per_beneficiary", eff_r or eff_se), ("value_per_unit", val_r)) if r]
    sim: Optional[Dict[str, Any]] = None
    tornado: List[Dict[str, Any]] = []
    metric_name = "npv" if value is not None else "total_outcome_units"
    if ranged:
        ctx.progress(40, f"simulating ({sims:,} draws)")
        rng = np.random.default_rng(0)
        tu_d = _tri(rng, tu_r[0], tu, tu_r[1], sims) if tu_r else np.full(sims, tu)
        if eff_se is not None:
            eff_d = rng.normal(eff, eff_se, sims)
        elif eff_r:
            eff_d = _tri(rng, eff_r[0], eff, eff_r[1], sims)
        else:
            eff_d = np.full(sims, eff)
        val_d = _tri(rng, val_r[0], value, val_r[1], sims) if val_r else (np.full(sims, value) if value is not None else None)
        b_d, u_d, c_d, pvb_d = project(tu_d, eff_d, val_d)
        q = lambda a: (float(np.percentile(a, 5)), float(np.percentile(a, 95)))
        sim = {"simulations": sims, "seed": 0, "beneficiaries_90": q(b_d), "total_outcome_units_90": q(u_d), "total_costs_90": q(c_d)}
        if pvb_d is not None:
            npv_d = pvb_d - c_d
            with np.errstate(all="ignore"):
                bcr_d = np.where(c_d > 0, pvb_d / c_d, np.nan)
                roi_d = np.where(c_d > 0, (pvb_d - c_d) / c_d, np.nan)
            ok = np.isfinite(bcr_d)
            sim.update({"pv_benefits_90": q(pvb_d), "npv_90": q(npv_d), "bcr_90": q(bcr_d[ok]) if ok.any() else None, "roi_90": q(roi_d[ok]) if ok.any() else None,
                        "p_bcr_above_1": float(np.mean(npv_d[c_d > 0] > 0)) if (c_d > 0).any() else None, "p_npv_positive": float(np.mean(npv_d > 0))})
            sim["bcr_draws"] = bcr_d[ok]
        for key, r, central in (("take_up_rate", tu_r, tu), ("effect_per_beneficiary", (eff - Z90 * eff_se, eff + Z90 * eff_se) if eff_se is not None else eff_r, eff), ("value_per_unit", val_r, value)):
            if not r:
                continue
            res = []
            for v in r:
                kw = {"take_up_rate": tu, "effect_per_beneficiary": eff, "value_per_unit": value}
                kw[key] = v
                b_, u_, c_, p_ = project(kw["take_up_rate"], kw["effect_per_beneficiary"], kw["value_per_unit"])
                res.append((p_ - c_) if p_ is not None else u_)
            tornado.append({"input": key, "low": _r6(r[0]), "high": _r6(r[1]), "central": _r6(central), f"{metric_name}_at_low": _money(res[0]) if value is not None else _r6(res[0]),
                            f"{metric_name}_at_high": _money(res[1]) if value is not None else _r6(res[1]), "swing": _money(abs(res[1] - res[0])) if value is not None else _r6(abs(res[1] - res[0]))})
        tornado.sort(key=lambda r: -(r["swing"] or 0))
    else:
        notes.append("no low/high ranges or effect_se were given, so the projection is a single deterministic calculation without intervals")
    ctx.progress(75, "writing")
    out = ctx.project.new_artifact_dir("ex-ante", unit)
    table = [{"quantity": "beneficiaries", "formula": "target_population × take_up_rate", "value": benef},
             {"quantity": "total_outcome_units", "formula": f"beneficiaries × effect × Σ_k (1 − decay)^(k−1), k = 1..{years}", "value": units},
             {"quantity": "total_costs", "formula": "fixed_costs + cost_per_beneficiary × beneficiaries (year 0)", "value": costs},
             {"quantity": "cost_per_beneficiary_total", "formula": "total_costs / beneficiaries", "value": costs / benef if benef > 0 else None},
             {"quantity": "cost_per_outcome_unit", "formula": "total_costs / total_outcome_units", "value": costs / units if units > 0 else None}]
    if value is not None:
        table += [{"quantity": "pv_benefits", "formula": f"value_per_unit × beneficiaries × effect × Σ_k (1 − decay)^(k−1) / (1 + r)^k", "value": pvb},
                  {"quantity": "npv", "formula": "pv_benefits − total_costs", "value": npv}, {"quantity": "bcr", "formula": "pv_benefits / total_costs", "value": bcr},
                  {"quantity": "roi", "formula": "(pv_benefits − total_costs) / total_costs", "value": roi},
                  {"quantity": "break_even_take_up_rate", "formula": "fixed_costs / (target_population × (value × effect × Σ_k disc − cost_per_beneficiary))", "value": be_take_up},
                  {"quantity": "break_even_effect_per_beneficiary", "formula": "total_costs / (beneficiaries × value × Σ_k disc)", "value": be_effect}]
    csv_path = _save_table(ctx, out, "calculation", pd.DataFrame(table))
    if tornado:
        _save_table(ctx, out, "tornado", pd.DataFrame(tornado))
    bcr_varies = False
    if sim and sim.get("bcr_draws") is not None and len(sim["bcr_draws"]) > 1:
        d = sim["bcr_draws"]
        bcr_varies = float(np.ptp(d)) > 1e-9 * max(1.0, float(np.max(np.abs(d))))
        if not bcr_varies:
            notes.append("the BCR is the same in every draw (with zero fixed costs, benefits and costs both scale with take-up), so the chart shows the outcome range instead of a BCR distribution")
    if bcr_varies:
        draws = sim.pop("bcr_draws")
        counts, edges = np.histogram(draws, bins=20)
        chart = svg.histogram(f"Projected BCR across {sims:,} draws", [float(e) for e in edges], [int(c) for c in counts],
                              subtitle=f"central {_ratio(bcr)} · 90% interval {_ratio(sim['bcr_90'][0])} to {_ratio(sim['bcr_90'][1])} · P(BCR > 1) = {_pct1(sim['p_bcr_above_1'] * 100)}%")
        chart_path = str(ctx.project.write_text(out / "bcr_distribution.svg", chart))
    else:
        if sim and "bcr_draws" in sim:
            sim.pop("bcr_draws")
        lab = [f"total {unit}", "beneficiaries"]
        vals = [units, benef]
        lows = [sim["total_outcome_units_90"][0], sim["beneficiaries_90"][0]] if sim else vals
        highs = [sim["total_outcome_units_90"][1], sim["beneficiaries_90"][1]] if sim else vals
        chart = svg.dots(f"Projected outcome ({unit})", lab, vals, lows, highs, subtitle=("dot = central, bar = 90% interval" if sim else "central projection, no ranges given"), zero_line=False)
        chart_path = str(ctx.project.write_text(out / "outcome_range.svg", chart))
        _office_png(chart_path, "dots", f"Projected outcome ({unit})", lab, vals, lows, highs)
    if sim:
        for k, v in list(sim.items()):
            if isinstance(v, tuple):
                sim[k] = [(_money(x) if k in ("pv_benefits_90", "npv_90", "total_costs_90") else _ratio(x) if k in ("bcr_90", "roi_90") else _r6(x)) for x in v]
            elif isinstance(v, float):
                sim[k] = round(v, 4)
    assumptions = [_assumption("target_population", pop, "caller"), _assumption("take_up_rate", {"central": tu, "low": tu_r[0] if tu_r else None, "high": tu_r[1] if tu_r else None}, args.get("take_up_source")),
                   _assumption("effect_per_beneficiary", {"central": eff, "low": eff_r[0] if eff_r else None, "high": eff_r[1] if eff_r else None, "se": eff_se}, args.get("effect_source"), f"{unit} per beneficiary per year"),
                   _assumption("costs", {"cost_per_beneficiary": cpb, "fixed_costs": fixed}, args.get("cost_source"), currency),
                   _assumption("years", years, "caller"), _assumption("effect_decay_pct", decay * 100, "caller"), _assumption("discount_rate", rate, "caller")]
    if value is not None:
        assumptions.append(_assumption("value_per_unit", {"central": value, "low": val_r[0] if val_r else None, "high": val_r[1] if val_r else None}, args.get("value_source"), f"{currency} per {unit}"))
    interp = [PROJECTION_TEXT,
              f"If {_pct1(tu * 100)}% of {pop:,.0f} take up the intervention, {benef:,.0f} beneficiaries gain {_r6(eff)} {unit} each per year"
              + (f" (decaying {decay * 100:g}% a year over {years} years)" if years > 1 else "") + f": {_r6(units):,} {unit} in total"
              + (f" (90% interval {sim['total_outcome_units_90'][0]:,} to {sim['total_outcome_units_90'][1]:,})" if sim else "")
              + f" for {currency} {_money(costs):,} ({currency} {_money(costs / benef) if benef > 0 else None} per beneficiary, {currency} {_money(costs / units) if units > 0 else None} per {unit})."]
    if value is not None:
        interp.append(f"Valuing each {unit} at {currency} {_money(value):,}, PV benefits are {currency} {_money(pvb):,}: NPV {currency} {_money(npv):,}, BCR {_ratio(bcr)}, ROI {_pct1(roi * 100) if roi is not None else None}%"
                      + (f"; 90% interval for BCR {sim['bcr_90'][0]} to {sim['bcr_90'][1]}, P(BCR > 1) = {_pct1(sim['p_bcr_above_1'] * 100)}%." if sim and sim.get("bcr_90") else ".")
                      + (f" Break-even take-up {_pct1(be_take_up * 100)}%" + (" (not reachable: above 100%)." if be_take_up > 1 else ".") if be_take_up is not None else "")
                      + (f" Break-even effect {_r6(be_effect)} {unit} per beneficiary ({_pct1(be_effect / eff * 100) if eff else None}% of the assumed effect)." if be_effect is not None else ""))
    if tornado:
        interp.append(f"The result is most sensitive to {tornado[0]['input']} (swing in {metric_name} of {tornado[0]['swing']:,} between its low and high).")
    ctx.project.audit({"tool": "ex_ante_impact_projection", "unit": unit, "beneficiaries": benef, "npv": npv, "artifact": str(out)})
    ctx.progress(100, "done")
    return {"currency": currency, "outcome_unit": unit, "discount_rate": rate, "years": years, "beneficiaries": _r6(benef), "total_outcome_units": _r6(units),
            "total_costs": _money(costs), "cost_per_beneficiary": _money(costs / benef) if benef > 0 else None, "cost_per_outcome_unit": _money(costs / units) if units > 0 else None,
            "pv_benefits": _money(pvb) if pvb is not None else None, "npv": _money(npv) if npv is not None else None, "bcr": _ratio(bcr), "roi": _ratio(roi),
            "roi_pct": _pct1(roi * 100) if roi is not None else None, "break_even_take_up_rate": _r6(be_take_up), "break_even_effect_per_beneficiary": _r6(be_effect),
            "unrounded": {"beneficiaries": benef, "total_outcome_units": units, "total_costs": costs, "pv_benefits": pvb, "npv": npv, "bcr": bcr, "roi": roi,
                          "break_even_take_up_rate": be_take_up, "break_even_effect_per_beneficiary": be_effect, "discount_multiplier": S, "outcome_multiplier": U},
            "ranged_inputs": ranged, "simulation": sim, "sensitivity": tornado, "calculation_table": table, "assumptions": assumptions, **_transparency(assumptions),
            "notes": notes, "interpretation": interp, "summary_text": interp[1],
            "caption": (f"Distribution of the projected BCR over {sims:,} seeded draws; a projection from stated assumptions." if sim and value is not None and sim.get("bcr_90")
                        else f"Projected beneficiaries and total {unit} from stated assumptions" + (" with 90% intervals." if sim else ".")),
            "chart_path": chart_path, "csv_path": csv_path, "artifact_dir": str(out), "elapsed_s": round(time.time() - t0, 2)}
