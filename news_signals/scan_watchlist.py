"""Scan latest MOPS 重訊 across all Taiwan companies and produce a
forward-looking watchlist of stocks whose recent disclosures match the
verified leading-signal whitelist and that have NOT already surged.

Whitelist categories (from comparison_report.md):
- 投資 / 併購 / 轉投資       (delta +35pp, lift 2.75x)
- 庫藏股 / 減資               (delta +20pp, lift inf)
- 高階人事異動                 (delta +20pp, lift inf)
- 股利政策 / 除權息           (delta +20pp, lift 5x)

MOPS endpoint used:
- POST /mops/web/ajax_t05sr01_1 with TYPEK=all&step=0 returns the latest
  batch of daily disclosures (usually today's and yesterday's rows).

Output data/watchlist_scan.json:
- generated_at, source_date_range
- items: [{symbol, name, date, time, subject, labels, matched_keywords,
           already_surged, days_since_surge}]
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
import time
from html import unescape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SURGE_CSV = ROOT / "data" / "surge_events.csv"
OUT = ROOT / "data" / "watchlist_scan.json"

MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1"

# The categories that survived the control-group comparison.
WHITELIST_RULES: list[tuple[str, list[str]]] = [
    ("投資 / 併購 / 轉投資", ["取得", "併購", "投資", "轉投資", "子公司", "設立", "增資", "策略聯盟", "MOU", "合作"]),
    ("庫藏股 / 減資", ["庫藏股", "減資", "註銷"]),
    ("高階人事異動", ["發言人", "董事長", "總經理", "財務長", "獨立董事", "改派", "解任", "任期屆滿"]),
    ("股利政策 / 除權息", ["股利", "配息", "配股", "盈餘分配", "股東常會", "除息", "除權"]),
    # bonus category: sales / order news (not in original whitelist but
    # often accompanies genuine growth catalysts) — kept but tagged.
    ("重大契約 / 訂單", ["合約", "訂單", "簽署"]),
]


def classify(subject: str) -> tuple[list[str], list[str]]:
    """Return (matched_labels, matched_keywords)."""
    labels: list[str] = []
    kws_hit: list[str] = []
    for lbl, kws in WHITELIST_RULES:
        matched = [k for k in kws if k in subject]
        if matched:
            labels.append(lbl)
            kws_hit.extend(matched)
    return labels, kws_hit


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Referer": "https://mopsov.twse.com.tw/mops/web/t05sr01_1",
    })
    return s


def fetch_latest(session: requests.Session, typek: str = "all") -> list[dict]:
    """Return the latest batch of MOPS daily 重訊."""
    r = session.post(
        MOPS_URL,
        data={"TYPEK": typek, "step": "0"},
        timeout=30,
    )
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    table = None
    for tbl in soup.find_all("table", class_="hasBorder"):
        headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if "公司代號" in headers and "主旨" in headers:
            table = tbl
            break
    if table is None:
        return []
    out: list[dict] = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        co_id = tds[0].get_text(strip=True)
        name = tds[1].get_text(strip=True)
        roc_date = tds[2].get_text(strip=True)       # e.g. 115/08/22
        roc_time = tds[3].get_text(strip=True)
        subject = tds[4].get_text(" ", strip=True)
        # convert ROC to Gregorian date
        try:
            y, m, d = roc_date.split("/")
            gdate = dt.date(int(y) + 1911, int(m), int(d)).isoformat()
        except Exception:
            gdate = ""
        out.append({
            "co_id": co_id,
            "name": name,
            "date": gdate,
            "time": roc_time,
            "subject": subject,
        })
    return out


def load_surge_history(within_days: int = 60) -> dict[str, dt.date]:
    """Map co_id → most recent surge T0 within N days."""
    if not SURGE_CSV.exists():
        return {}
    cutoff = dt.date.today() - dt.timedelta(days=within_days)
    latest: dict[str, dt.date] = {}
    with SURGE_CSV.open("r", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            sym = r["symbol"].split(".")[0]  # strip .TW / .TWO
            try:
                t0 = dt.date.fromisoformat(r["T0"])
            except Exception:
                continue
            if t0 < cutoff:
                continue
            if sym not in latest or t0 > latest[sym]:
                latest[sym] = t0
    return latest


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    session = make_session()
    all_ann: list[dict] = []
    for market in ("sii", "otc"):
        try:
            rows = fetch_latest(session, market)
            for r in rows:
                r["market"] = market
            all_ann.extend(rows)
        except Exception as exc:
            print(f"[WARN] {market}: {exc}", file=sys.stderr)
        time.sleep(0.5)

    # dedupe by (co_id, date, time, subject)
    seen: set[tuple] = set()
    unique = []
    for a in all_ann:
        k = (a["co_id"], a["date"], a["time"], a["subject"])
        if k in seen:
            continue
        seen.add(k)
        unique.append(a)
    print(f"fetched {len(unique)} unique announcements (raw)")

    # classify & filter
    surge_history = load_surge_history(within_days=60)
    matches: list[dict] = []
    for a in unique:
        labels, kws = classify(a["subject"])
        if not labels:
            continue
        recent_t0 = surge_history.get(a["co_id"])
        already_surged = recent_t0 is not None
        days_since = ((dt.date.today() - recent_t0).days
                      if recent_t0 else None)
        matches.append({
            **a,
            "labels": labels,
            "matched_keywords": kws,
            "already_surged": already_surged,
            "days_since_surge": days_since,
        })

    # sort: not-yet-surged first, then by number of matched labels desc, then by date desc
    matches.sort(
        key=lambda r: (
            0 if not r["already_surged"] else 1,
            -len(r["labels"]),
            r["date"] or "",
        ),
        reverse=False,
    )
    # reverse date so newest first among not-surged
    matches.sort(
        key=lambda r: (0 if not r["already_surged"] else 1, -len(r["labels"])),
    )

    dates = sorted({a["date"] for a in unique if a["date"]})
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_date_range": {
            "min": dates[0] if dates else "",
            "max": dates[-1] if dates else "",
        },
        "raw_count": len(unique),
        "matched_count": len(matches),
        "not_surged_count": sum(1 for m in matches if not m["already_surged"]),
        "already_surged_count": sum(1 for m in matches if m["already_surged"]),
        "items": matches,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"raw={payload['raw_count']} matched={payload['matched_count']} "
          f"not_surged={payload['not_surged_count']} already_surged={payload['already_surged_count']}")

    # print top 10 not-surged for a quick preview
    print("\n== 觀察名單前 10 名（未飆漲）==")
    top = [m for m in matches if not m["already_surged"]][:10]
    for m in top:
        labels = " · ".join(m["labels"])
        print(f"  {m['co_id']:6} {m['name'][:10]:10} {m['date']} [{labels}] {m['subject'][:50]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
