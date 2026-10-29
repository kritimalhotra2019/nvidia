#!/usr/bin/env python3
"""Exercise the real Swift Agent/worker loop against a deterministic local model.

By default, an answered pending clarification must execute directly through
JSON-schema actions. --repeated-question instead starts a fresh combined request
and repeats the PII question; --native-parse-error starts that request with an
Ollama XML tool-parser error. Every mode must execute exact selective cleaning,
create a real Word report with charts, and finish using observed artifacts.
No inference or keys.
An existing worker environment is reused read-only; all state/output is isolated.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import zipfile
import xml.etree.ElementTree as ET


DROP = ["respondent_name", "phone", "email"]
KEEP = ["household_id", "age", "gender", "income", "schooling", "wellbeing"]
GOAL = "Create a Word descriptive report with household statistics and labelled charts using synthetic-households.csv."
QUESTION = "Should I remove personal information before creating the report? Which columns should I exclude?"
ANSWER = "Keep all columns except respondent_name, phone and email. Proceed with the report."


def read_steps(project: Path) -> list[dict]:
    path = project / "research.sqlite3"
    if not path.exists():
        return []
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM research_steps ORDER BY sequence").fetchall()
    return [dict(row, arguments=json.loads(row["arguments_json"]), result=json.loads(row["result_json"])) for row in rows]


class Fixture:
    def __init__(self, output: Path, native_parse_error: bool = False, repeated_question: bool = False):
        self.output = output
        self.native_parse_error = native_parse_error
        self.repeated_question = repeated_question
        self.native_first = native_parse_error or repeated_question
        self.native_parse_errors = 0
        self.project = output / "app-support/projects/default"
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self.normal_calls = 0
        self.action_calls = 0

    def action(self, request: dict) -> str | None:
        self.requests.append(request)
        if "format" not in request:
            self.normal_calls += 1
            assert self.native_first, "An answered pending clarification must start with structured actions, without another native question"
            assert self.normal_calls == 1, "After its first stall or parser error, the agent must stay in structured recovery mode"
            if self.native_parse_error:
                assert request.get("tools"), "The parser-error case must begin with native tool schemas"
                self.native_parse_errors += 1
                return None
            return QUESTION
        self.action_calls += 1
        assert self.normal_calls == int(self.native_first), "Fresh-request recovery modes must exercise the initial native attempt"
        assert self.action_calls <= 4, "The agent repeated an action instead of completing"
        assert "tools" not in request, "Structured actions must not mix the native tool parser with format"
        assert request.get("think") is False
        assert request["options"]["temperature"] == 0
        schema = request["format"]
        variants = schema["oneOf"]
        assert len(variants) in (2, 3), "Recovery needs tool/blocked, and answer only after required exports exist"
        names = variants[0]["properties"]["tool"]["enum"]
        assert {"clean_dataset", "report_dataset"}.issubset(names), "Clarification lost the original report tools"
        messages = "\n".join(message.get("content", "") for message in request["messages"])
        assert GOAL in messages and ANSWER in messages, "Recovery lost the original objective or exact latest user constraints"
        assert QUESTION not in messages, "Fresh recovery context should not repeat the assistant's stale question"

        datasets = json.loads((self.project / "datasets.json").read_text())["datasets"]
        source = next(dataset for dataset in datasets if not dataset.get("derived_from"))
        assert source["id"] in messages, "The selected imported input is missing from the model context"
        steps = read_steps(self.project)
        clean = [step for step in steps if step["tool"] == "clean_dataset" and step["ok"]]
        reports = [step for step in steps if step["tool"] == "report_dataset" and step["ok"]]
        assert (len(variants) == 3) == bool(reports), "The final-answer branch must be unavailable until a real Word export exists"
        if not clean:
            return json.dumps({"tool": "clean_dataset", "arguments": {
                "dataset_id": source["id"], "pii": "keep", "drop_columns": DROP,
                "steps": [], "fill_missing": "none",
            }})
        cleaned_id = clean[-1]["result"]["dataset_id"]
        assert cleaned_id in messages and "Observed result" in messages, "The model needs the actual cleaned dataset result"
        if not reports:
            return json.dumps({"tool": "report_dataset", "arguments": {
                "dataset_id": cleaned_id, "title": "Synthetic household descriptive report",
                "format": "docx", "outcome": "wellbeing",
            }})
        result = reports[-1]["result"]
        docx = Path(result["docx_path"])
        # Foundation's compact JSON escapes '/' as '\/', which is equivalent
        # JSON spelling; the path must still come from the observed tool result.
        assert docx.is_file() and str(docx) in messages.replace("\\/", "/"), "Final answer must follow an observed, existing Word artifact"
        return json.dumps({"answer": f"Created the Word report with descriptive statistics and labelled charts: {docx}. Removed only respondent_name, phone and email; the other columns and missing values were preserved."})


def handler_for(fixture: Fixture):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, value: dict, status: int = 200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/ps":
                self.send_json({"models": []})
            elif path == "/api/tags":
                self.send_json({"models": [{"name": "fixture-ollama", "model": "fixture-ollama"}]})
            else:
                fixture.errors.append(f"Unexpected GET {path}")
                self.send_json({"error": "Unexpected fixture request"}, 404)

        def do_POST(self):
            try:
                path = urlsplit(self.path).path
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if path == "/api/show":
                    self.send_json({"capabilities": ["completion", "tools"], "model_info": {"fixture.context_length": 32768}})
                    return
                assert path == "/api/chat", f"Unexpected POST {path}"
                assert request["model"] == "fixture-ollama"
                assert request["stream"] is True
                answer = fixture.action(request)
                if answer is None:
                    self.send_json({"error": "XML syntax error on line 1: invalid character entity in native tool call"}, 500)
                    return
                # Split JSON mid-token to exercise native NDJSON stream assembly.
                midpoint = len(answer) // 2
                chunks = [
                    {"message": {"role": "assistant", "content": answer[:midpoint]}, "done": False},
                    {"message": {"role": "assistant", "content": answer[midpoint:]}, "done": True,
                     "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 50},
                ]
                body = b"".join(json.dumps(chunk).encode() + b"\n" for chunk in chunks)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                fixture.errors.append(str(error))
                self.send_json({"error": str(error)}, 500)
    return Handler


def verify(fixture: Fixture, source: Path, original_hash: str) -> dict:
    assert not fixture.errors, "; ".join(fixture.errors)
    assert fixture.normal_calls == int(fixture.native_first) and fixture.action_calls == 3, "Expected clean/report/answer actions and a native attempt only in fresh-request recovery modes"
    assert fixture.native_parse_errors == int(fixture.native_parse_error), "Expected exactly one native parser error only in parser-error mode"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash, "The original input changed"
    with sqlite3.connect(f"{(fixture.project / 'research.sqlite3').as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tasks = [dict(row) for row in connection.execute("SELECT * FROM research_tasks")]
    assert len(tasks) == 1 and tasks[0]["status"] == "completed", f"Unexpected journal status: {[task['status'] for task in tasks]}"
    assert GOAL in tasks[0]["instruction"] and ANSWER in tasks[0]["instruction"]
    assert QUESTION not in tasks[0]["summary"] and "Created the Word report" in tasks[0]["summary"]
    steps = read_steps(fixture.project)
    assert [(step["tool"], step["ok"]) for step in steps] == [("clean_dataset", 1), ("report_dataset", 1)], "Actual worker actions must run exactly once and succeed"
    clean, report = steps
    arguments = clean["arguments"]
    assert arguments["pii"] == "keep" and arguments["drop_columns"] == DROP
    assert arguments["steps"] == [] and arguments["fill_missing"] == "none"
    assert report["arguments"]["dataset_id"] == clean["result"]["dataset_id"]
    assert report["arguments"]["format"] == "docx"
    cleaned_path = Path(clean["result"]["path"])
    with source.open(newline="") as stream:
        original = list(csv.DictReader(stream))
    with cleaned_path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == KEEP
        cleaned = list(reader)
    assert len(cleaned) == len(original) == 48
    # CSV export may canonicalize numeric formatting; semantic cell values and
    # missingness must be unchanged, while category strings are exact.
    numeric = {"age", "income", "schooling", "wellbeing"}
    for before, after in zip(original, cleaned):
        for column in KEEP:
            if column in numeric and before[column]:
                assert float(before[column]) == float(after[column]), (column, before[column], after[column])
            else:
                assert before[column] == after[column], (column, before[column], after[column])
    assert cleaned[7]["income"] == "", "The unrequested missing income must not be imputed"
    docx = Path(report["result"]["docx_path"])
    with zipfile.ZipFile(docx) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        text = " ".join(document.itertext()).lower()
        for column in ["age", "gender", "income", "schooling", "wellbeing"]:
            assert column in text, f"The Word report omits {column}"
        images = [name for name in archive.namelist() if name.startswith("word/media/")]
        assert images, "The Word report must contain actual embedded chart images"
    assert report["result"]["exports"]["docx"]["charts_embedded"] > 0
    assert not report["result"]["exports"]["docx"]["charts_referenced_only"], "Every requested Word graph must be embedded"
    svg_paths = sorted(str(path) for path in (fixture.project / "artifacts").rglob("*.svg"))
    assert svg_paths, "The report must retain editable vector chart outputs"
    mode = "native_parse_error" if fixture.native_parse_error else "repeated_clarification" if fixture.repeated_question else "answered_continuation"
    return {"status": "passed", "fixture_mode": mode,
            "native_parse_errors": fixture.native_parse_errors,
            "model_requests": len(fixture.requests), "native_requests": fixture.normal_calls, "structured_requests": fixture.action_calls,
            "successful_tools": [step["tool"] for step in steps], "task_status": tasks[0]["status"],
            "rows": len(cleaned), "retained_columns": KEEP, "removed_columns": DROP,
            "word_report": str(docx), "embedded_charts": len(images), "svg_charts": len(svg_paths),
            "cleaned_dataset": str(cleaned_path), "original_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path, help="Built Squipi executable (usually Squipi.app/Contents/MacOS/Squipi)")
    parser.add_argument("--worker-python", type=Path, default=Path.home() / "Library/Application Support/Squipi/worker-env/bin/python")
    parser.add_argument("--output-dir", type=Path, help="A new directory to retain synthetic fixtures, app state and test results")
    parser.add_argument("--timeout", type=int, default=180)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--repeated-question", action="store_true",
                      help="Start a fresh combined request without history; repeat its PII question once, then require structured recovery")
    mode.add_argument("--native-parse-error", action="store_true",
                      help="Start a fresh combined request without history; return one HTTP 500 XML syntax error, then require structured recovery")
    args = parser.parse_args()
    binary = args.binary.resolve()
    assert binary.is_file(), f"Missing Squipi executable: {binary}"
    worker_python = Path(os.path.abspath(args.worker_python))
    assert worker_python.is_file() and os.access(worker_python, os.X_OK), "An existing working Python environment is required; this test does not install dependencies"
    output = args.output_dir.resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix="squipi-agent-integration-"))
    if args.output_dir:
        output.mkdir(parents=True, exist_ok=False)
    support = output / "app-support"
    support.mkdir()
    (support / "worker-env").symlink_to(worker_python.parent.parent, target_is_directory=True)
    retired_runtime = support / "deepagents-env"
    (retired_runtime / "bin").mkdir(parents=True)
    retired_python = retired_runtime / "bin/python"
    retired_python.write_text("#!/bin/sh\nexit 97\n")
    retired_python.chmod(0o755)
    (retired_runtime / "squipi-ready.json").write_text("{}")
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
    fixture = Fixture(output, native_parse_error=args.native_parse_error, repeated_question=args.repeated_question)
    instruction = ANSWER
    history = None
    if fixture.native_first:
        instruction = GOAL + "\n\nLatest user instruction:\n" + ANSWER
    else:
        history = support / "history.json"
        history.write_text(json.dumps([
            {"role": "user", "content": GOAL + "\n📎 synthetic-households.csv"},
            {"role": "assistant", "content": QUESTION},
        ]))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(fixture))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {name: os.environ[name] for name in ["PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL", "TZ"] if name in os.environ}
    environment.update({"SQUIPI_TEST_DATA_DIR": str(support),
                        "SQUIPI_TEST_OLLAMA_URL": f"http://127.0.0.1:{server.server_address[1]}",
                        # Retired settings must not reactivate the external planner.
                        "SQUIPI_TEST_DEEPAGENTS": "1", "SQUIPI_TEST_DEEPAGENTS_PYTHON": str(retired_python),
                        "SQUIPI_OLLAMA_MODEL": "fixture-ollama", "PYTHONDONTWRITEBYTECODE": "1"})
    if history is not None:
        environment["SQUIPI_AGENT_HISTORY"] = str(history)
    try:
        completed = subprocess.run([str(binary), "--agent", instruction, str(source)], env=environment,
                                   text=True, capture_output=True, timeout=args.timeout, check=False)
        (output / "stdout.log").write_text(completed.stdout)
        (output / "stderr.log").write_text(completed.stderr)
        assert completed.returncode == 0, f"App exited {completed.returncode}; inspect {output}/stderr.log"
        assert "FINAL: Created the Word report" in completed.stdout, "The accepted structured answer must be visible in CLI output"
        results = verify(fixture, source, original_hash)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2))
        print(f"PASS: native Ollama structured recovery → actual worker cleaning → actual Word report; evidence: {output}")
    finally:
        server.shutdown()
        server.server_close()
        (output / "model-requests.json").write_text(json.dumps(fixture.requests, indent=2) + "\n")
        if fixture.errors:
            (output / "fixture-errors.json").write_text(json.dumps(fixture.errors, indent=2) + "\n")


if __name__ == "__main__":
    main()
