"""领先訊號分析後端 — 讀取 PoC 20 檔 + 對照組 + MOPS 重訊，供 API 使用。"""

from __future__ import annotations

import csv
import datetime as dt
import json
import threading
from collections import Counter
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MOPS_DIR = DATA_DIR / "mops"

POC_CANDIDATES = DATA_DIR / "poc20_candidates.csv"
CTL_CANDIDATES = DATA_DIR / "control20_candidates.csv"
POC_SUMMARY = DATA_DIR / "poc20_summary.json"
CTL_SUMMARY = DATA_DIR / "control20_summary.json"
SURGE_CSV = DATA_DIR / "surge_events.csv"
WATCHLIST = DATA_DIR / "watchlist_scan.json"

SIGNAL_RULES: list[tuple[str, list[str]]] = [
    ("資產處分（賣廠、賣土地、賣設備）", ["出售", "處分", "廠房", "土地", "設備", "轉讓"]),
    ("庫藏股 / 減資", ["庫藏股", "減資", "註銷"]),
    ("轉換公司債 / 現金增資 / 私募",
     ["轉換公司債", "現金增資", "私募", "有擔保", "無擔保"]),
    ("董事會決議 / 董事會召開", ["董事會", "決議"]),
    ("財務報告 / 財報公告", ["財務報告", "財報", "自結", "合併財務報告"]),
    ("法說會 / 券商論壇", ["法人說明會", "法說會", "投資論壇", "券商", "受邀參加"]),
    ("高階人事異動 (董監事 / CEO / CFO / 發言人)",
     ["發言人", "董事長", "總經理", "財務長", "獨立董事", "人事", "改派", "解任"]),
    ("重大契約 / 訂單", ["合約", "訂單", "簽署", "MOU", "策略聯盟", "合作"]),
    ("投資 / 併購 / 轉投資", ["取得", "併購", "投資", "轉投資", "子公司", "設立"]),
    ("股利政策 / 除權息", ["股利", "配息", "配股", "盈餘分配", "股東常會"]),
    ("注意 / 處置股 (異常波動)", ["注意", "處置", "異常", "警示"]),
    ("信用交易 / 融資融券變動", ["融資", "融券", "信用交易"]),
]


def classify(subject: str) -> list[str]:
    hits = [lbl for lbl, kws in SIGNAL_RULES if any(k in subject for k in kws)]
    return hits or ["其他"]


_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"mtime": 0.0, "data": None}


def _cache_mtime() -> float:
    mts = []
    for p in (POC_CANDIDATES, CTL_CANDIDATES, POC_SUMMARY, CTL_SUMMARY):
        if p.exists():
            mts.append(p.stat().st_mtime)
    if MOPS_DIR.exists():
        for p in MOPS_DIR.glob("*.json"):
            mts.append(p.stat().st_mtime)
    return max(mts, default=0.0)


