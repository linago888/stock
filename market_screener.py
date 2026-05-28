from __future__ import annotations

import argparse
import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

import pandas as pd

from stock_picker import (
    PickResult,
    fetch_yahoo_prices,
    load_financials,
    load_price_csv,
    score_financials,
    score_technicals,
)


LISTED_COMPANY_URL = "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"
OTC_COMPANY_URL = "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv"
DEFAULT_UNIVERSE_PATH = Path("data/universe.csv")
DEFAULT_PRICE_CACHE_DIR = Path("data/market_prices")


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    name: str
    market: str
    industry: str = ""

    @property
    def yahoo_symbol(self) -> str:
        suffix = ".TW" if self.market == "TWSE" else ".TWO"
        return f"{self.symbol}{suffix}"


def read_remote_csv(url: str, timeout: int = 30) -> list[dict[str, str]]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 stock-picker/1.0"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            text = raw.decode(encoding)
            return list(csv.DictReader(io.StringIO(text)))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", raw, 0, 1, f"Cannot decode {url}")


def first_value(row: dict[str, str], candidates: Iterable[str]) -> str:
    for candidate in candidates:
        if candidate in row and str(row[candidate]).strip():
            return str(row[candidate]).strip()
    for key, value in row.items():
        for candidate in candidates:
            if candidate in key and str(value).strip():
                return str(value).strip()
    return ""


def is_common_stock(symbol: str) -> bool:
    return symbol.isdigit() and len(symbol) == 4 and not symbol.startswith("00")


def fetch_market_universe(include_listed: bool = True, include_otc: bool = True) -> list[StockInfo]:
    sources: list[tuple[str, str]] = []
    if include_listed:
        sources.append(("TWSE", LISTED_COMPANY_URL))
    if include_otc:
        sources.append(("TPEx", OTC_COMPANY_URL))

    stocks: list[StockInfo] = []
    for market, url in sources:
        for row in read_remote_csv(url):
            symbol = first_value(row, ["公司代號", "證券代號", "股票代號"])
            name = first_value(row, ["公司簡稱", "公司名稱", "證券名稱"])
            industry = first_value(row, ["產業別"])
            if is_common_stock(symbol):
                stocks.append(StockInfo(symbol=symbol, name=name, market=market, industry=industry))
    return sorted({stock.symbol: stock for stock in stocks}.values(), key=lambda item: item.symbol)


def save_universe(stocks: list[StockInfo], path: Path = DEFAULT_UNIVERSE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "name", "market", "industry", "yahoo_symbol"])
        writer.writeheader()
        for stock in stocks:
            writer.writerow(
                {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "market": stock.market,
                    "industry": stock.industry,
                    "yahoo_symbol": stock.yahoo_symbol,
                }
            )


def load_universe(path: Path = DEFAULT_UNIVERSE_PATH) -> list[StockInfo]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    stocks = [
        StockInfo(
            symbol=str(row.get("symbol", "")).strip(),
            name=str(row.get("name", "")).strip(),
            market=str(row.get("market", "")).strip() or "TWSE",
            industry=str(row.get("industry", "")).strip(),
        )
        for row in rows
        if str(row.get("symbol", "")).strip()
    ]
    return stocks


def ensure_universe(path: Path = DEFAULT_UNIVERSE_PATH, refresh: bool = False) -> list[StockInfo]:
    if refresh or not path.exists():
        stocks = fetch_market_universe()
        save_universe(stocks, path)
        return stocks
    return load_universe(path)


def price_cache_path(stock: StockInfo, cache_dir: Path = DEFAULT_PRICE_CACHE_DIR) -> Path:
    return cache_dir / f"{stock.yahoo_symbol}.csv"


def fetch_prices_cached(
    stock: StockInfo,
    cache_dir: Path = DEFAULT_PRICE_CACHE_DIR,
    range_days: str = "1y",
    refresh: bool = True,
) -> pd.DataFrame:
    path = price_cache_path(stock, cache_dir)
    if path.exists() and not refresh:
        return load_price_csv(path)

    prices = fetch_yahoo_prices(stock.yahoo_symbol, range_days=range_days)
    cache_dir.mkdir(parents=True, exist_ok=True)
    prices.to_csv(path, index=False, encoding="utf-8-sig")
    return prices


