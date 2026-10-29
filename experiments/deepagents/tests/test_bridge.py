import json,sqlite3
from pathlib import Path
from typing import ClassVar
import pytest
from langchain_ollama import ChatOllama
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult,ChatGeneration
from squipi_deepagents.bridge import plan,local_endpoint

class Fixture(ChatOllama):
    replies:ClassVar[list]=[]
    seen:ClassVar[list]=[]
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.seen.append((messages,kwargs))
        assert 'task' not in {t['function']['name'] for t in kwargs.get('tools',[])}
        assert 'execute' not in {t['function']['name'] for t in kwargs.get('tools',[])}
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

def model(replies):
    Fixture.replies=list(replies);Fixture.seen=[]
    return Fixture(model='qwen3.5:9b',reasoning=False)

def request():
    return {'provider':'ollama','endpoint':'http://127.0.0.1:11434','model':'qwen3.5:9b','objective_id':'stable-objective','messages':[{'role':'system','content':'Preserve originals. No arbitrary execution.'},{'role':'user','content':'Clean the attached synthetic data.'}],'tools':[{'function':{'name':'clean_dataset','description':'Clean registered data','parameters':{'type':'object','properties':{'dataset_id':{'type':'string'}},'required':['dataset_id'],'additionalProperties':False}}}]}

def answer(text='Done',**kw):return AIMessage(content=text,response_metadata={'done_reason':'stop'},**kw)

def test_proposals_do_not_execute_and_checkpoint_survives_new_model(tmp_path):
    r=request();a=plan(r,tmp_path,model([answer('',tool_calls=[{'id':'one','name':'clean_dataset','args':{'dataset_id':'synthetic'}}])]))
    assert a['tool_calls']==[{'name':'clean_dataset','arguments':{'dataset_id':'synthetic'}}]
    assert list(tmp_path.iterdir())==[tmp_path/'checkpoints.sqlite3']
    r['messages'].append({'role':'tool','content':'Observed host cleaning succeeded: dataset cleaned_1','tool_call_id':'one','name':'clean_dataset'})
    b=plan(r,tmp_path,model([answer('Used observed cleaned_1')]))
    assert b['text']=='Used observed cleaned_1'
    msgs=Fixture.seen[0][0]
    assert any('host_execution_pending' in str(m.content) for m in msgs)
    assert sum(m.id=='squipi-host-context' for m in msgs)==1
    assert any('cleaned_1' in str(m.content) for m in msgs)

def test_virtual_scratch_file_persists_without_touching_mac_files(tmp_path):
    plan(request(),tmp_path,model([answer('',tool_calls=[{'id':'scratch','name':'write_file','args':{'file_path':'/notes.txt','content':'Saved research notes'}}]),answer()]))
    assert not (tmp_path/'notes.txt').exists()
    plan(request(),tmp_path,model([answer('',tool_calls=[{'id':'read','name':'read_file','args':{'file_path':'/notes.txt'}}]),answer('Recovered notes')]))
    assert any('Saved research notes' in str(m.content) for m in Fixture.seen[-1][0])

def test_truncated_model_action_cannot_become_host_proposal(tmp_path):
    bad=AIMessage(content='',tool_calls=[{'id':'one','name':'clean_dataset','args':{'dataset_id':'synthetic'}}],response_metadata={'done_reason':'length'})
    with pytest.raises(ValueError,match='incomplete'):plan(request(),tmp_path,model([bad]))

@pytest.mark.parametrize('url',['https://api.example.test/v1','http://127.0.0.1.evil.test/v1','http://user:pass@localhost/v1','http://localhost/v1?key=x','file:///tmp/local','http://localhost/other'])
def test_remote_and_ambiguous_endpoints_are_rejected(url):
    with pytest.raises(ValueError):local_endpoint(url,'mlx')


def test_indispensable_blocker_is_partial_not_fake_completion(tmp_path):
    value=plan(request(),tmp_path,model([answer('',tool_calls=[{'id':'blocked','name':'squipi_report_blocker','args':{'reason':'This action requires interactive native approval.'}}])]))
    assert value['finish_reason']=='blocked' and value['tool_calls']==[]
