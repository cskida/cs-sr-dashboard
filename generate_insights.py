#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cs_sr_dashboard_data.json の集計結果から所見(insights)を自動生成して上書きするスクリプト。

etl_cs_dashboard.py を再実行すると insights は STATIC_INSIGHTS(手書きの固定文)で
上書きされてしまうため、その直後にこのスクリプトを実行して、最新の実績値に基づいた
文章に差し替える。日次自動更新のワークフローで使うことを想定している。

使い方:
    python3 generate_insights.py --data cs_sr_dashboard_data.json

生成する内容(既存の insights と同じ構造):
    period_label : 対象期間の表記
    overall      : 全社の直近傾向(数値入り)
    by_category  : カテゴリ別の所見(dict: カテゴリ名 -> 文章)
    by_location  : 拠点別の所見(dict: 拠点名 -> 文章)

数値はすべて JSON 内の rows / sr_major_rows から機械的に集計しており、
人間が文章を書き足す必要はない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

# 所見に載せる最小規模。出荷数がこれ未満のカテゴリ/拠点は母数が小さく率が暴れるため除外する。
MIN_SHIPPED_FOR_COMMENT = 300

# 所見に載せるカテゴリ数・拠点数の上限
MAX_CATEGORIES = 5
MAX_LOCATIONS = 6

METRIC_COLS = [
    "inquiry_count",
    "sr_count",
    "refund_amount",
    "refund_count",
    "sales_amount",
    "gross_profit",
    "question_count",
    "shipped_count",
    "junk_shipped_count",
]


def _load_rows(data: dict) -> pd.DataFrame:
    rows = data.get("rows") or []
    if not rows:
        raise SystemExit("[ERROR] rows が空です。ETLが正常に完了しているか確認してください。")
    df = pd.DataFrame(rows)
    for c in METRIC_COLS:
        if c not in df.columns:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if "year_month" not in df.columns:
        df["year_month"] = df["week_start"].astype(str).str.slice(0, 7)
    return df


def _pct(numerator: float, denominator: float) -> float:
    """百分率を返す。分母が0のときは0を返す(率の平均ではなく合計同士で割ること)。"""
    if not denominator:
        return 0.0
    return numerator / denominator * 100.0


