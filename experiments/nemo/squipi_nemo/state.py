"""Host-owned durable task state, independent of the chosen model and NAT session."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from squipi_worker.project import Project, _json_default
from squipi_worker.registry import JobContext, registry, validate, InvalidArguments
from squipi_worker.security import AllowList
from squipi_worker import tools  # noqa: F401; register existing Squipi tools


class NeedsReview(RuntimeError):
    """An artifact changed or a tool's side effects have an uncertain outcome."""


def dumps(value):
    return json.dumps(value, default=_json_default, ensure_ascii=False, sort_keys=True)


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def artifact_paths(value):
    """Collect actual result paths, including nested reports/charts/datasets."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == 'path' or key.endswith('_path')):
                found.add(item)
            else:
                found.update(artifact_paths(item))
    elif isinstance(value, list):
        for item in value:
            found.update(artifact_paths(item))
    return found


@contextlib.contextmanager
def exclusive_job(root, lock_name='run.lock'):
    with (Path(root) / lock_name).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('This task is already running in another process.') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Job:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = json.loads((self.root / 'task.json').read_text())
        self.project = Project(self.root / 'project', AllowList())
        for item in self.manifest['inputs']:
            Path(item['path']).resolve().relative_to(self.root / 'inputs')
            self.project.allow.allow(item['path'])
        self.db = sqlite3.connect(self.root / 'progress.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls (
                key TEXT PRIMARY KEY, tool TEXT NOT NULL, arguments TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT, artifacts TEXT, started REAL NOT NULL);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def load(self):
        row = self.db.execute('SELECT value FROM state WHERE id=1').fetchone()
        return json.loads(row['value']) if row else {'messages': [], 'status': 'ready', 'attempts': 0}

    def save(self, state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (dumps(state),))

    def rows(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM calls ORDER BY started')]

    def check_integrity(self):
        for item in self.manifest['inputs']:
            self.verify({item['path']: item['sha256']})
        for item in self.manifest.get('templates', {}).values():
            self.verify({item['path']: item['sha256']})
        for row in self.rows():
            if row['status'] == 'running':
                raise NeedsReview(f"Interrupted during {row['tool']}. Reconcile its output before retrying; no automatic replay.")
            if row['status'] == 'done':
                self.verify(json.loads(row['artifacts']))

    def verify(self, artifacts):
        for name, checksum in artifacts.items():
            path = Path(name)
            path.resolve().relative_to(self.root)
            if not path.is_file() or digest(path) != checksum:
                raise NeedsReview(f'Saved file is missing or changed: {path.name}. No silent restart.')

    def apply_template(self, name, args):
        args = dict(args)
        templates = self.manifest.get('templates', {})
        if name == 'report_dataset':
            for fmt, entry in templates.items():
                key = fmt + '_template_id'
                if args.get(key, entry['id']) != entry['id']:
                    raise ValueError('Use the template pinned to this task.')
                args[key] = entry['id']
        elif name == 'export_document' and args.get('format') in templates:
            entry = templates[args['format']]
            if args.get('template_id', entry['id']) != entry['id']:
                raise ValueError('Use the template pinned to this task.')
            args['template_id'] = entry['id']
        elif name == 'begin_template_presentation' and 'pptx' in templates:
            entry = templates['pptx']
            if args.get('template_id', entry['id']) != entry['id']:
                raise ValueError('Use the template pinned to this task.')
            args['template_id'] = entry['id']
        return args

    def call(self, name, args):
        from .tools import ALLOWED_TOOLS, EXCEL_SCHEMA, export_excel
        if name not in ALLOWED_TOOLS:
            raise ValueError(f'Tool is outside the local trial: {name}')
        args = self.apply_template(name, args)
        validate(EXCEL_SCHEMA if name == 'export_excel' else registry.get(name).params, args)
        if 'path' in args:
            self.project.allow.check_readable(args['path'])
        data_args = args.get('chart', {}) if name == 'edit_presentation_slide' else args
        if 'dataset_id' in data_args:
            dataset = self.project.dataset(data_args['dataset_id'])
            self.project.allow.check_readable(dataset.path)
            data_args['dataset_id'] = dataset.id
            # Once a cleaned copy exists, prevent downstream work accidentally
            # switching back to its original source. A new task can analyse raw data.
            if name not in ('clean_dataset', 'profile_dataset'):
                for previous in reversed(self.rows()):
                    if previous['tool'] == 'clean_dataset' and previous['status'] == 'done':
                        cleaned = json.loads(previous['result'])
                        if dataset.id == cleaned['from_dataset_id']:
                            raise ValueError('Use the saved cleaned dataset: ' + cleaned['dataset_id'])
        dependency = None
        if 'dataset_id' in data_args and name != 'set_column_roles':
            dependency = {'sha256': digest(dataset.path), 'overrides': dataset.overrides or {}}
        key = hashlib.sha256(dumps([name, args, dependency]).encode()).hexdigest()
        old = self.db.execute('SELECT * FROM calls WHERE key=?', (key,)).fetchone()
        if old:
            if old['status'] == 'done':
                self.verify(json.loads(old['artifacts']))
                return json.loads(old['result'])
            if old['status'] == 'failed':
                raise InvalidArguments(json.loads(old['result'])['error'] + ' Correct the arguments before retrying.')
            raise NeedsReview(f"Prior {name} has an uncertain outcome. Review before retrying.")
        before = self.write_fingerprints()
        # Record intent before executing a tool. A hard crash cannot silently retry a write.
        with self.db:
            self.db.execute('INSERT INTO calls VALUES (?, ?, ?, ?, NULL, NULL, ?)',
                            (key, name, dumps(args), 'running', time.time()))
        context = JobContext(self.project, report=lambda *_: None,
                             cancelled=lambda: (self.root / 'stop.requested').exists(), allow_network=False)
        try:
            result = export_excel(context, args) if name == 'export_excel' else registry.call(name, args, context)
            result = dict(result, _squipi_step_key=key)
            artifacts = {}
            for item in artifact_paths(result):
                path = Path(item).resolve()
                path.relative_to(self.root)
                if not path.is_file() or not path.stat().st_size:
                    raise NeedsReview('Tool reported a missing/empty file: ' + path.name)
                artifacts[str(path)] = digest(path)
            with self.db:
                self.db.execute('UPDATE calls SET status=?, result=?, artifacts=? WHERE key=?',
                                ('done', dumps(result), dumps(artifacts), key))
            return result
        except Exception as exc:
            if isinstance(exc, InvalidArguments) and self.write_fingerprints() == before:
                # Analysis may have produced disposable profile/charts before
                # discovering an invalid outcome. No dataset, registry or final
                # deliverable changed, so let the model correct its arguments.
                with self.db:
                    self.db.execute('UPDATE calls SET status=?, result=? WHERE key=?',
                                    ('failed', dumps({'error': str(exc)}), key))
                raise
            # A handler may have written files before throwing. Keep this intent
            # uncertain; never assume an exception implies no side effects.
            with self.db:
                self.db.execute('UPDATE calls SET result=? WHERE key=?', (dumps({'error': str(exc)}), key))
            raise NeedsReview(f'{name} stopped during execution: {exc}. Review its outputs before retrying.') from exc

    def write_fingerprints(self):
        files = [p for p in (self.project.root / 'artifacts').rglob('*')
                 if p.is_file() and p.suffix in {'.csv', '.xlsx', '.docx', '.pptx', '.html', '.parquet'}
                 and not (p.name == 'report.html' and '_profile_' in p.parent.name)]
        registry_path = self.project.root / 'datasets.json'
        if registry_path.exists():
            files.append(registry_path)
        return {str(path): digest(path) for path in files}

    def output_files(self):
        files = {}
        for row in self.rows():
            if row['status'] == 'done':
                files.update(json.loads(row['artifacts']))
        # Inputs/template files aren't deliverables even if read_document returned their paths.
        return [name for name in files if Path(name).is_relative_to(self.project.root / 'artifacts')]

    def missing_outputs(self):
        formats = {Path(path).suffix[1:] for path in self.output_files()}
        return sorted(set(self.manifest['required_outputs']) - formats)

    def pending_visual_reviews(self):
        return [json.loads(row['result'])['path'] for row in self.rows()
                if row['status'] == 'done' and row['tool'] == 'finalize_template_presentation'
                and json.loads(row['result']).get('visual_review_required')]

    def status(self):
        state = self.load()
        return {'task_id': self.manifest['id'], 'status': state['status'],
                'objective': self.manifest['objective'], 'attempts': state['attempts'],
                'completed_tools': [row['tool'] for row in self.rows() if row['status'] == 'done'],
                'outputs': self.output_files(), 'missing_outputs': self.missing_outputs(),
                'pending_visual_reviews': self.pending_visual_reviews(),
                'error': state.get('error'), 'answer': state.get('answer'),
                'pause_reason': state.get('pause_reason')}
