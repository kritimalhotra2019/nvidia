"""Worker and external-harness boundaries; every HTTP response is a local fixture."""
import io
import json
import time
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from squipi_worker import mcp
from squipi_worker.project import Project
from squipi_worker.registry import InvalidArguments, JobContext, registry, validate
from squipi_worker.security import AccessDenied, AllowList
from squipi_worker.server import Server as WorkerServer
from squipi_worker.tools import web


@pytest.fixture
def project(tmp_path):
    return Project(tmp_path / "project", AllowList())


@pytest.fixture
def http(monkeypatch):
    class Response:
        def __init__(self, url):
            papers = "openalex.org" in url
            self.headers = {"Content-Type": "application/json" if papers else "text/html"}
            self.data = (b'{"meta": {"count": 0}, "results": []}' if papers else
                         b"<html><title>Fixture</title><p>Observed evidence.</p></html>")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return self.data[:size]

    opened = Mock(side_effect=lambda request, timeout: Response(request.full_url))
    monkeypatch.setattr(web._opener, "open", opened)
    return opened


@pytest.mark.parametrize("value", [{}, {"count": "wrong"}, {"count": 1, "extra": True}])
def test_nullable_object_validates_required_properties_and_extras(value):
    schema = {"type": ["object", "null"], "properties": {"count": {"type": "integer"}},
              "required": ["count"], "additionalProperties": False}
    validate(schema, None)
    validate(schema, {"count": 1})
    with pytest.raises(InvalidArguments):
        validate(schema, value)


@pytest.mark.parametrize("name,args", [
    ("set_reminder", {"text": 42}),
    ("set_reminder", {"text": "Review", "in_minutes": "10"}),
    ("set_reminder", {"text": "Review", "in_minutes": -1}),
    ("remember_fact", {}),
    ("remember_fact", {"fact": "A fact", "network_authorized": True}),
    ("log_finding", {"title": "Finding", "text": "Evidence", "kind": "invented"}),
    ("log_finding", {"title": "Finding", "text": "Evidence", "sources": [42]}),
    ("list_project", {"path": "/unapproved"}),
    ("list_project", []),
    ("list_project", None),
])
def test_mcp_validates_before_app_tool_side_effects(project, tmp_path, monkeypatch, name, args):
    memory = mcp.AppMemory(tmp_path / "app")
    writes = Mock(side_effect=AssertionError("invalid input reached an app handler"))
    for method in ("remember", "remind", "log"):
        monkeypatch.setattr(memory, method, writes)
    response = mcp.Server(project, memory).handle({"id": 1, "method": "tools/call",
                                                 "params": {"name": name, "arguments": args}})
    assert response["result"]["isError"] is True
    writes.assert_not_called()
    assert not memory.mind.exists()


NETWORK_REQUESTS = [("search_papers", {"query": "research evidence"}),
                    ("fetch_web", {"url": "https://example.invalid/evidence"})]


