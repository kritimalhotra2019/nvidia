"""Plain-language meaning for variables and findings (28 Oct 2026, user: "find_insights just shows associations
with var names exactly as is without explaining what the var means, what the association could mean or not mean").

Two general helpers: variable_meaning() reads a column name through a lexicon of abbreviations common in
economics, trade and household survey data plus the column's observed profile; interpret() writes, per finding
kind, what the pattern could mean and what it could not, flagging relations that hold by construction (a rank of
the same index, the same measure under two classifications) so they are not read as results about the world."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

LEXICON: Dict[str, str] = {
    "eci": "Economic Complexity Index (how diversified and sophisticated an economy's exports are; higher = more complex)",
    "coi": "Complexity Outlook Index", "pci": "Product Complexity Index",
    "hs": "Harmonized System trade classification", "hs92": "HS 1992 trade classification", "hs96": "HS 1996 trade classification",
    "hs02": "HS 2002 trade classification", "hs07": "HS 2007 trade classification", "hs12": "HS 2012 trade classification", "hs17": "HS 2017 trade classification",
    "sitc": "SITC (Standard International Trade Classification)", "rank": "rank (1 = highest)", "proj": "projection (projected)",
    "growth": "growth rate", "gdp": "GDP (gross domestic product)", "gni": "GNI (gross national income)", "pc": "per capita", "percap": "per capita",
    "pct": "percent", "perc": "percent", "share": "share (fraction of the total)", "idx": "index", "index": "index", "score": "score",
    "inr": "Indian rupees", "usd": "US dollars", "eur": "euros", "ppp": "purchasing-power parity", "cpi": "consumer price index", "infl": "inflation",
    "hh": "household", "hhs": "households", "exp": "expenditure", "expend": "expenditure", "cons": "consumption", "inc": "income", "wage": "wage",
    "edu": "education", "educ": "education", "emp": "employment", "unemp": "unemployment", "lfp": "labour-force participation",
    "yr": "year", "yrs": "years", "mo": "month", "qtr": "quarter", "wk": "week", "avg": "average", "med": "median", "sd": "standard deviation", "se": "standard error",
    "n": "count", "num": "number", "cnt": "count", "tot": "total", "id": "identifier", "iso": "ISO country code", "iso3": "ISO three-letter country code",
    "dob": "date of birth", "fs": "food security", "foodsec": "food security", "wb": "wellbeing", "satis": "satisfaction", "bmi": "body-mass index",
    "haz": "height-for-age z-score", "waz": "weight-for-age z-score", "whz": "weight-for-height z-score", "muac": "mid-upper-arm circumference",
    "fdi": "foreign direct investment", "oda": "official development assistance", "pop": "population", "urb": "urban", "rur": "rural",
    "elec": "electricity", "san": "sanitation", "san_imp": "improved sanitation", "dist": "district", "prov": "province", "reg": "region",
    "treat": "treatment (programme) group", "ctrl": "control group", "bl": "baseline", "el": "endline", "ml": "midline", "fu": "follow-up",
    "r1": "round 1", "r2": "round 2", "r3": "round 3", "t0": "baseline", "t1": "first follow-up", "t2": "second follow-up",
    "amt": "amount", "val": "value", "qty": "quantity", "prc": "price", "px": "price", "vol": "volume", "rev": "revenue", "prof": "profit",
    "log": "natural log of", "ln": "natural log of", "sq": "squared", "chg": "change in", "diff": "difference in", "dummy": "0/1 indicator", "flag": "0/1 indicator",
}
GENERIC = {"value", "values", "data", "var", "variable", "col", "column", "x", "y", "a", "b"}


def _tokens(name: str) -> List[str]:
    """Split on separators and camelCase; a letters+digits token stays whole when the lexicon knows it (hs92, r1)."""
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(name))
    out: List[str] = []
    for t in re.split(r"[^A-Za-z0-9]+", s):
        if not t:
            continue
        if t.lower() in LEXICON or not re.fullmatch(r"[A-Za-z]+\d+", t):
            out.append(t)
        else:
            m = re.fullmatch(r"([A-Za-z]+)(\d+)", t)
            out.extend([m.group(1), m.group(2)])
    return out


def expand_name(name: str) -> str:
    """'eci_rank_hs92' → 'Economic Complexity Index (…) rank (1 = highest) under the HS 1992 trade classification'."""
    toks = _tokens(name)
    out: List[str] = []
    for t in toks:
        low = t.lower()
        if low in LEXICON:
            out.append(LEXICON[low])
        elif re.fullmatch(r"\d{4}", low):
            out.append(f"year {low}")
        elif re.fullmatch(r"\d+", low):
            out.append(f"(#{low})")
        else:
            out.append(low)
    return " ".join(out)


def variable_meaning(name: str, info: Optional[Dict[str, Any]] = None, series=None) -> str:
    """One sentence: expanded name, kind of variable, observed range or categories, missingness."""
    info = info or {}
    expanded = expand_name(name)
    parts = [f"{name}: {expanded}" if expanded.lower() != str(name).lower().replace("_", " ") else f"{name}"]
    role = info.get("role")
    if role == "numeric" and series is not None:
        try:
            import pandas as pd
            s = pd.to_numeric(series, errors="coerce").dropna()
            if len(s):
                lo, hi, med = float(s.min()), float(s.max()), float(s.median())
                fmt = (lambda v: f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.3g}")
                parts.append(f"numeric, observed {fmt(lo)} to {fmt(hi)} (median {fmt(med)})")
        except Exception:
            parts.append("numeric")
    elif role == "numeric":
        parts.append("numeric")
    elif role in ("categorical", "boolean"):
        u = info.get("unique")
        parts.append(f"{role} with {u} distinct values" if u else role)
    elif role == "date":
        parts.append("date")
    elif role:
        parts.append(str(role))
    miss = info.get("missing_pct")
    if isinstance(miss, (int, float)) and miss >= 5:
        parts.append(f"{miss:.0f}% missing")
    if re.search(r"(^|_)(rank|rk)($|_)", str(name), re.I):
        parts.append("a rank reverses the index it orders: rank 1 is the highest value")
    return "; ".join(parts) + "."


def _core(name: str) -> List[str]:
    return [t.lower() for t in _tokens(name) if t.lower() not in {"rank", "rk", "idx", "index", "score", "val", "value"} and not t.isdigit()]


def _same_measure(a: str, b: str) -> Optional[str]:
    """Why two columns may be the same thing: a rank of the other, or one measure under two classifications."""
    ra, rb = bool(re.search(r"(^|_)(rank|rk)($|_)", a, re.I)), bool(re.search(r"(^|_)(rank|rk)($|_)", b, re.I))
    ca, cb = _core(a), _core(b)
    if ra != rb and ca == cb:
        return "rank"
    classes = {"hs", "hs92", "hs96", "hs02", "hs07", "hs12", "hs17", "sitc", "naics", "isic", "cpc"}
    sa, sb = set(ca) - classes, set(cb) - classes
    if sa and sa == sb and (set(ca) & classes) and (set(cb) & classes) and ca != cb:
        return "classification"
    if ca and ca == cb and a != b:
        return "variant"
    return None


def readings(f: Dict[str, Any], cols: Dict[str, Dict[str, Any]], not_causal: str) -> Dict[str, str]:
    """Two registers for every finding (28 Oct, user): 'specialist' in economist / impact-evaluation terms and
    'plain' for a reader without them. Each says what the pattern could mean and what it could not."""
    kind = f.get("kind")
    if kind == "correlation":
        a, b, r = f.get("a", ""), f.get("b", ""), float((f.get("numbers") or {}).get("r") or 0)
        same = _same_measure(a, b)
        if same == "rank":
            ranked = b if re.search(r"rank", b, re.I) else a
            return {"specialist": f"Mechanical collinearity: {ranked} is a monotone transform (rank) of the other column, so r ≈ −1 holds by construction. Not an empirical relationship; drop one before modelling.",
                    "plain": f"{ranked} just orders the other column from highest to lowest, so of course they move opposite to each other. This tells us nothing new."}
        if same == "classification":
            return {"specialist": f"The same index under two trade classifications (r = {r:+.2f}): a measurement-consistency check across revisions, not an economic relationship. Retain one series; report the other only as a robustness check.",
                    "plain": f"These are two versions of the same measure, counted with two different product lists, so they agree almost perfectly. That is a check that the data are consistent, not a result about the economy."}
        if same == "variant":
            return {"specialist": "Two variants of one measure; near-identical by construction. Treat as one variable.",
                    "plain": "Two versions of the same thing; expect them to agree."}
        strength = "strong" if abs(r) >= 0.5 else "moderate"
        return {"specialist": f"Bivariate Pearson r = {r:+.2f} ({strength}, pooled across rows). Candidate explanations: a genuine gradient, omitted variables (unit size, period, region) driving both, reverse causality, or a mechanical link if one series is derived from the other; influential observations can inflate |r|. Pooled cross-section and within-unit relationships can differ in sign. {not_causal}",
                "plain": f"Rows with higher {a} tend to have {'higher' if r > 0 else 'lower'} {b}. This could be a real link, or both could be moved by something else (bigger countries, later years, one region), or one number may be partly built from the other. It does not show that one causes the other."}
    if kind == "trend":
        y, over = f.get("outcome", ""), f.get("over", "") or "period"
        return {"specialist": f"Change in the cross-sectional mean of {y} by {over} on an unbalanced panel: a composition effect (entry, exit, missingness by {over}) can move the mean with no within-unit change. Verify with a balanced panel or unit fixed effects before interpreting as a secular trend.",
                "plain": f"This is the average {y} across everyone in the data for each {over}. If different countries or households appear in different years, the average can rise or fall without any one of them changing. Check the same units over time before calling it a trend."}
    if kind == "group_difference":
        y, g = f.get("outcome", ""), f.get("by", "")
        eta = (f.get("numbers") or {}).get("eta_squared")
        es = f" (η² = {eta:.2f})" if isinstance(eta, (int, float)) else ""
        return {"specialist": f"Difference in means of {y} across {g}{es}. Without random assignment to {g}, selection and confounding (covariates that differ by group) are not ruled out; the gap is descriptive. Conditioning on baseline covariates or a credible design (RCT, DiD, RD) is needed for a causal reading. {not_causal}",
                "plain": f"{y} is different on average between the {g} groups. The groups may also differ in other ways, such as age, region or who ends up in each group, and those differences could explain the gap. It does not show that {g} causes {y}."}
    if kind == "concentration":
        return {"specialist": "Highly unbalanced categorical distribution; comparisons against minority categories have low power and wide confidence intervals.",
                "plain": "Almost everyone is in one category, so comparisons with the smaller categories rest on very few cases and are shaky."}
    if kind == "skew":
        return {"specialist": "Right-skewed distribution: the mean exceeds the median; use medians, trimmed means or a log transform; standard errors on the mean are sensitive to the tail.",
                "plain": "A few very large values pull the average up. The middle value (median) describes a typical row better."}
    if kind == "outliers":
        return {"specialist": "Extreme values beyond 3 IQR: possible measurement error, unit inconsistency or genuine tail; inspect and winsorise or flag before estimation.",
                "plain": "Some values are far outside the rest. They may be typos, mixed units, or real rare cases. Look at them before trusting averages."}
    if kind == "structure":
        return {"specialist": "Wide-format roster blocks: per-member fields of one record. Reshape to long format for member-level analysis.",
                "plain": "Repeated column groups are the same questions asked about each household member, not separate variables."}
    text = str(f.get("interpretation") or not_causal)
    return {"specialist": text, "plain": text}


def interpret(f: Dict[str, Any], cols: Dict[str, Dict[str, Any]], not_causal: str) -> str:
    r = readings(f, cols, not_causal)
    return f"Specialist reading: {r['specialist']} Plain reading: {r['plain']}"


def finding_vars(f: Dict[str, Any]) -> List[str]:
    out = []
    for k in ("a", "b", "outcome", "by", "over", "column"):
        v = f.get(k)
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    return out


def glossary(names: List[str], cols: Dict[str, Dict[str, Any]], df=None, limit: int = 16) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for n in names[:limit]:
        out[n] = variable_meaning(n, cols.get(n), df[n] if df is not None and n in df.columns else None)
    return out
