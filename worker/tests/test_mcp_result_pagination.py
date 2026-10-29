"""Oversized responses remain recoverable through Squipi's own MCP toolset."""
import hashlib
import json
from pathlib import Path

import pytest

from squipi_worker import mcp
from squipi_worker.project import Project
from squipi_worker.registry import InvalidArguments
from squipi_worker.research import ResearchStore
from squipi_worker.security import AccessDenied, AllowList


@pytest.fixture
def server(tmp_path):
    return mcp.Server(Project(tmp_path / 'project', AllowList()), mcp.AppMemory(tmp_path / 'app'))


def call(server, name, args):
    response = server.handle({'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}})['result']
    assert response['isError'] is False, response
    text = response['content'][0]['text']
    assert len(text) <= mcp.MCP_INLINE_CHARS
    return json.loads(text)


@pytest.mark.parametrize('text', ['evidence with quotes " and newlines\n' * 4000,
                                '最後のページ🙂 cafe\u0301\n' * 8000,
                                '\x00\x01\x02' * 20000])
def test_large_response_round_trip_restarts_and_does_not_repeat_original_action(server, monkeypatch, text):
    original = {'text': text, 'rows': [1, 2, 3], 'tail': 'FINAL-EVIDENCE-30'}
    calls = []
    def action(fact):
        calls.append(fact)
        return original['text']
    monkeypatch.setattr(server.memory, 'remember', action)
    envelope = call(server, 'remember_fact', {'fact': 'synthetic test'})
    assert envelope['truncated'] and envelope['next_call']['name'] == 'read_tool_result'
    # A brand new MCP process can resolve the same ID from project-local storage.
    restarted = mcp.Server(Project(server.project.root, AllowList()), server.memory)
    pieces = []
    args = envelope['next_call']['arguments']
    end = 0
    while args:
        result = call(restarted, 'read_tool_result', args)
        assert result['offset'] == end
        pieces.append(result['text'])
        end = result['end_offset']
        assert result['complete'] == (result['next_offset'] is None)
        args = None if result['next_offset'] is None else {'tool_result_id': result['tool_result_id'], 'offset': result['next_offset']}
    assert json.loads(''.join(pieces)) == {'summary_text': text}
    assert calls == ['synthetic test']


def test_small_result_is_not_wrapped(server):
    value = {'text': 'short response', 'rows': 12}
    assert json.loads(server._transport_text(value)) == value
    assert not (server.project.root / 'artifacts/tool-results').exists()


@pytest.mark.parametrize('args', [
    {'tool_result_id': '../../private.txt'},
    {'tool_result_id': 'result_' + 'a'*64, 'offset': -1},
    {'tool_result_id': 'result_' + 'a'*64, 'offset': True},
    {'tool_result_id': 'result_' + 'a'*64, 'max_chars': 9000},
    {'tool_result_id': 'result_' + 'a'*64, 'path': '/tmp/not-a-result'},
])
def test_retrieval_rejects_invalid_arguments(server, args):
    response = server.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'read_tool_result', 'arguments': args}})
    assert response['result']['isError']


def test_missing_modified_out_of_range_or_external_symlink_rejected(server, tmp_path):
    envelope = json.loads(server._transport_text({'text': 'large ' * 10000}))
    result_id = envelope['tool_result_id']
    with pytest.raises(InvalidArguments, match='exceeds'):
        server.call('read_tool_result', {'tool_result_id': result_id, 'offset': envelope['total_chars']+1})
    path = server._result_path(result_id)
    path.write_text('modified')
    with pytest.raises(InvalidArguments, match='modified'):
        server.call('read_tool_result', {'tool_result_id': result_id})
    path.unlink()
    with pytest.raises(InvalidArguments, match='No saved'):
        server.call('read_tool_result', {'tool_result_id': result_id})
    outside = tmp_path / 'outside.json'
    outside.write_text('private data')
    path.symlink_to(outside)
    with pytest.raises(AccessDenied):
        server.call('read_tool_result', {'tool_result_id': result_id})


def test_cache_failure_preserves_success_and_explains_unavailable_recovery(server, monkeypatch):
    monkeypatch.setattr(server.project, 'write_text', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    response = json.loads(server._transport_text({'summary_text': 'Action completed. ' * 5000, 'persistence_warning': 'Journal unavailable.'}))
    assert response['truncated']
    assert response['next_call'] is None
    assert response['tool_result_id'] is None
    assert 'disk full' in response['retrieval_warning']
    assert response['persistence_warning'] == 'Journal unavailable.'
    assert 'Action completed' in response['preview']


def test_escaped_warning_is_bounded_and_transport_terminates(server):
    response = server._transport_text({'text': 'Evidence ' * 10000, 'persistence_warning': '\x00' * 4000})
    assert len(response) <= mcp.MCP_INLINE_CHARS
    assert len(json.dumps({'result': response}, ensure_ascii=False)) < 39321
    assert 'persistence_warning' in json.loads(response)


@pytest.mark.parametrize('escaped', [False, True])
def test_document_journal_coverage_matches_text_actually_delivered(server, escaped):
    path=server.project.root / 'many-sections.md'
    body='Evidence with quotes " slash \\ and controls \x00🙂. ' if escaped else 'Synthetic survey evidence. '
    path.write_text('\n\n'.join(f'## Section {i}\n\n{body * 4}' for i in range(250)), 'utf-8')
    task=ResearchStore(server.project.root).start_research_task('Read the entire document.', [str(path)])['task_id']
    server.task_id=task
    pieces=[]
    args={'path':str(path),'max_chars':12000}
    end=0
    while args:
        result=call(server,'read_document',args)
        assert 'text' in result and 'tool_result_id' not in result
        assert result['offset']==end
        pieces.append(result['text'])
        end=result['end_offset']
        args=result.get('next_call',{}).get('arguments')
    full=''.join(pieces)
    assert full==Path(result['text_path']).read_text('utf-8')
    journal=ResearchStore(server.project.root).read_research_task(task)['steps']
    assert [step['result']['text'] for step in journal]==pieces
    assert end==result['total_chars']


def test_document_storage_warning_does_not_replace_delivered_text_with_preview(server, monkeypatch):
    path=server.project.root / 'evidence.txt'
    path.write_text('Research evidence. '*2000)
    monkeypatch.setattr(server,'_checkpoint',lambda *args:'\x00'*4000)
    result=call(server,'read_document',{'path':str(path),'max_chars':12000})
    assert 'text' in result and 'tool_result_id' not in result


def test_reading_current_journal_does_not_embed_itself_recursively(server):
    store=ResearchStore(server.project.root)
    task=store.start_research_task('Read a source and verify history.', [])['task_id']
    store.record_research_step(task, 'read_document', {}, {'text':'Evidence '*2000}, True)
    server.task_id=task
    for expected_count in range(1,8):
        response=server.call('read_research_task', {'task_id':task})
        assert len(response['steps'])==expected_count
    saved=store.read_research_task(task)['steps']
    assert saved[0]['result']['text']=='Evidence '*2000
    for count, step in enumerate(saved[1:],1):
        assert step['result']['retrieval_reference']['through_step_count']==count
        assert 'steps' not in step['result'] and len(json.dumps(step['result']))<500