def score_one_stock(
    stock: StockInfo,
    financials_by_symbol: dict[str, dict[str, str]],
    cache_dir: Path,
    range_days: str,
    refresh_prices: bool,
) -> tuple[PickResult | None, str | None]:
    try:
        prices = fetch_prices_cached(stock, cache_dir=cache_dir, range_days=range_days, refresh=refresh_prices)
        result = score_technicals(stock.yahoo_symbol, prices, name=stock.name)
        financials = financials_by_symbol.get(stock.symbol) or financials_by_symbol.get(stock.yahoo_symbol)
        score_financials(result, financials)
        return result, None
    except Exception as exc:
        return None, f"{stock.symbol} {stock.name}: {exc}"


def screen_market(
    universe_path: Path = DEFAULT_UNIVERSE_PATH,
    cache_dir: Path = DEFAULT_PRICE_CACHE_DIR,
    financials_path: Path | None = Path("data/financials.csv"),
    range_days: str = "1y",
    min_score: float = 55,
    top: int = 20,
    refresh_universe: bool = False,
    refresh_prices: bool = True,
    max_symbols: int | None = None,
    workers: int = 6,
    progress_callback: object | None = None,
) -> dict[str, object]:
    stocks = ensure_universe(universe_path, refresh=refresh_universe)
    if max_symbols:
        stocks = stocks[:max_symbols]
    financials_by_symbol = load_financials(financials_path) if financials_path else {}

    started = time.time()
    results: list[PickResult] = []
    errors: list[str] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(
                score_one_stock,
                stock,
                financials_by_symbol,
                cache_dir,
                range_days,
                refresh_prices,
            )
            for stock in stocks
        ]
        for future in as_completed(futures):
            result, error = future.result()
            completed += 1
            if result is not None:
                results.append(result)
            if error is not None:
                errors.append(error)
            if progress_callback is not None:
                progress_callback(
                    {
                        "completed": completed,
                        "total": len(stocks),
                        "scored": len(results),
                        "errors": len(errors),
                    }
                )

    ranked = sorted(results, key=lambda item: item.score, reverse=True)
    picks = [result for result in ranked if result.score >= min_score][:top]
    return {
        "results": picks,
        "all_results": ranked,
        "errors": errors,
        "meta": {
            "symbols_requested": len(stocks),
            "symbols_scored": len(results),
            "errors": len(errors),
            "range": range_days,
            "min_score": min_score,
            "top": top,
            "elapsed_seconds": round(time.time() - started, 2),
            "universe_path": str(universe_path),
            "cache_dir": str(cache_dir),
        },
    }


def print_market_results(payload: dict[str, object]) -> None:
    meta = payload["meta"]
    print(
        f"已分析 {meta['symbols_scored']}/{meta['symbols_requested']} 檔，"
        f"達標 {len(payload['results'])} 檔，耗時 {meta['elapsed_seconds']} 秒"
    )
    for index, result in enumerate(payload["results"], start=1):
        print(f"{index}. {result.symbol} {result.name} | {result.score:.1f} | 收盤 {result.price:.2f}")
        for reason in result.reasons[:5]:
            print(f"   - {reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="每日全台股起漲股票篩選")
    parser.add_argument("--refresh-universe", action="store_true", help="重新下載上市/上櫃股票池")
    parser.add_argument("--no-refresh-prices", action="store_true", help="只使用本機價格快取")
    parser.add_argument("--range", default="1y", help="Yahoo Finance 日 K 區間，例如 6mo, 1y, 2y")
    parser.add_argument("--min-score", type=float, default=55)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--max-symbols", type=int, help="只分析前 N 檔，供測試使用")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--financials", default="data/financials.csv")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = screen_market(
        financials_path=Path(args.financials) if args.financials else None,
        range_days=args.range,
        min_score=args.min_score,
        top=args.top,
        refresh_universe=args.refresh_universe,
        refresh_prices=not args.no_refresh_prices,
        max_symbols=args.max_symbols,
        workers=args.workers,
    )
    print_market_results(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
