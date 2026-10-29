# Squipi — architecture decision (v0.1, Milestone 1)

*Small octopus. Big insights.* A private, local-first research companion for the Mac desktop.

## Decision in one paragraph

Squipi is a fork of Ribbit (`Pyanov/frog` @ `796523d05aef49fcaca794fdc7b3f6f04c7358ee`, 2026‑10‑08, MIT).
We keep Ribbit's native foundation — the transparent always‑on‑top `NSPanel` + `WKWebView`
character window, hold‑to‑talk hotkeys, Apple speech, llama.cpp brain via LLM.swift,
JSON memory, menu bar, meeting notes — and replace three things: the creature (an original
flapjack octopus), the persona, and the job (research manager instead of dictation sidekick).
We add three things: a controlled Python analysis worker behind private IPC, a typed tool
registry shared by both model paths, and a Nebius Token Factory provider with an approval
gate. Nothing from the upstream app's identity is reused: new bundle id, new storage
directory, new Keychain service, so Squipi never inherits VoicePet's permissions, memories
or keys.

## What is inherited, replaced, added

| Area | Inherited (Ribbit) | Replaced | Added |
|---|---|---|---|
| Character window | `PetPanel` (borderless, non‑activating, floating, all Spaces), cursor tracking, drag, wander | — | file drop target (`NSDraggingDestination`) |
| Character | contract `pet.setState/setLevel/lookAt/setVelocity/setTalking` | `web/src/main.js` → flapjack octopus, new states and props | `pet.setProps`, `pet.setReducedMotion`, `pet.setQuiet` |
| Local brain | LLM.swift 3.0.3 + llama.cpp, Gemma 4 E4B default on 16 GB | persona | `ModelProvider` protocol; `LocalProvider` wraps `Brain` |
| Memory | `Mind` (facts + reminders, JSON) | — | project memory lives with the worker's project dir (M3) |
| Speech | Apple SpeechAnalyzer, AVSpeech voice | voice lines; Parakeet/FluidAudio dropped (Apple on-device only) | — |
| Meetings | `MeetingRecorder`, diarization, summaries | summary engine stays Claude/Apple for now | — (M4) |
| Analysis | — | — | `worker/` Python package, JSON‑lines over stdio, tools: `import_dataset` (CSV, Excel, Parquet, Stata `.dta`, SPSS `.sav` via optional pyreadstat), `profile_dataset`, `set_column_roles`, `clean_dataset`, `aggregate`, `correlate`, `compare_groups`, `regress`, `estimate_treatment_effect`, `difference_in_differences`, `attrition_check`, `lee_bounds`, `randomized_trial`, `regression_discontinuity`, `matching_estimate`, `instrumental_variables`, `benefit_cost_analysis`, `cost_effectiveness_analysis`, `sroi_analysis`, `ex_ante_impact_projection`, `field_checks` (high‑frequency survey checks with an issues table and a base‑R rerun script; `tools/field_checks.py`), `randomize_assignment`, `pre_analysis_plan`, `create_chart`, `render_report`, `export_document`, `read_document`, `search_document` (literal or semantic via local `nomic-embed-text`), `search_library` (across all documents), `extract_document_tables`, `extract_study_evidence` (evidence matrix with quotes and offsets), `meta_analysis` (DerSimonian–Laird / fixed effect, forest plot), `code_transcripts` and `search_quotes` (qualitative coding of transcripts: local embeddings or TF‑IDF, k‑means themes or a supplied codebook, anonymised quotes; `tools/qualitative.py`), `export_replication_package` (README, steps.json, reproduce.py replaying the journal through the registry with a worker code snapshot, best‑effort reproduce.R, data/outputs copies, sha256 manifest, zip; `tools/replication.py`), `reporting_checklist` (CONSORT‑SPI / TREND / STROBE item coverage with quoted evidence and a Word checklist; `tools/reporting.py`) |
| Agent | — | — | `Agent` (model proposes function calls, worker computes, model explains; Work entries per tool run) |
| Workspace | — | — | `Workspace` panel (WKWebView) showing HTML reports and SVG charts with Open/Reveal |
| Cloud / local server | Claude key for note summaries | — | `OllamaProvider` (native API, this Mac) and `NebiusProvider` (OpenAI‑compatible, streaming, `reasoning_content`, Keychain, payload preview + approval) behind one `ChatProvider` protocol |

## Identity

