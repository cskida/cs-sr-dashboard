#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""etl_cs_dashboard.py の各集計ステージをチェックポイント(pickle)を挟みながら
個別に実行するためのランナー。45秒/呼び出しという実行環境の制約に対応するため、
main()と全く同じ関数呼び出し・同じ入出力になるようにし、最後のfinalizeステージで
main()と同一構造のJSONを書き出す。業務ロジックは一切変更していない。
"""
import json
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etl_cs_dashboard as m

CK_DIR = Path("/tmp/etl_checkpoints")
CK_DIR.mkdir(exist_ok=True)


def ck_path(name):
    return CK_DIR / f"{name}.pkl"


def save(name, obj):
    t0 = time.time()
    with open(ck_path(name), "wb") as f:
        pickle.dump(obj, f)
    print(f"  [save {name}] {time.time()-t0:.1f}s", flush=True)


def load(name):
    with open(ck_path(name), "rb") as f:
        return pickle.load(f)


def has(name):
    return ck_path(name).exists()


def run_stage(stage: str):
    t0 = time.time()

    if stage == "discover":
        backend = m.LocalCacheDriveBackend("drive_cache", m.FISCAL_ROOT_ID)
        weeks = m.discover_week_files(backend, m.FISCAL_ROOT_ID)
        print(f"discover: {time.time()-t0:.1f}s weeks={len(weeks)}", flush=True)
        save("weeks", weeks)

    elif stage == "category_master":
        weeks = load("weeks")
        r = m.build_product_category_master(weeks)
        print(f"category_master: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("category_master", r)

    elif stage == "cost_master":
        weeks = load("weeks")
        r = m.build_product_cost_master(weeks)
        print(f"cost_master: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("cost_master", r)

    elif stage == "status_master":
        weeks = load("weeks")
        r = m.build_product_status_master(weeks)
        print(f"status_master: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("status_master", r)

    elif stage == "shipping_fee_master":
        # A項目対応: 商品_出荷の「ヤフオク配送料」が"らくらく家財便"の行の実際の送料を
        # 受注_通常_出荷/受注_JPON_出荷から突合するためのマスタ。
        weeks = load("weeks")
        r = m.build_shipping_fee_master(weeks)
        print(f"shipping_fee_master: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("shipping_fee_master", r)

    elif stage == "cs_sr":
        weeks = load("weeks")
        stats = m.ExclusionStats()
        r = m.aggregate_cs_sr(weeks, stats)
        print(f"cs_sr: {time.time()-t0:.1f}s n={len(r)} cs_rows={stats.cs_rows} through_rows={stats.through_rows}", flush=True)
        save("cs_sr", r)
        save("stats_cs_sr", stats.cs_rows)
        save("stats_through_cs_sr", stats.through_rows)

    elif stage == "refund":
        weeks = load("weeks")
        cost_master = load("cost_master")
        stats = m.ExclusionStats()
        r = m.aggregate_refund(weeks, cost_master, stats)
        print(f"refund: {time.time()-t0:.1f}s n={len(r)} henkin_rows={stats.henkin_rows}", flush=True)
        save("refund", r)
        save("stats_henkin", stats.henkin_rows)

    elif stage == "question":
        weeks = load("weeks")
        category_master = load("category_master")
        stats = m.ExclusionStats()
        r = m.aggregate_question(weeks, category_master, stats)
        print(f"question: {time.time()-t0:.1f}s n={len(r)} shitsumon_rows={stats.shitsumon_rows}", flush=True)
        save("question", r)
        save("stats_shitsumon", stats.shitsumon_rows)

    elif stage == "shipped_sales_read":
        # 受注_通常_出荷/受注_JPON_出荷の全期間CSV読み込み+結合のみを行う(この部分が
        # shipped_sales ステージの実行時間の大半を占めるため、45秒/呼び出し対策として
        # 個別ステージに分離した)。
        weeks = load("weeks")
        r = m._read_and_merge_shipped_raw(weeks)
        print(f"shipped_sales_read: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("shipped_merged_raw", r)

    elif stage == "shipped_sales":
        weeks = load("weeks")
        category_master = load("category_master")
        cost_master = load("cost_master")
        status_master = load("status_master")
        merged_raw = load("shipped_merged_raw") if has("shipped_merged_raw") else None
        stats = m.ExclusionStats()
        r = m.aggregate_shipped_and_sales(weeks, category_master, cost_master, status_master, stats, merged_raw)
        print(f"shipped_sales: {time.time()-t0:.1f}s n={len(r)} juchu_rows={stats.juchu_rows}", flush=True)
        save("shipped_sales", r)
        save("stats_juchu", stats.juchu_rows)

    elif stage == "listed":
        weeks = load("weeks")
        stats = m.ExclusionStats()
        r = m.aggregate_listed(weeks, stats)
        print(f"listed: {time.time()-t0:.1f}s n={len(r)} shuppinmachi_rows={stats.shuppinmachi_rows}", flush=True)
        save("listed", r)
        save("stats_shuppinmachi", stats.shuppinmachi_rows)

    elif stage == "sr_major_rows":
        weeks = load("weeks")
        stats = m.ExclusionStats()
        r = m.build_sr_major_rows(weeks, stats)
        print(f"sr_major_rows: {time.time()-t0:.1f}s n={len(r)} through_rows={stats.through_rows}", flush=True)
        save("sr_major_rows", r)
        save("stats_through_sr_major", stats.through_rows)

    elif stage == "cause_rows":
        weeks = load("weeks")
        stats = m.ExclusionStats()
        r = m.build_cause_rows(weeks, stats)
        print(f"cause_rows: {time.time()-t0:.1f}s n={len(r)} through_rows={stats.through_rows}", flush=True)
        save("cause_rows", r)
        save("stats_through_cause", stats.through_rows)

    elif stage == "ship_date_master":
        weeks = load("weeks")
        r = m.build_ship_date_master(weeks)
        print(f"ship_date_master: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("ship_date_master", r)

    elif stage == "shukka_detail":
        weeks = load("weeks")
        stats = m.ExclusionStats()
        shipping_fee_master = load("shipping_fee_master") if has("shipping_fee_master") else m.build_shipping_fee_master(weeks)
        ship_date_master = load("ship_date_master") if has("ship_date_master") else m.build_ship_date_master(weeks)
        r = m.build_shukka_detail(weeks, stats, shipping_fee_master, ship_date_master)
        print(f"shukka_detail: {time.time()-t0:.1f}s n={len(r)} shukka_rows={stats.shukka_rows}", flush=True)
        save("shukka_detail", r)
        save("stats_shukka", stats.shukka_rows)

    elif stage == "product_attr_master":
        weeks = load("weeks")
        r = m.build_product_attr_master(weeks)
        print(f"product_attr_master: {time.time()-t0:.1f}s n={len(r)}", flush=True)
        save("product_attr_master", r)

    elif stage == "condition_price_variance":
        detail = load("shukka_detail")
        weeks = load("weeks")
        attr_master = load("product_attr_master") if has("product_attr_master") else m.build_product_attr_master(weeks)
        extra_cond, extra_band = m.aggregate_condition_price_metrics(weeks, attr_master)
        cond = m.build_condition_rows(detail, extra_cond)
        pb = m.build_price_band_rows(detail, extra_band)
        pv = m.build_profit_variance_rows(detail)
        print(f"condition/price_band/profit_variance: {time.time()-t0:.1f}s n={len(cond)}/{len(pb)}/{len(pv)}", flush=True)
        save("condition_rows", cond)
        save("price_band_rows", pb)
        save("profit_variance_rows", pv)
        vc = m.build_variance_breakdown_rows(detail, "condition")
        vb = m.build_variance_breakdown_rows(detail, "price_band")
        print(f"  粗利差異の分解: コンディション別{len(vc):,}行 / 価格帯別{len(vb):,}行", flush=True)
        save("variance_condition_rows", vc)
        save("variance_band_rows", vb)

    elif stage == "category_profit_deficit":
        weeks = load("weeks")
        detail = load("shukka_detail")
        cost_master = load("cost_master")
        cpd = m.build_category_profit_detail_rows(detail)
        deficit = m.build_deficit_rows(weeks, detail, cost_master)
        print(f"category_profit_detail/deficit: {time.time()-t0:.1f}s n={len(cpd)}/{len(deficit)}", flush=True)
        save("category_profit_detail_rows", cpd)
        save("deficit_rows", deficit)
        dm = m.build_deficit_mode_rows(weeks, detail, cost_master)
        print(f"  損益2軸(会計上の粗利/最終利益): {len(dm):,}行", flush=True)
        save("deficit_mode_rows", dm)

    elif stage == "customer_segments":
        # ⑨ SRリピーター・ロイヤルカスタマー分析。受注_通常_出荷の顧客情報(氏名・住所・
        # 電話番号・メールアドレス)でUnion-Find名寄せを行い、顧客単位の指標を算出する。
        # PIIを含む対応表は customer_lookup.csv にのみ書き出し、チェックポイント/JSONには
        # 匿名ラベルと数値指標だけを保存する。
        weeks = load("weeks")
        cost_master = load("cost_master")
        attr_master = load("product_attr_master") if has("product_attr_master") else None
        category_master = load("category_master") if has("category_master") else None
        rows, lookup, meta, detail_rows = m.build_customer_segment_rows(
            weeks, cost_master, attr_master, category_master)
        m.write_customer_lookup_csv(
            lookup, str(Path(__file__).resolve().parent / "customer_lookup.csv")
        )
        print(f"customer_segments: {time.time()-t0:.1f}s rows={len(rows)}", flush=True)
        for k, v in meta.items():
            print(f"  meta {k}: {v}", flush=True)
        save("customer_segment_rows", rows)
        save("customer_detail_rows", detail_rows)
        save("customer_segment_meta", meta)

    elif stage == "finalize":
        weeks = load("weeks")
        cs_sr = load("cs_sr")
        refund = load("refund")
        question = load("question")
        shipped_sales = load("shipped_sales")
        listed = load("listed")

        key_cols = ["week_start", "week_end", "year_month", "location", "category"]
        merged = cs_sr
        for other in (refund, question, shipped_sales, listed):
            merged = merged.merge(other, on=key_cols, how="outer")

        for col in m.METRIC_COLUMNS:
            if col not in merged.columns:
                merged[col] = 0
            merged[col] = merged[col].fillna(0)

        nonzero_mask = merged[m.METRIC_COLUMNS].abs().sum(axis=1) > 0
        merged = merged[nonzero_mask].copy()
        merged = merged.sort_values(["week_start", "location", "category"]).reset_index(drop=True)

        rows = []
        for _, r in merged.iterrows():
            rows.append(
                {
                    "week_start": r["week_start"],
                    "week_end": r["week_end"],
                    "year_month": r["year_month"],
                    "location": r["location"],
                    "category": r["category"],
                    "inquiry_count": int(r["inquiry_count"]),
                    "sr_count": int(r["sr_count"]),
                    "refund_amount": float(r["refund_amount"]),
                    "refund_count": int(r["refund_count"]),
                    "return_shipping_cost": float(r["return_shipping_cost"]),
                    "sales_amount": float(r["sales_amount"]),
                    "gross_profit": float(r["gross_profit"]),
                    "question_count": int(r["question_count"]),
                    "shipped_count": int(r["shipped_count"]),
                    "listed_count": int(r["listed_count"]),
                    "junk_shipped_count": int(r["junk_shipped_count"]),
                    "junk_listed_count": int(r["junk_listed_count"]),
                }
            )

        sr_major_rows = load("sr_major_rows")
        cause_rows = load("cause_rows")
        condition_rows = load("condition_rows")
        price_band_rows = load("price_band_rows")
        profit_variance_rows = load("profit_variance_rows")
        variance_condition_rows = load("variance_condition_rows") if has("variance_condition_rows") else []
        variance_band_rows = load("variance_band_rows") if has("variance_band_rows") else []
        category_profit_detail_rows = load("category_profit_detail_rows")
        deficit_rows = load("deficit_rows")
        deficit_mode_rows = load("deficit_mode_rows") if has("deficit_mode_rows") else []
        customer_segment_rows = load("customer_segment_rows") if has("customer_segment_rows") else []
        customer_detail_rows = load("customer_detail_rows") if has("customer_detail_rows") else []

        stats = m.ExclusionStats()
        stats.cs_rows = load("stats_cs_sr")
        stats.henkin_rows = load("stats_henkin")
        stats.shitsumon_rows = load("stats_shitsumon")
        stats.juchu_rows = load("stats_juchu")
        stats.shuppinmachi_rows = load("stats_shuppinmachi")
        stats.shukka_rows = load("stats_shukka")
        # 「スルー」除外は aggregate_cs_sr/aggregate_sr_major/aggregate_cause の3ステージそれぞれで
        # 独立した ExclusionStats インスタンスを使っているため、3つの保存値を合算する
        # (main()側は単一のstatsインスタンスを使い回すため自然に合算されるが、
        # run_stage.py はステージごとに別インスタンスなのでここで明示的に合算する)。
        stats.through_rows = (
            load("stats_through_cs_sr") + load("stats_through_sr_major") + load("stats_through_cause")
        )

        m.print_monthly_summary(merged)
        m.print_exclusion_stats(stats)
        print(f"\n[INFO] sr_major_rows={len(sr_major_rows)}件, cause_rows={len(cause_rows)}件")
        print(
            f"[INFO] condition_rows={len(condition_rows)}件, price_band_rows={len(price_band_rows)}件, "
            f"profit_variance_rows={len(profit_variance_rows)}件"
        )
        print(
            f"[INFO] category_profit_detail_rows={len(category_profit_detail_rows)}件, "
            f"deficit_rows={len(deficit_rows)}件"
        )
        print(f"[INFO] customer_segment_rows={len(customer_segment_rows)}件")
        detail = load("shukka_detail")
        if not detail.empty:
            v = detail["variance"]
            print(
                f"[INFO] 粗利差異(全期間合計): 件数={len(v)}, 上振れ={int((v>0).sum())}件/{v[v>0].sum():,.0f}円, "
                f"下振れ={int((v<0).sum())}件/{v[v<0].sum():,.0f}円, 差異合計={v.sum():,.0f}円"
            )

        data_through = m.compute_data_through(weeks)

        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fiscal_year_label": m.FISCAL_YEAR_LABEL,
            "fiscal_year_start": m.FISCAL_YEAR_START,
            "data_through": data_through,
            "rows": rows,
            "sr_major_rows": sr_major_rows,
            "cause_rows": cause_rows,
            "condition_rows": condition_rows,
            "price_band_rows": price_band_rows,
            "profit_variance_rows": profit_variance_rows,
            "variance_condition_rows": variance_condition_rows,
            "variance_band_rows": variance_band_rows,
            "category_profit_detail_rows": category_profit_detail_rows,
            "deficit_rows": deficit_rows,
            "deficit_mode_rows": deficit_mode_rows,
            "customer_segment_rows": customer_segment_rows,
            "customer_detail_rows": customer_detail_rows,
            "insights": m.STATIC_INSIGHTS,
        }

        out_path = Path(str(Path(__file__).resolve().parent / "cs_sr_dashboard_data.json"))
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[INFO] 出力完了: {out_path} (rows={len(rows)})", flush=True)

        print(f"[INFO] 検出した週フォルダ数: {len(weeks)}", flush=True)
        for w in weeks:
            found = sorted(w.files.keys())
            print(f"  - {w.week_start}~{w.week_end}: {found}", flush=True)

    else:
        print(f"UNKNOWN STAGE: {stage}")
        sys.exit(1)


if __name__ == "__main__":
    run_stage(sys.argv[1])
