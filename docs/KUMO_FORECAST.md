# Optional NVIDIA Kumo Forecast

This is an isolated experimental forecasting adapter, separate from the main worker and research model. It does not alter the research loop. No forecast is available until explicit runtime setup succeeds. `forecast_capabilities` checks setup files without loading a model; it is not a quality qualification.

## Direct use in Squipi

Open Me → Brain → NVIDIA Kumo Forecast. Import or explicitly choose a dataset,
select the time and numeric value columns, optionally group separate series,
and choose a horizon before pressing Run forecast. The direct form does not
call Qwen or Nebius. Importing only registers the file. Results offer saved CSV
and chart buttons, with an optional Word summary generated from the same results.

## Native integration contract

- Tool `forecast_series`: required `dataset_id`, `time_column`, `target_column`, `horizon` (1–72); optional `group_column`, `frequency` (`auto` or a pandas calendar frequency), `units`, `title`, plain CSV `filename`.
- The host obtains an exclusive model lease, pauses other model requests and unloads local LLMs. It sends **top-level IPC** `exclusive_model: "kumo"` alongside `method: "call"`, outside model-generated `params.args`. Without the lease marker, the tool refuses before inference. This marker is an assertion by the native host, not a substitute for native concurrency enforcement.
- The worker spawns `squipi_kumo_forecast.py` using the isolated configured Python. It exits before the LLM continues; cancellation terminates the owned process. A 15-minute subprocess safety ceiling reports failure and never marks incomplete output successful.
- Config lives at `~/Library/Application Support/Squipi/kumo/config.json`; a host-provided `SQUIPI_KUMO_CONFIG` environment variable can point to an isolated test config.
- Config requires `enabled: true`, `backend: "kumo-ts"`, absolute `python`, `package_path`, `checkpoint_path`, `backbone_path`, and a `device` (`cpu`, `mps`, or `cuda`). Setup adds provenance and hash fields. CPU is the conservative initial default; MPS is not claimed verified without a real run.

## Explicit setup

Run `bash worker/bootstrap-kumo.sh "<Squipi application-support directory>" "<bundled uv executable>"` from a native host action. It never runs a forecast. It uses an isolated `kumo/env`, `kumo/source`, `kumo/models` and caches; existing worker/Ollama/MLX installations are unchanged.

The reviewed SDK revision is `NVIDIA/Kumo-TS@af74798bbf7d020d3298f545fb9be383560a18ce`, distribution `kumo-forecast==1.0.0`. `uv sync --frozen --no-dev` uses its lockfile and the existing Python 3.12 interpreter. Only these three files are downloaded, never whole model repositories:

| Repository / revision | File | Bytes |
|---|---|---:|
| `nvidia/Kumo-Forecast@402caa11b7ff585141e912c6743026141f648892` | `moment_head_512_6hr.pt` | 1,404,422,626 |
| `AutonLab/MOMENT-1-large@ca58581bc7bea2ebed4e80dc0a3e4b8b609c6ecc` | `model.safetensors` | 1,385,501,256 |
| same MOMENT revision | `config.json` | 951 |

Total model files: **2,789,924,833 bytes**, plus the isolated Python dependencies. The newer 1.2 checkpoint is not silently substituted into this older SDK interface. Setup verifies exact lengths and checkpoint SHA256s, writes a receipt, and configures only after all files succeed. Inference sets Hugging Face/Transformers offline flags, uses local paths, and denies IP socket connections in the child process.

## Research contract and limits

Each series needs at least 512 complete regular observations. Up to eight series run sequentially. The latest 512 observations are passed to the direct model; the tool never pads, silently imputes, samples, aggregates duplicates, or switches to a naive model. Explicit date/time values are required. Missing groups, targets, dates, duplicate times and irregular grids fail with a concrete message. Unsorted valid data is sorted in memory, with originals preserved.

Calendar frequency is validated against every timestamp. Monthly dates are produced with calendar offsets, not an assumed 30-day interval. The SDK receives regular position timestamps because this direct model operates on ordered numeric observations; the host adapter restores the separately validated calendar dates. Timezone-aware and mixed-zone inputs require explicit normalisation first.

Outputs include a CSV dataset, SVG and PNG plots, model/source provenance, and a reusable analysis ID. `write_research_results_brief` can consume that ID to produce Word with exact summary table values and charts.

These are **point forecasts**, not calibrated confidence/prediction intervals and not causal impact estimates. No backtest runs automatically. A last-observation baseline is displayed for context; it is not a claim that Kumo outperforms it. Forecasts can violate domain bounds and need researcher review.

## Verification

`worker/tests/test_kumo_forecast_adapter.py` exercises validation, calendar dates, group separation, exclusive-access gating, and saved CSV/chart/Word integration with an explicitly stubbed model response. It does **not** qualify actual Kumo inference, performance or MPS support.

Official references: [Kumo-TS](https://github.com/NVIDIA/Kumo-TS), [pinned ForecastingConfig](https://github.com/NVIDIA/Kumo-TS/blob/af74798bbf7d020d3298f545fb9be383560a18ce/Kumo-Forecast/sdk/forecasting.py), [Kumo model card](https://huggingface.co/nvidia/Kumo-Forecast).
