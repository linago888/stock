"""Deep watchlist backfill: iterate high-liquidity Taiwan stocks and pull
last N days of MOPS 重訊, then apply the whitelist filter.

Universe: stocks in data/market_prices/ with avg 20-day volume above
threshold. Default threshold picks ~400-700 stocks — the tradable set.

For each candidate, use existing per-company scraper interface via
subprocess (reuses list caching / structure). Merge results with
scan_watchlist.py output.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mops_scraper import scrape_range  # reuse
from scan_watchlist import (
    WHITELIST_RULES,
    classify,
    load_surge_history,
)

ROOT = Path(__file__).resolve().parent.parent
PRICE_DIR = ROOT / "data" / "market_prices"
UNIVERSE = ROOT / "data" / "universe.csv"
CACHE_DIR = ROOT / "data" / "mops"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "data" / "watchlist_scan.json"


def load_universe_names() -> dict[str, tuple[str, str]]:
    """Return co_id -> (name, market: sii/otc)."""
    mapping: dict[str, tuple[str, str]] = {}
    if not UNIVERSE.exists():
        return mapping
    with UNIVERSE.open("r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("yahoo_symbol") or row.get("symbol") or "").strip()
            name = (row.get("name") or "").strip()
            if not sym:
                continue
            code = sym.split(".")[0]
            market = "otc" if sym.endswith(".TWO") else "sii"
            mapping[code] = (name, market)
    return mapping


def pick_liquid_stocks(min_avg_volume: float, limit: int) -> list[str]:
    """Return co_ids sorted by avg 20-day volume desc, top `limit`."""
    scored: list[tuple[float, str]] = []
    for p in sorted(PRICE_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(p, usecols=["date", "volume"], nrows=200)
        except Exception:
            continue
        if len(df) < 20:
            continue
        avg = df["volume"].tail(20).mean()
        if avg < min_avg_volume:
            continue
        code = p.stem.split(".")[0]
        scored.append((float(avg), code))
    scored.sort(reverse=True)
    return [c for _, c in scored[:limit]]


def compute_tech_metrics(co_id: str) -> dict:
    """Compute short-term price/volume metrics for a stock.

    Returns:
      last_close, last_date, close_vs_20d_high, close_vs_20d_ma,
      volume_vs_20d_avg, mom_30d_pct (last close vs 30d ago close),
      already_moved (mom_30d_pct >= 15%),
      tech_signal: 'breakout_zone' | 'flying' | 'weak' | 'quiet'
    """
    for suffix in (".TW", ".TWO"):
        p = PRICE_DIR / f"{co_id}{suffix}.csv"
        if p.exists():
            break
    else:
        return {}
    try:
        df = pd.read_csv(p)
    except Exception:
        return {}
    if len(df) < 30:
        return {}
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close", "volume"]).sort_values("date").reset_index(drop=True)
    if len(df) < 30:
        return {}
    last = df.iloc[-1]
    last_close = float(last["close"])
    last_vol = float(last["volume"])
    win20 = df.tail(20)
    high20 = float(win20["close"].max())
    ma20 = float(win20["close"].mean())
    vol20 = float(win20["volume"].mean()) or 1.0
    close_ago_30d = float(df.iloc[-min(30, len(df) - 1)]["close"])
    mom_30d = (last_close - close_ago_30d) / close_ago_30d if close_ago_30d else 0.0

    close_vs_high = last_close / high20 if high20 else 0.0
    close_vs_ma = last_close / ma20 if ma20 else 0.0
    vol_ratio = last_vol / vol20 if vol20 else 0.0

    if close_vs_high > 1.03 or mom_30d > 0.20:
        tech = "flying"           # already broke out — too late
    elif 0.95 <= close_vs_high <= 1.03 and vol_ratio > 1.5:
        tech = "breakout_zone"    # imminent / at breakout — best zone
    elif close_vs_ma < 0.97:
        tech = "weak"             # below MA — no momentum
    else:
        tech = "quiet"            # neutral

    return {
        "last_close": round(last_close, 2),
        "last_date": last["date"].strftime("%Y-%m-%d"),
        "close_vs_20d_high": round(close_vs_high, 3),
        "close_vs_20d_ma": round(close_vs_ma, 3),
        "volume_vs_20d_avg": round(vol_ratio, 2),
        "mom_30d_pct": round(mom_30d * 100, 1),
        "already_moved": bool(mom_30d >= 0.15),
        "tech_signal": tech,
    }


def scan_one(session_reuse: bool, co_id: str, market: str, start: dt.date, end: dt.date):
    """Wrapper around scrape_range with per-symbol JSON cache."""
    from mops_scraper import scrape_range as _scrape

    cache = CACHE_DIR / f"{co_id}_{start.isoformat()}_{end.isoformat()}.json"
    if cache.exists() and cache.stat().st_size > 40:
        return json.loads(cache.read_text(encoding="utf-8"))
    rows = _scrape(co_id, market, start, end, sleep=0.4)
    cache.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="lookback window in days")
    ap.add_argument("--min-volume", type=float, default=5_000_000.0,
                    help="minimum 20-day avg volume")
    ap.add_argument("--limit", type=int, default=400, help="max stocks to scan")
    args = ap.parse_args()

    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days)
    print(f"backfill window: {start} → {end}")
    universe = load_universe_names()

    stocks = pick_liquid_stocks(args.min_volume, args.limit)
    print(f"liquid stocks selected: {len(stocks)}")

    all_matches: list[dict] = []
    surge_history = load_surge_history(within_days=60)
    tech_cache: dict[str, dict] = {}
    for i, code in enumerate(stocks, 1):
        info = universe.get(code)
        if not info:
            continue
        name, market = info
        try:
            rows = scan_one(True, code, market, start, end)
        except Exception as exc:
            print(f"[WARN] {code}: {exc}", file=sys.stderr)
            continue
        tech = tech_cache.get(code)
        if tech is None:
            tech = compute_tech_metrics(code) or {}
            tech_cache[code] = tech
        for a in rows:
            labels, kws = classify(a.get("subject", ""))
            if not labels:
                continue
            recent_t0 = surge_history.get(code)
            all_matches.append({
                "co_id": code,
                "name": name,
                "market": market,
                "date": a.get("date", ""),
                "time": a.get("roc_time", ""),
                "subject": a.get("subject", ""),
                "labels": labels,
                "matched_keywords": kws,
                "already_surged": recent_t0 is not None,
                "days_since_surge": (dt.date.today() - recent_t0).days if recent_t0 else None,
                "tech": tech,
            })
        if i % 25 == 0:
            print(f"  {i}/{len(stocks)} scanned, matches so far: {len(all_matches)}", flush=True)
        # be polite to MOPS
        time.sleep(0.2)

    # do NOT merge with previous scan — old items may have stale labels from
    # earlier classifier rules; always start from this run's fresh classification
    merged = list(all_matches)

    merged.sort(key=lambda r: (0 if not r["already_surged"] else 1,
                               -len(r["labels"]),
                               r["date"]),
                reverse=False)
    merged.sort(key=lambda r: (0 if not r["already_surged"] else 1, -len(r["labels"])))

    dates = sorted({m["date"] for m in merged if m["date"]})
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "deep",
        "backfill_window": {"start": start.isoformat(), "end": end.isoformat()},
        "universe_size": len(stocks),
        "source_date_range": {"min": dates[0] if dates else "",
                              "max": dates[-1] if dates else ""},
        "raw_count": sum(1 for _ in merged),
        "matched_count": len(merged),
        "not_surged_count": sum(1 for m in merged if not m["already_surged"]),
        "already_surged_count": sum(1 for m in merged if m["already_surged"]),
        "items": merged,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}: matched={payload['matched_count']} "
          f"not_surged={payload['not_surged_count']} "
          f"already_surged={payload['already_surged_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
