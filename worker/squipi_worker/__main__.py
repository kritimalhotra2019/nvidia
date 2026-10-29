"""python -m squipi_worker --project DIR            serve JSON lines on stdin/stdout
   python -m squipi_worker --selftest [--project DIR] profile a built-in fictional CSV and print the summary
   python -m squipi_worker --call TOOL --args JSON --project DIR [--allow PATH ...]   one-shot, for tests
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import tools  # noqa: F401  (registers the tools)
from .project import Project, _json_default
from .registry import registry, JobContext
from .security import AllowList
from .server import Server


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="squipi-worker")
    ap.add_argument("--project", help="project directory (created if missing)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--call", help="tool name for a one-shot call")
    ap.add_argument("--args", default="{}", help="JSON arguments for --call")
    ap.add_argument("--allow", action="append", default=[], help="paths the worker may read (repeatable)")
    a = ap.parse_args(argv)

    if a.selftest:
        from .fixtures import fictional_programme
        root = Path(a.project) if a.project else Path(tempfile.mkdtemp(prefix="squipi-selftest-"))
        allow = AllowList()
        csv = fictional_programme(root / "fictional_programme.csv")
        allow.allow(csv)
        project = Project(root, allow)
        ctx = JobContext(project, report=lambda p, n: print(f"  {p:3d}% {n}", file=sys.stderr), cancelled=lambda: False)
        imp = registry.call("import_dataset", {"path": str(csv)}, ctx)
        prof = registry.call("profile_dataset", {"dataset_id": imp["dataset_id"]}, ctx)
        print(prof["summary_text"])
        print("report:", prof["report_path"])
        print("charts:", len(prof["charts"]))
        return 0

    if a.call:
        if not a.project:
            print("--call needs --project", file=sys.stderr); return 2
        allow = AllowList()
        for p in a.allow:
            allow.allow(p)
        project = Project(a.project, allow)
        ctx = JobContext(project, report=lambda p, n: None, cancelled=lambda: False)
        result = registry.call(a.call, json.loads(a.args), ctx)
        print(json.dumps(result, default=_json_default, indent=2))
        return 0

    if not a.project:
        print("usage: python -m squipi_worker --project DIR", file=sys.stderr); return 2
    allow = AllowList()
    for p in a.allow:
        allow.allow(p)
    project = Project(a.project, allow)
    # stdout is the protocol channel: make sure nothing else prints there
    sys.stdout.reconfigure(line_buffering=True)
    Server(project).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
