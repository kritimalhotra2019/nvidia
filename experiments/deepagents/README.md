# Squipi Deep Agents runner

**Retired experiment — 2026-10-24.** The app now uses Squipi's native research
harness for Ollama and MLX. This source is retained for investigation and is not
compiled or bundled. The former Swift adapter and native integration fixture are
archived in `legacy/`; the fixture requires a historical Deep Agents-enabled build.
Existing user checkpoints and the installed Python environment are retained.
The integration and verification notes below describe the historical experiment,
not a current setting or a guarantee of live-model task completion.

Pinned `deepagents==0.7.32`, `langchain-ollama==1.1.0`, `langchain-openai==1.7.2` and
`langgraph-checkpoint-sqlite==3.1.2` (full dependency versions in `requirements.lock`).
Runs locally for Ollama and MLX only. No hosted API key or tracing is configured.

The native app wraps its local provider for research decisions. A short-lived Python
process invokes the real `create_deep_agent` graph with a SQLite checkpointer, a
thread-state virtual filesystem, context management and Squipi's tool schemas.
Custom tools return **proposals only** with `return_direct=True`; they never execute
research or Mac operations. The native host validates and executes each proposal via
its existing permission, journal, output-check and objective-cache boundaries. The
next decision receives authoritative observed host results. A fixed host-context
message ID replaces the prior snapshot rather than duplicating the whole journal.

This keeps one owner of external side effects. It is a Deep Agents planning/context
integration, not a second independently executing research loop. The native run
budget and completion gates still apply. Graph scratch state is per objective;
long-term user preferences continue to use Squipi's existing project-memory tools.

Graph checkpoints live at `projects/default/deepagents/checkpoints.sqlite3`, keyed by
the stable native objective UUID. The native continuation and execution cache remain
necessary: a checkpoint alone does not guarantee exactly-once execution of external
operations. Existing standard-runner tasks retain their native history when resumed.

Local model URLs are restricted to plain HTTP loopback addresses; hosted aliases and
redirects are rejected. The process inherits no cloud credentials. Shell tools and
parallel/general-purpose subagents are disabled. Virtual filesystem operations only
change graph scratch state, not user Mac files. Real files and templates use native
host tools and folder grants. Truncated/invalid model actions cannot become executable
proposals. Local native-tool parser failures retain one validated action recovery
through Squipi's original provider, with the same host execution gates. Missing inputs/denied permissions can return a typed blocker. Each decision
subprocess is cancelled with its parent request and has a ten-minute safety bound.

The app bundles this source and its lockfile. `bootstrap.sh RUNTIME_DIR UV_BINARY`
installs a separate Python environment and writes a readiness marker. The user can
switch Deep Agents off in Me → Research runner; greetings and simple explanations
continue through the original fast provider path.

## Verification

- Ten Python checks cover proposal-only execution, real checkpoint reopening, virtual
  notes persisting between graph runs, truncated-action rejection, typed blockers,
  local URL restrictions and disabled shell/delegation tools.
- `tests/test_deepagents_native.py` uses the actual native release app, actual Deep
  Agents and actual worker with scripted loopback model servers for both adapters.
  Each produces a selectively cleaned 48-row CSV, correctly named XLSX and a real
  DOCX with charts. It also checks that noninteractive Mac actions fail without
  exposing application data or presenting an executable host action.
- Controlled inference verifies integration, not live Qwen reasoning, timing or
  arbitrary research quality. Real-model and interactive Accessibility results are
  recorded separately in `docs/VERIFICATION.md`.
