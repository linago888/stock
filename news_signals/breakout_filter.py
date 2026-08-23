"""外資+均線糾結+帶量長紅突破三步驟濾網。

Step 1  外資近 5 日累積買超（含外資自營商），排序取前列
Step 2  MA5 / MA10 / MA20 糾結：(max-min)/close × 100 <= threshold
Step 3  帶量長紅突破：
        - 收盤 > 開盤（紅 K）
        - 實體 %  >= body_min
        - 量比 = 當日量 / 前 20 日均量 >= 1.5
        - 突破：收盤 > max(MA5, MA10, MA20) 且 > 過去 20 日高（不含當日）

輸出 data/breakout_scan.json，供網站 /signals.html 使用。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
PRICE_DIR = ROOT / "data" / "market_prices"
UNIVERSE = ROOT / "data" / "universe.csv"
OUT = ROOT / "data" / "breakout_scan.json"

TWSE_FUND_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"


def fetch_foreign_net(date: dt.date) -> dict[str, int]:
    """Return {stock_id: foreign_net_shares} for a single trading day.
    Returns empty dict when the date has no data (weekend/holiday)."""
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    r = session.get(
        TWSE_FUND_URL,
        params={
            "date": date.strftime("%Y%m%d"),
            "selectType": "ALL",
            "response": "json",
        },
        timeout=30,
    )
    r.encoding = "utf-8"
    try:
        payload = r.json()
    except Exception:
        return {}
    if payload.get("stat") != "OK":
        return {}
    out: dict[str, int] = {}
    for row in payload.get("data", []):
        if len(row) < 8:
            continue
        code = row[0].strip()
        try:
            # 外陸資買賣超 + 外資自營商買賣超
            main = int(row[4].replace(",", "") or 0)
            prop = int(row[7].replace(",", "") or 0)
            out[code] = main + prop
        except (ValueError, IndexError):
            continue
    return out


def last_n_trading_days(n: int, session=None) -> list[dt.date]:
    """Return the last n trading dates by walking back from today,
    querying TWSE — a date with no data is a non-trading day."""
    today = dt.date.today()
    dates: list[dt.date] = []
    d = today
    tried = 0
    while len(dates) < n and tried < n * 3:
        tried += 1
        data = fetch_foreign_net(d)
        if data:
            dates.append(d)
        else:
            pass
        d = d - dt.timedelta(days=1)
        time.sleep(0.3)
    return list(reversed(dates))


def compute_foreign_5d(days: list[dt.date]) -> dict[str, dict]:
    """Return {stock_id: {net_5d, days_bought, day_series}}."""
    per_day: dict[str, dict[dt.date, int]] = {}
    for d in days:
        data = fetch_foreign_net(d)
        for code, net in data.items():
            per_day.setdefault(code, {})[d] = net
        time.sleep(0.3)
    out: dict[str, dict] = {}
    for code, day_map in per_day.items():
        series = [day_map.get(d, 0) for d in days]
        net_5d = sum(series)
        days_bought = sum(1 for v in series if v > 0)
        # longest tail of consecutive positive days
        tail = 0
        for v in reversed(series):
            if v > 0:
                tail += 1
            else:
                break
        out[code] = {
            "net_5d_shares": net_5d,
            "net_5d_lots": round(net_5d / 1000, 1),
            "days_bought": days_bought,
            "consecutive_tail": tail,
            "series_lots": [round(v / 1000, 1) for v in series],
        }
    return out


def compute_technical(price_path: Path) -> dict | None:
    try:
        df = pd.read_csv(price_path)
    except Exception:
        return None
    if len(df) < 25:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume"]).sort_values("date").reset_index(drop=True)
    if len(df) < 25:
        return None
    last = df.iloc[-1]
    win20 = df.tail(20)
    prev20 = df.iloc[-21:-1] if len(df) >= 21 else df.tail(20)
    ma5 = float(df["close"].tail(5).mean())
    ma10 = float(df["close"].tail(10).mean())
    ma20 = float(df["close"].tail(20).mean())
    close = float(last["close"])
    open_ = float(last["open"])
    high = float(last["high"])
    low = float(last["low"])
    vol = float(last["volume"])
    ma_max = max(ma5, ma10, ma20)
    ma_min = min(ma5, ma10, ma20)
    ma_spread_pct = (ma_max - ma_min) / close * 100 if close else 999
    body_pct = (close - open_) / open_ * 100 if open_ else 0
    is_red = close > open_
    prev_20d_high = float(prev20["close"].max()) if len(prev20) else 0
    range_pct = (prev20["close"].max() - prev20["close"].min()) / prev20["close"].mean() * 100 if len(prev20) else 999
    vol20 = float(prev20["volume"].mean()) if len(prev20) else 1
    volume_ratio = vol / vol20 if vol20 else 0
    breakout = close > ma_max and close > prev_20d_high

    return {
        "last_date": last["date"].strftime("%Y-%m-%d"),
        "close": round(close, 2),
        "open": round(open_, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma_spread_pct": round(ma_spread_pct, 2),
        "body_pct": round(body_pct, 2),
        "is_red": bool(is_red),
        "prev_20d_high": round(prev_20d_high, 2),
        "range_20d_pct": round(range_pct, 2),
        "volume_ratio": round(volume_ratio, 2),
        "breakout": bool(breakout),
    }


def load_universe_names() -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    if not UNIVERSE.exists():
        return mapping
    with UNIVERSE.open("r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sym = (row.get("yahoo_symbol") or row.get("symbol") or "").strip()
            name = (row.get("name") or "").strip()
            if sym:
                code = sym.split(".")[0]
                market = "otc" if sym.endswith(".TWO") else "sii"
                mapping[code] = (name, market)
    return mapping


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--foreign-days", type=int, default=5,
                    help="外資累積天數（預設 5）")
    ap.add_argument("--top-foreign", type=int, default=200,
                    help="外資買超金額前 N 檔進入 Step 2/3（預設 200）")
    ap.add_argument("--ma-spread-max", type=float, default=2.5,
                    help="MA5/10/20 糾結門檻 %（預設 2.5）")
    ap.add_argument("--body-min", type=float, default=2.0,
                    help="長紅實體 %（預設 2.0）")
    ap.add_argument("--vol-ratio-min", type=float, default=1.5,
                    help="量比門檻（預設 1.5）")
    ap.add_argument("--range-max", type=float, default=15.0,
                    help="整理區間 20 日 range% 上限（預設 15）")
    ap.add_argument("--limit", type=int, default=50,
                    help="輸出前 N 檔（預設 50）")
    args = ap.parse_args()

    print(f"抓取最近 {args.foreign_days} 個交易日的外資買賣超…")
    trading_days = last_n_trading_days(args.foreign_days)
    if not trading_days:
        print("無法取得 TWSE 資料", file=sys.stderr)
        return 1
    print(f"  交易日：{[d.isoformat() for d in trading_days]}")

    foreign = compute_foreign_5d(trading_days)
    print(f"  收集到 {len(foreign)} 檔 TWSE 上市股外資資料")

    # take top-N by foreign net for step 2/3
    ranked = sorted(foreign.items(), key=lambda kv: kv[1]["net_5d_shares"], reverse=True)
    top = [(code, info) for code, info in ranked if info["net_5d_shares"] > 0][: args.top_foreign]
    print(f"  外資淨買超 > 0 的前 {len(top)} 檔進入技術面篩選")

    universe = load_universe_names()
    matched = []
    for code, info in top:
        # find matching price file (sii=TW, otc=TWO)
        # fund T86 only covers 上市 (sii). All codes here are TW.
        path = PRICE_DIR / f"{code}.TW.csv"
        if not path.exists():
            path = PRICE_DIR / f"{code}.TWO.csv"
        if not path.exists():
            continue
        tech = compute_technical(path)
        if not tech:
            continue
        # Step gate 判定
        step2_pass = tech["ma_spread_pct"] <= args.ma_spread_max and tech["range_20d_pct"] <= args.range_max
        step3_pass = (
            tech["is_red"]
            and tech["body_pct"] >= args.body_min
            and tech["volume_ratio"] >= args.vol_ratio_min
            and tech["breakout"]
        )

        # score
        # Step 1: normalize by top foreign net (log scale for outliers)
        foreign_score = min(30, info["net_5d_lots"] / max(1, ranked[0][1]["net_5d_lots"] / 1000) * 30)
        step2_score = max(0, (args.ma_spread_max - tech["ma_spread_pct"]) * 4)  # tighter = higher
        step3_score = 0
        if step3_pass:
            step3_score = 20 + min(15, (tech["volume_ratio"] - 1.5) * 5) + min(10, tech["body_pct"] * 2)
        elif tech["is_red"] and tech["volume_ratio"] >= 1.2:
            step3_score = 5  # partial credit for volume + red only

        total = round(foreign_score + step2_score + step3_score, 2)
        info_name, market = universe.get(code, ("", "sii"))
        matched.append({
            "co_id": code,
            "name": info_name or code,
            "market": market,
            "foreign_5d_lots": info["net_5d_lots"],
            "foreign_days_bought": info["days_bought"],
            "foreign_consec_tail": info["consecutive_tail"],
            "foreign_series_lots": info["series_lots"],
            **tech,
            "step2_pass": bool(step2_pass),
            "step3_pass": bool(step3_pass),
            "steps_passed": sum([True, step2_pass, step3_pass]),  # Step 1 by inclusion
            "foreign_score": round(foreign_score, 2),
            "step2_score": round(step2_score, 2),
            "step3_score": round(step3_score, 2),
            "score": total,
        })

    # sort: 3 steps passed first, then score
    matched.sort(key=lambda r: (-r["steps_passed"], -r["score"]))

    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "trading_days": [d.isoformat() for d in trading_days],
        "params": {
            "foreign_days": args.foreign_days,
            "top_foreign": args.top_foreign,
            "ma_spread_max": args.ma_spread_max,
            "body_min": args.body_min,
            "vol_ratio_min": args.vol_ratio_min,
            "range_max": args.range_max,
        },
        "total_scanned": len(top),
        "matched_count": len(matched),
        "three_step_count": sum(1 for m in matched if m["steps_passed"] == 3),
        "items": matched[: args.limit],
    }
    def _json_safe(o):
        if hasattr(o, "item"):
            return o.item()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_safe), encoding="utf-8")
    print(f"\n寫入 {OUT}")
    print(f"符合 3 步驟：{payload['three_step_count']} 檔 / 前 {len(matched)} 檔進入排序")
    print("\n== Top 15 ==")
    for i, m in enumerate(matched[:15], 1):
        stars = "✓" * m["steps_passed"] + "·" * (3 - m["steps_passed"])
        print(f"  {i:>2}. {m['co_id']:>6} {m['name'][:10]:10} "
              f"score={m['score']:>6}  外資5D={m['foreign_5d_lots']:>7} 張  "
              f"MA差{m['ma_spread_pct']:.1f}%  量比{m['volume_ratio']:.1f}× "
              f"實體{m['body_pct']:+.1f}%  突破={'✓' if m['breakout'] else '·'}  [{stars}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
