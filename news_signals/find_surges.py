"""Scan data/market_prices/ for "surge events".

Surge event definition (PoC):
- 20-day forward return >= 30% (peak within next 20 trading days)
- Volume at the surge peak >= 3x the 20-day average leading up
- Event date T0 = the first day inside the surge window whose close
  is >= entry_close * (1 + surge_threshold * 0.5) AND whose volume
  >= 3x avg (i.e. the moment the market "notices")

Outputs data/surge_events.csv with columns:
  symbol, name, entry_date, T0, peak_date, entry_close, peak_close,
  return_pct, T0_volume, avg_volume_20d, vol_ratio
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PRICE_DIR = ROOT / "data" / "market_prices"
UNIVERSE = ROOT / "data" / "universe.csv"
OUT = ROOT / "data" / "surge_events.csv"

SURGE_RETURN = 0.30
SURGE_WINDOW = 20      # trading days
VOL_RATIO = 3.0
MIN_HISTORY = 60       # need 60 days of history for avg_volume
LOOKBACK_YEARS = 2     # only consider events within last 2 years


def load_names() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not UNIVERSE.exists():
        return mapping
    with UNIVERSE.open("r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sym = (row.get("yahoo_symbol") or row.get("symbol") or "").strip()
            name = (row.get("name") or "").strip()
            if sym:
                mapping[sym] = name
                if "." in sym:
                    mapping[sym.split(".")[0]] = name
    return mapping


def scan_symbol(path: Path, names: dict[str, str]) -> list[dict]:
    try:
        df = pd.read_csv(path)
    except Exception:
        return []
    if len(df) < MIN_HISTORY + SURGE_WINDOW:
        return []
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close", "volume"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return []

    cutoff = df["date"].max() - pd.DateOffset(years=LOOKBACK_YEARS)
    df["avg_vol_20"] = df["volume"].rolling(20).mean()

    events: list[dict] = []
    n = len(df)
    i = MIN_HISTORY
    while i < n - SURGE_WINDOW:
        if df.at[i, "date"] < cutoff:
            i += 1
            continue
        entry_close = df.at[i, "close"]
        if entry_close <= 0:
            i += 1
            continue
        window = df.iloc[i + 1 : i + 1 + SURGE_WINDOW]
        if window.empty:
            break
        peak_close = window["close"].max()
        peak_idx = window["close"].idxmax()
        ret = (peak_close - entry_close) / entry_close
        if ret < SURGE_RETURN:
            i += 1
            continue

        avg_vol = df.at[i, "avg_vol_20"]
        if not avg_vol or pd.isna(avg_vol):
            i += 1
            continue

        # find T0 = first day inside window with vol >= 3x avg AND close >= entry*(1 + surge/2)
        threshold_close = entry_close * (1 + SURGE_RETURN * 0.5)
        t0_row = None
        for j in range(i + 1, i + 1 + SURGE_WINDOW):
            if df.at[j, "close"] >= threshold_close and df.at[j, "volume"] >= VOL_RATIO * avg_vol:
                t0_row = j
                break
        if t0_row is None:
            i += 1
            continue

        events.append(
            {
                "symbol": path.stem,
                "name": names.get(path.stem, ""),
                "entry_date": df.at[i, "date"].strftime("%Y-%m-%d"),
                "T0": df.at[t0_row, "date"].strftime("%Y-%m-%d"),
                "peak_date": df.at[peak_idx, "date"].strftime("%Y-%m-%d"),
                "entry_close": round(float(entry_close), 2),
                "T0_close": round(float(df.at[t0_row, "close"]), 2),
                "peak_close": round(float(peak_close), 2),
                "return_pct": round(float(ret * 100), 2),
                "T0_volume": int(df.at[t0_row, "volume"]),
                "avg_volume_20d": int(avg_vol),
                "vol_ratio": round(float(df.at[t0_row, "volume"] / avg_vol), 2),
            }
        )
        # skip past this surge window to avoid double-counting overlapping events
        i = peak_idx + 5

    return events


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    files = sorted(PRICE_DIR.glob("*.csv"))
    print(f"scanning {len(files)} price files")
    names = load_names()
    print(f"universe names loaded: {len(names)}")

    all_events: list[dict] = []
    for k, path in enumerate(files):
        events = scan_symbol(path, names)
        all_events.extend(events)
        if (k + 1) % 200 == 0:
            print(f"  {k + 1}/{len(files)} scanned, events so far: {len(all_events)}")

    # keep only .TW / .TWO (skip if universe not present)
    if not all_events:
        print("no surges detected", file=sys.stderr)
        return 1

    df_out = pd.DataFrame(all_events).sort_values(
        ["return_pct", "vol_ratio"], ascending=[False, False]
    )
    df_out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"wrote {OUT} ({len(df_out)} events)")

    print("\n== Top 20 events ==")
    for _, r in df_out.head(20).iterrows():
        print(
            f"  {r['symbol']:12} {r['name'][:12]:12} T0={r['T0']} "
            f"return={r['return_pct']:6.1f}%  vol×{r['vol_ratio']:.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
