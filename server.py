from __future__ import annotations

import json
import math
import csv
import re
import traceback
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

import pandas as pd

from market_screener import (
    DEFAULT_PRICE_CACHE_DIR,
    DEFAULT_UNIVERSE_PATH,
    ensure_universe,
    fetch_market_universe,
    load_universe,
    price_cache_path,
    save_universe,
    screen_market,
)
from stock_picker import (
    PickResult,
    add_indicators,
    fetch_yahoo_prices,
    load_price_csv,
    load_financials,
    normalize_tw_symbol,
    score_financials,
    score_technicals,
)
import sitca_web


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
ERROR_LOG = ROOT / "server_error.log"
JOBS: dict[str, dict[str, object]] = {}
KLINE_SIGNAL_KEYS = [
    "above_ma20",
    "above_ma60",
    "ma20_slope_up",
    "near_breakout_20d",
    "breakout_20d",
    "volume_expansion",
    "strong_volume",
    "kd_golden",
    "kd_bullish",
    "macd_turn_positive",
    "macd_rising",
    "rsi_healthy",
    "not_extended",
]


def log_error(exc: BaseException) -> None:
    with ERROR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(traceback.format_exc())
        handle.write("\n")


def clean_number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 4)


def result_to_dict(result: PickResult, chart: list[dict[str, object]]) -> dict[str, object]:
    return {
        "symbol": result.symbol,
        "name": result.name,
        "score": round(result.score, 2),
        "price": clean_number(result.price),
        "stop_loss": clean_number(result.stop_loss),
        "reasons": result.reasons,
        "warnings": result.warnings,
        "metrics": {key: clean_number(value) for key, value in result.metrics.items()},
        "signals": result.signals,
        "buy_plan": result.buy_plan,
        "chart": chart,
    }


def market_result_to_dict(result: PickResult) -> dict[str, object]:
    chart: list[dict[str, object]] = []
    cache_path = DEFAULT_PRICE_CACHE_DIR / f"{result.symbol}.csv"
    if cache_path.exists():
        try:
            chart = chart_points(pd.read_csv(cache_path))
        except Exception:
            chart = []
    return result_to_dict(result, chart)


def chart_points(frame: pd.DataFrame, limit: int = 90) -> list[dict[str, object]]:
    data = add_indicators(frame).tail(limit)
    points: list[dict[str, object]] = []
    for _, row in data.iterrows():
        points.append(
            {
                "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
                "open": clean_number(row.get("open")),
                "high": clean_number(row.get("high")),
                "low": clean_number(row.get("low")),
                "close": clean_number(row["close"]),
                "ma20": clean_number(row.get("ma20")),
                "ma60": clean_number(row.get("ma60")),
                "volume": clean_number(row.get("volume")),
                "k": clean_number(row.get("k")),
                "d": clean_number(row.get("d")),
                "macd": clean_number(row.get("macd")),
                "macd_signal": clean_number(row.get("macd_signal")),
                "macd_hist": clean_number(row.get("macd_hist")),
            }
        )
    return points


def price_chart(symbol: str, limit: int = 120) -> dict[str, object]:
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        raise ValueError("缺少股票代號")
    path = DEFAULT_PRICE_CACHE_DIR / f"{clean_symbol}.csv"
    if not path.exists() and "." not in clean_symbol:
        tw_path = DEFAULT_PRICE_CACHE_DIR / f"{clean_symbol}.TW.csv"
        two_path = DEFAULT_PRICE_CACHE_DIR / f"{clean_symbol}.TWO.csv"
        path = tw_path if tw_path.exists() else two_path
    if not path.exists():
        raise FileNotFoundError(f"尚未蒐集 {symbol} 的 K 線快取")

    stock = None
    for item in load_universe(DEFAULT_UNIVERSE_PATH):
        if item.yahoo_symbol == path.stem:
            stock = item
            break
    prices = load_price_csv(path)
    result = score_technicals(path.stem, prices, name=stock.name if stock else "")
    return {
        "symbol": path.stem,
        "name": stock.name if stock else "",
        "chart": chart_points(prices, limit=limit),
        "buy_plan": result.buy_plan,
    }