@pytest.mark.parametrize("name,args", NETWORK_REQUESTS)
@pytest.mark.parametrize("surface", ["registry", "mcp", "worker"])
@pytest.mark.parametrize("authorized", [False, True])
def test_network_permission_is_checked_before_http(project, tmp_path, http, name, args, surface, authorized):
    if surface == "registry":
        ctx = JobContext(project, lambda p, n: None, lambda: False, allow_network=authorized)
        if authorized:
            registry.call(name, args, ctx)
        else:
            with pytest.raises(AccessDenied, match="Network access is disabled"):
                registry.call(name, args, ctx)
    elif surface == "mcp":
        server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"), allow_network=authorized)
        result = server.handle({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        assert result["result"]["isError"] is not authorized
    else:
        out = io.StringIO()
        server = WorkerServer(project, out=out)
        request = {"id": 1, "method": "call", "params": {"tool": name, "args": args}}
        if authorized:
            request["network_authorized"] = True
        server.serve(io.StringIO(json.dumps(request) + "\n"))
        result = [json.loads(line) for line in out.getvalue().splitlines() if '"ok"' in line][-1]
        assert result["ok"] is authorized
        if not authorized:
            assert result["error"]["code"] == "access_denied"
    assert http.call_count == int(authorized)


@pytest.mark.parametrize("permission", [None, "true", 1])
def test_native_permission_requires_host_boolean(project, http, permission):
    out = io.StringIO()
    request = {"id": 1, "method": "call", "network_authorized": permission,
               "params": {"tool": "search_papers", "args": {"query": "evidence"}, "network_authorized": True}}
    WorkerServer(project, out=out).serve(io.StringIO(json.dumps(request) + "\n"))
    assert json.loads(out.getvalue().splitlines()[-1])["error"]["code"] == "access_denied"
    http.assert_not_called()


def test_model_cannot_enable_network_in_tool_arguments(project, tmp_path, http):
    server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"))
    with pytest.raises(InvalidArguments):
        server.call("search_papers", {"query": "evidence", "allow_network": True})
    with pytest.raises(InvalidArguments, match="unknown tool"):
        server.call("allow_network", {})
    http.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
def test_mcp_cli_controls_network_without_hiding_tools(tmp_path, monkeypatch, enabled):
    observed = []
    monkeypatch.delenv("SQUIPI_RESEARCH_TASK_ID", raising=False)

    def inspect(server):
        observed.append(server.allow_network)
        assert set(registry.names()) - mcp.JOURNAL_TOOLS <= {tool["name"] for tool in server.tools()}
        assert server.call("list_project", {}) == {"datasets": [], "documents": []}

    monkeypatch.setattr(mcp.Server, "serve", inspect)
    args = ["--project", str(tmp_path / "project"), "--app-support", str(tmp_path / "app")]
    if enabled:
        args.append("--allow-network")
    assert mcp.main(args) == 0
    assert observed == [enabled]


@pytest.fixture(params=["UTC", "Asia/Kolkata"])
def local_timezone(request, monkeypatch):
    with monkeypatch.context() as env:
        env.setenv("TZ", request.param)
        time.tzset()
        yield request.param
    time.tzset()


def test_reminder_relative_and_created_dates_use_swift_utc_epoch(tmp_path, local_timezone):
    memory = mcp.AppMemory(tmp_path / "new-app-support")
    before = time.time() - 978307200  # Foundation Date's 2001-01-01T00:00:00Z epoch.
    memory.remind("Review evidence", in_minutes=90)
    memory.remind("Now", in_minutes=0)
    after = time.time() - 978307200
    first, immediate = memory._load()["reminders"]
    assert before - 1e-6 <= first["created"] <= after + 1e-6  # datetime stores microseconds; tolerate one rounding unit.
    assert first["due"] - first["created"] == 90 * 60
    assert immediate["due"] == immediate["created"]


def test_reminder_absolute_dates_preserve_local_or_explicit_timezone(tmp_path, local_timezone):
    memory = mcp.AppMemory(tmp_path / "app", now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    memory.remind("Local clock", due="2026-10-21 12:00")
    memory.remind("Offset clock", due="2026-10-21T12:00:00+05:30")
    local, offset = memory._load()["reminders"]
    local_utc_hour = 12 if local_timezone == "UTC" else 6
    local_utc_minute = 0 if local_timezone == "UTC" else 30
    expected_local = datetime(2026, 10, 21, local_utc_hour, local_utc_minute, tzinfo=timezone.utc)
    expected_offset = datetime(2026, 10, 21, 6, 30, tzinfo=timezone.utc)
    assert local["due"] == expected_local.timestamp() - 978307200
    assert offset["due"] == expected_offset.timestamp() - 978307200


@pytest.mark.parametrize("timing", [
    {"in_minutes": -1}, {"in_minutes": True}, {"in_minutes": "5"},
    {"in_minutes": float("nan")}, {"in_minutes": float("inf")}, {"in_minutes": 1e300},
    {"in_minutes": 5, "due": "2026-10-21 12:00"},
    {"due": "2026-02-30 12:00"}, {"due": "2026-10-21"}, {"due": ""},
    {"due": "2025-01-01T12:00:00Z"}, {"due": 123},
])
def test_reminder_invalid_times_never_save_an_untimed_fallback(tmp_path, timing):
    memory = mcp.AppMemory(tmp_path / "app", now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(InvalidArguments):
        memory.remind("Review", **timing)
    assert not memory.mind.exists()


def test_reminder_dst_gaps_folds_and_relative_elapsed_time(tmp_path, monkeypatch):
    with monkeypatch.context() as env:
        env.setenv("TZ", "America/New_York")
        time.tzset()
        try:
            memory = mcp.AppMemory(tmp_path / "app", now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
            for wall_time in ("2026-03-08 02:30", "2026-11-01 01:30"):
                with pytest.raises(InvalidArguments, match="daylight-saving"):
                    memory.remind("Ambiguous", due=wall_time)
            memory.remind("First 1:30", due="2026-11-01T01:30:00-04:00")
            memory.remind("Second 1:30", due="2026-11-01T01:30:00-05:00")
            first, second = memory._load()["reminders"]
            assert second["due"] - first["due"] == 3600
            near_jump = mcp.AppMemory(tmp_path / "relative", now=lambda: datetime(2026, 3, 8, 6, 55, tzinfo=timezone.utc))
            near_jump.remind("Ten actual minutes", in_minutes=10)
            reminder = near_jump._load()["reminders"][0]
            assert reminder["due"] - reminder["created"] == 600
            assert reminder["due"] == datetime(2026, 3, 8, 7, 5, tzinfo=timezone.utc).timestamp() - 978307200
        finally:
            env.undo()
            time.tzset()


def test_reminder_restart_and_corrupt_storage_preserve_data(tmp_path):
    memory = mcp.AppMemory(tmp_path / "app", now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    memory.remind("After restart", in_minutes=5)
    restarted = mcp.AppMemory(memory.dir)
    assert restarted._load() == memory._load()
    memory.mind.write_text("not valid JSON", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        restarted.remind("Must not erase prior data", in_minutes=1)
    assert memory.mind.read_text() == "not valid JSON"


@pytest.mark.parametrize("name", sorted(mcp.JOURNAL_TOOLS))
def test_mcp_keeps_execution_ledger_host_only(project, tmp_path, monkeypatch, name):
    call = Mock(side_effect=AssertionError("model reached host journal"))
    monkeypatch.setattr(registry, "call", call)
    server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"), task_id="task_host")
    assert not mcp.JOURNAL_TOOLS & {tool["name"] for tool in server.tools()}
    with pytest.raises(AccessDenied, match="managed by the host"):
        server.call(name, {"task_id": "forged"})
    call.assert_not_called()


@pytest.mark.parametrize("name,args,method", [
    ("set_reminder", {"text": "Review", "in_minutes": 5}, "remind"),
    ("remember_fact", {"fact": "Study A"}, "remember"),
    ("log_finding", {"title": "Study", "text": "Evidence", "sources": ["paper-1"]}, "log"),
    ("list_project", {}, None),
])
def test_mcp_checkpoints_every_app_tool(project, tmp_path, monkeypatch, name, args, method):
    journal = Mock(return_value={"step_id": "step_observed"})
    monkeypatch.setattr(registry, "call", journal)
    memory = mcp.AppMemory(tmp_path / "app")
    mutation = Mock(return_value="Action completed")
    if method:
        monkeypatch.setattr(memory, method, mutation)
    server = mcp.Server(project, memory, task_id="task_host")
    result = server.call(name, args)
    assert journal.call_count == 1
    tool, payload, ctx = journal.call_args.args
    assert tool == "record_research_step"
    assert payload == {"task_id": "task_host", "tool": name, "arguments": args, "result": result, "ok": True}
    assert ctx.allow_network is False
    assert mutation.call_count == int(method is not None)


def test_mcp_checkpoints_full_result_and_original_arguments_before_transport_truncation(project, tmp_path, monkeypatch):
    full_result = {"text": "evidence " * 10000, "nested": {"counts": [1, 2, None]}}
    observed = []

    def call(name, args, ctx):
        if name == "record_research_step":
            observed.append(args)
            return {"step_id": "step_observed"}
        assert name == "read_document"
        args["options"]["changed_by_handler"] = True
        return full_result

    monkeypatch.setattr(registry, "call", call)
    server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"), task_id="task_host")
    result = server.handle({"id": 1, "method": "tools/call", "params": {
        "name": "read_document", "arguments": {"document_id": "doc_1", "options": {"pages": [1, 2]}}}})
    assert result["result"]["isError"] is False
    envelope = json.loads(result["result"]["content"][0]["text"])
    assert envelope["truncated"] is True
    assert envelope["next_call"]["name"] == "read_tool_result"
    assert len(result["result"]["content"][0]["text"]) <= mcp.MCP_INLINE_CHARS
    assert observed == [{"task_id": "task_host", "tool": "read_document",
                         "arguments": {"document_id": "doc_1", "options": {"pages": [1, 2]}},
                         "result": full_result, "ok": True}]


@pytest.mark.parametrize("journal_fails", [False, True])
def test_mcp_checkpoints_handler_errors_and_rethrows_original(project, tmp_path, monkeypatch, journal_fails):
    observed = []
    original = InvalidArguments("source document is unavailable")

    def call(name, args, ctx):
        if name == "record_research_step":
            observed.append(args)
            if journal_fails:
                raise OSError("journal storage unavailable")
            return {"step_id": "step_observed"}
        raise original

    monkeypatch.setattr(registry, "call", call)
    server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"), task_id="task_host")
    with pytest.raises(InvalidArguments) as raised:
        server.call("read_document", {"document_id": "doc_missing"})
    assert raised.value is original
    assert observed == [{"task_id": "task_host", "tool": "read_document",
                         "arguments": {"document_id": "doc_missing"}, "ok": False,
                         "result": {"error": "source document is unavailable", "error_type": "InvalidArguments"}}]
    response = server._tool_error(1, raised.value, str(raised.value))
    text = response["result"]["content"][0]["text"]
    assert "source document is unavailable" in text
    assert ("persistence_warning" in text) is journal_fails
    if journal_fails:
        assert "journal storage unavailable" in text


@pytest.mark.parametrize("large_result", [False, True])
def test_mcp_journal_failure_preserves_success_without_repeating_action(project, tmp_path, monkeypatch, large_result):
    journal = Mock(side_effect=OSError("journal storage unavailable"))
    monkeypatch.setattr(registry, "call", journal)
    memory = mcp.AppMemory(tmp_path / "app")
    mutation = Mock(return_value="Action completed" + ("x" * 70000 if large_result else ""))
    monkeypatch.setattr(memory, "remember", mutation)
    server = mcp.Server(project, memory, task_id="task_host")
    response = server.handle({"id": 1, "method": "tools/call", "params": {
        "name": "remember_fact", "arguments": {"fact": "Research preference"}}})
    assert response["result"]["isError"] is False
    text = "\n".join(item["text"] for item in response["result"]["content"])
    assert "Action completed" in text and "persistence_warning" in text
    assert "journal storage unavailable" in text
    mutation.assert_called_once_with("Research preference")
    assert journal.call_count == 1
    assert journal.call_args.args[1]["ok"] is True


def test_mcp_standalone_memory_does_not_create_a_journal(project, tmp_path, monkeypatch):
    journal = Mock(side_effect=AssertionError("standalone MCP should not journal"))
    monkeypatch.setattr(registry, "call", journal)
    memory = mcp.AppMemory(tmp_path / "app")
    server = mcp.Server(project, memory)
    assert server.call("remember_fact", {"fact": "Keep source links"}) == {"summary_text": "Remembered: Keep source links"}
    assert "Keep source links" in memory._load()["facts"]
    assert {"remember_fact", "recall_research", "remember_research"} <= {tool["name"] for tool in server.tools()}
    journal.assert_not_called()


@pytest.mark.parametrize("task_id,expected", [("task_from_host", "task_from_host"), ("", None),
                                            ("${SQUIPI_RESEARCH_TASK_ID}", None),
                                            ("${env:SQUIPI_RESEARCH_TASK_ID}", None)])
def test_mcp_reads_task_id_only_from_host_environment(tmp_path, monkeypatch, task_id, expected):
    observed = []
    monkeypatch.setenv("SQUIPI_RESEARCH_TASK_ID", task_id)
    monkeypatch.setattr(mcp.Server, "serve", lambda server: observed.append(server.task_id))
    assert mcp.main(["--project", str(tmp_path / "project"), "--app-support", str(tmp_path / "app")]) == 0
    assert observed == [expected]


@pytest.mark.parametrize("has_host_task", [False, True])
def test_mcp_rejects_plan_updates_without_matching_host_task(project, tmp_path, has_host_task):
    ctx = JobContext(project, lambda p, n: None, lambda: False)
    current = registry.call("start_research_task", {"instruction": "Current research"}, ctx)["task_id"]
    prior = registry.call("start_research_task", {"instruction": "Interrupted prior research"}, ctx)["task_id"]
    server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"), task_id=current if has_host_task else None)
    plan = [{"title": "Mark the wrong task complete", "status": "completed"}]
    with pytest.raises(AccessDenied, match="supplied by the host"):
        server.call("plan_research", {"task_id": prior, "steps": plan})
    for task_id in (current, prior):
        assert registry.call("read_research_task", {"task_id": task_id}, ctx)["task"]["plan"] == []
    current_steps = registry.call("read_research_task", {"task_id": current}, ctx)["steps"]
    assert len(current_steps) == int(has_host_task)
    if has_host_task:
        assert current_steps[0]["ok"] is False
        assert current_steps[0]["arguments"]["task_id"] == prior


def test_mcp_updates_current_plan_and_can_read_prior_task(project, tmp_path):
    ctx = JobContext(project, lambda p, n: None, lambda: False)
    current = registry.call("start_research_task", {"instruction": "Current research"}, ctx)["task_id"]
    prior = registry.call("start_research_task", {"instruction": "Prior research"}, ctx)["task_id"]
    server = mcp.Server(project, mcp.AppMemory(tmp_path / "app"), task_id=current)
    plan = [{"title": "Inspect existing evidence", "status": "running"}]
    result = server.call("plan_research", {"task_id": current, "steps": plan})
    assert result["saved"] is True and result["plan"] == plan
    assert registry.call("read_research_task", {"task_id": current}, ctx)["task"]["plan"] == plan
    assert server.call("read_research_task", {"task_id": prior})["task"]["task_id"] == prior