def _load_all(force: bool = False) -> dict:
    with _LOCK:
        m = _cache_mtime()
        if not force and _CACHE["data"] is not None and m == _CACHE["mtime"]:
            return _CACHE["data"]  # type: ignore[return-value]

        def load_candidates(path: Path) -> list[dict]:
            if not path.exists():
                return []
            return list(csv.DictReader(path.open("r", encoding="utf-8-sig")))

        def load_summary(path: Path) -> dict:
            if not path.exists():
                return {}
            return json.loads(path.read_text(encoding="utf-8"))

        def load_anns(candidates: list[dict]) -> dict[str, list[dict]]:
            out: dict[str, list[dict]] = {}
            for c in candidates:
                sym = c["symbol"]
                code = sym.split(".")[0]
                try:
                    t0 = dt.date.fromisoformat(c["T0"])
                except Exception:
                    continue
                start = (t0 - dt.timedelta(days=30)).isoformat()
                end = (t0 - dt.timedelta(days=1)).isoformat()
                p = MOPS_DIR / f"{code}_{start}_{end}.json"
                if not p.exists():
                    out[sym] = []
                    continue
                anns = json.loads(p.read_text(encoding="utf-8"))
                for a in anns:
                    try:
                        a["_dbefore"] = (t0 - dt.date.fromisoformat(a["date"])).days
                    except Exception:
                        a["_dbefore"] = None
                    a["_labels"] = classify(a["subject"])
                anns.sort(key=lambda a: (a["date"], a.get("spoke_time", "")))
                out[sym] = anns
            return out

        poc_c = load_candidates(POC_CANDIDATES)
        ctl_c = load_candidates(CTL_CANDIDATES)
        poc_s = load_summary(POC_SUMMARY)
        ctl_s = load_summary(CTL_SUMMARY)
        poc_a = load_anns(poc_c)
        ctl_a = load_anns(ctl_c)

        surge_count = 0
        if SURGE_CSV.exists():
            with SURGE_CSV.open("r", encoding="utf-8-sig") as fh:
                surge_count = sum(1 for _ in csv.reader(fh)) - 1

        data = {
            "poc_candidates": poc_c,
            "ctl_candidates": ctl_c,
            "poc_summary": poc_s,
            "ctl_summary": ctl_s,
            "poc_anns": poc_a,
            "ctl_anns": ctl_a,
            "surge_event_count": surge_count,
            "mops_files": len(list(MOPS_DIR.glob("*.json"))) if MOPS_DIR.exists() else 0,
            "cache_mtime": m,
        }
        _CACHE["data"] = data
        _CACHE["mtime"] = m
        return data


def status() -> dict:
    d = _load_all()
    return {
        "surge_event_count": d["surge_event_count"],
        "poc_count": len(d["poc_candidates"]),
        "ctl_count": len(d["ctl_candidates"]),
        "poc_hit_rate": _hit_rate(d["poc_anns"]),
        "ctl_hit_rate": _hit_rate(d["ctl_anns"]),
        "poc_total_anns": sum(len(v) for v in d["poc_anns"].values()),
        "ctl_total_anns": sum(len(v) for v in d["ctl_anns"].values()),
        "date_range": _date_range(d["poc_candidates"] + d["ctl_candidates"]),
        "mops_files": d["mops_files"],
        "cache_mtime": d["cache_mtime"],
    }


def _hit_rate(anns_map: dict[str, list[dict]]) -> dict:
    total = len(anns_map) or 1
    hit = sum(1 for v in anns_map.values() if v)
    return {"hit": hit, "total": total, "pct": round(100 * hit / total, 1)}


def _date_range(candidates: list[dict]) -> dict:
    dates = []
    for c in candidates:
        try:
            dates.append(dt.date.fromisoformat(c["T0"]))
        except Exception:
            pass
    if not dates:
        return {"min": "", "max": ""}
    return {"min": min(dates).isoformat(), "max": max(dates).isoformat()}


def comparison() -> dict:
    d = _load_all()
    p_summary, c_summary = d["poc_summary"], d["ctl_summary"]
    p_n = p_summary.get("n_stocks") or len(d["poc_candidates"]) or 1
    c_n = c_summary.get("n_stocks") or len(d["ctl_candidates"]) or 1
    p_counts = p_summary.get("signal_stock_count", {})
    c_counts = c_summary.get("signal_stock_count", {})
    labels = set(p_counts) | set(c_counts)
    rows = []
    for lbl in labels:
        p_stocks = p_counts.get(lbl, 0)
        c_stocks = c_counts.get(lbl, 0)
        p_cov = p_stocks / p_n
        c_cov = c_stocks / c_n
        rows.append(
            {
                "label": lbl,
                "poc_stocks": p_stocks,
                "poc_total": p_n,
                "poc_cov": round(p_cov, 4),
                "ctl_stocks": c_stocks,
                "ctl_total": c_n,
                "ctl_cov": round(c_cov, 4),
                "delta": round(p_cov - c_cov, 4),
                "lift": (round(p_cov / c_cov, 3) if c_cov > 0 else None),
            }
        )
    rows.sort(key=lambda r: (-r["delta"], -r["poc_cov"]))
    return {"rows": rows}


