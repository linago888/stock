"""Bundle all signals PoC data into one JSON for Vercel deployment.

Reads:
- data/poc20_candidates.csv, data/control20_candidates.csv
- data/poc20_summary.json, data/control20_summary.json
- data/mops/*.json (only those referenced by candidates)
- data/surge_events.csv (only the count)

Writes:
- data/signals_bundle.json (~200-500 KB)
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MOPS = DATA / "mops"
OUT = DATA / "signals_bundle.json.gz"


def load_candidates(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return list(csv.DictReader(p.open("r", encoding="utf-8-sig")))


def load_summary(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_anns(candidates: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in candidates:
        sym = c["symbol"]
        code = sym.split(".")[0]
        try:
            t0 = dt.date.fromisoformat(c["T0"])
        except Exception:
            continue
        start = (t0 - dt.timedelta(days=30)).isoformat()
        end = (t0 - dt.timedelta(days=1)).isoformat()
        p = MOPS / f"{code}_{start}_{end}.json"
        out[sym] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    poc_c = load_candidates(DATA / "poc20_candidates.csv")
    ctl_c = load_candidates(DATA / "control20_candidates.csv")
    poc_s = load_summary(DATA / "poc20_summary.json")
    ctl_s = load_summary(DATA / "control20_summary.json")
    poc_a = load_anns(poc_c)
    ctl_a = load_anns(ctl_c)

    surge_count = 0
    surge_csv = DATA / "surge_events.csv"
    if surge_csv.exists():
        with surge_csv.open("r", encoding="utf-8-sig") as fh:
            surge_count = sum(1 for _ in csv.reader(fh)) - 1

    watchlist_path = DATA / "watchlist_scan.json"
    watchlist = json.loads(watchlist_path.read_text(encoding="utf-8")) if watchlist_path.exists() else {}

    bundle = {
        "surge_event_count": surge_count,
        "poc_candidates": poc_c,
        "ctl_candidates": ctl_c,
        "poc_summary": poc_s,
        "ctl_summary": ctl_s,
        "poc_anns": poc_a,
        "ctl_anns": ctl_a,
        "watchlist": watchlist,
    }
    raw = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    with gzip.open(OUT, "wb", compresslevel=9) as fh:
        fh.write(raw)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB, uncompressed {len(raw) / 1024:.1f} KB)")
    print(f"  poc: {len(poc_c)} stocks, {sum(len(v) for v in poc_a.values())} announcements")
    print(f"  ctl: {len(ctl_c)} stocks, {sum(len(v) for v in ctl_a.values())} announcements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
