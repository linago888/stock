from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


PRICE_COLUMNS = {
    "date": "date",
    "datetime": "date",
    "time": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj close": "close",
    "volume": "volume",
    "成交股數": "volume",
    "開盤價": "open",
    "最高價": "high",
    "最低價": "low",
    "收盤價": "close",
    "日期": "date",
}


@dataclass
class PickResult:
    symbol: str
    name: str = ""
    score: float = 0.0
    price: float = math.nan
    stop_loss: float = math.nan
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    signals: dict[str, bool] = field(default_factory=dict)
    buy_plan: dict[str, object] = field(default_factory=dict)


def normalize_tw_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        return symbol
    if "." in symbol:
        return symbol
    if symbol.isdigit():
        return f"{symbol}.TW"
    return symbol


def yahoo_chart_url(symbol: str, range_days: str = "1y", interval: str = "1d") -> str:
    query = urllib.parse.urlencode(
        {
            "range": range_days,
            "interval": interval,
            "includeAdjustedClose": "true",
        }
    )
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"


def fetch_yahoo_prices(symbol: str, range_days: str = "1y", timeout: int = 20) -> pd.DataFrame:
    """Fetch OHLCV from Yahoo's public chart endpoint without extra dependencies."""
    last_error: Exception | None = None
    candidates = [normalize_tw_symbol(symbol)]
    if symbol.strip().isdigit():
        candidates.append(f"{symbol.strip()}.TWO")

    for candidate in dict.fromkeys(candidates):
        try:
            request = urllib.request.Request(
                yahoo_chart_url(candidate, range_days=range_days),
                headers={"User-Agent": "Mozilla/5.0 stock-picker/1.0"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            chart = payload["chart"]["result"][0]
            timestamps = chart["timestamp"]
            quote = chart["indicators"]["quote"][0]
            frame = pd.DataFrame(
                {
                    "date": pd.to_datetime(timestamps, unit="s").date,
                    "open": quote["open"],
                    "high": quote["high"],
                    "low": quote["low"],
                    "close": quote["close"],
                    "volume": quote["volume"],
                }
            )
            frame = frame.dropna(subset=["open", "high", "low", "close"])
            if not frame.empty:
                frame.attrs["resolved_symbol"] = candidate
                return normalize_price_frame(frame)
        except (urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc

    raise RuntimeError(f"無法抓取 {symbol} 的 Yahoo 價格資料: {last_error}")


def normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    renamed: dict[str, str] = {}
    for column in frame.columns:
        key = str(column).strip().lower()
        renamed[column] = PRICE_COLUMNS.get(key, key)
    frame = frame.rename(columns=renamed)
    missing = {"date", "open", "high", "low", "close", "volume"} - set(frame.columns)
    if missing:
        raise ValueError(f"價格 CSV 缺少欄位: {', '.join(sorted(missing))}")

    out = frame[["date", "open", "high", "low", "close", "volume"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    for column in ["open", "high", "low", "close", "volume"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=["date", "open", "high", "low", "close"])
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return out.reset_index(drop=True)


def load_price_csv(path: Path) -> pd.DataFrame:
    return normalize_price_frame(pd.read_csv(path))


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ma20"] = close.rolling(20).mean()
    df["ma60"] = close.rolling(60).mean()
    df["ma20_slope"] = df["ma20"].diff(5) / df["ma20"].shift(5) * 100
    df["vol20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol20"]

    low_min = low.rolling(9).min()
    high_max = high.rolling(9).max()
    raw_k = (close - low_min) / (high_max - low_min).replace(0, np.nan) * 100
    df["k"] = raw_k.ewm(alpha=1 / 3, adjust=False).mean()
    df["d"] = df["k"].ewm(alpha=1 / 3, adjust=False).mean()

    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    df["macd"] = ema12 - ema26
    df["macd_signal"] = ema(df["macd"], 9)
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    true_range = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = true_range.rolling(14).mean()
    df["atr_pct"] = df["atr14"] / close * 100
    df["high20_prev"] = high.shift(1).rolling(20).max()
    df["low10_prev"] = low.shift(1).rolling(10).min()
    return df


def crossed_above(series_a: pd.Series, series_b: pd.Series) -> bool:
    if len(series_a) < 2 or len(series_b) < 2:
        return False
    return series_a.iloc[-2] <= series_b.iloc[-2] and series_a.iloc[-1] > series_b.iloc[-1]


def latest_float(row: pd.Series, key: str) -> float:
    value = row.get(key, math.nan)
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def add_score(result: PickResult, points: float, reason: str) -> None:
    result.score += points
    result.reasons.append(f"+{points:g} {reason}")


def add_warning(result: PickResult, points: float, reason: str) -> None:
    result.score -= points
    result.warnings.append(f"-{points:g} {reason}")


def build_technical_signals(df: pd.DataFrame) -> dict[str, bool]:
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else latest
    close = latest["close"]
    ma20 = latest.get("ma20", math.nan)
    ma60 = latest.get("ma60", math.nan)
    high20_prev = latest.get("high20_prev", math.nan)
    vol_ratio = latest.get("vol_ratio", math.nan)
    k_value = latest.get("k", math.nan)
    d_value = latest.get("d", math.nan)
    macd_hist = latest.get("macd_hist", math.nan)
    rsi = latest.get("rsi14", math.nan)
    atr_pct = latest.get("atr_pct", math.nan)

    macd_rising = False
    if len(df) >= 3:
        macd_rising = bool(latest["macd_hist"] > prev["macd_hist"] > df["macd_hist"].iloc[-3])

    not_extended = True
    if pd.notna(ma20):
        not_extended = not_extended and close <= ma20 * 1.12
    if pd.notna(rsi):
        not_extended = not_extended and rsi <= 78
    if pd.notna(atr_pct):
        not_extended = not_extended and atr_pct <= 7

    return {
        "above_ma20": bool(pd.notna(ma20) and close > ma20),
        "above_ma60": bool(pd.notna(ma60) and close > ma60),
        "ma20_slope_up": bool(pd.notna(latest.get("ma20_slope", math.nan)) and latest["ma20_slope"] > 0.8),
        "breakout_20d": bool(pd.notna(high20_prev) and close > high20_prev),
        "near_breakout_20d": bool(pd.notna(high20_prev) and close >= high20_prev * 0.98),
        "volume_expansion": bool(pd.notna(vol_ratio) and vol_ratio >= 1.15),
        "strong_volume": bool(pd.notna(vol_ratio) and vol_ratio >= 1.5),
        "kd_golden": bool(crossed_above(df["k"], df["d"]) and pd.notna(k_value) and k_value < 70),
        "kd_bullish": bool(pd.notna(k_value) and pd.notna(d_value) and k_value > d_value and k_value < 80),
        "macd_turn_positive": bool(crossed_above(df["macd_hist"], pd.Series(0, index=df.index))),
        "macd_rising": macd_rising,
        "rsi_healthy": bool(pd.notna(rsi) and 50 <= rsi <= 70 and rsi > prev.get("rsi14", math.nan)),
        "not_extended": bool(not_extended),
    }


def build_buy_plan(symbol: str, df: pd.DataFrame, signals: dict[str, bool]) -> dict[str, object]:
    latest = df.iloc[-1]
    close = float(latest["close"])
    ma20 = latest.get("ma20", math.nan)
    ma60 = latest.get("ma60", math.nan)
    atr = latest.get("atr14", math.nan)
    high20_prev = latest.get("high20_prev", math.nan)
    low10_prev = latest.get("low10_prev", math.nan)
    atr_value = float(atr) if pd.notna(atr) and atr > 0 else max(close * 0.025, 0.01)

    entry_low = close
    entry_high = close
    buy_type = "觀察"
    reason = "尚未出現明確買進點，等待 K 線轉強或回測支撐。"

    if signals.get("breakout_20d") and (signals.get("volume_expansion") or signals.get("macd_rising")):
        buy_type = "突破買進"
        entry_low = close
        entry_high = close + atr_value * 0.35
        reason = "股價突破前 20 日高點，若隔日不跌回突破價，可視為突破買進點。"
    elif signals.get("near_breakout_20d") and signals.get("above_ma20"):
        buy_type = "突破前緣"
        entry_low = max(float(ma20) if pd.notna(ma20) else close - atr_value * 0.5, close - atr_value * 0.45)
        entry_high = close + atr_value * 0.2
        reason = "股價貼近 20 日高點且站上 20 日線，可等拉回不破短均或放量突破時買進。"
    elif signals.get("above_ma20") and (signals.get("kd_golden") or signals.get("macd_rising") or signals.get("rsi_healthy")):
        buy_type = "回測買進"
        support = float(ma20) if pd.notna(ma20) else close - atr_value
        entry_low = support
        entry_high = support + atr_value * 0.45
        reason = "短線站上 20 日線且動能轉強，較佳買點在回測 20 日線附近不破時。"
    elif signals.get("kd_golden") or signals.get("macd_turn_positive"):
        buy_type = "轉強觀察"
        entry_low = close
        entry_high = close + atr_value * 0.25
        reason = "動能指標轉強，但趨勢條件尚未完整，需等站回均線或突破壓力再提高買進信心。"

    stop_candidates = [close - atr_value * 2, close * 0.93]
    if pd.notna(low10_prev):
        stop_candidates.append(float(low10_prev) - atr_value * 0.2)
    if pd.notna(ma20):
        stop_candidates.append(float(ma20) - atr_value * 1.2)
    stop_loss = max(price for price in stop_candidates if price < close)
    risk = max(entry_high - stop_loss, atr_value)
    target_1 = entry_high + risk * 1.5
    target_2 = entry_high + risk * 2.0

    if pd.notna(high20_prev) and buy_type != "突破買進":
        target_1 = max(target_1, float(high20_prev))
    if pd.notna(ma60) and close < ma60:
        reason += " 目前仍在 60 日線下，部位需保守。"

    return {
        "type": buy_type,
        "entry_low": round(float(entry_low), 2),
        "entry_high": round(float(max(entry_low, entry_high)), 2),
        "reference_price": round(close, 2),
        "stop_loss": round(float(stop_loss), 2),
        "target_1": round(float(target_1), 2),
        "target_2": round(float(target_2), 2),
        "risk_percent": round((entry_high - stop_loss) / entry_high * 100, 2) if entry_high else None,
        "reason": reason,
    }


def score_technicals(symbol: str, frame: pd.DataFrame, name: str = "") -> PickResult:
    df = add_indicators(frame)
    result = PickResult(symbol=symbol, name=name)
    if len(df) < 60:
        result.warnings.append("資料少於 60 根 K 線，趨勢判斷可信度較低")

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else latest
    result.price = float(latest["close"])
    stop_candidates = [latest["close"] * 0.93]
    if pd.notna(latest.get("low10_prev", math.nan)):
        stop_candidates.append(float(latest["low10_prev"]))
    if pd.notna(latest.get("atr14", math.nan)):
        stop_candidates.append(float(latest["close"] - latest["atr14"] * 2))
    result.stop_loss = float(max(price for price in stop_candidates if price < latest["close"]))
    result.metrics = {
        "close": latest_float(latest, "close"),
        "ma20": latest_float(latest, "ma20"),
        "ma60": latest_float(latest, "ma60"),
        "ma20_slope": latest_float(latest, "ma20_slope"),
        "vol_ratio": latest_float(latest, "vol_ratio"),
        "k": latest_float(latest, "k"),
        "d": latest_float(latest, "d"),
        "macd_hist": latest_float(latest, "macd_hist"),
        "rsi14": latest_float(latest, "rsi14"),
        "atr_pct": latest_float(latest, "atr_pct"),
    }
    result.signals.update(build_technical_signals(df))
    result.buy_plan = build_buy_plan(symbol, df, result.signals)

    close = latest["close"]
    if pd.notna(latest["ma20"]) and close > latest["ma20"]:
        add_score(result, 12, "收盤站上 20 日均線，短線多方取得主導")
    if pd.notna(latest["ma60"]) and close > latest["ma60"]:
        add_score(result, 10, "收盤站上 60 日均線，中期趨勢偏多")
    if pd.notna(latest["ma20_slope"]) and latest["ma20_slope"] > 0.8:
        add_score(result, 10, f"20 日均線 5 日斜率 {latest['ma20_slope']:.1f}%，趨勢正在轉強")
    if pd.notna(latest["high20_prev"]) and close > latest["high20_prev"]:
        add_score(result, 16, "收盤突破前 20 日高點，具備起漲突破型態")
    elif pd.notna(latest["high20_prev"]) and close >= latest["high20_prev"] * 0.98:
        add_score(result, 8, "股價貼近前 20 日高點，處於突破前緣")

    if pd.notna(latest["vol_ratio"]) and latest["vol_ratio"] >= 1.5:
        add_score(result, 12, f"成交量為 20 日均量 {latest['vol_ratio']:.1f} 倍，突破有量能支持")
    elif pd.notna(latest["vol_ratio"]) and latest["vol_ratio"] >= 1.15:
        add_score(result, 6, f"成交量高於 20 日均量 {latest['vol_ratio']:.1f} 倍")

    if crossed_above(df["k"], df["d"]) and latest["k"] < 70:
        add_score(result, 10, "KD 黃金交叉且尚未過熱")
    elif pd.notna(latest["k"]) and pd.notna(latest["d"]) and latest["k"] > latest["d"] and latest["k"] < 80:
        add_score(result, 5, "KD 維持多方排列")

    if crossed_above(df["macd_hist"], pd.Series(0, index=df.index)):
        add_score(result, 12, "MACD 柱狀體翻正，動能由空轉多")
    elif latest["macd_hist"] > prev["macd_hist"] > df["macd_hist"].iloc[-3] if len(df) >= 3 else False:
        add_score(result, 7, "MACD 柱狀體連續擴大，動能升溫")

    if pd.notna(latest["rsi14"]) and 50 <= latest["rsi14"] <= 70 and latest["rsi14"] > prev["rsi14"]:
        add_score(result, 8, f"RSI {latest['rsi14']:.1f} 位於健康強勢區並上升")
    elif pd.notna(latest["rsi14"]) and latest["rsi14"] > 78:
        add_warning(result, 8, f"RSI {latest['rsi14']:.1f} 偏熱，追價風險提高")

    if pd.notna(latest["ma20"]) and close > latest["ma20"] * 1.12:
        add_warning(result, 10, "股價距 20 日均線超過 12%，短線乖離偏大")
    if pd.notna(latest["atr_pct"]) and latest["atr_pct"] > 7:
        add_warning(result, 8, f"ATR 波動率 {latest['atr_pct']:.1f}% 偏高，停損距離可能過大")
    if pd.notna(latest["ma20"]) and close < latest["ma20"]:
        add_warning(result, 10, "收盤未站上 20 日均線，尚未確認短線轉強")

    return result


def load_financials(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    output: dict[str, dict[str, str]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        output[symbol] = row
        if symbol.endswith((".TW", ".TWO")):
            output[symbol.split(".")[0]] = row
    return output


def financial_value(row: dict[str, str], key: str) -> float:
    try:
        return float(str(row.get(key, "")).replace("%", "").strip())
    except ValueError:
        return math.nan


def score_financials(result: PickResult, financials: dict[str, str] | None) -> None:
    if not financials:
        result.warnings.append("缺少財務資料，僅依技術面評分")
        result.signals.update(
            {
                "financial_growth": False,
                "eps_positive": False,
                "roe_quality": False,
                "reasonable_valuation": False,
            }
        )
        return

    result.name = result.name or str(financials.get("name", ""))
    revenue_yoy = financial_value(financials, "revenue_yoy")
    eps_ttm = financial_value(financials, "eps_ttm")
    roe = financial_value(financials, "roe")
    gross_margin = financial_value(financials, "gross_margin")
    operating_margin = financial_value(financials, "operating_margin")
    debt_to_equity = financial_value(financials, "debt_to_equity")
    pe = financial_value(financials, "pe")

    result.signals.update(
        {
            "financial_growth": bool(pd.notna(revenue_yoy) and revenue_yoy >= 10),
            "eps_positive": bool(pd.notna(eps_ttm) and eps_ttm > 0),
            "roe_quality": bool(pd.notna(roe) and roe >= 12),
            "reasonable_valuation": bool(pd.notna(pe) and 0 < pe <= 25),
        }
    )

    if pd.notna(revenue_yoy) and revenue_yoy >= 10:
        add_score(result, 10, f"營收年增 {revenue_yoy:.1f}%，基本面有成長佐證")
    elif pd.notna(revenue_yoy) and revenue_yoy < 0:
        add_warning(result, 8, f"營收年增 {revenue_yoy:.1f}%，成長動能不足")

    if pd.notna(eps_ttm) and eps_ttm > 0:
        add_score(result, 6, f"近四季 EPS {eps_ttm:.2f} 為正")
    elif pd.notna(eps_ttm) and eps_ttm <= 0:
        add_warning(result, 10, "近四季 EPS 非正，財務品質需保守看待")

    if pd.notna(roe) and roe >= 12:
        add_score(result, 8, f"ROE {roe:.1f}% 達雙位數，資本效率佳")
    if pd.notna(gross_margin) and pd.notna(operating_margin) and gross_margin >= 25 and operating_margin >= 8:
        add_score(result, 6, f"毛利率 {gross_margin:.1f}%、營益率 {operating_margin:.1f}%，獲利結構尚可")
    if pd.notna(debt_to_equity) and debt_to_equity <= 100:
        add_score(result, 4, f"負債權益比 {debt_to_equity:.1f}%，財務槓桿可控")
    elif pd.notna(debt_to_equity) and debt_to_equity > 200:
        add_warning(result, 6, f"負債權益比 {debt_to_equity:.1f}% 偏高")
    if pd.notna(pe) and 0 < pe <= 25:
        add_score(result, 4, f"本益比 {pe:.1f}，估值未明顯過高")
    elif pd.notna(pe) and pe > 45:
        add_warning(result, 5, f"本益比 {pe:.1f} 偏高，需確認成長能否支撐")


def load_symbols(args: argparse.Namespace) -> list[str]:
    symbols: list[str] = []
    if args.symbols:
        symbols.extend(part.strip() for part in args.symbols.split(",") if part.strip())
    if args.watchlist:
        with Path(args.watchlist).open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    symbols.append(line.split(",")[0].strip())
    if args.prices_dir and not symbols:
        symbols.extend(path.stem for path in Path(args.prices_dir).glob("*.csv"))
    return list(dict.fromkeys(symbols))


def price_data_for(symbol: str, args: argparse.Namespace) -> pd.DataFrame:
    if args.fetch:
        if args.sleep:
            time.sleep(args.sleep)
        return fetch_yahoo_prices(symbol, range_days=args.range)

    if not args.prices_dir:
        raise ValueError("未使用 --fetch 時，請提供 --prices-dir")
    path = Path(args.prices_dir) / f"{symbol}.csv"
    if not path.exists() and "." in symbol:
        path = Path(args.prices_dir) / f"{symbol.split('.')[0]}.csv"
    if not path.exists():
        raise FileNotFoundError(f"找不到價格檔: {path}")
    return load_price_csv(path)


def pick_stocks(args: argparse.Namespace) -> list[PickResult]:
    financials_by_symbol = load_financials(Path(args.financials) if args.financials else None)
    results: list[PickResult] = []
    for symbol in load_symbols(args):
        try:
            prices = price_data_for(symbol, args)
            resolved_symbol = prices.attrs.get("resolved_symbol", normalize_tw_symbol(symbol))
            financials = financials_by_symbol.get(symbol.upper()) or financials_by_symbol.get(
                resolved_symbol.upper()
            )
            name = str(financials.get("name", "")) if financials else ""
            result = score_technicals(resolved_symbol, prices, name=name)
            score_financials(result, financials)
            results.append(result)
        except Exception as exc:
            results.append(PickResult(symbol=symbol, score=-999, warnings=[str(exc)]))
    return sorted(results, key=lambda item: item.score, reverse=True)


def save_fetched_prices(args: argparse.Namespace) -> None:
    symbols = load_symbols(args)
    output_dir = Path(args.download_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        if args.sleep:
            time.sleep(args.sleep)
        prices = fetch_yahoo_prices(symbol, range_days=args.range)
        resolved_symbol = prices.attrs.get("resolved_symbol", normalize_tw_symbol(symbol))
        output_path = output_dir / f"{resolved_symbol}.csv"
        columns = ["date", "close"] if args.close_only else ["date", "open", "high", "low", "close", "volume"]
        prices.loc[:, columns].to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"已下載 {resolved_symbol}: {output_path}")


def print_results(results: Iterable[PickResult], top: int, min_score: float) -> None:
    filtered = [result for result in results if result.score >= min_score][:top]
    if not filtered:
        print("沒有股票達到篩選門檻。可降低 --min-score，或檢查價格/財務資料是否完整。")
        return

    for rank, result in enumerate(filtered, start=1):
        title = f"{rank}. {result.symbol}"
        if result.name:
            title += f" {result.name}"
        print(f"{title} | 分數 {result.score:.1f} | 收盤 {result.price:.2f} | 參考停損 {result.stop_loss:.2f}")
        print("   買進理由:")
        for reason in result.reasons[:8]:
            print(f"   - {reason}")
        if result.warnings:
            print("   風險提醒:")
            for warning in result.warnings[:5]:
                print(f"   - {warning}")
        metrics = result.metrics
        if metrics:
            def fmt(value: float, digits: int = 2) -> str:
                return "NA" if pd.isna(value) else f"{value:.{digits}f}"

            print(
                "   指標摘要: "
                f"MA20 {fmt(metrics['ma20'])}, MA60 {fmt(metrics['ma60'])}, "
                f"K/D {fmt(metrics['k'], 1)}/{fmt(metrics['d'], 1)}, "
                f"MACD hist {fmt(metrics['macd_hist'])}, RSI {fmt(metrics['rsi14'], 1)}, "
                f"量比 {fmt(metrics['vol_ratio'], 1)}"
            )
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="台灣股票起漲候選股篩選系統")
    source = parser.add_argument_group("資料來源")
    source.add_argument("--symbols", help="逗號分隔股票代號，例如 2330,2317,2454")
    source.add_argument("--watchlist", help="股票清單文字檔，每行一個代號")
    source.add_argument("--prices-dir", help="本機價格 CSV 資料夾")
    source.add_argument("--financials", help="財務指標 CSV")
    source.add_argument("--fetch", action="store_true", help="從 Yahoo Finance 抓取日 K 資料")
    source.add_argument("--range", default="1y", help="Yahoo 抓取區間，例如 6mo, 1y, 2y")
    source.add_argument("--sleep", type=float, default=0.0, help="抓取每檔股票前等待秒數，避免請求過快")
    source.add_argument("--download-dir", help="只下載網路價格資料到指定資料夾，不執行篩選")
    source.add_argument("--close-only", action="store_true", help="搭配 --download-dir，只輸出 date 與 close")

    output = parser.add_argument_group("輸出")
    output.add_argument("--top", type=int, default=10, help="輸出前幾名")
    output.add_argument("--min-score", type=float, default=55, help="最低推薦分數")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not load_symbols(args):
        parser.error("請提供 --symbols、--watchlist，或在 --prices-dir 放入 CSV")
    if args.download_dir:
        save_fetched_prices(args)
        return 0
    results = pick_stocks(args)
    print_results(results, args.top, args.min_score)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
