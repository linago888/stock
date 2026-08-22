"""One-shot: pick → batch scrape → aggregate. Reuses cached MOPS json."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
py = sys.executable

steps = [
    [py, str(ROOT / "pick_candidates.py")],
    [py, str(ROOT / "batch_scrape.py")],
    [py, str(ROOT / "aggregate_signals.py")],
]
for cmd in steps:
    print(f"\n>>> {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"step failed with exit {r.returncode}", file=sys.stderr)
        sys.exit(r.returncode)
