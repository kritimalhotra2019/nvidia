> Native integration: the new app build offers this runner in Me → Research runner, off by default.
> Real model testing still pauses before some template deliverables. Checkpoints improve recovery,
> not model capability. The standard runner remains the default. See docs/VERIFICATION.md.

# Squipi × NeMo Agent Toolkit trial

This is an isolated **local command-line prototype**, not a replacement for the installed Squipi app.
It uses NVIDIA NeMo Agent Toolkit 1.9.0 to compose a model client, registered Squipi tools, and a
research workflow. The model chooses the tools. Squipi's own SQLite journal supplies durable
execution; installing NAT alone does not supply these recovery guarantees.

## Try it

From this directory, run `./setup.sh` once. It creates a separate `.venv`; it does not change
the shipping app's Python environment. Start Ollama with a downloaded tool-capable model.
No NVIDIA, Nebius, or OpenAI API key is needed. The default is local `qwen3.5:9b`.

Create a new task directory with your instruction and explicitly attached files:

```bash
./trial.sh init ./runs/household-test \
  --attach /absolute/path/households.xlsx \
  --template /absolute/path/example.pptx \
  --require xlsx --require docx --require pptx \
  --instruction 'Remove PII, save cleaned Excel, then analyse the cleaned data. Create descriptive statistics, labelled graphs and key insights in Word and PowerPoint. Use my presentation template.'

# Deliberately pause once the cleaned Excel is saved.
./trial.sh run ./runs/household-test --pause-after export_excel

# This can be a different terminal/process. Use the SAME task directory.
./trial.sh run ./runs/household-test
./trial.sh status ./runs/household-test
```

Each invocation has a five-minute budget by default (`--seconds`) and at most 12 tool calls
(`--steps`). Status is `paused` when a budget is reached. Run the same command to resume;
Ctrl-C also pauses. `--message 'additional instructions'`
adds an amendment after pending calls have finished. Do not rerun `init` to resume.
Use `--model nemotron-3-nano:4b` to try the other downloaded local model.

For automatic continuation within a running CLI process:

```bash
./trial.sh work ./runs/household-test --cycles 6 --request-seconds 120 --max-stalls 2
# In another terminal:
./trial.sh stop ./runs/household-test
```

`work` continues after step/time budgets or transient model failures, retaining the same task.
It stops on explicit Stop, uncertain writes, permanent errors, repeated attempts without a new
successful step, or the overall cycle limit. A new explicit `run`/`work` resumes a stopped task.
The model-request deadline is separate from the invocation budget. Older large tool results are
compacted in model context; full results and all user messages remain saved. Recent request timings
and token usage (where available) are in the SQLite state. This is not yet a native app background
service. Closing the process does not schedule an automatic restart.

`--require` declares the artifact formats that must exist before the task can complete. It
does not replace the natural-language instruction or fix the tool sequence. Without these
flags, a text-only response can complete a task. Files and original template bytes are copied
into this task's private directory; the originals are not modified.

## What is integrated

- NAT component registration and workflow loading; NAT-managed local model and tool invocations.
- Existing Squipi schema validation and allow-list, cleaning, analysis, charts and Office exports.
- A trial Excel exporter that writes literal text cells (including formula-like source text).
- Exact conversation/tool-call checkpoints, plus a journal committed before/after each tool call.
- Successful call reuse keyed by validated arguments; output checksums are verified on resume.
- Pinned Office templates and original objective across processes; downstream tools reject the raw
  dataset ID after a cleaned copy has been produced.
- Full tool results on disk, with paginated retrieval for long model-visible results.
- Single-writer locking and a conservative `needs_review` state for uncertain in-flight writes,
  changed/missing saved inputs, templates or outputs. No silent rerun in these cases.

The allowed tools contain no network search, shell execution, arbitrary Python, reminders or
template-library mutation. Only a loopback model endpoint is accepted; cloud-named models are
rejected. Choose a genuinely local model/server: a custom loopback proxy can itself forward
requests. Cloud authorization and the native app's user-instruction editor are not connected.
No external tracing exporter is configured; task state and tool results remain on disk.

## Limits and evaluation

This is an integration experiment, not a claim that every research request now succeeds.
The journal is separate from the installed app. There is no native NeMo selector yet.
Completion checks verify required files exist; independent review must still check content,
PII coverage and whether the analysis answers the question. Existing PII heuristics are not a
guarantee of anonymization. The exact tool call is cached, not every semantically equivalent
wording. Uncertain writes require manual inspection; automatic reconciliation is future work.

The existing `export_document`/`report_dataset` template route still reuses styles/layouts only.
For actual source-slide designs the trial also exposes `begin_template_presentation`,
`inspect_presentation_draft`, `edit_presentation_slide` and `finalize_template_presentation`.
These preserve selected source slides, edit identified text/table slots, and compute editable
histogram/category-count charts from allowed datasets. Every edit is an immutable revision;
stale revisions and unfinished bracketed prompts are rejected. Source notes/comments are removed.
Original slide artwork and fixed branding are retained intentionally; other unmarked old text must
be identified and replaced by the caller. This is not automatic interpretation of arbitrary designs.

Finalized template drafts explicitly require rendered visual review. The agent reports `needs_review`
instead of claiming completion; the prototype does not yet contain its own renderer/reviewer.
The installed app's tool routing and UI have not been wired to this path. Older tool-result compaction
does not guarantee that arbitrarily long user conversations fit every model's context window.

The controlled integration tests use real NAT and real worker tools with a scripted loopback
model. They cover process-boundary continuation, a real response timeout, artifact reuse and
template/data lineage. These establish execution behaviour, not model reasoning quality.
Run them with `python -m pytest tests -q` after installing `pytest` in the trial environment.

Official references:
- [NAT installation and supported platforms](https://docs.nvidia.com/nemo/agent-toolkit/latest/get-started/installation.html)
- [NAT custom functions](https://docs.nvidia.com/nemo/agent-toolkit/latest/extend/custom-components/custom-functions/functions.html)
- [Local model endpoints](https://docs.nvidia.com/nemo/agent-toolkit/latest/build-workflows/llms/using-local-llms.html)
