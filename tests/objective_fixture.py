"""Controlled protocol peer for the request-specification phase, not model reasoning."""
import json

def serve_contract(handler, body):
    if not any('OBJECTIVE_CONTRACT_COMPILER' in m.get('content','') for m in body.get('messages',[])):
        return False
    payload=next((json.loads(m['content']) for m in reversed(body['messages']) if m.get('role')=='user' and m.get('content','').startswith('{')),None)
    assert payload and payload['input_ids']
    action={'tool':'set_research_requirements','arguments':{'requirements':[{
        'id':'fixture-selected-sources','kind':'source_used','quote':payload['user_messages'][0][:900],
        'description':'Use the selected sources','source_ids':payload['input_ids']}]}}
    handler.send_response(200)
    if '/api/' in handler.path:
        handler.send_header('Content-Type','application/x-ndjson');handler.end_headers()
        handler.wfile.write((json.dumps({'message':{'role':'assistant','content':json.dumps(action)},'done':True,'done_reason':'stop'})+'\n').encode())
    else:
        handler.send_header('Content-Type','application/json');handler.end_headers()
        handler.wfile.write(json.dumps({'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}]}).encode())
    return True
