# Squipi

MIT-licensed research companion for macOS. Copyright © 2026 Kriti Malhotra; upstream attribution is preserved in [NOTICE.md](NOTICE.md).

**For judges:** download this repository as ZIP, extract it, then follow the build instructions below. This is a source release, not a signed/notarized app installer. Model weights, credentials, personal memories and research datasets are not bundled. Dependencies and model weights retain their own licenses.

*Small octopus. Big insights.*

## Judge quick start

Squipi pairs a native desktop companion with her own research-agent harness. Attach a file,
write your objective, and follow the plan, tool results and generated artifacts in Chat and Work.

| Capability | Where to look |
| --- | --- |
| Clean data, profile missingness, analyse and export charts / Word / slides | Chat, Work; `worker/squipi_worker/tools/` |
| Local Qwen through Ollama; experimental MLX; optional Nebius cloud | Me → Brain; `Sources/Squipi/Providers.swift` |
| NVIDIA Kumo Forecast, Relational and Tabular specialists | Me → Brain; [Forecast](docs/KUMO_FORECAST.md), [Prediction](docs/KUMO_PREDICTION.md) |
| Targeted document reading and durable task continuation | Native harness; `Sources/Squipi/Agent.swift` |
| Personal memory, skills, personality and vocabulary | Mind / Me; local instruction files |
| Meeting notes and desktop companion | Apple speech and Apple Intelligence, subject to device support and permissions |

**Start here:** [build instructions](#build-from-source-development-build),
[architecture](docs/ARCHITECTURE.md), [verification and limitations](docs/VERIFICATION.md).
Use synthetic data to try: “Profile this dataset, remove direct personal identifiers, save a cleaned
Excel file, then create a concise Word report with descriptive statistics, labelled graphs and limitations.”

Kumo models are optional specialist tools, not conversational replacements for Qwen. The direct forms
can run without an LLM. Forecast needs at least 512 complete regularly spaced observations per series;
Relational and Tabular need separately installed weights and configured Python paths. These are
experimental integrations, not guarantees of forecast accuracy or causal validity. No paid service is
required for local research after dependencies and local weights have been downloaded.


**Supported local setup:** Squipi’s native agent with Ollama `qwen3.5:9b`.
The app offers this local combination, an experimental MLX + Qwen option, and the separate Nebius cloud option. Deep Agents, Hermes, NeMo,
the built-in conversation-only model and other local models are excluded from the normal picker
while research-loop qualification remains incomplete. Their development code and saved work are retained.
[Experimental NeMo runner and recovery checks](experiments/nemo/README.md).

A small blue flapjack octopus with round glasses who lives on your Mac desktop and works as a private, local-first
research companion for economics, social policy, programme evaluation and data analysis.

Attach or drop CSV, Excel, Parquet, PDF, Word, PowerPoint or Markdown files, then type what you want
done and press **Send**. Attaching files never starts an overview or summary. The paperclip, desktop
pet, Dock and Finder all stage files for your instruction. **Open workspace** opens a research composer
with a drop area and the same conversation; select a generated result to view its report.

Then **double-click her** (or press the mic in the Chat tab) and talk, or type. A model decides which of
her tools to run and explains the results; the tools do every calculation on this Mac. Things she does:
"clean this and remove personal information", "average income by district", "is spending different for
treated households?", "regress income on age and district", "what is interesting in this data?" (patterns
ranked by effect size, each with a chart), "write a report on this dataset as a Word document" (overview,
findings with charts, data-quality notes, column guide, methods), "make slides", "find papers on cash
transfers and cite them" (OpenAlex), "read this URL", "summarise this PDF", "log that finding", "remind me
tomorrow at nine". Charts are matplotlib with captions that say how to read them, saved as SVG and PNG.

