"""Vercel serverless handler for the SITCA fund-holdings API.

All /api/sitca/* paths are rewritten by vercel.json to /api/sitca?action=...
"""

from __future__ import annotations

import gzip
import json
import os
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "data" / "sitca_bundle.csv.gz"

_CACHE: dict[str, object] = {"df": None}


def _load() -> pd.DataFrame:
    df = _CACHE.get("df")
    if df is not None:
        return df  # type: ignore[return-value]
    if not BUNDLE.exists():
        df = pd.DataFrame()
    else:
        with gzip.open(BUNDLE, "rt", encoding="utf-8") as fh:
            df = pd.read_csv(fh, dtype=str)
        df["amount_num"] = (
            df["amount"].fillna("").str.replace(",", "").replace("", "0").astype(float)
        )
        df["pct_num"] = (
            df["pct_of_nav"].fillna("").str.replace("%", "").replace("", "0").astype(float)
        )
        df["stock_code"] = df["target_code"].fillna("").str.strip()
        df["stock_name"] = df["target_name"].fillna("").str.strip()
        df["stock_id"] = df.apply(
            lambda r: r["stock_code"] if r["stock_code"] else r["stock_name"], axis=1
        )
    _CACHE["df"] = df
    return df


def _monthly_stats(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    g = df.groupby(["year_month", "stock_id", "stock_code", "stock_name"], dropna=False)
    return g.agg(
        company_count=("company_id", "nunique"),
        fund_count=("fund_name", "nunique"),
        total_amount=("amount_num", "sum"),
        avg_pct=("pct_num", "mean"),
    ).reset_index()


def action_status() -> dict:
    df = _load()
    months = sorted(df["year_month"].dropna().unique().tolist()) if not df.empty else []
    return {
        "csv_count": 1,
        "row_count": int(len(df)),
        "stock_row_count": int(len(df)),
        "months": months,
        "company_count": int(df["company_id"].nunique()) if not df.empty else 0,
        "source": "bundle",
    }


def action_months() -> dict:
    df = _load()
    return {"months": sorted(df["year_month"].dropna().unique().tolist())}


def action_companies() -> dict:
    df = _load()
    if df.empty:
        return {"companies": []}
    pairs = (
        df[["company_id", "company_name"]]
        .drop_duplicates()
        .sort_values("company_id")
    )
    return {
        "companies": [
            {"id": r.company_id, "name": r.company_name}
            for r in pairs.itertuples(index=False)
        ]
    }


def action_top_stocks(params: dict[str, list[str]]) -> dict:
    month = (params.get("month", [""])[0]) or None
    limit = int(params.get("limit", ["10"])[0])
    df = _load()
    stats = _monthly_stats(df)
    if stats.empty:
        return {"items": []}
    dom_type = (
        df.groupby("stock_id")["target_type"]
        .agg(lambda s: s.value_counts().idxmax() if len(s) else "")
        .to_dict()
    )
    if month:
        sub = stats[stats["year_month"] == month].copy()
        sub = sub.sort_values(["company_count", "total_amount"], ascending=[False, False]).head(limit)
        items = [
            {
                "stock_id": r.stock_id,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "type": dom_type.get(r.stock_id, ""),
                "company_count": int(r.company_count),
                "fund_count": int(r.fund_count),
                "total_amount": float(r.total_amount),
                "month": month,
            }
            for r in sub.itertuples(index=False)
        ]
    else:
        overall = (
            stats.groupby(["stock_id", "stock_code", "stock_name"])
            .agg(
                max_company_count=("company_count", "max"),
                avg_company_count=("company_count", "mean"),
                months_present=("year_month", "nunique"),
                sum_amount=("total_amount", "sum"),
            )
            .reset_index()
            .sort_values(["max_company_count", "avg_company_count"], ascending=[False, False])
            .head(limit)
        )
        items = [
            {
                "stock_id": r.stock_id,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "type": dom_type.get(r.stock_id, ""),
                "max_company_count": int(r.max_company_count),
                "avg_company_count": round(float(r.avg_company_count), 2),
                "months_present": int(r.months_present),
                "total_amount": float(r.sum_amount),
            }
            for r in overall.itertuples(index=False)
        ]
    return {"items": items}


def action_matrix(params: dict[str, list[str]]) -> dict:
    limit = int(params.get("limit", ["30"])[0])
    df = _load()
    stats = _monthly_stats(df)
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
    sub = stats[stats["stock_id"].isin(overall["stock_id"].tolist())]
    pivot = sub.pivot_table(
        index=["stock_id", "stock_code", "stock_name"],
        columns="year_month",
        values="company_count",
        fill_value=0,
    ).reindex(columns=months, fill_value=0)
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


def action_sync(params: dict[str, list[str]]) -> dict:
    direction = params.get("direction", ["buy"])[0]
    min_delta = int(params.get("min_delta", ["3"])[0])
    limit = int(params.get("limit", ["30"])[0])
    df = _load()
    stats = _monthly_stats(df)
    if stats.empty:
        return {"direction": direction, "min_delta": min_delta, "items": []}
    months = sorted(stats["year_month"].unique())
    pivot = stats.pivot_table(
        index=["stock_id", "stock_code", "stock_name"],
        columns="year_month",
        values="company_count",
        fill_value=0,
    ).reindex(columns=months, fill_value=0)
    diffs = pivot.diff(axis=1).fillna(0).astype(int)
    records = []
    for j, col in enumerate(diffs.columns):
        if j == 0:
            continue
        for idx, delta in diffs[col].items():
            stock_id, stock_code, stock_name = idx
            records.append(
                {
                    "month": col,
                    "stock_id": stock_id,
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "prev_count": int(pivot.at[idx, months[j - 1]]),
                    "curr_count": int(pivot.at[idx, col]),
                    "delta": int(delta),
                }
            )
    if direction == "buy":
        records = [r for r in records if r["delta"] >= min_delta]
        records.sort(key=lambda r: (-r["delta"], r["month"]))
    else:
        records = [r for r in records if r["delta"] <= -min_delta]
        records.sort(key=lambda r: (r["delta"], r["month"]))
    return {"direction": direction, "min_delta": min_delta, "items": records[:limit]}


def action_stock(params: dict[str, list[str]]) -> dict:
    stock_id = params.get("id", [""])[0]
    df = _load()
    if df.empty or not stock_id:
        return {"stock_id": stock_id, "months": [], "rows": []}
    sub = df[
        (df["stock_id"] == stock_id)
        | (df["stock_code"] == stock_id)
        | (df["stock_name"] == stock_id)
    ].copy()
    if sub.empty:
        return {"stock_id": stock_id, "months": [], "rows": []}
    sub = sub.sort_values(["year_month", "company_name", "fund_name"])
    months = sorted(sub["year_month"].unique())
    stock_code = sub["stock_code"].iloc[0]
    stock_name = sub["stock_name"].iloc[0]
    by_month = (
        sub.groupby("year_month")
        .agg(
            company_count=("company_id", "nunique"),
            fund_count=("fund_name", "nunique"),
            total_amount=("amount_num", "sum"),
        )
        .reset_index()
    )
    rows = [
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
        for r in sub.itertuples(index=False)
    ]
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


HANDLERS = {
    "status": lambda p: action_status(),
    "months": lambda p: action_months(),
    "companies": lambda p: action_companies(),
    "top-stocks": action_top_stocks,
    "matrix": action_matrix,
    "sync": action_sync,
    "stock": action_stock,
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        action = (params.get("action", [""])[0]).strip()
        if not action:
            self._send_json({"error": "missing action"}, status=400)
            return
        fn = HANDLERS.get(action)
        if not fn:
            self._send_json({"error": f"unknown action: {action}"}, status=404)
            return
        try:
            body = fn(params)
            self._send_json(body)
        except Exception as exc:
            self._send_json({"error": str(exc), "action": action}, status=500)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return