def stocks(group: str = "poc") -> dict:
    d = _load_all()
    if group == "control" or group == "ctl":
        candidates = d["ctl_candidates"]
        anns = d["ctl_anns"]
    else:
        candidates = d["poc_candidates"]
        anns = d["poc_anns"]

    out = []
    for c in candidates:
        sym = c["symbol"]
        stock_anns = anns.get(sym, [])
        label_hits: Counter = Counter()
        for a in stock_anns:
            for lbl in a.get("_labels", []):
                label_hits[lbl] += 1
        out.append(
            {
                "symbol": sym,
                "name": c.get("name", ""),
                "T0": c.get("T0", ""),
                "return_pct": _num(c.get("return_pct")),
                "vol_ratio": _num(c.get("vol_ratio")),
                "ann_count": len(stock_anns),
                "labels": list(label_hits),
            }
        )
    return {"group": group, "items": out}


def announcements(symbol: str, group: str = "poc") -> dict:
    d = _load_all()
    anns_map = d["ctl_anns"] if group in ("ctl", "control") else d["poc_anns"]
    anns = anns_map.get(symbol, [])
    return {
        "symbol": symbol,
        "count": len(anns),
        "items": [
            {
                "date": a.get("date"),
                "days_before": a.get("_dbefore"),
                "time": a.get("roc_time"),
                "subject": a.get("subject"),
                "labels": a.get("_labels", []),
                "detail": a.get("detail", ""),
            }
            for a in anns
        ],
    }


def watchlist_scan() -> dict:
    """Load the pre-computed watchlist (produced offline by scan_watchlist* scripts)."""
    if not WATCHLIST.exists():
        return {
            "generated_at": "",
            "items": [],
            "matched_count": 0,
            "not_surged_count": 0,
            "already_surged_count": 0,
        }
    return json.loads(WATCHLIST.read_text(encoding="utf-8"))


# Signal category weights derived from the PoC 20 vs Control 20 lift values.
# Categories with lift infinity (no control-group matches) are capped at 5.
CATEGORY_WEIGHTS = {
    "投資 / 併購 / 轉投資": 2.75,
    "庫藏股 / 減資": 5.0,
    "高階人事異動": 5.0,
    "股利政策 / 除權息": 5.0,
    "重大契約 / 訂單": 1.5,
}


PROXY_PREFIXES = ("代", "本公司代", "代重要子公司", "代子公司")


def _is_proxy(subject: str) -> bool:
    """A '代...公告' announcement is a parent company forwarding a subsidiary's news.
    Halve its weight — the subsidiary's action is not the parent's own catalyst."""
    if not subject:
        return False
    s = subject.lstrip()
    return any(s.startswith(p) for p in PROXY_PREFIXES)


def _score_stock(anns: list[dict], latest_signal_date: str) -> dict:
    """Score one stock aggregated across its 14-day announcements."""
    from collections import Counter, defaultdict
    import datetime as dt

    labels_hit: set[str] = set()
    label_counts: Counter = Counter()      # (label, is_proxy) -> count
    proxy_labels_hit: set[str] = set()
    own_labels_hit: set[str] = set()
    for a in anns:
        proxy = _is_proxy(a.get("subject", ""))
        for lbl in a.get("labels", []):
            labels_hit.add(lbl)
            if proxy:
                proxy_labels_hit.add(lbl)
                label_counts[(lbl, True)] += 1
            else:
                own_labels_hit.add(lbl)
                label_counts[(lbl, False)] += 1

    # own signals full weight; proxy signals half weight
    signal_score = 0.0
    for (lbl, is_proxy), cnt in label_counts.items():
        w = CATEGORY_WEIGHTS.get(lbl, 0.5)
        if is_proxy:
            w *= 0.5
        signal_score += w * cnt

    # diversity: only own-signal categories count fully; proxy-only categories at half
    diversity_bonus = 1.5 * len(own_labels_hit) + 0.5 * len(proxy_labels_hit - own_labels_hit)

    day_counts: Counter = Counter(a.get("date", "") for a in anns)
    max_same_day = max(day_counts.values()) if day_counts else 0
    concentration_bonus = 2.0 if max_same_day >= 3 else (1.0 if max_same_day >= 2 else 0.0)

    recency_bonus = 0.0
    try:
        today = dt.date.today()
        latest = dt.date.fromisoformat(latest_signal_date)
        d = (today - latest).days
        if d <= 3:
            recency_bonus = 3.0
        elif d <= 7:
            recency_bonus = 2.0
        elif d <= 14:
            recency_bonus = 1.0
    except Exception:
        pass

    proxy_ratio = sum(1 for a in anns if _is_proxy(a.get("subject", ""))) / max(1, len(anns))
    # Technical adjustment: use first ann's tech snapshot (all anns for the
    # same stock share the same tech snapshot from the scan).
    tech = next((a.get("tech") for a in anns if a.get("tech")), {}) or {}
    tech_signal = tech.get("tech_signal", "")
    tech_bonus_map = {"breakout_zone": 5.0, "flying": -10.0, "weak": -3.0, "quiet": 0.0}
    tech_bonus = tech_bonus_map.get(tech_signal, 0.0)

    total = signal_score + diversity_bonus + concentration_bonus + recency_bonus + tech_bonus
    return {
        "signal_score": round(signal_score, 2),
        "diversity_bonus": round(diversity_bonus, 2),
        "concentration_bonus": concentration_bonus,
        "recency_bonus": recency_bonus,
        "tech_bonus": tech_bonus,
        "score": round(total, 2),
        "labels_hit": sorted(labels_hit),
        "own_labels_hit": sorted(own_labels_hit),
        "max_same_day": max_same_day,
        "proxy_ratio": round(proxy_ratio, 2),
        "tech": tech,
    }


