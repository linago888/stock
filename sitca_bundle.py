"""Bundle scraped SITCA CSVs into a single gzipped CSV for Vercel deployment.

Filters down to stock-type rows only (since the website only shows stocks),
which shrinks 30 MB / 216 files down to ~2-3 MB / 1 file.
"""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "data" / "sitca"
OUT_PATH = ROOT / "data" / "sitca_bundle.csv.gz"

STOCK_TYPE_KEYWORDS = (
    "國內上市", "國內上櫃", "國內興櫃",
)


def is_stock_type(t: object) -> bool:
    if not isinstance(t, str):
        return False
    return any(k in t for k in STOCK_TYPE_KEYWORDS)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    files = sorted(SRC_DIR.glob("2*_A*.csv"))
    if not files:
        print("no source CSVs", file=sys.stderr)
        return 1
    print(f"merging {len(files)} files")
    df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
    print(f"raw rows: {len(df)}")
    df = df[df["target_type"].map(is_stock_type)].copy()
    print(f"stock rows: {len(df)}")
    df = df[
        [
            "year_month",
            "company_id",
            "company_name",
            "fund_name",
            "rank",
            "target_type",
            "target_code",
            "target_name",
            "amount",
            "pct_of_nav",
        ]
    ]
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_PATH, "wb", compresslevel=9) as fh:
        fh.write(csv_bytes)
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"wrote {OUT_PATH} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
