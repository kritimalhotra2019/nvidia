"""Validate the runtime and plugin before marking it ready. No model or user files."""
import importlib.metadata
import json
from pathlib import Path
import sys


def check():
    import numpy
    import pandas
    import pyarrow
    from nat.runtime.loader import load_workflow
    from langchain_openai import ChatOpenAI
    from squipi_worker.registry import registry
    from squipi_worker import tools
    from squipi_nemo import register
    entries = importlib.metadata.entry_points(group='nat.components')
    if not any(e.name == 'squipi_nemo' and e.value == 'squipi_nemo.register' for e in entries):
        raise RuntimeError('Squipi NeMo plugin registration is missing')
    return {'schema': 1, 'ready': True, 'python': sys.version.split()[0],
            'numpy': numpy.__version__, 'nat': importlib.metadata.version('nvidia-nat')}


if __name__ == '__main__':
    result = check()
    if len(sys.argv) > 1:
        destination = Path(sys.argv[1])
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, indent=2) + '\n')
        temporary.replace(destination)
    print(json.dumps(result))
