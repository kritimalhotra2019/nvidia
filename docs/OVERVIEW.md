# Squipi overview

Squipi is a macOS desktop research companion with an animated character, a native agent harness and local research tools. The user attaches files or grants folder access and provides an objective. The model proposes actions; the host validates permissions and arguments, the worker computes results, and the app records progress and presents outputs.

## Source map

| Component | Location | Responsibility |
|---|---|---|
| Native app and harness | `Sources/Squipi/` | Conversation, task continuity, approvals, provider routing, memory, speech/meetings, reminders and Mac integration |
| Research worker | `worker/squipi_worker/` | Data/document tools, statistics, charts, exports, source lineage, research records and playbooks |
| Kumo adapters | `worker/squipi_kumo_forecast.py`, `worker/squipi_kumo_predict.py` | Isolated local specialist inference and explicit model/runtime configuration |
| Character | `web/src/` | Three.js character, appearance, clothing and work/reading animations |
| Personality | `Resources/personality.md` | Bundled identity and capability guidance; host permissions remain authoritative |
| Setup and distribution | `build.sh`, `scripts/`, `distribution/` | Source build, optional MLX setup and development packaging |
| Verification | `tests/`, `worker/tests/` | Native policy checks, controlled model peers and worker regressions |
| Archived experiments | `experiments/` | Retained development work, not additional supported UI harness choices |

## Runtime choices

Squipi's own harness is the production orchestration layer. Qwen through Ollama runs locally; MLX is another local, experimental backend. Nebius provides approved cloud inference. These modes share local task records and research memory. Kumo Forecast, Relational and Tabular Small are specialist predictive models, not chat models; the direct workbench does not require a conversational model to call them.

The app offers local Apple speech/meeting processing, a Dock icon, appearance choices, user-editable instructions, memory and personality, saved templates and research tools. Availability of optional libraries, model downloads, operating-system features and external services must be checked on the target machine. The existence of a tool or playbook is not a guarantee that every scientific use is valid.

## Privacy and persistence

Project data, saved task records and personal instructions live on the Mac. Nebius can receive approved context and subsequent tool results. Online search/retrieval sends requests to external services under its separate approval boundary. Local mode does not automatically make an explicitly requested network tool local. Credentials belong in the app's secure settings, not the repository.

Saved objectives, checkpoints and verified tool results support user-directed continuation. Learning records reusable procedures with evidence and reversible versions; it does not retrain model weights. Research conclusions remain subject to researcher review.

## Start here

- [README](../README.md): prerequisites, build, setup and feature use.
- [Verification and limitations](VERIFICATION.md): what development checks establish and what remains unqualified.
- [Kumo Forecast](KUMO_FORECAST.md) and [Kumo Prediction](KUMO_PREDICTION.md): specialist contracts and configuration.
- [Meeting status](NEMOTRON-TRANSCRIBE.md): Apple-only current path and deferred speech API prototype.
- [MIT license](../LICENSE) and [attribution](../NOTICE.md): licensing and upstream provenance. Dependencies and model weights retain their own terms.

This public source package contains no model weights, API keys, personal memories or research datasets. It is a development source release, not a notarized installer.
