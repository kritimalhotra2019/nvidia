"""Bounded, local, cross-sectional Kumo prediction; no model-generated code."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..registry import tool, InvalidArguments
from ..objectives import digest
from .research_packages import frame, result, schema, STRING, clean

MAX_CONTEXT, MAX_QUERY, MAX_LINKED_ROWS = 1024, 256, 4096
REVISIONS = {'relational': '2bd603d3d8f25f67a7aaa8579908595e20567e22',
             'tabular': '3c3e10bbdb590ace29e7026847f92db3c603096d'}
HASHES = {
    'relational': {'classification': '0c2e35cc18303f0d3ddeaf04e9cab6ca3a8bd6ea17195ac0e42d47af4bcb2a2d',
                   'regression': 'f863b3632f738e1444fb83a8cea5746f298889b5c96953a000c4720d202ae4cf'},
    'tabular': {'classification': '1ff91f484e19021aaf7d9eff6cc07e6d95a60b4aaad473e83e2ab2c3bfd2b617',
                'regression': 'fc672f536501d64356036050ac2acea7e46927f7b9ad38366abe048f88e7d423'}}


def config_path():
    return Path(os.environ.get('SQUIPI_KUMO_PREDICT_CONFIG', str(Path.home() / 'Library/Application Support/Squipi/kumo/prediction-config.json'))).expanduser()


def runtime_configuration():
    try:
        cfg = json.loads(config_path().read_text())
    except (OSError, ValueError):
        raise InvalidArguments('Kumo prediction is not configured. Connect the existing isolated structured-data-models runtime in Me; no downloads or inference occurred.') from None
    if cfg.get('enabled') is not True or cfg.get('device', 'cpu') != 'cpu':
        raise InvalidArguments('Kumo prediction needs an enabled CPU runtime configuration.')
    p = cfg.get('python', '')
    if not Path(p).is_absolute() or not Path(p).is_file() or not os.access(p, os.X_OK):
        raise InvalidArguments('The isolated Kumo prediction Python is missing.')
    for engine in REVISIONS:
        p = Path(cfg.get(engine + '_path', ''))
        if not p.is_absolute() or not p.is_dir() or p.name != REVISIONS[engine]:
            raise InvalidArguments('Kumo prediction needs the pinned local ' + engine + ' snapshot. Inference never downloads weights.')
        for filename in ('classifier.pt', 'regressor.pt'):
            if not (p / ('small' if engine == 'tabular' else '') / filename).is_file():
                raise InvalidArguments('A pinned Kumo ' + engine + ' checkpoint is missing.')
    return cfg


@tool('prediction_capabilities', 'Check configured local NVIDIA Kumo Relational and Tabular Small runtime files. Does not load a model or make network requests.', schema({}), tested=False)
def prediction_capabilities(ctx, args):
    try:
        runtime_configuration()
        return {'ready': True, 'experimental': True, 'engines': ['Kumo Relational', 'Kumo Tabular Small'],
                'device': 'cpu', 'data_modes': ['cross_sectional'], 'maximum_context': MAX_CONTEXT,
                'maximum_query': MAX_QUERY, 'summary_text': 'Local prediction files are configured; this does not certify predictive performance.'}
    except InvalidArguments as exc:
        return {'ready': False, 'experimental': True, 'summary_text': str(exc)}


def _private_name(name):
    n = re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')
    return bool(re.search(r'(^|_)(id|uuid|email|phone|mobile|telephone|address|passport|aadhaar|ssn|imei|imsi|gps|latitude|longitude|device|sim|notes|note|comment|comments|transcript|interview)(_|$)', n)
                or n in ('name', 'full_name', 'first_name', 'last_name', 'respondent_name', 'person_name'))


def _features(df, columns, forbidden):
    if not columns or len(columns) > 20 or len(columns) != len(set(columns)):
        raise InvalidArguments('Select 1–20 distinct feature columns explicitly for each table.')
    if set(columns) & (set(forbidden) | {'__kumo_entity_key'}):
        raise InvalidArguments('Outcome labels and entity/foreign keys cannot be predictive features.')
    missing = set(columns) - set(df.columns)
    if missing:
        raise InvalidArguments('Requested features are absent: ' + ', '.join(sorted(missing)))
    stypes = {}
    for c in columns:
        s = df[c]
        if _private_name(c):
            raise InvalidArguments('Potential identifier, PII or free-text feature refused: ' + c + '. Select de-identified structured predictors explicitly.')
        if s.isna().any():
            raise InvalidArguments('Feature has missing values: ' + c + '. Clean explicitly; no rows are silently dropped or imputed.')
        if pd.api.types.is_datetime64_any_dtype(s) or re.search(r'(^|[_ ])(date|timestamp|time)([_ ]|$)', c, re.I):
            raise InvalidArguments('Temporal features/histories need an explicit as-of design. This adapter accepts only prepared cross-sectional snapshots.')
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            if not np.isfinite(s.to_numpy(dtype=float)).all():
                raise InvalidArguments('Feature has nonfinite numeric values: ' + c)
            stypes[c] = 'numerical'
        else:
            text = s.astype(str)
            if text.str.len().max() > 80 or text.nunique() > 32 or text.str.contains(r'@|https?://|\b\d{9,}\b', regex=True).any():
                raise InvalidArguments('Potential free text, identifier or high-cardinality feature refused: ' + c)
            stypes[c] = 'categorical'
    return clean(df[columns].to_dict('records')), stypes


def prepare_prediction(base, args, linked_frames):
    """Pure validation; internal row keys hide source IDs from model input."""
    if args.get('data_mode') != 'cross_sectional':
        raise InvalidArguments('Choose cross_sectional explicitly only for predictors known at prediction time. Temporal/event histories require a separately prepared as-of snapshot and are not supported directly.')
    if args.get('task') not in ('classification', 'regression'):
        raise InvalidArguments('Choose classification or regression explicitly.')
    key, target = args['id_column'], args['target_column']
    if key == target or key not in base or target not in base:
        raise InvalidArguments('Select distinct existing entity ID and target columns.')
    if base[key].isna().any() or base[key].duplicated().any():
        raise InvalidArguments('Base entity IDs must be present and unique. Repeated entity/time anchors are not supported; prepare an explicit as-of snapshot.')
    if not len(base):
        raise InvalidArguments('The selected base dataset is empty.')
    known = base[target].notna().to_numpy()
    n_context, n_query = int(known.sum()), int((~known).sum())
    if not 20 <= n_context <= MAX_CONTEXT or not 1 <= n_query <= MAX_QUERY:
        raise InvalidArguments(f'Use 20–{MAX_CONTEXT} labelled context rows and 1–{MAX_QUERY} rows with missing targets. No sampling or label deletion is performed.')
    features, stypes = _features(base, args['feature_columns'], [key, target])
    for c, kind in stypes.items():
        if kind == 'categorical' and not base.loc[~known, c].isin(base.loc[known, c]).all():
            raise InvalidArguments('Query feature has categories not observed in labelled context: ' + c + '. Recode with a justified rule explicitly.')
    for c in args['feature_columns']:
        if base.loc[known, c].astype(str).equals(base.loc[known, target].astype(str)):
            raise InvalidArguments('Feature duplicates the observed target: ' + c + '. This leaks the answer.')
    y = base.loc[known, target]
    classes = []
    if args['task'] == 'classification':
        # JSON keys keep integer 1 and string "1" distinct; public mapping preserves labels.
        tokens = [json.dumps(clean(v), sort_keys=True, allow_nan=False) for v in y]
        counts = pd.Series(tokens).value_counts()
        if not 2 <= len(counts) <= 10 or counts.min() < 5:
            raise InvalidArguments('Classification needs 2–10 observed classes with at least five labelled rows in every class.')
        mapping = {v: i for i, v in enumerate(dict.fromkeys(tokens))}
        classes = [json.loads(v) for v in mapping]
        targets = [mapping[v] for v in tokens]
    else:
        numeric = pd.to_numeric(y, errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(numeric).all() or len(np.unique(numeric)) < 2:
            raise InvalidArguments('Regression needs finite numeric targets with at least two distinct observed values.')
        targets = numeric.tolist()
    engine = args.get('engine', 'auto')
    engine = ('relational' if linked_frames else 'tabular') if engine == 'auto' else engine
    if engine not in ('tabular', 'relational') or (engine == 'tabular' and linked_frames) or (engine == 'relational' and not linked_frames):
        raise InvalidArguments('Use Relational with linked tables; use Tabular Small for a single flat table. Tables will not be silently ignored.')
    id_map = {value: i for i, value in enumerate(base[key].tolist())}
    linked = []
    for spec, data in linked_frames:
        foreign = spec['foreign_key']
        if foreign not in data or data[foreign].isna().any():
            raise InvalidArguments('Every linked table needs a present, nonmissing foreign key.')
        if not 1 <= len(data) <= MAX_LINKED_ROWS:
            raise InvalidArguments(f'Each linked table needs 1–{MAX_LINKED_ROWS} rows; filter explicitly first.')
        if not data[foreign].isin(base[key]).all():
            raise InvalidArguments('A linked table contains unmatched entity keys. Reconcile or explicitly filter the selected datasets; no rows were dropped.')
        if target in spec['feature_columns']:
            raise InvalidArguments('The target column cannot appear in linked predictors.')
        records, types = _features(data, spec['feature_columns'], [foreign, target])
        linked.append({'records': records, 'stypes': types, 'entity_keys': [id_map[v] for v in data[foreign]],
                       'dataset_id': spec['dataset_id']})
    order = np.flatnonzero(known).tolist() + np.flatnonzero(~known).tolist()
    return {'engine': engine, 'task': args['task'], 'n_context': n_context, 'n_query': n_query,
            'features': [features[i] for i in order], 'feature_stypes': stypes,
            'entity_keys': order, 'targets': targets, 'classes': classes, 'linked': linked}, np.flatnonzero(~known).tolist()


def _run_model(ctx, cfg, prepared, out):
    if getattr(ctx, 'exclusive_model', None) != 'kumo':
        raise InvalidArguments('Kumo prediction needs exclusive model access from the native host.')
    request, response = out / 'model-request.json', out / 'model-response.json'
    ctx.project.write_json(request, {**prepared, 'runtime_config': str(config_path()), 'expected_checkpoint_sha256': HASHES[prepared['engine']][prepared['task']]})
    runner = Path(__file__).resolve().parents[2] / 'squipi_kumo_predict.py'
    env = dict(os.environ)
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1', DO_NOT_TRACK='1', TOKENIZERS_PARALLELISM='false')
    env.pop('PYTHONPATH', None)
    start = time.monotonic()
    with (out / 'runtime.log').open('w') as log:
        process = subprocess.Popen([cfg['python'], str(runner), str(request), str(response)], stdout=log, stderr=subprocess.STDOUT, env=env, cwd=out)
        try:
            while process.poll() is None:
                ctx.check()
                if time.monotonic() - start > 900:
                    raise InvalidArguments('Kumo prediction exceeded its process safety timeout; no output is marked complete.')
                time.sleep(.15)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
    if process.returncode != 0 or not response.is_file():
        detail = ''
        if response.is_file():
            try: detail = str(json.loads(response.read_text()).get('error', ''))[:400]
            except ValueError: pass
        raise InvalidArguments('Kumo prediction failed. ' + detail + ' Local log: ' + str(out / 'runtime.log'))
    try: output = json.loads(response.read_text())
    except ValueError: raise InvalidArguments('Kumo returned invalid JSON.') from None
    return output, time.monotonic() - start


def validate_output(output, prepared):
    rows = output.get('rows')
    if not isinstance(rows, list) or len(rows) != prepared['n_query']:
        raise InvalidArguments('Prediction row count does not match the selected query rows.')
    for row, expected_key in zip(rows, prepared['entity_keys'][prepared['n_context']:]):
        if row.get('entity_key') != expected_key:
            raise InvalidArguments('Prediction row identity/order does not match the selected query rows.')
        values = row.get('probabilities') if prepared['task'] == 'classification' else [row.get('prediction')]
        if not isinstance(values, list) or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
            raise InvalidArguments('Kumo returned nonfinite or malformed predictions.')
        if prepared['task'] == 'classification' and (len(values) != len(prepared['classes']) or any(v < 0 or v > 1 for v in values) or abs(sum(values) - 1) > 1e-4):
            raise InvalidArguments('Kumo class scores are not valid normalized probabilities.')
    return rows


FEATURES = {'type': 'array', 'minItems': 1, 'maxItems': 20, 'uniqueItems': True, 'items': STRING}
LINK = schema({'dataset_id': STRING, 'foreign_key': STRING, 'feature_columns': FEATURES}, ['dataset_id', 'foreign_key', 'feature_columns'])


@tool('predict_outcome', 'Experimental local NVIDIA Kumo classification or regression. Explicitly selected observed targets are context; rows with missing targets are queries. Auto chooses Tabular Small for one table, Relational for up to three linked tables. Requires unique entity IDs, 20–1024 labelled rows, 1–256 query rows, prepared cross-sectional predictors, isolated runtime and exclusive model access. Saves per-query CSV and provenance; no causal, accuracy or calibration claim. Never silently samples, drops rows, fills labels or ignores linked tables.',
      schema({'dataset_id': STRING, 'id_column': STRING, 'target_column': STRING, 'feature_columns': FEATURES,
              'task': {'type': 'string', 'enum': ['classification', 'regression']},
              'data_mode': {'type': 'string', 'enum': ['cross_sectional']},
              'engine': {'type': 'string', 'enum': ['auto', 'tabular', 'relational']},
              'linked_tables': {'type': 'array', 'maxItems': 3, 'items': LINK}, 'filename': STRING, 'title': STRING},
             ['dataset_id', 'id_column', 'target_column', 'feature_columns', 'task', 'data_mode']), needs_model=True, tested=False)
def predict_outcome(ctx, args):
    cfg = runtime_configuration()
    if getattr(ctx, 'exclusive_model', None) != 'kumo':
        raise InvalidArguments('Kumo prediction needs exclusive model access from the native host. No model was started.')
    ctx.progress(5, 'Checking prediction inputs and entity links')
    base, ds = frame(ctx, args)
    links, linked_sources = [], []
    specs = args.get('linked_tables', [])
    source_ids = [args['dataset_id']] + [s['dataset_id'] for s in specs]
    if len(set(source_ids)) != len(source_ids):
        raise InvalidArguments('Select distinct datasets for base and each linked table.')
    for spec in specs:
        data, source = frame(ctx, spec)
        links.append((spec, data)); linked_sources.append(source)
    prepared, query_indices = prepare_prediction(base, args, links)
    filename = args.get('filename', 'Kumo outcome predictions.csv')
    if Path(filename).name != filename or not filename.lower().endswith('.csv'):
        raise InvalidArguments('Use a plain .csv filename.')
    source_lineage = {d.id: digest(d.path) for source in [ds, *linked_sources] for d in ctx.project.dataset_lineage(source.id)}
    out = ctx.project.new_artifact_dir('kumo_prediction', args.get('title', ds.name))
    ctx.progress(15, 'Running isolated Kumo ' + prepared['engine'].title() + '; other model calls are paused')
    output, elapsed = _run_model(ctx, cfg, prepared, out)
    predictions = validate_output(output, prepared)
    # Never present scores calculated from a source that changed during the run.
    if any(digest(ctx.project.dataset(i).path) != signature for i, signature in source_lineage.items()):
        raise InvalidArguments('A selected source changed during prediction. No prediction is accepted; rerun on a stable snapshot.')
    records = []
    for source_index, pred in zip(query_indices, predictions):
        row = {'source_row_number': source_index + 1, 'entity_id': clean(base[args['id_column']].iloc[source_index])}
        if prepared['task'] == 'classification':
            scores = pred['probabilities']; winner = int(np.argmax(scores))
            row.update(predicted_class=prepared['classes'][winner], class_index=winner,
                       **{'probability_' + str(i): v for i, v in enumerate(scores)})
        else: row['predicted_median'] = pred['prediction']
        records.append(row)
    table = pd.DataFrame(records); path = out / filename
    # CSV formula escaping changes display only, with an explicit provenance note.
    for c in table.select_dtypes(include=['object', 'string']).columns:
        table[c] = table[c].map(lambda v: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v)
    table.to_csv(path, index=False)
    derived = ctx.project.register_derived(path, filename, len(table), len(table.columns), ds.id, list(table.columns))
    ctx.project.record_dataset_sources(derived.id, source_ids)
    summary = [{'engine': 'NVIDIA Kumo ' + ('Tabular Small' if prepared['engine'] == 'tabular' else 'Relational'),
                'task': prepared['task'], 'labelled_context': prepared['n_context'], 'query_rows': prepared['n_query'],
                'linked_tables': len(links), 'source_rows': len(base)}]
    mapping = [{'class_index': i, 'label': v, 'probability_column': 'probability_' + str(i)} for i, v in enumerate(prepared['classes'])]
    if prepared['task'] == 'classification':
        statistics = [{'class_index': i, 'class_label': v,
                       'assigned_query_rows': int((table['class_index'] == i).sum()),
                       'mean_uncalibrated_probability': float(table['probability_' + str(i)].mean())}
                      for i, v in enumerate(prepared['classes'])]
    else:
        statistics = [{'quantity': 'Query-level predicted medians', 'minimum': float(table['predicted_median'].min()),
                       'median': float(table['predicted_median'].median()), 'maximum': float(table['predicted_median'].max())}]
    ctx.project.write_json(out / 'class-mapping.json', mapping)
    payload = {'n': len(base), 'prediction_dataset_id': derived.id, 'csv_path': str(path), 'prediction_summary': summary,
               'class_mapping': mapping, 'prediction_statistics': statistics, 'source_lineage': source_lineage,
               'selected_source_ids': source_ids, 'specification': args, 'runtime_seconds': round(elapsed, 3),
               'runtime': {k: output.get(k) for k in ('engine', 'device', 'package_version', 'model_revision', 'checkpoint_sha256')},
               'uncertainty': {'calibrated': False}, 'limitations': [
                   'Experimental predictions, not causal effects or impact estimates. No out-of-sample accuracy or calibration was evaluated by this call.',
                   'Cross-sectional snapshot only: every predictor must have been known before its outcome. Temporal histories and repeated entity anchors are refused; semantic target leakage still requires researcher review.',
                   'Context and query entities are disjoint. Only selected features and synthetic numeric join keys enter the local model; originals remain unchanged.',
                   'Classification outputs are model probabilities without calibration guarantees. Regression uses the model median; no calibrated prediction interval is claimed.',
                   'PII and identifier screening is heuristic, not a guarantee of anonymisation. Sensitive attributes require a justified research design and review.',
                   'CSV text beginning with a spreadsheet formula prefix is escaped for safe opening. All selected input hashes and lineage are saved.']}
    saved = result(ctx, 'kumo_prediction', ds, payload)
    saved['summary_text'] = f'Kumo {prepared["engine"].title()} saved {prepared["n_query"]} query predictions to {filename}. Experimental; no accuracy, calibration or causal claim. Class labels are mapped in class_mapping. Use analysis_id with write_research_results_brief.'
    ctx.progress(100, 'Prediction CSV and provenance saved')
    return saved
