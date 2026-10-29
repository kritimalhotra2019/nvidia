#!/usr/bin/env python3
"""Controlled model peers, real native harness/worker: compaction, fresh topics and learned hints.
No paid API or real user data. This verifies orchestration, not model intelligence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from squipi_worker.learning import LearningStore
from squipi_worker.research import ResearchStore
from squipi_worker.project import Project
from squipi_worker.security import AllowList

p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
root=args.output_dir.resolve();root.mkdir(parents=True,exist_ok=False)
current={};errors=[];results=[]


class Peer(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'{"data":[{"id":"fixture-model"}],"models":[{"name":"qwen3.5:9b"}]}')
    def do_POST(self):
        try:
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            current['requests'].append(body)
            text='\n'.join(m.get('content','') for m in body.get('messages',[]))
            content='';reasoning='';calls=[];finish='stop'
            if current['case']=='discussion':
                assert 'Conversation checkpoint' in text and 'GAINS_FADED_ROUND_THREE' in text,'Compaction lost the preceding answer/correction'
                assert 'is it worth it then' in text
                if len(current['requests'])==1:
                    reasoning='A thought without a final answer';finish='length'
                else:content='It can still be worthwhile for acute support; the earlier discussion identified benefits that faded by the third round, so lasting effects should not be assumed.'
            elif current['case'] in ['fresh-topic','external-followup']:
                assert current['case']=='external-followup' or 'Current user objective' in text or 'top countries by GDP' in text
                if current['case']=='external-followup':
                    offered=[tool['function']['name'] for tool in body.get('tools',[])]
                    assert 'fetch_web' in offered, 'Explicit online follow-up lost web capabilities'
                    assert 'GDP measures' in text, 'Follow-up lost the preceding GDP topic'
                if len(current['requests'])==1:
                    calls=[{'id':'old-source','type':'function','function':{'name':'read_document','arguments':{'document_id':current['old_doc'],'offset':0,'max_chars':8000}}}]
                else:content='The biggest economies are unverified placeholder figures.'
            else:
                learned='Verify numerator intersection with the eligible denominator' in text
                assert learned == current['with_lesson'],'Learned hint missing, or leaking into the baseline'
                content='Use the explicit eligible population as the denominator and check that counted outcomes belong to it.'
            self.send_response(200)
            if '/api/' in self.path:
                self.send_header('Content-Type','application/x-ndjson');self.end_headers()
                message={'role':'assistant','content':content,'thinking':reasoning}
                if calls:message['tool_calls']=calls
                self.wfile.write((json.dumps({'message':message,'done':True,'done_reason':finish})+'\n').encode())
            else:
                message={'role':'assistant','content':content,'reasoning_content':reasoning}
                if calls:
                    message['tool_calls']=[c|{'function':c['function']|{'arguments':json.dumps(c['function']['arguments'])}} for c in calls]
                if body.get('stream'):
                    self.send_header('Content-Type','text/event-stream');self.end_headers()
                    self.wfile.write(('data: '+json.dumps({'choices':[{'delta':message,'finish_reason':finish}]})+'\n\ndata: [DONE]\n\n').encode())
                else:
                    self.send_header('Content-Type','application/json');self.end_headers()
                    self.wfile.write(json.dumps({'choices':[{'message':message,'finish_reason':finish}]}).encode())
        except Exception as e:
            errors.append(str(e));self.send_response(500);self.end_headers();self.wfile.write(str(e).encode())


server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    for provider in ['ollama','mlx']:
        for case in ['discussion','fresh-topic','external-followup','learning-baseline','learning-enabled']:
            support=root/f'{provider}-{case}';support.mkdir()
            (support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
            project=Project(support/'projects/default',AllowList())
            old=support/'unrelated-aid-report.md';old.write_text('Unrelated assistance evaluation. '*300)
            project.allow.allow(old);doc=project.register_document(old,'text',1,900,old)
            history=[{'role':'user','content':'Earlier unrelated discussion '+('old context '*2000)},
                     {'role':'assistant','content':'Older response '+('previous '*2000)},
                     {'role':'user','content':'Summarize the evaluation.'},
                     {'role':'assistant','content':'GAINS_FADED_ROUND_THREE: benefits were temporary; the comparison concerns acute support.'}]
            if case in ['fresh-topic','external-followup']:
                history=[{'role':'user','content':'What is GDP?'},{'role':'assistant','content':'GDP measures final goods and services produced.'}]
            with_lesson=case=='learning-enabled'
            if with_lesson:
                r=ResearchStore(project.root);tid=r.start_research_task('Calculate eligible response rates')['task_id']
                sid=r.record_research_step(tid,'calculate_dataset_rate',{}, {'summary_text':'Calculated the eligible denominator.'},True)['step_id']
                r.finish_research_task(tid,'completed','Rate computed')
                LearningStore(project.root).propose(tid,[{'kind':'procedure','title':'Eligible rates','trigger':'compute eligible response rate',
                    'body':'Verify numerator intersection with the eligible denominator','tools':['calculate_dataset_rate'],
                    'checks':['Inspect actual eligibility coding'],'evidence':[sid]}])
            history_file=support/'history.json';history_file.write_text(json.dumps(history))
            current={'case':case,'requests':[],'old_doc':doc.id,'with_lesson':with_lesson}
            env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':provider,
                'SQUIPI_TEST_OLLAMA_URL':f'http://127.0.0.1:{server.server_port}',
                'SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','PYTHONDONTWRITEBYTECODE':'1'}
            if case=='discussion':env|={'SQUIPI_AGENT_HISTORY':str(history_file),'SQUIPI_TEST_DISCUSSION':'1'}
            elif case in ['fresh-topic','external-followup']:env['SQUIPI_AGENT_HISTORY']=str(history_file)
            question='is it worth it then' if case=='discussion' else ('GOOD JOB, CHECK WORLD BANK TOO' if case=='external-followup' else 'top countries by GDP in 2026' if case=='fresh-topic' else 'How should we compute an eligible response rate?')
            proc=subprocess.run([str(args.binary.resolve()),'--agent',question],env=env,text=True,capture_output=True,timeout=120)
            (support/'native.log').write_text(proc.stdout+proc.stderr)
            assert proc.returncode==0,proc.stderr[-2000:]
            result=json.loads((support/'last-agent-result.json').read_text())
            if case=='discussion':
                assert result['status']=='completed' and len(current['requests'])==2,result
                assert not result['steps'],'Discussion unnecessarily executed a research workflow'
            elif case in ['fresh-topic','external-followup']:
                assert result['status']=='partial' and "couldn't verify" in result['text'],result
                assert not any(s['ok'] and s['tool']=='read_document' for s in result['steps']),result
            else:assert result['status']=='completed',result
            (support/'requests.json').write_text(json.dumps(current['requests'],indent=2))
            results.append({'provider':provider,'case':case,'status':result['status'],'model_calls':len(current['requests'])})
    assert not errors,errors
    report={'passed':True,'controlled_model':True,'cases':results}
    (root/'verification.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
finally:server.shutdown()
