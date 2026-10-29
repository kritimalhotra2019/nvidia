"""Exercise native short-chat routing without any installed worker or model weights."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument('--binary', type=Path, required=True)
args = parser.parse_args()
requests = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(request)
        response = {'message': {'role': 'assistant', 'content': 'Hello!'}, 'done': True, 'done_reason': 'stop'}
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        self.wfile.write((json.dumps(response) + '\n').encode())

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='squipi-short-chat-') as directory:
        env = os.environ | {'SQUIPI_TEST_DATA_DIR': directory, 'SQUIPI_TEST_OLLAMA_URL': f'http://127.0.0.1:{server.server_port}', 'SQUIPI_OLLAMA_MODEL': 'fixture'}
        questions = ['hi how are you', 'Hi! How are you?', 'hello, how are you doing today?', 'Hi Swipey, how are you?', 'what all can you do?', "what's your fav colour", 'What is inflation?', 'Can you explain sampling bias?']
        for text in questions:
            result = subprocess.run([str(args.binary.resolve()), '--agent', text], env=env, text=True, capture_output=True, timeout=20)
            assert result.returncode == 0 and 'TASK_STATUS: completed' in result.stdout, result.stdout + result.stderr
            assert 'installing worker' not in result.stdout
        assert not (Path(directory) / 'worker-env').exists(), 'Casual chat must not install data tools'
        assert len(requests) == len(questions), 'One request per short conversation, no agent retry loop'
        for request in requests:
            assert not request.get('tools'), 'Greetings must not carry the tool catalog'
            assert len(request['messages']) == 2
            assert len(json.dumps(request['messages'])) < (13000 if 'Tool and skill directory' in request['messages'][0]['content'] else 2400)
            assert request.get('think') is False
        assert requests[0]['options']['num_predict'] == 64
        assert sum(len(m['content']) for m in requests[0]['messages']) < 1800
        assert 'SQUIPI PERSONALITY' in requests[0]['messages'][0]['content']
        assert 'Tool and skill directory' not in requests[0]['messages'][0]['content']
        history = [{'role': 'user', 'content': 'Hi how are you'}, {'role': 'assistant', 'content': "I'm hanging out with friends."}]
        history_path = Path(directory) / 'history.json'
        history_path.write_text(json.dumps(history))
        for question in ['what friends', "Good too, what's up"]:
            result = subprocess.run([str(args.binary.resolve()), '--agent', question], env=env | {'SQUIPI_AGENT_HISTORY': str(history_path)}, text=True, capture_output=True, timeout=20)
            assert result.returncode == 0 and 'TASK_STATUS: completed' in result.stdout, result.stdout + result.stderr
            assert 'installing worker' not in result.stdout
            request = requests[-1]
            assert request['messages'][1:3] == history, request
            assert not request.get('tools') and request.get('think') is False
            assert 'simulation disclaimers' in request['messages'][0]['content']
        assert len(requests) == len(questions) + 2
        print('Passed: natural greetings, capabilities and simple explanations; thinking off, one request, no tools or worker setup.')
finally:
    server.shutdown()
    server.server_close()
