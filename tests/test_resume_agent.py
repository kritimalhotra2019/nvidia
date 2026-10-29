#!/usr/bin/env python3
"""Cross-process native resume/cache/retry regression, with a loopback model fixture."""
import argparse,csv,json,os,subprocess,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_ollama_agent_integration import read_steps
p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
root=a.output_dir.resolve();root.mkdir();support=root/'support';support.mkdir();(support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
source=root/'households.csv'
with source.open('w') as f:
 w=csv.writer(f);w.writerow(['name','email','income','wellbeing']);w.writerows([[f'Person{i}',f'p{i}@example.invalid',1000+i*10,30+i%9] for i in range(48)])
project=support/'projects/default';phase=1;requests=[];errors=[];retried=False;replayed=False
class Peer(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  global retried,replayed
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append({'phase':phase,'body':body})
   steps=read_steps(project);clean=[s for s in steps if s['tool']=='clean_dataset' and s['ok']];reports=[s for s in steps if s['tool']=='report_dataset' and s['ok']]
   datasets=json.loads((project/'datasets.json').read_text())['datasets'];original=next(d for d in datasets if not d.get('derived_from'))
   args={'dataset_id':original['id'],'pii':'keep','drop_columns':['name','email'],'steps':[],'fill_missing':'none'}
   if phase==1 and clean:
    self.send_response(404);self.end_headers();self.wfile.write(b'{"error":"fixture interrupted after cleaning"}');return
   if phase==2:
    context=json.dumps(body).replace('\\/','/')
    assert clean[0]['result']['dataset_id'] in context,'Prior cleaned dataset was not automatically restored'
    if not retried:
     retried=True;self.send_response(503);self.end_headers();self.wfile.write(b'{"error":"fixture temporary outage"}');return
   if not clean or (phase==2 and not replayed):
    action={'tool':'clean_dataset','arguments':args}
    if phase==2:replayed=True
   elif not reports:action={'tool':'report_dataset','arguments':{'dataset_id':clean[0]['result']['dataset_id'],'format':'docx','title':'Resumed household analysis','outcome':'wellbeing'}}
   else:action={'answer':'Created the Word report at '+reports[-1]['result']['docx_path']}
   self.send_response(200)
   if body.get('stream'):
    self.send_header('Content-Type','text/event-stream');self.end_headers()
    call={'index':0,'id':'fixture_call','type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments'])}}
    self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'tool_calls':[call]},'finish_reason':'tool_calls'}]})+'\n\ndata: [DONE]\n\n').encode())
   else:
    self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}]}).encode())
  except Exception as e:
   errors.append(str(e));self.send_response(400);self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':'mlx','SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','PYTHONDONTWRITEBYTECODE':'1'}
objective='Continue the current user-requested research task.\nOriginal user request:\nRemove name and email, preserve all other data and create a Word report with descriptive statistics and charts.\nResearch objective id: '+str(uuid.uuid4())
def run(instruction,*files):
 r=subprocess.run([str(a.binary.resolve()),'--agent',instruction,*map(str,files)],env=env,capture_output=True,text=True,timeout=120);(root/f'phase-{phase}.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
 return json.loads((support/'last-agent-result.json').read_text())
try:
 first=run(objective,source);assert first['status']=='failed',first
 clean=next(s for s in read_steps(project) if s['tool']=='clean_dataset' and s['ok']);saved=Path(clean['result']['path']);before=saved.read_bytes();mtime=saved.stat().st_mtime_ns
 phase=2
 second=run(objective+'\nInputs selected for this task: '+clean['arguments']['dataset_id']+'\nEarlier runs of this same task: '+first['task_id']+'.')
 assert second['status']=='completed',second
 steps=read_steps(project);cleans=[s for s in steps if s['tool']=='clean_dataset' and s['ok']]
 assert len(cleans)==2 and cleans[1]['result'].get('reused_saved_result') is True,cleans
 assert cleans[0]['result']['dataset_id']==cleans[1]['result']['dataset_id']
 assert saved.read_bytes()==before and saved.stat().st_mtime_ns==mtime,'Completed cleaning was rerun'
 assert any(s['tool']=='report_dataset' and s['ok'] and Path(s['result']['docx_path']).is_file() for s in steps)
 assert not errors,errors
 result={'status':'passed','cross_process_resume':True,'restored_prior_result_before_model_request':True,'identical_cleaning_reused':True,'temporary_model_outage_retried':retried,'cleaned_file_unchanged':True,'word_report_created':True,'synthetic_model_fixture':True}
 (root/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:
 server.shutdown();(root/'requests.json').write_text(json.dumps(requests,indent=2))
