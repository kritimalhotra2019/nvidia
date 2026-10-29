"""Real native host + NeMo + worker, with a clearly controlled model endpoint."""
import json, os, sys, threading, subprocess, uuid, argparse
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'experiments/nemo'),str(root/'experiments/nemo/tests'),str(root/'worker')]
from test_trial import ModelFixture,server_for,seed
from squipi_nemo.state import Job,digest
parser=argparse.ArgumentParser();parser.add_argument('--binary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--runtime',type=Path,default=root/'experiments/nemo/.venv');args=parser.parse_args()
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
support=out/'app-support';support.mkdir(exist_ok=True)
for name in ['worker-env','nemo-env']:
 p=support/name
 if not p.exists():p.symlink_to(args.runtime.resolve(),target_is_directory=True)
source,_=seed(out/'inputs')
jobid=str(uuid.uuid4()).upper();jobpath=support/'research-jobs'/jobid
fixture=ModelFixture(jobpath);server=server_for(fixture);threading.Thread(target=server.serve_forever,daemon=True).start()
env=os.environ|{'SQUIPI_TEST_DATA_DIR':str(support),'SQUIPI_TEST_OLLAMA_URL':f'http://127.0.0.1:{server.server_port}', 'SQUIPI_OLLAMA_MODEL':'controlled-fixture','SQUIPI_TEST_NEMO_JOB':jobid}
command=[str(args.binary.resolve()),'--agent','Remove name and email, save cleaned Excel, and produce Word and PowerPoint descriptive reports with labelled graphs.',str(source)]
try:
 first=subprocess.run(command,env=env,capture_output=True,text=True,timeout=160);(out/'first.log').write_text(first.stdout+'\n'+first.stderr)
 assert first.returncode==0 and 'TASK_STATUS: completed' in first.stdout,first.stdout+first.stderr
 job=Job(jobpath);before={p:digest(p) for p in job.output_files()};assert len(before)>0;job.close()
 second=subprocess.run([str(args.binary.resolve()),'--agent','continue'],env=env,capture_output=True,text=True,timeout=80);(out/'resume.log').write_text(second.stdout+'\n'+second.stderr)
 assert second.returncode==0 and 'TASK_STATUS: completed' in second.stdout,second.stdout+second.stderr
 job=Job(jobpath);status=job.status();assert status['completed_tools'].count('clean_dataset')==1;job.close()
 assert all(digest(p)==sha for p,sha in before.items())
 assert len(fixture.requests)==5, len(fixture.requests)
 (out/'verification.json').write_text(json.dumps({'native_bridge':'passed','resume_preserved_outputs':True,'model_requests':len(fixture.requests),'status':status},indent=2))
 print('Native NeMo bridge passed: cleaning, Excel, Word, PowerPoint, exact resume, no repeated writes. Controlled model fixture.')
finally:server.shutdown();server.server_close()
