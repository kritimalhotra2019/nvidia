#!/usr/bin/env python3
"""Cross-process saved brief reuse with a fresh request-completeness review."""
import argparse,csv,json,os,subprocess,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_ollama_agent_integration import read_steps
from objective_fixture import serve_contract
p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
root=a.output_dir.resolve();root.mkdir(parents=True,exist_ok=False);support=root/'support';support.mkdir();(support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
source=root/'monitoring.csv'
with source.open('w') as f:
 w=csv.writer(f);w.writerow(['record_id','value']);w.writerows([[i,100+i] for i in range(12)])
project=support/'projects/default';phase=1;reviews=[];errors=[];requests=[]
class Peer(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append({'phase':phase,'body':body});content=json.dumps(body)
   if serve_contract(self,body):return
   is_review='RESEARCH_DELIVERABLE_REVIEW' in content
   steps=read_steps(project);done={s['tool']:s['result'] for s in steps if s['ok']}
   ds=json.loads((project/'datasets.json').read_text())['datasets'][0]['id']
   if is_review:reviews.append(phase);action=None;text=json.dumps({'passed':True,'issues':[]})
   elif phase==1 and 'write_research_brief' in done:
    self.send_response(404);self.end_headers();self.wfile.write(b'{"error":"scripted interruption after verified export"}');return
   elif phase==2:
    assert done['write_research_brief']['analysis_id'] in content,'Saved calculation handle missing before the next planner decision'
    assert done['write_research_brief']['analysis_id'] in content
    action=None;text=json.dumps({'answer':'Created the requested Word brief: '+done['write_research_brief']['path']})
   elif 'summarize_research_data' not in done:action={'tool':'summarize_research_data','arguments':{'dataset_id':ds,'columns':['value'],'units':{'value':'points'},'charts':False}};text=''
   else:action={'tool':'write_research_brief','arguments':{'analysis_id':done['summarize_research_data']['analysis_id'],'title':'Monitoring results','filename':'Saved brief.docx'}};text=''
   call={'id':'fixture_call','type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments'])}} if action else None
   self.send_response(200)
   if body.get('stream'):
    self.send_header('Content-Type','text/event-stream');self.end_headers();delta={'tool_calls':[{'index':0,**call}]} if call else {'content':text}
    self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':'tool_calls' if call else 'stop'}]})+'\n\ndata: [DONE]\n\n').encode())
   else:
    self.send_header('Content-Type','application/json');self.end_headers();message={'content':None,'tool_calls':[call]} if call else {'content':text}
    self.wfile.write(json.dumps({'choices':[{'message':message,'finish_reason':'tool_calls' if call else 'stop'}]}).encode())
  except Exception as e:errors.append(str(e));self.send_response(500);self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':'mlx','SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','PYTHONDONTWRITEBYTECODE':'1'}
objective='Continue the current user-requested research task.\nOriginal user request:\nAnalyze value in points and create a Word brief without graphs. Save it as "Saved brief.docx".\nResearch objective id: '+str(uuid.uuid4())
def run(text,files=()):
 proc=subprocess.run([str(a.binary.resolve()),'--agent',text,*map(str,files)],env=env,capture_output=True,text=True,timeout=120);(root/f'phase-{phase}.log').write_text(proc.stdout+proc.stderr);assert proc.returncode==0,proc.stderr
 return json.loads((support/'last-agent-result.json').read_text())
try:
 first=run(objective,[source]);assert first['status']=='failed',first
 steps=read_steps(project);brief=next(s['result'] for s in steps if s['tool']=='write_research_brief' and s['ok']);saved=Path(brief['path']);before=saved.read_bytes();mtime=saved.stat().st_mtime_ns
 ds=brief['dataset_id'];phase=2
 second=run(objective+f'\nInputs selected for this task: {ds}\nEarlier runs of this same task: '+first['task_id']+'.')
 assert second['status']=='completed',second
 steps=read_steps(project)
 assert len([s for s in steps if s['tool']=='summarize_research_data' and s['ok']])==1
 assert len([s for s in steps if s['tool']=='write_research_brief' and s['ok']])==1
 assert reviews==[] and saved.read_bytes()==before and saved.stat().st_mtime_ns==mtime
 assert not errors,errors
 result={'passed':True,'scripted_model':True,'saved_analysis_and_brief_reused_without_execution':True,'restored_brief_passed_output_checks':True,'word_file_unchanged':True}
 (root/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:server.shutdown();(root/'requests.json').write_text(json.dumps(requests,indent=2))
