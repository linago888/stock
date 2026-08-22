"""Vercel serverless handler for the 領先訊號 API.

All /api/signals/* paths are rewritten by vercel.json to
/api/signals?action=<name>. Reads a pre-built gzipped JSON bundle
(data/signals_bundle.json.gz) at cold-start.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "data" / "signals_bundle.json.gz"

_CACHE: dict[str, object] = {"data": None}

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


def _classify(subject: str) -> list[str]:
    hits = [lbl for lbl, kws in SIGNAL_RULES if any(k in subject for k in kws)]
    return hits or ["其他"]


def _load() -> dict:
    if _CACHE["data"] is not None:
        return _CACHE["data"]  # type: ignore[return-value]
    with gzip.open(BUNDLE, "rt", encoding="utf-8") as fh:
        d = json.load(fh)
    # enrich announcements with days_before + labels once
    import datetime as dt
    for group_key in ("poc_anns", "ctl_anns"):
        anns_map = d.get(group_key, {})
        for sym, anns in anns_map.items():
            t0_str = ""
            for c in d.get("poc_candidates" if group_key == "poc_anns" else "ctl_candidates", []):
                if c["symbol"] == sym:
                    t0_str = c.get("T0", "")
                    break
            t0 = None
            if t0_str:
                try:
                    t0 = dt.date.fromisoformat(t0_str)
                except Exception:
                    t0 = None
            for a in anns:
                if t0 and a.get("date"):
                    try:
                        a["_dbefore"] = (t0 - dt.date.fromisoformat(a["date"])).days
                    except Exception:
                        a["_dbefore"] = None
                a["_labels"] = _classify(a.get("subject", ""))
            anns.sort(key=lambda x: (x.get("date", ""), x.get("spoke_time", "")))
    _CACHE["data"] = d
    return d


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hit_rate(anns_map: dict) -> dict:
    total = len(anns_map) or 1
    hit = sum(1 for v in anns_map.values() if v)
    return {"hit": hit, "total": total, "pct": round(100 * hit / total, 1)}


def action_status(_p) -> dict:
    d = _load()
    dates = []
    import datetime as dt
    for c in d.get("poc_candidates", []) + d.get("ctl_candidates", []):
        try:
            dates.append(dt.date.fromisoformat(c.get("T0", "")))
        except Exception:
            pass
    return {
        "surge_event_count": d.get("surge_event_count", 0),
        "poc_count": len(d.get("poc_candidates", [])),
        "ctl_count": len(d.get("ctl_candidates", [])),
        "poc_hit_rate": _hit_rate(d.get("poc_anns", {})),
        "ctl_hit_rate": _hit_rate(d.get("ctl_anns", {})),
        "poc_total_anns": sum(len(v) for v in d.get("poc_anns", {}).values()),
        "ctl_total_anns": sum(len(v) for v in d.get("ctl_anns", {}).values()),
        "date_range": {
            "min": min(dates).isoformat() if dates else "",
            "max": max(dates).isoformat() if dates else "",
        },
    }


def action_comparison(_p) -> dict:
    d = _load()
    p_s = d.get("poc_summary", {})
    c_s = d.get("ctl_summary", {})
    p_n = p_s.get("n_stocks") or len(d.get("poc_candidates", [])) or 1
    c_n = c_s.get("n_stocks") or len(d.get("ctl_candidates", [])) or 1
    p_counts = p_s.get("signal_stock_count", {})
    c_counts = c_s.get("signal_stock_count", {})
    labels = set(p_counts) | set(c_counts)
    rows = []
    for lbl in labels:
        p_st = p_counts.get(lbl, 0)
        c_st = c_counts.get(lbl, 0)
        p_cov = p_st / p_n
        c_cov = c_st / c_n
        rows.append({
            "label": lbl,
            "poc_stocks": p_st, "poc_total": p_n, "poc_cov": round(p_cov, 4),
            "ctl_stocks": c_st, "ctl_total": c_n, "ctl_cov": round(c_cov, 4),
            "delta": round(p_cov - c_cov, 4),
            "lift": (round(p_cov / c_cov, 3) if c_cov > 0 else None),
        })
    rows.sort(key=lambda r: (-r["delta"], -r["poc_cov"]))
    return {"rows": rows}


def action_stocks(params: dict[str, list[str]]) -> dict:
    d = _load()
    group = params.get("group", ["poc"])[0]
    if group in ("ctl", "control"):
        candidates = d.get("ctl_candidates", [])
        anns_map = d.get("ctl_anns", {})
    else:
        candidates = d.get("poc_candidates", [])
        anns_map = d.get("poc_anns", {})
    out = []
    for c in candidates:
        sym = c["symbol"]
        stock_anns = anns_map.get(sym, [])
        label_hits: Counter = Counter()
        for a in stock_anns:
            for lbl in a.get("_labels", []):
                label_hits[lbl] += 1
        out.append({
            "symbol": sym,
            "name": c.get("name", ""),
            "T0": c.get("T0", ""),
            "return_pct": _num(c.get("return_pct")),
            "vol_ratio": _num(c.get("vol_ratio")),
            "ann_count": len(stock_anns),
            "labels": list(label_hits),
        })
    return {"group": group, "items": out}


def action_announcements(params: dict[str, list[str]]) -> dict:
    d = _load()
    sym = params.get("symbol", [""])[0]
    group = params.get("group", ["poc"])[0]
    anns_map = d.get("ctl_anns", {}) if group in ("ctl", "control") else d.get("poc_anns", {})
    anns = anns_map.get(sym, [])
    return {
        "symbol": sym,
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


def action_whitelist(params: dict[str, list[str]]) -> dict:
    min_delta = float(params.get("min_delta", ["0.2"])[0])
    min_lift = float(params.get("min_lift", ["2.0"])[0])
    min_stocks = int(params.get("min_stocks", ["3"])[0])
    comp = action_comparison(params)
    keep = []
    for r in comp["rows"]:
        if r["delta"] < min_delta:
            continue
        if r["poc_stocks"] < min_stocks:
            continue
        if r["lift"] is not None and r["lift"] < min_lift:
            continue
        keywords = next((kws for lbl, kws in SIGNAL_RULES if lbl == r["label"]), [])
        keep.append({**r, "keywords": keywords})
    return {"items": keep, "min_delta": min_delta, "min_lift": min_lift, "min_stocks": min_stocks}


HANDLERS = {
    "status": action_status,
    "comparison": action_comparison,
    "stocks": action_stocks,
    "announcements": action_announcements,
    "whitelist": action_whitelist,
}


def _resolve(path: str, params: dict[str, list[str]]) -> tuple[int, dict]:
    """Given the request path and query params, return (status, json_body)."""
    # Vercel now routes /api/signals/<action> to /api/signals with the tail
    # preserved in PATH_INFO. Support both /api/signals?action=X and
    # /api/signals/X path forms.
    action = (params.get("action", [""])[0]).strip()
    if not action and path:
        # strip leading /api/signals and any trailing / to get action from path
        tail = path.split("/api/signals", 1)[-1].lstrip("/").split("?", 1)[0]
        if tail:
            action = tail.strip("/")
    if not action:
        return 400, {"error": "missing action"}
    fn = HANDLERS.get(action)
    if not fn:
        return 404, {"error": f"unknown action: {action}", "path": path}
    try:
        return 200, fn(params)
    except Exception as exc:
        return 500, {"error": str(exc), "action": action}


def app(environ, start_response):
    """WSGI entry — modern @vercel/python builder looks for `app` first."""
    path = environ.get("PATH_INFO", "")
    query = environ.get("QUERY_STRING", "")
    params = parse_qs(query)
    status, payload = _resolve(path, params)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
    start_response(
        f"{status} {reason}",
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "public, max-age=300"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


class handler(BaseHTTPRequestHandler):
    """Legacy fallback entry — some builders still look for a `handler` class."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        status, payload = _resolve(parsed.path, params)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        return