* Bundle id: `com.kritimalhotra.squipi` (macOS permissions are keyed on it; do not change after the first public build).
* Storage: `~/Library/Application Support/Squipi/` — `models/`, `worker-env/`, `projects/<id>/`, `mind.json`, `notes.json`.
* Keychain service: `com.kritimalhotra.squipi` (accounts: `nebius`, `anthropic`).
* App name: Squipi. Executable target: `Squipi`.

## Model paths

* **Ollama on this Mac (default when it is running).** `OllamaProvider` talks to Ollama's native API
  (`http://localhost:11434/api/chat`): native tool calls, streaming JSON lines, a `think` switch (off by
  default; Qwen/Nemotron otherwise reason for a long time), context capped at 24,576 tokens and
  `keep_alive` 5 min by default (2–30 min in the Me tab) so the ~6 GB model is released soon after a reply. Default model `qwen3.5:9b`;
  any model with tool calling can be picked in the Me tab (Nemotron 3 Nano 4B fits; Nemotron 3.5 needs
  more than 16 GB). Nothing leaves the Mac and no approval card is shown. A Nous Research Hermes install
  was considered as the harness and rejected: the loop Squipi needs is ~200 lines, and an external agent
  runtime would bring its own tools, memory and sandbox and bypass the allow‑list and the approval gate.
* **Built‑in (LLM.swift).** Gemma 4 E4B (5 GB) through llama.cpp, conversation only, grounded in the
  last profile. It is unloaded whenever Ollama is the brain: GPU memory is shared and both do not fit.
* **Nebius • Nemotron.** OpenAI‑compatible chat completions at a configurable base URL
  (default `https://api.tokenfactory.nebius.com/v1/`), model id validated against `/models`,
  streaming, cancellation, timeouts, bounded retries, tool calls. Nemotron models on Token Factory return
  their thinking in a separate `reasoning_content` field, so the client reads both. Every cloud
  call is preceded by a payload preview; approval is scoped to a project workflow and revocable. In the
  agent loop the tool results so far are part of the payload, and the card says so.

## Tool engine

`Agent.respond` is the loop: model proposes (function call) → worker validates the arguments against the
tool's JSON schema and the allow‑list → worker executes → app checks that reported files exist → the result
goes back to the model as a `tool` message → the model answers in prose. At most six rounds per turn. Every
tool run becomes an entry in the Work tab (what, which data, result, limits, outputs) and HTML/SVG outputs
open in the workspace. The worker is the only thing that reads data files, and only paths the user dropped
or picked (the registry remembers earlier grants). It is a child process on stdin/stdout (no sockets), one job
at a time, cancellable, with progress events. Instructions found inside files are data, never authority.
The model never computes: `aggregate`, `compare_groups`, `correlate`, `regress`, `clean_dataset` return the
numbers and template‑written `interpretation` lines ("associated with", never "causes"); the model rephrases.

Documents (PDF, Word, PowerPoint, Markdown) go through `read_document` (extraction only) and are summarised in
parts by the current model (`Agent.summarize`); the report that opens is written by `render_report` from the
summary plus the document's measured structure.

Personal data: `profile_dataset` flags columns that look like names, emails, phones, national ids, addresses,
birth dates, coordinates or IPs (by name and by value pattern), never prints their values, and
`clean_dataset` can pseudonymise them (salted SHA‑256, one salt per project, stable across runs), coarsen
(birth year, 0.1° coordinates), redact them inside free text, or drop them.

Project‑local stores: `research.sqlite3` holds research memory and the task journal; `learning.sqlite3`
holds the conversation archive and experience learning (`lessons`, `outcomes`, `reviews`) plus the user
feedback store (`feedback`: a thumbs up/down per task with tags and a sanitised note, mirrored to
`feedback-cases.jsonl` as the case bank the nightly matrix replays). Feedback is written only by the
host through the `learning` IPC method, never by a model tool; a user `down` withholds a trial hint
from prompts and a second `down` archives it.

## Build order

1. **M1:** octopus + local conversation + drop a CSV → real profile/chart → open the output. Done.
2. **M2:** Ollama/Nebius provider + approval gate + model→tools→grounded result. Done (2026‑10‑16): tool
   loop, cleaning with PII handling, descriptive and inferential statistics, document reading and
   synthesis, Word/PowerPoint export.
3. **M3:** retained research/impact tools (DiD, event study, RD, IV, matching, power), project memory, Excel export. RD, IV, matching and a
   randomised‑trial tool landed 2026‑10‑27 (`worker/squipi_worker/tools/impact_designs.py`); power/MDE planning is still open.
4. **M4:** dictation, meetings, distribution polish (inherited, to be re‑verified under the new bundle id).
