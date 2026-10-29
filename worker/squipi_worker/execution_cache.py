"""Objective-scoped reuse of verified deterministic results across process restarts.

Scope is supplied by the native host, never a model argument. Only successful,
read/transform/export operations are reusable. Input bytes, role overrides, code
and output bytes are checked; memory, plans, network and desktop actions never replay.
"""
import hashlib
import json
import re
import sqlite3
import importlib.metadata
import os
from pathlib import Path
from .project import _json_default

ELIGIBLE = frozenset({'profile_dataset','clean_dataset','find_insights','compare_groups','correlate',
    'aggregate','calculate_dataset_rate','regress','create_chart','export_excel','report_dataset','export_document',
    'read_document','search_document','combine_datasets','summarize_research_data','write_research_brief','economic_appraisal','plan_research_power'})
ELIGIBLE = ELIGIBLE | frozenset({'validate_research_data','redact_text_pii','fit_fixed_effects_model','estimate_causal_effect','survey_summary','extract_research_document','write_research_results_brief'})


def _signature(path):
    p = Path(path)
    if not p.is_file():
        raise ValueError('Cached file is missing')
    digest = hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'path':str(p.resolve()), 'sha256':digest.hexdigest()}


def _output_files(value):
    found = set()
    def visit(obj, key=''):
        if isinstance(obj,dict):
            for k,v in obj.items():visit(v,k)
        elif isinstance(obj,list):
            for v in obj:visit(v,key)
        elif isinstance(obj,str) and (key == 'path' or key.endswith('_path') or key.endswith('_paths')) and Path(obj).is_absolute() and Path(obj).is_file():
            found.add(obj)
    visit(value)
    return sorted(found)


class ExecutionCache:
    def __init__(self, project):
        self.project = project
        self.path = project.root / 'execution-cache.sqlite3'
        if self.path.is_symlink():raise ValueError('Cache cannot be a symbolic link')
        code_files=list(Path(__file__).parent.rglob('*.py'))+list(Path(__file__).parent.rglob('*.R'))
        versions={}
        for name in ['pandas','numpy','scipy','matplotlib','pandera','presidio-analyzer','presidio-anonymizer','en-core-web-sm','docling','pyfixest','dowhy','onnxruntime','torch','rapidocr','openpyxl','pyarrow','python-docx','python-pptx','pypdf']:
            try:versions[name]=importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:versions[name]=None
        self.code = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(code_files))+json.dumps(versions,sort_keys=True).encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS reuse (scope TEXT, key TEXT, result TEXT, outputs TEXT, PRIMARY KEY(scope,key))')

    def _key(self, tool, args):
        args=dict(args)
        if tool=='summarize_research_data':
            # Explicit defaults and omitted defaults describe the same work.
            # Keep changed measures, units, weights and grouping distinct.
            for key,default in {'charts':True,'allow_sample':False,'group_by':[],
                                'categorical_columns':[],'paired_changes':[],'units':{}}.items():
                args.setdefault(key,default)
        inputs=[]
        for key,value in args.items():
            if key == 'dataset_id':
                for ds in self.project.dataset_lineage(value):
                    p=self.project.allow.check_readable(ds.path)
                    inputs.append([ds.id,_signature(p),ds.overrides,ds.sheet])
            elif key == 'dataset_ids':
                for item in value:
                    for ds in self.project.dataset_lineage(item):
                        inputs.append([ds.id,_signature(self.project.allow.check_readable(ds.path)),ds.overrides,ds.sheet])
            elif key == 'path':inputs.append(_signature(self.project.allow.check_readable(value)))
            elif key == 'analysis_id':
                if value.startswith('research_'):
                    from .tools.research_packages import load_package_analysis
                    load_package_analysis(self.project,value)
                    index=json.loads((self.project.root/'package-analyses.json').read_text())
                    inputs.append(_signature(index[value]['path']))
                    continue
                from .tools.research_analysis import load_analysis
                saved=load_analysis(self.project,value)
                index=json.loads((self.project.root/'research-analyses.json').read_text())
                inputs.append(_signature(index[value]['path']))
                inputs.append(_signature(self.project.allow.check_readable(saved['dataset_path'])))
            elif key == 'analysis_ids':
                from .tools.research_packages import load_package_analysis
                for identifier in value:
                    load_package_analysis(self.project,identifier)
                    index=json.loads((self.project.root/'package-analyses.json').read_text())
                    inputs.append(_signature(index[identifier]['path']))
            elif key == 'document_id':
                # Document chunks can change independently; don't reuse without a
                # verified source identity in this version.
                return None
        if tool=='survey_summary' and os.environ.get('SQUIPI_RSCRIPT'):
            inputs.append(_signature(os.environ['SQUIPI_RSCRIPT']))
        if tool=='extract_research_document':
            models=Path(os.environ.get('SQUIPI_DOCUMENT_MODELS','/nonexistent'))
            if models.is_dir():
                inputs.extend(_signature(p) for p in sorted(models.rglob('*')) if p.is_file() and not any(part.startswith('.') for part in p.relative_to(models).parts))
        library=self.project.root/'templates.json'
        if tool in {'report_dataset','export_document','write_research_brief'}:
            # A changed imported template/default invalidates a previous export.
            if library.exists():
                inputs.append(_signature(library))
                data=json.loads(library.read_text())
                for item in data.get('templates',[]):
                    inputs.append(_signature(self.project.root/'templates'/item['filename']))
        return hashlib.sha256(json.dumps([self.code,tool,args,inputs],sort_keys=True).encode()).hexdigest()

    def lookup(self,tool,args,scope):
        """Host-only inspection: return a verified hit, never execute on a miss."""
        if tool not in ELIGIBLE or not isinstance(args,dict) or not isinstance(scope,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',scope):
            return None
        try:key=self._key(tool,args)
        except (OSError,ValueError,KeyError,TypeError,AttributeError):return None
        if key is None:return None
        with sqlite3.connect(self.path) as db:
            row=db.execute('SELECT result,outputs FROM reuse WHERE scope=? AND key=?',(scope,key)).fetchone()
        if row:
            try:
                outputs=json.loads(row[1])
                if all(_signature(item['path'])==item for item in outputs):
                    result=json.loads(row[0])
                    # A derived dataset must still be registered, not just on disk.
                    if result.get('dataset_id'):self.project.dataset(result['dataset_id'])
                    if result.get('document_id'):self.project.document(result['document_id'])
                    if result.get('analysis_id'):
                        if result['analysis_id'].startswith('research_'):
                            from .tools.research_packages import load_package_analysis
                            load_package_analysis(self.project,result['analysis_id'])
                        else:
                            from .tools.research_analysis import load_analysis
                            load_analysis(self.project,result['analysis_id'])
                    result['reused_saved_result']=True
                    result['summary_text']='Reused saved result. '+result.get('summary_text','')
                    return result
            except (OSError,ValueError,KeyError,TypeError,AttributeError):pass
        return None

    def call(self, tool, args, ctx, scope, execute):
        ctx.check()
        saved=self.lookup(tool,args,scope)
        if saved is not None:return saved
        if tool not in ELIGIBLE or not isinstance(args,dict) or not isinstance(scope,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',scope):
            return execute()
        key=self._key(tool,args)
        if key is None:return execute()
        result=execute()
        outputs=[_signature(p) for p in _output_files(result)]
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO reuse VALUES (?,?,?,?)',(scope,key,json.dumps(result,default=_json_default),json.dumps(outputs)))
        return result
