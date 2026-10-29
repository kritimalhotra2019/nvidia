from pathlib import Path
import pytest
from squipi_worker import tools
from squipi_worker.project import Project
from squipi_worker.registry import JobContext,registry,InvalidArguments
from squipi_worker.security import AllowList,AccessDenied
from squipi_worker.tools import web

def test_search_needs_host_network_permission(tmp_path):
    ctx=JobContext(Project(tmp_path/'project',AllowList()),lambda *_:None,lambda:False)
    with pytest.raises(AccessDenied):registry.call('search_web',{'query':'synthetic evidence'},ctx)

def test_search_returns_only_discovery_links_and_timestamp(tmp_path,monkeypatch):
    page=b'<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fevidence">Primary evidence</a><a class="result__a" href="javascript:alert(1)">Invalid</a>'
    monkeypatch.setattr(web,'_get',lambda *_:(page,'text/html'))
    ctx=JobContext(Project(tmp_path/'project',AllowList()),lambda *_:None,lambda:False,allow_network=True)
    r=registry.call('search_web',{'query':'synthetic evidence'},ctx)
    assert r['results']==[{'title':'Primary evidence','url':'https://example.org/evidence'}]
    assert r['retrieved_at'] and r['content_trust']=='untrusted_search_results'
    assert Path(r['results_path']).is_file()

def test_blocked_search_is_an_error_not_an_empty_success(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'_get',lambda *_:(b'<p>challenge required</p>','text/html'))
    ctx=JobContext(Project(tmp_path/'project',AllowList()),lambda *_:None,lambda:False,allow_network=True)
    with pytest.raises(InvalidArguments,match='blocked'):registry.call('search_web',{'query':'synthetic evidence'},ctx)