def stock_identity(symbol: str) -> tuple[str, str, str]:
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        raise ValueError("symbol is required")
    if "." not in clean_symbol:
        tw_symbol = f"{clean_symbol}.TW"
        two_symbol = f"{clean_symbol}.TWO"
        if (DEFAULT_PRICE_CACHE_DIR / f"{tw_symbol}.csv").exists():
            clean_symbol = tw_symbol
        elif (DEFAULT_PRICE_CACHE_DIR / f"{two_symbol}.csv").exists():
            clean_symbol = two_symbol
    stock_name = ""
    for stock in load_universe(DEFAULT_UNIVERSE_PATH):
        if stock.yahoo_symbol == clean_symbol or stock.symbol == clean_symbol.split(".")[0]:
            stock_name = stock.name
            break
    code = clean_symbol.split(".")[0]
    return clean_symbol, code, stock_name


def extract_next_data(html: str) -> dict[str, object]:
    marker = "__NEXT_DATA__"
    idx = html.find(marker)
    if idx < 0:
        raise ValueError("source page has no embedded data")
    start = html.find("{", idx)
    if start < 0:
        raise ValueError("source page embedded data is invalid")
    payload, _ = json.JSONDecoder().raw_decode(html[start:])
    if not isinstance(payload, dict):
        raise ValueError("source page embedded data is invalid")
    return payload


def fetch_eps_estimate(symbol: str) -> dict[str, object]:
    clean_symbol, code, stock_name = stock_identity(symbol)
    year = time.localtime().tm_year
    cnyes_url = f"https://www.cnyes.com/twstock/{code}/research/finirating"
    query = f"{code} {stock_name} {year} 預估 EPS".strip()
    result: dict[str, object] = {
        "symbol": clean_symbol,
        "code": code,
        "name": stock_name,
        "year": year,
        "estimated_eps": None,
        "trailing_eps": None,
        "target_price_median": None,
        "rating_date": None,
        "source": "Cnyes",
        "source_url": cnyes_url,
        "search_urls": {
            "google": f"https://www.google.com/search?q={quote_plus(query)}",
            "bing": f"https://www.bing.com/search?q={quote_plus(query)}",
        },
        "note": "鉅亨頁面目前可抓到個股 EPS 與券商目標價資料；若要確認今年預估 EPS，請開啟即時搜尋連結核對法人報告或新聞來源。",
    }
    try:
        request = Request(cnyes_url, headers={"User-Agent": "Mozilla/5.0"})
        html = urlopen(request, timeout=15).read().decode("utf-8", "ignore")
        next_data = extract_next_data(html)
        page_props = next_data.get("props", {}).get("pageProps", {})
        if not isinstance(page_props, dict):
            return result
        profile = page_props.get("companyProfile", {})
        if isinstance(profile, dict):
            result["name"] = stock_name or str(profile.get("companyName") or "")
            result["trailing_eps"] = clean_number(profile.get("eps"))
        estimates = page_props.get("factSetEstimate", {})
        if isinstance(estimates, dict):
            medians = estimates.get("feMedian")
            dates = estimates.get("rateDate")
            if isinstance(medians, list) and medians:
                result["target_price_median"] = clean_number(medians[0])
            if isinstance(dates, list) and dates:
                try:
                    result["rating_date"] = time.strftime("%Y-%m-%d", time.localtime(int(dates[0])))
                except (TypeError, ValueError, OSError):
                    result["rating_date"] = None
    except Exception as exc:
        result["note"] = f"網路查詢暫時失敗：{exc}。可先開啟搜尋連結手動確認今年預估 EPS。"
    return result


def parse_symbols(raw: str) -> list[str]:
    symbols = [part.strip() for part in raw.replace("\n", ",").split(",")]
    return list(dict.fromkeys(symbol for symbol in symbols if symbol))


