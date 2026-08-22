"""Pick 20 diverse surge events for the expanded PoC.

Rules:
- return_pct in [50, 200]  (skip both weak and absurd)
- vol_ratio in [3, 15]     (skip thin manipulation and extreme spikes)
- prefer sii (上市) over otc (上櫃)
- spread across T0 quarters (max 4 per quarter)
- one event per stock (keep highest return)
- deterministic: sort by return desc, then take
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN = ROOT / "data" / "surge_events.csv"
OUT = ROOT / "data" / "poc20_candidates.csv"


def quarter(ymd: str) -> str:
    y, m = int(ymd[:4]), int(ymd[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = list(csv.DictReader(IN.open("r", encoding="utf-8-sig")))
    print(f"total surge events: {len(rows)}")

    filtered = []
    for r in rows:
        ret = float(r["return_pct"])
        vol = float(r["vol_ratio"])
        if not (50 <= ret <= 200):
            continue
        if not (3.0 <= vol <= 15.0):
            continue
        r["_ret"] = ret
        r["_vol"] = vol
        r["_q"] = quarter(r["T0"])
        filtered.append(r)

    # sort so that sii comes first within same return band, then by return desc
    filtered.sort(key=lambda r: (0 if r["symbol"].endswith(".TW") else 1, -r["_ret"]))

    seen_symbols: set[str] = set()
    q_count: dict[str, int] = defaultdict(int)
    picked = []
    # first pass: sii, 4/quarter cap
    for r in filtered:
        if not r["symbol"].endswith(".TW"):
            continue
        sym = r["symbol"]
        if sym in seen_symbols:
            continue
        if q_count[r["_q"]] >= 4:
            continue
        seen_symbols.add(sym)
        q_count[r["_q"]] += 1
        picked.append(r)
        if len(picked) >= 16:
            break
    # second pass: fill remaining with otc (drop quarter cap)
    for r in filtered:
        if not r["symbol"].endswith(".TWO"):
            continue
        sym = r["symbol"]
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)
        picked.append(r)
        if len(picked) >= 20:
            break

    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "symbol", "name", "T0", "entry_date", "peak_date",
                "entry_close", "T0_close", "peak_close",
                "return_pct", "vol_ratio",
            ],
        )
        w.writeheader()
        for r in picked:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    print(f"picked {len(picked)} candidates -> {OUT}")
    print(f"\nby quarter: {dict(q_count)}\n")
    print("== picked ==")
    for r in picked:
        print(f"  {r['symbol']:12} {r['name'][:12]:12} T0={r['T0']} "
              f"ret={r['_ret']:5.1f}% vol×{r['_vol']:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
