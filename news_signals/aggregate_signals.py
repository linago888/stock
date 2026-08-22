"""Aggregate MOPS 重訊 across all PoC-20 stocks and extract common signals.

Outputs news_signals/poc20_report.md with:
- Per-stock summary (days-before, subject, snippet)
- Cross-stock signal type frequency
- Top subject keywords (character n-gram frequency)
- Days-before-T0 distribution histogram
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOPS_DIR = ROOT / "data" / "mops"


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
    ("私募現增", ["私募"]),
]


def classify(subject: str) -> list[str]:
    hits = []
    for label, kws in SIGNAL_RULES:
        if any(k in subject for k in kws):
            hits.append(label)
    return hits or ["其他"]


def days_before(t0: dt.date, date_str: str) -> int:
    d = dt.date.fromisoformat(date_str)
    return (t0 - d).days


def load_candidates(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open("r", encoding="utf-8-sig")))


def load_anns(sym_full: str, t0: dt.date) -> list[dict]:
    code = sym_full.split(".")[0]
    start = (t0 - dt.timedelta(days=30)).isoformat()
    end = (t0 - dt.timedelta(days=1)).isoformat()
    p = MOPS_DIR / f"{code}_{start}_{end}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def char_ngrams(text: str, n: int = 3) -> list[str]:
    text = re.sub(r"[\s,，。（）()【】\[\]／/、；;：:\-—_·．.\d]", "", text)
    return [text[i : i + n] for i in range(len(text) - n + 1)]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(ROOT / "data" / "poc20_candidates.csv"))
    ap.add_argument("--out", default=str(ROOT / "news_signals" / "poc20_report.md"))
    ap.add_argument("--title", default="台股飆漲前夕 MOPS 重訊 PoC 20 檔擴大報告")
    ap.add_argument("--summary-json", default="",
                    help="also emit machine-readable stats to this JSON path")
    args = ap.parse_args()
    candidates_path = Path(args.candidates)
    output_path = Path(args.out)
    candidates = load_candidates(candidates_path)

    per_stock = []
    for c in candidates:
        t0 = dt.date.fromisoformat(c["T0"])
        anns = load_anns(c["symbol"], t0)
        for a in anns:
            a["_dbefore"] = days_before(t0, a["date"])
            a["_labels"] = classify(a["subject"])
        anns.sort(key=lambda a: (a["date"], a.get("spoke_time", "")))
        per_stock.append({"c": c, "T0": t0, "anns": anns})

    # cross-stock aggregates
    signal_stock_count: Counter = Counter()   # label -> # of stocks where it appears
    signal_ann_count: Counter = Counter()     # label -> # of announcements
    days_dist: Counter = Counter()
    subject_ngrams: Counter = Counter()
    for s in per_stock:
        seen_labels: set[str] = set()
        for a in s["anns"]:
            for lbl in a["_labels"]:
                signal_ann_count[lbl] += 1
                if lbl not in seen_labels:
                    signal_stock_count[lbl] += 1
                    seen_labels.add(lbl)
            days_dist[a["_dbefore"]] += 1
            for g in char_ngrams(a["subject"], 3):
                subject_ngrams[g] += 1

    # write report
    lines: list[str] = []
    lines.append(f"# {args.title}")
    lines.append("")
    lines.append(f"**樣本**：{len(candidates)} 檔 T0 落在 2025Q3 – 2026Q2、20 日內漲幅 50–200%、"
                 "T0 量比 3–15 倍的飆漲股。每檔取 T-30 到 T-1 天窗口的 MOPS 重大訊息。")
    lines.append("")

    # summary table
    total_ann = sum(len(s["anns"]) for s in per_stock)
    hit_stocks = sum(1 for s in per_stock if s["anns"])
    lines.append(f"- **命中率**：{hit_stocks}/{len(candidates)} 檔股票 T-30 天內存在重訊 "
                 f"（{100 * hit_stocks / len(candidates):.0f}%）")
    lines.append(f"- **總重訊數**：{total_ann}（平均每檔 {total_ann / len(candidates):.1f} 筆）")
    lines.append("")

    lines.append("## 訊號類型出現頻率（跨股票）")
    lines.append("")
    lines.append("| 訊號類型 | 出現於幾檔 | 覆蓋率 | 總筆數 |")
    lines.append("|---|---:|---:|---:|")
    for lbl, cnt in signal_stock_count.most_common():
        coverage = 100 * cnt / len(candidates)
        lines.append(f"| {lbl} | {cnt} | {coverage:.0f}% | {signal_ann_count[lbl]} |")
    lines.append("")

    lines.append("## 距離 T0 的天數分布（T-N 天）")
    lines.append("")
    lines.append("| 區間 | 筆數 |")
    lines.append("|---|---:|")
    buckets = [
        ("T-1 ~ T-3", 1, 3),
        ("T-4 ~ T-7", 4, 7),
        ("T-8 ~ T-14", 8, 14),
        ("T-15 ~ T-21", 15, 21),
        ("T-22 ~ T-30", 22, 30),
    ]
    for name, lo, hi in buckets:
        cnt = sum(v for d, v in days_dist.items() if lo <= d <= hi)
        lines.append(f"| {name} | {cnt} |")
    lines.append("")

    lines.append("## 主旨常見 3-gram（可作為關鍵字白名單種子）")
    lines.append("")
    lines.append("| 3-gram | 出現次數 |")
    lines.append("|---|---:|")
    for g, cnt in subject_ngrams.most_common(25):
        if cnt >= 3:
            lines.append(f"| `{g}` | {cnt} |")
    lines.append("")

    # per-stock detail
    lines.append("---")
    lines.append("## 逐檔詳情")
    for s in per_stock:
        c = s["c"]
        lines.append("")
        lines.append(
            f"### {c['symbol']} {c['name']}"
            f" — T0 {c['T0']}，20 日 +{c['return_pct']}%，量 ×{c['vol_ratio']}"
        )
        if not s["anns"]:
            lines.append("")
            lines.append("_（窗口內無重訊）_")
            continue
        lines.append("")
        lines.append("| 距離 T0 | 日期 | 分類 | 主旨 |")
        lines.append("|---|---|---|---|")
        for a in s["anns"]:
            subj = a["subject"].replace("|", "／").replace("\n", " ")
            if len(subj) > 60:
                subj = subj[:60] + "…"
            labels = " · ".join(a["_labels"])
            lines.append(f"| T-{a['_dbefore']} | {a['date']} | {labels} | {subj} |")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {output_path}")
    if args.summary_json:
        Path(args.summary_json).write_text(
            json.dumps(
                {
                    "n_stocks": len(candidates),
                    "hit_stocks": hit_stocks,
                    "total_announcements": total_ann,
                    "signal_stock_count": dict(signal_stock_count),
                    "signal_ann_count": dict(signal_ann_count),
                    "days_dist": {str(k): v for k, v in days_dist.items()},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"\nhit rate: {hit_stocks}/{len(candidates)} ({100*hit_stocks/len(candidates):.0f}%)")
    print(f"total announcements: {total_ann}")
    print("\ntop signals:")
    for lbl, cnt in signal_stock_count.most_common(10):
        print(f"  {cnt:3d} stocks | {lbl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
