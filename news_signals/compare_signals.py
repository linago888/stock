"""Compare PoC (surge group) vs Control group signal frequencies.

Reads two summary JSON files produced by aggregate_signals.py --summary-json
and produces:
- side-by-side signal coverage table
- coverage delta (PoC - Control) — the key "information gain" number
- lift ratio (PoC / Control)
- markdown report written to news_signals/comparison_report.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "news_signals" / "comparison_report.md"


def load(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def coverage(summary: dict, label: str) -> float:
    n = summary["n_stocks"]
    return summary["signal_stock_count"].get(label, 0) / n if n else 0.0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--poc", required=True, help="PoC summary JSON")
    ap.add_argument("--control", required=True, help="Control summary JSON")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    poc = load(args.poc)
    ctl = load(args.control)

    all_labels = set(poc["signal_stock_count"]) | set(ctl["signal_stock_count"])
    rows = []
    for lbl in all_labels:
        p_cov = coverage(poc, lbl)
        c_cov = coverage(ctl, lbl)
        delta = p_cov - c_cov
        lift = (p_cov / c_cov) if c_cov > 0 else (math.inf if p_cov > 0 else 0)
        rows.append(
            {
                "label": lbl,
                "poc_stocks": poc["signal_stock_count"].get(lbl, 0),
                "poc_cov": p_cov,
                "ctl_stocks": ctl["signal_stock_count"].get(lbl, 0),
                "ctl_cov": c_cov,
                "delta": delta,
                "lift": lift,
            }
        )
    rows.sort(key=lambda r: (-r["delta"], -r["poc_cov"]))

    lines: list[str] = []
    lines.append("# 飆漲組 vs 對照組 訊號比對")
    lines.append("")
    lines.append(
        f"- **飆漲組**：{poc['n_stocks']} 檔，"
        f"命中率 {poc['hit_stocks']}/{poc['n_stocks']} "
        f"({100 * poc['hit_stocks'] / poc['n_stocks']:.0f}%)，"
        f"平均 {poc['total_announcements'] / poc['n_stocks']:.1f} 筆重訊"
    )
    lines.append(
        f"- **對照組**：{ctl['n_stocks']} 檔（非飆漲、隨機時點、"
        f"確認 20 日內漲幅 ≤ 15%），命中率 "
        f"{ctl['hit_stocks']}/{ctl['n_stocks']} "
        f"({100 * ctl['hit_stocks'] / ctl['n_stocks']:.0f}%)，"
        f"平均 {ctl['total_announcements'] / ctl['n_stocks']:.1f} 筆重訊"
    )
    lines.append("")
    lines.append("## 訊號類型比對（依飆漲組 – 對照組 覆蓋率差排序）")
    lines.append("")
    lines.append("| 訊號類型 | 飆漲組 | 對照組 | Δ 覆蓋率 | 提升倍數 (lift) |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in rows:
        lift_str = "∞" if math.isinf(r["lift"]) else f"{r['lift']:.2f}×"
        arrow = "🔺" if r["delta"] > 0.1 else "🔻" if r["delta"] < -0.05 else "·"
        lines.append(
            f"| {arrow} {r['label']} | "
            f"{r['poc_stocks']}/{poc['n_stocks']} ({100*r['poc_cov']:.0f}%) | "
            f"{r['ctl_stocks']}/{ctl['n_stocks']} ({100*r['ctl_cov']:.0f}%) | "
            f"{100*r['delta']:+.0f}pp | {lift_str} |"
        )
    lines.append("")
    lines.append("## 解讀指引")
    lines.append("")
    lines.append("- **Δ 覆蓋率 (percentage points)**: 飆漲組覆蓋率 − 對照組覆蓋率。"
                 "正值大 = 該訊號在飆漲前確實比較常出現，具領先價值；"
                 "負值 = 反而在對照組更常見，多半是基準頻率高的常規公告；"
                 "接近 0 = 無資訊增益，需剔除或降權。")
    lines.append("")
    lines.append("- **提升倍數 (lift)**: 飆漲組覆蓋率 / 對照組覆蓋率。"
                 "lift ≥ 2× 是明顯有領先性的訊號。"
                 "但要看絕對覆蓋率 — 例如 3/20 vs 1/20 的 lift 是 3× 但樣本太少，僅供參考。")
    lines.append("")
    lines.append("- **命中率**：飆漲組明顯高於對照組 → 「飆漲前公司比較會發公告」的假設成立；"
                 "若差不多 → 需思考挑選規則或訊號類別是否設計得夠嚴。")

    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")

    print("\n== Top 10 signals by delta ==")
    for r in rows[:10]:
        print(f"  Δ{100*r['delta']:+5.0f}pp  {r['label']:40} "
              f"poc={r['poc_stocks']:2d} ctl={r['ctl_stocks']:2d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
