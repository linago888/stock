"""SITCA fund-holdings analysis backend used by the website.

Reads the per-(month, company) CSVs scraped by sitca_scraper.py and exposes
analysis helpers returning plain dicts (JSON-friendly).
"""

from __future__ import annotations

import csv
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "sitca"

STOCK_TYPE_KEYWORDS = (
    "國內上市", "國內上櫃", "國內興櫃",
    "國外股票", "存託憑證",
)

_CACHE: dict[str, object] = {"mtime": 0.0, "df": None, "stocks": None}
_LOCK = threading.Lock()

JOBS: dict[str, dict[str, object]] = {}


def _is_stock_type(t: object) -> bool:
    if not isinstance(t, str):
        return False
    return any(k in t for k in STOCK_TYPE_KEYWORDS)


def _num(s: object) -> float:
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def list_csv_files() -> list[Path]:
    if not DATA_DIR.exists():
        return []
    return sorted(DATA_DIR.glob("2*_A*.csv"))


def _latest_mtime() -> float:
    files = list_csv_files()
    if not files:
        return 0.0
    return max(p.stat().st_mtime for p in files)


def load_all(force: bool = False) -> pd.DataFrame:
    """Load and cache merged DataFrame; reload only when CSV files change."""
    with _LOCK:
        mtime = _latest_mtime()
        if (
            not force
            and _CACHE["df"] is not None
            and mtime == _CACHE["mtime"]
        ):
            return _CACHE["df"]  # type: ignore[return-value]

        files = list_csv_files()
        if not files:
            df = pd.DataFrame(
                columns=[
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
            )
        else:
            df = pd.concat(
                [pd.read_csv(p, dtype=str) for p in files], ignore_index=True
            )
            df["amount_num"] = df["amount"].map(_num)
            df["pct_num"] = df["pct_of_nav"].map(_num)
            df["is_stock"] = df["target_type"].map(_is_stock_type)
            df["stock_code"] = df["target_code"].fillna("").str.strip()
            df["stock_name"] = df["target_name"].fillna("").str.strip()
            df["stock_id"] = df.apply(
                lambda r: r["stock_code"] if r["stock_code"] else r["stock_name"],
                axis=1,
            )

        _CACHE["df"] = df
        _CACHE["mtime"] = mtime
        _CACHE["stocks"] = None
        return df


def load_stocks(force: bool = False) -> pd.DataFrame:
    df = load_all(force=force)
    if _CACHE["stocks"] is None or force:
        stocks = df[df["is_stock"]].copy() if len(df) else df.copy()
        _CACHE["stocks"] = stocks
    return _CACHE["stocks"]  # type: ignore[return-value]


def list_months() -> list[str]:
    df = load_all()
    if df.empty:
        return []
    return sorted(df["year_month"].dropna().unique().tolist())


def list_companies() -> list[dict]:
    df = load_all()
    if df.empty:
        return []
    pairs = (
        df[["company_id", "company_name"]]
        .drop_duplicates()
        .sort_values("company_id")
    )
    return [
        {"id": r.company_id, "name": r.company_name}
        for r in pairs.itertuples(index=False)
    ]


def status() -> dict:
    files = list_csv_files()
    df = load_all()
    months = list_months()
    return {
        "data_dir": str(DATA_DIR),
        "csv_count": len(files),
        "row_count": int(len(df)),
        "stock_row_count": int(df["is_stock"].sum()) if "is_stock" in df else 0,
        "months": months,
        "company_count": df["company_id"].nunique() if not df.empty else 0,
        "last_updated": _CACHE["mtime"],
    }


def _monthly_stock_stats() -> pd.DataFrame:
    stocks = load_stocks()
    if stocks.empty:
        return stocks
    g = stocks.groupby(
        ["year_month", "stock_id", "stock_code", "stock_name"], dropna=False
    )
    out = g.agg(
        company_count=("company_id", "nunique"),
        fund_count=("fund_name", "nunique"),
        total_amount=("amount_num", "sum"),
        avg_pct=("pct_num", "mean"),
    ).reset_index()
    return out


def top_stocks(month: str | None = None, limit: int = 10) -> list[dict]:
    """Return stocks with the most fund companies holding them.

    If month is None, ranks by max company_count across all months.
    """
    stats = _monthly_stock_stats()
    if stats.empty:
        return []

    stock_dom_type = (
        load_stocks()
        .groupby("stock_id")["target_type"]
        .agg(lambda s: s.value_counts().idxmax() if len(s) else "")
        .to_dict()
    )

    if month:
        sub = stats[stats["year_month"] == month].copy()
        sub = sub.sort_values(
            ["company_count", "total_amount"], ascending=[False, False]
        ).head(limit)
        return [
            {
                "stock_id": r.stock_id,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "type": stock_dom_type.get(r.stock_id, ""),
                "company_count": int(r.company_count),
                "fund_count": int(r.fund_count),
                "total_amount": float(r.total_amount),
                "month": month,
            }
            for r in sub.itertuples(index=False)
        ]

    overall = (
        stats.groupby(["stock_id", "stock_code", "stock_name"])
        .agg(
            max_company_count=("company_count", "max"),
            avg_company_count=("company_count", "mean"),
            months_present=("year_month", "nunique"),
            sum_amount=("total_amount", "sum"),
        )
        .reset_index()
        .sort_values(
            ["max_company_count", "avg_company_count"], ascending=[False, False]
        )
        .head(limit)
    )
    return [
        {
            "stock_id": r.stock_id,
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "type": stock_dom_type.get(r.stock_id, ""),
            "max_company_count": int(r.max_company_count),
            "avg_company_count": round(float(r.avg_company_count), 2),
            "months_present": int(r.months_present),
            "total_amount": float(r.sum_amount),
        }
        for r in overall.itertuples(index=False)
    ]


def month_matrix(limit: int = 30) -> dict:
    """Return a stock × month matrix of company_count for the top stocks."""
    stats = _monthly_stock_stats()
    if stats.empty:
        return {"months": [], "rows": []}

    months = sorted(stats["year_month"].unique())

    overall = (
        stats.groupby(["stock_id", "stock_code", "stock_name"])
        .agg(max_co=("company_count", "max"))
        .reset_index()
        .sort_values("max_co", ascending=False)
        .head(limit)
    )
    top_ids = overall["stock_id"].tolist()
    sub = stats[stats["stock_id"].isin(top_ids)]
    pivot = sub.pivot_table(
        index=["stock_id", "stock_code", "stock_name"],
        columns="year_month",
        values="company_count",
        fill_value=0,
    )
    pivot = pivot.reindex(columns=months, fill_value=0)
    pivot = pivot.loc[
        pd.MultiIndex.from_frame(overall[["stock_id", "stock_code", "stock_name"]])
    ]

    rows = []
    for idx, values in pivot.iterrows():
        stock_id, stock_code, stock_name = idx
        rows.append(
            {
                "stock_id": stock_id,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "counts": [int(values[m]) for m in months],
            }
        )
    return {"months": months, "rows": rows}


def synchronized_moves(
    direction: str = "buy",
    min_delta: int = 3,
    limit: int = 30,
) -> list[dict]:
    """Find stocks where company_count moved sharply in a single month.

    direction = 'buy'  -> delta >= +min_delta (sync buy)
                'sell' -> delta <= -min_delta (sync sell)
    """
    stats = _monthly_stock_stats()
    if stats.empty:
        return []
    months = sorted(stats["year_month"].unique())
    if len(months) < 2:
        return []
    pivot = stats.pivot_table(
        index=["stock_id", "stock_code", "stock_name"],
        columns="year_month",
        values="company_count",
        fill_value=0,
    )
    diffs = pivot.diff(axis=1).fillna(0).astype(int)

    records = []
    for col in diffs.columns[1:]:
        col_diffs = diffs[col]
        for idx, delta in col_diffs.items():
            stock_id, stock_code, stock_name = idx
            prev_count = int(pivot.at[idx, months[months.index(col) - 1]])
            curr_count = int(pivot.at[idx, col])
            records.append(
                {
                    "month": col,
                    "stock_id": stock_id,
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "prev_count": prev_count,
                    "curr_count": curr_count,
                    "delta": int(delta),
                }
            )

    if direction == "buy":
        records = [r for r in records if r["delta"] >= min_delta]
        records.sort(key=lambda r: (-r["delta"], r["month"]))
    else:
        records = [r for r in records if r["delta"] <= -min_delta]
        records.sort(key=lambda r: (r["delta"], r["month"]))
    return records[:limit]


def stock_detail(stock_id: str) -> dict:
    """Return all per-month per-fund records for a single stock."""
    stocks = load_stocks()
    if stocks.empty:
        return {"stock_id": stock_id, "months": [], "rows": []}
    sub = stocks[
        (stocks["stock_id"] == stock_id)
        | (stocks["stock_code"] == stock_id)
        | (stocks["stock_name"] == stock_id)
    ].copy()
    if sub.empty:
        return {"stock_id": stock_id, "months": [], "rows": []}

    sub = sub.sort_values(["year_month", "company_name", "fund_name"])
    months = sorted(sub["year_month"].unique())

    stock_code = sub["stock_code"].iloc[0]
    stock_name = sub["stock_name"].iloc[0]

    # company_count per month
    by_month = (
        sub.groupby("year_month")
        .agg(
            company_count=("company_id", "nunique"),
            fund_count=("fund_name", "nunique"),
            total_amount=("amount_num", "sum"),
        )
        .reset_index()
    )

    rows = []
    for r in sub.itertuples(index=False):
        rows.append(
            {
                "year_month": r.year_month,
                "company_id": r.company_id,
                "company_name": r.company_name,
                "fund_name": r.fund_name,
                "rank": r.rank,
                "target_type": r.target_type,
                "amount": r.amount,
                "pct_of_nav": r.pct_of_nav,
            }
        )

    return {
        "stock_id": stock_id,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "months": months,
        "monthly_stats": [
            {
                "month": r.year_month,
                "company_count": int(r.company_count),
                "fund_count": int(r.fund_count),
                "total_amount": float(r.total_amount),
            }
            for r in by_month.itertuples(index=False)
        ],
        "rows": rows,
    }


SITCA_INDEX_URL = "https://www.sitca.org.tw/ROC/Industry/IN2629.aspx?pid=IN22601_04"


def latest_remote_month() -> str | None:
    """Fetch SITCA index and return the latest available YYYYMM."""
    try:
        req = Request(SITCA_INDEX_URL, headers={"User-Agent": "Mozilla/5.0"})
        html = urlopen(req, timeout=15).read().decode("utf-8", "ignore")
    except Exception:
        return None
    months = re.findall(r'<option[^>]*value="(\d{6})"', html)
    return max(months) if months else None


def _month_list(spec: str) -> list[str]:
    import datetime as dt

    spec = spec or "202511-202604"
    spec = str(spec).strip()
    if spec in ("auto", "latest"):
        latest = latest_remote_month()
        if latest is None:
            return []
        return [latest]
    a, b = (spec.split("-", 1) + [None])[:2] if "-" in spec else (spec, spec)
    start = dt.date(int(a[:4]), int(a[4:]), 1)
    end = dt.date(int(b[:4]), int(b[4:]), 1)
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y%m"))
        y, m = (cur.year, cur.month + 1) if cur.month < 12 else (cur.year + 1, 1)
        cur = dt.date(y, m, 1)
    return out


def _company_count() -> int:
    """Best-effort count of investment companies (35-36)."""
    n = len(list_companies())
    return n or 36


def _count_csvs_for(month_list: list[str]) -> int:
    return sum(1 for ym in month_list for _ in DATA_DIR.glob(f"{ym}_A*.csv"))


def start_scrape_job(
    months: str = "",
    sleep: float = 0.6,
    force: bool = False,
) -> dict:
    """Spawn a background scrape job.

    months: "YYYYMM", "YYYYMM-YYYYMM", or "auto" / "latest" (detect from SITCA).
    force: when True, scraper does NOT skip existing CSVs (full re-fetch).
    """
    import subprocess
    import sys

    job_id = uuid.uuid4().hex
    month_list = _month_list(months)
    companies = _company_count()
    expected_total = len(month_list) * companies
    baseline = _count_csvs_for(month_list)
    # passed to the subprocess: if month_list was resolved from 'auto', use
    # an explicit range (single month) so the scraper doesn't repeat detection.
    months_for_cli = (
        f"{month_list[0]}-{month_list[-1]}" if month_list else (months or "202511-202604")
    )
    JOBS[job_id] = {
        "id": job_id,
        "status": "running",
        "started_at": time.time(),
        "log_path": str(DATA_DIR / f"scrape_{job_id}.log"),
        "months": months,
        "month_list": month_list,
        "resolved_months": months_for_cli,
        "company_count": companies,
        "expected_total": expected_total,
        "baseline": baseline,
        "force": bool(force),
    }

    def worker() -> None:
        log = DATA_DIR / f"scrape_{job_id}.log"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if force:
            # delete existing CSVs for the resolved months so progress restarts at 0
            for ym in month_list:
                for p in DATA_DIR.glob(f"{ym}_A*.csv"):
                    try:
                        p.unlink()
                    except Exception:
                        pass
            # baseline is now 0 for these months
            JOBS[job_id]["baseline"] = 0
        cmd = [
            sys.executable,
            str(ROOT / "sitca_scraper.py"),
            "--months",
            months_for_cli,
            "--sleep",
            str(sleep),
        ]
        if not force:
            cmd.append("--resume")
        try:
            with log.open("w", encoding="utf-8") as fh:
                proc = subprocess.run(
                    cmd, stdout=fh, stderr=fh, text=True, encoding="utf-8"
                )
            JOBS[job_id]["status"] = "done" if proc.returncode == 0 else "error"
            JOBS[job_id]["exit_code"] = proc.returncode
        except Exception as exc:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(exc)
        JOBS[job_id]["finished_at"] = time.time()
        # reset cache so new data is picked up
        with _LOCK:
            _CACHE["mtime"] = 0.0
            _CACHE["df"] = None
            _CACHE["stocks"] = None

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


def scrape_job_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise KeyError("找不到爬蟲工作")
    log_path = Path(str(job.get("log_path", "")))
    last_lines: list[str] = []
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                last_lines = fh.readlines()[-10:]
        except Exception:
            last_lines = []

    month_list = list(job.get("month_list") or [])
    expected_total = int(job.get("expected_total") or 0)
    if month_list:
        csv_count = _count_csvs_for(month_list)
    else:
        csv_count = len(list_csv_files())

    started = float(job.get("started_at") or 0)
    baseline = int(job.get("baseline") or 0)
    elapsed = max(0.0, time.time() - started) if started else 0.0
    done_new = max(0, csv_count - baseline)
    work = max(0, expected_total - baseline)
    rate = done_new / elapsed if elapsed > 0 and done_new else 0.0
    eta = (work - done_new) / rate if rate > 0 else None

    return {
        **job,
        "csv_count": csv_count,
        "expected_total": expected_total,
        "baseline": baseline,
        "done_in_job": done_new,
        "work_in_job": work,
        "elapsed_sec": round(elapsed, 1),
        "eta_sec": round(eta, 1) if eta is not None else None,
        "log_tail": [ln.rstrip() for ln in last_lines],
    }
