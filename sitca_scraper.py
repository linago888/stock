"""SITCA fund top-10 holdings scraper.

Iterates over (year-month × investment company), queries the SITCA page,
parses the holdings table and writes one CSV row per (month, company, fund,
rank, target).

Usage:
    python sitca_scraper.py --months 202511-202604 --out data/sitca

The page exposes top-10 holdings per fund per month. We query by company
(radio = rbComid), which returns every fund of that company plus its top
holdings of every type (stock, bond, fund, ETF, ...). We keep them all
in CSV and filter by 標的種類 downstream.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import time
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

URL = "https://www.sitca.org.tw/ROC/Industry/IN2629.aspx?pid=IN22601_04"

HEADERS_OUT = [
    "year_month",
    "company_id",
    "company_name",
    "fund_name",
    "rank",
    "target_type",      # 標的種類 (股票/債券/...)
    "target_code",      # 代號 / ISIN / Bloomberg
    "target_name",
    "amount",           # 金額(千元)
    "pct_of_nav",       # 占基金淨資產比例(%)
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Referer": URL,
        }
    )
    return s


def fetch_form_state(session: requests.Session) -> dict[str, str]:
    r = session.get(URL, timeout=30)
    r.encoding = "utf-8"
    html = r.text

    def grab(name: str) -> str:
        m = re.search(rf'id="{name}" value="([^"]*)"', html)
        return m.group(1) if m else ""

    return {
        "__VIEWSTATE": grab("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": grab("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": grab("__EVENTVALIDATION"),
    }


def list_companies(session: requests.Session) -> list[tuple[str, str]]:
    r = session.get(URL, timeout=30)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    sel = soup.find("select", id="ctl00_ContentPlaceHolder1_ddlQ_Comid")
    if sel is None:
        raise RuntimeError("company dropdown not found")
    out = []
    for opt in sel.find_all("option"):
        code = opt.get("value", "").strip()
        text = opt.get_text(strip=True)
        # text looks like "A0001 兆豐投信"
        name = text.split(maxsplit=1)[1] if " " in text else text
        if code:
            out.append((code, name))
    return out


def query(
    session: requests.Session,
    state: dict[str, str],
    ym: str,
    comid: str,
) -> str:
    data = {
        **state,
        "ctl00$ContentPlaceHolder1$ddlQ_YM": ym,
        "ctl00$ContentPlaceHolder1$rdo1": "rbComid",
        "ctl00$ContentPlaceHolder1$ddlQ_Comid": comid,
        "ctl00$ContentPlaceHolder1$ddlQ_Class": "AA1",
        "ctl00$ContentPlaceHolder1$ddlQ_Comid1": comid,
        "ctl00$ContentPlaceHolder1$ddlQ_Class1": "",
        "ctl00$ContentPlaceHolder1$BtnQuery": "查詢",
    }
    r = session.post(URL, data=data, timeout=60)
    r.encoding = "utf-8"
    # refresh state for next request
    html = r.text
    for k in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        m = re.search(rf'id="{k}" value="([^"]*)"', html)
        if m:
            state[k] = m.group(1)
    return html


def parse_holdings(html: str, ym: str, company_id: str, company_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    # The data table is the inner table whose first row has '基金名稱' as the
    # first cell. Use recursive=False on tr so we don't pick up nested tables.
    target_tbl = None
    for tbl in soup.find_all("table"):
        trs = tbl.find_all("tr", recursive=False)
        if not trs:
            continue
        first_cells = [td.get_text(strip=True) for td in trs[0].find_all(["th", "td"], recursive=False)]
        if first_cells and first_cells[0] == "基金名稱" and "名次" in first_cells:
            target_tbl = tbl
            break
    if target_tbl is None:
        return []

    rows = target_tbl.find_all("tr", recursive=False)
    headers = [td.get_text(strip=True) for td in rows[0].find_all(["th", "td"], recursive=False)]

    # ranked-row layout: when a row also contains a fund name (first cell looks
    # like a fund title rather than a numeric rank), it has 10 cells and starts
    # with fund_name. Otherwise it has 9 cells starting with rank.
    out: list[dict] = []
    current_fund = ""
    for tr in rows[1:]:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td"], recursive=False)]
        if not cells:
            continue
        # subtotal rows look like ['合計', '63.74'] or similar -- skip
        if cells[0].startswith("合計") or cells[0].startswith("小計"):
            continue
        if len(cells) >= 10:
            # row with fund name
            current_fund = cells[0]
            rank = cells[1]
            target_type = cells[2]
            target_code = cells[3]
            target_name = cells[4]
            amount = cells[5]
            pct = cells[-1]
        elif len(cells) >= 9:
            rank = cells[0]
            target_type = cells[1]
            target_code = cells[2]
            target_name = cells[3]
            amount = cells[4]
            pct = cells[-1]
        else:
            continue
        # skip rows that don't actually carry a rank number
        if not rank.strip().isdigit():
            continue
        out.append(
            {
                "year_month": ym,
                "company_id": company_id,
                "company_name": company_name,
                "fund_name": current_fund,
                "rank": rank,
                "target_type": target_type,
                "target_code": target_code,
                "target_name": target_name,
                "amount": amount,
                "pct_of_nav": pct,
            }
        )
    return out


def month_range(spec: str) -> list[str]:
    if "-" in spec:
        a, b = spec.split("-", 1)
    else:
        a = b = spec
    start = dt.date(int(a[:4]), int(a[4:]), 1)
    end = dt.date(int(b[:4]), int(b[4:]), 1)
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y%m"))
        y, m = cur.year, cur.month + 1
        if m > 12:
            y, m = y + 1, 1
        cur = dt.date(y, m, 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="202511-202604",
                    help="YYYYMM or YYYYMM-YYYYMM range, inclusive")
    ap.add_argument("--out", default=r"D:\Workplace\stock\data\sitca")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--companies", default="", help="comma-separated company ids; default all")
    ap.add_argument("--resume", action="store_true",
                    help="skip (month, company) pairs that already have a CSV")
    args = ap.parse_args()

    months = month_range(args.months)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = make_session()
    companies = list_companies(session)
    if args.companies:
        wanted = {c.strip() for c in args.companies.split(",") if c.strip()}
        companies = [(c, n) for c, n in companies if c in wanted]

    state = fetch_form_state(session)
    print(f"months={months}", flush=True)
    print(f"companies={len(companies)}", flush=True)

    total_rows = 0
    for ym in months:
        for comid, cname in companies:
            csv_path = out_dir / f"{ym}_{comid}.csv"
            if args.resume and csv_path.exists() and csv_path.stat().st_size > 100:
                continue
            try:
                html = query(session, state, ym, comid)
            except Exception as exc:
                print(f"[ERROR] {ym} {comid}: {exc}", flush=True)
                # rebuild state and try once more
                state = fetch_form_state(session)
                time.sleep(2.0)
                try:
                    html = query(session, state, ym, comid)
                except Exception as exc2:
                    print(f"[GIVEUP] {ym} {comid}: {exc2}", flush=True)
                    continue
            holdings = parse_holdings(html, ym, comid, cname)
            with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=HEADERS_OUT)
                w.writeheader()
                w.writerows(holdings)
            total_rows += len(holdings)
            print(f"{ym} {comid} {cname}: {len(holdings)} rows", flush=True)
            time.sleep(args.sleep)
    print(f"total rows: {total_rows}", flush=True)


if __name__ == "__main__":
    main()
