#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ダッシュボード冒頭の所見(insights)を、集計結果から自動生成するモジュール。

これまで所見は毎週手で書き直していたが、自動更新を担当者が誰でも回せるようにするため、
最新月の実績値から機械的に文章を組み立てる。数値はすべて cs_sr_dashboard_data.json の
rows / sr_major_rows から計算しており、率の定義はダッシュボード本体と同一:
    SR率 = SR件数 ÷ 出荷商品数
    返金率(件数) = 返金件数 ÷ 出荷商品数
    ジャンク出荷比率 = ジャンク出荷数 ÷ 出荷商品数
    質問率 = 質問数 ÷ 出品数
    粗利率 = 粗利 ÷ 売上金額
"""
from __future__ import annotations

from collections import defaultdict

NUM_FIELDS = [
    "inquiry_count", "sr_count", "refund_amount", "refund_count", "return_shipping_cost",
    "sales_amount", "gross_profit", "question_count", "shipped_count", "listed_count",
    "junk_shipped_count", "junk_listed_count",
]


def _acc():
    return {f: 0 for f in NUM_FIELDS}


def _add(dst, row):
    for f in NUM_FIELDS:
        dst[f] += row.get(f) or 0


def _rates(a):
    ship = a["shipped_count"]
    return {
        "sr_rate": a["sr_count"] / ship * 100 if ship else 0,
        "refund_rate": a["refund_count"] / ship * 100 if ship else 0,
        "junk_rate": a["junk_shipped_count"] / ship * 100 if ship else 0,
        "question_rate": a["question_count"] / a["listed_count"] * 100 if a["listed_count"] else 0,
        "margin": a["gross_profit"] / a["sales_amount"] * 100 if a["sales_amount"] else 0,
    }


def _fmt_int(n):
    return f"{int(round(n)):,}"


def build_insights(data: dict) -> dict:
    rows = data.get("rows") or []
    if not rows:
        return {"period_label": "", "overall": "データがありません。", "by_category": "", "by_location": ""}

    by_month = defaultdict(_acc)
    for r in rows:
        _add(by_month[r["year_month"]], r)
    months = sorted(by_month)
    cur_m = months[-1]
    cur, curR = by_month[cur_m], _rates(by_month[cur_m])
    prevs = [(m, _rates(by_month[m])) for m in months[-4:-1]]

    # ---- 全体所見 ----
    trend = "、".join(f"{m[5:7].lstrip('0')}月{r['sr_rate']:.2f}%" for m, r in prevs)
    y, mm = cur_m.split("-")
    lines = [
        f"{y}年{mm.lstrip('0')}月は出荷{_fmt_int(cur['shipped_count'])}件に対しSR{_fmt_int(cur['sr_count'])}件で、"
        f"SR率は{curR['sr_rate']:.2f}%です(直近の推移: {trend})。"
        f"問合せ{_fmt_int(cur['inquiry_count'])}件、質問{_fmt_int(cur['question_count'])}件"
        f"(出品{_fmt_int(cur['listed_count'])}件に対し質問率{curR['question_rate']:.2f}%)、"
        f"返金{_fmt_int(cur['refund_count'])}件・{_fmt_int(cur['refund_amount'])}円です。",
        f"ジャンク出荷比率は{curR['junk_rate']:.2f}%、粗利率は{curR['margin']:.1f}%。",
    ]
    if prevs:
        pr = prevs[-1][1]
        d = curR["sr_rate"] - pr["sr_rate"]
        direction = "改善" if d < 0 else ("悪化" if d > 0 else "横ばい")
        lines.append(f"前月比ではSR率が{abs(d):.2f}ポイントの{direction}、"
                     f"ジャンク出荷比率は{curR['junk_rate'] - pr['junk_rate']:+.2f}ポイント、"
                     f"粗利率は{curR['margin'] - pr['margin']:+.1f}ポイントです。")

    majors = defaultdict(int)
    for r in data.get("sr_major_rows") or []:
        if r.get("year_month") == cur_m:
            majors[r["major"]] += r.get("count") or 0
    if majors:
        tot = sum(majors.values())
        top = sorted(majors.items(), key=lambda x: -x[1])[:3]
        lines.append("SRの内訳は" + "、".join(f"{k}{v}件({v / tot * 100:.1f}%)" for k, v in top) + "が中心です。")

    # ---- カテゴリ別 / 拠点別 ----
    def dim_text(dim, min_ship, top_n):
        agg = defaultdict(_acc)
        for r in rows:
            if r["year_month"] == cur_m:
                _add(agg[r[dim]], r)
        items = [(k, v, _rates(v)) for k, v in agg.items() if v["shipped_count"] >= min_ship]
        if not items:
            return ""
        out = []
        for k, v, R in sorted(items, key=lambda x: -x[2]["sr_rate"])[:top_n]:
            note = []
            if R["sr_rate"] >= curR["sr_rate"] * 1.3:
                note.append("SR率が全体平均を大きく上回っており要注意")
            if R["junk_rate"] >= 25:
                note.append("ジャンク比率が高く仕入れ状態の確認が必要")
            if R["margin"] <= 35:
                note.append("粗利率が低く収益性に課題")
            out.append(
                f"{k}: 出荷{_fmt_int(v['shipped_count'])}件、SR率{R['sr_rate']:.2f}%、"
                f"返金率{R['refund_rate']:.2f}%、ジャンク比率{R['junk_rate']:.1f}%、粗利率{R['margin']:.1f}%"
                + ("。" + "、".join(note) + "。" if note else "。")
            )
        best = sorted(items, key=lambda x: x[2]["sr_rate"])[0]
        out.append(
            f"{best[0]}: 出荷{_fmt_int(best[1]['shipped_count'])}件でSR率{best[2]['sr_rate']:.2f}%と最も低く、"
            f"粗利率{best[2]['margin']:.1f}%。良好な運用ができている区分です。"
        )
        return "\n".join(out)

    return {
        "period_label": f"{months[0][:4]}年{months[0][5:7].lstrip('0')}月〜{y}年{mm.lstrip('0')}月"
                        f"(データ最終日: {data.get('data_through', '')})",
        "overall": "".join(lines),
        "by_category": dim_text("category", 150, 5),
        "by_location": dim_text("location", 100, 5),
    }