def watchlist_ranked(limit: int = 10, exclude_surged: bool = True) -> dict:
    """Return top-N stocks in the watchlist ranked by surge-probability heuristic.

    exclude_surged also filters items whose tech snapshot marks the stock as
    already_moved (30-day momentum >= 15%) — those have "already moved" and
    aren't good forward-looking candidates.
    """
    scan = watchlist_scan()
    items = scan.get("items") or []
    if exclude_surged:
        items = [i for i in items
                 if not i.get("already_surged")
                 and not (i.get("tech") or {}).get("already_moved")]

    # group by (co_id, name)
    from collections import defaultdict
    per_stock: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for it in items:
        per_stock[(it["co_id"], it.get("name", ""))].append(it)

    scored = []
    for (co_id, name), anns in per_stock.items():
        latest_date = max((a.get("date", "") for a in anns), default="")
        earliest_date = min((a.get("date", "") for a in anns), default="")
        s = _score_stock(anns, latest_date)
        # collect the subject headlines for context
        subjects = [
            {
                "date": a.get("date"),
                "labels": a.get("labels", []),
                "subject": a.get("subject", ""),
            }
            for a in sorted(anns, key=lambda a: a.get("date", ""), reverse=True)
        ]
        scored.append({
            "co_id": co_id,
            "name": name,
            "market": anns[0].get("market") if anns else "",
            "signal_count": len(anns),
            "latest_date": latest_date,
            "earliest_date": earliest_date,
            **s,
            "subjects": subjects,
        })

    scored.sort(key=lambda r: (-r["score"], -r["signal_count"]))
    return {
        "generated_at": scan.get("generated_at", ""),
        "backfill_window": scan.get("backfill_window"),
        "universe_size": scan.get("universe_size"),
        "total_signals": len(items),
        "stocks_with_signal": len(scored),
        "limit": limit,
        "items": scored[:limit],
    }


def whitelist(min_delta: float = 0.2, min_lift: float = 2.0,
              min_stocks: int = 3) -> dict:
    """Return the signal categories that survive the control comparison."""
    comp = comparison()
    keep = []
    for r in comp["rows"]:
        if r["delta"] < min_delta:
            continue
        if r["poc_stocks"] < min_stocks:
            continue
        if r["lift"] is not None and r["lift"] < min_lift:
            continue
        # gather keywords from rules
        keywords = next((kws for lbl, kws in SIGNAL_RULES if lbl == r["label"]), [])
        keep.append({**r, "keywords": keywords})
    return {"items": keep, "min_delta": min_delta, "min_lift": min_lift, "min_stocks": min_stocks}


def _num(v: object) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
