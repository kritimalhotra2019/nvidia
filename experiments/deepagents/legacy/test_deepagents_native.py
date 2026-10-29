#!/usr/bin/env python3
"""Actual Deep Agents/LangGraph + native host/worker, with loopback model fixtures."""
import argparse,csv,hashlib,json,os,sqlite3,subprocess,threading,zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_ollama_agent_integration import read_steps
p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--python',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
root=a.output_dir.resolve();root.mkdir();requests=[];errors=[];case='';support=None;parser_error_sent=False
class Peer(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  global parser_error_sent
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append({'case':case,'path':self.path,'body':body})
   names={t['function']['name'] for t in body.get('tools',[])}
   assert not names.intersection({'execute','task'}),'Unrestricted execution/delegation exposed'
   if 'format' not in body:assert 'write_file' in names and 'read_file' in names,'Deep Agents filesystem middleware was not active'
   if case=='ollama_parse_recovery' and not parser_error_sent:
    parser_error_sent=True;self.send_response(500);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"error":"fixture native tool syntax error"}');return
   steps=read_steps(support/'projects/default');done={s['tool']:s['result'] for s in steps if s['ok']}
   ds=json.loads((support/'projects/default/datasets.json').read_text())['datasets'];original=next(d for d in ds if not d.get('derived_from'))
   if case=='mac_denied':action={'tool':'squipi_report_blocker','arguments':{'reason':'Mac access requires interactive native approval; none was granted.'}} if any(s['tool']=='mac_list_apps' for s in steps) else {'tool':'mac_list_apps','arguments':{}}
   elif 'clean_dataset' not in done:action={'tool':'clean_dataset','arguments':{'dataset_id':original['id'],'pii':'keep','drop_columns':['name','email'],'steps':[],'fill_missing':'none'}}
   elif 'export_excel' not in done:action={'tool':'export_excel','arguments':{'dataset_id':done['clean_dataset']['dataset_id'],'filename':'Non PII household data.xlsx'}}
   elif 'report_dataset' not in done:action={'tool':'report_dataset','arguments':{'dataset_id':done['clean_dataset']['dataset_id'],'format':'docx','title':'Deep Agents synthetic research','outcome':'wellbeing'}}
   else:action={'answer':'Created the cleaned Excel file and Word report at '+done['report_dataset']['docx_path']}
   message={'role':'assistant','content':action.get('answer','')}
   if 'tool' in action:message['tool_calls']=[{'id':'call_'+str(len(requests)),'type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments']) if not case.startswith('ollama') else action['arguments']}}]
   self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
   if not case.startswith('ollama'):
    assert self.path=='/v1/chat/completions' and body['model']=='default_model'
    assert body['chat_template_kwargs']['enable_thinking'] is False
    value={'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if 'tool' in action else 'stop'}],'usage':{'total_tokens':100}}
   else:
    assert self.path=='/api/chat' and body['model']=='qwen3.5:9b' and isinstance(body['think'],bool)
    if 'format' in body:message={'role':'assistant','content':json.dumps(action if 'tool' in action else {'answer':action['answer']})}
    value={'message':message,'done':True,'done_reason':'stop','prompt_eval_count':50,'eval_count':50}
   self.wfile.write(json.dumps(value).encode())
  except Exception as e:
   errors.append(str(e));self.send_response(400);self.end_headers();self.wfile.write(str(e).encode())
server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start();results=[]
try:
 for case in ['ollama','mlx','mac_denied','ollama_parse_recovery']:
  output=root/case;support=output/'app-support';support.mkdir(parents=True);(support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
  source=output/'households.csv'
  with source.open('w') as f:
   w=csv.writer(f);w.writerow(['household_id','name','email','income','gender','wellbeing']);w.writerows([[f'HH{i}',f'Person{i}',f'p{i}@example.invalid',1000+i*100 if i!=7 else '',['F','M'][i%2],30+i%15] for i in range(48)])
  before=hashlib.sha256(source.read_bytes()).hexdigest()
  env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':'ollama' if case.startswith('ollama') else 'mlx','SQUIPI_TEST_DEEPAGENTS':'1','SQUIPI_TEST_DEEPAGENTS_PYTHON':str(a.python.absolute()),'SQUIPI_TEST_OLLAMA_URL':f'http://127.0.0.1:{server.server_port}','SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','PYTHONDONTWRITEBYTECODE':'1'}
  proc=subprocess.run([str(a.binary.resolve()),'--agent','List running applications' if case=='mac_denied' else 'Remove only name and email, keep other columns and missing values, create the cleaned Excel file and a Word report with descriptive statistics and labelled charts.',str(source)],env=env,capture_output=True,text=True,timeout=150)
  (output/'run.log').write_text(proc.stdout+proc.stderr);assert proc.returncode==0,proc.stderr
  reply=json.loads((support/'last-agent-result.json').read_text())
  if case=='mac_denied':
   assert reply['status'] in {'partial','failed'} and 'approval' in reply['text'],reply
   steps=read_steps(support/'projects/default')
   assert [s['tool'] for s in steps]==['mac_list_apps'] and not steps[0]['ok'],steps
   assert not (support/'mac-actions.jsonl').exists()
   results.append({'case':case,'status':'passed','no_mac_access_without_native_approval':True});continue
  assert reply['status']=='completed',(case,reply)
  assert reply['via'].startswith('Deep Agents'),reply
  steps=read_steps(support/'projects/default');assert [s['tool'] for s in steps]==['clean_dataset','export_excel','report_dataset'],steps
  clean=list(csv.DictReader(Path(steps[0]['result']['path']).open()));assert len(clean)==48 and list(clean[0])==['household_id','income','gender','wellbeing'] and clean[7]['income']==''
  assert Path(steps[1]['result']['path']).name=='Non PII household data.xlsx'
  with zipfile.ZipFile(steps[2]['result']['docx_path']) as z:assert any(n.startswith('word/media/') for n in z.namelist())
  assert hashlib.sha256(source.read_bytes()).hexdigest()==before
  assert (support/'projects/default/deepagents/checkpoints.sqlite3').is_file()
  results.append({'provider':case,'status':'passed','tools':[s['tool'] for s in steps],'real_deepagents':True,'real_exports':True,'scripted_model':True,'original_unchanged':True})
 assert not errors,errors
 (root/'verification.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
finally:
 server.shutdown();(root/'requests.json').write_text(json.dumps(requests,indent=2));(root/'errors.json').write_text(json.dumps(errors,indent=2))
