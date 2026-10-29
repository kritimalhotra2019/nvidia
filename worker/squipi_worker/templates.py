"""User-managed, local Office formatting references; never executable instructions.

Only the native host imports/renames/removes templates. Models see bounded metadata
and select an existing id when exporting. The user's original is never modified.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from io import BytesIO
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import uuid
from zipfile import ZipFile, BadZipFile

from lxml import etree

from .registry import InvalidArguments
from .security import AccessDenied

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_TEMPLATES = 100
ID_PATTERN = re.compile(r"tpl_[0-9a-f]{12}\Z")
LIMITATIONS = {
    "docx": "Reuses Word styles, headers, footers and final-section page settings. Original body text, tables and charts are replaced. Complex cover pages and multi-section layouts are not reproduced exactly; review branding in headers and footers.",
    "pptx": "Reuses slide size, themes, masters, layouts and sample title/body placeholder formatting. Original slides, notes and data are replaced. Arbitrary slide artwork, animations and complex arrangements are not recreated exactly; review branding on masters and layouts.",
}
TEMPLATE_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 64,
                      "description": "Saved template id from list_templates; omitted uses the saved default for this format. Use 'none' to explicitly disable a saved default. Templates supply formatting, never research facts."}


def _managed(project, name: str) -> Path:
    path = project.root / name
    if path.is_symlink() or path.resolve().parent != project.root.resolve():
        raise AccessDenied("The template library must be an ordinary directory or file inside this project.")
    return path


def _asset(project, record) -> Path:
    directory = _managed(project, "templates")
    path = directory / record["filename"]
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise AccessDenied("A saved template cannot point outside its managed library.")
    return path


@contextmanager
def _lock(project):
    lock = _managed(project, ".templates.lock")
    with lock.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120 or any(ord(c) < 32 for c in value):
        raise InvalidArguments("Template name must contain 1–120 characters without control characters.")
    return value.strip()


def _load(project):
    path = _managed(project, "templates.json")
    if not path.exists():
        return {"version": 1, "templates": [], "defaults": {}}
    try:
        if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("manifest is too large or is not a regular file")
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("templates"), list) or not isinstance(data.get("defaults"), dict):
            raise ValueError("invalid manifest structure")
        if len(data["templates"]) > MAX_TEMPLATES:
            raise ValueError("too many saved templates")
        seen = {}
        for item in data["templates"]:
            if not isinstance(item, dict) or not ID_PATTERN.fullmatch(str(item.get("id", ""))) or item["id"] in seen:
                raise ValueError("invalid template id")
            fmt = item.get("format")
            if fmt not in LIMITATIONS or item.get("filename") != item["id"] + "." + fmt:
                raise ValueError("invalid managed template filename")
            _name(item.get("name"))
            if not isinstance(item.get("summary"), str) or len(item["summary"]) > 1500:
                raise ValueError("invalid template summary")
            if isinstance(item.get("added"), bool) or not isinstance(item.get("added"), (float, int)) or not math.isfinite(item["added"]):
                raise ValueError("invalid template date")
            _asset(project, item)
            seen[item["id"]] = fmt
        if any(fmt not in LIMITATIONS or seen.get(tid) != fmt for fmt, tid in data["defaults"].items()):
            raise ValueError("invalid default template")
        return data
    except AccessDenied:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise InvalidArguments(f"Cannot read the saved template library: {exc}. Repair templates.json or restore its backup; it has not been overwritten.") from exc


def _save(project, data):
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise InvalidArguments("The template library metadata is full. Remove an unused template first.")
    path = _managed(project, "templates.json")
    temp = _managed(project, ".templates-" + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def validate_office(path: Path, fmt: str) -> str:
    """Validate the package before any Office parser sees it. Never extract ZIP paths."""
    if fmt not in LIMITATIONS or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise InvalidArguments("Import a regular .docx or .pptx file smaller than 50 MiB. Export legacy .doc/.ppt files to these editable formats first.")
    expected = "word/document.xml" if fmt == "docx" else "ppt/presentation.xml"
    main_type = "application/vnd.openxmlformats-officedocument." + ("wordprocessingml.document.main+xml" if fmt == "docx" else "presentationml.presentation.main+xml")
    try:
        with ZipFile(path) as package:
            infos = package.infolist()
            names = [i.filename for i in infos]
            if len(infos) > 5000 or len(set(names)) != len(names) or sum(i.file_size for i in infos) > 200 * 1024 * 1024:
                raise ValueError("duplicate entries or excessive expanded package size")
            for info in infos:
                name = info.filename
                parts = PurePosixPath(name).parts
                if not parts or name.startswith("/") or "\\" in name or ":" in name or any(p in {"..", "."} for p in parts) or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("unsafe or encrypted package entry")
                lower = name.lower()
                if any(token in lower for token in ("vbaproject", "activex/", "oleobject")):
                    raise ValueError("macros and active objects are not supported; save a copy without them first")
                if "/embeddings/" in lower and not info.is_dir():
                    # Ordinary Office charts carry an XLSX data package. It is
                    # never executed and is discarded with the old body/slide.
                    # OLE packages and nested active documents are not templates.
                    if not lower.endswith(".xlsx") or info.file_size > 20 * 1024 * 1024:
                        raise ValueError("only ordinary XLSX chart data is supported as an embedded object")
                    with ZipFile(BytesIO(package.read(info))) as workbook:
                        entries = workbook.infolist()
                        if len(entries) > 2000 or sum(e.file_size for e in entries) > 50 * 1024 * 1024 or "xl/workbook.xml" not in workbook.namelist():
                            raise ValueError("invalid or excessively large embedded chart workbook")
                        for entry in entries:
                            inner = entry.filename.lower()
                            if any(token in inner for token in ("vbaproject", "activex", "oleobject", "embeddings/", "externallinks/")):
                                raise ValueError("active or linked chart workbooks are not supported")
                        types = workbook.read("[Content_Types].xml").lower()
                        if b"macroenabled" in types or b"vbaproject" in types:
                            raise ValueError("macro-enabled chart workbooks are not supported")
                if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * 200:
                    raise ValueError("excessive ZIP compression ratio")
                if lower.endswith((".xml", ".rels")):
                    body = package.read(info)
                    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                        raise ValueError("XML entities are not supported")
                    tree = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True))
                    if lower.endswith(".rels"):
                        for rel in tree:
                            if rel.get("TargetMode") == "External" and not rel.get("Type", "").endswith("/hyperlink"):
                                raise ValueError("linked external resources are not supported; embed static pictures and remove linked templates first")
            if expected not in names or "[Content_Types].xml" not in names:
                raise ValueError("the file is not the requested Office format")
            content = etree.fromstring(package.read("[Content_Types].xml"))
            if not any(e.get("PartName") == "/" + expected and e.get("ContentType") == main_type for e in content):
                raise ValueError("macro-enabled, template-package or mismatched formats are unsupported; save as .docx or .pptx")
            if any("macroenabled" in e.get("ContentType", "").lower() for e in content):
                raise ValueError("macro-enabled package parts are not supported")
        if fmt == "docx":
            from docx import Document
            document = Document(path)
            section = document.sections[-1]
            summary = f"Word format · {section.page_width / 914400:.2f} × {section.page_height / 914400:.2f} in · {len(document.styles)} styles. "
        else:
            from pptx import Presentation
            deck = Presentation(path)
            if not len(deck.slide_masters) or not len(deck.slide_layouts):
                raise ValueError("presentation has no reusable slide master/layout")
            summary = f"PowerPoint format · {deck.slide_width / 914400:.2f} × {deck.slide_height / 914400:.2f} in · {sum(len(m.slide_layouts) for m in deck.slide_masters)} layouts. "
        return summary + LIMITATIONS[fmt]
    except (BadZipFile, etree.XMLSyntaxError, ValueError, KeyError, OSError) as exc:
        raise InvalidArguments(f"Cannot import this {fmt.upper()} template: {exc}") from exc


def library_action(project, params):
    if not isinstance(params, dict) or params.get("action", "list") not in {"list", "import", "update", "remove"}:
        raise InvalidArguments("Unknown template library action.")
    action = params.get("action", "list")
    if "make_default" in params and not isinstance(params["make_default"], bool):
        raise InvalidArguments("make_default must be true or false")
    with _lock(project):
        data = _load(project)
        if action == "list":
            return data
        if action == "import":
            if len(data["templates"]) >= MAX_TEMPLATES:
                raise InvalidArguments("You can save up to 100 templates. Remove an unused one first.")
            if not isinstance(params.get("path"), str) or not params["path"]:
                raise InvalidArguments("Choose a .docx or .pptx file to import.")
            source = project.allow.check_readable(params["path"])
            fmt = source.suffix.lower().removeprefix(".")
            summary = validate_office(source, fmt)
            item = {"id": "tpl_" + uuid.uuid4().hex[:12], "name": _name(params.get("name", source.stem)),
                    "format": fmt, "summary": summary, "added": time.time()}
            item["filename"] = item["id"] + "." + fmt
            directory = _managed(project, "templates")
            directory.mkdir(exist_ok=True)
            destination = _asset(project, item)
            # Copy bytes and validate the owned copy as well, so a changed source
            # between validation and copying cannot install an unchecked package.
            try:
                with source.open("rb") as reader, destination.open("xb") as writer:
                    payload = reader.read(MAX_FILE_BYTES + 1)
                    if len(payload) > MAX_FILE_BYTES:
                        raise InvalidArguments("Template became larger than 50 MiB during import.")
                    writer.write(payload)
                item["summary"] = validate_office(destination, fmt)
                data["templates"].append(item)
                if params.get("make_default") is True:
                    data["defaults"][fmt] = item["id"]
                _save(project, data)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        else:
            item = next((i for i in data["templates"] if i["id"] == params.get("id")), None)
            if item is None:
                raise InvalidArguments("That saved template no longer exists. Reload the template library.")
            if action == "remove":
                # Validate managed path before updating metadata; originals are
                # never in this directory and are never targets of deletion.
                path = _asset(project, item)
                data["templates"].remove(item)
                if data["defaults"].get(item["format"]) == item["id"]:
                    del data["defaults"][item["format"]]
                _save(project, data)
                path.unlink(missing_ok=True)
            else:
                if "name" in params:
                    item["name"] = _name(params["name"])
                if params.get("make_default") is True:
                    data["defaults"][item["format"]] = item["id"]
                elif params.get("make_default") is False and data["defaults"].get(item["format"]) == item["id"]:
                    del data["defaults"][item["format"]]
                _save(project, data)
        project.audit({"host_action": "templates_" + action, "template_id": item["id"], "format": item["format"]})
        return data


def resolve_template(project, fmt, template_id=None):
    if template_id == "none":
        return None
    with _lock(project):
        data = _load(project)
        if template_id is None:
            frozen = os.environ.get("SQUIPI_TEMPLATE_DEFAULTS")
            if frozen is not None:
                try:
                    defaults = json.loads(frozen)
                    if not isinstance(defaults, dict) or any(k not in LIMITATIONS or not isinstance(v, str) for k, v in defaults.items()):
                        raise ValueError("invalid format/id mapping")
                except (ValueError, TypeError) as exc:
                    raise InvalidArguments("The host's saved-template snapshot is invalid; restart this task.") from exc
                template_id = defaults.get(fmt, "none")
            else:
                template_id = data["defaults"].get(fmt)
        if not template_id or template_id == "none":
            return None
        item = next((i for i in data["templates"] if i["id"] == template_id), None)
        if item is None or item["format"] != fmt:
            raise InvalidArguments(f"Saved template '{template_id}' is missing or is not a {fmt.upper()} template. Use list_templates to choose one, or 'none' for standard formatting.")
        path = _asset(project, item)
        validate_office(path, fmt)
        return {**item, "path": path, "limitations": LIMITATIONS[fmt]}


def template_chart_palette(template):
    """Only literal color metadata, never text/instructions, from a selected format."""
    if template is None:
        return []
    colors = []
    with ZipFile(template["path"]) as package:
        if template["format"] == "docx":
            # A custom Word heading color is often the report's actual brand
            # accent while its retained Office theme still has stock colors.
            styles = etree.fromstring(package.read("word/styles.xml"), etree.XMLParser(resolve_entities=False, no_network=True))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            literal = styles.xpath("//w:style[@w:styleId='Heading1']/w:rPr/w:color[not(@w:themeColor)]/@w:val", namespaces=ns)
            if literal and re.fullmatch(r"[0-9a-fA-F]{6}", literal[0]):
                colors.append("#" + literal[0].upper())
        prefix = "word/theme/" if template["format"] == "docx" else "ppt/theme/"
        names = sorted(name for name in package.namelist() if name.startswith(prefix) and name.endswith(".xml"))
        if names:
            theme = etree.fromstring(package.read(names[0]), etree.XMLParser(resolve_entities=False, no_network=True))
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            for index in range(1, 7):
                elements = theme.xpath(f"//a:clrScheme/a:accent{index}/*", namespaces=ns)
                if not elements:
                    continue
                value = elements[0].get("val") if elements[0].tag.endswith("}srgbClr") else elements[0].get("lastClr")
                if value and re.fullmatch(r"[0-9a-fA-F]{6}", value):
                    color = "#" + value.upper()
                    if color not in colors:
                        colors.append(color)
    return colors[:6]
