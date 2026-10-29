#!/usr/bin/env python3
"""Real native Agent/worker with a scripted MLX HTTP peer, not live inference."""
import argparse,csv,hashlib,json,os,sqlite3,subprocess,threading,zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from test_ollama_agent_integration import read_steps
from objective_fixture import serve_contract

p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
root=a.output_dir.resolve();root.mkdir(exist_ok=False);requests=[];errors=[];redirected=[]
class Sink(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):redirected.append(self.path);self.send_response(500);self.end_headers()
sink=ThreadingHTTPServer(('127.0.0.1',0),Sink);threading.Thread(target=sink.serve_forever,daemon=True).start()
case='';support=None
class Peer(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append({'case':case,'body':body})
   assert self.path=='/v1/chat/completions';assert body['model']=='default_model'
   assert self.headers.get('Authorization')=='Bearer local';assert body['chat_template_kwargs']=={'enable_thinking':False}
   assert body['messages'][0]['role']=='system' and all(m['role']!='system' for m in body['messages'][1:])
   for spec in body.get('tools',[]):
    function=spec['function']
    if function['name']=='summarize_research_data':
     params=function['parameters']
     assert params['additionalProperties'] is False, 'Host weakened strict argument validation'
     assert params['properties']['units']['additionalProperties']=={'type':'string'}, 'Typed map schema was lost'
    if function['name']=='write_research_brief':
     assert function['parameters']['properties']['title']['minLength']==1, 'Host lost string validation'

   if case=='redirect':
    self.send_response(307);self.send_header('Location',f'http://127.0.0.1:{sink.server_port}/leak');self.end_headers();return
   if case=='dead_worker':
    self.send_response(404);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"error":"generation thread died"}');return
   if case=='stream_error':
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.write(b'data: {"error":"generation thread died"}\n\n');return
   if case=='incomplete':
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.write(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"profile_dataset","arguments":"{}"}}]},"finish_reason":null}]}\n\n');return
   if case not in ['malformed','truncated','empty','recovery_empty'] and serve_contract(self,body):return
   if case=='document_scope':
    names={tool['function']['name'] for tool in body.get('tools',[])}
    assert not names.intersection({'profile_dataset','clean_dataset','report_dataset','find_insights','regress'}),'Stale dataset tools leaked into document scope'
   action=None;text='' if case in ['empty','recovery_empty'] else 'Hello!'
   if case=='recovery_empty' and len([r for r in requests if r['case']==case])==1:text='I will do that now.'  
   if case in ['native','recovery']:
    project=support/'projects/default';steps=read_steps(project);done={x['tool']:x['result'] for x in steps if x['ok']}
    ds=json.loads((project/'datasets.json').read_text())['datasets'];source=next(d for d in ds if not d.get('derived_from'))
    if case=='recovery' and len([r for r in requests if r['case']==case])==1:text='I will do that now.'
    elif 'clean_dataset' not in done:action={'tool':'clean_dataset','arguments':{'dataset_id':source['id'],'pii':'keep','drop_columns':['name','email'],'steps':[],'fill_missing':'none'}}
    elif 'export_excel' not in done:action={'tool':'export_excel','arguments':{'dataset_id':done['clean_dataset']['dataset_id']}}
    elif 'report_dataset' not in done:action={'tool':'report_dataset','arguments':{'dataset_id':done['clean_dataset']['dataset_id'],'format':'docx','title':'Synthetic household analysis','outcome':'wellbeing'}}
    else:text='Saved the cleaned Excel and Word report with charts: '+done['report_dataset']['docx_path']
    if not body.get('tools') or (not body.get('stream') and action is None):text=json.dumps(action or {'answer':text});action=None
   tool=None
   if action:tool={'index':0,'id':'fixture_call_'+str(len(requests)),'type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments'])}}
   if case in ['malformed','truncated']:tool={'index':0,'id':'bad','type':'function','function':{'name':'profile_dataset','arguments':'[]' if case=='malformed' else '{}'}}
   finish='length' if case=='truncated' else ('tool_calls' if tool else 'stop')
   self.send_response(200)
   if body['stream']:
    self.send_header('Content-Type','text/event-stream');self.end_headers()
    if tool:
     first=json.loads(json.dumps(tool));second={'index':0,'function':{'arguments':tool['function']['arguments'][3:]}};first['function']['arguments']=tool['function']['arguments'][:3]
     deltas=[{'tool_calls':[first]},{'tool_calls':[second]}]
    else:deltas=[{'content':text[:3]},{'content':text[3:]}]
    for delta in deltas:self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':None}]})+'\n\n').encode())
    self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{},'finish_reason':finish}],'usage':{'total_tokens':100}})+'\n\ndata: [DONE]\n\n').encode())
   else:
    message={'content':None,'tool_calls':[{k:v for k,v in tool.items() if k!='index'}]} if tool else {'content':text}
    self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'choices':[{'message':message,'finish_reason':finish}]}).encode())
  except Exception as e:
   errors.append(str(e));self.send_response(500);self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start();results=[]