def _agg(df: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """指標を合計し、率を「合計÷合計」で算出する。個々の月/行の率を平均してはいけない。"""
    if by is None:
        g = df[METRIC_COLS].sum().to_frame().T
    else:
        g = df.groupby(by)[METRIC_COLS].sum().reset_index()
    g["sr_rate"] = [_pct(s, sh) for s, sh in zip(g["sr_count"], g["shipped_count"])]
    g["refund_rate"] = [_pct(r, sh) for r, sh in zip(g["refund_count"], g["shipped_count"])]
    g["junk_rate"] = [_pct(j, sh) for j, sh in zip(g["junk_shipped_count"], g["shipped_count"])]
    g["profit_rate"] = [_pct(p, sa) for p, sa in zip(g["gross_profit"], g["sales_amount"])]
    return g


def _fmt_month(ym: str) -> str:
    """'2026-08' -> '2026年8月'"""
    try:
        y, m = ym.split("-")
        return f"{int(y)}年{int(m)}月"
    except Exception:
        return ym


def build_period_label(df: pd.DataFrame, data_through: str | None) -> str:
    months = sorted(df["year_month"].dropna().unique())
    if not months:
        return "期間不明"
    first, last = _fmt_month(months[0]), _fmt_month(months[-1])
    through = f"({data_through} まで)" if data_through else ""
    return f"{first}〜{last}{through} 全{len(months)}ヶ月"


def build_overall(df: pd.DataFrame, latest_ym: str, prev_ym: str | None) -> str:
    cur = _agg(df[df["year_month"] == latest_ym]).iloc[0]
    total = _agg(df).iloc[0]
    label = _fmt_month(latest_ym)

    parts = [
        f"{label}の実績は、出荷{int(cur['shipped_count']):,}点・売上{int(cur['sales_amount']):,}円に対し、"
        f"SR発生{int(cur['sr_count']):,}件(SR率{cur['sr_rate']:.2f}%)、"
        f"問合せ{int(cur['inquiry_count']):,}件、質問{int(cur['question_count']):,}件でした。"
    ]

    if prev_ym:
        prev = _agg(df[df["year_month"] == prev_ym]).iloc[0]
        d_sr = cur["sr_rate"] - prev["sr_rate"]
        d_refund = cur["refund_rate"] - prev["refund_rate"]
        direction = "改善" if d_sr < 0 else "悪化"
        parts.append(
            f"前月({_fmt_month(prev_ym)})比ではSR率が{abs(d_sr):.2f}ポイント{direction}し、"
            f"返金率は{prev['refund_rate']:.2f}%→{cur['refund_rate']:.2f}%"
            f"({'▲' if d_refund < 0 else '+'}{abs(d_refund):.2f}pt)となっています。"
        )

    parts.append(
        f"返金は{int(cur['refund_count']):,}件・{int(cur['refund_amount']):,}円(返金率{cur['refund_rate']:.2f}%)、"
        f"ジャンク出荷比率は{cur['junk_rate']:.2f}%、粗利率は{cur['profit_rate']:.2f}%です。"
    )
    parts.append(
        f"全期間累計では出荷{int(total['shipped_count']):,}点・SR率{total['sr_rate']:.2f}%・"
        f"返金率{total['refund_rate']:.2f}%が基準値となります。"
    )
    return "".join(parts)


def _comment(name: str, r: pd.Series, base: pd.Series, kind: str) -> str:
    """1カテゴリ/1拠点ぶんの所見文を組み立てる。全体平均との差分で特徴を言語化する。"""
    flags = []
    if r["sr_rate"] >= base["sr_rate"] * 1.3:
        flags.append("SR率が全体平均を大きく上回っています")
    elif r["sr_rate"] <= base["sr_rate"] * 0.7:
        flags.append("SR率は全体平均を下回り安定しています")
    if r["refund_rate"] >= base["refund_rate"] * 1.3:
        flags.append("返金率も高水準です")
    if r["junk_rate"] >= base["junk_rate"] * 1.5:
        flags.append("ジャンク出荷比率が突出しています")
    if not flags:
        flags.append("各指標とも全体平均並みで推移しています")

    return (
        f"出荷{int(r['shipped_count']):,}点・売上{int(r['sales_amount']):,}円。"
        f"SR率{r['sr_rate']:.2f}%・返金率{r['refund_rate']:.2f}%・"
        f"ジャンク出荷比率{r['junk_rate']:.2f}%・粗利率{r['profit_rate']:.2f}%。"
        + "、".join(flags)
        + "。"
    )


def build_group_insights(df: pd.DataFrame, col: str, limit: int) -> dict:
    base = _agg(df).iloc[0]
    g = _agg(df, by=col)
    g = g[g["shipped_count"] >= MIN_SHIPPED_FOR_COMMENT]
    if g.empty:
        return {}

    # 「特筆すべきもの」を優先: 全体平均からの乖離が大きい順。規模も加味する。
    g["deviation"] = (
        (g["sr_rate"] - base["sr_rate"]).abs() / max(base["sr_rate"], 0.01)
        + (g["refund_rate"] - base["refund_rate"]).abs() / max(base["refund_rate"], 0.01)
        + (g["junk_rate"] - base["junk_rate"]).abs() / max(base["junk_rate"], 0.01)
    )
    g["score"] = g["deviation"] * (g["shipped_count"] ** 0.3)
    g = g.sort_values("score", ascending=False).head(limit)

    return {str(row[col]): _comment(str(row[col]), row, base, col) for _, row in g.iterrows()}


def build_sr_major_note(data: dict, latest_ym: str) -> str:
    """SR大項目の内訳から、直近月に多い問題を1文で返す。データが無ければ空文字。"""
    rows = data.get("sr_major_rows") or []
    if not rows:
        return ""
    df = pd.DataFrame(rows)
    if "year_month" not in df.columns:
        df["year_month"] = df["week_start"].astype(str).str.slice(0, 7)
    df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(0)
    cur = df[df["year_month"] == latest_ym]
    if cur.empty:
        return ""
    top = cur.groupby("major")["count"].sum().sort_values(ascending=False).head(3)
    total = top.sum()
    if not total:
        return ""
    items = "、".join(f"{k}{int(v):,}件" for k, v in top.items())
    return f"{_fmt_month(latest_ym)}のSR大項目は{items}が上位です。"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="cs_sr_dashboard_data.json", help="集計結果JSONのパス")
    args = parser.parse_args()

    path = Path(args.data)
    data = json.loads(path.read_text(encoding="utf-8"))
    df = _load_rows(data)

    months = sorted(df["year_month"].dropna().unique())
    latest_ym = months[-1]
    prev_ym = months[-2] if len(months) >= 2 else None

    overall = build_overall(df, latest_ym, prev_ym)
    sr_note = build_sr_major_note(data, latest_ym)
    if sr_note:
        overall = overall + sr_note

    insights = {
        "period_label": build_period_label(df, data.get("data_through")),
        "overall": overall,
        "by_category": build_group_insights(df, "category", MAX_CATEGORIES),
        "by_location": build_group_insights(df, "location", MAX_LOCATIONS),
    }

    data["insights"] = insights
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[INFO] insights を再生成しました (対象月: {latest_ym})")
    print(f"[INFO] period_label: {insights['period_label']}")
    print(f"[INFO] by_category: {len(insights['by_category'])}件, by_location: {len(insights['by_location'])}件")
    print(f"\n{insights['overall']}\n")


if __name__ == "__main__":
    main()
