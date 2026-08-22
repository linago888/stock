"""Scrape MOPS 重大訊息 for a given stock over a date range.

MOPS endpoints (both live on mopsov.twse.com.tw):
- POST /mops/web/ajax_t05st01 with step=1 -> list of announcements
- POST /mops/web/ajax_t05st01 with step=2 -> single announcement detail

Usage:
    python mops_scraper.py --symbol 3576 --market sii --start 2026-01-06 --end 2026-02-05
"""

from __future__ import annotations

import argparse
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

BASE = "https://mopsov.twse.com.tw/mops/web/ajax_t05st01"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "mops"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Referer": "https://mopsov.twse.com.tw/mops/web/t05st01",
        }
    )
    return s


def roc_year(g: int) -> int:
    return g - 1911


def month_range(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    """(gregorian_year, month) pairs covering [start, end] inclusive."""
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def fetch_list(
    session: requests.Session, co_id: str, market: str, year: int, month: int
) -> list[dict]:
    """Return announcement metadata for a (co_id, month)."""
    data = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "true",
        "off": "1",
        "keyword4": "",
        "code1": "",
        "TYPEK2": "",
        "checkbtn": "",
        "queryName": "co_id",
        "inpuType": "co_id",
        "TYPEK": market,
        "co_id": co_id,
        "year": str(roc_year(year)),
        "month": f"{month:02d}",
    }
    r = session.post(BASE, data=data, timeout=30)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    table = None
    for tbl in soup.find_all("table", class_="hasBorder"):
        headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if "發言日期" in headers and "主旨" in headers:
            table = tbl
            break
    if table is None:
        return []

    out: list[dict] = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        subject_cell = tds[4].get_text(" ", strip=True)
        btn = tr.find("input", {"value": "詳細資料"})
        seq_no = spoke_date = spoke_time = ""
        if btn and btn.has_attr("onclick"):
            oc = btn["onclick"]
            m_seq = re.search(r"seq_no\.value='([^']+)'", oc)
            m_date = re.search(r"spoke_date\.value='([^']+)'", oc)
            m_time = re.search(r"spoke_time\.value='([^']+)'", oc)
            if m_seq:
                seq_no = m_seq.group(1)
            if m_date:
                spoke_date = m_date.group(1)
            if m_time:
                spoke_time = m_time.group(1)
        out.append(
            {
                "co_id": co_id,
                "market": market,
                "spoke_date": spoke_date,       # YYYYMMDD
                "spoke_time": spoke_time,       # HHMMSS
                "roc_date": tds[2].get_text(strip=True),
                "roc_time": tds[3].get_text(strip=True),
                "subject": subject_cell,
                "seq_no": seq_no,
            }
        )
    return out


def fetch_detail(
    session: requests.Session, meta: dict, year_of_row: int, month_of_row: int
) -> str:
    data = {
        "step": "2",
        "firstin": "true",
        "off": "1",
        "keyword4": "",
        "code1": "",
        "TYPEK2": "",
        "checkbtn": "",
        "queryName": "co_id",
        "inpuType": "co_id",
        "TYPEK": meta["market"],
        "co_id": meta["co_id"],
        "year": str(roc_year(year_of_row)),
        "month": f"{month_of_row:02d}",
        "seq_no": meta["seq_no"],
        "spoke_date": meta["spoke_date"],
        "spoke_time": meta["spoke_time"],
    }
    r = session.post(BASE, data=data, timeout=30)
    r.encoding = "utf-8"
    text = re.sub(r"<script.*?</script>", "", r.text, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text


def scrape_range(
    co_id: str,
    market: str,
    start: dt.date,
    end: dt.date,
    sleep: float = 0.6,
) -> list[dict]:
    session = make_session()
    months = month_range(start, end)
    print(f"  querying {len(months)} month(s): {months}", flush=True)
    all_rows: list[dict] = []
    for y, m in months:
        try:
            rows = fetch_list(session, co_id, market, y, m)
        except Exception as exc:
            print(f"    [WARN] list {y}-{m}: {exc}", flush=True)
            rows = []
        # filter to date range
        for row in rows:
            sd = row.get("spoke_date")
            if not sd or len(sd) != 8:
                continue
            row_date = dt.date(int(sd[:4]), int(sd[4:6]), int(sd[6:]))
            if row_date < start or row_date > end:
                continue
            row["date"] = row_date.isoformat()
            row["source_year"] = y
            row["source_month"] = m
            all_rows.append(row)
        time.sleep(sleep)
    # fetch details
    for i, row in enumerate(all_rows, 1):
        try:
            row["detail"] = fetch_detail(session, row, row["source_year"], row["source_month"])
        except Exception as exc:
            print(f"    [WARN] detail {row['spoke_date']} {row['seq_no']}: {exc}", flush=True)
            row["detail"] = ""
        print(f"    [{i}/{len(all_rows)}] {row['date']} {row['subject'][:40]}", flush=True)
        time.sleep(sleep)
    return all_rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="company code, e.g. 3576")
    ap.add_argument("--market", default="sii", choices=["sii", "otc", "rotc"],
                    help="sii=上市, otc=上櫃")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--sleep", type=float, default=0.6)
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    print(f"scraping MOPS 重訊 for {args.symbol} ({args.market}) "
          f"{start} → {end}")
    rows = scrape_range(args.symbol, args.market, start, end, sleep=args.sleep)

    out_path = OUT_DIR / f"{args.symbol}_{args.start}_{args.end}.json"
    out_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {out_path} ({len(rows)} announcements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
