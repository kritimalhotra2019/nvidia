import pandas as pd
import pytest
from squipi_worker import tools
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext,registry,InvalidArguments
from squipi_worker.tools.insights import _eta_squared
from squipi_worker.tools.datasets import analyse_column


def test_singletons_are_not_headline_group_effects():
    y=pd.Series(range(20));g=pd.Series(list('abcdefghijklmnopqrst'))
    assert _eta_squared(y,g)==0
    assert _eta_squared(pd.Series([1]*10+[10]*10),pd.Series(['a']*10+['b']*10))>.9


@pytest.mark.parametrize('name',['deviceid','subscriberid','simid'])
def test_admin_identifiers_are_not_quantities(name):
    assert analyse_column(name,pd.Series([100000000001,100000000002,100000000003]),3)['role']=='id'


def test_append_preserves_rows_sources_and_rejects_misaligned_schema(tmp_path):
    p=Project(tmp_path/'project',AllowList());ctx=JobContext(p,lambda *a:None,lambda:False);ids=[]
    for name,values in [('district_a',[1,2]),('district_b',[3,4,5])]:
        path=tmp_path/(name+'.csv');pd.DataFrame({'income':values,'status':['treatment']*len(values)}).to_csv(path,index=False);p.allow.allow(path)
        ids.append(registry.call('import_dataset',{'path':str(path)},ctx)['dataset_id'])
    result=registry.call('combine_datasets',{'dataset_ids':ids,'mode':'append'},ctx)
    f=pd.read_csv(result['path']);assert len(f)==5 and list(f.income)==[1,2,3,4,5]
    assert f['_source_dataset_id'].value_counts().to_dict()=={ids[1]:3,ids[0]:2}
    assert [x['rows'] for x in result['source_datasets']]==[2,3]
    with pytest.raises(InvalidArguments):registry.call('combine_datasets',{'dataset_ids':[ids[0],ids[0]],'mode':'append'},ctx)
    with pytest.raises(InvalidArguments):registry.call('combine_datasets',{'dataset_ids':[ids[0],result['dataset_id']],'mode':'append'},ctx)


def test_encrypted_office_file_stops_registration(tmp_path):
    p=Project(tmp_path/'project',AllowList());ctx=JobContext(p,lambda *a:None,lambda:False)
    path=tmp_path/'encrypted.xlsx';path.write_bytes(bytes.fromhex('d0cf11e0a1b11ae1')+b'\0'*100);p.allow.allow(path)
    with pytest.raises(InvalidArguments,match='unencrypted'):registry.call('import_dataset',{'path':str(path)},ctx)
    assert p.datasets()==[]


def test_requested_output_name_and_path_rejection(tmp_path):
    p=Project(tmp_path/'project',AllowList());ctx=JobContext(p,lambda *a:None,lambda:False)
    source=tmp_path/'households.csv';pd.DataFrame({'name':['Person A','Person B'],'income':[100,200]}).to_csv(source,index=False);p.allow.allow(source)
    original=registry.call('import_dataset',{'path':str(source)},ctx)['dataset_id']
    clean=registry.call('clean_dataset',{'dataset_id':original,'pii':'keep','drop_columns':['name'],'output_name':'Non PII household data'},ctx)
    from pathlib import Path
    assert Path(clean['path']).name=='Non PII household data.csv'
    exported=registry.call('export_excel',{'dataset_id':clean['dataset_id'],'filename':'Non PII household data.xlsx'},ctx)
    assert Path(exported['path']).name=='Non PII household data.xlsx'
    assert list(pd.read_excel(exported['path']).columns)==['income']
    assert pd.read_csv(source).columns.tolist()==['name','income']
    for name in ['../outside','/tmp/outside','subdir/outside','bad\\name']:
        with pytest.raises(InvalidArguments):registry.call('clean_dataset',{'dataset_id':original,'output_name':name},ctx)
        with pytest.raises(InvalidArguments):registry.call('export_excel',{'dataset_id':original,'filename':name},ctx)