def selected_criteria(payload: dict[str, object]) -> list[str]:
    criteria = payload.get("criteria", [])
    if not isinstance(criteria, list):
        return KLINE_SIGNAL_KEYS
    return [str(item) for item in criteria if str(item)]


def matched_signal_keys(result: dict[str, object], criteria: list[str]) -> list[str]:
    signals = result.get("signals", {})
    if not isinstance(signals, dict):
        return []
    return [item for item in criteria if bool(signals.get(item))]


def annotate_matches(result: dict[str, object], criteria: list[str]) -> dict[str, object]:
    matched = matched_signal_keys(result, criteria)
    result["matched_criteria"] = matched
    result["matched_count"] = len(matched)
    return result


def sort_by_matched_count(results: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(
        results,
        key=lambda item: (-int(item.get("matched_count", 0) or 0), str(item.get("symbol", ""))),
    )


def screen_stocks(payload: dict[str, object]) -> dict[str, object]:
    symbols = parse_symbols(str(payload.get("symbols", "")))
    if not symbols:
        raise ValueError("請至少輸入一個股票代號")

    range_days = str(payload.get("range", "1y"))
    top = int(payload.get("top", 10))
    min_matches = int(payload.get("min_matches", 2))
    criteria = selected_criteria(payload)
    sleep = float(payload.get("sleep", 0))
    financials_path = ROOT / str(payload.get("financials", "data/financials.csv"))
    financials_by_symbol = load_financials(financials_path)

    results: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for symbol in symbols:
        try:
            if sleep > 0:
                import time

                time.sleep(sleep)
            prices = fetch_yahoo_prices(symbol, range_days=range_days)
            resolved_symbol = prices.attrs.get("resolved_symbol", normalize_tw_symbol(symbol))
            financials = financials_by_symbol.get(symbol.upper()) or financials_by_symbol.get(
                str(resolved_symbol).upper()
            )
            name = str(financials.get("name", "")) if financials else ""
            result = score_technicals(str(resolved_symbol), prices, name=name)
            score_financials(result, financials)
            results.append(annotate_matches(result_to_dict(result, chart_points(prices)), criteria))
        except Exception as exc:
            errors.append({"symbol": symbol, "message": str(exc)})

    ranked = sort_by_matched_count(results)
    filtered = [item for item in ranked if int(item["matched_count"]) >= min_matches][:top]
    return {
        "results": filtered,
        "all_results": ranked,
        "errors": errors,
        "meta": {
            "symbols_requested": len(symbols),
            "symbols_scored": len(results),
            "range": range_days,
            "min_matches": min_matches,
            "top": top,
            "criteria": criteria,
        },
    }


def universe_summary(refresh: bool = False) -> dict[str, object]:
    stocks = ensure_universe(DEFAULT_UNIVERSE_PATH, refresh=refresh)
    markets: dict[str, int] = {}
    for stock in stocks:
        markets[stock.market] = markets.get(stock.market, 0) + 1
    return {
        "count": len(stocks),
        "markets": markets,
        "path": str(DEFAULT_UNIVERSE_PATH),
        "updated": DEFAULT_UNIVERSE_PATH.stat().st_mtime if DEFAULT_UNIVERSE_PATH.exists() else None,
    }


def price_cache_status(limit: int = 200) -> dict[str, object]:
    stocks = load_universe(DEFAULT_UNIVERSE_PATH)
    stocks_by_yahoo = {stock.yahoo_symbol: stock for stock in stocks}
    rows: list[dict[str, object]] = []
    latest_dates: dict[str, int] = {}
    cached_files = sorted(DEFAULT_PRICE_CACHE_DIR.glob("*.csv")) if DEFAULT_PRICE_CACHE_DIR.exists() else []

    def latest_cached_row(path: Path) -> tuple[str, float | None, int]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            latest: dict[str, str] | None = None
            count = 0
            for row in reader:
                if row:
                    latest = row
                    count += 1
        if latest is None:
            return "", None, 0
        date_value = str(latest.get("date", "")).strip()
        close_value = clean_number(latest.get("close"))
        return date_value, close_value, count

    for path in cached_files:
        symbol = path.stem
        stock = stocks_by_yahoo.get(symbol)
        try:
            latest_date, latest_close, row_count = latest_cached_row(path)
            if not latest_date:
                continue
            latest_dates[latest_date] = latest_dates.get(latest_date, 0) + 1
            rows.append(
                {
                    "symbol": symbol,
                    "name": stock.name if stock else "",
                    "market": stock.market if stock else "",
                    "date": latest_date,
                    "close": latest_close,
                    "rows": row_count,
                    "path": str(path),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "symbol": symbol,
                    "name": stock.name if stock else "",
                    "market": stock.market if stock else "",
                    "date": "",
                    "close": None,
                    "rows": 0,
                    "error": str(exc),
                    "path": str(path),
                }
            )

    rows = sorted(rows, key=lambda item: (str(item.get("date", "")), str(item.get("symbol", ""))), reverse=True)
    latest_date = rows[0]["date"] if rows else None
    return {
        "total_universe": len(stocks),
        "cached_count": len([row for row in rows if row.get("date")]),
        "cache_dir": str(DEFAULT_PRICE_CACHE_DIR),
        "latest_date": latest_date,
        "latest_dates": dict(sorted(latest_dates.items(), reverse=True)[:10]),
        "rows": rows[:limit],
        "row_count": len(rows),
        "limit": limit,
    }


def refresh_universe() -> dict[str, object]:
    stocks = fetch_market_universe()
    save_universe(stocks, DEFAULT_UNIVERSE_PATH)
    return universe_summary(refresh=False)


def screen_whole_market(payload: dict[str, object]) -> dict[str, object]:
    max_symbols_raw = payload.get("max_symbols")
    max_symbols = int(max_symbols_raw) if max_symbols_raw not in (None, "", 0, "0") else None
    criteria = selected_criteria(payload)
    min_matches = int(payload.get("min_matches", 2))
    data = screen_market(
        range_days=str(payload.get("range", "1y")),
        min_score=-999,
        top=99999,
        refresh_universe=bool(payload.get("refresh_universe", False)),
        refresh_prices=bool(payload.get("refresh_prices", True)),
        max_symbols=max_symbols,
        workers=int(payload.get("workers", 6)),
        progress_callback=payload.get("_progress_callback"),
    )
    ranked = [annotate_matches(market_result_to_dict(result), criteria) for result in data["all_results"]]
    top = int(payload.get("top", 20))
    ranked = sort_by_matched_count(ranked)
    filtered = [item for item in ranked if int(item["matched_count"]) >= min_matches][:top]
    data["meta"]["min_matches"] = min_matches
    data["meta"]["top"] = top
    data["meta"]["criteria"] = criteria
    return {
        "results": filtered,
        "all_results": ranked,
        "errors": [{"symbol": "", "message": message} for message in data["errors"][:80]],
        "meta": data["meta"],
    }


def create_screen_job(payload: dict[str, object]) -> dict[str, object]:
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id,
        "status": "running",
        "created_at": time.time(),
        "completed": 0,
        "total": 0,
        "scored": 0,
        "errors": 0,
        "percent": 0,
        "result": None,
        "error": None,
    }

    def update_progress(progress: dict[str, object]) -> None:
        job = JOBS[job_id]
        total = int(progress.get("total", 0) or 0)
        completed = int(progress.get("completed", 0) or 0)
        job.update(progress)
        job["percent"] = round(completed / total * 100, 1) if total else 0

    def worker() -> None:
        try:
            payload["_progress_callback"] = update_progress
            result = screen_whole_market(payload)
            job = JOBS[job_id]
            job["status"] = "done"
            job["result"] = result
            job["percent"] = 100
        except Exception as exc:
            log_error(exc)
            job = JOBS[job_id]
            job["status"] = "error"
            job["error"] = str(exc)

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


