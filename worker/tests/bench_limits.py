"""Not a unit test: profiles a synthetic 1,000,000-row CSV and prints time + peak RSS.
Run: .venv/bin/python tests/bench_limits.py [rows]"""
import resource
import sys, time, tempfile
from pathlib import Path
import numpy as np, pandas as pd
from squipi_worker import tools  # noqa
from squipi_worker.project import Project
from squipi_worker.registry import registry, JobContext
from squipi_worker.security import AllowList

rows = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
root = Path(tempfile.mkdtemp(prefix="squipi-bench-"))
rng = np.random.default_rng(0)
t0 = time.time()
df = pd.DataFrame({
    "unit_id": rng.integers(1, 50_000, rows), "date": pd.date_range("2015-01-01", periods=rows, freq="min").strftime("%Y-%m-%d"),
    **{f"x{i}": rng.normal(size=rows) for i in range(10)},
    **{f"c{i}": rng.choice(["a", "b", "c", "d", "e"], rows) for i in range(5)},
    "amount": rng.lognormal(5, 1, rows), "flag": rng.integers(0, 2, rows), "pct": rng.uniform(0, 100, rows),
})
csv = root / "big.csv"; df.to_csv(csv, index=False)
print(f"wrote {rows:,} rows, {csv.stat().st_size/1e6:.0f} MB in {time.time()-t0:.1f}s")
allow = AllowList(); allow.allow(csv)
project = Project(root / "project", allow)
ctx = JobContext(project, report=lambda p, n: None, cancelled=lambda: False)
t1 = time.time()
imp = registry.call("import_dataset", {"path": str(csv)}, ctx)
prof = registry.call("profile_dataset", {"dataset_id": imp["dataset_id"]}, ctx)
t2 = time.time()
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)  # ru_maxrss is bytes on macOS, KB on Linux
print(f"profile: {t2-t1:.1f}s, sampled={prof['sampled']}, rows_profiled={prof['rows_profiled']:,}, charts={len(prof['charts'])}, peak RSS {peak:.0f} MB")