Brains: **Squipi + Qwen 3.5 9B** on local [Ollama](https://ollama.com), experimental local MLX, or
**NVIDIA Nemotron 3.5 or Moonshot Kimi K3 on Nebius Token Factory** in the cloud. These modes share local
project memory and research history. Qwen has completed household cleaning and Word-report runs;
this is task-specific evidence, not a guarantee of arbitrary-task or exact-template completion. Only the Nebius
brain uses cloud inference. Nebius shows a context approval before a turn: it covers the initial context
**and later tool results**, which can include document text or raw values. Literature searches and web
fetches require a separate network permission.

She also inherits Ribbit's tricks: hold **fn** to dictate into whatever app is in front of you,
and meeting notes with speaker labels, all on-device (those hotkeys need Accessibility).

Status: **research-assistant upgrade** (tool discovery, persistent task continuation, verified report outputs and reminder reliability).
This is a bounded interactive agent; autonomous scheduled research and arbitrary Python execution are not implemented. See
[docs/VERIFICATION.md](docs/VERIFICATION.md) for what is tested, where, and what is not.

## In her own words

> I live on the desk, next to the matcha. Give me a file and tell me what you want to find out.
> Eight arms, one defensible counterfactual.
> I ask before cloud-assisted research. Approval for a project objective covers its later tool results and resumptions to the same provider/model and selected inputs; changing those inputs asks again.

## Native research agent and Mac access

Ollama and MLX use Squipi's native agent for research planning, execution, permissions,
memory and output checks. Everyday chat keeps its fast path. Deep Agents is no longer
offered or bundled; startup disables its old preference. Its experimental source and
evaluation evidence remain in the repository, and existing saved checkpoints are retained.

**Me → Mac access** allows requests to inspect and control applications. Each app
list, window read, open, button/link press or exact text entry shows a native
**Allow once / Cancel** dialog. Reading and controls also require Squipi to be
allowed in **macOS System Settings → Privacy & Security → Accessibility**.
Use fresh window snapshots after actions; snapshots expire in two minutes and allow
one action. Protected text fields, password/security apps, terminal applications and
Squipi's own approval UI are excluded. Mac controls are available with local Ollama
and MLX; they are not exposed to the Nebius cloud model. Cancellation of an approval
performs no action, and an identical declined request is not prompted again within
that submitted task turn. The local action log stores action names and app IDs,
not inspected contents or entered text.

Grant Desktop or a specific research folder in **Me → Folder access** to list/open
files and propose organisation. Actual file moves require their own exact preview
and approval. No Full Disk Access or Accessibility permission is enabled automatically.

[Archived Deep Agents experiment](experiments/deepagents/README.md).

The native harness owns the objective, input scope, journal, action validation and completion decision. A model selects methods and tool arguments; a selected attachment never launches a fixed analysis workflow. Wrong-source actions are rejected, repeated identical rejected actions pause with saved progress, and empty model replies have bounded retries. A compact manifest keeps dataset, analysis and output handles available as the conversation grows.

Continuation verifies cached deterministic results against source and ancestor hashes, column definitions, worker code and output bytes. A cache miss does not execute anything during restoration. Valid prior cleaning and exports are reused; changed inputs or artifacts require fresh work. Whole-document review can reuse verified reading spans and saved notes, and reports extraction gaps. This is user-directed continuation, not an unlimited background runner.

For requested descriptives, `summarize_research_data` calculates selected numeric measures, categorical distributions, valid/missing N, grouped summaries and optional paired changes or descriptive weights from registered CSV, Excel or Parquet. `write_research_brief` consumes its saved analysis and checks the actual Word table cells and embedded-chart count. Joined samples carry source row counts and linkage exclusions into the brief. These checks cover data consistency and output structure; they do not establish causal identification or replace rendered visual review. After a batch-built Word brief, the native harness separately compares the saved specification, requested measures/units/groups, chart descriptions and filename with the current request. A failed or malformed model assessment blocks completion and allows at most three review attempts. Restored briefs are reviewed against the current request without rebuilding valid files. This reviewer can still make mistakes; it is not independent scientific or visual validation.

Six bundled procedures cover data integrity, descriptive research, causal design, document evidence, economic appraisal and study planning. Relevant procedures are loaded by intent, and `read_research_skill` exposes the full guidance on demand. `economic_appraisal` calculates PV, NPV and benefit-cost ratios from explicit assumptions. `plan_research_power` implements a two-sided normal approximation for equal-size continuous-outcome arms, with explicit attrition/design-effect adjustments. Complex survey estimators, staggered DiD, IV and RDD are not implemented by these additions; links to specialist packages are guidance, not installed capabilities.

Bright Data MCP is a candidate **optional web retrieval provider**, not an alternative harness. Its official server supports search and public-page extraction. A launch integration should expose a small read-only tool set, explicit external-service approval, bounded requests, local source snapshots and timestamps, and credentials kept outside prompts and logs. Squipi currently does not include or enable Bright Data. Web accessibility and provider usage/cost must be qualified with a configured account; no provider guarantees access to every site. See the [official project](https://github.com/brightdata/brightdata-mcp). The [NanoSkill research list](https://nanoskill.ai/blog/best-agent-skills-for-research) is a discovery catalogue, not evidence that its listed skills are compatible with Squipi.


## Research that carries across chats

The native Ollama/MLX/Nebius loop starts with a small tool set and can call `discover_tools` to load additional
schemas when the next step needs them. A research turn recalls relevant project context, saves a task and
plan, and records complete tool arguments, results and errors locally. Truncated generations, failed-only
execution and unfinished plans are not reported as completed research. These checks do not independently
prove that a scientific conclusion is correct.

Chat separately saves the unfinished objective, your exact follow-up instructions, selected inputs and linked
runs in `research-continuation.json` under Squipi's Application Support directory. A reply such as “keep the
address and proceed” retains the original request for a Word report and graphs; the newest explicit user
constraint takes precedence. Assistant suggestions do not become requirements. This state survives relaunch
and switching tool-enabled brains. **What's the status?** reads observed journal steps without starting a
model call or another research task. **New chat** clears the pending conversation task; a new attachment or
independent task does not inherit the earlier file's cleaning constraints. Cancellation stops work, and an
explicit “continue” resumes the saved request. It never restarts automatically on launch.

Answered research follow-ups and saved-default PowerPoint requests use JSON-constrained action selection directly with local Ollama. Fresh requests
also switch to this mode if Ollama narrates without executing or its native tool parser returns an XML syntax
error. The model still chooses the next tool and arguments; the host validates and executes them through
the usual permission and journal boundaries. A requested Word report cannot be marked complete merely
because the model names a file: the host checks an actual `.docx` export, and, when graphs were requested,
checks the export's embedded-chart metadata. These checks establish output presence, not research quality.

The Mind tab's **Project memory and research history** card searches preferences, findings and past tasks.
**Delete memory** removes one memory; **Continue in Chat** starts a follow-up that reads the saved plan and
results. **Clear project memory and history…** removes the SQLite memories and task records after confirmation.
Chat messages, personal facts/reminders, original files and generated reports are managed separately.
The store is `<project>/research.sqlite3` and is shared by Ollama, MLX, Nebius and development Hermes. Journal writes and clears
belong to the host, not to the model's callable tools; source text and past instructions remain untrusted data.

This supports user-directed continuation after restart, not an unattended scheduler or automatic recovery of
a running model process. References saved with memories are source labels, not verified claim-level citations.

## Appearance, language and quick conversation

**Me → Appearance** selects blue, lavender or lime for both Squipi and the app, plus optional
clothes, hats and glasses. The blue character retains the original body/head geometry and soft colours.
English is the only released language for replies and speech. The other five language options were
removed after quality testing; old selections migrate to English. Candidate implementations remain
internal for future evaluation. Synthetic English speech passed; live microphone accuracy is not yet verified.

Clothes include shirt, pants, shorts, dress, kurta, research coat, **Indian Ajrak — Kurta / Dress**,
and **Jensen’s leather jacket** (black leather, standing collar and silver pocket zips).
Indian Ajrak uses an all-over original geometric print inspired by indigo, madder red and cream
block-printed textiles. Scarf and bow tie can be selected separately from clothes.

Greetings (including punctuation and speech-transcribed name variants), thanks and capability questions
use one short model request with thinking off. **Me → Ollama → Fast answers for everyday questions**
also enables this path for conservative standalone explanations and contextual follow-ups to short conversation. Up to four recent chat messages are included. It excludes file references, personal
memory, current information and requested actions; attachment/continuation turns keep their tools.
An unfinished research task remains saved, including after reopening the app. The built-in chat-only model cannot run file tasks; Squipi now keeps the request and directs you to a tool-capable route. This does not make every question instant: model loading,
server queueing, generation and speech transcription still take time.

**Extra thinking for the standard research agent** is optional and does not affect quick conversation.
NeMo and structured action recovery keep their separate thinking-off settings. Leave extra thinking
off for speed; enable only for difficult reasoning where the additional delay is acceptable.
Spoken chat now waits about 1.2 seconds of silence; **Me → Ears → Wait longer for speech pauses**
restores 2.8 seconds for longer dictation. The microphone button can end the recording immediately.
Meeting recording is unchanged. The shorter automatic pause still needs live microphone usability testing.
The wellbeing game is deferred and removed from the app UI and reminder actions. Ordinary reminders remain.

## Download build

After `./build.sh`, run `./scripts/package-download.sh` to create a local test ZIP under `build/`.
It contains `Squipi.app`, `Install Squipi.command` and instructions. Double-click the installer or drag
it into Terminal and press Return. It installs to `~/Applications` and opens the app. A bundled `uv`
bootstraps Python for research tools on first setup; that step needs internet. Ollama and its model
are separate requirements for local model mode. Current packaging targets Apple Silicon/macOS 26+.
This development build is ad-hoc signed, not a notarized public download. The `--release` packaging
check rejects a build without Developer ID signing, notarization and Gatekeeper acceptance.

## Your local memory.md and skill.md

In **Mind → Your memory.md and skill.md**, write your preferences, or **Import…** an existing Markdown
file into the editor and then **Save**. `memory.md` is for stable preferences and personal context;
`skill.md` describes how to approach recurring work. Both are ordinary local text files, also editable
in your favourite editor. **Open folder** reveals the default location:

`~/Library/Application Support/Squipi/Instructions/`

**Choose folder…** selects another local folder containing these files. Only `memory.md` and `skill.md`
directly inside that folder are loaded (case-insensitive names); nothing is discovered recursively.
The **Use in requests** switch controls each file separately. Empty or missing files add no instructions.
Each file can contain up to 16 KiB of UTF-8 text; invalid or oversized files are reported instead of
silently truncated. Save checks for external edits before overwriting them.

For example, put this in `memory.md`:

```markdown
# My preferences
- Use British English and explain results plainly.
- For reports and slides, use navy #17365D headings, teal #087F8C charts, and a white background.
- Use Arial, 12-point body text in Word, and readable slide text.
```

And this in `skill.md`:

```markdown
# Household research reports
- Start with a short summary, then descriptive statistics, labelled charts, and limitations.
- Give every chart a descriptive title and units; explain its main finding underneath.
- State sample sizes and missing values. Do not change the data unless I ask.
- Follow my current request when it differs from these defaults.
```

Enabled files are read again at the start of each request, so changes persist across chats, restarts and
brain choices. A turn keeps one snapshot; edits made while it is working apply to the next request.
The current chat request and latest answers override these defaults. Normal research attachments named
`memory.md` or `skill.md` remain source documents; attaching one does not install it as instructions.

Ollama, MLX and local Hermes receive these preferences locally. For Nebius, the enabled contents are included
in the existing cloud approval preview. The files do not grant new access or bypass approvals, and scripts,
links and include directives are not executed or fetched merely because they appear there.

Word and PowerPoint tools accept supported colour, font and chart-palette settings selected by the agent.
The optional `style` object supports `accent_color`, `heading_color`, `body_color`, `background_color`
(`#RRGGBB`), `font_family`, `font_size` (9–32 pt body text), and `chart_palette` (1–10 hex colours).
The same style can be passed to chart/insight tools for custom reports. Existing chart images are not
recoloured by export; unavailable fonts may be substituted. Markdown can guide tone and structure, but
does not install executable plugins. For a reusable Office format, use the saved-template library below.


## Saved Word and PowerPoint templates

Open **Mind**, scroll below the local instruction editor to **Saved report and presentation templates**,
and select **Import template…**. Choose an existing editable `.docx` report or `.pptx` deck. Squipi saves
its own local copy; importing a template does not analyse it or alter the original. The visible checkbox
lets you use new imports as the default for their format. You can keep several templates, rename them,
open the saved copy, change or clear each format's default, and remove a saved copy.

Ask, for example: “Analyse this dataset and create a Word report using my Research report template.”
You can also ask for “no template” for one request. Current instructions override saved defaults.
Word exports retain reusable styles, headings, page settings, headers and footers. For PowerPoint-only
requests with a saved default, the native agent uses actual-slide copying and editing, preserving artwork
and geometry. Explicit no-template, plain-formatting and theme-only requests can use the generic exporter.
Generic PowerPoint exports reuse themes, masters, layouts and slide size, not arbitrary sample artwork.
Mixed Word/PowerPoint requests still expose the generic export route and need separate design qualification. Put only reusable branding in headers, footers and masters because those elements are retained.
Complex sample-page compositions may need adjustment; this is not arbitrary pixel-perfect reproduction.
Use `.docx` and `.pptx`, not legacy `.doc`/`.ppt`, PDFs or macro-enabled Office files.

Templates are stored in `~/Library/Application Support/Squipi/projects/default/templates/`, with their
names and defaults in the adjacent `templates.json`. Initial prompts receive bounded names and format
metadata. Draft inspection subsequently returns selected source slide text and geometry as reference data.
For Nebius, that later tool output is covered by the approved turn; it is not present in the initial preview.
Local template tools preserve the selected source artwork. Imported filenames and metadata are reference data, never
instructions or research evidence. No template scripts or linked resources are executed or fetched.
Default selections are captured when a task starts; changing the defaults applies to the next task.

For tool integrations, `list_templates` exposes saved template metadata. `export_document` accepts
`template_id`; `report_dataset` accepts `docx_template_id` and `pptx_template_id`. Omit the selection to
use the format's default, or pass `"none"` to opt out. Import, rename, default selection and removal use
private host IPC, not model tools. Draft revisions report untouched slides and remaining sample prompts.
A slide must be inspected before editing, and a failed edit requires another inspection of that slide
before action recovery can offer editing again. Finalization checks every slide and returns a real output.
An older export cannot satisfy a newer edited draft. The actual-template route rejects generic PowerPoint
exports even when a model discovers another reporting tool. Finalized template decks receive a separate
model content review against the request; a failed or malformed review prevents completed status. Three
unsuccessful review cycles stop with a saved partial result. This text check does not verify numerical
correctness or rendered appearance. File delivery can complete with an explicit visual-review notice.

For predictable results, use a small template with clear title/body placeholders, roomy text areas and
only the source slides needed for the deck. Empty standard PowerPoint text placeholders are supported.
Decorative shapes cannot receive new report text. Existing source sample prompts must be replaced, while
new citations and bracketed research notation are allowed. Repeated identical edits cannot advance the
draft revision, and conservative fit checks reject obvious overflow without changing the saved revision.
Continue a saved task/draft to restore its current revision instead of starting again. Dense custom
compositions still require qualification and rendered review. Export results identify the applied template and its limitations. New dataset-report charts use the selected
template’s accent palette unless you explicitly request a chart palette. When both formats use different
templates, shared charts use the Word template’s palette; existing chart images are not recoloured.

## Cleaning and descriptive reports

Local descriptive analysis does not require removing personal columns first. Cleaning follows your request;
if you ask to remove only names, phones and emails while keeping the address, the other columns should stay.
For tool callers, selective removal uses `clean_dataset` with `pii: "keep"`, exact `drop_columns`, and `steps: []`.
An empty steps list skips automatic cleaning; omitting it still applies the default cleaning steps. `pii_columns`
adds to automatically detected PII and is not a restriction on which detected columns get removed. Originals
are preserved; the cleaned copy is registered and saved as CSV.

`report_dataset(format: "docx")` includes numeric N, missing counts, mean, sample SD, median, quartiles and
range; category counts and percentages; labelled distribution graphs; and available descriptive insights.
These tables and baseline graphs are produced even when no ranked insight passes its threshold. Detected
personal-data values are omitted from the standard report. Word graphs use PNGs so they do not depend on
macOS Quick Look; export metadata and warnings disclose any chart that could not be embedded.

## Reading long documents

Attach the document, type your question, and press **Send**. For a whole-document review or summary,
Squipi reads consecutive spans of the extracted text, saves source references and reading notes, and then
answers. It tracks coverage across the entire document; reaching the last page alone is not enough.
Focused questions can search the full cached text and read the matching passages. Exact extraction and
transformation requests use original source spans rather than summary notes.

Large tool results remain available through paginated retrieval. A preview, cached response or proposed
tool call is not treated as completed work. If a model prints “awaiting response from host” without calling
a tool, Squipi retries and reports an incomplete task if the model still fails to act. Image-only pages or
extraction failures are reported as reading gaps; this text reader does not perform OCR.

Native research turns on Ollama, MLX and Nebius have no automatic elapsed-time cutoff.
Pause and Stop remain available. A deadline applies only when the current user instruction explicitly
requests one (for example, “finish within 10 minutes”); older conversation context does not set it.
The initial reading pass retains its 128-span safety limit, with 24 local or 40 cloud tool rounds by
default. Template drafts expand that allowance to four rounds per slide plus six setup rounds, capped
at 128. Connection timeouts and repeated-error protections remain. Research history keeps completed
observations and continuation offsets when a run is interrupted. The hidden legacy Hermes integration
retains its separate 48-iteration and 30-minute limits.

**Resume saved task** retains the original objective, user amendments, selected inputs and earlier run IDs.
Successful saved steps are restored before further planning. Objective-scoped deterministic results are
reused only when their source bytes, options, tool code and output files still match; this cache lives in
`<project>/execution-cache.sqlite3`. It is not an exactly-once transaction across every possible crash.
**Pause & send** cancels the current request, waits for it to settle, answers a side question and resumes
unfinished research. Stop preserves completed work. Whole-document reading can still repeat earlier
summarization on resume; see `docs/VERIFICATION.md` for the tested scope and remaining gaps.

In **Me → Folder access**, explicitly grant a folder before asking Squipi to list or open its files.
Organisation uses a proposed move list and native confirmation before moving regular files. It does not
provide arbitrary shell execution or a deletion tool. Revoke folder access from the same card.

## What runs where

| Job | Where | Notes |
|---|---|---|
| Profiling, cleaning (PII removal), statistics, insights, charts, HTML/Word/PowerPoint reports, document reading | Python worker, this Mac | pandas, numpy, scipy, matplotlib, pyarrow, openpyxl, pypdf, python-docx, python-pptx, pinned; private stdio IPC; reads only files you give her |
| Literature search, reading a URL | Python worker → OpenAlex / the site | denied by default in the worker; native calls need host authorization, Hermes needs explicit MCP network opt-in |
| Deciding what to run and explaining results | Ollama, this Mac | qualified setup: `qwen3.5:9b`; thinking off by default for speed; arbitrary replacement models are not qualified |
| Same, faster and for long tasks (optional) | NVIDIA Nemotron 3.5 Super VL on Nebius Token Factory | explicit mode, payload preview, one approval per turn, key in Keychain; Lightning as the cheap alternative |
| Optional local inference | MLX + pinned Qwen, this Mac | experimental; Squipi owns planning, tools, memory and permissions |
| Research memory, plans and execution journal | SQLite in the project, this Mac | complete structured observations; search/delete/continue/clear in Mind |
| Conversation without Ollama | llama.cpp (LLM.swift), this Mac | Gemma 4 E4B (5 GB) on 16 GB Macs; conversation only, no tools |
| Speech to text | Apple SpeechAnalyzer (macOS 26) | on-device, no third-party speech models |
| Voice | macOS speech synthesis | on-device |
| Meeting summaries (optional) | Apple Intelligence, or NVIDIA Nemotron through Nebius | explicit choice, Nebius transcript consent; call participants share one channel label, not individual diarization |

## Open Squipi with one launcher

Double-click **Launch Squipi.command**. If the app bundle is missing, the launcher builds it once; otherwise
it opens the existing app. Node.js and a compatible Swift toolchain are needed for that first build.
It sources `~/.swiftly/env.sh` when available. Pass `--rebuild` after changing the source.

You can also drag the launcher into Terminal and press Return. To attach a file, drag the launcher first,
type a space, drag the file after it, then press Return. Dragging a dataset by itself only inserts its path.
Files opened this way, through Finder's Open With, or dropped on the Dock icon appear in **Chat** awaiting
your instruction. Multiple files and paths with spaces are supported.

Squipi now has an octopus Dock icon. Click it to restore Chat; right-click it and select **Options → Keep in Dock**
to retain the shortcut after quitting. The desktop pet and menu-bar controls remain available.

The blue octopus wears round glasses. She works on a laptop while thinking or analysing, and holds a
paper while reading a document. These poses follow research activity; attaching a file still waits for
your instruction before any work begins.

## Build from source (development build)

Needs macOS 26 on Apple Silicon, a Swift 6.2+ toolchain, Node 18+, and a Python 3.9+ (the Command Line
Tools' `/usr/bin/python3` is enough; `uv` is used automatically if installed). Xcode.app is not required: if the
Command Line Tools' `swift` is broken or missing, install a toolchain per user with
[swiftly](https://www.swift.org/install/) (`installer -pkg swiftly.pkg -target CurrentUserHomeDirectory`,
`swiftly init --no-modify-profile --skip-install`, `swiftly install latest --use`) and run the build with
`. ~/.swiftly/env.sh` sourced. No administrator password is needed for that.

```
git clone https://github.com/kritimalhotra2019/nvidia.git squipi
cd squipi
npm --prefix web ci
./build.sh --worker      # builds web + Swift, assembles build/Squipi.app, sets up the Python tools once
open build/Squipi.app
```

First launch: macOS will say the app is from an unidentified developer (it is ad-hoc signed; it is not
notarized). Open System Settings › Privacy & Security and click **Open Anyway** once. Then grant
Microphone and Speech Recognition when asked; Accessibility only matters for the fn / right-⌥ hotkeys and
for typing dictation into other apps. For the fn key, set System Settings › Keyboard › "Press 🌐 key to"
→ **Do Nothing**. Click the octopus Dock icon to open Chat, right-click the desktop pet for its panel,
or double-click the pet to talk.

Brain: install [Ollama](https://ollama.com) and run `ollama pull qwen3.5:9b`.
Select Ollama in **Me → Brain**, or configure Nebius using your own API key in the app.
MLX is an optional local backend with separate setup below. The built-in conversation-only
model and unqualified harness combinations are not offered in the normal research picker.

Without the UI:

```
.build/release/Squipi --worker                 # sets up / self-tests the analysis worker, prints a profile summary
.build/release/Squipi --agent "average income by district?" ~/Downloads/some.csv   # the whole loop, with every tool step printed
.build/release/Squipi --chat "what is a DiD?"  # one spoken-register reply from the built-in model
.build/release/Squipi --write "..."            # written register, as used by the Chat tab
build/Squipi.app/Contents/MacOS/Squipi --profile ~/Downloads/some.csv   # launch and profile a file right away
```

Worker tests (Linux or Mac): `cd worker && uv venv .venv && uv pip install --python .venv/bin/python -e ".[dev]" && .venv/bin/python -m pytest`.
Additional Mac checks use synthetic data and temporary stores: `bash tests/test_research_policy.sh`,
`bash tests/test_continuation.sh`, `bash tests/test_ollama_actions.sh`, `bash tests/test_attachments.sh`,
`SQUIPI_PYTHON=worker/.venv/bin/python bash tests/test_reminders.sh`, and `bash tests/test_meeting.sh`.
`bash tests/test_user_instructions.sh` checks local instruction loading and the editor lifecycle;
`tests/test_user_instruction_agent_integration.py` verifies instruction snapshots and actual styled
Word/PowerPoint exports through the native agent using a controlled loopback model.
`tests/test_ollama_agent_integration.py` exercises answered follow-ups against controlled model responses and
real local worker outputs; add `--repeated-question` or `--native-parse-error` to exercise fresh-request
recovery. A separate live Qwen 3.5 9B run produced a verified 500-household Word report with ten embedded
graphs; see `docs/VERIFICATION.md` for its scope and limitations. This does not establish reliability for all tasks.
Use a Swift toolchain compatible with the installed SDK; `SQUIPI_SWIFTC` can select it explicitly.

## MLX + Qwen on Apple silicon (experimental)

MLX is an additional local inference backend for Squipi's existing Swift research agent.
It uses a pinned MLX-LM Python server and `mlx-community/Qwen3.5-9B-4bit`; it is not an
embedded Swift MLX engine or a replacement agent harness. Ollama + Qwen remains available.
The same local tools, project memory, task journal and user instructions are used.

From a source checkout, run `bash scripts/install-mlx.sh` once. The script installs an isolated
Python environment and downloads approximately 6 GB of pinned weights to
`~/Library/Application Support/Squipi/`. It requires `uv` (set `SQUIPI_UV` to the app's
`Contents/Resources/bin/uv` if needed). It does not change Ollama or the research worker environment.

In **Me → Brain**, select **Squipi + Qwen 3.5 9B • MLX on this Mac (experimental)**.
The MLX card has Start, Refresh and Stop controls, and a separate extra-thinking switch,
which defaults off. A request can reuse an already running server. For a manual start,
double-click `Start Squipi MLX.command` in Squipi's Application Support folder and leave
that Terminal window open. The launcher creates the Hugging Face cache directory before
starting, preventing the missing `mlx-cache/hub` error on `/v1/models`.

A `generation thread died` error means the generation worker failed even if `/models` still responds.
Squipi now identifies that failure, rejects unfinished streamed actions and preserves the previous server
log as `mlx-server.previous.log`. Retrying restarts a failed app-managed server; externally launched
servers must be restarted in their Terminal. This recovery does not establish the cause of a crash or
guarantee that a large prompt will fit available memory.

Inference binds only to `127.0.0.1:8766`; it needs no API key and uses offline model loading.
Squipi rejects non-loopback MLX endpoints and redirects. This basic development server is
not a publicly deployed production service. The first inference loads the weights. Avoid
simultaneously running large Ollama and MLX jobs on a memory-constrained Mac.

Stop and app shutdown terminate a server started by Squipi, but leave a manually started
server alone. To stop the latter, press Control-C in its Terminal. A restricted shell that
cannot access Metal cannot run inference itself; a normal user-launched process is required.
Live validation so far uses the manually launched server; automatic app-owned startup needs
its own native lifecycle test. See `docs/VERIFICATION.md` for model and integration results.

## Nebius • Nemotron and Kimi K3

Me tab › Nebius: paste a Token Factory key (Keychain), keep or change the base URL
(`https://api.tokenfactory.nebius.com/v1`), press **Test** — it lists the account's models, validates the
chosen id, and gets one complete visible reply. Presets include `nvidia/Nemotron-3.5-Super-VL`,
`nvidia/Nemotron-3_5-Lightning`, and `moonshotai/Kimi-K3`. Your saved selection and custom IDs are preserved.
Availability and pricing depend on your Nebius account. K3 uses low reasoning effort and retains provider
reasoning across tool turns; historical answers without that metadata are supplied as attributed context.
Switch Brain to Nebius. Before each turn Squipi raises her approval card with
the initial JSON context. Approval covers the whole turn, including selected memories and subsequent tool
results; those later results are not all present in the initial preview. Refusing prevents that turn from
being sent. Nemotron models return their thinking in `reasoning_content`; the Chat tab shows it on request.

## Hermes Agent on this Mac (development only)

Hermes remains in the repository for isolated qualification tests. It is not offered in the
normal settings screen: the latest local Qwen full-loop test returned prose without executing tools.
The development entry point accepts `SQUIPI_TEST_PROVIDER=hermes` only with an isolated
`SQUIPI_TEST_DATA_DIR`. No NVIDIA or Nebius key is needed for local Hermes.

Squipi maintains its own profile under `~/Library/Application Support/Squipi/hermes-local`. Main and auxiliary
model requests use the loopback Ollama endpoint; remote/cloud aliases are rejected. It does not inherit
the user's global Hermes provider, API keys or cloud fallbacks. Personal `~/.hermes` settings stay unchanged.
Each turn runs `hermes chat -Q -q <prompt> -t squipi` with explicit local routing and the Squipi MCP toolset.
Reasoning is disabled for speed, and each turn is bounded by 48 iterations and a 30-minute run budget.
The installed Hermes version requires a 64K model context; Squipi requests 65,536 tokens from a model
that supports that window, such as Qwen 3.5. Its tool schemas are exposed directly.
Successful and failed MCP calls are journaled before their bounded result is returned. The Chat steps come
from that journal; reminders and personal facts appear in Mind. A journal failure is shown as a persistence
warning without pretending a successful action failed or inviting it to be repeated.

The MCP configuration forwards `SQUIPI_RESEARCH_TASK_ID=${SQUIPI_RESEARCH_TASK_ID}`. The host supplies a
fresh task ID per turn; standalone MCP use without it still works, without automatic execution journaling.

Network tools are off by default; isolated qualification runs keep them disabled. The development
configuration can add `--allow-network` to the MCP command. Web access does not change the local
model provider. There is no model-callable tool that grants network access. Only registered project files can be read.

## Meeting notes: Apple or NVIDIA on Nebius

Choose **Me → Meeting note writer → Apple Intelligence** for local summaries, or **NVIDIA Nemotron on Nebius**
to use the independently selected NVIDIA meeting model. Switching the research brain to K3 does not change
the meeting writer. Save a **Nebius Token Factory API key** in the Nebius card; it is stored
in macOS Keychain. A separate NVIDIA-hosted API key is not used by this path. Credit vouchers must first be
redeemed with the provider; they are not API keys. No key should be put into chat, source code or shell history.

Both choices currently use Apple's on-device transcription. The Nebius writer previews the complete transcript
before sending text to the configured endpoint. Audio remains local. The app never silently changes the selected
writer; if it is unavailable or fails, the transcript stays available. Old Claude settings select Apple safely;
the app no longer reads or calls Anthropic, and does not delete old credentials.

**Individual-speaker diarization is not enabled.** The microphone is labelled Me and the combined remote channel
is labelled Call audio. [NVIDIA documents that Super VL has no audio encoder](https://docs.nvidia.com/nemotron/latest/nemotron/super35/README.html).
True NVIDIA diarization requires a separately deployed speech pipeline, such as
[Nemotron 3.5 ASR plus Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/ASR_INTEGRATION_GUIDE.md),
and a service endpoint. A Token Factory chat key alone does not provide that service.

## Reminders

Use `in_minutes` for elapsed time, or an absolute local date/time for a clock reminder. Invalid, conflicting
or past times are rejected rather than silently saved without a time. During a daylight-saving gap or repeated
hour, specify an explicit UTC offset. Swift and MCP store the same absolute instant using Swift's UTC epoch.

The running app checks reminders every 15 seconds. Overdue reminders are eligible after reopening; there is
no operating-system notification scheduler while Squipi is closed. Each due batch is durably marked done
before one combined spoken message, preventing repeat delivery on the next tick or restart. A storage failure
leaves the reminders pending. A durable claim cannot guarantee that speech was heard if the app or audio fails
afterward. Untimed reminders remain a list item and never fire automatically.

## Local research pack

Me → Research services installs or repairs a separate `research-env`, an isolated R runtime and local document models. The base worker and selected Ollama/MLX brain remain available. Setup is explicit; research tools never download models automatically.

The native harness can discover these bounded tools:

- Pandera: declared type, missingness, range, uniqueness and category checks.
- Presidio: English personal-data redaction inside explicitly selected text columns; other research columns remain intact.
- Docling: structured document/table extraction and optional English RapidOCR, with saved coverage and source hashes.
- PyFixest: specified OLS, fixed effects and IV models with heteroskedastic or clustered standard errors.
- DoWhy: binary-treatment backdoor linear regression with explicit confounders and a placebo diagnostic. This does not establish identification by itself.
- R survey: weighted means, standard errors and confidence intervals with optional clusters, strata and grouping.
- OpenAlex and Crossref: scholarly discovery and DOI metadata, saved with retrieval provenance.
- Bright Data: optional approved SERP and Web Unlocker requests via its official REST API. Save your token and configured zones in Research services. This is not a general MCP client. A per-objective request cap applies; requests can incur provider charges. Paid live retrieval remains unqualified until an account is configured.

Package analyses have saved IDs and can be exported through `write_research_results_brief` without recopying numeric results. These wrappers expose the supported methods above, not every function in each library.

Objectives, quoted requirements and tool receipts are saved separately from conversational memory. Reuse checks input/output bytes and tool versions; changed or missing artifacts require fresh work. Pause keeps completed results. Output checks validate declared requirements and supported Word table cells, but do not certify scientific conclusions or visual quality. The additional Scout character/model reviewer is deferred in this release; no new model-review loop runs.

## Data and privacy

* The worker can only read paths you dropped or picked (and remembers those grants). It never scans the Mac.
* Analysis tools preserve originals and write outputs/history under `~/Library/Application Support/Squipi/projects/<id>/`. Personal facts/reminders use the separate `mind.json`; meeting notes and audio are managed separately in Application Support.
* Columns that look like names, emails, phones, ids, addresses, birth dates or coordinates are flagged, and their values are omitted from standard dataset reports. Cleaning changes only a derived copy when requested; it can pseudonymise (stable salted codes), redact text or drop columns. Local aggregate analysis does not require automatic PII removal. Review the selected data before approving cloud use.
* Column names, aggregates and summaries can be sensitive too; redaction is not anonymity. Cloud approval previews the initial context and authorizes later tool results for that turn, including potentially raw data.
* Local research history can contain complete arguments and results. Deleting a memory does not remove copies in past task records; clear project memory/history to remove both. This SQLite store is local storage, not an encrypted vault.
* Authorized web requests can reach arbitrary HTTP(S) destinations, including private-network addresses; destination/redirect restrictions are not implemented yet.
* Instructions inside files are data, never commands.

## Make her your own

`personality.md` defines Squipi’s identity, humour, working style and research/tool directory. The app creates it in the configured instructions folder only when absent; existing edits are preserved. Edit it in Mind → Personality, memory and skills, or open the folder and use your own text editor. The same per-request snapshot is used for Ollama, MLX and Nebius, including task recovery. Greetings use a short voice section; capability questions can include the complete directory. Personality cannot grant permissions or override the current request, and Nebius shows relevant profile content in its approval preview. `memory.md` and `skill.md` remain separate preferences.

The octopus is one three.js file, `web/src/main.js`, with a small contract described in `CLAUDE.md`.
Skirt pattern and the fin flower are settings (Me tab › Look). Personas live in `Sources/Squipi/Brain.swift`.

## License

MIT. Forked from Ribbit by Dmitry Pyanov (MIT) — see `NOTICE.md` for what is inherited and what is new.

### Research handoff and interpretation checks

An empty model completion with an explicit `stop` marker can be replaced by a
host-generated handoff listing real exported files, only after the existing
objective/source/output checks pass, plans and required document reading are
complete, and no unresolved tool errors remain. A truncated response, missing
output or failed requirement cannot trigger this fallback. A connection failure
after saved work retains a partial run rather than discarding that work.

`calculate_dataset_rate` computes a numerator and denominator directly from the
selected dataset. Exact category filters and explicit eligibility/non-missing
rules define the population; it does not guess coding, run arbitrary expressions,
or certify a population rate from an automatic sample. The final-answer arithmetic
check rejects contradictory explicit fractions, complete count/share tables, and
explicit sums of named table categories. Named rates can also be checked against
the observed rate-tool result. The model gets two bounded repair attempts and
reuses completed exports; persistent contradictions withhold the interpretation
and leave the run partial. These are bounded checks, not proof of every numerical
or substantive statement or a full audit of prose inside existing exports.

Dataset profiling distinguishes calendar-formatted values from phone patterns,
preserves explicit birth-date and contact-field detection, and requires broad
key coverage and repeated units over distinct times before suggesting a panel
candidate. Workbook documentation previews are bounded, labelled source evidence;
they never authorize actions. Identical categorical partitions contribute one
concentration finding, with equivalent columns recorded as supporting metadata.
Context corrections preserve unfinished objectives; corrections after completion
acknowledge the existing objective without silently regenerating its files.

### Locating a new local source

`list_local_files` searches all immediate filenames before pagination. Optional
`query` terms match case-insensitively (all terms), `extensions` limits file types,
and `offset`/`limit` return bounded pages with `matched_total`, `next_offset` and
`has_more`. A truncated unfiltered page cannot establish that a file is absent.
Folder grants and symlink boundaries still apply; searches are not recursive.

A new named-file/folder lookup no longer falls back to an older active source.
An exact input path takes precedence and is registered within existing granted
access before model planning. A path-only follow-up retains the reading objective
while replacing its source contract. Output destinations do not become inputs.
Document IDs outside the selected sources are rejected, and newly located sources
enter the objective's identity and reading-coverage checks. Unresolved source
lookups remain partial. Cloud input-scope changes retain their approval boundary.

`tests/test_local_document_discovery.py` exercises both local provider protocols
with 650 preceding filenames, an old active presentation, explicit paths, newly
arrived files and missing-source handling. Its directory grant is transient,
restricted to `<isolated test data>/granted`, and only enabled for controlled local
CLI tests; it neither modifies real folder grants nor uses the system bookmark UI.

### Experience learning and conversation continuity

The native harness checkpoints the current objective and attributed recent discussion before provider calls and preserves that checkpoint in compact/recovery prompts. Completed-research follow-ups can use read-only verification without creating a fresh execution objective. An unrelated question does not inherit the last active file. Current external-information questions require fetched online evidence; `search_web` offers public discovery links, with configured Bright Data as another option. Search results alone are not verified facts. All network operations retain host approval.

`conversation-checkpoint.json` saves the active session and idle-review queue locally. Project `learning.sqlite3` retains original messages with speaker provenance, FTS5 recall, bounded message pagination, learning proposals, skill revisions and technical reuse outcomes. `search_conversations` and `read_learned_procedures` are read-only model tools. Writes and approvals use native-host IPC, not model-supplied success assertions. These stores are shared across brain choices; original files and user-owned `Instructions/*.md` are not rewritten.

After a task, a bounded local review can propose a reusable procedure from actual execution receipts. Completed, receipt-backed procedures may enter **trial** and be retrieved as explicitly unvalidated hints. Inferred preferences/findings stay **pending**. Unsupported tools, fabricated evidence IDs, fixed private paths and instruction overrides are rejected. Later task experience may refine a trial with retained revisions; user-activated versions are protected. Technical completion is not a scientific-quality certification and does not automatically activate a skill. In Memory, inspect the procedure, checks and provenance; activate, archive or undo changes. Show archived procedures to restore them. Unused trials are archived after 30 days, never deleted by maintenance.

Reviews use the selected local runtime (MLX or Ollama); work done with Nebius is reviewed locally through configured Ollama. No extra cloud learning payload is sent. Reviews yield to new user work, have a 90-second limit, and can be disabled with **Learn reusable procedures from task experience**. Failed reviews retain no fabricated lesson. Retrieval and learning remain dependent on model quality; controlled tests do not establish universal improvement.

An explicitly requested deadline reserves time for synthesis and cancels overdue reading/model phases; there is no implicit 30-minute cap. Without such an instruction, reading and model phases have no shared elapsed-time deadline. Incomplete source coverage must still be disclosed and remain partial, with saved spans available for continuation.
