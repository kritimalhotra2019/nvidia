"""Private subprocess entry point for Kumo-TS. No downloads or network at inference."""
import hashlib
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
                os._exit(70)  # Only this owned child; prevents orphan inference.
    threading.Thread(target=watch_parent, daemon=True, name='kumo-parent-watch').start()
    request = json.loads(Path(request_path).read_text())
    cfg = json.loads(Path(request['runtime_config']).read_text())
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      HF_HUB_DISABLE_IMPLICIT_TOKEN='1', DO_NOT_TRACK='1')
    # Defense in depth: an inference process cannot open an IP connection even
    # if a dependency ignores the offline environment variables.
    import socket
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    def offline_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError('Network access is disabled during Kumo inference.')
        return original_connect(self, address)
    def offline_connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError('Network access is disabled during Kumo inference.')
        return original_connect_ex(self, address)
    socket.socket.connect = offline_connect
    socket.socket.connect_ex = offline_connect_ex
    import joblib
    import numpy as np
    import pandas as pd
    import torch
    from sdk.forecasting import ForecastingConfig, perform_forecasting
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    checkpoint = Path(cfg['checkpoint_path'])
    checksum = hashlib.sha256()
    with checkpoint.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):checksum.update(chunk)
    checkpoint_sha = checksum.hexdigest()
    if cfg.get('checkpoint_sha256') and checkpoint_sha != cfg['checkpoint_sha256']:
        raise ValueError('Kumo checkpoint hash does not match the trusted setup receipt.')
    predictions = []
    for i, series in enumerate(request['series']):
        values = np.asarray(series['values'], dtype=np.float32)
        standardizer = Path(response_path).parent/f'standardizer_{i}.pkl'
        joblib.dump({'mean':np.array([values.mean()],dtype=np.float32),
                     'std':np.array([max(float(values.std()),1e-8)],dtype=np.float32)},standardizer)
        # This direct checkpoint treats time as ordered positions. The parent
        # retains validated calendar dates, including unequal month lengths.
        data = pd.DataFrame({'timestamp':pd.date_range('2000-01-01',periods=len(values),freq='h'),'target':values})
        config = ForecastingConfig(seq_len=512, forecast_horizon=request['horizon'], model_horizon=72,
                  ckpt=str(checkpoint), standardizer_pkl=str(standardizer), model_name=cfg['backbone_path'],
                  device=cfg.get('device','cpu'), local_files_only=True, use_cross_channel=False,
                  batch_size=1, num_workers=0, alpha=1.0, interpretability=False, seed=13)
        output = perform_forecasting(df=data, config=config)
        if 'target_forecast' not in output or len(output) != request['horizon']:
            raise ValueError('Kumo returned an unexpected forecast shape.')
        forecast = output['target_forecast'].to_numpy(dtype=float)
        if not np.isfinite(forecast).all():raise ValueError('Kumo returned nonfinite predictions.')
        predictions.append({'series':series['series'],'values':forecast.tolist()})
    response = {'predictions':predictions, 'device':cfg.get('device','cpu'), 'checkpoint_sha256':checkpoint_sha,
                'source_revision':cfg.get('source_revision'), 'model_revision':cfg.get('model_revision'),
                'package_version':cfg.get('package_version')}
    Path(response_path).write_text(json.dumps(response,allow_nan=False))


if __name__ == '__main__':
    try: main(sys.argv[1],sys.argv[2])
    except Exception as exc:
        if len(sys.argv)>2:Path(sys.argv[2]).write_text(json.dumps({'error':type(exc).__name__+': '+str(exc)[:1000]}))
        raise
