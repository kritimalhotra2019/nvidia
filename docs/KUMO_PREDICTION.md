# Kumo Relational and Tabular Small

`predict_outcome` is an optional, experimental CPU tool. With one table it uses
NVIDIA Kumo Tabular Small; with explicit linked tables it uses Kumo Relational.
Classification returns class probabilities and regression returns the model's
median. These are predictive associations, not causal effects or impact estimates.
The tool does not measure accuracy or calibrate probabilities.

## Direct use in Squipi

Open Me → Brain and click NVIDIA Kumo Relational or NVIDIA Kumo Tabular Small.
Import a CSV/Excel file or refresh and choose an existing dataset. Select the
entity ID, outcome and predictor columns; Relational additionally requires the
linked tables, join keys and selected predictors. Confirm the prepared snapshot,
then press Run prediction. Importing alone never starts inference. An optional
Word summary is assembled from the saved results. Qwen and Nebius are not required
for this path; they remain optional conversational callers of the same worker tool.
Cancel targets this form's pending operation and waits for worker acknowledgement.

## Contract

Required: `dataset_id`, `id_column`, `target_column`, `feature_columns`,
`task` (`classification` or `regression`), `data_mode` (`cross_sectional`).
Optional: `engine` (`auto`, `tabular`, `relational`), `filename`, `title`,
`linked_tables` containing `dataset_id`, `foreign_key`, and `feature_columns`.

- Known targets are labelled context; missing targets are query rows. The adapter
  never removes known labels to invent a test set.
- Base entity IDs must be unique. Context and query entities are disjoint.
- 20–1024 context rows, 1–256 query rows, 1–20 features per table; at most three
  linked tables with 4096 rows each. No automatic sampling.
- Classification: 2–10 classes, at least five context observations per class.
- A linked row belongs to exactly one base entity; unmatched keys fail explicitly.
- Features must be selected, complete, finite structured values. PII, ID-like,
  free-text, temporal, target, and key features are refused heuristically.
  Screening cannot guarantee that a column is anonymous or free of target leakage.
- The contract is deliberately limited to prepared cross-sectional snapshots.
  Direct temporal histories and repeated entity-time anchors are unsupported.
  Researchers must ensure all predictors precede the outcome. The adapter cannot
  infer feature availability from names or establish causal identification.
- Model inputs use synthetic numeric join keys, not original source IDs. The
  output CSV retains local entity IDs and original query row order. Class labels
  map to named probability columns through `class_mapping` / `class-mapping.json`.
- CSV, source hashes, all linked lineage, model revision/hash, scope, runtime and
  limitations are saved. Word briefs include scope and class mapping, while full
  per-entity results stay in CSV. No hundreds-row report dump.

## Existing local runtime, no downloads

This machine already has an isolated environment at
`~/kumo-uli/.venv` with Python 3.12.10, `structured-data-models==0.2.1`,
`torch==2.14.0`, and `pyg-lib==0.9.0+pt214`. It remains separate from the worker,
Forecast, Ollama and MLX environments. No installation is performed by prediction.

Trusted host configuration is `~/Library/Application Support/Squipi/kumo/prediction-config.json`
(or host environment `SQUIPI_KUMO_PREDICT_CONFIG`). Example:

```json
{
  "enabled": true,
  "device": "cpu",
  "python": "/Users/YOUR_USERNAME/kumo-uli/.venv/bin/python",
  "relational_path": "/Users/YOUR_USERNAME/.cache/huggingface/hub/models--nvidia--Kumo-Relational/snapshots/2bd603d3d8f25f67a7aaa8579908595e20567e22",
  "tabular_path": "/Users/YOUR_USERNAME/.cache/huggingface/hub/models--nvidia--Kumo-Tabular/snapshots/3c3e10bbdb590ace29e7026847f92db3c603096d"
}
```

Existing weights, all verified by SHA-256 before loading:

| Model/task | File | SHA-256 |
|---|---|---|
| Relational classification | classifier.pt | 0c2e35cc18303f0d3ddeaf04e9cab6ca3a8bd6ea17195ac0e42d47af4bcb2a2d |
| Relational regression | regressor.pt | f863b3632f738e1444fb83a8cea5746f298889b5c96953a000c4720d202ae4cf |
| Tabular Small classification | small/classifier.pt | 1ff91f484e19021aaf7d9eff6cc07e6d95a60b4aaad473e83e2ab2c3bfd2b617 |
| Tabular Small regression | small/regressor.pt | fc672f536501d64356036050ac2acea7e46927f7b9ad38366abe048f88e7d423 |

The child preserves the reviewed SDK's official pretrained/meta loading but
replaces that model module's checkpoint resolver with an exact local allowlist.
Only the requested task checkpoint loads. Offline environment flags, telemetry
disablement and blocked IP socket connections prevent remote fallback. CPU
threads are bounded. Parent-death watch, cancellation and a 900-second process
timeout terminate the owned child. Native host must first obtain the shared
exclusive Kumo model lease; tool arguments cannot grant it.

`prediction_capabilities` checks paths only, not inference quality. SDK metadata
must be 0.2.1. The model recipe already produces normalized probabilities; the
adapter does not apply softmax a second time. Regression `q500` is the point median,
and no calibrated prediction interval is advertised.

## Validation scope

Focused unit tests use stubbed model outputs to check validation, identity,
provenance, CSV and Word export. A separate, tiny synthetic live check for each
engine is required before claiming runtime integration works. It is not evidence
that either engine beats a simple baseline on a research dataset. Preserve the
real-data evaluation results and compare held-out predictions against appropriate
baselines before making substantive research claims.
