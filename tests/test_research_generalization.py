#!/usr/bin/env python3
"""Native host/real worker regression across unrelated schemas and runtimes.

Model replies are simulated. This verifies execution/protocols, not Qwen reasoning.
"""
import argparse,json,os,subprocess,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pandas as pd
from squipi_worker.project import Project
from squipi_worker.security import AllowList
from squipi_worker.registry import JobContext,registry
from squipi_worker import tools
from test_ollama_agent_integration import read_steps
from objective_fixture import serve_contract

p=argparse.ArgumentParser();p.add_argument('--binary',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--quality-cases',action='store_true');a=p.parse_args()
root=a.output_dir.resolve();root.mkdir(parents=True,exist_ok=False);errors=[];requests=[];results=[]
cases=[('retail','csv',pd.DataFrame({'sku':['A'+str(i) for i in range(12)],'branch':['N']*6+['S']*6,'revenue':range(100,112),'units':range(12)}),{'columns':['revenue','units'],'group_by':['branch'],'units':{'revenue':'USD','units':'items'}}),
       ('equipment','parquet',pd.DataFrame({'sensor_id':range(12),'facility':['Plant A']*6+['Plant B']*6,'before':range(12),'after':[x+2 for x in range(12)]}),{'columns':['before','after'],'group_by':['facility'],'units':{'before':'kWh','after':'kWh'},'paired_changes':[{'baseline':'before','followup':'after','label':'Change','unit':'kWh'}]}),
       ('feedback','xlsx',pd.DataFrame({'ticket_id':range(12),'area':['Urban']*6+['Rural']*6,'rating':['Good','Poor',None]*4}),{'categorical_columns':['rating'],'group_by':['area']})]
provider='';case_name='';support=None;spec=None;stale_id=None;attempted_stale=False;loop=False;review_counts={}

class Peer(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"models":[]}')
 def do_POST(self):
  global attempted_stale
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append({'provider':provider,'case':case_name,'body':body})
   if serve_contract(self,body):return
   steps=read_steps(support/'projects/default');done={s['tool']:s['result'] for s in steps if s['ok']}
   data=json.loads((support/'projects/default/datasets.json').read_text())['datasets'];source=next(d for d in data if d['name'].startswith(case_name+'.'))
   is_review=any('RESEARCH_DELIVERABLE_REVIEW' in m.get('content','') for m in body['messages'])
   if is_review:
    identity=provider+'-'+case_name;review_counts[identity]=review_counts.get(identity,0)+1
    bad=case_name=='retail_review_reject' or (case_name=='retail_review_repair' and review_counts[identity]==1)
    action={'answer':json.dumps({'passed':not bad,'issues':['The report title must match the exact requested title.'] if bad else []})}
   elif case_name=='repeated_discovery':action={'tool':'discover_tools','arguments':{'names':['profile_dataset']}}
   elif case_name=='stale_analysis_list' and not attempted_stale:
    if 'discover_tools' not in done:action={'tool':'discover_tools','arguments':{'names':['write_research_results_brief']}}
    else:
     attempted_stale=True;action={'tool':'write_research_results_brief','arguments':{'analysis_ids':['research_old_unselected'],'title':'Wrong source','filename':'Wrong source.docx'}}
   elif loop or not attempted_stale:
    attempted_stale=True;action={'tool':'profile_dataset','arguments':{'dataset_id':stale_id}}
   elif 'profile_dataset' not in done:action={'tool':'profile_dataset','arguments':{'dataset_id':source['id']}}
   elif 'summarize_research_data' not in done:action={'tool':'summarize_research_data','arguments':{'dataset_id':source['id'],**spec}}
   elif 'write_research_brief' in done and case_name in ['retail_review_repair','retail_review_reject'] and (case_name=='retail_review_reject' or (review_counts.get(provider+'-'+case_name,0)==1 and done['write_research_brief'].get('review_evidence',{}).get('title')!=case_name+' findings')):action={'tool':'write_research_brief','arguments':{'analysis_id':done['summarize_research_data']['analysis_id'],'title':case_name+' findings','filename':case_name+'_brief.docx'}}
   elif a.quality_cases and 'calculate_dataset_rate' not in done:
    if not any(t.get('function',{}).get('name')=='calculate_dataset_rate' for t in body.get('tools',[])):action={'tool':'discover_tools','arguments':{'names':['calculate_dataset_rate']}}
    else:action={'tool':'calculate_dataset_rate','arguments':{'dataset_id':source['id'],'label':'North share','numerator_filters':[{'column':'branch','values':['N']}]}}
   elif a.quality_cases and case_name=='empty_without_export':action={'answer':''}
   elif 'write_research_brief' not in done:action={'tool':'write_research_brief','arguments':{'analysis_id':done['summarize_research_data']['analysis_id'],'title':('Wrong initial title' if case_name in ['retail_review_repair','retail_review_reject'] else case_name+' findings'),'filename':case_name+'_brief.docx'}}
   elif a.quality_cases:
    repaired=any('Percentage arithmetic mismatch' in m.get('content','') for m in body['messages'])
    value='50%' if repaired and case_name=='numeric_repair' else '20%'
    action={'answer':('' if case_name in ['empty_handoff','truncated_empty','empty_unmet_output','empty_without_export'] else 'North share: '+value+' (6/12 rows).')}
   else:action={'answer':'Created the requested brief with checked tables and charts: '+done['write_research_brief']['path']}
   finish='length' if case_name=='truncated_empty' and 'answer' in action else 'stop'
   text=action.get('answer','');call=None
   if not is_review and (body.get('format') or (provider=='mlx' and not body.get('stream') and 'answer' in action)):text=json.dumps(action)
   elif 'tool' in action:call={'id':'fixture_'+str(len(requests)),'type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments'])}}
   self.send_response(200)
   if provider=='ollama':
    self.send_header('Content-Type','application/x-ndjson');self.end_headers()
    message={'role':'assistant','content':text}
    if call:message['tool_calls']=[{'function':{'name':action['tool'],'arguments':action['arguments']}}]
    self.wfile.write((json.dumps({'message':message,'done':True,'done_reason':finish})+'\n').encode())
   elif body.get('stream'):
    self.send_header('Content-Type','text/event-stream');self.end_headers()
    delta={'tool_calls':[{'index':0,**call}]} if call else {'content':text}
    self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':'tool_calls' if call else finish}]})+'\n\ndata: [DONE]\n\n').encode())
   else:
    self.send_header('Content-Type','application/json');self.end_headers()
    message={'content':None,'tool_calls':[call]} if call else {'content':text}
    self.wfile.write(json.dumps({'choices':[{'message':message,'finish_reason':'tool_calls' if call else finish}]}).encode())
  except Exception as e:
   errors.append(str(e));self.send_response(500);self.end_headers()

