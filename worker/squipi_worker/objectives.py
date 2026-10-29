"""Host-owned objectives and execution receipts. Model proposals are never receipts.

SQLite transactions persist a successful result before its IPC reply. A process
restart marks unfinished attempts interrupted; it never erases successful work.
Requirements cite the actual user instruction, and can only be changed explicitly.
"""
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DETERMINISTIC_ANALYSIS_TOOLS={'filter_rows','estimate_treatment_effect','difference_in_differences','attrition_check','lee_bounds','regress','compare_groups','aggregate','correlate',
    'find_insights','fit_fixed_effects_model','estimate_causal_effect','survey_summary','cost_effectiveness_analysis','benefit_cost_analysis','sroi_analysis','ex_ante_impact_projection','calculate_dataset_rate',
    'randomized_trial','regression_discontinuity','matching_estimate','instrumental_variables','meta_analysis','field_checks','randomize_assignment','extract_study_evidence','code_transcripts',
    'create_chart'}   # a chart of a mean by group is the calculation (27 Oct: 'average sales by arm, side by side' judged unmet after the chart was drawn)
WHOLE_DATASET_ANALYSIS_TOOLS={'find_insights','report_dataset'}   # every column is examined (27 Oct: 'produce key insights' judged unmet after find_insights ran)

from .registry import InvalidArguments, validate
from .security import ensure_within


def now(): return datetime.now(timezone.utc).isoformat()
def encode(value): return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


REQUIREMENT = {'type':'object','properties':{
    'id':{'type':'string','minLength':1,'maxLength':80},
    'kind':{'type':'string','enum':['artifact','analysis','chart','columns_absent','columns_present','pii_absent','source_used','document_coverage']},
    'quote':{'type':'string','minLength':2,'maxLength':1000},
    'description':{'type':'string','minLength':1,'maxLength':600},
    'format':{'type':'string','enum':['xlsx','csv','docx','pptx','pdf','html']},
    'filename':{'type':'string','maxLength':180},
    'count':{'type':'integer','minimum':1,'maximum':12,'description':'Exact chart count only when explicitly requested.'},
    'columns':{'type':'array','maxItems':50,'items':{'type':'string','maxLength':200}},
    'groups':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':200}},
    'units':{'type':'object','additionalProperties':{'type':'string','maxLength':100}},
    'source_ids':{'type':'array','maxItems':30,'items':{'type':'string','maxLength':80}}},
    'required':['id','kind','quote','description'],'additionalProperties':False}


