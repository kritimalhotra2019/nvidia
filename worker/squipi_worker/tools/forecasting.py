"""Optional Kumo forecasts in a separate, offline process; never changes originals.

The native host owns the exclusive-model lease. A model cannot grant that lease
or select executable paths through the tool schema. Runtime setup is explicit.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..registry import tool, InvalidArguments
from .research_packages import frame, result, schema, STRING

HISTORY = 512
MAX_GROUPS = 8
MODEL_NOTICE = 'Experimental NVIDIA Kumo point forecasts, not causal effects or calibrated prediction intervals.'


def config_path():
    # Host/user configuration only; never obtained from a document or tool args.
    return Path(os.environ.get('SQUIPI_KUMO_CONFIG', str(Path.home() / 'Library/Application Support/Squipi/kumo/config.json'))).expanduser()


def runtime_configuration():
    p = config_path()
    if not p.is_file():
        raise InvalidArguments('Kumo Forecast is not set up. Configure the isolated Kumo runtime in Me first; no model or weights were downloaded and no data was sent.')
    try:
        cfg = json.loads(p.read_text())
    except (ValueError, OSError):
        raise InvalidArguments('The Kumo runtime configuration is unreadable; repair setup in Me.') from None
    if cfg.get('enabled') is not True:
        raise InvalidArguments('Kumo Forecast is disabled in its local runtime configuration.')
    if cfg.get('backend', 'kumo-ts') != 'kumo-ts':
        raise InvalidArguments('This runtime needs the supported kumo-ts adapter; no different forecasting model will be substituted.')
    for key in ('python', 'package_path', 'checkpoint_path', 'backbone_path'):
        value = cfg.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).exists():
            raise InvalidArguments('Kumo setup is incomplete: missing '+key+'. Complete setup before forecasting; inference never downloads files.')
    if not Path(cfg['python']).is_file() or not os.access(cfg['python'], os.X_OK):
        raise InvalidArguments('Kumo setup has no executable isolated Python interpreter.')
    if cfg.get('device', 'cpu') not in ('cpu', 'mps', 'cuda'):
        raise InvalidArguments('Kumo device must be cpu, mps or cuda in the host configuration.')
    return cfg


@tool('forecast_capabilities', 'Check whether the optional NVIDIA Kumo Forecast runtime is configured. Reads local setup only; does not load a model, download weights, or contact an API.', schema({}), tested=False)
def forecast_capabilities(ctx, args):
    try:
        cfg = runtime_configuration()
        return {'ready': True, 'experimental': True, 'engine': 'NVIDIA Kumo Forecast',
                'device': cfg.get('device', 'cpu'), 'minimum_history': HISTORY, 'maximum_horizon': 72,
                'source_revision': cfg.get('source_revision'), 'model_revision': cfg.get('model_revision'),
                'summary_text': 'Kumo runtime files are configured; this is a setup check, not proof of successful inference.'}
    except InvalidArguments as exc:
        return {'ready': False, 'experimental': True, 'engine': 'NVIDIA Kumo Forecast', 'summary_text': str(exc)}


def prepare_series(df, time_column, target_column, group_column=None, frequency='auto', horizon=12):
    cols = [time_column, target_column] + ([group_column] if group_column else [])
    if len(set(cols)) != len(cols):
        raise InvalidArguments('Time, target and group columns must be distinct.')
    missing = set(cols) - set(df.columns)
    if missing:
        raise InvalidArguments('Requested columns are absent: '+', '.join(sorted(missing)))
    if df.empty:
        raise InvalidArguments('The selected dataset is empty.')
    if group_column and df[group_column].isna().any():
        raise InvalidArguments('The group column has missing values. Resolve them explicitly before forecasting; no rows were dropped.')
    grouped = df.groupby(group_column, sort=False, observed=True) if group_column else [('Series', df)]
    groups = list(grouped)
    if len(groups) > MAX_GROUPS:
        raise InvalidArguments(f'Forecast at most {MAX_GROUPS} series at a time; filter the dataset explicitly first.')
    prepared = []
    for label, part in groups:
        if len(part) < HISTORY:
            raise InvalidArguments(f'Series {label!s} has {len(part)} observations; this Kumo checkpoint requires at least {HISTORY} regular observations. No padding, imputation or alternate model was used.')
        if pd.api.types.is_numeric_dtype(part[time_column]):
            raise InvalidArguments('Use explicit date/time values, not a numeric year or row number, for the time column.')
        dates = pd.to_datetime(part[time_column], errors='coerce', format='mixed')
        if dates.isna().any():
            raise InvalidArguments(f'Series {label!s} contains missing or invalid dates; resolve them explicitly.')
        try:
            if dates.dt.tz is not None:
                raise InvalidArguments('Use timezone-naive calendar times or explicitly convert to UTC before forecasting. DST-sensitive local timestamps are not inferred.')
        except AttributeError:
            raise InvalidArguments('Mixed timezone timestamps are not supported; explicitly normalise the time column.') from None
        values = pd.to_numeric(part[target_column], errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise InvalidArguments(f'Series {label!s} has missing, nonnumeric or nonfinite target values. Clean them explicitly; forecasting does not impute.')
        ordered = pd.DataFrame({'timestamp': dates.to_numpy(), 'target': values}).sort_values('timestamp', kind='stable')
        idx = pd.DatetimeIndex(ordered['timestamp'])
        if idx.has_duplicates:
            raise InvalidArguments(f'Series {label!s} has duplicate timestamps. Aggregate explicitly with a justified rule first.')
        inferred = pd.infer_freq(idx)
        freq = inferred if frequency == 'auto' else frequency
        if not freq:
            raise InvalidArguments(f'Series {label!s} is irregular or has missing periods. Resolve gaps explicitly; no resampling was performed.')
        try:
            expected = pd.date_range(idx[0], periods=len(idx), freq=freq)
            future = pd.date_range(idx[-1], periods=horizon+1, freq=freq)[1:]
        except (ValueError, TypeError):
            raise InvalidArguments('The frequency is not a supported pandas calendar frequency.') from None
        if not expected.equals(idx) or len(future) != horizon or not (future[0] > idx[-1]):
            raise InvalidArguments(f'Series {label!s} does not match the requested regular frequency {freq}; no dates were invented or rows dropped.')
        history = ordered.tail(HISTORY)
        prepared.append({'series': str(label), 'frequency': str(freq), 'n_total': len(part), 'n_context': HISTORY,
                         'sorted_by_time': not dates.is_monotonic_increasing,
                         'timestamps': [v.isoformat() for v in history['timestamp']],
                         'values': history['target'].tolist(), 'future_timestamps': [v.isoformat() for v in future]})
    return prepared


def _run_model(ctx, cfg, prepared, out, horizon):
    if getattr(ctx, 'exclusive_model', None) != 'kumo':
        raise InvalidArguments('Kumo needs exclusive model access. Squipi must pause other model calls and unload local language models before forecasting. No Kumo model was started.')
    request = out / 'model-request.json'
    response = out / 'model-response.json'
    ctx.project.write_json(request, {'horizon': horizon, 'series': prepared, 'runtime_config': str(config_path())})
    runner = Path(__file__).resolve().parents[2] / 'squipi_kumo_forecast.py'
    env = dict(os.environ)
    env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'DO_NOT_TRACK': '1', 'TOKENIZERS_PARALLELISM': 'false',
                'MPLCONFIGDIR': str(out / '.matplotlib')})
    # The runner can import only the approved host-configured package. No shell/code arguments.
    env['PYTHONPATH'] = cfg['package_path']
    log_path = out / 'runtime.log'
    started = time.monotonic()
    with log_path.open('w') as log:
        process = subprocess.Popen([cfg['python'], str(runner), str(request), str(response)],
                                   stdout=log, stderr=subprocess.STDOUT, env=env, cwd=out)
        try:
            while process.poll() is None:
                ctx.check()
                if time.monotonic()-started > 900:
                    raise InvalidArguments('Kumo did not finish within 15 minutes. The original data is unchanged; no forecast is marked completed.')
                time.sleep(.15)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
    if process.returncode != 0 or not response.is_file():
        detail = ''
        if response.is_file():
            try: detail = str(json.loads(response.read_text()).get('error', ''))[:600]
            except ValueError: pass
        raise InvalidArguments('Kumo Forecast failed without producing validated predictions. '+detail+' Local diagnostic log: '+str(log_path))
    try:
        data = json.loads(response.read_text())
    except ValueError:
        raise InvalidArguments('Kumo returned an invalid result; no prediction is accepted.') from None
    predictions = data.get('predictions')
    if not isinstance(predictions, list) or len(predictions) != len(prepared):
        raise InvalidArguments('Kumo returned the wrong number of series; no prediction is accepted.')
    for series, pred in zip(prepared, predictions):
        if pred.get('series') != series['series'] or len(pred.get('values', [])) != horizon:
            raise InvalidArguments('Kumo output does not match the requested series or horizon.')
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in pred['values']):
            raise InvalidArguments('Kumo output contains invalid forecast values.')
    return data, time.monotonic()-started


@tool('forecast_series',
      'Forecast a selected numeric time series with the optional local NVIDIA Kumo Forecast model. '
      'Requires at least 512 complete regular observations per series, isolated runtime setup and exclusive model access. '
      'Optional group_column forecasts up to eight separate series sequentially; horizon is 1–72 periods. '
      'Saves a forecast CSV, labelled SVG/PNG charts and a reusable analysis_id. Originals are unchanged. '
      'Returns point predictions only: no calibrated intervals, causal effect claims, or silent alternate-model fallback.',
      schema({'dataset_id': STRING, 'time_column': STRING, 'target_column': STRING, 'group_column': STRING,
              'frequency': {'type':'string','minLength':1,'maxLength':30},
              'horizon': {'type':'integer','minimum':1,'maximum':72}, 'units':STRING, 'title':STRING,
              'filename': STRING}, ['dataset_id','time_column','target_column','horizon']), needs_model=True, tested=False)
def forecast_series(ctx, args):
    cfg = runtime_configuration()
    if getattr(ctx, 'exclusive_model', None) != 'kumo':
        raise InvalidArguments('Kumo needs exclusive model access from the native host before forecasting. No model was started.')
    ctx.progress(5, 'Checking time-series data')
    df, ds = frame(ctx, args)
    prepared = prepare_series(df, args['time_column'], args['target_column'], args.get('group_column'),
                              args.get('frequency', 'auto'), args['horizon'])
    filename = args.get('filename', 'Kumo forecasts.csv')
    if Path(filename).name != filename or not filename.lower().endswith('.csv'):
        raise InvalidArguments('Use a plain .csv filename for forecast output.')
    out = ctx.project.new_artifact_dir('kumo_forecast', args.get('title', ds.name))
    ctx.progress(15, 'Running isolated Kumo Forecast; other model calls are paused')
    model_result, elapsed = _run_model(ctx, cfg, prepared, out, args['horizon'])
    rows, summaries, charts = [], [], []
    ctx.progress(85, 'Saving forecast and labelled charts')
    from .charts import _mpl
    plt = _mpl(None)
    for i, (s, pred) in enumerate(zip(prepared, model_result['predictions'])):
        for timestamp, value in zip(s['future_timestamps'], pred['values']):
            rows.append({'series': s['series'], 'timestamp': timestamp, 'forecast': value,
                         'last_value_baseline': s['values'][-1], 'units': args.get('units', args['target_column'])})
        summaries.append({'series': s['series'], 'observations': s['n_total'], 'context_used': HISTORY,
                          'frequency': s['frequency'], 'horizon': args['horizon'], 'last_observed': s['values'][-1],
                          'first_forecast': pred['values'][0], 'final_forecast': pred['values'][-1],
                          'units': args.get('units', args['target_column'])})
        fig, ax = plt.subplots(figsize=(9, 4.8))
        dates = pd.to_datetime(s['timestamps'][-72:])
        future = pd.to_datetime(s['future_timestamps'])
        ax.plot(dates, s['values'][-72:], label='Observed', color='#286B98', linewidth=1.8)
        ax.plot([dates[-1], *future], [s['values'][-1], *pred['values']], label='Kumo point forecast', color='#477000', linewidth=2)
        ax.plot(future, [s['values'][-1]]*len(future), '--', label='Last-value baseline', color='#888888', linewidth=1)
        ax.axvline(dates[-1], color='#777777', linewidth=.8, linestyle=':')
        ax.set_xlabel('Time'); ax.set_ylabel(args.get('units', args['target_column']))
        ax.set_title((args.get('title') or ('Forecast: '+args['target_column']))+' · '+s['series'])
        ax.legend(loc='best'); fig.autofmt_xdate(); fig.tight_layout(rect=(0,.06,1,1))
        fig.text(.05,.015,'Point forecasts only · no calibrated prediction interval · not a causal impact estimate',fontsize=8,color='#666666')
        svg_path = out/f'forecast_{i+1}.svg'; fig.savefig(svg_path); fig.savefig(svg_path.with_suffix('.png'),dpi=150); plt.close(fig)
        charts.append(str(svg_path))
    table = pd.DataFrame(rows)
    csv_path = out/filename; table.to_csv(csv_path, index=False)
    derived = ctx.project.register_derived(csv_path, filename, len(table), len(table.columns), ds.id, list(table.columns))
    payload = {'n': len(df), 'forecast_dataset_id': derived.id, 'csv_path': str(csv_path), 'chart_path': charts[0], 'chart_paths': charts,
               'series_summary': summaries, 'forecast_rows': rows,
               'specification': {**args, 'history_length': HISTORY, 'engine':'NVIDIA Kumo Forecast', 'device':cfg.get('device','cpu')},
               'runtime': {k:model_result.get(k) for k in ('source_revision','model_revision','checkpoint_sha256','device','package_version')},
               'runtime_seconds': round(elapsed,3), 'uncertainty': {'calibrated_intervals_available': False},
               'limitations': [MODEL_NOTICE,
                   'Each series uses its latest 512 observations. Earlier rows are validated but are not input to this direct forecast.',
                   'No out-of-sample backtest was performed by this call. The last-value baseline is a reference, not a validated performance comparison.',
                   'Forecast dates follow the validated calendar frequency. Missing dates, missing targets and duplicate times are refused, never silently filled.',
                   'Forecasts can be outside the historical range. Domain constraints and structural breaks require researcher review.']}
    saved = result(ctx, 'kumo_forecast', ds, payload)
    saved['summary_text'] = f'NVIDIA Kumo forecasted {args["horizon"]} periods for {len(prepared)} series. Saved {filename} and labelled charts. Point forecasts only; no calibrated intervals or causal interpretation. Use analysis_id with write_research_results_brief for a Word report.'
    ctx.progress(100, 'Forecast files saved')
    return saved
