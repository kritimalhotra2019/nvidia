"""HTML reports for the workspace panel. Self-contained files: inline CSS, inline SVG, no scripts,
no remote assets, so they render offline in a WKWebView and open in any browser."""
from __future__ import annotations

import html
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

from ..registry import JobContext, tool, InvalidArguments
from .. import svg
from .document_style import STYLE_SCHEMA, normalize_style, heading_color, contrast_text
from ..templates import TEMPLATE_ID_SCHEMA, resolve_template, template_chart_palette

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
REPORT_CSP = ("default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
              "img-src 'self' data:; connect-src 'none'; font-src 'none'; object-src 'none'; "
              "frame-src 'none'; media-src 'none'; base-uri 'none'; form-action 'none'")
_STATIC_SVG_ELEMENTS = {
    "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textpath", "use", "image", "clippath", "mask", "pattern",
    "lineargradient", "radialgradient", "stop", "style", "title", "desc", "symbol",
}
_PRESENTATION_ATTRIBUTES = {"style", "fill", "stroke", "filter", "clip-path", "mask",
                            "marker-start", "marker-mid", "marker-end", "cursor"}


def _svg_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def _safe_svg_css(value: str) -> None:
    """Static chart styles only; URLs may reference an element in this SVG."""
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    if any(token in value for token in ("\\", "@", "<", ">", "/*", "*/")):
        raise InvalidArguments("chart SVG contains an unsafe or unsupported style")
    for match in re.finditer(r"([A-Za-z_-][\w-]*)\s*\(", value):
        if match.group(1).lower() not in {"url", "rgb", "rgba", "hsl", "hsla"}:
            raise InvalidArguments("chart SVG contains an unsupported CSS function")
        if match.group(1).lower() == "url":
            url = re.match(r"url\s*\(\s*(['\"]?)(#[A-Za-z0-9_.:-]+)\1\s*\)", value[match.start():], re.I)
            if url is None:
                raise InvalidArguments("chart SVG styles may only reference internal #fragment URLs")


