"""Merge scraped MOPS JSON for 3 surge stocks into one markdown report.

Reads data/mops/*_*.json and writes news_signals/poc_report.md so the
whole thing can be handed to an LLM (or read by human) for signal
extraction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOPS = ROOT / "data" / "mops"
OUT = ROOT / "news_signals" / "poc_report.md"

# Hand-picked from surge_events.csv (see find_surges.py).
STOCKS = [
    {
        "symbol": "3576",
        "name": "聯合再生",
        "sector": "太陽能 / 綠能",
        "market": "sii",
        "T0": "2026-02-06",
        "peak_date": "2026-02-24",
        "entry_close": None,   # filled below
        "peak_close": None,
        "return_pct": 140.5,
        "vol_ratio": 6.8,
        "window": ("2026-01-07", "2026-02-05"),
    },
    {
        "symbol": "2231",
        "name": "為升",
        "sector": "汽車電子 / 胎壓感測 (TPMS)",
        "market": "sii",
        "T0": "2025-08-18",
        "return_pct": 103.2,
        "vol_ratio": 10.3,
        "window": ("2025-07-19", "2025-08-17"),
    },
    {
        "symbol": "6147",
        "name": "頎邦",
        "sector": "半導體封測 / DDI",
        "market": "otc",
        "T0": "2026-04-02",
        "return_pct": 104.8,
        "vol_ratio": 4.4,
        "window": ("2026-03-03", "2026-04-01"),
    },
]


def load_announcements(symbol: str, window: tuple[str, str]) -> list[dict]:
    path = MOPS / f"{symbol}_{window[0]}_{window[1]}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def trim_detail(text: str, limit: int = 900) -> str:
    text = text.replace("公開資訊觀測站", "").strip()
    # remove the boilerplate footer
    for marker in ("以上資料均由各公司", "本資料由"):
        idx = text.rfind(marker)
        if 0 < idx < len(text) - 20:
            text = text[:idx].rstrip()
    text = text.strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " …(截斷)"
    return text


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    lines: list[str] = []
    lines.append("# 台股飆漲前夕 MOPS 重訊 PoC 報告")
    lines.append("")
    lines.append("**假設**：股票於 T0 出現爆量長紅（20 日內漲幅 ≥ 30%，量能 ≥ 20 日均量 3 倍），"
                 "則 T-30 到 T-1 天窗口內的 MOPS 重大訊息應存在可辨識的先行訊號。")
    lines.append("")
    lines.append("**資料**：公開資訊觀測站 (MOPS) 個股重大訊息全文。")
    lines.append("")
    lines.append("| 代號 | 名稱 | 產業 | T0 | 20日內漲幅 | T0 量比 | T-30 重訊數 |")
    lines.append("|---|---|---|---|---:|---:|---:|")
    for s in STOCKS:
        anns = load_announcements(s["symbol"], s["window"])
        s["_ann_count"] = len(anns)
        s["_anns"] = anns
        lines.append(
            f"| {s['symbol']} | {s['name']} | {s['sector']} | {s['T0']} | "
            f"{s['return_pct']:.1f}% | {s['vol_ratio']}x | {len(anns)} |"
        )
    lines.append("")

    for s in STOCKS:
        lines.append(f"## {s['symbol']} {s['name']}（{s['sector']}）")
        lines.append("")
        lines.append(
            f"- T0（爆量起漲日）：**{s['T0']}**，20 日內漲幅 **+{s['return_pct']:.1f}%**，"
            f"T0 量比 **{s['vol_ratio']}x**"
        )
        lines.append(f"- 觀察窗口：{s['window'][0]} → {s['window'][1]}（T-30 到 T-1）")
        lines.append(f"- 重訊筆數：**{s['_ann_count']}**")
        lines.append("")
        if not s["_anns"]:
            lines.append("_（窗口內無重訊）_")
            lines.append("")
            continue
        # sort by date
        for a in sorted(s["_anns"], key=lambda r: (r.get("date", ""), r.get("spoke_time", ""))):
            days_before = "?"
            try:
                import datetime as dt
                d0 = dt.date.fromisoformat(s["T0"])
                d = dt.date.fromisoformat(a["date"])
                days_before = f"T-{(d0 - d).days}"
            except Exception:
                pass
            lines.append(f"### [{days_before}] {a['date']} {a['roc_time']} — {a['subject']}")
            lines.append("")
            detail = trim_detail(a.get("detail", ""))
            if detail:
                lines.append("```")
                lines.append(detail)
                lines.append("```")
                lines.append("")

    lines.append("---")
    lines.append("## 供 LLM 分析的提問模板")
    lines.append("")
    lines.append("> 以上是 3 檔股票在飆漲前 30 天內出現的所有 MOPS 重大訊息。請歸納：")
    lines.append(">")
    lines.append("> 1. 這 3 檔股票在飆漲前是否存在**共同**的訊號類型（例如：資產處分、庫藏股/減資、"
                 "轉換公司債、董事會集中決議日、法說會/券商論壇邀請等）？")
    lines.append("> 2. 若要建立一組「先行指標關鍵字清單」用於掃描全市場 MOPS 重訊，"
                 "你會建議哪 5–10 個關鍵字或事件類型？")
    lines.append("> 3. 這些訊號距離 T0 的時間分布是否有規律（幾天前開始集中）？")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
