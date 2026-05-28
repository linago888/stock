"""Aggregate scraped SITCA top-10 holdings into stock-level statistics.

Outputs (under data/sitca/_reports):
  - holdings_all.csv          : merged raw rows
  - holdings_stocks.csv       : filtered to stock-type rows
  - top10_overall.csv         : top 10 stocks by # companies holding (any month)
  - top10_by_month.csv        : top 10 per month
  - company_count_by_month.csv: pivot of company-count per (stock, month)
  - month_diff.csv            : month-over-month change in company_count
  - synchronized_moves.csv    : stocks where company_count changes >= threshold in one month
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(r"D:\Workplace\stock\data\sitca")
OUT_DIR = DATA_DIR / "_reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 標的種類 keywords that count as stock (vs bond / fund / ETF / others)
STOCK_TYPES = (
    "國內上市", "國內上櫃", "國內興櫃",
    "國外股票", "國外存託憑證", "存託憑證",
)


def is_stock_type(t: str) -> bool:
    if not isinstance(t, str):
        return False
    return any(k in t for k in STOCK_TYPES)


def normalize_amount(s: str) -> float:
    if not isinstance(s, str):
        return 0.0
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def normalize_pct(s: str) -> float:
    if not isinstance(s, str):
        return 0.0
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def stock_key(code: str, name: str) -> tuple[str, str]:
    """Pick a stable (code, name) key.

    For Taiwan-listed stocks the code is a 4-digit number; we use the code
    when present. Foreign stocks use an ISIN/CUSIP-like code. We fall back to
    the name when the code is empty.
    """
    code = (code or "").strip()
    name = (name or "").strip()
    return code, name


def main() -> None:
    files = sorted(DATA_DIR.glob("2*_A*.csv"))
    if not files:
        print("no scraped CSV found", file=sys.stderr)
        sys.exit(1)

    print(f"merging {len(files)} CSV files")
    df = pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)
    df["amount_num"] = df["amount"].map(normalize_amount)
    df["pct_num"] = df["pct_of_nav"].map(normalize_pct)
    df["is_stock"] = df["target_type"].map(is_stock_type)
    df.to_csv(OUT_DIR / "holdings_all.csv", index=False, encoding="utf-8-sig")

    stocks = df[df["is_stock"]].copy()
    # Clean up stock code & name. Taiwan codes are 4 digits; foreign codes are
    # ISIN strings. Use code as primary key; fall back to name when missing.
    stocks["stock_code"] = stocks["target_code"].fillna("").str.strip()
    stocks["stock_name"] = stocks["target_name"].fillna("").str.strip()
    stocks["stock_id"] = stocks.apply(
        lambda r: r["stock_code"] if r["stock_code"] else r["stock_name"], axis=1
    )
    stocks.to_csv(OUT_DIR / "holdings_stocks.csv", index=False, encoding="utf-8-sig")
    print(f"stock-type rows: {len(stocks)}")

    # --- aggregate: per (month, stock) ---
    grp = stocks.groupby(["year_month", "stock_id", "stock_code", "stock_name"], dropna=False)
    monthly = grp.agg(
        company_count=("company_id", "nunique"),
        fund_count=("fund_name", "nunique"),
        total_amount=("amount_num", "sum"),
        companies=("company_name", lambda s: ",".join(sorted(set(s)))),
    ).reset_index()
    monthly.sort_values(["year_month", "company_count", "total_amount"], ascending=[True, False, False], inplace=True)
    monthly.to_csv(OUT_DIR / "monthly_stock_stats.csv", index=False, encoding="utf-8-sig")

    # --- top 10 per month ---
    top_by_month = (
        monthly.sort_values(["year_month", "company_count", "total_amount"], ascending=[True, False, False])
        .groupby("year_month")
        .head(10)
    )
    top_by_month.to_csv(OUT_DIR / "top10_by_month.csv", index=False, encoding="utf-8-sig")

    # --- overall top 10 across half year (using max company_count over months) ---
    overall = (
        monthly.groupby(["stock_id", "stock_code", "stock_name"])
        .agg(
            months_present=("year_month", "nunique"),
            max_company_count=("company_count", "max"),
            avg_company_count=("company_count", "mean"),
            sum_total_amount=("total_amount", "sum"),
        )
        .reset_index()
        .sort_values(["max_company_count", "avg_company_count"], ascending=[False, False])
    )
    overall.to_csv(OUT_DIR / "stock_overall.csv", index=False, encoding="utf-8-sig")
    overall.head(20).to_csv(OUT_DIR / "top10_overall.csv", index=False, encoding="utf-8-sig")

    # --- pivot table: company_count by month per stock ---
    pivot = monthly.pivot_table(
        index=["stock_id", "stock_code", "stock_name"],
        columns="year_month",
        values="company_count",
        fill_value=0,
    ).reset_index()
    pivot.to_csv(OUT_DIR / "company_count_by_month.csv", index=False, encoding="utf-8-sig")

    # --- month-over-month diff ---
    months = sorted(monthly["year_month"].unique())
    diffs: list[pd.DataFrame] = []
    # collapse to one (month, stock_id) row by summing company_count of any
    # duplicate stock_ids (rare; arises when target_name differs slightly).
    monthly_uniq = monthly.groupby(["year_month", "stock_id"], as_index=False)[
        "company_count"
    ].max()
    for i in range(1, len(months)):
        prev_m, cur_m = months[i - 1], months[i]
        prev = monthly_uniq[monthly_uniq["year_month"] == prev_m].set_index("stock_id")["company_count"]
        cur = monthly_uniq[monthly_uniq["year_month"] == cur_m].set_index("stock_id")["company_count"]
        all_ids = prev.index.union(cur.index)
        d = pd.DataFrame(
            {
                "stock_id": all_ids,
                "prev_month": prev_m,
                "curr_month": cur_m,
                "prev_count": prev.reindex(all_ids, fill_value=0).values,
                "curr_count": cur.reindex(all_ids, fill_value=0).values,
            }
        )
        d["delta"] = d["curr_count"] - d["prev_count"]
        diffs.append(d)
    diff_df = pd.concat(diffs, ignore_index=True) if diffs else pd.DataFrame()
    # attach name (take the first non-empty pair per stock_id)
    name_lookup = (
        monthly.sort_values("year_month")
        .drop_duplicates("stock_id", keep="last")
        .set_index("stock_id")[["stock_code", "stock_name"]]
    )
    diff_df = diff_df.merge(name_lookup, left_on="stock_id", right_index=True, how="left")
    diff_df.to_csv(OUT_DIR / "month_diff.csv", index=False, encoding="utf-8-sig")

    # --- synchronized buy/sell within half year ---
    # A "synchronized buy" month for a stock = delta >= +3 (>=3 more companies)
    # A "synchronized sell" month = delta <= -3
    sync_buys = diff_df[diff_df["delta"] >= 3].sort_values("delta", ascending=False)
    sync_sells = diff_df[diff_df["delta"] <= -3].sort_values("delta")
    sync_buys.to_csv(OUT_DIR / "synchronized_buys.csv", index=False, encoding="utf-8-sig")
    sync_sells.to_csv(OUT_DIR / "synchronized_sells.csv", index=False, encoding="utf-8-sig")

    # --- summary print ---
    print("\n=== Top 10 stocks by max # of fund companies holding (half year) ===")
    top10 = overall.head(10)
    for _, r in top10.iterrows():
        print(f"  {r['stock_code']:>16}  {r['stock_name'][:30]:30}  "
              f"max_co={int(r['max_company_count'])} avg_co={r['avg_company_count']:.1f} "
              f"months={int(r['months_present'])}")

    print("\n=== Top 10 per latest month ({}) ===".format(months[-1]))
    latest = top_by_month[top_by_month["year_month"] == months[-1]].head(10)
    for _, r in latest.iterrows():
        print(f"  {r['stock_code']:>16}  {r['stock_name'][:30]:30}  co={int(r['company_count'])} funds={int(r['fund_count'])}")

    print("\n=== Synchronized BUYS (delta >= +3) — top 15 ===")
    for _, r in sync_buys.head(15).iterrows():
        print(f"  {r['curr_month']}  {r['stock_code']:>16}  {r['stock_name'][:30]:30}  {int(r['prev_count'])} -> {int(r['curr_count'])} (delta {int(r['delta']):+d})")

    print("\n=== Synchronized SELLS (delta <= -3) — top 15 ===")
    for _, r in sync_sells.head(15).iterrows():
        print(f"  {r['curr_month']}  {r['stock_code']:>16}  {r['stock_name'][:30]:30}  {int(r['prev_count'])} -> {int(r['curr_count'])} (delta {int(r['delta']):+d})")


if __name__ == "__main__":
    main()
