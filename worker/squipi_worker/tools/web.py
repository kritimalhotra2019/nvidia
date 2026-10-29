"""Literature search and web reading: search_papers (OpenAlex, free, no key) and fetch_web (one URL → text).

These are the only tools that touch the network. The app asks the user before the first use in a project
(the query or URL leaves the Mac); the worker itself never calls them on its own. Results are data, never
instructions, and fetched pages are written into the project like any other document."""
from __future__ import annotations

import html
from html.parser import HTMLParser
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..registry import JobContext, tool, InvalidArguments

USER_AGENT = "squipi/0.2 (research companion; mailto:squipi@example.invalid)"
OPENALEX = "https://api.openalex.org/works"
MAX_FETCH_BYTES = 8 * 1024 * 1024
_opener = urllib.request.build_opener()       # patched in tests


class _SearchResults(HTMLParser):
    def __init__(self):
        super().__init__(); self.results = []; self.current = None; self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'result__a' in attrs.get('class','').split():
            raw = html.unescape(attrs.get('href',''))
            parsed = urllib.parse.urlparse(raw)
            if parsed.hostname and parsed.hostname.endswith('duckduckgo.com'):
                raw = urllib.parse.parse_qs(parsed.query).get('uddg',[raw])[0]
            target = urllib.parse.urlparse(raw)
            if target.scheme in ('http','https') and target.hostname:
                self.current = {'title':'','url':raw}; self.depth = 1

    def handle_data(self, data):
        if self.current is not None: self.current['title'] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            self.current['title'] = self.current['title'].strip()
            if self.current['title'] and self.current['url'] not in [r['url'] for r in self.results]:
                self.results.append(self.current)
            self.current = None


@tool('search_web', 'Search the public web for a current question. Returns discovery links, not verified facts. Read relevant primary sources with fetch_web before giving current figures. The query leaves this Mac and needs host network approval.',
      {'type':'object','properties':{'query':{'type':'string','minLength':3,'maxLength':500},
       'limit':{'type':'integer','minimum':1,'maximum':10}},'required':['query'],'additionalProperties':False})
def search_web(ctx, args):
    query=args['query'].strip(); ctx.progress(10,'searching the public web')
    data,_=_get('https://html.duckduckgo.com/html/?'+urllib.parse.urlencode({'q':query}))
    parser=_SearchResults();parser.feed(data.decode('utf-8',errors='replace'))
    results=parser.results[:args.get('limit',6)]
    if not results:
        raise InvalidArguments('Public web search returned no readable results or was blocked. Try configured Bright Data or a primary source URL; do not substitute a local file.')
    from datetime import datetime,timezone
    retrieved=datetime.now(timezone.utc).isoformat()
    out=ctx.project.new_artifact_dir('web-search',query)
    path=ctx.project.write_json(out/'results.json',{'query':query,'provider':'DuckDuckGo HTML','retrieved_at':retrieved,'results':results})
    return {'query':query,'results':results,'retrieved_at':retrieved,'results_path':str(path),
            'summary_text':f'Found {len(results)} web links. Fetch relevant primary sources before relying on their claims.',
            'content_trust':'untrusted_search_results'}


