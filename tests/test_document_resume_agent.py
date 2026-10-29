#!/usr/bin/env python3
"""Verify whole-source reading resumes with real native host/worker and a scripted model."""
import argparse,json,os,re,sqlite3,subprocess,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_ollama_agent_integration import read_steps
from objective_fixture import serve_contract
p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
root=a.output_dir.resolve();root.mkdir(parents=True,exist_ok=False);support=root/'support';support.mkdir();(support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
source=root/'long-study.md';source.write_text(('Evidence chapter. The reported coefficient is descriptive and its context matters. '+'Additional method details. '*30+'\n')*70)
project=support/'projects/default';phase=1;spans=[];errors=[];requests=[]
class Peer(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append({'phase':phase,'body':body})
   if serve_contract(self,body):return
   content='\n'.join(m.get('content','') for m in body['messages'])
   if '<source_text>' in content:
    m=re.search(r'extracted character offsets (\d+)\.\.<(\d+)',content);assert m,'Reading locator absent'
    start,end=map(int,m.groups());spans.append({'phase':phase,'start':start,'end':end})
    if phase==1 and start>0:
     self.send_response(404);self.end_headers();self.wfile.write(b'{"error":"scripted interruption after first saved span"}');return
    text=f'Saved evidence from extracted offsets {start} to {end}. The comparisons are descriptive.'
   else:
    text=json.dumps({'blocked':'Scripted source-reading interruption; preserve saved notes.'}) if phase==1 else json.dumps({'answer':'Reviewed all extracted source spans. The document describes comparisons; its results do not establish cause and effect.'})
   self.send_response(200)
   if body.get('stream'):
    self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':text},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode())
   else:
    self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'choices':[{'message':{'content':text},'finish_reason':'stop'}]}).encode())
  except Exception as e:errors.append(str(e));self.send_response(500);self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':'mlx','SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','PYTHONDONTWRITEBYTECODE':'1'}
objective='Continue the current user-requested research task.\nOriginal user request:\nRead the full attached document and summarize the methods and findings.\nResearch objective id: '+str(uuid.uuid4())
def run(text,files=()):
 proc=subprocess.run([str(a.binary.resolve()),'--agent',text,*map(str,files)],env=env,capture_output=True,text=True,timeout=120)
 (root/f'phase-{phase}.log').write_text(proc.stdout+proc.stderr);assert proc.returncode==0,proc.stderr
 return json.loads((support/'last-agent-result.json').read_text())
try:
 first=run(objective,[source]);assert first['status']=='partial',first
 good=[s for s in read_steps(project) if s['tool']=='read_document' and s['ok']]
 assert len(good)==1 and good[0]['result']['reading_notes']
 docid=good[0]['result']['document_id'];cursor=good[0]['result']['end_offset'];task=first['task_id']
 phase=2
 second=run(objective+f'\nInputs selected for this task: {docid}\nEarlier runs of this same task: {task}.')
 assert second['status']=='completed',second
 assert min(s['start'] for s in spans if s['phase']==2)==cursor,'Successful reading was unnecessarily repeated'
 # A changed source must not inherit earlier notes as verified evidence.
 phase=3;source.write_text('CORRECTION: source revised.\n'+source.read_text())
 third=run(objective+f'\nInputs selected for this task: {docid}\nEarlier runs of this same task: {task}.',[source])
 assert third['status']=='completed',third
 assert min(s['start'] for s in spans if s['phase']==3)==0,'Changed source improperly reused earlier reading'
 assert not errors,errors
 result={'passed':True,'scripted_model':True,'saved_reading_resumed_at_cursor':cursor,'changed_source_restarts_verified_reading':True,'source_characters':len(source.read_text()),'spans':spans}
 (root/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:
 server.shutdown();(root/'requests.json').write_text(json.dumps(requests,indent=2))