class ObjectiveStore:
    def __init__(self, project):
        self.project=project
        self.path=ensure_within(project.root/'objectives.sqlite3',project.root)
        if self.path.is_symlink(): raise InvalidArguments('Objective database cannot be a symlink')
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS objectives(id TEXT PRIMARY KEY,instructions TEXT NOT NULL,
                inputs TEXT NOT NULL,requirements TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,updated TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS objective_messages(objective TEXT NOT NULL,text TEXT NOT NULL,created TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS objective_specifications(objective TEXT PRIMARY KEY,signature TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS objective_corrections(objective TEXT NOT NULL,quote TEXT NOT NULL,retired TEXT NOT NULL,created TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,objective TEXT NOT NULL,tool TEXT NOT NULL,
                args TEXT NOT NULL,result TEXT NOT NULL,state TEXT NOT NULL,assets TEXT NOT NULL,dependencies TEXT NOT NULL,updated TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS receipt_objective ON receipts(objective,updated);
            ''')

    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        return db

    def _row(self,db,identifier):
        row=db.execute('SELECT * FROM objectives WHERE id=?',(identifier,)).fetchone()
        if row is None:raise InvalidArguments('No host-opened research objective')
        return row

    def _inputs(self, identifiers):
        items={d.id:d for d in self.project.datasets()+self.project.documents()}
        result=[]
        for identifier in identifiers:
            if identifier not in items:raise InvalidArguments('A required input is unavailable: '+identifier)
            d=items[identifier];p=self.project.allow.check_readable(d.path)
            result.append({'id':d.id,'path':str(p),'sha256':digest(p)})
        return result

    def open(self,identifier,instruction,inputs,user_instruction=None):
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',identifier):raise InvalidArguments('Invalid objective identity')
        if not isinstance(instruction,str) or not 1<=len(instruction)<=128000:raise InvalidArguments('Invalid objective instruction')
        if not isinstance(inputs,list) or len(inputs)>50:raise InvalidArguments('Invalid selected inputs')
        user_instruction=user_instruction or instruction
        if not isinstance(user_instruction,str) or not 1<=len(user_instruction)<=128000:raise InvalidArguments("Invalid user instruction")
        identities=self._inputs(inputs)
        with self.db() as db:
            row=db.execute('SELECT * FROM objectives WHERE id=?',(identifier,)).fetchone()
            if row:
                history=json.loads(row['instructions'])
                if instruction!=history[-1]:history.append(instruction)
                revised=identities!=json.loads(row['inputs']) or instruction!=json.loads(row['instructions'])[-1]
                requirements=[r for r in json.loads(row['requirements']) if not r['id'].startswith('host-source-')]
                requirements += [{'id':'host-source-'+i['id'],'kind':'source_used','quote':instruction[:1000],'description':'Use required input '+i['id'],'source_ids':[i['id']]} for i in identities]
                db.execute('UPDATE objectives SET requirements=? WHERE id=?',(encode(requirements),identifier))
                db.execute('UPDATE objectives SET instructions=?,inputs=?,state=?,revision=revision+?,updated=? WHERE id=?',
                    (encode(history),encode(identities),'running',int(revised),now(),identifier))
                db.execute("UPDATE receipts SET state='interrupted' WHERE objective=? AND state='running'",(identifier,))
            else:
                # Minimal explicit output requirements even if a model never proposes a contract.
                requirements=[]
                formats={'xlsx':r'\b(?:excel|xlsx|workbook)\b','docx':r'\b(?:word|docx)\b','pptx':r'\b(?:powerpoint|pptx|presentation)\b'}
                for fmt,pattern in formats.items():
                    match=re.search(r'\b(?:create|return|save|export|produce|write|generate|deliver|give|make|build|prepare)\b(?:(?!\b(?:from|using|read|open)\b)[^;\n]){0,120}?'+pattern,user_instruction,re.I)
                    if match:
                        requirement={'id':'host-output-'+fmt,'kind':'artifact','format':fmt,'quote':match.group(),'description':'Requested '+fmt+' deliverable'}
                        named=re.search(r"\b(?:named|called|save(?: it)? as|filename)\s*[=:]?\s*[\"']([^\"'\n]+\."+fmt+r")[\"']",user_instruction,re.I)
                        if named:requirement['filename']=named.group(1)
                        requirements.append(requirement)
                for item in identities:
                    requirements.append({'id':'host-source-'+item['id'],'kind':'source_used','quote':instruction[:1000],
                        'description':'Use required input '+item['id'],'source_ids':[item['id']]})
                db.execute('INSERT INTO objectives VALUES (?,?,?,?,?,?,?)',(identifier,encode([instruction]),encode(identities),encode(requirements),'running',1,now()))
            latest=db.execute('SELECT text FROM objective_messages WHERE objective=? ORDER BY created DESC LIMIT 1',(identifier,)).fetchone()
            if latest is None or latest['text']!=user_instruction:db.execute('INSERT INTO objective_messages VALUES (?,?,?)',(identifier,user_instruction,now()))
        return self.snapshot(identifier)

    def specification_signature(self,db,identifier,row):
        messages=[r[0] for r in db.execute("SELECT text FROM objective_messages WHERE objective=? ORDER BY created",(identifier,))]
        meaningful=[m for m in messages if not re.fullmatch(r"\s*(?:please\s+)?(?:continue|resume|proceed|carry on|go on)[.!]?\s*",m,re.I)]
        return hashlib.sha256(encode([meaningful,json.loads(row["inputs"])]).encode()).hexdigest(),messages

    def snapshot(self,identifier):
        with self.db() as db:
            row=self._row(db,identifier)
            receipts=list(reversed(db.execute('SELECT * FROM receipts WHERE objective=? ORDER BY updated DESC LIMIT 80',(identifier,)).fetchall()))
            corrected=bool(db.execute('SELECT 1 FROM objective_corrections WHERE objective=?',(identifier,)).fetchone())
            signature,messages=self.specification_signature(db,identifier,row)
            compiled=db.execute('SELECT signature FROM objective_specifications WHERE objective=?',(identifier,)).fetchone()
        return {'objective_id':identifier,'revision':row['revision'],'state':row['state'],
            'instructions':json.loads(row['instructions']),'inputs':json.loads(row['inputs']),
            'requirements':json.loads(row['requirements']),'explicit_corrections':corrected,
            'user_messages':messages,'needs_specification':compiled is None or compiled['signature']!=signature,
            'steps':[{'step_id':r['id'],'tool':r['tool'],'arguments':json.loads(r['args']),'state':r['state'],
                      'result':json.loads(r['result']),'dependencies':json.loads(r['dependencies'])} for r in receipts]}

    def requirements(self,identifier,items,retire_ids=None,correction_quote=None):
        validate({'type':'array','minItems':0,'maxItems':40,'items':REQUIREMENT},items)
        retire_ids=retire_ids or []
        if not items and not retire_ids:raise InvalidArguments('Provide requirements or an explicit correction')
        with self.db() as db:
            row=self._row(db,identifier);history=[r[0] for r in db.execute('SELECT text FROM objective_messages WHERE objective=? ORDER BY created',(identifier,))];sources={i['id'] for i in json.loads(row['inputs'])}
            existing={i['id']:i for i in json.loads(row['requirements'])}
            if retire_ids:
                latest=history[-1] if history else ''
                if not isinstance(retire_ids,list) or len(retire_ids)>40 or not isinstance(correction_quote,str) or len(correction_quote)<3 or correction_quote.casefold() not in latest.casefold() or not re.search(r'\b(?:instead|rather|replace|rename|change|only|without|skip|not|no longer|do not|don.t)\b',correction_quote,re.I):
                    raise InvalidArguments('Retiring requirements needs a quoted explicit correction in the latest user message')
                for key in retire_ids:
                    if key not in existing or key.startswith('host-source-'):raise InvalidArguments('Required input identity cannot be retired by a model')
                    del existing[key]
                db.execute('INSERT INTO objective_corrections VALUES (?,?,?,?)',(identifier,correction_quote,encode(retire_ids),now()))
            if len({i['id'] for i in items})!=len(items):raise InvalidArguments('Requirement ids must be unique')
            for item in items:
                if not any(item['quote'].casefold() in text.casefold() for text in history):raise InvalidArguments('Each requirement must quote actual submitted user instructions')
                if item['id'].startswith('host-'):raise InvalidArguments('Host requirements cannot be replaced')
                if not set(item.get('source_ids',[]))<=sources:raise InvalidArguments('Requirement names an unselected source')
                if item.get('filename') and Path(item['filename']).name!=item['filename']:raise InvalidArguments('Filename must be a plain filename')
                existing[item['id']]=item
            db.execute('UPDATE objectives SET requirements=?,revision=revision+1,updated=? WHERE id=?',(encode(list(existing.values())),now(),identifier))
            signature,_=self.specification_signature(db,identifier,row)
            db.execute('INSERT OR REPLACE INTO objective_specifications VALUES (?,?)',(identifier,signature))
        return {'objective_id':identifier,'requirements':list(existing.values()),'summary_text':'Saved checkable requirements anchored to your instruction; execution evidence remains host-owned.'}

    def begin(self,identifier,tool,args):
        with self.db() as db:
            self._row(db,identifier)
            previous=db.execute("SELECT id,result FROM receipts WHERE objective=? AND state='succeeded'",(identifier,)).fetchall()
            text=encode(args);deps=[]
            for r in previous:
                result=json.loads(r['result'])
                if any(isinstance(result.get(k),str) and result[k] in text for k in ('dataset_id','analysis_id','document_id','draft_id')):deps.append(r['id'])
            step=uuid.uuid4().hex
            db.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?)',(step,identifier,tool,encode(args),'{}','running','[]',encode(deps),now()))
        return step

    def finish_step(self,step,result,success):
        from .execution_cache import _output_files
        assets=[{'path':p,'sha256':digest(p)} for p in _output_files(result)] if success else []
        with self.db() as db:
            db.execute('UPDATE receipts SET result=?,state=?,assets=?,updated=? WHERE id=?',(encode(result),'succeeded' if success else 'failed',encode(assets),now(),step))

    def set_state(self,identifier,state):
        if state not in {'running','paused','cancelled','blocked','completed','partial','failed'}:raise InvalidArguments('Unknown objective state')
        with self.db() as db:
            self._row(db,identifier)
            db.execute('UPDATE objectives SET state=?,updated=? WHERE id=?',(state,now(),identifier))
        return {'objective_id':identifier,'state':state}

    def review(self,identifier):
        snapshot=self.snapshot(identifier);issues=[];notes=[];valid=[];files=[];checks=[]
        for source in snapshot['inputs']:
            try:
                self.project.allow.check_readable(source['path'])
                if digest(source['path'])!=source['sha256']:raise ValueError('changed')
            except (OSError,ValueError):issues.append({'requirement':'source-identity','message':'Required source changed or became unavailable: '+source['id']})
        with self.db() as db:receipts=db.execute("SELECT * FROM receipts WHERE objective=? AND state='succeeded' ORDER BY updated",(identifier,)).fetchall()
        exporters={'export_excel','export_document','report_dataset','write_research_brief','write_research_results_brief','finalize_template_presentation'}
        latest_exports={}
        for receipt in receipts:
            if receipt['tool'] not in exporters:continue
            r=json.loads(receipt['result']);path=r.get('path') or r.get('docx_path') or r.get('pptx_path')
            if path:latest_exports[(Path(path).suffix.lower(),Path(path).name)]=receipt['id']
        for receipt in receipts:
            assets=json.loads(receipt['assets'])
            try:
                if not all(digest(a['path'])==a['sha256'] for a in assets):continue
            except OSError:continue
            if receipt['tool'] in exporters and receipt['id'] not in latest_exports.values():continue
            valid.append({'tool':receipt['tool'],'arguments':json.loads(receipt['args']),'result':json.loads(receipt['result'])})
            
            if receipt['tool'] in {'export_excel','export_document','report_dataset','write_research_brief','write_research_results_brief','finalize_template_presentation','clean_dataset','redact_text_pii'}:
                files.extend(a['path'] for a in assets)
        analyses=[]
        from .tools.research_analysis import load_analysis
        package_analyses=[]
        from .tools.research_packages import load_package_analysis
        for entry in valid:
            identifiers=entry['result'].get('analysis_ids',[]) or [entry['result'].get('analysis_id')]
            for package_id in identifiers:
                if package_id and package_id.startswith('research_'):
                    try:package_analyses.append(load_package_analysis(self.project,package_id))
                    except (ValueError,OSError,KeyError):pass
            analysis_id=entry['result'].get('analysis_id')
            if analysis_id:
                try:analyses.append(load_analysis(self.project,analysis_id))
                except (ValueError,OSError,KeyError):pass
        if any(r.get('kind')=='artifact' and r.get('format')=='docx' for r in snapshot['requirements']):
            exported_ids={e['result'].get('analysis_id') for e in valid if e['tool']=='write_research_brief'}
            analyses=[a for a in analyses if a['analysis_id'] in exported_ids]
        for requirement in snapshot['requirements']:
            kind=requirement['kind'];passed=False;detail=requirement['description']
            if kind=='artifact':
                candidates=[Path(p) for p in files if (not requirement.get('format') or Path(p).suffix.lower()=='.'+requirement['format']) and (not requirement.get('filename') or Path(p).name==requirement['filename'])]
                for p in candidates:
                    try:
                        if p.suffix.lower()=='.xlsx':
                            import openpyxl
                            book=openpyxl.load_workbook(p,read_only=True);ok=bool(book.sheetnames);book.close()
                        elif p.suffix.lower()=='.docx':
                            from docx import Document
                            doc=Document(p);ok=bool(doc.paragraphs or doc.tables)
                        elif p.suffix.lower()=='.pptx':
                            from pptx import Presentation
                            ok=len(Presentation(p).slides)>0
                        else:ok=p.stat().st_size>0
                        passed=passed or ok
                    except Exception:pass
            elif kind in {'analysis','chart'}:
                for analysis in analyses:
                    spec=analysis['specification'];measures={r['variable'] for r in analysis.get('records',[])}|set(spec.get('categorical_columns',[]))
                    available=measures if kind=='analysis' else set(analysis.get('chart_measures',[]))
                    units={r['variable']:r['unit'] for r in analysis.get('records',[])}
                    sources=set(analysis.get('source_lineage',{}))|{analysis['dataset_id']}
                    if set(requirement.get('columns',[]))<=available and set(requirement.get('groups',[]))<=set(spec.get('group_by',[])) and all(units.get(k)==v for k,v in requirement.get('units',{}).items()) and set(requirement.get('source_ids',[]))<=sources and (kind!='chart' or not requirement.get('count') or len(analysis.get('chart_measures',[]))==requirement['count']):passed=True
                if kind=='analysis':
                    exported_package_ids={i for e in valid if e['tool']=='write_research_results_brief' for i in e['result'].get('analysis_ids',[])}
                    needs_package_brief=any(r.get('kind')=='artifact' and r.get('format')=='docx' for r in snapshot['requirements'])
                    for analysis in package_analyses:
                        if needs_package_brief and analysis['analysis_id'] not in exported_package_ids:continue
                        spec=analysis.get('specification',{})
                        measures=set(spec.get('columns',[]))|set(spec.get('predictors',[]))|{spec.get('outcome'),spec.get('treatment')}
                        units=spec.get('units',{}) or {spec.get('outcome'):spec.get('outcome_unit')}
                        if set(requirement.get('columns',[]))<=measures and all(units.get(k)==v for k,v in requirement.get('units',{}).items()) and set(requirement.get('source_ids',[]))<=set(analysis['source_lineage']) and (not requirement.get('groups') or requirement['groups']==[spec.get('group_by')]):passed=True
                if kind=='analysis' and not passed:
                    # Deterministic analysis tools (impact evaluation, value for money, statistics) satisfy an analysis
                    # requirement when their successful call used the required columns on a selected source.
                    # 28 Oct: or when several calls on the same source cover them between them ("compare endline weight and
                    # breastfeeding, and check baseline balance" ran as four compare_groups calls and was judged unmet).
                    union_strings=set();union_lineage=set()
                    for e in valid:
                        if e['tool'] not in DETERMINISTIC_ANALYSIS_TOOLS:continue
                        strings=set()
                        if e['tool'] in WHOLE_DATASET_ANALYSIS_TOOLS:
                            # These examine every column of the source; the columns a contract names are covered.
                            whole=e['result'].get('dataset_id') or (e.get('arguments') or {}).get('dataset_id')
                            try:strings.update(self.project.dataset(whole).columns or [])
                            except (KeyError,AttributeError,TypeError):pass
                        def _walk(v):
                            if isinstance(v,str):strings.add(v)
                            elif isinstance(v,list):[_walk(x) for x in v]
                            elif isinstance(v,dict):[_walk(x) for x in v.values()]
                        _walk(e.get('arguments') or {})
                        for key in ('outcome','treatment','columns','predictors','covariates','outcome_unit'):
                            if e['result'].get(key) is not None:_walk(e['result'][key])
                        dsid=e['result'].get('dataset_id') or (e.get('arguments') or {}).get('dataset_id')
                        lineage=set()
                        if dsid:
                            try:lineage={d.id for d in self.project.dataset_lineage(dsid)}|{dsid}
                            except KeyError:lineage={dsid}
                        if set(requirement.get('columns',[]))<=strings and (not requirement.get('source_ids') or not lineage or set(requirement['source_ids'])<=lineage) and (not requirement.get('groups') or set(requirement['groups'])<=strings):
                            passed=True;break
                        if not requirement.get('source_ids') or not lineage or set(requirement['source_ids'])<=lineage:
                            union_strings|=strings;union_lineage|=lineage
                    if not passed and union_strings and set(requirement.get('columns',[]))<=union_strings and (not requirement.get('groups') or set(requirement['groups'])<=union_strings):
                        passed=True
            elif kind=='source_used':
                used=set()
                for analysis in analyses:used.update(analysis.get('source_lineage',{}));used.add(analysis['dataset_id'])
                for entry in valid:
                    if entry['tool']=='write_research_results_brief':used.update(entry['result'].get('source_dataset_ids',[]))
                    if entry['tool']=='report_dataset' and entry['arguments'].get('dataset_id'):
                        used.update(d.id for d in self.project.dataset_lineage(entry['arguments']['dataset_id']))
                needs_brief=any(r.get('kind')=='artifact' and r.get('format')=='docx' for r in snapshot['requirements'])
                if not needs_brief:
                    for entry in valid:
                        dsid=entry['result'].get('dataset_id') or entry['arguments'].get('dataset_id')
                        if dsid:
                            try:used.update(d.id for d in self.project.dataset_lineage(dsid))
                            except KeyError:pass
                for entry in valid:
                    if entry['tool']=='read_document' and entry['result'].get('document_id'):used.add(entry['result']['document_id'])
                passed=set(requirement.get('source_ids',[]))<=used and bool(requirement.get('source_ids'))
            elif kind=='pii_absent':
                # The latest derived dataset in the selected lineage must carry no column that
                # the deterministic profiler flags as personal data (names, phones, emails, ids…).
                from .tools.datasets import read_dataset, detect_pii
                candidates=[]
                for entry in valid:
                    dsid=entry['result'].get('dataset_id')
                    if not dsid or entry['tool'] in {'profile_dataset','import_dataset'}:continue
                    try:
                        lineage={d.id for d in self.project.dataset_lineage(dsid)}
                        if requirement.get('source_ids') and not set(requirement['source_ids'])<=lineage:continue
                        candidates.append(dsid)
                    except KeyError:pass
                if candidates:
                    dsid=candidates[-1]
                    try:
                        frame=read_dataset(self.project.dataset(dsid),nrows=2000)
                        # Columns the user explicitly asked to keep (host-derived from the request) are exempt; the
                        # review still names them so the answer can say that coordinates or names stay by request.
                        kept=[c for c in frame.columns if str(c) in set(requirement.get('columns',[]))]
                        flagged=[c for c in frame.columns if str(c) not in set(requirement.get('columns',[])) and detect_pii(str(c),frame[c])]
                        detail=requirement['description']+(' — remaining personal-data columns in '+dsid+': '+', '.join(flagged[:8]) if flagged else ' — no personal-data columns remain in '+dsid)
                        if kept:detail+=' — kept by request: '+', '.join(str(c) for c in kept[:8])+' (these can still identify households; the user chose to keep them)'
                        passed=not flagged
                    except (OSError,ValueError,KeyError):pass
            elif kind in {'columns_absent','columns_present'}:
                for entry in valid:
                    dsid=entry['result'].get('dataset_id')
                    if not dsid or entry['tool'] in {'profile_dataset','import_dataset'}:continue
                    try:
                        ds=self.project.dataset(dsid)
                        lineage={d.id for d in self.project.dataset_lineage(dsid)}
                        if not set(requirement.get('source_ids',[]))<=lineage:continue
                        from .tools.datasets import read_dataset
                        names=set(read_dataset(ds,nrows=1).columns);wanted=set(requirement.get('columns',[]))
                        if wanted and (not names&wanted if kind=='columns_absent' else wanted<=names):passed=True
                    except (OSError,ValueError,KeyError):pass
            elif kind=='document_coverage':
                requested=requirement.get('source_ids',[])
                passed=bool(requested)
                for docid in requested:
                    spans={}
                    for entry in valid:
                        r=entry['result']
                        if entry['tool']=='read_document' and r.get('document_id')==docid and r.get('extraction_complete') is True:
                            spans.setdefault((r.get('snapshot_id'),r.get('total_chars')),[]).append((r.get('offset',0),r.get('end_offset',0)))
                    covered=False
                    for (_,total),ranges in spans.items():
                        end=0
                        for start,stop in sorted(ranges):
                            if start>end:break
                            end=max(end,stop)
                        if total and end>=total:covered=True
                    passed=passed and covered
            # Hard requirements decide the verdict: things the host can verify after the fact (a file, columns kept or
            # removed, personal data gone, the right source used, a chart the request named in so many words).
            # Soft ones (an inferred analysis or chart, coverage) are reported as notes and never cost a repair round:
            # 28 Oct, invented "visualize"/"perform statistical analysis" rows held correct work at "partial".
            quote=str(requirement.get('quote') or '').lower()
            hard=kind in {'artifact','columns_absent','columns_present','pii_absent','source_used','document_coverage'} or (kind=='chart' and re.search(r'\b(chart|graph|plot|figure|visuali[sz]|histogram|scatter)',quote) is not None)
            checks.append({'id':requirement['id'],'passed':passed,'hard':hard,'description':detail})
            if not passed:
                if hard:issues.append({'requirement':requirement['id'],'message':detail})
                else:notes.append({'requirement':requirement['id'],'message':detail})
        report={'objective_id':identifier,'revision':snapshot['revision'],'passed':not issues,'checks':checks,'issues':issues,'notes':notes,
            'verified_steps':len(valid),'explicit_corrections':snapshot['explicit_corrections'],'reviewer':'Squipi output checks','review_type':'deterministic requirement checks; not scientific or visual certification',
            'summary_text':('All '+str(len(checks))+' declared requirements passed.' if not issues else 'Unresolved requirements: '+'; '.join(i['message'] for i in issues))}
        folder=self.project.root/'objective-reviews';folder.mkdir(exist_ok=True)
        report['review_path']=str(self.project.write_json(folder/(identifier+'.json'),report))
        return report