def job_status(job_id: str) -> dict[str, object]:
    job = JOBS.get(job_id)
    if not job:
        raise KeyError("找不到工作")
    return job


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            query = urlparse(self.path).query
            if path == "/health":
                self.send_json({"ok": True})
                return
            if path == "/api/universe":
                self.send_json(universe_summary(refresh=False))
                return
            if path == "/api/price-status":
                from urllib.parse import parse_qs

                limit_values = parse_qs(query).get("limit", ["200"])
                self.send_json(price_cache_status(limit=int(limit_values[0])))
                return
            if path == "/api/price-chart":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                symbol = params.get("symbol", [""])[0]
                limit = int(params.get("limit", ["120"])[0])
                self.send_json(price_chart(symbol=symbol, limit=limit))
                return
            if path == "/api/eps-estimate":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                symbol = params.get("symbol", [""])[0]
                self.send_json(fetch_eps_estimate(symbol=symbol))
                return
            if path == "/api/job-status":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                self.send_json(job_status(params.get("id", [""])[0]))
                return
            if path == "/api/sitca/status":
                self.send_json(sitca_web.status())
                return
            if path == "/api/sitca/months":
                self.send_json({"months": sitca_web.list_months()})
                return
            if path == "/api/sitca/companies":
                self.send_json({"companies": sitca_web.list_companies()})
                return
            if path == "/api/sitca/top-stocks":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                month = params.get("month", [""])[0] or None
                limit = int(params.get("limit", ["10"])[0])
                self.send_json({"items": sitca_web.top_stocks(month=month, limit=limit)})
                return
            if path == "/api/sitca/matrix":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                limit = int(params.get("limit", ["30"])[0])
                self.send_json(sitca_web.month_matrix(limit=limit))
                return
            if path == "/api/sitca/sync":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                direction = params.get("direction", ["buy"])[0]
                min_delta = int(params.get("min_delta", ["3"])[0])
                limit = int(params.get("limit", ["30"])[0])
                self.send_json(
                    {
                        "direction": direction,
                        "min_delta": min_delta,
                        "items": sitca_web.synchronized_moves(
                            direction=direction, min_delta=min_delta, limit=limit
                        ),
                    }
                )
                return
            if path == "/api/sitca/stock":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                stock_id = params.get("id", [""])[0]
                self.send_json(sitca_web.stock_detail(stock_id))
                return
            if path == "/api/sitca/company-changes":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                self.send_json(
                    sitca_web.company_changes(
                        company_id=params.get("company", [""])[0],
                        curr_month=params.get("curr", [""])[0],
                        prev_month=params.get("prev", [""])[0],
                    )
                )
                return
            if path == "/api/sitca/scrape-status":
                from urllib.parse import parse_qs

                params = parse_qs(query)
                self.send_json(sitca_web.scrape_job_status(params.get("id", [""])[0]))
                return
            if path == "/":
                self.path = "/index.html"
            super().do_GET()
        except Exception as exc:
            log_error(exc)
            self.send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path not in {
                "/api/screen",
                "/api/screen-market",
                "/api/screen-market-job",
                "/api/refresh-universe",
                "/api/sitca/scrape",
            }:
                self.send_error(404, "Not found")
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/api/screen":
                self.send_json(screen_stocks(payload))
            elif path == "/api/screen-market":
                self.send_json(screen_whole_market(payload))
            elif path == "/api/screen-market-job":
                self.send_json(create_screen_job(payload))
            elif path == "/api/sitca/scrape":
                months = str(payload.get("months", "") or "")
                sleep = float(payload.get("sleep", 0.6))
                force = bool(payload.get("force", False))
                self.send_json(
                    sitca_web.start_scrape_job(months=months, sleep=sleep, force=force)
                )
            else:
                self.send_json(refresh_universe())
        except Exception as exc:
            log_error(exc)
            self.send_json({"error": str(exc)}, status=400)

    def send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    args = SimpleNamespace(host="127.0.0.1", port=8000)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"台股篩選網頁已啟動: http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
