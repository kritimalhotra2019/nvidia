# squipi-worker

The local analysis process behind Squipi. It speaks JSON lines on stdin/stdout (no sockets),
runs one job at a time, reports progress, and can be cancelled. It only reads files whose
paths the app has explicitly allowed, and writes only inside the project directory it is given.

    python -m squipi_worker --project /path/to/project   # serve
    python -m squipi_worker --selftest                     # profile a built-in fictional CSV and print the result

Protocol: see `squipi_worker/server.py`. Tools: see `squipi_worker/registry.py`.
