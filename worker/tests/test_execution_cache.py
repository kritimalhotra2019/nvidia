import json
from pathlib import Path
import pandas as pd
import pytest
from squipi_worker import tools
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext,registry,InvalidArguments
from squipi_worker.execution_cache import ExecutionCache


def setup(tmp_path):
    p=Project(tmp_path/'project',AllowList());ctx=JobContext(p,lambda *a:None,lambda:False)
    source=tmp_path/'source.csv';pd.DataFrame({'name':['Alice','Bob'],'income':[10,20]}).to_csv(source,index=False)
    p.allow.allow(source);ds=registry.call('import_dataset',{'path':str(source)},ctx)['dataset_id']
    return p,ctx,source,ds


def test_successful_clean_is_reused_after_process_state_recreation(tmp_path):
    p,ctx,source,ds=setup(tmp_path);args={'dataset_id':ds,'pii':'keep','drop_columns':['name'],'steps':[]}
    first=ExecutionCache(p).call('clean_dataset',args,ctx,'objective_123',lambda:registry.call('clean_dataset',args,ctx))
    restored=Project(p.root,AllowList());ctx2=JobContext(restored,lambda *a:None,lambda:False)
    again=ExecutionCache(restored).call('clean_dataset',args,ctx2,'objective_123',lambda:pytest.fail('Completed cleaning was executed again'))
    assert again['dataset_id']==first['dataset_id'] and again['reused_saved_result']
    assert source.read_text().startswith('name,income')


def test_input_or_output_changes_invalidate_result(tmp_path):
    p,ctx,source,ds=setup(tmp_path);args={'dataset_id':ds,'pii':'keep','steps':[]};cache=ExecutionCache(p)
    first=cache.call('clean_dataset',args,ctx,'objective_123',lambda:registry.call('clean_dataset',args,ctx))
    output=Path(first['path']);output.write_text('income\n999\n')
    count=[]
    cache.call('clean_dataset',args,ctx,'objective_123',lambda:count.append('executed') or {'summary_text':'fresh'})
    assert count==['executed']
    source.write_text('name,income\nAlice,100\nBob,200\n')
    cache.call('clean_dataset',args,ctx,'objective_123',lambda:count.append('input changed') or {'summary_text':'fresh'})
    assert count[-1]=='input changed'


def test_failed_calls_and_other_objectives_are_not_reused(tmp_path):
    p,ctx,source,ds=setup(tmp_path);cache=ExecutionCache(p);args={'dataset_id':ds}
    def fail():raise InvalidArguments('failure')
    with pytest.raises(InvalidArguments):cache.call('profile_dataset',args,ctx,'objective_123',fail)
    first=cache.call('profile_dataset',args,ctx,'objective_123',lambda:{'marker':1})
    assert first['marker']==1
    second=cache.call('profile_dataset',args,ctx,'objective_other',lambda:{'marker':2})
    assert second['marker']==2


def test_sensitive_side_effects_never_replay(tmp_path):
    p,ctx,source,ds=setup(tmp_path);cache=ExecutionCache(p);calls=[]
    for name in ['remember_research','plan_research','apply_file_moves','fetch_web']:
        for _ in range(2):cache.call(name,{},ctx,'objective_123',lambda:calls.append(name) or {})
    assert len(calls)==8


def test_package_result_and_word_export_reuse_survive_restart_and_detect_tampering(tmp_path):
    p,ctx,source,ds=setup(tmp_path)
    args={'dataset_id':ds,'rules':[{'column':'income','dtype':'integer','minimum':0}]}
    first=ExecutionCache(p).call('validate_research_data',args,ctx,'objective_123',lambda:registry.call('validate_research_data',args,ctx))
    restored=Project(p.root,AllowList());ctx2=JobContext(restored,lambda *a:None,lambda:False)
    repeated=ExecutionCache(restored).call('validate_research_data',args,ctx2,'objective_123',lambda:pytest.fail('Validation was executed twice'))
    assert repeated['analysis_id']==first['analysis_id'] and repeated['reused_saved_result']
    export={'analysis_ids':[first['analysis_id']],'title':'Validation','filename':'Validation.docx'}
    brief=ExecutionCache(p).call('write_research_results_brief',export,ctx,'objective_123',lambda:registry.call('write_research_results_brief',export,ctx))
    saved=ExecutionCache(restored).call('write_research_results_brief',export,ctx2,'objective_123',lambda:pytest.fail('Export was executed twice'))
    assert saved['path']==brief['path'] and saved['reused_saved_result']
    registry_file=p.root/'package-analyses.json'
    assert registry_file.is_file()
    # The saved result itself is part of the cache integrity boundary.
    Path(first['results_path']).write_text('{}')
    ran=[]
    ExecutionCache(restored).call('validate_research_data',args,ctx2,'objective_123',lambda:ran.append(True) or {})
    assert ran
