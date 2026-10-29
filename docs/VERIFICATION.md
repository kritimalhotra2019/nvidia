# Verification and limitations

This is a development source release for inspection. It is not a security certification, a guarantee of scientific correctness, or a claim that every research task completes on every backend. Historical checks below were performed during development; they are not a new clean-machine acceptance run of this source archive.

## What has been checked

- The native Swift harness and real Python worker have controlled integration fixtures for Ollama and MLX. Scripted model peers exercise tool execution, invalid/truncated/empty responses, argument repair, selected-source handling, continuation, approval boundaries and real spreadsheet/Word exports. These test the host protocol and do not measure live Qwen reasoning.
- Worker tests cover data validation, cleaning, joins/cardinality, descriptive statistics, identifiers and denominators, document paging, source lineage, saved calculations, chart/report outputs and cache invalidation. Tests use generated or inline fixtures; private research datasets are not included.
- Native regression checks cover durable objectives, source selection, conversation checkpoints, reminders, local-file permissions, cloud approvals and experience-learning controls. A successful artifact-existence check does not establish that an interpretation is useful or causally identified.
- Live local Qwen and Nebius runs have produced research outputs during development. Results vary by task and provider. The 29 October saved Nebius evaluation had usable outputs but did not earn a clean four-case acceptance: it retained omissions, a partial report task and quote-formatting defects. This release makes no blanket end-to-end pass claim.
- Development builds have passed local ad-hoc signature checks. That is distinct from Developer ID signing, notarization and clean-machine installation.

## Kumo verification on 29 October 2026

One real CPU Forecast integration used a synthetic 600-row daily series, passed the most recent 512 observations to the model and produced 14 finite future values with the expected dates. Source and holdout files remained unchanged. CSV, SVG/PNG and Word outputs were produced, and Word table values matched saved results. This was a worker-level integration, not GUI acceptance. In that synthetic holdout, Kumo was less accurate than both last-value and weekly-seasonal naive baselines. Forecast remains experimental; this check does not show general predictive superiority, calibrated intervals, causal effects or MPS qualification.

Four tiny real CPU integrations exercised classification and regression with each of Kumo Tabular Small and Kumo Relational. They checked query IDs/order, source preservation, lineage, finite outputs, classification probability sums and Word table cells. These establish execution/output contracts, not held-out predictive quality or native GUI acceptance. See [Forecast](KUMO_FORECAST.md) and [Prediction](KUMO_PREDICTION.md) for inputs and limits.

## Meetings, speech and desktop controls

Apple Speech and Apple Intelligence are the current meeting path. Controlled meeting fixtures test transcript persistence, cancellation/failure handling and stale cloud preferences. They do not substitute for live microphone/audio quality testing. No active NVIDIA Transcribe API feature or individual-speaker diarization is claimed. Reminders require the application to be running.

Folder access and Mac UI control require explicit grants/approvals and macOS permissions. UI-control fixtures do not establish compatibility with every application. Terminal/security targets and arbitrary shell execution are excluded from this control path.

## Known limitations

- Model speed, memory pressure, backend availability, tool choice, source relevance and substantive research judgement remain variable. Partial outputs must be reviewed.
- Complex PowerPoint templates can require repair and rendered visual inspection. Text/metadata checks cannot guarantee visual fidelity.
- Numeric and citation guards reduce some failures but do not independently certify every number, source or causal claim. Prediction and descriptive association are not impact identification.
- Resume restores saved context and verified outputs but is not an exactly-once guarantee across all crashes. No unattended general research scheduler is promised.
- MLX is experimental. English is the qualified interface/speech language; other languages are not offered as supported options.
- Network tools and optional services require their own connectivity and credentials. No scraping provider guarantees access to every site.
- Source builds require the toolchain described in the README. Model weights and environments are separate downloads. There is no bundled notarized binary in this repository.

## Running checks

Follow the [README](../README.md) to build and install test dependencies. Worker tests are in `worker/tests/`; native policy checks and controlled integration scripts are in `tests/`. Some integrations require a built native app, an explicit interpreter or separately configured live backend. Inspect each script's options before running it. Do not run live provider tests with sensitive data or credentials in logs.

Private task histories, research findings, local installation paths and rollback records are deliberately absent from this public summary. The source release preserves the regression fixtures without distributing personal project state.
