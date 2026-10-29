"""Offline report boundaries, including real Matplotlib vector/raster SVG output."""
import html
import xml.etree.ElementTree as ET

import pytest

from squipi_worker.project import Project
from squipi_worker.registry import InvalidArguments, JobContext, registry
from squipi_worker.security import AllowList
from squipi_worker.tools.report import REPORT_CSP, _safe_chart_svg


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path / "project", AllowList())


def render(project, path):
    ctx = JobContext(project, lambda *args: None, lambda: False)
    return registry.call("render_report", {"title": "Synthetic report", "sections": [{"heading": "Chart", "chart_path": str(path)}]}, ctx)


@pytest.mark.parametrize("suffix,body", [
    (".md", "<script>alert('synthetic')</script>"),
    (".html", '<img src="https://example.invalid/pixel">'),
    (".svg", "<html><script>alert('synthetic')</script></html>"),
    (".svg", "not XML"),
])
def test_report_rejects_non_svg_content(project, suffix, body):
    path = project.root / ("source" + suffix)
    path.write_text(body)
    with pytest.raises(InvalidArguments):
        render(project, path)


@pytest.mark.parametrize("body", [
    '<script>alert("synthetic")</script>',
    '<foreignObject><div>unsafe HTML</div></foreignObject>',
    '<rect onload="alert(1)"/>',
    '<image href="https://example.invalid/pixel"/>',
    '<image href="file:///synthetic-private-file"/>',
    '<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="//example.invalid/chart.svg#x"/>',
    '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>',
    '<rect style="fill: url(https://example.invalid/color)"/>',
    '<rect fill="url(https://example.invalid/color"/>',
    '<style>@import "https://example.invalid/style.css";</style>',
    '<style>.x{fill:u/**/rl(https://example.invalid/color)}</style>',
    '<style>.x{fill:u\\72l(https://example.invalid/color)}</style>',
    '<style>.x{background:image-set("https://example.invalid/pixel" 1x)}</style>',
    '<style><g/>@import "https://example.invalid/style.css";</style>',
    '<image color-profile="url(https://example.invalid/profile)"/>',
    '<animate attributeName="href" values="https://example.invalid/pixel"/>',
    '<svg xml:base="https://example.invalid/"><use href="#x"/></svg>',
    '<metadata><script>alert(1)</script></metadata>',
    '<h:iframe xmlns:h="http://www.w3.org/1999/xhtml" src="https://example.invalid/"/>',
])
def test_report_rejects_active_or_external_svg(project, body):
    path = project.root / "unsafe.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg">' + body + '</svg>')
    with pytest.raises(InvalidArguments):
        render(project, path)


def test_report_rejects_outside_and_symlink_charts(project, tmp_path):
    outside = tmp_path / "outside.svg"
    outside.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    linked = project.root / "linked.svg"
    linked.symlink_to(outside)
    for path in (outside, linked):
        with pytest.raises(InvalidArguments):
            render(project, path)


def test_report_rejects_entity_expansion(project):
    path = project.root / "entities.svg"
    path.write_text('<!DOCTYPE svg [<!ENTITY a "synthetic">]><svg xmlns="http://www.w3.org/2000/svg"><text>&a;</text></svg>')
    with pytest.raises(InvalidArguments):
        render(project, path)


def test_matplotlib_svg_keeps_static_chart_and_internal_references(project):
    from matplotlib.figure import Figure
    fig = Figure(figsize=(5, 2))
    line, heatmap = fig.subplots(1, 2)
    line.plot([1, 2, 3], [2, 1, 4], marker="o")
    line.set_title("Synthetic vector")
    heatmap.imshow([[1, 2], [3, 4]])
    path = project.root / "matplotlib.svg"
    fig.savefig(path, format="svg")
    raw = path.read_text()
    assert "metadata" in raw and "data:image/png;base64" in raw
    sanitized = _safe_chart_svg(project, str(path))
    parsed = ET.fromstring(sanitized)
    assert parsed.tag == "{http://www.w3.org/2000/svg}svg"
    assert sanitized.startswith("<svg ")
    assert "url(#" in sanitized and "data:image/png;base64" in sanitized
    assert "<metadata" not in sanitized and "<!DOCTYPE" not in sanitized
    result = render(project, path)
    output = project.root / result["report_path"]
    report = html.unescape(output.read_text())
    assert 'http-equiv="Content-Security-Policy"' in report
    assert REPORT_CSP in report
    assert "script-src 'none'" in report and "img-src 'self' data:" in report


def test_tiny_svg_stays_supported_and_processing_instructions_are_removed(project):
    path = project.root / "tiny.svg"
    path.write_text('<?xml-stylesheet href="https://example.invalid/style.css"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4" fill="#333"/><text x="0" y="2">&lt;script&gt;</text></svg>')
    sanitized = _safe_chart_svg(project, str(path))
    assert "xml-stylesheet" not in sanitized and "example.invalid" not in sanitized
    assert "&lt;script&gt;" in sanitized
    render(project, path)
