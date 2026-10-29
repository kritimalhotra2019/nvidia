"""export_document: the same section structure render_report takes, written as a Word (.docx) or PowerPoint (.pptx) file.

Charts are the worker's SVGs; Office files need bitmaps, so on macOS they are rasterised with the system's
Quick Look tool (`qlmanage`). Where that is unavailable the chart is referenced by path instead of embedded."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unicodedata import category

from ..registry import JobContext, tool, InvalidArguments
from .document_style import STYLE_SCHEMA, normalize_style, contrast_text, heading_color
from ..templates import TEMPLATE_ID_SCHEMA, resolve_template

SECTION_SCHEMA = {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
    "heading": {"type": "string"},
    "text": {"type": ["string", "null"], "description": "paragraphs separated by blank lines; lines starting with '- ' become bullets"},
    "table": {"type": ["object", "null"], "properties": {"columns": {"type": "array", "items": {"type": "string"}}, "rows": {"type": "array", "maxItems": 200, "description": "Split longer tables into multiple sections; rows must never be silently truncated."}}},
    "chart_path": {"type": ["string", "null"], "description": "an SVG inside the project (from a tool result)"}}, "required": ["heading"]}}

OFFICE_FILENAME_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 120,
    "description": "Exact output filename, without a directory path. The selected .docx or .pptx extension is added if omitted; a different extension is rejected."}


def office_filename(filename: Optional[str], fmt: str) -> str:
    """Resolve only a basename inside the new artifact directory; never rename external files."""
    defaults = {"docx": "report.docx", "pptx": "slides.pptx"}
    if fmt not in defaults:
        raise InvalidArguments("Choose docx or pptx for an Office export")
    if filename is None:
        return defaults[fmt]
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 120
            or filename in {".", ".."} or Path(filename).name != filename
            or any(char in filename for char in "/\\:")
            or any(category(char) == "Cc" for char in filename)):
        raise InvalidArguments("filename must be a plain name of 1–120 characters, without a directory path or control characters")
    suffix = Path(filename).suffix
    if suffix and suffix.lower() != "." + fmt:
        raise InvalidArguments(f"filename must end in .{fmt}, or omit the extension")
    return filename if suffix else filename + "." + fmt


def _chart_file(ctx: JobContext, chart_path: str) -> Path:
    p = Path(chart_path)
    p = ctx.project.root / p if not p.is_absolute() else p
    try:
        p.resolve().relative_to(ctx.project.root.resolve())
    except ValueError:
        raise InvalidArguments(f"chart_path must be inside the project: {chart_path}")
    if not p.exists():
        raise InvalidArguments(f"chart not found: {chart_path}")
    return p


def svg_to_png(svg_path: Path, out_dir: Path, width: int = 1400) -> Optional[Path]:
    """A PNG written next to the SVG (matplotlib charts) is used as is; otherwise rasterise with Quick Look on macOS.
    Returns None when no rasteriser is available."""
    sibling = svg_path.with_suffix(".png")
    if sibling.exists() and sibling.stat().st_size > 0:
        return sibling
    if sys.platform != "darwin" or not shutil.which("qlmanage"):
        return None
    try:
        subprocess.run(["qlmanage", "-t", "-s", str(width), "-o", str(out_dir), str(svg_path)], check=True, capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    png = out_dir / (svg_path.name + ".png")
    return png if png.exists() and png.stat().st_size > 0 else None


def _cell(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        from .. import svg
        return svg._fmt(v)
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _paragraphs(text: str) -> List[Dict[str, Any]]:
    blocks = []
    for para in str(text).split("\n\n"):
        lines = [l for l in para.split("\n") if l.strip()]
        if lines and all(l.strip().startswith(("- ", "• ")) for l in lines):
            blocks.append({"bullets": [l.strip()[2:].strip() for l in lines]})
        elif lines:
            blocks.append({"text": " ".join(l.strip() for l in lines)})
    return blocks


def _text_width_points(text: str, font_size: float, *, bold=False) -> float:
    """Conservative font-independent estimate, used only to reserve header space."""
    narrow, wide = set("ilI.,:;!'|"), set("MWmw@%")
    units = sum(.28 if char in narrow else .9 if char in wide else .64 if char.isupper() or char.isdigit() else .56 for char in text)
    return units * font_size * (1.08 if bold else 1)


def _docx_header_minimums(columns: List[str], font_size: float) -> List[int]:
    # Header words may wrap at spaces, but should never need mid-word breaks.
    return [int((max((_text_width_points(word, font_size, bold=True) for word in str(column).split()), default=0) + 13) * 12700) for column in columns]


def _docx_column_widths(columns: List[str], rows: List[Any], total: int, font_size: float = 9.5) -> List[int]:
    """Reserve readable label/summary space rather than equal-width report cells."""
    names = [str(column).strip().lower() for column in columns]
    count = len(names)
    if count == 1:
        return [total]
    if names[0] == "variable" and count >= 5:
        first = min(1828800, int(total * .4))  # two inches for long source variable names
        weights = [first] + [(total - first) / (count - 1)] * (count - 1)
    elif names == ["column", "role", "missing", "summary"]:
        weights = [2, 1.05, .65, 2.3]
    elif names[0] == "category" and count == 3:
        weights = [3.4, .9, 1.7]
    elif names == ["measure", "value"]:
        weights = [4, 2]
    else:
        weights = []
        for index, name in enumerate(names):
            values = [_cell(row[index]) for row in rows[:200] if index < len(row)]
            longest = max([len(name), *(min(len(value), 70) for value in values)])
            weights.append(max(8, longest) ** .5)
    minimums = _docx_header_minimums(columns, font_size)
    baseline = [int(total * weight / sum(weights)) for weight in weights]
    if all(width >= minimum for width, minimum in zip(baseline, minimums)):
        baseline[-1] += total - sum(baseline)
        return baseline
    if names[0] == "variable" and count >= 5:
        minimums[0] = max(minimums[0], min(1188720, int(total * .27)))
    remaining = max(0, total - sum(minimums))
    widths = ([minimum + int(remaining * weight / sum(weights)) for minimum, weight in zip(minimums, weights)]
              if sum(minimums) <= total else [int(total * minimum / sum(minimums)) for minimum in minimums])
    widths[-1] += total - sum(widths)
    return widths


def _docx_table(document, columns: List[str], rows: List[Any], presentation: Dict[str, Any], *, template_style=None, use_template=False):
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu, Pt, RGBColor

    section = document.sections[-1]
    total = int(section.page_width - section.left_margin - section.right_margin)
    normal_size = document.styles["Normal"].font.size
    font_size = min(presentation["font_size"], 14) if "font_size" in presentation else ((normal_size.pt if normal_size else 11) if use_template else (9.5 if len(columns) >= 5 else 10))
    # Dense tables may use a smaller size than the report body, matching the
    # existing exporter contract. Never shrink the document's template styles.
    minimums = _docx_header_minimums(columns, font_size)
    if sum(minimums) > total:
        font_size = max(9, min(font_size, font_size * total / sum(minimums)))
    widths = _docx_column_widths(columns, rows, total, font_size)
    table = document.add_table(rows=1, cols=len(columns))
    if template_style is not None:
        table.style = template_style
    elif not use_template:
        table.style = "Table Grid"
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for column, width in zip(table.columns, widths):
        column.width = Emu(width)
    properties = table._tbl.tblPr
    for existing in list(properties):
        if existing.tag in {qn("w:tblBorders"), qn("w:tblCellMar")}:
            properties.remove(existing)
    borders = OxmlElement("w:tblBorders")
    for side in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        border = OxmlElement("w:" + side)
        for key, value in {"val": "single", "sz": "4", "color": "D9D9D9"}.items():
            border.set(qn("w:" + key), value)
        borders.append(border)
    if not use_template:
        properties.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for side, value in [("top", "80"), ("bottom", "80"), ("left", "100"), ("right", "100")]:
        margin = OxmlElement("w:" + side)
        margin.set(qn("w:w"), value)
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    properties.append(margins)

    def numeric(value):
        if value is None or str(value).strip() in {"", "—"}:
            return True
        try:
            float(str(value).replace(",", "").removesuffix("%"))
            return True
        except ValueError:
            return False

    numeric_columns = [bool(rows) and all(index < len(row) and numeric(row[index]) for row in rows[:200]) for index in range(len(columns))]
    for index, column in enumerate(columns):
        table.rows[0].cells[index].text = str(column)
    for values in rows[:200]:
        cells = table.add_row().cells
        for index, value in enumerate(list(values)[:len(columns)]):
            cells[index].text = _cell(value)
    for row_index, row in enumerate(table.rows):
        row_properties = row._tr.get_or_add_trPr()
        row_properties.append(OxmlElement("w:cantSplit"))
        if row_index == 0:
            row_properties.append(OxmlElement("w:tblHeader"))
        for column_index, cell in enumerate(row.cells):
            cell.width = Emu(widths[column_index])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if row_index == 0 and (not use_template or "accent_color" in presentation):
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), presentation.get("accent_color", "#F2F2F2")[1:])
                shade.set(qn("w:val"), "clear")
                cell._tc.get_or_add_tcPr().append(shade)
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if numeric_columns[column_index] else WD_ALIGN_PARAGRAPH.LEFT
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = 1.08
                paragraph.paragraph_format.keep_with_next = row_index == 0
                for run in paragraph.runs:
                    run.font.size = Pt(font_size)
                    run.font.bold = row_index == 0
                    text_color = (contrast_text(presentation["accent_color"]) if row_index == 0 and "accent_color" in presentation
                                  else presentation.get("body_color", "#000000"))
                    if not use_template or "body_color" in presentation or (row_index == 0 and "accent_color" in presentation):
                        run.font.color.rgb = RGBColor.from_string(text_color[1:])
                    if "font_family" in presentation:
                        run.font.name = presentation["font_family"]
    return table


def _clear_source_metadata(package, properties):
    for name in ("author", "category", "comments", "content_status", "identifier", "keywords", "language", "last_modified_by", "subject", "title"):
        setattr(properties, name, "")
    relationships = package.rels if hasattr(package, "rels") else package._rels
    for rid, rel in list(relationships.items()):
        if not rel.reltype.endswith(("/officeDocument", "/metadata/core-properties")):
            relationships.pop(rid)


def _word_template(path):
    from copy import deepcopy
    import docx
    from docx.oxml.ns import qn
    document = docx.Document(path)
    table_style = document.tables[0].style if document.tables else None
    sections = list(document.sections)
    final = sections[-1]._sectPr
    # An inherited header/footer still needs its relationship after the earlier
    # section breaks are removed. Preserve the effective final-section branding.
    for tag in ("headerReference", "footerReference"):
        for kind in ("default", "first", "even"):
            if any(e.get(qn("w:type")) == kind for e in final.findall(qn("w:" + tag))):
                continue
            for section in reversed(sections[:-1]):
                ref = next((e for e in section._sectPr.findall(qn("w:" + tag)) if e.get(qn("w:type")) == kind), None)
                if ref is not None:
                    final.insert(0, deepcopy(ref))
                    break
    body = document._element.body
    for element in list(body):
        if element is not final:
            body.remove(element)
    retained = {e.get(qn("r:id")) for e in final.iter() if e.get(qn("r:id"))}
    safe_types = {"styles", "settings", "numbering", "fontTable", "theme", "webSettings"}
    for rid, rel in list(document.part.rels.items()):
        if rid not in retained and rel.reltype.rsplit("/", 1)[-1] not in safe_types:
            document.part.drop_rel(rid)
    _clear_source_metadata(document.part.package, document.core_properties)
    return document, table_style


def _write_docx(ctx: JobContext, args: Dict[str, Any], out: Path, pngs: Dict[str, Optional[Path]]) -> Path:
    import docx
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Inches, Pt, RGBColor
    template = args.get("_template")
    d, template_table_style = _word_template(template["path"]) if template else (docx.Document(), None)
    presentation = args.get("style") or {}
    style = d.styles["Normal"]
    if not template or "font_family" in presentation:
        style.font.name = presentation.get("font_family", "Calibri")
    if not template or "font_size" in presentation:
        style.font.size = Pt(presentation.get("font_size", 11))
    if "body_color" in presentation:
        style.font.color.rgb = RGBColor.from_string(presentation["body_color"][1:])
    if not template:
        style.paragraph_format.space_after = Pt(8)
    for name in ["Title", "Subtitle", "Header", *[f"Heading {level}" for level in range(1, 10)]]:
        if name not in d.styles:
            continue
        heading_style = d.styles[name]
        if not template or "heading_color" in presentation or "accent_color" in presentation:
            heading_style.font.color.rgb = RGBColor.from_string(heading_color(presentation)[1:])
        if "font_family" in presentation:
            heading_style.font.name = presentation["font_family"]
        color = heading_style.element.find("w:rPr/w:color", heading_style.element.nsmap)
        if color is not None and (not template or "heading_color" in presentation or "accent_color" in presentation):
            for attribute in ["themeColor", "themeTint", "themeShade"]:
                color.attrib.pop(qn("w:" + attribute), None)
        # The bundled Word template has a blue rule on Title; removing its
        # paragraph border is necessary in Word and LibreOffice, not just HTML.
        if not template:
            for border in heading_style.element.findall("w:pPr/w:pBdr", heading_style.element.nsmap):
                border.getparent().remove(border)
    if not template:
        d.styles["Title"].font.size = Pt(26)
        d.styles["Heading 1"].font.size = Pt(14)
    if "font_size" in presentation:
        d.styles["Title"].font.size = Pt(min(48, presentation["font_size"] * 2.2))
        d.styles["Heading 1"].font.size = Pt(min(40, presentation["font_size"] * 1.3))
    if "background_color" in presentation:
        for existing in d._element.findall(qn("w:background")):
            d._element.remove(existing)
        background = OxmlElement("w:background")
        background.set(qn("w:color"), presentation["background_color"][1:])
        d._element.insert(0, background)
        display = OxmlElement("w:displayBackgroundShape")
        d.settings.element.append(display)
    if not template:
        d.styles["Heading 1"].paragraph_format.space_before = Pt(14)
        d.styles["Heading 1"].paragraph_format.space_after = Pt(6)
    d.add_heading(args["title"], level=0)
    if args.get("subtitle"):
        d.add_paragraph(args["subtitle"], style="Subtitle").runs[0].italic = True
    for s in args["sections"]:
        if s["heading"].strip():
            d.add_heading(s["heading"], level=1)
        for b in _paragraphs(s.get("text") or ""):
            if "bullets" in b:
                for item in b["bullets"]:
                    d.add_paragraph(item, style="List Bullet")
            else:
                d.add_paragraph(b["text"])
        if s.get("table"):
            cols = s["table"].get("columns", [])
            rows = s["table"].get("rows", [])
            if cols:
                d.paragraphs[-1].paragraph_format.keep_with_next = True
                _docx_table(d, cols, rows, presentation, template_style=template_table_style, use_template=bool(template))
        if s.get("chart_path"):
            png = pngs.get(s["chart_path"])
            if png:
                if not s.get("table"):
                    d.paragraphs[-1].paragraph_format.keep_with_next = True
                section = d.sections[-1]
                d.add_picture(str(png), width=min(Inches(6.0), section.page_width - section.left_margin - section.right_margin))
                if s.get("table"):
                    d.paragraphs[-1].paragraph_format.space_before = Pt(8)
            else:
                d.add_paragraph(f"Chart: {s['chart_path']}").runs[0].italic = True
    if args.get("sources"):
        d.add_heading("Sources", level=1)
        for src in args["sources"]:
            d.add_paragraph(str(src), style="List Bullet")
    note = d.add_paragraph("Written by Squipi from tool results on this Mac. Check the numbers against the underlying outputs before sharing.")
    note.paragraph_format.space_before = Pt(8)
    note.runs[0].italic = True
    path = out / office_filename(args.get("filename"), "docx")
    d.save(str(path))
    return path


def _pptx_template(path):
    from copy import deepcopy
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER
    prs = Presentation(path)
    body_types = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.VERTICAL_BODY}
    layouts = [layout for master in prs.slide_masters for layout in master.slide_layouts]
    samples = list(prs.slides)
    title_sample = samples[0] if samples else None
    content_sample = next((slide for slide in samples if slide.shapes.title is not None and any(p.placeholder_format.type in body_types for p in slide.placeholders)), None)
    title_layout = title_sample.slide_layout if title_sample else layouts[0]
    content_layout = content_sample.slide_layout if content_sample else next((layout for layout in layouts if any(p.placeholder_format.type in body_types for p in layout.placeholders) and any(p.placeholder_format.type in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE} for p in layout.placeholders)), title_layout)

    def formatting(slide):
        if slide is None:
            return {}
        result = {}
        for shape in slide.placeholders:
            if not shape.has_text_frame:
                continue
            paragraph = shape.text_frame.paragraphs[0]
            result[shape.placeholder_format.idx] = {
                "geometry": (shape.left, shape.top, shape.width, shape.height),
                "body": deepcopy(shape.text_frame._txBody.bodyPr),
                "list": deepcopy(shape.text_frame._txBody.find("{http://schemas.openxmlformats.org/drawingml/2006/main}lstStyle")),
                "paragraph": deepcopy(paragraph._p.pPr),
                "run": deepcopy(paragraph.runs[0]._r.rPr) if paragraph.runs else None,
            }
        background = slide._element.cSld.find("{http://schemas.openxmlformats.org/presentationml/2006/main}bg")
        # Picture backgrounds belong to a slide relationship, so keep only
        # relationship-free fills. Master/layout artwork stays in its own part.
        if background is not None and not any("relationships}" in key for e in background.iter() for key in e.attrib):
            result["background"] = deepcopy(background)
        return result

    title_format, content_format = formatting(title_sample), formatting(content_sample)
    for slide_id in list(prs.slides._sldIdLst):
        prs.part.drop_rel(slide_id.rId)
        prs.slides._sldIdLst.remove(slide_id)
    safe_types = {"slideMaster", "notesMaster", "handoutMaster", "presProps", "viewProps", "tableStyles", "theme"}
    for rid, rel in list(prs.part.rels.items()):
        if rel.reltype.rsplit("/", 1)[-1] not in safe_types:
            prs.part.drop_rel(rid)
    # Custom slide shows and sections can contain old source titles/slide ids.
    for child in list(prs._element):
        if child.tag.rsplit("}", 1)[-1] in {"custShowLst", "extLst"}:
            prs._element.remove(child)
    _clear_source_metadata(prs.part.package, prs.core_properties)
    return prs, title_layout, content_layout, title_format, content_format


def _apply_placeholder_format(slide, formats):
    from copy import deepcopy
    from pptx.oxml.xmlchemy import OxmlElement
    from pptx.oxml.ns import qn
    for placeholder in slide.placeholders:
        item = formats.get(placeholder.placeholder_format.idx)
        if not item or not placeholder.has_text_frame:
            continue
        placeholder.left, placeholder.top, placeholder.width, placeholder.height = item["geometry"]
        body = placeholder.text_frame._txBody
        for tag, key in (("a:bodyPr", "body"), ("a:lstStyle", "list")):
            old = body.find(qn(tag))
            if old is not None:
                body.remove(old)
            if item[key] is not None:
                body.insert(0 if key == "body" else 1, deepcopy(item[key]))
        paragraph = placeholder.text_frame.paragraphs[0]
        if paragraph._p.pPr is not None:
            paragraph._p.remove(paragraph._p.pPr)
        if item["paragraph"] is not None:
            paragraph._p.insert(0, deepcopy(item["paragraph"]))
        if item["run"] is not None:
            properties = paragraph._p.get_or_add_pPr()
            old = properties.find(qn("a:defRPr"))
            if old is not None:
                properties.remove(old)
            default = deepcopy(item["run"])
            default.tag = qn("a:defRPr")
            # Links on old example text do not belong on newly generated text.
            for child in list(default):
                if child.tag in {qn("a:hlinkClick"), qn("a:hlinkMouseOver")}:
                    default.remove(child)
            properties.append(default)
    if formats.get("background") is not None:
        slide._element.cSld.insert(0, deepcopy(formats["background"]))


def _write_pptx(ctx: JobContext, args: Dict[str, Any], out: Path, pngs: Dict[str, Optional[Path]]) -> Path:
    from copy import deepcopy
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import PP_PLACEHOLDER
    presentation = args.get("style") or {}
    template = args.get("_template")
    if template:
        prs, title_layout, content_layout, title_format, content_format = _pptx_template(template["path"])
    else:
        prs = Presentation()
        prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
        title_layout, content_layout = prs.slide_layouts[0], prs.slide_layouts[5]
        title_format = content_format = {}
    width, height = prs.slide_width, prs.slide_height
    margin, gutter = int(width * .04), int(width * .025)
    body_types = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.VERTICAL_BODY}

    def new_slide(layout, formats, title):
        slide = prs.slides.add_slide(layout)
        _apply_placeholder_format(slide, formats)
        for shape in slide.placeholders:
            if shape.has_text_frame:
                shape.text_frame.clear()
        shape = slide.shapes.title
        if shape is None:
            shape = slide.shapes.add_textbox(margin, int(height * .06), width - margin * 2, int(height * .14))
            shape.text_frame.paragraphs[0].font.size = Pt(28)
        shape.text_frame.paragraphs[0].text = title
        return slide

    title_slide = new_slide(title_layout, title_format, args["title"])
    subtitle = next((p for p in title_slide.placeholders if p.placeholder_format.type == PP_PLACEHOLDER.SUBTITLE and p.has_text_frame), None)
    if subtitle is None and args.get("subtitle"):
        subtitle = title_slide.shapes.add_textbox(margin, int(height * .55), width - margin * 2, int(height * .2))
    if subtitle is not None:
        subtitle.text_frame.paragraphs[0].text = args.get("subtitle") or "Prepared with Squipi"

    def fill_text(shape, blocks, font_size=16):
        from pptx.oxml.xmlchemy import OxmlElement
        from pptx.oxml.ns import qn
        tf = shape.text_frame
        tf.word_wrap = True
        first = True
        base_properties = deepcopy(tf.paragraphs[0]._p.pPr)
        for block in blocks:
            for item in block.get("bullets") or [block.get("text", "")]:
                paragraph = tf.paragraphs[0] if first else tf.add_paragraph()
                first = False
                if base_properties is not None and paragraph._p.pPr is None:
                    paragraph._p.insert(0, deepcopy(base_properties))
                paragraph.text = item
                # Native bullets prevent a literal dot from doubling a bullet
                # already inherited from the template's content placeholder.
                properties = paragraph._p.get_or_add_pPr()
                for child in list(properties):
                    if child.tag in {qn("a:buNone"), qn("a:buChar"), qn("a:buAutoNum")}:
                        properties.remove(child)
                marker = OxmlElement("a:buChar" if "bullets" in block else "a:buNone")
                if "bullets" in block:
                    marker.set("char", "•")
                default = properties.find(qn("a:defRPr"))
                properties.insert(list(properties).index(default) if default is not None else len(properties), marker)
                if not template:
                    paragraph.font.size = Pt(font_size)

    for section in args["sections"]:
        has_chart = bool(section.get("chart_path") and pngs.get(section["chart_path"]))
        slide = new_slide(content_layout, content_format, section["heading"] or "Research findings")
        body = next((p for p in slide.placeholders if p.placeholder_format.type in body_types and p.has_text_frame), None)
        left, top, area_width, area_height = (body.left, body.top, body.width, body.height) if body is not None else (margin, int(height * .22), width - margin * 2, int(height * .70))
        # A slide template may use a narrow content placeholder; stay inside it.
        area_width = min(area_width, width - left - margin)
        area_height = min(area_height, height - top - int(height * .06))
        left_width = int((area_width - gutter) * .48) if has_chart else area_width
        blocks = _paragraphs(section.get("text") or "")
        table_data = section.get("table") or {}
        columns = table_data.get("columns", [])
        stacked_chart = bool(has_chart and not columns and width / height < 1.5 and len(section.get("text") or "") <= 300)
        text_height = 0
        if stacked_chart:
            import math
            left_width = area_width
            point_size = body.text_frame.paragraphs[0].font.size.pt if body is not None and body.text_frame.paragraphs[0].font.size else 19
            lines = max(1, math.ceil(len(section.get("text") or "") / max(25, area_width / 914400 * 7)))
            text_height = min(int(area_height * .4), int((lines * point_size * 1.3 + 9) * 12700)) if blocks else 0
        if blocks:
            body = body if body is not None else slide.shapes.add_textbox(left, top, left_width, area_height)
            body.width = left_width
            body.height = text_height if stacked_chart else (int(area_height * .43) if columns else area_height)
            fill_text(body, blocks)
        elif body is not None:
            slide.shapes._spTree.remove(body._element)
        if columns:
            rows = table_data.get("rows", [])[:12]
            table_top = top + (int(area_height * .47) if blocks else 0)
            table_height = min(area_height - (table_top - top), int(height * .055) * (len(rows) + 1))
            shape = slide.shapes.add_table(len(rows) + 1, len(columns), left, table_top, left_width, table_height)
            table = shape.table
            for i, column in enumerate(columns):
                table.cell(0, i).text = str(column)
            for r_i, row in enumerate(rows, start=1):
                for c_i, value in enumerate(list(row)[:len(columns)]):
                    table.cell(r_i, c_i).text = _cell(value)
            for row in table.rows:
                for cell in row.cells:
                    cell.text_frame.paragraphs[0].font.size = Pt(12)
        if has_chart:
            from PIL import Image
            image_path = pngs[section["chart_path"]]
            with Image.open(image_path) as img:
                aspect = img.width / img.height
            chart_width = area_width if stacked_chart else area_width - left_width - gutter
            available_height = area_height - text_height - (gutter if blocks else 0) if stacked_chart else area_height
            chart_height = min(available_height, int(chart_width / aspect))
            chart_width = min(chart_width, int(chart_height * aspect))
            chart_left = left + int((area_width - chart_width) / 2) if stacked_chart else left + left_width + gutter
            chart_top = top + text_height + (gutter if blocks else 0) if stacked_chart else top
            slide.shapes.add_picture(str(image_path), chart_left, chart_top, width=chart_width, height=chart_height)
        elif section.get("chart_path"):
            box = slide.shapes.add_textbox(left, top + area_height - int(height * .1), area_width, int(height * .1))
            box.text_frame.text = f"Chart: {section['chart_path']}"
    if args.get("sources"):
        slide = new_slide(content_layout, content_format, "Sources")
        body = next((p for p in slide.placeholders if p.placeholder_format.type in body_types and p.has_text_frame), None)
        if body is None:
            body = slide.shapes.add_textbox(margin, int(height * .22), width - margin * 2, int(height * .70))
        fill_text(body, [{"bullets": [str(source) for source in args["sources"]]}], font_size=14)
    if presentation:
        for slide in prs.slides:
            if "background_color" in presentation:
                slide.background.fill.solid()
                slide.background.fill.fore_color.rgb = RGBColor.from_string(presentation["background_color"][1:])
            for shape in slide.shapes:
                if shape.has_text_frame:
                    is_title = shape == slide.shapes.title
                    for paragraph in shape.text_frame.paragraphs:
                        fonts = [paragraph.font, *(run.font for run in paragraph.runs)]
                        for font in fonts:
                            if "font_family" in presentation:
                                font.name = presentation["font_family"]
                            if "font_size" in presentation:
                                font.size = Pt(min(48, presentation["font_size"] * 1.6) if is_title else presentation["font_size"])
                            color = heading_color(presentation, "") if is_title else presentation.get("body_color", "")
                            if color:
                                font.color.rgb = RGBColor.from_string(color[1:])
                elif shape.has_table:
                    for row_index, row in enumerate(shape.table.rows):
                        for cell in row.cells:
                            if row_index == 0 and "accent_color" in presentation:
                                cell.fill.solid()
                                cell.fill.fore_color.rgb = RGBColor.from_string(presentation["accent_color"][1:])
                            elif "background_color" in presentation:
                                cell.fill.solid()
                                cell.fill.fore_color.rgb = RGBColor.from_string(presentation["background_color"][1:])
                            color = (contrast_text(presentation["accent_color"]) if row_index == 0 and "accent_color" in presentation
                                     else presentation.get("body_color", ""))
                            for paragraph in cell.text_frame.paragraphs:
                                for font in [paragraph.font, *(run.font for run in paragraph.runs)]:
                                    if "font_family" in presentation:
                                        font.name = presentation["font_family"]
                                    if "font_size" in presentation:
                                        font.size = Pt(min(presentation["font_size"], 16))
                                    if color:
                                        font.color.rgb = RGBColor.from_string(color[1:])
    path = out / office_filename(args.get("filename"), "pptx")
    prs.save(str(path))
    return path


@tool("export_document",
      "Write a Word (.docx) or PowerPoint (.pptx) file from sections you supply (heading, text, table, chart_path from tool results). "
      "Numbers must come from tool results. Charts are embedded as images where the system can rasterise them.",
      {"type": "object", "properties": {
          "format": {"type": "string", "enum": ["docx", "pptx"]},
          "title": {"type": "string", "minLength": 1},
          "filename": OFFICE_FILENAME_SCHEMA,
          "subtitle": {"type": ["string", "null"]},
          "sections": SECTION_SCHEMA,
          "style": STYLE_SCHEMA,
          "template_id": TEMPLATE_ID_SCHEMA,
          "sources": {"type": "array", "items": {"type": "string"}}},
       "required": ["format", "title", "sections"], "additionalProperties": False})
def export_document(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    if any(len((s.get('table') or {}).get('rows',[]))>200 for s in args['sections']):
        raise InvalidArguments('A table exceeds 200 rows. Split it into multiple sections; no rows were silently omitted.')
    args = {**args, "style": normalize_style(args.get("style"))}
    fmt = args["format"]
    args["filename"] = office_filename(args.get("filename"), fmt)
    template = resolve_template(ctx.project, fmt, args.get("template_id"))
    args["_template"] = template
    # Title-only dividers are valid; entirely empty model sections are not.
    args["sections"] = [section for section in args["sections"] if section["heading"].strip() or (section.get("text") or "").strip() or (section.get("table") or {}).get("columns") or section.get("chart_path")]
    if not args["sections"]:
        raise InvalidArguments("The report has no section content. Supply a heading, analysis, table or chart; empty slides are not exported.")
    out = ctx.project.new_artifact_dir("export", args["title"])
    ctx.progress(10, "charts")
    pngs: Dict[str, Optional[Path]] = {}
    png_dir = out / "png"
    png_dir.mkdir(exist_ok=True)
    for s in args["sections"]:
        ctx.check()
        if s.get("chart_path") and s["chart_path"] not in pngs:
            svg_file = _chart_file(ctx, s["chart_path"])
            pngs[s["chart_path"]] = svg_to_png(svg_file, png_dir)
    ctx.progress(50, "writing")
    path = _write_docx(ctx, args, out, pngs) if fmt == "docx" else _write_pptx(ctx, args, out, pngs)
    embedded = sum(1 for v in pngs.values() if v)
    missing = [k for k, v in pngs.items() if not v]
    verified = None
    if fmt == "docx":
        # Count what the saved document actually contains, not what we intended to add.
        try:
            from docx import Document as _Document
            verified = len(_Document(str(path)).inline_shapes)
            if verified < embedded:
                missing = missing + [f"{embedded - verified} chart(s) intended but not present as inline images in {path.name}"]
                embedded = verified
        except Exception:  # noqa: BLE001 - verification is advisory; the export itself succeeded
            verified = None
    ctx.project.audit({"tool": "export_document", "format": fmt, "artifact": str(path)})
    ctx.progress(100, "done")
    return {"path": str(path), "format": fmt, "sections": len(args["sections"]), "charts_embedded": embedded, "charts_referenced_only": missing, "charts_verified_in_file": verified,
            "size_bytes": path.stat().st_size, "artifact_dir": str(out), "style_applied": args["style"],
            "template_applied": ({key: template[key] for key in ("id", "name", "format", "limitations")} if template else None),
            "summary_text": f"Wrote {path.name}: {len(args['sections'])} sections" + (f", {embedded} charts embedded" if embedded else "") + (f", {len(missing)} charts could not be rasterised and are referenced by path" if missing else "") + "."}
