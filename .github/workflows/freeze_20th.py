#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""20期(終了済み)の集計結果を「凍結データ」として月次に丸めて書き出すスクリプト。

20期(2025年7月〜2026年6月)はすでに確定しており、今後変わることはない。
毎週の自動更新で20期のCSV(約700MB)を読み直すのは時間もコストも無駄なので、
一度だけこのスクリプトで月次集計に丸めた JSON を作り、GitHubリポジトリに置いて使い回す。

    python3 freeze_20th.py --input cs_sr_dashboard_data.json --output data_20th_frozen.json.gz

以降の週次自動更新では、21期のCSVだけをGoogle Driveから読んで日次集計し、
この凍結データと結合してダッシュボードを生成する(merge_and_build.py)。
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

# 21期の開始日。これより前の行を「20期(凍結対象)」として月次に丸める。
FY21_START = "2026-07-01"

# 合算してはいけない列(次元 or 合算後に再計算する派生値)
DIM_EXTRA = {"price_band_sort"}
DERIVED = {
    "category_profit_detail_rows": {
        "avg_lead_days": lambda a: (a["_lead_total"] / a["count"]) if a.get("count") else 0,
        "margin_rate": lambda a: (a["gross_profit"] / a["sales_amount"]) if a.get("sales_amount") else 0,
        "avg_sale_price": lambda a: (a["sales_amount"] / a["count"]) if a.get("count") else 0,
        "avg_profit_price": lambda a: (a["gross_profit"] / a["count"]) if a.get("count") else 0,
    },
    "deficit_rows": {
        "avg_deficit_per_item": lambda a: (a["total_deficit"] / a["count"]) if a.get("count") else 0,
    },
}


def collapse_to_month(key: str, rows: list[dict]) -> list[dict]:
    """week_start が FY21_START より前の行だけを、同じ月・同じ次元でまとめる。"""
    if not rows or not isinstance(rows[0], dict) or "week_start" not in rows[0]:
        return []
    cols = list(rows[0].keys())
    derived = DERIVED.get(key, {})
    dims = [c for c in cols if isinstance(rows[0][c], str) or c in DIM_EXTRA]
    metrics = [c for c in cols if c not in dims and c not in derived]
    buckets: dict[tuple, dict] = {}
    for r in rows:
        ws = r["week_start"]
        if ws >= FY21_START:
            continue  # 21期は凍結しない(毎週作り直す)
        month_start = ws[:7] + "-01"

        def dim_val(c):
            if c in ("week_start", "week_end"):
                return month_start
            if c == "year_month":
                return ws[:7]
            return r[c]

        dim_vals = tuple(dim_val(c) for c in dims)
        acc = buckets.get(dim_vals)
        if acc is None:
            acc = {c: dim_val(c) for c in dims}
            for c in metrics:
                acc[c] = 0
            if key == "category_profit_detail_rows":
                acc["_lead_total"] = 0.0
            buckets[dim_vals] = acc
        for c in metrics:
            acc[c] += r.get(c) or 0
        if key == "category_profit_detail_rows":
            acc["_lead_total"] += (r.get("avg_lead_days") or 0) * (r.get("count") or 0)
    out = []
    for acc in buckets.values():
        for c, fn in derived.items():
            acc[c] = fn(acc)
        acc.pop("_lead_total", None)
        out.append({c: acc.get(c, 0) for c in cols})
    return out


def _key_of(r, dims):
    return tuple(r.get(c) for c in dims)


def boundary_rows(key: str, full: list[dict], live: list[dict]) -> list[dict]:
    """20期のファイルにしか入っていない「21期日付の記録」を抽出する。

    20期の半月ファイルは登録日で切り出されているため、6月に登録され7月以降に
    返金・対応された記録は6月のファイルに入っている(実データでSR137件・返金127件を確認)。
    週次の自動更新は21期のファイルしか読まないので、この分をここで取り出して
    凍結データに含めておかないと、7月以降の集計から恒久的に欠落してしまう。

    full(20期+21期の全ファイルで集計した結果)から live(21期ファイルのみの結果)を
    同じキーで引き算し、差分(=20期ファイル由来の寄与)を返す。
    """
    if not full:
        return []
    cols = list(full[0].keys())
    derived = DERIVED.get(key, {})
    dims = [c for c in cols if isinstance(full[0][c], str) or c in DIM_EXTRA]
    metrics = [c for c in cols if c not in dims and c not in derived]

    live_map = {}
    for r in live:
        if r["week_start"] >= FY21_START:
            live_map[_key_of(r, dims)] = r

    out, negatives = [], 0
    for r in full:
        if r["week_start"] < FY21_START:
            continue
        lr = live_map.get(_key_of(r, dims))
        rec = {c: r[c] for c in dims}
        has_value = False
        for c in metrics:
            # 差はマイナスになることもある(重複除去の採用行が変わるケース)。
            # 合算時に正しく打ち消し合うよう、0に丸めずそのまま保持する。
            diff = (r.get(c) or 0) - ((lr.get(c) or 0) if lr else 0)
            if diff < 0:
                negatives += 1
            rec[c] = diff
            if diff:
                has_value = True
        if not has_value:
            continue
        if key == "category_profit_detail_rows":
            rec["_lead_total"] = (r.get("avg_lead_days") or 0) * rec.get("count", 0)
        for c, fn in derived.items():
            rec[c] = fn(rec)
        rec.pop("_lead_total", None)
        out.append({c: rec.get(c, 0) for c in cols})
    if negatives:
        print(f"    ※{key}: 差がマイナスの値が{negatives}件ありました(そのまま保持し合算で相殺します)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="cs_sr_dashboard_data.json")
    ap.add_argument("--live21", default=None,
                    help="21期のファイルだけで集計した結果のJSON。指定すると、20期ファイルにしか"
                         "入っていない21期日付の記録(境界分)を凍結データに含める")
    ap.add_argument("--output", default="data_20th_frozen.json.gz")
    args = ap.parse_args()

    live = json.loads(Path(args.live21).read_text(encoding="utf-8")) if args.live21 else None

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    frozen = {"fiscal_year_start": data.get("fiscal_year_start"), "fy21_start": FY21_START}
    total_before = total_after = 0
    for k, v in data.items():
        if not isinstance(v, list) or not v or not isinstance(v[0], dict):
            continue
        if "week_start" not in v[0]:
            # ⑧顧客セグメントは全期間の名寄せ結果で、期間で切り分けられない。
            # 週次の自動更新では作り直せないため、この凍結データにそのまま同梱して引き継ぐ
            # (更新したい場合はこの freeze_20th.py を再実行して凍結データを作り直す)。
            frozen[k] = v
            print(f"  {k}: {len(v):,}行 (そのまま同梱)")
            continue
        before = [r for r in v if r["week_start"] < FY21_START]
        after = collapse_to_month(k, v)
        extra = boundary_rows(k, v, live.get(k) or []) if live else []
        total_before += len(before)
        total_after += len(after) + len(extra)
        frozen[k] = after + extra
        note = f" + 境界分{len(extra):,}行" if extra else ""
        print(f"  {k}: {len(before):,}行 -> {len(after):,}行{note}")

    payload = json.dumps(frozen, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out = Path(args.output)
    out.write_bytes(gzip.compress(payload, 9))
    print(f"\n20期の凍結データを書き出しました: {out} ({out.stat().st_size / 1024 / 1024:.2f} MB)")
    print(f"合計 {total_before:,}行 -> {total_after:,}行")


if __name__ == "__main__":
    main()