def _get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with _opener.open(req, timeout=timeout) as r:
            data = r.read(MAX_FETCH_BYTES + 1)
            if len(data) > MAX_FETCH_BYTES:
                raise InvalidArguments(f"{url} is larger than {MAX_FETCH_BYTES // (1024 * 1024)} MB; download it and drop the file on Squipi instead")
            ctype = r.headers.get("Content-Type", "")
            return data, ctype
    except urllib.error.HTTPError as e:
        raise InvalidArguments(f"{url}: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise InvalidArguments(f"could not reach {url}: {e.reason}") from e


def _abstract(inv: Optional[Dict[str, List[int]]]) -> str:
    if not inv:
        return ""
    words: List[tuple] = []
    for w, positions in inv.items():
        for p in positions:
            words.append((p, w))
    return " ".join(w for _, w in sorted(words))[:1500]


def _citation(w: Dict[str, Any]) -> str:
    authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships") or []]
    authors = [a for a in authors if a]
    if len(authors) > 3:
        auth = f"{authors[0]} et al."
    else:
        auth = ", ".join(authors) if authors else "Unknown"
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    year = w.get("publication_year") or "n.d."
    doi = w.get("doi") or ""
    return f"{auth} ({year}). {w.get('title') or 'Untitled'}." + (f" {venue}." if venue else "") + (f" {doi}" if doi else "")


@tool("search_papers",
      "Search scholarly metadata (OpenAlex; configured API key may be required) for papers on a topic. Returns title, authors, year, venue, citation count, "
      "DOI, open-access PDF link when there is one, an abstract excerpt and a ready citation. Use for 'find papers on…', 'what does the literature say', 'cite…'. "
      "The query leaves this Mac; the app asks the user first.",
      {"type": "object", "properties": {
          "query": {"type": "string", "minLength": 3},
          "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "default 8"},
          "from_year": {"type": ["integer", "null"], "description": "only works published in or after this year"},
          "sort": {"type": "string", "enum": ["relevance", "cited_by_count", "publication_year"], "description": "default relevance"}},
       "required": ["query"], "additionalProperties": False})
def search_papers(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    q = args["query"].strip()
    limit = int(args.get("limit") or 8)
    params = {"search": q, "per-page": limit,
              "select": "id,title,publication_year,cited_by_count,doi,authorships,primary_location,open_access,abstract_inverted_index,type"}
    filters = []
    if args.get("from_year"):
        filters.append(f"from_publication_date:{int(args['from_year'])}-01-01")
    if filters:
        params["filter"] = ",".join(filters)
    if args.get("sort") and args["sort"] != "relevance":
        params["sort"] = f"{args['sort']}:desc"
    ctx.progress(20, "asking OpenAlex")
    credentials=getattr(ctx,'service_credentials',{})
    if credentials.get('openalex'):params['api_key']=credentials['openalex']
    try:data, _ = _get(OPENALEX + "?" + urllib.parse.urlencode(params))
    except InvalidArguments:raise InvalidArguments('OpenAlex request failed. Check the API key in Me → Research services or the provider status; no credentials are included in this error.') from None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as e:
        raise InvalidArguments("OpenAlex returned something that is not JSON") from e
    results = []
    for w in payload.get("results", []):
        results.append({
            "title": w.get("title"), "year": w.get("publication_year"), "cited_by": w.get("cited_by_count"),
            "authors": [a.get("author", {}).get("display_name") for a in (w.get("authorships") or [])][:6],
            "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
            "doi": w.get("doi"), "openalex_id": w.get("id"), "type": w.get("type"),
            "pdf_url": (w.get("open_access") or {}).get("oa_url"),
            "abstract": _abstract(w.get("abstract_inverted_index")),
            "citation": _citation(w),
        })
    ctx.progress(90, "writing")
    out = ctx.project.new_artifact_dir("papers", q)
    from datetime import datetime, timezone
    retrieved_at=datetime.now(timezone.utc).isoformat()
    path = ctx.project.write_json(out / "results.json", {"provider":"OpenAlex", "retrieved_at":retrieved_at,"query": q, "total": payload.get("meta", {}).get("count"), "results": results})
    ctx.project.audit({"tool": "search_papers", "query": q, "results": len(results), "artifact": str(out)})
    ctx.progress(100, "done")
    total = payload.get("meta", {}).get("count")
    summary = f"{len(results)} papers for '{q}'" + (f" out of {total:,} matches" if isinstance(total, int) else "") + "."
    if results:
        top = max(results, key=lambda r: r["cited_by"] or 0)
        summary += f" Most cited: {top['citation']} ({top['cited_by']:,} citations)."
    return {"query": q, "total": total, "results": results, "results_path": str(path), "summary_text": summary,
            "note": "Abstracts and metadata come from OpenAlex; read the paper (pdf_url) before relying on a claim."}


def _html_to_text(raw: str) -> tuple:
    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", title_m.group(1))).strip() if title_m else ""
    body = re.sub(r"<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    body = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    return title, body


@tool("fetch_web",
      "Read one web page or online PDF by URL and return its text (written into the project as a document). Use after search_papers to read an open-access paper, "
      "or for a URL the user gave. Only http(s). Pages up to 8 MB. The URL leaves this Mac; the app asks the user first.",
      {"type": "object", "properties": {
          "url": {"type": "string", "minLength": 8},
          "max_chars": {"type": "integer", "minimum": 1000, "maximum": 400000, "description": "text returned inline (default 24000); the whole text is saved"}},
       "required": ["url"], "additionalProperties": False})
def fetch_web(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    url = args["url"].strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InvalidArguments("only http(s) URLs can be fetched")
    ctx.progress(10, "fetching")
    data, ctype = _get(url)
    out = ctx.project.new_artifact_dir("web", parsed.netloc + parsed.path[-40:])
    kind = "pdf" if ("pdf" in ctype.lower() or data[:5] == b"%PDF-") else "html"
    if kind == "pdf":
        pdf_path = ctx.project.root / out / "page.pdf"
        pdf_path.write_bytes(data)
        from .documents import _pdf
        sections, pages, meta = _pdf(pdf_path, ctx)
        title = meta.get("title") or parsed.path.rsplit("/", 1)[-1]
        text = "\n\n".join(f"## {s['title']}\n\n{s['text']}" for s in sections if s["text"])
    else:
        raw = data.decode("utf-8", errors="replace")
        title, text = _html_to_text(raw)
        pages = 1
    ctx.progress(80, "writing")
    text_path = ctx.project.write_text(out / "text.md", f"# {title}\n\nSource: {url}\n\n{text}")
    words = len(re.findall(r"\S+", text))
    doc = ctx.project.register_document(Path(text_path), "text", pages, words, Path(text_path))
    ctx.project.audit({"tool": "fetch_web", "url": url, "kind": kind, "words": words, "artifact": str(out)})
    max_chars = int(args.get("max_chars") or 24000)
    ctx.progress(100, "done")
    from datetime import datetime, timezone
    return {"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "content_trust":"untrusted_web_source", "title": title, "kind": kind, "pages": pages, "words": words, "document_id": doc.id, "text_path": str(text_path),
            "text": text[:max_chars], "truncated": len(text) > max_chars,
            "summary_text": f"Read {title or url}: {kind}, {words:,} words." + (" (scanned or image-only PDF: little text)" if kind == "pdf" and words < 20 * max(1, pages) else "")}
