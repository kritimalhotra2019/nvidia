"""JSON-lines server on stdin/stdout.

Request:  {"id": "r1", "method": "hello" | "allow" | "call" | "cancel" | "datasets" (also lists documents) | "shutdown", "params": {...}}
Reply:    {"id": "r1", "ok": true, "result": {...}, "elapsed_ms": 12}
          {"id": "r1", "ok": false, "error": {"code": "invalid_arguments" | "access_denied" | "cancelled" | "failed", "message": "..."}}
Progress: {"id": "r1", "progress": {"pct": 40, "note": "reading"}}   (zero or more, before the reply)

One job runs at a time; `cancel` targets a queued or running job by id. Nothing but protocol lines
is ever written to stdout; diagnostics go to stderr.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
import traceback
from typing import Any, Dict, Optional

from .execution_cache import ExecutionCache
from . import __version__
from .project import Project, _json_default
from .registry import registry, JobContext, InvalidArguments, Cancelled
from .security import AllowList, AccessDenied


class Server:
    def __init__(self, project: Project, out=None):
        self.project = project
        self._objective_id = None
        self._current_receipt = None
        self.out = out or sys.stdout
        self._lock = threading.Lock()
        self._jobs: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._cancelled: set[str] = set()
        self._running: Optional[str] = None
        self._worker = threading.Thread(target=self._run_jobs, name="squipi-jobs", daemon=True)
        self._worker.start()
        # A new worker process means any task still "running" was interrupted (quit, crash, kill).
        try:
            from .research import ResearchStore
            swept = ResearchStore(self.project.root).sweep_interrupted()
            if swept:
                print(f"research journal: marked {swept} interrupted task(s) partial", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - never block startup on journal maintenance
            print(f"research journal sweep skipped: {exc}", file=sys.stderr)

    # ---- output
    def send(self, obj: Dict[str, Any]) -> None:
        line = json.dumps(obj, default=_json_default, ensure_ascii=False)
        with self._lock:
            self.out.write(line + "\n")
            self.out.flush()

    def _embedding_status(self) -> Dict[str, Any]:
        """Configuration only (no probe at hello, so the embedding model is not loaded until first use)."""
        try:
            from .embeddings import Embedder
            return Embedder(self.project.root).status(probe=False)
        except Exception:  # noqa: BLE001
            return {"enabled": False}

    def reply(self, rid: Any, result: Any, t0: float) -> None:
        self.send({"id": rid, "ok": True, "result": result, "elapsed_ms": int((time.time() - t0) * 1000)})

    def fail(self, rid: Any, code: str, message: str, t0: float) -> None:
        if self._current_receipt and rid==self._running:
            from .objectives import ObjectiveStore
            ObjectiveStore(self.project).finish_step(self._current_receipt,{'error':message,'code':code},False)
        self.send({"id": rid, "ok": False, "error": {"code": code, "message": message}, "elapsed_ms": int((time.time() - t0) * 1000)})

    # ---- dispatch
    def handle(self, req: Dict[str, Any]) -> bool:
        """Returns False when the server should stop."""
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        t0 = time.time()
        if method == "hello":
            import pandas, numpy
            self.reply(rid, {"worker": "squipi-worker", "version": __version__, "python": sys.version.split()[0],
                             "pandas": pandas.__version__, "numpy": numpy.__version__,
                             "project": str(self.project.root), "tools": registry.describe(),
                             "embeddings": self._embedding_status(),
                             "limits": {"full_profile_rows": 2_000_000, "full_profile_bytes": 400 * 1024 * 1024, "sample_rows": 500_000}}, t0)
        elif method == "allow":
            paths = params.get("paths") or []
            if not isinstance(paths, list):
                self.fail(rid, "invalid_arguments", "paths must be a list", t0); return True
            added = [str(self.project.allow.allow(p)) for p in paths]
            self.reply(rid, {"allowed": added}, t0)
        elif method == "datasets":
            self.reply(rid, {"datasets": [d.to_json() for d in self.project.datasets()],
                             "documents": [d.to_json() for d in self.project.documents()]}, t0)
        elif method == "forget":
            self.reply(rid, {"forgotten": bool(self.project.forget(str(params.get("id", ""))))}, t0)
        elif method == "clear_project":
            # Native-host IPC only (28 Oct 2026, user: "there has to be a way to clear all datasets"). Drops every
            # registered dataset and document, saved outputs and caches. The user's own files are never touched;
            # research memory, the task journal and learning stay unless journal=true. dry_run reports what would go.
            import shutil
            root = self.project.root
            dry = bool(params.get("dry_run", False))
            datasets = list(self.project.datasets()); documents = list(self.project.documents())
            targets = []
            if params.get("artifacts", True):
                targets += [p for p in (root / "artifacts").iterdir()] if (root / "artifacts").is_dir() else []
                targets += [root / "presentation-drafts", root / "objective-reviews", root / "deepagents"]
            if params.get("caches", True):
                targets += [root / "execution-cache.sqlite3", root / "embeddings.sqlite3", root / "dataset-lineage.json", root / "datasets.json.bak"]
            if params.get("journal", False):
                targets += [root / "research.sqlite3", root / "objectives.sqlite3", root / "learning.sqlite3", root / "audit.jsonl", root / "feedback-cases.jsonl"]
            def size(p):
                try:
                    return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                except OSError:
                    return 0
            existing = [p for p in targets if p.exists()]
            total = sum(size(p) for p in existing)
            removed = 0
            if not dry:
                for item in datasets + documents:
                    self.project.forget(item.id)
                for p in existing:
                    try:
                        shutil.rmtree(p) if p.is_dir() else p.unlink()
                        removed += 1
                    except OSError:
                        pass
                (root / "artifacts").mkdir(exist_ok=True)
            self.reply(rid, {"dry_run": dry, "datasets": len(datasets), "documents": len(documents), "items": len(existing), "bytes": int(total), "removed": removed}, t0)
        elif method == "templates":
            # Native-host IPC only: deliberately absent from the model registry.
            from .templates import library_action
            try:
                self.reply(rid, library_action(self.project, params), t0)
            except InvalidArguments as exc:
                self.fail(rid, "invalid_arguments", str(exc), t0)
            except AccessDenied as exc:
                self.fail(rid, "access_denied", str(exc), t0)
            except Exception as exc:
                self.fail(rid, "failed", f"{type(exc).__name__}: {exc}", t0)
        elif method == "learning":
            # Native-host only: models can search, but cannot fabricate learning provenance or approve writes.
            from .learning import LearningStore
            try:
                self.reply(rid, LearningStore(self.project.root).action(params), t0)
            except Exception as exc:
                self.fail(rid, 'failed', str(exc), t0)
        elif method == "objective":
            from .objectives import ObjectiveStore
            try:
                store=ObjectiveStore(self.project)
                action=params.get('action')
                if action=='open':
                    result=store.open(params.get('objective_id'),params.get('instruction'),params.get('inputs',[]),params.get('user_instruction'))
                    self._objective_id=result['objective_id']
                elif action=='review':result=store.review(params['objective_id'])
                elif action=='state':
                    result=store.set_state(params['objective_id'],params['state'])
                    if self._objective_id==params['objective_id'] and params['state']!='running':self._objective_id=None
                else:raise InvalidArguments('Unknown objective operation')
                self.reply(rid,result,t0)
            except Exception as exc:self.fail(rid,'failed',str(exc),t0)
        elif method == "cached_result":
            # Native-host IPC only, absent from the model/MCP tool registry.
            try:
                saved=ExecutionCache(self.project).lookup(params.get('tool'),params.get('args'),params.get('execution_scope'))
                self.reply(rid,{'cached_result':saved},t0)
            except Exception as exc:
                self.fail(rid,'failed',str(exc),t0)
        elif method == "call":
            self._jobs.put({"id": rid, "tool": params.get("tool"), "args": params.get("args", {}), "t0": t0,
                            "network_authorized": req.get("network_authorized") is True, "execution_scope": params.get("execution_scope"), "objective_id":self._objective_id,
                            "exclusive_model": req.get("exclusive_model") if req.get("exclusive_model") == "kumo" else None,
                            "service_credentials": params.get("service_credentials",{}) if req.get("network_authorized") is True else {}})
        elif method == "cancel":
            target = params.get("target")
            self._cancelled.add(target)
            self.reply(rid, {"cancelled": target, "was_running": self._running == target}, t0)
        elif method == "shutdown":
            self.reply(rid, {"bye": True}, t0)
            return False
        else:
            self.fail(rid, "invalid_arguments", f"unknown method {method!r}", t0)
        return True

    def _run_jobs(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            rid, t0 = job["id"], job["t0"]
            if rid in self._cancelled:
                self.fail(rid, "cancelled", "cancelled before it started", t0); continue
            self._running = rid
            ctx = JobContext(self.project,
                             report=lambda pct, note: self.send({"id": rid, "progress": {"pct": pct, "note": note}}),
                             cancelled=lambda: rid in self._cancelled,
                             allow_network=job["network_authorized"])
            ctx.objective_id=job.get('objective_id')
            ctx.service_credentials=job.get('service_credentials',{})
            ctx.exclusive_model=job.get('exclusive_model')
            receipt=None
            try:
                if ctx.objective_id and job['tool'] not in {'read_research_objective','research_capabilities','start_research_task','record_research_step','finish_research_task','clear_research_history','read_research_task','recall_research'}:
                    from .objectives import ObjectiveStore
                    receipt=ObjectiveStore(self.project).begin(ctx.objective_id,job['tool'],job['args'])
                    self._current_receipt=receipt
                result = ExecutionCache(self.project).call(job["tool"], job["args"], ctx, job.get("execution_scope"), lambda: registry.call(job["tool"], job["args"], ctx))
                if receipt:ObjectiveStore(self.project).finish_step(receipt,result,True)
                self.reply(rid, result, t0)
            except Cancelled:
                self.fail(rid, "cancelled", "cancelled", t0)
            except InvalidArguments as e:
                self.fail(rid, "invalid_arguments", str(e), t0)
            except AccessDenied as e:
                self.fail(rid, "access_denied", str(e), t0)
            except MemoryError:
                self.fail(rid, "failed", "ran out of memory; try a smaller file or let Squipi sample it", t0)
            except Exception as e:  # noqa: BLE001 - every failure must become a visible error
                print(traceback.format_exc(), file=sys.stderr)
                self.fail(rid, "failed", f"{type(e).__name__}: {e}", t0)
            finally:
                if receipt:
                    # A committed success is never overwritten by interrupted/error handling.
                    with ObjectiveStore(self.project).db() as db:
                        db.execute("UPDATE receipts SET state='failed' WHERE id=? AND state='running'",(receipt,))
                self._current_receipt=None
                self._running = None
                self._cancelled.discard(rid)

    def serve(self, inp=None) -> None:
        inp = inp or sys.stdin
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                self.send({"id": None, "ok": False, "error": {"code": "bad_json", "message": str(e)}})
                continue
            if not self.handle(req):
                break
        self._jobs.put(None)
        self._worker.join(timeout=2)
