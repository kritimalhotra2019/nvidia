"""Bounded continuation of a saved job. Never retry uncertain writes or user stops."""
import asyncio
import json
import sys
from pathlib import Path
from .state import Job, exclusive_job

RETRYABLE = {'budget', 'request_timeout', 'transient_model_error'}


def fingerprint(root):
    job = Job(root)
    try:
        return tuple(row['key'] for row in job.rows() if row['status'] == 'done')
    finally:
        job.close()


async def supervise(root, invoke, *, cycles=6, max_stalls=2, backoff=1):
    if cycles < 1 or max_stalls < 1 or backoff < 0:
        raise ValueError('cycles/max_stalls must be positive and backoff nonnegative')
    root = Path(root)
    with exclusive_job(root, 'supervisor.lock'):
        before, stalls = fingerprint(root), 0
        result = None
        for cycle in range(cycles):
            if (root / 'stop.requested').exists():
                job = Job(root)
                try:
                    state = job.load()
                    state.update(status='paused', pause_reason='user_stop')
                    job.save(state)
                    return job.status()
                finally:
                    job.close()
            result = await invoke()
            if result['status'] != 'paused' or result.get('pause_reason') not in RETRYABLE:
                return result
            after = fingerprint(root)
            stalls = stalls + 1 if after == before else 0
            before = after
            if stalls >= max_stalls:
                job = Job(root)
                try:
                    state = job.load()
                    state.update(pause_reason='no_progress', error='Paused after repeated attempts without a successful new step. Review the model error or change the model; saved work is intact.')
                    job.save(state)
                    return job.status()
                finally:
                    job.close()
            if cycle + 1 < cycles:
                print(f'Continuing saved task (cycle {cycle + 2}/{cycles}); completed files are retained.', file=sys.stderr, flush=True)
                # Short intervals keep explicit Stop responsive during backoff too.
                for _ in range(int(backoff * 10)):
                    if (root / 'stop.requested').exists():
                        break
                    await asyncio.sleep(.1)
        job = Job(root)
        try:
            state = job.load()
            state.update(pause_reason='continuation_limit', error='Continuation budget reached. Resume this task to keep working; completed steps remain saved.')
            job.save(state)
            return job.status()
        finally:
            job.close()
