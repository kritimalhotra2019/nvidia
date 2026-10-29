"""Private offline CPU runner for pinned Kumo Relational / Tabular Small."""
import hashlib
import importlib
import importlib.metadata
import json
import os
import sys
import threading
import time
from pathlib import Path


def main(request_path, response_path):
    parent_pid = os.getppid()
    if parent_pid <= 1:
        raise RuntimeError('Kumo parent worker is no longer running.')
    def watch_parent():
        while True:
            time.sleep(.5)
            if os.getppid() != parent_pid:
                os._exit(70)
    threading.Thread(target=watch_parent, daemon=True, name='kumo-parent-watch').start()
    req = json.loads(Path(request_path).read_text())
    cfg = json.loads(Path(req['runtime_config']).read_text())
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      HF_HUB_DISABLE_IMPLICIT_TOKEN='1', DO_NOT_TRACK='1', TOKENIZERS_PARALLELISM='false')
    import socket
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    def offline_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError('Network access is disabled during Kumo prediction.')
        return original_connect(self, address)
    def offline_connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError('Network access is disabled during Kumo prediction.')
        return original_connect_ex(self, address)
    socket.socket.connect, socket.socket.connect_ex = offline_connect, offline_connect_ex
    if importlib.metadata.version('structured-data-models') != '0.2.1':
        raise RuntimeError('This adapter requires the reviewed structured-data-models 0.2.1 runtime.')
    import numpy as np
    import pandas as pd
    import torch
    from sdm import RelatedTables, TableTensor
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    torch.manual_seed(13)
    engine, task = req['engine'], req['task']
    if engine not in ('tabular', 'relational') or task not in ('classification', 'regression'):
        raise ValueError('Unsupported prediction engine or task.')
    filename = ('classifier.pt' if task == 'classification' else 'regressor.pt')
    if engine == 'tabular': filename = 'small/' + filename
    root = Path(cfg[engine + '_path'])
    checkpoint = root / filename
    checksum = hashlib.sha256()
    with checkpoint.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): checksum.update(chunk)
    checkpoint_sha = checksum.hexdigest()
    if checkpoint_sha != req['expected_checkpoint_sha256']:
        raise RuntimeError('Prediction checkpoint does not match the pinned trusted hash.')
    # Public constructors have no path argument. Preserve official meta-device
    # loading while replacing only the used module's Hub resolver with a strict
    # local allowlist. No fallback resolver or remote repository request exists.
    module = importlib.import_module('sdm.models.kumo.' + engine + '.model')
    expected_repo = 'nvidia/Kumo-' + ('Tabular' if engine == 'tabular' else 'Relational')
    def local_checkpoint(repo_id, filename, *, revision=None, **kwargs):
        if repo_id != expected_repo or filename != str(checkpoint.relative_to(root)) or revision != 'v1.0.1':
            raise RuntimeError('The SDK requested an unapproved checkpoint.')
        return str(checkpoint)
    module.download_checkpoint = local_checkpoint
    model = (module.KumoTabular(task=task, size='small', device='cpu') if engine == 'tabular'
             else module.KumoRelational(task=task, device='cpu'))
    n = req['n_context']
    features = pd.DataFrame(req['features'])
    y = TableTensor.from_columns({'outcome': req['targets']},
                                stypes={'outcome': 'categorical' if task == 'classification' else 'numerical'}, device='cpu')
    if engine == 'tabular':
        x = TableTensor.from_pandas(features, stypes=req['feature_stypes'], device='cpu')
        with torch.inference_mode():
            output = model(x[:n], y, x[n:], num_estimators=1)
    else:
        key = '__kumo_entity_key'
        keys = req['entity_keys']
        task_x = TableTensor.from_columns({key: keys}, stypes={key: 'id'}, device='cpu')
        entity_frame = features.copy(); entity_frame[key] = keys
        entities = TableTensor.from_pandas(entity_frame, stypes={**req['feature_stypes'], key: 'id'}, device='cpu')
        tables = {'entities': entities}; relationships = []
        for i, linked in enumerate(req['linked']):
            data = pd.DataFrame(linked['records']); data[key] = linked['entity_keys']
            name = 'linked_' + str(i)
            tables[name] = TableTensor.from_pandas(data, stypes={**linked['stypes'], key: 'id'}, device='cpu')
            relationships.append({'left_table': name, 'left_columns': key, 'right_table': 'entities', 'right_columns': key})
        related = RelatedTables(tables=tables, relationships=relationships,
                                task_links=[{'task_columns': key, 'table': 'entities', 'table_columns': key}])
        context_keys = set(keys[:n]); query_keys = set(keys[n:])
        context_tables, query_tables = {'entities': entities[:n]}, {'entities': entities[n:]}
        for i, linked in enumerate(req['linked']):
            name = 'linked_' + str(i)
            ci = torch.tensor([j for j, k in enumerate(linked['entity_keys']) if k in context_keys], dtype=torch.long)
            qi = torch.tensor([j for j, k in enumerate(linked['entity_keys']) if k in query_keys], dtype=torch.long)
            context_tables[name] = tables[name][ci]
            query_tables[name] = tables[name][qi]
        with torch.inference_mode():
            output = model(task_x[:n], y, task_x[n:],
                           related_context_tables=related.replace_tables(context_tables),
                           related_query_tables=related.replace_tables(query_tables), num_hops=1, num_estimators=1)
    data = output.to_pandas()
    if len(data) != req['n_query']:
        raise RuntimeError('The model changed the query row count.')
    if task == 'classification':
        columns = [str(i) for i in range(len(req['classes']))]
        if set(data.columns) != set(columns):
            raise RuntimeError('The model returned unexpected class labels.')
        values = data[columns].to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1) or not np.allclose(values.sum(axis=1), 1, atol=1e-4):
            raise RuntimeError('The model returned invalid classification probabilities.')
        rows = [{'entity_key': key, 'probabilities': row.tolist()} for key, row in zip(req['entity_keys'][n:], values)]
    else:
        if 'q500' not in data:
            raise RuntimeError('The model did not return its median regression quantile.')
        values = data['q500'].to_numpy(dtype=float)
        if not np.isfinite(values).all(): raise RuntimeError('The model returned nonfinite regression predictions.')
        rows = [{'entity_key': key, 'prediction': float(v)} for key, v in zip(req['entity_keys'][n:], values)]
    Path(response_path).write_text(json.dumps({'rows': rows, 'engine': engine, 'task': task,
        'device': 'cpu', 'package_version': importlib.metadata.version('structured-data-models'),
        'model_revision': root.name, 'checkpoint_sha256': checkpoint_sha}, allow_nan=False))


if __name__ == '__main__':
    try: main(sys.argv[1], sys.argv[2])
    except Exception as exc:
        if len(sys.argv) > 2:
            Path(sys.argv[2]).write_text(json.dumps({'error': type(exc).__name__ + ': ' + str(exc)[:1000]}))
        raise