try:
 for case in ['greeting','native','recovery','malformed','truncated','redirect','dead_worker','stream_error','incomplete','empty','recovery_empty']:
  output=root/case;support=output/'app-support';support.mkdir(parents=True)
  (support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
  retired=support/'deepagents-env';(retired/'bin').mkdir(parents=True)
  retired_python=retired/'bin/python';retired_python.write_text('#!/bin/sh\nexit 97\n');retired_python.chmod(0o755)
  (retired/'squipi-ready.json').write_text('{}')
  source=output/'households.csv'
  with source.open('w') as f:
   w=csv.writer(f);w.writerow(['household_id','name','email','income','gender','wellbeing']);w.writerows([[f'HH{i}',f'Person{i}',f'p{i}@example.invalid',1000+(i%12)*100 if i!=7 else '',['F','M'][i%2],30+(i%15)] for i in range(48)])
  before=hashlib.sha256(source.read_bytes()).hexdigest()
  env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':'mlx','SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','SQUIPI_TEST_DEEPAGENTS':'1','SQUIPI_TEST_DEEPAGENTS_PYTHON':str(retired_python),'PYTHONDONTWRITEBYTECODE':'1'}
  command=[str(a.binary.resolve()),'--agent','hi how are you' if case in ['greeting','redirect'] else 'Remove only name and email, keep missing values, save the cleaned Excel file, and create a Word report with descriptive stats and labelled charts.']
  if case not in ['greeting','redirect']:command.append(str(source))
  proc=subprocess.run(command,env=env,capture_output=True,text=True,timeout=120);(output/'run.log').write_text(proc.stdout+proc.stderr)
  assert proc.returncode==0,(case,proc.stderr)
  reply=json.loads((support/'last-agent-result.json').read_text());steps=read_steps(support/'projects/default')
  assert hashlib.sha256(source.read_bytes()).hexdigest()==before
  if case in ['greeting','native','recovery']:assert reply['status']=='completed',(case,reply)
  else:assert reply['status']=='failed' and not any(s['ok'] for s in steps),(case,reply)
  if case in ['dead_worker','stream_error']:
   assert 'generation worker stopped' in json.dumps(reply) and 'could not find' not in json.dumps(reply),reply
  if case=='incomplete':assert 'before a completion marker' in json.dumps(reply),reply
  if case in ['empty','recovery_empty']:assert 'empty reply' in json.dumps(reply),reply
  if case=='recovery_empty':assert len([r for r in requests if r['case']==case])==4, 'Empty recovery was not retried twice'
  if case in ['native','recovery']:
   assert [s['tool'] for s in steps if s['tool'] not in ['set_research_requirements','objective_requirement_review']]==['clean_dataset','export_excel','report_dataset'],steps
   assert any(s['tool']=='objective_requirement_review' and s['ok'] for s in steps)
   steps=[s for s in steps if s['tool'] in ['clean_dataset','export_excel','report_dataset']]
   clean=Path(steps[0]['result']['path']);rows=list(csv.DictReader(clean.open()));assert len(rows)==48 and list(rows[0])==['household_id','income','gender','wellbeing'];assert rows[7]['income']==''
   assert Path(steps[1]['result']['path']).is_file()
   with zipfile.ZipFile(steps[2]['result']['docx_path']) as z:assert any(n.startswith('word/media/') for n in z.namelist())
  results.append({'case':case,'status':'passed','task_status':reply['status'],'tools':[s['tool'] for s in steps]})
 case='document_scope';support=root/'native/app-support'
 document=root/'case-study.md';document.write_text('Case study: A programme helps households access schooling. Assess social return on investment and value for money. Evidence includes programme costs and observed schooling attendance; causal impact is uncertain.')
 proc=subprocess.run([str(a.binary.resolve()),'--agent','Read the attached case study and explain an SROI approach.',str(document)],env=env|{'SQUIPI_TEST_DATA_DIR':str(support)},capture_output=True,text=True,timeout=120)
 (root/'document-scope.log').write_text(proc.stdout+proc.stderr)
 assert proc.returncode==0,proc.stderr
 assert any(r['case']=='document_scope' for r in requests)
 results.append({'case':'document_scope_with_existing_datasets','status':'passed'})
 assert not errors,errors
 assert not redirected,'MLX followed a redirect with private prompt data'
 (root/'verification.json').write_text(json.dumps({'scripted_model':True,'results':results},indent=2));print(json.dumps(results,indent=2))
finally:
 server.shutdown();sink.shutdown();(root/'requests.json').write_text(json.dumps(requests,indent=2))
