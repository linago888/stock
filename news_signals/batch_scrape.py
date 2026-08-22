"""Batch scrape MOPS 重訊 for all candidates in a CSV.

Usage:
    python batch_scrape.py --candidates data/poc20_candidates.csv
    python batch_scrape.py --candidates data/control20_candidates.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOPS_DIR = ROOT / "data" / "mops"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(ROOT / "data" / "poc20_candidates.csv"))
    args = ap.parse_args()
    candidates_path = Path(args.candidates)
    rows = list(csv.DictReader(candidates_path.open("r", encoding="utf-8-sig")))
    print(f"batch scraping {len(rows)} candidates from {candidates_path.name}")
    for i, r in enumerate(rows, 1):
        sym_full = r["symbol"]
        code = sym_full.split(".")[0]
        market = "sii" if sym_full.endswith(".TW") else "otc"
        t0 = dt.date.fromisoformat(r["T0"])
        start = (t0 - dt.timedelta(days=30)).isoformat()
        end = (t0 - dt.timedelta(days=1)).isoformat()
        out = MOPS_DIR / f"{code}_{start}_{end}.json"
        if out.exists() and out.stat().st_size > 40:
            print(f"[{i}/{len(rows)}] {code} {r['name']} skip (cached)", flush=True)
            continue
        print(f"[{i}/{len(rows)}] {code} {r['name']} {market} {start} → {end}", flush=True)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "news_signals" / "mops_scraper.py"),
                "--symbol", code,
                "--market", market,
                "--start", start,
                "--end", end,
                "--sleep", "0.5",
            ],
            check=False,
        )
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