def _safe_chart_svg(project, chart_path: str) -> str:
    """Validate and reserialize a static SVG; never inline arbitrary project text."""
    path = Path(chart_path)
    try:
        path = (project.root / path if not path.is_absolute() else path).resolve()
        path.relative_to(project.root.resolve())
        if path.suffix.lower() != ".svg" or not path.is_file():
            raise ValueError("not an SVG file")
        raw = path.read_text("utf-8")
    except (OSError, UnicodeError, ValueError) as e:
        raise InvalidArguments(f"chart_path must be an SVG file inside the project: {chart_path}") from e
    if re.search(r"<!\s*ENTITY\b", raw, re.I):
        raise InvalidArguments("chart SVG must not contain entity declarations")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise InvalidArguments("chart_path is not a well-formed SVG") from e
    if root.tag not in ("svg", "{" + SVG_NS + "}svg"):
        raise InvalidArguments("chart_path must have an SVG root element")
    # Check executable markup even inside metadata, then remove metadata and all
    # XML processing instructions/DTDs by serializing only the parsed SVG tree.
    for node in root.iter():
        if _svg_name(node.tag) in {"script", "foreignobject"}:
            raise InvalidArguments("chart SVG contains executable markup")
        if any(_svg_name(key).startswith("on") for key in node.attrib):
            raise InvalidArguments("chart SVG contains an event handler")
    for parent in root.iter():
        for child in list(parent):
            if _svg_name(child.tag) == "metadata":
                parent.remove(child)
    for node in root.iter():
        name = _svg_name(node.tag)
        if name not in _STATIC_SVG_ELEMENTS or (node.tag.startswith("{") and not node.tag.startswith("{" + SVG_NS + "}")):
            raise InvalidArguments(f"chart SVG contains an unsupported element: {name}")
        if name == "style":
            if list(node):
                raise InvalidArguments("chart SVG styles must contain plain CSS text")
            _safe_svg_css(node.text or "")
        for key, value in node.attrib.items():
            attribute = _svg_name(key)
            if attribute == "base":
                raise InvalidArguments("chart SVG must not change its base URL")
            if attribute in {"href", "src"}:
                internal = re.fullmatch(r"#[A-Za-z0-9_.:-]+", value.strip())
                raster = name == "image" and re.fullmatch(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+", value.strip(), re.I)
                if internal is None and not raster:
                    raise InvalidArguments("chart SVG references must be internal fragments or embedded raster images")
            if attribute in _PRESENTATION_ATTRIBUTES or re.search(r"url\s*\(", value, re.I):
                _safe_svg_css(value)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return ET.tostring(root, encoding="unicode")

CSS = """
:root { --ink:#35285e; --muted:#6b6480; --lav:#b6a6ee; --lav-soft:#efeafc; --deep:#5b4ca3; --bg:#fbfaff; --card:#ffffff; --warn:#b45309; --warn-bg:#fff4e0; --ok:#2f7a4a; --line:#e9e2fb; }
@media (prefers-color-scheme: dark) { :root { --ink:#eae4ff; --muted:#a79fc4; --lav:#8f7edb; --lav-soft:#2b2447; --deep:#c4b6ff; --bg:#17132a; --card:#211b3a; --warn:#ffc46b; --warn-bg:#3a2b12; --ok:#8fd4a8; --line:#352c5a; } }
* { box-sizing:border-box } html,body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,"SF Pro Text","Helvetica Neue",Helvetica,Arial,sans-serif; -webkit-font-smoothing:antialiased }
main { max-width:820px; margin:0 auto; padding:24px 20px 60px }
h1 { font-size:22px; margin:0 0 4px } h2 { font-size:15px; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); margin:28px 0 10px }
.sub { color:var(--muted); margin:0 0 18px } .lead { font-size:15px; background:var(--lav-soft); border-radius:14px; padding:14px 16px; margin:14px 0 }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px } .tile { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px 14px }
.tile b { display:block; font-size:20px } .tile span { color:var(--muted); font-size:12px }
table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; font-size:13px }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top } th { color:var(--muted); font-weight:600; font-size:12px } tr:last-child td { border-bottom:0 }
.role { display:inline-block; padding:1px 8px; border-radius:999px; background:var(--lav-soft); color:var(--deep); font-size:11.5px; font-weight:600 }
.flag { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11.5px; margin:2px 4px 2px 0; background:var(--lav-soft); color:var(--deep) } .flag.warn { background:var(--warn-bg); color:var(--warn) }
.chart { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:8px; margin:12px 0 } .chart svg { width:100%; height:auto; display:block }
.note { color:var(--muted); font-size:12.5px } code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; background:var(--lav-soft); padding:1px 5px; border-radius:6px }
.flags { list-style:none; padding:0; margin:0 } .flagrow { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 12px; margin:6px 0 }
.why { color:var(--muted); font-size:12.5px; margin-top:4px } .caption { color:var(--muted); font-size:12.5px; margin:6px 8px 4px; line-height:1.45 }
.foot { margin-top:30px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); padding-top:12px }
@media (prefers-color-scheme: dark) { .chart svg rect:first-child { fill:#211b3a } .chart svg text { fill:#cfc6ef } }
"""


def _e(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def _num(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return svg._fmt(v)
    if isinstance(v, int):
        return f"{v:,}"
    return _e(v)


def _page(title: str, body: str, style=None) -> str:
    style = normalize_style(style)
    custom = ""
    if style:
        variables = []
        for key, names in [("body_color", ["ink", "muted"]), ("background_color", ["bg", "card"]), ("accent_color", ["deep", "lav"])]:
            variables.extend(f"--{name}:{style[key]}" for name in names if key in style)
        custom += ":root {" + ";".join(variables) + "}"
        if "heading_color" in style or "accent_color" in style:
            custom += "h1,h2 {color:" + heading_color(style) + "}"
        if "font_family" in style:
            custom += 'html,body {font-family:"' + style["font_family"] + '",sans-serif}'
        if "font_size" in style:
            custom += "html,body {font-size:" + str(style["font_size"]) + "pt}"
        if "accent_color" in style:
            custom += "th {background:" + style["accent_color"] + ";color:" + contrast_text(style["accent_color"]) + "}"
    # Generated SVGs already carry the requested colors. Do not recolor them
    # through the workspace's automatic dark-theme override.
    css = CSS if not style else re.sub(r"@media \(prefers-color-scheme: dark\) \{ \.chart svg .*? \}\s*\}", "", CSS)
    return f'<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{_e(REPORT_CSP)}"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title><style>{css}{custom}</style></head><body><main>{body}</main></body></html>'


def _column_rows(prof: Dict[str, Any]) -> str:
    rows = []
    for c in prof["columns"]:
        st = c.get("stats") or {}
        if c["role"] == "numeric" and st:
            detail = f"min {_num(st.get('min'))} · median {_num(st.get('median'))} · mean {_num(st.get('mean'))} · max {_num(st.get('max'))}"
        elif c["role"] == "date" and st:
            detail = f"{_e(st.get('min', '')[:10])} → {_e(st.get('max', '')[:10])} · {_e(st.get('frequency') or 'irregular')} · {_num(st.get('distinct_periods'))} periods"
        elif c["role"] in ("categorical", "text") and c.get("top_values"):
            detail = ", ".join(f"{_e(k)} ({v:,})" for k, v in c["top_values"][:4]) + (" …" if len(c["top_values"]) > 4 else "")
        elif c["role"] == "boolean" and st:
            detail = ", ".join(f"{_e(k)}: {v:,}" for k, v in st.get("values", {}).items())
        elif c["role"] == "id":
            detail = "identifier" + (f" · sample {_e(', '.join(c.get('sample', [])))}" if c.get("sample") else "")
        else:
            detail = ""
        notes = "".join(f'<div class="note">{_e(n)}</div>' for n in c.get("notes", []))
        flags = "".join(f'<span class="flag{" warn" if f in ("high_missing", "all_missing", "unit_unclear", "negative_values", "unparseable_dates", "case_or_space_variants") else ""}">{_e(f.replace("_", " "))}</span>' for f in c.get("flags", []))
        rows.append(f"<tr><td><code>{_e(c['name'])}</code><div class='note'>{_e(c['dtype'])}</div></td>"
                    f"<td><span class='role'>{_e(c['role'])}</span></td>"
                    f"<td>{_num(c['missing'])}<div class='note'>{c['missing_pct']:.1f}%</div></td>"
                    f"<td>{_num(c['unique'])}</td><td>{detail}{notes}{flags}</td></tr>")
    return "".join(rows)


def write_profile_report(project, out: Path, prof: Dict[str, Any]) -> Path:
    name = prof["name"]
    roles: Dict[str, int] = {}
    for c in prof["columns"]:
        roles[c["role"]] = roles.get(c["role"], 0) + 1
    tiles = [("rows", f"{prof['rows']:,}"), ("columns", str(prof["cols"])),
             ("missing cells", f"{sum(c['missing'] for c in prof['columns']):,}"),
             ("duplicate rows", f"{prof['duplicates']['rows']:,}")]
    tiles_html = "".join(f"<div class='tile'><b>{_e(v)}</b><span>{_e(k)}</span></div>" for k, v in tiles)
    roles_html = " · ".join(f"{v} {_e(k)}" for k, v in sorted(roles.items(), key=lambda kv: -kv[1]))
    flags = prof.get("quality_flags") or []
    def flag_item(f: Dict[str, Any]) -> str:
        label = f.get("label") or f["flag"].replace("_", " ")
        meaning = f.get("meaning") or ""
        action = f.get("action") or ""
        return (f"<li class='flagrow'><div><span class='flag {'warn' if f['level'] == 'warn' else ''}'>{_e(label)}</span> "
                f"<b>{_e(f['column'] or 'whole table')}</b> <span class='note'>· {_e(f['message'])}</span></div>"
                + (f"<div class='why'>{_e(meaning)}" + (f" <b>What to do:</b> {_e(action)}" if action else "") + "</div>" if meaning else "") + "</li>")
    warn = [f for f in flags if f["level"] == "warn"]
    flags_intro = ("<p class='note'>Nothing needs attention: no empty columns, no suspicious values, no duplicated rows.</p>" if not flags else
                   f"<p class='note'>{len(warn)} thing{'s' if len(warn) != 1 else ''} worth checking before you trust averages from this file"
                   + (f", plus {len(flags) - len(warn)} minor note{'s' if len(flags) - len(warn) != 1 else ''}" if len(flags) > len(warn) else "") + ". Each line says what was found, what it means, and what to do.</p>")
    flags_html = "".join(flag_item(f) for f in sorted(flags, key=lambda f: (f["level"] != "warn", f["flag"]))) or ""
    panel = prof.get("panel")
    panel_html = (f"<p class='lead'>Looks like a {'balanced' if panel['balanced'] else 'unbalanced'} panel: <b>{panel['units']:,}</b> units (<code>{_e(panel['unit'])}</code>) × <b>{panel['periods']}</b> periods (<code>{_e(panel['time'])}</code>)."
                  + (f" <span class='flag warn'>{panel['duplicate_unit_time_pairs']:,} duplicate unit × time pairs</span>" if panel['duplicate_unit_time_pairs'] else "") + "</p>") if panel else ""
    charts_html = ""
    for ch in prof.get("charts", []):
        try:
            cap = f"<p class='caption'>{_e(ch['caption'])}</p>" if ch.get("caption") else ""
            charts_html += f"<div class='chart'>{_safe_chart_svg(project, ch['path'])}{cap}</div>"
        except (OSError, InvalidArguments):
            continue
    sampled = f"<p class='note'>This profile was computed on the first {prof['rows_profiled']:,} rows of {prof['rows']:,} because the file is large.</p>" if prof.get("sampled") else ""
    sw = prof.get("software", {})
    body = (f"<h1>{_e(name)}</h1><p class='sub'>Dataset overview · {_e(prof['format'])}" + (f" · sheet {_e(prof['sheet'])}" if prof.get("sheet") else "") + f" · <code>{_e(prof['path'])}</code></p>"
            f"<p class='lead'>{_e(prof['summary_text'])}</p>{panel_html}{sampled}"
            f"<div class='grid'>{tiles_html}</div><p class='note' style='margin-top:8px'>{roles_html}</p>"
            f"<h2>Things to check</h2>{flags_intro}<ul class='flags'>{flags_html}</ul>"
            f"<h2>How to read the columns table</h2><p class='note'>Each row is one column of your file. <b>Role</b> is what Squipi thinks the column is: "
            f"<i>numeric</i> (amounts you can average), <i>categorical</i> (labels or groups), <i>id</i> (an identifier, never averaged), <i>date</i>, <i>boolean</i> (yes/no), <i>text</i>. "
            f"<b>Missing</b> is how many rows are blank. <b>Distinct</b> is how many different values appear. If a role is wrong, say so in the Chat tab and Squipi will correct it.</p>"
            f"<h2>Columns</h2><table><thead><tr><th>column</th><th>role</th><th>missing</th><th>distinct</th><th>summary</th></tr></thead><tbody>{_column_rows(prof)}</tbody></table>"
            f"<h2>Charts</h2>{charts_html or '<p class=note>No charts for this dataset.</p>'}"
            f"<div class='foot'>Deterministic profile by the Squipi worker (python {_e(sw.get('python'))}, pandas {_e(sw.get('pandas'))}). Roles are inferred from names and values and can be corrected; identifiers are never averaged. Nothing here is a causal claim.</div>")
    return project.write_text(out / "report.html", _page(f"{name} — overview", body))


@tool("render_report",
      "Write a simple HTML report from sections you supply (title, paragraphs, tables, chart paths). Numbers must come from tool results.",
      {"type": "object", "properties": {
          "title": {"type": "string", "minLength": 1},
          "style": STYLE_SCHEMA,
          "sections": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
              "heading": {"type": "string"},
              "text": {"type": ["string", "null"]},
              "table": {"type": ["object", "null"], "properties": {"columns": {"type": "array", "items": {"type": "string"}}, "rows": {"type": "array"}}},
              "chart_path": {"type": ["string", "null"]}}, "required": ["heading"]}},
          "sources": {"type": "array", "items": {"type": "string"}}},
       "required": ["title", "sections"], "additionalProperties": False})
