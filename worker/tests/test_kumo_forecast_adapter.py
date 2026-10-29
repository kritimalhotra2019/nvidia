"""No model or network calls: calendar/shape/privacy gates and saved-artifact integration."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from squipi_worker import tools  # noqa
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext, InvalidArguments, registry
from squipi_worker.tools import forecasting


def series(n=512, freq='D'):
    return pd.DataFrame({'date':pd.date_range('1980-01-01',periods=n,freq=freq).astype(str),
                         'visits':100+np.arange(n)*.1})


def test_calendar_months_use_actual_calendar_future():
    df=series(freq='MS')
    prepared=forecasting.prepare_series(df,'date','visits',horizon=3)
    assert prepared[0]['future_timestamps']==['2022-09-01T00:00:00','2022-10-01T00:00:00','2022-11-01T00:00:00']
    assert prepared[0]['frequency']=='MS'


def test_unsorted_rows_are_sorted_without_mutating_source():
    df=series(600).iloc[::-1].copy();original=df.copy(deep=True)
    p=forecasting.prepare_series(df,'date','visits',horizon=2)[0]
    assert p['sorted_by_time'] and p['n_total']==600 and p['n_context']==512
    assert p['values'][0]==pytest.approx(108.8)
    pd.testing.assert_frame_equal(df,original)


@pytest.mark.parametrize('case',['short','duplicate','missing_time','missing_value','infinite','irregular','frequency','numeric_time','same_columns'])
def test_bad_series_fail_before_model(case):
    df=series();kwargs={}
    if case=='short':df=df.iloc[:100]
    if case=='duplicate':df.loc[1,'date']=df.loc[0,'date']
    if case=='missing_time':df.loc[7,'date']=None
    if case=='missing_value':df.loc[7,'visits']=np.nan
    if case=='infinite':df.loc[7,'visits']=float('inf')
    if case=='irregular':df.loc[255,'date']='1980-09-12 12:00:00'
    if case=='frequency':kwargs['frequency']='MS'
    if case=='numeric_time':df['date']=range(len(df))
    if case=='same_columns':kwargs['group_column']='visits'
    with pytest.raises(InvalidArguments):forecasting.prepare_series(df,'date','visits',**kwargs)


def test_groups_are_separate_and_missing_group_refused():
    a=series();a['district']='a';b=series();b['district']='b';b['visits']*=2
    frame=pd.concat([a,b],ignore_index=True)
    p=forecasting.prepare_series(frame,'date','visits','district')
    assert len(p)==2 and p[1]['values'][-1]==2*p[0]['values'][-1]
    frame.loc[0,'district']=None
    with pytest.raises(InvalidArguments,match='missing values'):forecasting.prepare_series(frame,'date','visits','district')


def test_setup_check_does_not_import_or_download_model(tmp_path,monkeypatch):
    monkeypatch.setenv('SQUIPI_KUMO_CONFIG',str(tmp_path/'absent.json'))
    ctx=JobContext(Project(tmp_path/'project',AllowList()),lambda *a:None,lambda:False)
    r=registry.call('forecast_capabilities',{},ctx)
    assert r['ready'] is False and 'not set up' in r['summary_text']
    with pytest.raises(InvalidArguments,match='not set up'):
        registry.call('forecast_series',{'dataset_id':'x','time_column':'date','target_column':'visits','horizon':3},ctx)


@pytest.fixture
def world(tmp_path,monkeypatch):
    package=tmp_path/'package';package.mkdir();backbone=tmp_path/'backbone';backbone.mkdir()
    checkpoint=tmp_path/'checkpoint.pt';checkpoint.write_bytes(b'test only no model')
    config=tmp_path/'config.json';config.write_text(json.dumps({'enabled':True,'python':sys.executable,'package_path':str(package),
                     'checkpoint_path':str(checkpoint),'backbone_path':str(backbone),'device':'cpu'}))
    monkeypatch.setenv('SQUIPI_KUMO_CONFIG',str(config));monkeypatch.setenv('MPLCONFIGDIR',str(tmp_path/'matplotlib'))
    p=Project(tmp_path/'project',AllowList());ctx=JobContext(p,lambda *a:None,lambda:False)
    src=tmp_path/'input.csv';series().to_csv(src,index=False);p.allow.allow(src)
    ds=registry.call('import_dataset',{'path':str(src)},ctx)['dataset_id']
    return ctx,ds,src


def test_host_exclusive_lease_cannot_be_model_argument(world):
    ctx,ds,src=world
    with pytest.raises(InvalidArguments,match='exclusive'):
        registry.call('forecast_series',{'dataset_id':ds,'time_column':'date','target_column':'visits','horizon':3},ctx)
    with pytest.raises(InvalidArguments):
        registry.call('forecast_series',{'dataset_id':ds,'time_column':'date','target_column':'visits','horizon':3,'exclusive_model':'kumo'},ctx)


def test_saved_forecast_and_brief_preserve_actual_cells(world,monkeypatch):
    ctx,ds,src=world;ctx.exclusive_model='kumo';before=src.read_bytes()
    def stub(ctx,cfg,prepared,out,horizon):
        return {'predictions':[{'series':s['series'],'values':[151.2,152.3,153.4]} for s in prepared]},.01
    monkeypatch.setattr(forecasting,'_run_model',stub)
    r=registry.call('forecast_series',{'dataset_id':ds,'time_column':'date','target_column':'visits','horizon':3,'units':'visits','filename':'Expected visits.csv'},ctx)
    table=pd.read_csv(r['csv_path'])
    assert table['forecast'].tolist()==[151.2,152.3,153.4]
    assert table['timestamp'].tolist()==['1981-05-27T00:00:00','1981-05-28T00:00:00','1981-05-29T00:00:00']
    assert Path(r['chart_path']).is_file() and Path(r['chart_path']).with_suffix('.png').is_file()
    assert r['uncertainty']['calibrated_intervals_available'] is False
    assert src.read_bytes()==before
    brief=registry.call('write_research_results_brief',{'analysis_ids':[r['analysis_id']],'title':'Expected clinic visits','filename':'Forecast brief.docx'},ctx)
    assert Path(brief['docx_path']).is_file()
    assert brief['verification']['all_saved_result_cells_match'] is True
    from docx import Document
    d=Document(brief['docx_path']);assert len(d.inline_shapes)==1
    assert '153.4' in ' '.join(c.text for t in d.tables for row in t.rows for c in row.cells)
