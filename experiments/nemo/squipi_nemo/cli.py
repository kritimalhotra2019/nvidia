"""Explicit init/run/status CLI. No connection to the live app's settings or data."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import uuid
from urllib.parse import urlparse

from .state import Job, digest, dumps, exclusive_job
from .tools import ALLOWED_TOOLS


def initialize(root, objective, attachments, templates=(), required=()):
    from squipi_worker.project import Project
    from squipi_worker.security import AllowList
    from squipi_worker.templates import library_action

    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    inputs_dir = root / 'inputs'
    inputs_dir.mkdir()
    project = Project(root / 'project', AllowList())
    manifest = {'id': str(uuid.uuid4()), 'objective': objective, 'inputs': [],
                'templates': {}, 'required_outputs': list(required)}
    for index, source in enumerate(attachments):
        source = Path(source).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError('Attach a regular file')
        target = inputs_dir / f'{index}-{source.name}'
        shutil.copyfile(source, target)
        manifest['inputs'].append({'path': str(target), 'sha256': digest(target)})
    # The worker may read only the copied user inputs; source files stay untouched.
    # These grants are re-established by each tool adapter from this host manifest.
    for source in templates:
        source = Path(source).expanduser().resolve(strict=True)
        project.allow.allow(source)
        data = library_action(project, {'action': 'import', 'path': str(source), 'make_default': True})
        item = data['templates'][-1]
        target = project.root / 'templates' / item['filename']
        manifest['templates'][item['format']] = {'id': item['id'], 'name': item['name'],
                                                'path': str(target), 'sha256': digest(target)}
    (root / 'task.json').write_text(dumps(manifest) + '\n')
    job = Job(root)
    try:
        return job.status()
    finally:
        job.close()


def configuration(root, model, base_url, steps, seconds, pause_after=None, request_seconds=None):
    parsed = urlparse(base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('This trial supports only an explicit loopback HTTP model endpoint. Cloud approval is not integrated.')
    if 'cloud' in model.lower():
        raise ValueError('Select a downloaded local model; cloud models are outside this trial.')
    functions = {name: {'_type': 'squipi_local_tool', 'job_dir': str(root), 'tool_name': name}
                 for name in (*ALLOWED_TOOLS, 'read_step_result')}
    return {'llms': {'local': {'_type': 'openai', 'base_url': base_url, 'api_key': 'ollama-local',
                             'model_name': model, 'temperature': 0, 'max_retries': 0,
                             'request_timeout': request_seconds or seconds, 'max_tokens': 2500,
                             'reasoning_effort': 'none'}},
            'functions': functions,
            'workflow': {'_type': 'squipi_durable_research', 'job_dir': str(root), 'llm_name': 'local',
                         'tool_names': list(functions), 'max_steps': steps, 'seconds': seconds,
                         'pause_after': pause_after, 'request_seconds': request_seconds or seconds}}


async def invoke(root, model, base_url, steps, seconds, message='', pause_after=None, request_seconds=None):
    # No hosted tracing or real API credentials are used by this local trial.
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    os.environ.setdefault('MPLCONFIGDIR', str(Path(root) / '.cache' / 'matplotlib'))
    from nat.runtime.loader import load_workflow
    import yaml
    config = configuration(root, model, base_url, steps, seconds, pause_after, request_seconds)
    # Unique config avoids concurrent invocations overwriting each other's options.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='squipi-nat-config-') as temp:
        config_path = Path(temp) / 'config.yml'
        config_path.write_text(yaml.safe_dump(config))
        async with load_workflow(config_path) as manager:
            async with manager.run(message) as runner:
                return await runner.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('job', type=Path)
    init.add_argument('--instruction', required=True)
    init.add_argument('--attach', type=Path, action='append', default=[])
    init.add_argument('--template', type=Path, action='append', default=[])
    init.add_argument('--require', choices=['xlsx', 'docx', 'pptx', 'html'], action='append', default=[])
    run = sub.add_parser('run', help='Run one bounded invocation')
    run.add_argument('job', type=Path)
    run.add_argument('--model', default='qwen3.5:9b')
    run.add_argument('--base-url', default='http://127.0.0.1:11434/v1')
    run.add_argument('--steps', type=int, default=12)
    run.add_argument('--seconds', type=int, default=300)
    run.add_argument('--message', default='')
    run.add_argument('--pause-after', choices=ALLOWED_TOOLS, help='Pause after this tool to exercise recovery')
    run.add_argument('--request-seconds', type=int, default=120)
    work = sub.add_parser('work', help='Automatically continue recoverable pauses with bounded retries')
    work.add_argument('job', type=Path)
    work.add_argument('--model', default='qwen3.5:9b')
    work.add_argument('--base-url', default='http://127.0.0.1:11434/v1')
    work.add_argument('--steps', type=int, default=12)
    work.add_argument('--seconds', type=int, default=300)
    work.add_argument('--request-seconds', type=int, default=120)
    work.add_argument('--cycles', type=int, default=6)
    work.add_argument('--max-stalls', type=int, default=2)
    work.add_argument('--message', default='', help='New user instruction for the existing task')
    stop = sub.add_parser('stop', help='Request cooperative stop; never automatically resumes')
    stop.add_argument('job', type=Path)
    status = sub.add_parser('status')
    status.add_argument('job', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = initialize(args.job, args.instruction, args.attach, args.template, args.require)
        elif args.command == 'status':
            job = Job(args.job)
            try:
                result = job.status()
            finally:
                job.close()
        elif args.command == 'stop':
            if not (args.job / 'task.json').is_file():
                parser.error('Unknown task directory')
            (args.job / 'stop.requested').touch()
            result = {'status': 'stop_requested', 'message': 'The active model call will stop; a tool already writing may finish its current operation first.'}
        else:
            if min(args.steps, args.seconds, args.request_seconds) < 1:
                parser.error('Step and time limits must be positive')
            if args.command == 'work' and min(args.cycles, args.max_stalls) < 1:
                parser.error('Continuation limits must be positive')
            # A new explicit run/work command is the user's resume action.
            # Do not clear Stop if another invocation is still active.
            with exclusive_job(args.job, 'supervisor.lock'), exclusive_job(args.job):
                (args.job / 'stop.requested').unlink(missing_ok=True)
            pending_message = getattr(args, 'message', '')
            async def attempt():
                nonlocal pending_message
                message, pending_message = pending_message, ''
                return await invoke(args.job.resolve(), args.model, args.base_url, args.steps,
                                    args.seconds, message, getattr(args, 'pause_after', None),
                                    args.request_seconds)
            if args.command == 'work':
                from .supervisor import supervise
                result = asyncio.run(supervise(args.job, attempt, cycles=args.cycles, max_stalls=args.max_stalls))
            else:
                result = asyncio.run(attempt())
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        print('Paused. Run the same task directory again to resume.', flush=True)
        raise SystemExit(130)


if __name__ == '__main__':
    main()
