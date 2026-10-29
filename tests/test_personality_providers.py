"""Actual provider transports against a controlled loopback peer; no paid/cloud calls."""
import argparse,json,os,subprocess,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
p=argparse.ArgumentParser();p.add_argument('--harness',type=Path,required=True);p.add_argument('--profile',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
requests=[];errors=[];source=a.profile.read_text()
class Peer(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  try:
   body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(body)
   system=body['messages'][0]['content'];record=json.loads(system.split('\n',1)[1].split('\nCurrent reasoning backend:',1)[0])
   assert record['content']==source, 'Provider lost the shared personality or tool directory'
   assert 'warm, curious, sharp, resourceful and funny' in system
   assert 'survey_summary' in system and 'panel-econometrics' in system and 'brightdata_search' in system
   if body['model']=='fixture-nebius':
    assert 'sent online' in system and self.headers['Authorization']=='Bearer fixture-not-a-real-key'
   else:assert 'runs locally' in system
   self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
   if self.path=='/api/chat':result={'message':{'role':'assistant','content':'I am Squipi.'},'done':True,'done_reason':'stop'}
   else:result={'choices':[{'message':{'role':'assistant','content':'I am Squipi.'},'finish_reason':'stop'}]}
   self.wfile.write(json.dumps(result).encode())
  except Exception as e:errors.append(str(e));self.send_response(500);self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Peer);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 r=subprocess.run([str(a.harness.resolve()),f'http://127.0.0.1:{server.server_port}',str(a.profile.resolve())],capture_output=True,text=True,timeout=60)
 assert r.returncode==0,(r.stdout,r.stderr,errors)
 assert not errors and len(requests)==3,(errors,len(requests))
 a.output.write_text(json.dumps({'passed':True,'controlled_protocol_peer':True,'live_cloud_request':False,'same_complete_profile_through':['Ollama','MLX','Nebius'],'stdout':r.stdout},indent=2))
 print(r.stdout)
finally:server.shutdown()
