#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ダッシュボードの週次自動更新スクリプト(GitHub Actionsから実行される本体)。

処理の流れ:
  1. Google Drive の 21期フォルダから週次CSVを取得し、日次で集計する(21期のみ)
  2. リポジトリに置いてある 20期の凍結データ(data_20th_frozen.json.gz)と結合する
  3. 所見(insights)を最新の数値から自動生成する
  4. cs_sr_dashboard_data.json を書き出し、build_dashboard.py で index.html を生成する

20期(2025年7月〜2026年6月)は確定済みなので毎回読み直さない。これにより実行時間と
ダウンロード量を大幅に削減している(20期のCSVは約700MB)。

使い方:
    python3 update_dashboard.py --credentials service_account.json
    python3 update_dashboard.py --mode local --local-dir drive_cache   # 手元での動作確認用
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

import etl_cs_dashboard as m
import auto_insights

FY21_ROOT_ID = "1Ogk8yQsEEx_EpvEjf6lZmGflXwfrQ8iP"  # 「13.質問・SR分析　データ蓄積」内の21thフォルダ
FY21_START = "2026-07-01"


def collect_21st(backend) -> dict:
    """21期の週次CSVから、ダッシュボードが使う各行配列を日次粒度で作る。"""
    stats = m.ExclusionStats()
    weeks = m.discover_week_files(backend, FY21_ROOT_ID)
    weeks = [w for w in weeks if w.week_end >= FY21_START]
    if not weeks:
        raise SystemExit("21期の週フォルダが1つも見つかりませんでした。フォルダIDと共有設定を確認してください。")
    print(f"[INFO] 21期の週フォルダ: {len(weeks)}件 (最新 {max(w.week_end for w in weeks)})", flush=True)

    category_master = m.build_product_category_master(weeks)
    cost_master = m.build_product_cost_master(weeks)
    status_master = m.build_product_status_master(weeks)
    attr_master = m.build_product_attr_master(weeks)
    shipping_fee_master = m.build_shipping_fee_master(weeks)
    ship_date_master = m.build_ship_date_master(weeks)

    # run_stage.py の finalize と同じ手順で日次×拠点×カテゴリの行を組み立てる
    cs_sr = m.aggregate_cs_sr(weeks, stats)
    refund = m.aggregate_refund(weeks, cost_master, stats)
    question = m.aggregate_question(weeks, category_master, stats)
    shipped = m.aggregate_shipped_and_sales(weeks, category_master, cost_master, status_master, stats)
    listed = m.aggregate_listed(weeks, stats)

    key_cols = ["week_start", "week_end", "year_month", "location", "category"]
    merged = cs_sr
    for other in (refund, question, shipped, listed):
        merged = merged.merge(other, on=key_cols, how="outer")
    for col in m.METRIC_COLUMNS:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = merged[col].fillna(0)
    merged = merged[merged[m.METRIC_COLUMNS].abs().sum(axis=1) > 0].copy()
    merged = merged.sort_values(["week_start", "location", "category"]).reset_index(drop=True)
    int_cols = {"inquiry_count", "sr_count", "refund_count", "question_count",
                "shipped_count", "listed_count", "junk_shipped_count", "junk_listed_count"}
    rows = [
        {**{k: r[k] for k in key_cols},
         **{c: (int(r[c]) if c in int_cols else float(r[c])) for c in m.METRIC_COLUMNS}}
        for _, r in merged.iterrows()
    ]

    detail = m.build_shukka_detail(weeks, stats, shipping_fee_master, ship_date_master)
    extra_cond, extra_band = m.aggregate_condition_price_metrics(weeks, attr_master)

    out = {
        "rows": rows,
        "sr_major_rows": m.build_sr_major_rows(weeks, stats),
        "cause_rows": m.build_cause_rows(weeks, stats),
        "condition_rows": m.build_condition_rows(detail, extra_cond),
        "price_band_rows": m.build_price_band_rows(detail, extra_band),
        "profit_variance_rows": m.build_profit_variance_rows(detail),
        "category_profit_detail_rows": m.build_category_profit_detail_rows(detail),
        "deficit_rows": m.build_deficit_rows(weeks, detail, cost_master),
        "data_through": m.compute_data_through(weeks),
    }
    for k, v in out.items():
        if isinstance(v, list):
            print(f"[INFO]   21期 {k}: {len(v):,}行", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["drive", "local"], default="drive")
    ap.add_argument("--credentials", default=None, help="サービスアカウントJSONのパス(driveモード)")
    ap.add_argument("--local-dir", default="drive_cache", help="localモードで読むディレクトリ")
    ap.add_argument("--frozen", default="data_20th_frozen.json.gz")
    ap.add_argument("--output", default="cs_sr_dashboard_data.json")
    args = ap.parse_args()

    if args.mode == "drive":
        backend = m.LiveDriveBackend(args.credentials)
    else:
        backend = m.LocalCacheDriveBackend(args.local_dir, FY21_ROOT_ID)

    new = collect_21st(backend)

    frozen_path = Path(args.frozen)
    if not frozen_path.exists():
        raise SystemExit(f"20期の凍結データが見つかりません: {frozen_path}")
    frozen = json.loads(gzip.decompress(frozen_path.read_bytes()).decode("utf-8"))
    print(f"[INFO] 20期の凍結データを読み込み: {sum(len(v) for v in frozen.values() if isinstance(v, list)):,}行", flush=True)

    data = {
        "generated_at": m.datetime.now(m.timezone.utc).isoformat(),
        "fiscal_year_label": m.FISCAL_YEAR_LABEL,
        "fiscal_year_start": m.FISCAL_YEAR_START,
        "data_through": new.pop("data_through"),
    }
    for key in ["rows", "sr_major_rows", "cause_rows", "condition_rows", "price_band_rows",
                "profit_variance_rows", "category_profit_detail_rows", "deficit_rows"]:
        data[key] = (frozen.get(key) or []) + (new.get(key) or [])
        print(f"[INFO] {key}: 20期{len(frozen.get(key) or []):,} + 21期{len(new.get(key) or []):,} = {len(data[key]):,}行")

    # ⑧顧客セグメントは全期間の名寄せが必要で21期だけでは作れないため、凍結データがあれば
    # そのまま引き継ぐ(無ければ空。ページ自体は空表示になるだけで他ページには影響しない)。
    data["customer_segment_rows"] = frozen.get("customer_segment_rows") or []

    data["insights"] = auto_insights.build_insights(data)
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] 出力完了: {args.output} (data_through={data['data_through']})", flush=True)

    subprocess.run([sys.executable, "build_dashboard.py"], check=True)
    html = Path("cs_sr_dashboard.html")
    Path("index.html").write_bytes(html.read_bytes())
    print(f"[INFO] index.html を更新しました ({html.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
