"""Pick 20 control candidates: NON-surging stocks with random fake-T0 dates.

For each control:
- Random symbol from data/market_prices/ that is NOT in the PoC 20 surge set
  AND has NO surge event in surge_events.csv within the 60 days around
  the fake T0 (guarantees 'quiet period')
- Random fake T0 uniformly sampled from the same date range as PoC20 T0s

Output data/control20_candidates.csv with the same schema as
poc20_candidates.csv so batch_scrape.py can consume it unchanged.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
POC = ROOT / "data" / "poc20_candidates.csv"
SURGE = ROOT / "data" / "surge_events.csv"
UNIVERSE = ROOT / "data" / "universe.csv"
PRICE_DIR = ROOT / "data" / "market_prices"
OUT = ROOT / "data" / "control20_candidates.csv"

SEED = 20260528


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    random.seed(SEED)

    poc = list(csv.DictReader(POC.open("r", encoding="utf-8-sig")))
    poc_syms = {r["symbol"] for r in poc}
    print(f"poc 20 symbols: {len(poc_syms)}")

    # collect all surge events by (symbol, T0) so we can exclude control
    # candidates whose fake T0 falls within +/- 30d of a real surge.
    surge_by_sym: dict[str, list[dt.date]] = {}
    for r in csv.DictReader(SURGE.open("r", encoding="utf-8-sig")):
        surge_by_sym.setdefault(r["symbol"], []).append(dt.date.fromisoformat(r["T0"]))

    # universe lookup for name
    names: dict[str, str] = {}
    if UNIVERSE.exists():
        for row in csv.DictReader(UNIVERSE.open("r", encoding="utf-8-sig")):
            sym = (row.get("yahoo_symbol") or row.get("symbol") or "").strip()
            name = (row.get("name") or "").strip()
            if sym:
                names[sym] = name

    # T0 date range = same range as PoC 20
    poc_dates = [dt.date.fromisoformat(r["T0"]) for r in poc]
    min_date, max_date = min(poc_dates), max(poc_dates)
    span_days = (max_date - min_date).days
    print(f"fake T0 range: {min_date} ~ {max_date} ({span_days} days)")

    # candidate symbols = every price file, excluding PoC 20
    all_symbols = sorted(p.stem for p in PRICE_DIR.glob("*.csv"))
    candidates = [s for s in all_symbols if s not in poc_syms]
    random.shuffle(candidates)

    picked: list[dict] = []
    for sym in candidates:
        if len(picked) >= 20:
            break
        path = PRICE_DIR / f"{sym}.csv"
        try:
            df = pd.read_csv(path, usecols=["date", "close", "volume"])
        except Exception:
            continue
        if len(df) < 90:
            continue
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        available_dates = df["date"].dt.date
        # random fake T0 within [min_date, max_date] that actually exists in price data
        valid = available_dates[(available_dates >= min_date) & (available_dates <= max_date)]
        if valid.empty:
            continue
        fake_t0 = valid.sample(1, random_state=random.randint(0, 10_000_000)).iloc[0]

        # exclude if this stock has any real surge within +/- 30 days of fake_t0
        surges = surge_by_sym.get(sym, [])
        if any(abs((s - fake_t0).days) <= 30 for s in surges):
            continue

        # compute a naive "quiet-period" check: 20-day forward return < 15%
        idx = df.index[available_dates == fake_t0]
        if idx.empty:
            continue
        i = int(idx[0])
        if i < 20 or i > len(df) - 21:
            continue
        entry_close = float(df.at[i, "close"])
        fwd = df["close"].iloc[i + 1 : i + 21]
        if entry_close <= 0 or fwd.empty:
            continue
        fwd_return = (fwd.max() - entry_close) / entry_close
        if fwd_return >= 0.15:  # ensure control is genuinely quiet
            continue

        market = "sii" if sym.endswith(".TW") else "otc"
        picked.append(
            {
                "symbol": sym,
                "name": names.get(sym, sym.split(".")[0]),
                "T0": fake_t0.isoformat(),
                "entry_date": fake_t0.isoformat(),
                "peak_date": "",
                "entry_close": round(entry_close, 2),
                "T0_close": round(entry_close, 2),
                "peak_close": round(float(fwd.max()), 2),
                "return_pct": round(fwd_return * 100, 2),
                "vol_ratio": "",
            }
        )

    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "symbol", "name", "T0", "entry_date", "peak_date",
                "entry_close", "T0_close", "peak_close", "return_pct", "vol_ratio",
            ],
        )
        w.writeheader()
        for r in picked:
            w.writerow(r)

    print(f"picked {len(picked)} controls -> {OUT}")
    for r in picked:
        print(f"  {r['symbol']:12} {r['name'][:12]:12} fakeT0={r['T0']} fwd20d={r['return_pct']:+.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