def render_report(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    style = normalize_style(args.get("style"))
    parts = [f"<h1>{_e(args['title'])}</h1>"]
    for s in args["sections"]:
        parts.append(f"<h2>{_e(s['heading'])}</h2>")
        if s.get("text"):
            parts.append("".join(f"<p>{_e(p)}</p>" for p in str(s["text"]).split("\n\n")))
        if s.get("table"):
            t = s["table"]
            cols = t.get("columns", [])
            rows = t.get("rows", [])
            parts.append("<table><thead><tr>" + "".join(f"<th>{_e(c)}</th>" for c in cols) + "</tr></thead><tbody>"
                         + "".join("<tr>" + "".join(f"<td>{_num(v)}</td>" for v in r) + "</tr>" for r in rows) + "</tbody></table>")
        if s.get("chart_path"):
            parts.append(f"<div class='chart'>{_safe_chart_svg(ctx.project, s['chart_path'])}</div>")
    if args.get("sources"):
        parts.append("<h2>Sources</h2><ul>" + "".join(f"<li>{_e(s)}</li>" for s in args["sources"]) + "</ul>")
    parts.append("<div class='foot'>Written by Squipi from tool results. Check the numbers against the underlying outputs before sharing.</div>")
    out = ctx.project.new_artifact_dir("report", args["title"])
    path = ctx.project.write_text(out / "report.html", _page(args["title"], "".join(parts), style))
    ctx.project.audit({"tool": "render_report", "artifact": str(path)})
    return {"report_path": str(path), "artifact_dir": str(out), "style_applied": style}


# ---------- one-call dataset report

def _dictionary_units(ctx: JobContext, ds) -> Dict[str, str]:
    """Read labels as data from a workbook dictionary, including a cleaned copy's source."""
    import pandas as pd
    seen = set()
    while ds.derived_from and ds.id not in seen:
        seen.add(ds.id)
        try:
            ds = ctx.project.dataset(ds.derived_from)
        except KeyError:
            break
    if ds.format != "excel":
        return {}
    try:
        path = ctx.project.allow.check_readable(ds.path)
        with pd.ExcelFile(path, engine="openpyxl") as workbook:
            sheet = next((s for s in workbook.sheet_names if s.casefold().replace("_", " ") in {"data dictionary", "dictionary", "codebook"}), None)
            if sheet is None:
                return {}
            data = pd.read_excel(workbook, sheet_name=sheet, nrows=2000)
        headers = {str(c).strip().casefold(): c for c in data.columns}
        variable = next((headers[h] for h in ("variable", "column", "column name") if h in headers), None)
        unit = next((headers[h] for h in ("unit", "units", "type / unit") if h in headers), None)
        if variable is None or unit is None:
            return {}
        return {str(row[variable]): str(row[unit])[:160] for _, row in data.iterrows()
                if pd.notna(row[variable]) and pd.notna(row[unit])}
    except (OSError, ValueError, KeyError):
        # Optional metadata must not prevent a report on a readable data sheet.
        return {}


def _descriptive_sections(prof: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Descriptives are useful even when no ranked finding passes a threshold."""
    sections: List[Dict[str, Any]] = []
    numeric = [c for c in prof["columns"] if c["role"] == "numeric" and c.get("stats") and not c.get("pii")]
    n = prof["rows_profiled"]
    def statistic(value):
        # Chart tick abbreviations (e.g. 32k) discard too much precision in a
        # research table. Keep a consistent, explicit two-decimal format here.
        return f"{value:,.2f}" if value is not None and math.isfinite(value) else None
    # Small tables fit both a Word page and the exporter's 12-row slide limit.
    for start in range(0, len(numeric), 10):
        group = numeric[start:start + 10]
        label = f" ({start + 1}–{start + len(group)})" if len(numeric) > 10 else ""
        rows, spread = [], []
        for c in group:
            st = c["stats"]
            valid = sum(c.get("histogram", {}).get("counts", [])) or n - c["missing"]
            rows.append([c["name"], valid, c["missing"], statistic(st.get("mean")), statistic(st.get("std")) if valid > 1 else None, statistic(st.get("median"))])
            spread.append([c["name"], statistic(st.get("min")), statistic(st.get("p25")), statistic(st.get("p75")), statistic(st.get("max"))])
        sections.extend([
            {"heading": "Numeric descriptive statistics" + label,
             "text": "N is the number of valid observations. Missing values are excluded from each variable's calculations. SD is the sample standard deviation (undefined for fewer than two observations). Values retain their source units; identifiers and detected personal-data fields are excluded.",
             "table": {"columns": ["Variable", "Valid N", "Missing", "Mean", "SD", "Median"], "rows": rows}},
            {"heading": "Numeric ranges and quartiles" + label,
             "text": "The 25th and 75th percentiles bound the middle half of the observations.",
             "table": {"columns": ["Variable", "Minimum", "25th percentile", "75th percentile", "Maximum"], "rows": spread}},
        ])
    for c in prof["columns"]:
        if c["role"] not in ("categorical", "boolean") or c.get("pii"):
            continue
        values = c.get("top_values") or list((c.get("stats") or {}).get("values", {}).items())
        if not values:
            continue
        valid = n - c["missing"]
        rows = [[str(value), count, round(100 * count / valid, 2) if valid else None] for value, count in values[:10]]
        other = valid - sum(row[1] for row in rows)
        if other:
            rows.append(["Other categories (combined)", other, round(100 * other / valid, 2)])
        if c["missing"]:
            rows.append(["Missing (excluded from percentages)", c["missing"], None])
        sections.append({"heading": f"Category frequencies: {c['name']}",
                         "text": f"{valid:,} non-missing observations; {c['missing']:,} missing. Percentages use non-missing observations as the denominator. At most ten named categories are shown; all remaining categories are combined as Other.",
                         "table": {"columns": ["Category", "Count", "% of non-missing"], "rows": rows}})
    return sections


def _distribution_sections(ctx: JobContext, ds, prof: Dict[str, Any], outcome: str | None, units: Dict[str, str], style=None) -> List[Dict[str, Any]]:
    """Baseline distributions get portable PNGs, without relying on Quick Look."""
    from .datasets import read_dataset, SAMPLE_ROWS
    from .charts import draw
    numeric = [c for c in prof["columns"] if c["role"] == "numeric" and c.get("histogram") and not c.get("pii")]
    if outcome:
        numeric.sort(key=lambda c: c["name"] != outcome)
    categories = [c for c in prof["columns"] if c["role"] in ("categorical", "boolean") and c["unique"] and not c.get("pii")]
    selected = [(c, "histogram") for c in numeric[:2]] + [(c, "bar") for c in categories[:2]]
    if not selected:
        return []
    df = read_dataset(ds, nrows=SAMPLE_ROWS if prof.get("sampled") else None)
    out = ctx.project.new_artifact_dir("report-charts", ds.name)
    sections = []
    for i, (column, kind) in enumerate(selected, 1):
        ctx.check()
        name = column["name"]
        label = name.replace("_", " ") + (f" ({units[name]})" if name in units else "")
        # Only the plotted copy is relabelled; the source dataset is unchanged.
        frame = df[[name]].rename(columns={name: label})
        chart = draw(out, kind, frame, label, title=f"Distribution of {label}",
                     note=f"Source variable: {name}. Missing values excluded.", stem=f"distribution-{i}", style=style)
        sections.append({"heading": f"Distribution: {name}", "text": chart["caption"], "chart_path": chart["chart_path"]})
    return sections


@tool("report_dataset",
      "Write a complete report on a dataset in one call: overview, numeric descriptive statistics (N, missing, mean, SD, median, quartiles, range), category frequencies, labelled distribution graphs, key findings with charts (from find_insights), data-quality notes with what to do, "
      "a column guide and methods/limits. Produces HTML (opens in the workspace) and optionally Word and PowerPoint. Use for 'write a report', 'summarise this dataset', 'what did you find'.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "detail": {"type":"string","enum":["brief","full"],"description":"both write the full report (overview, distributions, every finding, method); kept for compatibility"},
          "style": STYLE_SCHEMA,
          "docx_template_id": TEMPLATE_ID_SCHEMA,
          "pptx_template_id": TEMPLATE_ID_SCHEMA,
          "title": {"type": ["string", "null"]},
          "filename": {"type": "string", "minLength": 1, "maxLength": 120,
                       "description": "Exact output basename for a single docx or pptx export, with its matching extension or no extension. For separately named outputs, make separate exports; not available with html or all."},
          "outcome": {"type": ["string", "null"], "description": "numeric column to focus the findings on"},
          "format": {"type": "string", "enum": ["html", "docx", "pptx", "all"], "description": "html opens on screen (default); docx / pptx also write that file; all writes everything"},
          "author": {"type": ["string", "null"]}},
       "required": ["dataset_id"], "additionalProperties": False})
def report_dataset(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    style = normalize_style(args.get("style"))
    from .datasets import profile_dataset
    from .insights import find_insights
    from .export import export_document, office_filename
    fmt = args.get("format") or "html"
    filename = None
    if "filename" in args:
        if fmt not in {"docx", "pptx"}:
            raise InvalidArguments("filename requires format docx or pptx. Use separate exports to name each output; omit filename for html or all.")
        filename = office_filename(args["filename"], fmt)
    ds = ctx.project.dataset(args["dataset_id"])
    title = args.get("title") or f"{ds.name.rsplit('.', 1)[0]}: what the data says"
    formats = ["html", "docx", "pptx"] if fmt == "all" else (["html", fmt] if fmt != "html" else ["html"])
    templates = {kind: resolve_template(ctx.project, kind, args.get(kind + "_template_id")) for kind in formats if kind != "html"}
    chart_style, chart_template = dict(style), None
    if "chart_palette" not in style:
        selected = templates.get("docx") or templates.get("pptx")
        palette = template_chart_palette(selected)
        if palette:
            chart_style["chart_palette"] = palette
            chart_template = {key: selected[key] for key in ("id", "name", "format")}
            chart_template["selection"] = "Shared charts use the Word template when both Word and PowerPoint formats are selected; otherwise they use the selected format. An explicit chart_palette overrides the saved template."
    ctx.progress(5, "profiling")
    prof = profile_dataset(ctx, {"dataset_id": ds.id})
    units = _dictionary_units(ctx, ds)
    ctx.progress(35, "finding patterns")
    ins = find_insights(ctx, {"dataset_id": ds.id, "outcome": args.get("outcome"), "max_findings": 8, "charts": True, "style": chart_style})
    ctx.progress(70, "writing")
    sections: List[Dict[str, Any]] = []
    tiles = [["rows", prof["rows"]], ["columns", prof["cols"]], ["missing cells", sum(c["missing"] for c in prof["columns"])],
             ["duplicate rows", prof["duplicates"]["rows"]], ["findings", len(ins["findings"])], ["quality issues", len(ins["data_quality"])]]
    sections.append({"heading": "Overview", "text": prof["summary_text"] + (f"\n\nFocus: {args['outcome']}." if args.get("outcome") else ""),
                     "table": {"columns": ["measure", "value"], "rows": tiles}})
    sections.extend(_descriptive_sections(prof))
    unit_rows = [[c["name"], units[c["name"]]] for c in prof["columns"] if c["name"] in units and not c.get("pii")]
    for start in range(0, len(unit_rows), 10):
        sections.append({"heading": "Variable units from the source dictionary", "text": "These labels are copied from the workbook's data dictionary. No unit conversions have been applied.",
                         "table": {"columns": ["Variable", "Type / unit"], "rows": unit_rows[start:start + 10]}})
    sections.extend(_distribution_sections(ctx, ds, prof, args.get("outcome"), units, chart_style))
    if ins["findings"]:
        for i, f in enumerate(ins["findings"], 1):
            text = f["detail"] + (f"\n\n{f['interpretation']}" if f.get("interpretation") else "")
            if f.get("caption"):
                text += f"\n\nHow to read the chart: {f['caption']}"
            sections.append({"heading": f"{i}. {f['title']}", "text": text, "chart_path": f.get("chart_path")})
    else:
        sections.append({"heading": "Findings", "text": "Nothing stood out: no group explains much of any outcome, no strong correlations and no trends over time."})
    if ins["data_quality"]:
        sections.append({"heading": "Data quality: fix these before trusting the numbers",
                         "table": {"columns": ["column", "issue", "what to do"], "rows": [[q["column"] or "whole table", q["issue"], q["action"]] for q in ins["data_quality"]]}})
    col_rows = []
    for c in prof["columns"]:
        st = c.get("stats") or {}
        if c.get("pii"):
            desc = "personal data; values not shown"
        elif c["role"] == "numeric" and st:
            desc = f"median {svg._fmt(st.get('median', 0))}, mean {svg._fmt(st.get('mean', 0))}, range {svg._fmt(st.get('min', 0))} to {svg._fmt(st.get('max', 0))}"
        elif c["role"] in ("categorical", "boolean") and c.get("top_values"):
            desc = ", ".join(f"{k} ({v:,})" for k, v in c["top_values"][:3])
        elif c["role"] == "date" and st:
            desc = f"{str(st.get('min', ''))[:10]} to {str(st.get('max', ''))[:10]}"
        else:
            desc = ""
        col_rows.append([c["name"], c["role"], f"{c['missing_pct']:.0f}%", desc])
    for start in range(0, len(col_rows), 10):
        group = col_rows[start:start + 10]
        sections.append({"heading": "Column guide" + (f" ({start + 1}–{start + len(group)})" if len(col_rows) > 10 else ""), "text": "Role is what the column is treated as: numeric (can be averaged), categorical (groups), id (never averaged), date, boolean, text.",
                         "table": {"columns": ["column", "role", "missing", "summary"], "rows": group}})
    sections.append({"heading": "Method and limits", "text": "\n\n".join([
        "All numbers were computed by Squipi's deterministic tools (pandas, numpy, scipy) on this Mac; the prose in each finding is a fixed template filled with those numbers.",
        " ".join(ins["limits"]),
        "Findings are descriptive. Causal claims need a research design (randomisation, difference-in-differences, instruments, discontinuities) that a profile does not have.",
        f"Source file: {ds.path}" + (f" (sheet {ds.sheet})" if ds.sheet else "") + ("; profiled on a sample of rows." if prof.get("sampled") else ".")])})
    finding_headings = {f"{i}. {f['title']}" for i, f in enumerate(ins["findings"], 1)}
    # 28 Oct (user): a report carries everything, whatever `detail` says. The old brief mode kept only charted
    # sections, which silently dropped seven of eight findings; nothing is pruned now.
    sources = [ds.path]
    results: Dict[str, Any] = {"dataset_id": ds.id, "title": title, "findings": len(ins["findings"]), "quality_issues": len(ins["data_quality"]), "sections": len(sections),
                               "charts": sum(bool(s.get("chart_path")) for s in sections), "exports": {}, "warnings": [], "style_applied": style,
                               "chart_style_applied": chart_style, "chart_template_applied": chart_template}
    html_res = render_report(ctx, {"title": title, "sections": sections, "sources": sources, "style": style})
    results["report_path"] = html_res["report_path"]
    for fmt in formats:
        if fmt == "html":
            continue
        ctx.progress(80 if fmt == "docx" else 90, f"writing {fmt}")
        export_args = {"format": fmt, "title": title, "subtitle": (f"Prepared by {args['author']} with Squipi" if args.get("author") else "Prepared with Squipi"),
                       "sections": sections, "sources": sources, "style": style}
        export_args["template_id"] = templates[fmt]["id"] if templates[fmt] else "none"
        if filename is not None:
            export_args["filename"] = filename
        r = export_document(ctx, export_args)
        results[f"{fmt}_path"] = r["path"]
        results["exports"][fmt] = {"charts_embedded": r["charts_embedded"], "charts_referenced_only": r["charts_referenced_only"], "template_applied": r["template_applied"]}
        if r["charts_referenced_only"]:
            results["warnings"].append(f"{fmt}: {len(r['charts_referenced_only'])} chart(s) could not be embedded and are referenced by path. The document is incomplete if embedded graphs were requested.")
    written_findings = sum(1 for sec in sections if sec.get('heading') in finding_headings)
    results["findings_written"] = written_findings
    results["summary_text"] = (f"Report '{title}': {len(ins['findings'])} findings computed and {written_findings} written in the document, {len(ins['data_quality'])} data-quality issues, {len(sections)} sections; "
                               + ", ".join(f for f in formats) + " written." + (f" Top finding: {ins['findings'][0]['title']}." if ins["findings"] else ""))
    if results["warnings"]:
        results["summary_text"] += " " + " ".join(results["warnings"])
    results["top_findings"] = [{"title": f["title"], "detail": f["detail"]} for f in ins["findings"][:5]]
    ctx.project.audit({"tool": "report_dataset", "dataset": ds.id, "formats": formats, "artifact": results["report_path"]})
    ctx.progress(100, "done")
    return results