server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 for provider in ['ollama','mlx']:
  selected=[(name,'csv',cases[0][2],cases[0][3]) for name in ['empty_handoff','numeric_repair','numeric_reject','truncated_empty','empty_unmet_output','empty_without_export']] if a.quality_cases else cases+[(name,'csv',cases[0][2],cases[0][3]) for name in ['repeated_wrong_source','repeated_discovery','stale_analysis_list']]
  for case_name,fmt,frame,spec in selected:
   loop=case_name=='repeated_wrong_source';attempted_stale=a.quality_cases
   out=root/(provider+'-'+case_name);support=out/'support';support.mkdir(parents=True)
   (support/'worker-env').symlink_to(Path.home()/'Library/Application Support/Squipi/worker-env',target_is_directory=True)
   project=Project(support/'projects/default',AllowList());ctx=JobContext(project,lambda *args:None,lambda:False)
   stale=out/'old-unrelated.csv';pd.DataFrame({'old_value':[999999]}).to_csv(stale,index=False);project.allow.allow(stale)
   stale_id=registry.call('import_dataset',{'path':str(stale)},ctx)['dataset_id']
   source=out/(case_name+'.'+fmt)
   if fmt=='csv':frame.to_csv(source,index=False)
   elif fmt=='parquet':frame.to_parquet(source,index=False)
   else:frame.to_excel(source,index=False)
   before=source.read_bytes()
   env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_PROVIDER':provider,'SQUIPI_TEST_OLLAMA_URL':f'http://127.0.0.1:{server.server_port}','SQUIPI_TEST_MLX_URL':f'http://127.0.0.1:{server.server_port}/v1','PYTHONDONTWRITEBYTECODE':'1'}
   instruction=f'Analyze {source.name} using the requested measures and groupings, and create a Word brief with a labelled chart. Save as "{case_name}_brief.docx". Use the exact report title "{case_name} findings". Keep the source unchanged.'
   if case_name=='empty_unmet_output':instruction += ' Also create Excel output named "Required table.xlsx".'
   run=subprocess.run([str(a.binary.resolve()),'--agent',instruction,str(source)],env=env,capture_output=True,text=True,timeout=150)
   (out/'run.log').write_text(run.stdout+run.stderr);assert run.returncode==0,(provider,case_name,run.stderr)
   reply=json.loads((support/'last-agent-result.json').read_text());steps=read_steps(support/'projects/default')
   assert source.read_bytes()==before
   assert not any(s['ok'] and s['arguments'].get('dataset_id')==stale_id for s in steps)
   if case_name=='stale_analysis_list':
    blocked=next(s for s in steps if s['tool']=='write_research_results_brief')
    assert not blocked['ok'] and 'older analysis cannot replace' in json.dumps(blocked['result']), blocked
   if a.quality_cases:
    expected='partial' if case_name in ['numeric_reject','truncated_empty','empty_unmet_output','empty_without_export'] else 'completed'
    assert reply['status']==expected,(provider,case_name,reply)
    exports=[s for s in steps if s['tool']=='write_research_brief' and s['ok']]
    assert len(exports)==(0 if case_name=='empty_without_export' else 1), (case_name,'completed work was repeated or invented')
    if exports:assert exports[0]['result']['verification']['passed']
    rates=[s for s in steps if s['tool']=='calculate_dataset_rate' and s['ok']]
    assert len(rates)==1 and rates[0]['result']['percentage']==50
    if case_name=='empty_handoff':assert 'empty final message' in reply['text'] and exports[0]['result']['path'] in reply['text']
    if case_name=='numeric_repair':assert '50%' in reply['text'] and '20%' not in reply['text']
    if case_name=='numeric_reject':assert 'withheld' in reply['text'] and 'Numerical interpretation' in reply['error']
   elif loop:
    assert reply['status']=='partial' and 'three identical' in reply['error'],reply
    assert len([s for s in steps if s['tool']=='profile_dataset'])==3
   elif case_name=='repeated_discovery':
    assert reply['status']=='partial' and 'three repeated capability' in reply['error'],reply
    assert len([s for s in steps if s['tool']=='discover_tools'])==3
   elif case_name=='retail_review_reject':
    assert reply['status']=='partial' and 'three bounded' in reply['error'],reply
    assert review_counts[provider+'-'+case_name]==3
   else:
    # An intentionally rejected stale package handle remains a conservative partial status.
    # This case verifies the source boundary and the valid replacement artifact.
    assert reply['status']==('partial' if case_name=='stale_analysis_list' else 'completed'),(provider,case_name,reply)
    brief=next(s['result'] for s in reversed(steps) if s['tool']=='write_research_brief' and s['ok'])
    assert brief['verification']['passed'] and Path(brief['path']).name==case_name+'_brief.docx'
    assert brief['charts_embedded']>=1
    assert review_counts.get(provider+'-'+case_name,0)==0, 'Deferred model reviewer must not run'
   results.append({'provider':provider,'case':case_name,'passed':True,'task_status':reply['status'],'stale_source_blocked':not a.quality_cases,'original_unchanged':True,'semantic_review_calls':review_counts.get(provider+'-'+case_name,0)})
 assert not errors,errors
 (root/'verification.json').write_text(json.dumps({'scripted_model':True,'results':results},indent=2));print(json.dumps(results,indent=2))
finally:
 server.shutdown();(root/'requests.json').write_text(json.dumps(requests,indent=2))
