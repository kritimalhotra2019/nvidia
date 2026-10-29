#!/usr/bin/env python3
"""Verify local instruction files reach the real Agent and style real exports.

The model is a controlled loopback fixture, not an inference-quality test. It
checks the exact context presented to both native and structured Ollama calls,
then selects ordinary worker actions. The real worker writes DOCX and PPTX;
their OOXML and chart output must reflect the selected style. User settings and
research data remain isolated. An existing worker environment is reused.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from http.server import ThreadingHTTPServer

from test_ollama_agent_integration import handler_for, read_steps


GOAL = "Create a Word and PowerPoint descriptive report with labelled charts using synthetic-households.csv."
ANSWER = "Keep all columns except respondent_name, phone and email. Proceed with the report."
QUESTION = "Should I remove personal information before creating the report? Which columns should I exclude?"
OVERRIDE = "For this request only, override my usual blue with green #18794E for headings, accents and charts."
PERSONALITY_MARKER = "USER_PERSONALITY_FIXTURE_024"
MEMORY_MARKER = "USER_MEMORY_FIXTURE_021"
SKILL_MARKER = "USER_SKILL_FIXTURE_021"
CHANGED_MARKER = "MID_TURN_CHANGE_MUST_WAIT_UNTIL_NEXT_REQUEST"
MEMORY = f"""# Personal memory
{MEMORY_MARKER}
I prefer blue #2457A6 headings and accents for Word reports and PowerPoint slides.
Use a white #FFFFFF background and dark #222222 body text.
"""
SKILL = f"""# Report design
{SKILL_MARKER}
For Word and PowerPoint, use Arial at 12 pt, clear section headings, labelled
charts and a short caption explaining the units. Use my blue #2457A6 as the
first chart colour, followed by #7A5195. Current chat instructions override
these defaults. Keep charts based on observed tool results.
"""
DROP = ["respondent_name", "phone", "email"]
KEEP = ["household_id", "age", "gender", "income", "schooling", "wellbeing"]


class InstructionFixture:
    def __init__(self, output: Path, *, native_first: bool, override: bool):
        self.output = output
        self.project = output / "app-support/projects/default"
        self.instructions = output / "app-support/Instructions"
        self.native_first = native_first
        self.override = override
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self.normal_calls = 0
        self.action_calls = 0
        color = "#18794E" if override else "#2457A6"
        self.style = {"accent_color": color, "heading_color": color,
                      "body_color": "#222222", "background_color": "#FFFFFF",
                      "font_family": "Arial", "font_size": 12,
                      "chart_palette": [color, "#7A5195"]}

    def action(self, request: dict) -> str:
        self.requests.append(request)
        messages = "\n".join(message.get("content", "") for message in request["messages"])
        assert PERSONALITY_MARKER in messages and "warm, funny and direct" in messages, "Identity must survive both native decisions and structured recovery"
        assert MEMORY_MARKER in messages and SKILL_MARKER in messages, "Local memory and skill must reach every model decision"
        assert "#2457A6" in messages and "Arial" in messages, "The source preferences were lost"
        assert CHANGED_MARKER not in messages, "Instruction files must be snapshotted once, not reread partway through a turn"
        assert GOAL in messages and ANSWER in messages, "Original objective and user answers must survive"
        assert (OVERRIDE in messages) == self.override, "The current request must preserve its style override"
        if len(self.requests) == 1:
            # Editing this local file during the first request must affect only
            # a subsequent turn, so the cloud-preview/context scope is stable.
            (self.instructions / "memory.md").write_text(MEMORY + "\n" + CHANGED_MARKER + "\nUse red #FF0000 now.\n")
            (self.instructions / "personality.md").write_text(CHANGED_MARKER + " identity edited during turn")
        if "format" not in request:
            self.normal_calls += 1
            assert self.native_first and self.normal_calls == 1
            assert request.get("tools"), "Fresh requests must offer the actual tool schemas"
            return QUESTION
        self.action_calls += 1
        assert self.normal_calls == int(self.native_first)
        assert self.action_calls <= 3, "Do not loop after successful tools"
        assert "tools" not in request and request.get("think") is False
        assert QUESTION not in messages, "Structured recovery must exclude the stale assistant question"
        names = request["format"]["oneOf"][0]["properties"]["tool"]["enum"]
        assert {"clean_dataset", "report_dataset"}.issubset(names)
        datasets = json.loads((self.project / "datasets.json").read_text())["datasets"]
        source = next(dataset for dataset in datasets if not dataset.get("derived_from"))
        steps = read_steps(self.project)
        clean = [step for step in steps if step["tool"] == "clean_dataset" and step["ok"]]
        report = [step for step in steps if step["tool"] == "report_dataset" and step["ok"]]
        if not clean:
            return json.dumps({"tool": "clean_dataset", "arguments": {
                "dataset_id": source["id"], "pii": "keep", "drop_columns": DROP,
                "steps": [], "fill_missing": "none"}})
        if not report:
            assert clean[-1]["result"]["dataset_id"] in messages
            return json.dumps({"tool": "report_dataset", "arguments": {
                "dataset_id": clean[-1]["result"]["dataset_id"],
                "title": "Household report with personal style", "format": "all",
                "outcome": "wellbeing", "style": self.style}})
        result = report[-1]["result"]
        for key in ["docx_path", "pptx_path"]:
            path = Path(result[key])
            assert path.is_file() and str(path) in messages.replace("\\/", "/")
        return json.dumps({"answer": f"Created the styled Word and PowerPoint reports: {result['docx_path']} and {result['pptx_path']}."})


def verify(fixture: InstructionFixture, source: Path, original_hash: str) -> dict:
    assert not fixture.errors, "; ".join(fixture.errors)
    assert fixture.normal_calls == int(fixture.native_first) and fixture.action_calls == 3
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    steps = [s for s in read_steps(fixture.project) if s["tool"] != "objective_requirement_review"]
    assert [(step["tool"], step["ok"]) for step in steps] == [("clean_dataset", 1), ("report_dataset", 1)]
    clean, report = steps
    assert report["arguments"]["style"] == fixture.style
    assert report["arguments"]["format"] == "all"
    assert clean["arguments"]["drop_columns"] == DROP and clean["arguments"]["steps"] == []
    with Path(clean["result"]["path"]).open(newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == KEEP
        rows = list(reader)
    assert len(rows) == 48 and rows[7]["income"] == ""
    with sqlite3.connect(f"{(fixture.project / 'research.sqlite3').as_uri()}?mode=ro", uri=True) as connection:
        tasks = connection.execute("SELECT status, summary FROM research_tasks").fetchall()
    assert len(tasks) == 1 and tasks[0][0] == "completed" and "Created the styled" in tasks[0][1]

    heading_color = fixture.style["heading_color"].removeprefix("#")
    docx = Path(report["result"]["docx_path"])
    with zipfile.ZipFile(docx) as archive:
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        styles = ET.fromstring(archive.read("word/styles.xml"))
        def property_for(style_id: str, path: str, attribute: str) -> str | None:
            element = styles.find(f"w:style[@w:styleId='{style_id}']/{path}", ns)
            return None if element is None else element.attrib.get("{" + ns["w"] + "}" + attribute)
        assert property_for("Normal", "w:rPr/w:rFonts", "ascii") == "Arial"
        assert property_for("Normal", "w:rPr/w:sz", "val") == "24"
        assert property_for("Normal", "w:rPr/w:color", "val") == "222222"
        for style_id in ["Title", "Heading1"]:
            assert property_for(style_id, "w:rPr/w:color", "val") == heading_color
        docx_images = [path for path in archive.namelist() if path.startswith("word/media/")]
        assert docx_images, "The Word report must embed real chart images"
    pptx = Path(report["result"]["pptx_path"])
    with zipfile.ZipFile(pptx) as archive:
        slide_names = [path for path in archive.namelist() if path.startswith("ppt/slides/slide") and path.endswith(".xml")]
        slides = [ET.fromstring(archive.read(path)) for path in slide_names]
        a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        colors = {element.attrib.get("val") for slide in slides for element in slide.iter(a + "srgbClr")}
        fonts = {element.attrib.get("typeface") for slide in slides for element in slide.iter(a + "latin")}
        assert heading_color in colors and "222222" in colors and "FFFFFF" in colors
        assert "Arial" in fonts
        pptx_images = [path for path in archive.namelist() if path.startswith("ppt/media/")]
        assert pptx_images, "The PowerPoint must embed real chart images"
    for fmt in ["docx", "pptx"]:
        assert report["result"]["exports"][fmt]["charts_embedded"] > 0
        assert not report["result"]["exports"][fmt]["charts_referenced_only"]
    charts = list((fixture.project / "artifacts").rglob("*.svg"))
    assert any(fixture.style["accent_color"].lower() in path.read_text().lower() for path in charts), "Generated charts must apply the requested palette"
    return {"status": "passed", "mode": "native_then_recovery_with_override" if fixture.native_first else "structured_with_defaults",
            "native_requests": fixture.normal_calls, "structured_requests": fixture.action_calls,
            "memory_and_skill_in_every_request": True, "instruction_snapshot_stable": True,
            "explicit_override": fixture.override, "style": fixture.style,
            "word_report": str(docx), "powerpoint": str(pptx),
            "docx_embedded_images": len(docx_images), "pptx_embedded_images": len(pptx_images),
            "original_unchanged": True, "task_status": "completed"}


def run_case(binary: Path, worker_python: Path, output: Path, *, native_first: bool, timeout: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    support = output / "app-support"
    support.mkdir()
    (support / "worker-env").symlink_to(worker_python.parent.parent, target_is_directory=True)
    instructions = support / "Instructions"
    instructions.mkdir()
    (instructions / "memory.md").write_text(MEMORY)
    (instructions / "skill.md").write_text(SKILL)
    (instructions / "personality.md").write_text("# Squipi\n" + PERSONALITY_MARKER + " warm, funny and direct. Use observed tool results; current user requests take precedence.")
    source = output / "synthetic-households.csv"
    fields = ["household_id", *DROP, "age", "gender", "income", "schooling", "wellbeing"]
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index in range(48):
            writer.writerow({"household_id": f"HH{index + 1:03d}", "respondent_name": f"Synthetic Person {index + 1:03d}",
                             "phone": f"fixture-phone-{index + 1:03d}", "email": f"hh{index + 1:03d}@example.invalid",
                             "age": 24 + index % 43, "gender": ["female", "male", "nonbinary"][index % 3],
                             "income": "" if index == 7 else 1200 + 73 * index,
                             "schooling": 5 + index % 14, "wellbeing": round(3.1 + (index % 15) * .37, 2)})
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    fixture = InstructionFixture(output, native_first=native_first, override=native_first)
    environment = {name: os.environ[name] for name in ["PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL", "TZ"] if name in os.environ}
    instruction = GOAL + "\n\nLatest user instruction:\n" + ANSWER + "\n" + OVERRIDE if native_first else ANSWER
    if not native_first:
        history = support / "history.json"
        history.write_text(json.dumps([{"role": "user", "content": GOAL + "\n📎 synthetic-households.csv"},
                                       {"role": "assistant", "content": QUESTION}]))
        environment["SQUIPI_AGENT_HISTORY"] = str(history)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(fixture))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment.update({"SQUIPI_TEST_DATA_DIR": str(support),
                        "SQUIPI_TEST_OLLAMA_URL": f"http://127.0.0.1:{server.server_address[1]}",
                        "SQUIPI_OLLAMA_MODEL": "fixture-ollama", "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        completed = subprocess.run([str(binary), "--agent", instruction, str(source)], env=environment,
                                   text=True, capture_output=True, timeout=timeout, check=False)
        (output / "stdout.log").write_text(completed.stdout)
        (output / "stderr.log").write_text(completed.stderr)
        assert completed.returncode == 0, f"App exited {completed.returncode}; inspect {output}/stderr.log"
        assert "FINAL: Created the styled Word and PowerPoint reports" in completed.stdout, f"No completed report; inspect {output}"
        result = verify(fixture, source, original_hash)
        (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    finally:
        server.shutdown()
        server.server_close()
        (output / "model-requests.json").write_text(json.dumps(fixture.requests, indent=2) + "\n")
        if fixture.errors:
            (output / "fixture-errors.json").write_text(json.dumps(fixture.errors, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--worker-python", type=Path, default=Path.home() / "Library/Application Support/Squipi/worker-env/bin/python")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    binary = args.binary.resolve()
    worker_python = Path(os.path.abspath(args.worker_python))
    assert binary.is_file() and worker_python.is_file() and os.access(worker_python, os.X_OK)
    output = args.output_dir.resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix="squipi-instruction-integration-"))
    if args.output_dir:
        output.mkdir(parents=True, exist_ok=False)
    results = [run_case(binary, worker_python, output / "default-blue", native_first=False, timeout=args.timeout),
               run_case(binary, worker_python, output / "override-green", native_first=True, timeout=args.timeout)]
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    print(f"PASS: local memory and skill → native/structured Agent contexts → themed DOCX/PPTX; evidence: {output}")


if __name__ == "__main__":
    main()
