import json
import gzip
import base64

with open('cs_sr_dashboard_data.json', encoding='utf-8') as f:
    data = json.load(f)

# gzip圧縮対応: JSON文字列をコンパクト(区切り文字を最小化)にしてからgzip圧縮し、base64で
# テキスト化してHTMLに埋め込む。Coworkアーティファクトの10MBアップロード上限に収めるため
# (フルデータは~19-24MB程度あるが、テキストなのでgzipで10倍以上圧縮できる)。
# ---------------------------------------------------------------------------
# 軽量化: 終了済みの20期(21期開始日より前)は月次に丸めてから埋め込む。
# 週の起点切替が必要なのは進行中の21期だけなので、過去期を日次で持ち続ける必要がない。
# 月次/四半期/半期/通期の集計値は丸め前と完全に一致する(同じ月の日次行を合算するだけ)。
# 平均・率の列(avg_*/margin_rate)は単純合算できないため、合算後に定義どおり再計算する。
# ---------------------------------------------------------------------------
DAILY_FROM = data.get('fiscal_year_start') or '2026-07-01'

# 合算してはいけない列(次元 or 合算後に再計算する派生値)
_DIM_EXTRA = {'price_band_sort'}
_DERIVED = {
    'category_profit_detail_rows': {
        'avg_lead_days': lambda a: (a['_lead_days_total'] / a['count']) if a.get('count') else 0,
        'margin_rate': lambda a: (a['gross_profit'] / a['sales_amount']) if a.get('sales_amount') else 0,
        'avg_sale_price': lambda a: (a['sales_amount'] / a['count']) if a.get('count') else 0,
        'avg_profit_price': lambda a: (a['gross_profit'] / a['count']) if a.get('count') else 0,
    },
    'deficit_rows': {
        'avg_deficit_per_item': lambda a: (a['total_deficit'] / a['count']) if a.get('count') else 0,
    },
}


# 1行=1商品の明細。合算すると意味が壊れるうえ出荷日も失われるので丸めない。
_ATOMIC_KEYS = {'item_detail_rows'}


def _collapse_old_period_to_month(key, rows):
    """week_start が DAILY_FROM より前の行を、同じ月・同じ次元でまとめて1行にする。"""
    if key in _ATOMIC_KEYS:
        return rows
    if not rows or not isinstance(rows[0], dict) or 'week_start' not in rows[0]:
        return rows
    cols = list(rows[0].keys())
    derived = _DERIVED.get(key, {})
    dims = [c for c in cols
            if isinstance(rows[0][c], str) or c in _DIM_EXTRA]
    metrics = [c for c in cols if c not in dims and c not in derived]
    kept, buckets = [], {}
    for r in rows:
        ws = r['week_start']
        if ws >= DAILY_FROM:
            kept.append(r)
            continue
        month_start = ws[:7] + '-01'
        dim_vals = tuple((month_start if c in ('week_start', 'week_end') else
                          (ws[:7] if c == 'year_month' else r[c])) for c in dims)
        acc = buckets.get(dim_vals)
        if acc is None:
            acc = {c: (month_start if c in ('week_start', 'week_end') else
                       (ws[:7] if c == 'year_month' else r[c])) for c in dims}
            for c in metrics:
                acc[c] = 0
            if key == 'category_profit_detail_rows':
                acc['_lead_days_total'] = 0.0
            buckets[dim_vals] = acc
        for c in metrics:
            acc[c] += r.get(c) or 0
        if key == 'category_profit_detail_rows':
            acc['_lead_days_total'] += (r.get('avg_lead_days') or 0) * (r.get('count') or 0)
    out = []
    for acc in buckets.values():
        for c, fn in derived.items():
            acc[c] = fn(acc)
        acc.pop('_lead_days_total', None)
        out.append({c: acc.get(c, 0) for c in cols})
    return out + kept


_before = sum(len(v) for v in data.values() if isinstance(v, list))
# 全体をまとめて作り直すとデータが一瞬2重にメモリへ載るため、キーごとに置き換える。
for _k in list(data.keys()):
    _v = data[_k]
    if isinstance(_v, list):
        data[_k] = _collapse_old_period_to_month(_k, _v)
        del _v
_after = sum(len(v) for v in data.values() if isinstance(v, list))
data['daily_from'] = DAILY_FROM
print(f'[INFO] 20期を月次に丸め: 合計行数 {_before:,} -> {_after:,} ({_after/_before*100:.1f}%)')

# 日次粒度化により行数が約8.6倍(9,771 -> 84,300行)に増えたため、埋め込み前に
# 「列名リスト + 値の配列」形式(_c/_d)へ変換してサイズを圧縮する。
# 1) 行ごとに繰り返されるキー名を1回だけ持つ
# 2) week_end/year_month は week_start から復元できるので落とす(日次なので week_end == week_start)
# 3) 金額・件数の小数は円単位に丸める(表示は整数のみ)
# JS側の rehydrateRows() でオブジェクト配列に戻すので、以降の集計・描画コードは一切変更不要。
def _slim(rows):
    if not rows or not isinstance(rows[0], dict):
        return rows
    drop = {'week_end', 'year_month'} if 'week_start' in rows[0] else set()
    cols = [c for c in rows[0].keys() if c not in drop]
    out = []
    for r in rows:
        rec = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, float):
                v = 0 if v != v else int(round(v))
            rec.append(v)
        out.append(rec)
    return {'_c': cols, '_d': out}


data = {k: (_slim(v) if isinstance(v, list) else v) for k, v in data.items()}
data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
_data_json_bytes = data_json.encode('utf-8')
_compressed = gzip.compress(_data_json_bytes, compresslevel=9)
data_json_gz_b64 = base64.b64encode(_compressed).decode('ascii')
print(
    f'[INFO] data_json: {len(_data_json_bytes):,} bytes -> gzip {len(_compressed):,} bytes '
    f'-> base64 {len(data_json_gz_b64):,} bytes (圧縮率 {len(_compressed)/len(_data_json_bytes)*100:.2f}%)'
)

html = r'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CS・SR分析ダッシュボード</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js" integrity="sha384-iU8HYtnGQ8Cy4zl7gbNMOhsDTTKX02BTXptVP/vqAWIaTfM7isw76iyZCsjL2eVi" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/gridjs.umd.js" integrity="sha384-/XXDzxe4FsGiAe50i/u9pY/Vy/uX654MHB1xoc1BJNnH1WXHhqHga9g3q5tF4gj7" crossorigin="anonymous"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/theme/mermaid.min.css" integrity="sha384-jZvDSsmGB9oGGT/4l9bHXGoAv1OxvG/cFmSo0dZaSqmBgvQTKDBFAMftlXTmMbNW" crossorigin="anonymous">
<style>
  :root{ color-scheme: light; }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; }
  body {
    margin: 0; padding: 24px; background: #f5f6f8; color: #1c1e21;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", "Segoe UI", sans-serif;
    font-size: 14px;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 0 0 12px; color:#333; }
  h3 { font-size: 13px; margin: 0 0 10px; color:#444; font-weight:600; }
  h4 { font-size: 12px; margin: 0 0 8px; color:#555; font-weight:600; }
  .sub { color:#666; font-size:12.5px; margin-bottom: 16px; }
  .card {
    background:#fff; border:1px solid #e3e5e8; border-radius:10px; padding:16px 18px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
  }
  .page-nav { display:flex; gap:4px; margin-bottom:18px; background:#12203f; border-radius:10px; padding:6px; flex-wrap:wrap; }
  .page-nav-btn {
    padding:10px 20px; border:none; background:none; color:#a9b7d6; font-size:13px; font-weight:700;
    cursor:pointer; border-radius:8px;
  }
  .page-nav-btn.active { background:#2455c9; color:#fff; }
  .page-section { display:none; }
  .page-section.active { display:block; }
  /* ⑨ページのセグメント切替タブ */
  .seg-tabs { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
  .seg-tab-btn {
    padding:10px 22px; border:1px solid #c9dbfa; background:#fff; color:#2455c9; font-size:13px;
    font-weight:700; cursor:pointer; border-radius:8px;
  }
  .seg-tab-btn.active { background:#2455c9; color:#fff; border-color:#2455c9; }
  .controls {
    display:flex; flex-wrap:wrap; gap:14px; align-items:flex-end; margin-bottom:18px;
    background:#fff; border:1px solid #e3e5e8; border-radius:10px; padding:14px 18px;
  }
  .ctl { display:flex; flex-direction:column; gap:4px; }
  .ctl label { font-size:11.5px; color:#666; }
  .ctl select {
    padding:6px 8px; border:1px solid #8fa8e0; border-radius:6px; font-size:13px; min-width:120px;
    background:#a6bff6; color:#12203f; font-weight:600;
  }
  .ctl-loc, .ctl-cat { display:none; }
  .ctl-loc.visible, .ctl-cat.visible { display:flex; }
  .kpi-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap:14px; margin-bottom:20px; }
  .kpi-card { padding:14px 16px; background:#f4cccc; border-color:#e7b3b3; }
  .kpi-title { font-size:12px; color:#7a4a4a; margin-bottom:8px; font-weight:600; }
  .kpi-row { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:4px; flex-wrap:wrap; }
  .kpi-main { font-size:21px; font-weight:700; }
  .kpi-rate { font-size:12.5px; color:#5c3a3a; }
  .kpi-delta { font-size:11.5px; margin-top:4px; }
  .up { color:#c0392b; } .down { color:#2471a3; } .flat { color:#888; }
  .insight-box { background:#eef4ff; border:1px solid #c9dbfa; border-radius:10px; padding:14px 18px; margin-bottom:20px; }
  .insight-box h3 { color:#1c2b4a; }
  .insight-meta { font-size:11.5px; color:#5a6b8c; margin-bottom:8px; }
  .insight-text { font-size:13px; line-height:1.8; color:#1c2b4a; white-space:pre-wrap; }
  .category-insights { display:flex; flex-direction:column; gap:10px; margin-bottom:20px; }
  .category-insight-card { background:#fff8ec; border:1px solid #f0dfb8; border-radius:10px; padding:12px 16px; }
  .category-insight-card h4 { margin:0 0 6px; color:#7a5b1e; }
  .category-insight-card div { font-size:12.5px; line-height:1.7; color:#4a3c1e; }
  .charts-grid { display:grid; grid-template-columns: repeat(2, 1fr); gap:14px; margin-bottom:20px; }
  .chart-card { height: 270px; }
  .chart-card canvas { max-height: 210px; }
  .chart-card.donut { height:300px; }
  .chart-card.donut canvas { max-height:240px; }
  .breakdown-section { margin-bottom:20px; }
  .mini-charts-grid { display:grid; grid-template-columns: repeat(2, 1fr); gap:14px; margin-bottom:16px; }
  .mini-charts-grid.one-col { grid-template-columns: 1fr; }
  .mini-chart-card { height: 280px; }
  .mini-chart-card canvas { max-height: 225px; }
  .major-chart-card { height:320px; margin-bottom:20px; }
  .major-chart-card canvas { max-height:255px; }
  .table-section { margin-bottom:20px; width:100%; overflow-x:auto; }
  .table-section table { font-size:12px; }
  .detail-table { font-size:12px; width:100%; }
  .detail-table th, .detail-table td { white-space: normal !important; word-break: break-word; vertical-align: top; min-width:88px; }
  .detail-table th:first-child, .detail-table td:first-child { min-width:110px; }
  .detail-table th { line-height:1.3; }
  .gridjs-wrapper { width:100% !important; }
  .gridjs-table { width:auto !important; min-width:100%; }
  .cause-pivot-grid { display:grid; grid-template-columns: repeat(2, 1fr); gap:14px; margin-bottom:14px; }
  .cause-pivot-grid.charts .card { height:280px; }
  .cause-pivot-grid.charts canvas { max-height:225px; }
  .cause-pivot table { width:100%; border-collapse:collapse; font-size:12.5px; }
  .cause-pivot th, .cause-pivot td { border:1px solid #e3e5e8; padding:6px 10px; text-align:right; }
  .cause-pivot th:first-child, .cause-pivot td:first-child { text-align:left; }
  .cause-pivot thead th { background:#f5f6f8; }
  .cause-pivot tr.major-row td { font-weight:700; background:#f9fafc; }
  .cause-pivot tr.total-row td { font-weight:700; background:#eef1f5; }
  .note { font-size:11.5px; color:#888; margin-top:16px; line-height:1.6; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; background:#eef1f5; color:#555; font-size:11px; margin-left:6px; }
  .drill-title { font-size:16px; font-weight:700; margin:0 0 4px; color:#12203f; }
</style>
</head>
<body>

<h1>CS・SR分析ダッシュボード</h1>
<div class="sub" id="subHeader">読み込み中...</div>

<div class="page-nav">
  <button class="page-nav-btn active" id="navBtnOverall" type="button">① 全拠点</button>
  <button class="page-nav-btn" id="navBtnAllCategory" type="button">② 全カテゴリ</button>
  <button class="page-nav-btn" id="navBtnCatDetail" type="button">③ カテゴリ詳細(SR改善分析)</button>
  <button class="page-nav-btn" id="navBtnDetail" type="button">④ 拠点×カテゴリ</button>
  <button class="page-nav-btn" id="navBtnCondition" type="button">⑤ コンディション</button>
  <button class="page-nav-btn" id="navBtnPriceBand" type="button">⑥ 価格帯</button>
  <button class="page-nav-btn" id="navBtnProfitVariance" type="button">⑦ 粗利差異</button>
  <button class="page-nav-btn" id="navBtnDeficit" type="button">⑧ 赤字(原価割れ)</button>
  <button class="page-nav-btn" id="navBtnCustomer" type="button">⑨ SRリピーター・ロイヤルカスタマー</button>
</div>

<div class="controls" id="globalControls">
  <div class="ctl">
    <label>期間粒度</label>
    <select id="granularity">
      <option value="week">週次</option>
      <option value="month" selected>月次</option>
      <option value="quarter">四半期</option>
      <option value="half">半期</option>
      <option value="year">通期</option>
    </select>
  </div>
  <div class="ctl ctl-weekstart" id="ctlWeekStart">
    <label>週の起点</label>
    <select id="weekStart">
      <option value="1">月曜〜日曜</option>
      <option value="2">火曜〜月曜</option>
      <option value="3">水曜〜火曜</option>
      <option value="4">木曜〜水曜</option>
      <option value="5">金曜〜木曜</option>
      <option value="6">土曜〜金曜</option>
      <option value="0">日曜〜土曜</option>
    </select>
  </div>
  <div class="ctl">
    <label>対象期間</label>
    <select id="period"></select>
  </div>
  <div class="ctl ctl-loc" id="ctlLoc">
    <label>拠点フィルタ(④拠点×カテゴリ・⑧赤字ページで使用。どちらも「全拠点」を選べます)</label>
    <select id="locFilter"></select>
  </div>
</div>

<!-- ============ ① 全拠点ページ ============ -->
<div class="page-section active" id="page-overall">

  <div class="kpi-grid" id="overallKpiGrid">
    <div class="card kpi-card">
      <div class="kpi-title">質問 (出品中・ヤフオク)</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiQuestionCount">-</span><span class="kpi-rate" id="kpiQuestionRate">率 -</span></div>
      <div class="kpi-delta" id="kpiQuestionDelta"></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">サービスリクエスト発生件数</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiSrCount">-</span><span class="kpi-rate" id="kpiSrRate">率 -</span></div>
      <div class="kpi-delta" id="kpiSrDelta"></div>
      <div class="kpi-delta" id="kpiSrForecast" style="color:#2455c9;"></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">返金額</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiRefundAmount">-</span><span class="kpi-rate" id="kpiRefundRate">率 -</span></div>
      <div class="kpi-delta" id="kpiRefundDelta"></div>
      <div class="kpi-delta" id="kpiRefundForecast" style="color:#2455c9;"></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">返金件数</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiRefundCount">-</span><span class="kpi-rate" id="kpiRefundCountRate">1件あたり -</span></div>
      <div class="kpi-delta" id="kpiRefundCountDelta"></div>
      <div class="kpi-delta" id="kpiRefundCountForecast" style="color:#2455c9;"></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">問合せ (CS_登録 種別=CS)</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiInquiryCount">-</span><span class="kpi-rate" id="kpiInquiryRate">率 -</span></div>
      <div class="kpi-delta" id="kpiInquiryDelta"></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">最終利益</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiProfitAmount">-</span><span class="kpi-rate" id="kpiProfitRate">率 -</span></div>
      <div class="kpi-delta" id="kpiProfitDelta"></div>
    </div>
  </div>
  <!-- B項目: KPIグリッドの直後は「全体サマリー所見」→「YoY(前年同期比)」の順で表示する
       (renderOverallPage() 内のJS呼び出し順序には依存しない。DOMの並び順だけで決まる)。 -->
  <div class="card insight-box">
    <h3>全体サマリー所見</h3>
    <div class="insight-meta" id="insightMeta"></div>
    <div class="insight-text" id="insightOverall">読み込み中...</div>
  </div>

  <div class="card table-section" id="yoyBoxOverall" style="display:none;">
    <h3>前年同期比(YoY) <span id="yoyPeriodLabelOverall" class="badge"></span></h3>
    <div id="yoyTableOverall"></div>
    <p class="note">週次選択時はYoY比較は表示されません。1年前の同じ期間のデータが存在しない場合は「前年データなし」と表示されます。</p>
  </div>

  <h2>推移(全社合計)</h2>
  <div class="charts-grid">
    <div class="card chart-card"><h3>ヤフオク質問 件数・率</h3><canvas id="trend_question"></canvas></div>
    <div class="card chart-card"><h3>SR発生 件数・率</h3><canvas id="trend_sr"></canvas></div>
    <div class="card chart-card"><h3>返金額・率</h3><canvas id="trend_refund"></canvas></div>
    <div class="card chart-card"><h3>問合せ 件数・率（落札後商品質問）</h3><canvas id="trend_inquiry"></canvas></div>
    <div class="card chart-card"><h3>最終利益・利益率</h3><canvas id="trend_profit"></canvas></div>
    <div class="card chart-card"><h3>ジャンク出品 件数・率</h3><canvas id="trend_junk"></canvas></div>
  </div>

  <h2>SR分類の内訳(大項目・全社合算) <span id="overallMajorPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card donut major-chart-card">
    <canvas id="overallMajorChart"></canvas>
  </div>

  <h2>拠点別 内訳比較(件数・率・金額を全拠点並べて表示) <span id="periodLabelLoc" class="badge"></span></h2>
  <div class="mini-charts-grid" id="locChartsGrid"></div>

  <h2>拠点別 SR分類の内訳(大項目) <span id="locMajorPeriodLabel" class="badge"></span></h2>
  <div class="card major-chart-card">
    <canvas id="locMajorChart"></canvas>
  </div>

  <!-- C項目: ジャンク出品比率のヒートマップは⑤拠点×カテゴリページへ移設した -->

  <div class="card table-section">
    <h3 id="tableTitleLocation">詳細テーブル(拠点別)</h3>
    <div id="detailTableLocation" class="detail-table"></div>
  </div>


  <hr style="margin:26px 0 18px; border:none; border-top:2px solid #e3e5e8;">
  <p class="drill-title">SR改善分析 <span id="ovImpPeriod" class="badge"></span></p>
  <div class="card insight-box"><div class="insight-text" id="ovImpInsight"></div></div>

  <h2>小項目別の件数・損失額</h2>
  <div class="card major-chart-card"><canvas id="ovMinorChart"></canvas></div>
  <div class="card table-section cause-pivot"><div id="ovMinorTable"></div></div>

  <h2>小項目別の推移</h2>
  <div class="card major-chart-card"><canvas id="ovMinorTrend"></canvas></div>

  <h2>原因分類 × 原因元</h2>
  <div class="card table-section"><div id="ovCauseCross"></div></div>

  <h2>拠点 × 小項目 ヒートマップ</h2>
  <div class="card table-section"><div id="ovMinorHeat"></div></div>

</div>

<!-- ============ (旧②拠点別ページ。現在は④拠点×カテゴリに統合) ============ -->
<div class="page-section" id="page-detail">

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。Ctrl/Cmd(Macは⌘)+クリックで複数選択すると合算表示します)</label>
      <select id="detailCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
    <div class="ctl">
      <label>&nbsp;</label>
      <span class="note" style="margin:0;">拠点は上部の「拠点」から選択します(「全拠点」も選べます)。<br>下のA・B・Cは同じ選択条件で連動します。</span>
    </div>
  </div>

  <p class="drill-title">A. 選択した拠点 × 選択したカテゴリ</p>


  <p class="drill-title" id="locCatDrillTitle">拠点とカテゴリを選択してください</p>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。Ctrl/Cmd(Macは⌘)+クリックで複数選択すると合算表示します)</label>
      <select id="locCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
  </div>

  <div class="kpi-grid" id="locCatKpiGrid"></div>

  <div class="card table-section" id="yoyBoxLocCat" style="display:none;">
    <h3>前年同期比(YoY) <span id="yoyPeriodLabelLocCat" class="badge"></span></h3>
    <div id="yoyTableLocCat"></div>
  </div>

  <h2>推移(選択拠点×カテゴリ vs 全社平均) <span class="badge">実線=選択条件 / 破線=全社平均</span></h2>
  <div class="charts-grid">
    <div class="card chart-card"><h3>ヤフオク質問 件数・率</h3><canvas id="lcTrend_question"></canvas></div>
    <div class="card chart-card"><h3>SR発生 件数・率</h3><canvas id="lcTrend_sr"></canvas></div>
    <div class="card chart-card"><h3>返金額・率</h3><canvas id="lcTrend_refund"></canvas></div>
    <div class="card chart-card"><h3>問合せ 件数・率（落札後商品質問）</h3><canvas id="lcTrend_inquiry"></canvas></div>
    <div class="card chart-card"><h3>最終利益・利益率</h3><canvas id="lcTrend_profit"></canvas></div>
    <div class="card chart-card"><h3>ジャンク出品 件数・率</h3><canvas id="lcTrend_junk"></canvas></div>
  </div>

  <!-- C項目: ①全拠点ページから移設したヒートマップ。集計ロジックは変更しておらず、
       上部のセレクタで選択中の拠点・カテゴリに関係なく「拠点×カテゴリ全体」を表示する。 -->
  <h2>拠点×カテゴリ別 ジャンク出品比率(選択期間) <span id="junkHeatmapPeriodLabel" class="badge"></span></h2>
  <div class="card table-section" id="junkHeatmapWrap">
    <div id="junkHeatmapTable"></div>
    <p class="note">色が濃いほどその拠点×カテゴリのジャンク出品比率(ジャンク出品件数÷出品数)が高いことを示します(出品数上位カテゴリを表示)。期間・粒度セレクタを切り替えると値が更新されます。この表は拠点・カテゴリの選択によらず全拠点×全カテゴリを表示します。</p>
  </div>

  <h2>SR分類の内訳(大項目) <span id="locCatMajorPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card donut major-chart-card">
    <canvas id="locCatMajorChart"></canvas>
  </div>

  <h2>コンディションランク別・価格帯別 内訳 <span id="locCatCondPeriodLabel" class="badge"></span></h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><h4>コンディションランク別</h4><canvas id="locCatConditionChart"></canvas></div>
    <div class="card mini-chart-card"><h4>価格帯別</h4><canvas id="locCatPriceBandChart"></canvas></div>
  </div>

  <h2>粗利差異(選択拠点×カテゴリ) <span id="locCatPvPeriodLabel" class="badge"></span></h2>
  <div class="kpi-grid" id="locCatPvKpiGrid"></div>

  <div class="card table-section">
    <h3 id="locCatTableTitle">期間別 詳細</h3>
    <div id="detailTableLocCat" class="detail-table"></div>
  </div>

  <p class="note">拠点セレクタ・カテゴリ複数選択で組合せを指定すると、その組合せの問合せ/SR/返金/質問/出荷/出品/ジャンク・コンディション・価格帯・粗利差異を集約して表示します(①全拠点・②全カテゴリページと同じ集計データを両方の軸で絞り込んだものです。ETL側で新しい集計を追加しているわけではありません)。カテゴリを複数選択した場合は選択カテゴリの合算値になります。</p>



  <div id="detailLocationBlock">
  <hr style="margin:28px 0 20px; border:none; border-top:2px solid #e3e5e8;">
  <p class="drill-title">B. 選択した拠点の全体像(カテゴリ内訳つき・全社平均と比較)</p>


  <p class="drill-title" id="locationDrillTitle">拠点を選択してください</p>

  <div class="kpi-grid" id="locDrillKpiGrid"></div>

  <div class="card insight-box" id="locDrillInsightBox" style="display:none;">
    <h3 id="locDrillInsightTitle">拠点所見</h3>
    <div class="insight-text" id="locDrillInsightText"></div>
  </div>

  <div class="card table-section" id="yoyBoxLocation" style="display:none;">
    <h3>前年同期比(YoY) <span id="yoyPeriodLabelLocation" class="badge"></span></h3>
    <div id="yoyTableLocation"></div>
  </div>

  <h2>推移(選択拠点 vs 全社平均) <span class="badge">実線=選択拠点 / 破線=全社平均</span></h2>
  <div class="charts-grid">
    <div class="card chart-card"><h3>ヤフオク質問 件数・率</h3><canvas id="locTrend_question"></canvas></div>
    <div class="card chart-card"><h3>SR発生 件数・率</h3><canvas id="locTrend_sr"></canvas></div>
    <div class="card chart-card"><h3>返金額・率</h3><canvas id="locTrend_refund"></canvas></div>
    <div class="card chart-card"><h3>問合せ 件数・率（落札後商品質問）</h3><canvas id="locTrend_inquiry"></canvas></div>
    <div class="card chart-card"><h3>最終利益・利益率</h3><canvas id="locTrend_profit"></canvas></div>
    <div class="card chart-card"><h3>ジャンク出品 件数・率</h3><canvas id="locTrend_junk"></canvas></div>
  </div>

  <div class="card table-section">
    <h3 id="tableTitleLocationDrill">この拠点のカテゴリ別内訳</h3>
    <div id="detailTableLocationDrill" class="detail-table"></div>
  </div>


  </div>

  <div id="detailCategoryBlock">
  <hr style="margin:28px 0 20px; border:none; border-top:2px solid #e3e5e8;">
  <p class="drill-title">C. 選択したカテゴリの全体像(全拠点合計・全カテゴリ平均と比較)</p>


  <p class="drill-title" id="categoryDrillTitle">カテゴリを選択してください</p>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。Ctrl/Cmd(Macは⌘)+クリックで複数選択すると合算表示します)</label>
      <select id="catMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
  </div>

  <div class="kpi-grid" id="catDrillKpiGrid"></div>

  <div class="card insight-box" id="catDrillInsightBox" style="display:none;">
    <h3 id="catDrillInsightTitle">カテゴリ所見</h3>
    <div class="insight-text" id="catDrillInsightText"></div>
  </div>

  <div class="card table-section" id="yoyBoxCategory" style="display:none;">
    <h3>前年同期比(YoY) <span id="yoyPeriodLabelCategory" class="badge"></span></h3>
    <div id="yoyTableCategory"></div>
  </div>

  <h2>推移(選択カテゴリ vs 全カテゴリ平均) <span class="badge">実線=選択カテゴリ / 破線=全カテゴリ平均</span></h2>
  <div class="charts-grid">
    <div class="card chart-card"><h3>ヤフオク質問 件数・率</h3><canvas id="catTrend_question"></canvas></div>
    <div class="card chart-card"><h3>SR発生 件数・率</h3><canvas id="catTrend_sr"></canvas></div>
    <div class="card chart-card"><h3>返金額・率</h3><canvas id="catTrend_refund"></canvas></div>
    <div class="card chart-card"><h3>問合せ 件数・率（落札後商品質問）</h3><canvas id="catTrend_inquiry"></canvas></div>
    <div class="card chart-card"><h3>最終利益・利益率</h3><canvas id="catTrend_profit"></canvas></div>
    <div class="card chart-card"><h3>ジャンク出品 件数・率</h3><canvas id="catTrend_junk"></canvas></div>
  </div>

  <h2>このカテゴリのSR原因分類ピボット(多方向) <span id="catDrillCausePivotPeriodLabel" class="badge"></span></h2>
  <div class="cause-pivot-grid charts">
    <div class="card"><h4>原因分類別 件数</h4><canvas id="catDrillCauseMajorBarChart"></canvas></div>
    <div class="card"><h4>原因元別 件数</h4><canvas id="catDrillCausePartBarChart"></canvas></div>
  </div>
  <div class="cause-pivot-grid">
    <div class="card cause-pivot"><h4>原因分類 → 原因元</h4><div id="catDrillCausePivotByMajor"></div></div>
    <div class="card cause-pivot"><h4>原因元 → 原因分類</h4><div id="catDrillCausePivotByPart"></div></div>
  </div>
  <p class="note">現時点では「原因分類・原因元」の入力はカメラ・カメラ周辺機器のSRにのみ行われています(CS_登録【分類用】)。他カテゴリでも入力が始まれば自動的に表・グラフに反映されます。</p>

  <div class="card table-section">
    <h3 id="tableTitleCategoryDrill">このカテゴリの拠点別内訳</h3>
    <div id="detailTableCategoryDrill" class="detail-table"></div>
  </div>


  </div>


  <hr style="margin:26px 0 18px; border:none; border-top:2px solid #e3e5e8;">
  <p class="drill-title">SR改善分析 <span id="dtImpPeriod" class="badge"></span></p>
  <div class="card insight-box"><div class="insight-text" id="dtImpInsight"></div></div>

  <h2>小項目別の件数・損失額</h2>
  <div class="card major-chart-card"><canvas id="dtMinorChart"></canvas></div>
  <div class="card table-section cause-pivot"><div id="dtMinorTable"></div></div>

  <h2>小項目別の推移</h2>
  <div class="card major-chart-card"><canvas id="dtMinorTrend"></canvas></div>

  <h2>原因分類 × 原因元</h2>
  <div class="card table-section"><div id="dtCauseCross"></div></div>

  <h2>拠点 × 小項目 ヒートマップ</h2>
  <div class="card table-section"><div id="dtMinorHeat"></div></div>

</div>

<!-- ============ ② 全カテゴリページ ============ -->
<div class="page-section" id="page-allcategory">

  <h2>カテゴリ別 所見</h2>
  <div class="category-insights" id="categoryInsights"></div>

  <div class="card table-section" id="yoyBoxAllCategory" style="display:none;">
    <h3>前年同期比(YoY・全カテゴリ合計) <span id="yoyPeriodLabelAllCategory" class="badge"></span></h3>
    <div id="yoyTableAllCategory"></div>
  </div>

  <!-- 期間 × カテゴリ のマトリクス。カテゴリ同士を並べて、時系列の動きを比較するためのもの。
       期間の粒度は上部の「期間粒度」セレクタ(週次/月次/四半期…)に従う。 -->
  <h2>期間 × カテゴリ の推移比較 <span id="catMatrixGranLabel" class="badge"></span></h2>
  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>指標</label>
      <select id="catMatrixMetric">
        <option value="refund_amount">返金額</option>
        <option value="refund_rate">返金額率（返金額÷売上）</option>
        <option value="sr_count">SR発生件数</option>
        <option value="sr_rate" selected>SR率（SR件数÷出荷数）</option>
        <option value="question_count">質問数</option>
        <option value="question_rate">質問率（質問数÷出品数）</option>
        <option value="inquiry_count">問合せ件数（落札後の商品質問）</option>
        <option value="inquiry_rate">問合せ率（問合せ件数÷出荷数）</option>
      </select>
    </div>
    <div class="ctl">
      <label>グラフに出すカテゴリ（出荷数の多い順）</label>
      <select id="catMatrixTopN">
        <option value="5">上位5</option>
        <option value="8" selected>上位8</option>
        <option value="12">上位12</option>
        <option value="999">すべて</option>
      </select>
    </div>
    <div class="ctl">
      <label>表示する期間</label>
      <select id="catMatrixSpan">
        <option value="12">直近12期間</option>
        <option value="18" selected>直近18期間</option>
        <option value="26">直近26期間</option>
        <option value="999">すべて</option>
      </select>
    </div>
  </div>
  <div class="card chart-card" style="height:340px;"><canvas id="catMatrixChart"></canvas></div>
  <div class="card table-section">
    <h3 id="catMatrixTableTitle">期間 × カテゴリ 一覧</h3>
    <div id="catMatrixTable" style="overflow-x:auto;"></div>
    <p class="note">行がカテゴリ、列が期間です。色が濃いほど値が大きいことを示します（率は各指標の最大値で正規化）。いちばん右の列は選択期間の合計（率は分子・分母をそれぞれ合計してから割り直した値。率の平均ではありません）で、この順に並べています。率を見るときは、母数（出荷数・出品数・売上）が全体の0.5%に満たないカテゴリは「1件で50%」のような極端な値になるため、灰色にして下にまとめています。上部の「期間粒度」を切り替えると週次・月次・四半期などに変わります。拠点フィルタはこの表には掛かりません（全拠点合計）。カテゴリ別のSR原因を深掘りしたいときは③カテゴリ詳細ページをご覧ください。</p>
  </div>

  <h2>カテゴリ別 内訳比較(件数・率・金額を全カテゴリ並べて表示) <span id="periodLabelCat" class="badge"></span></h2>
  <div class="mini-charts-grid one-col" id="catChartsGrid"></div>

  <h2>カテゴリ別 SR分類の内訳(大項目) <span id="catMajorPeriodLabel" class="badge"></span></h2>
  <div class="card major-chart-card">
    <canvas id="catMajorChart"></canvas>
  </div>

  <div class="card table-section">
    <h3 id="tableTitleCategory">詳細テーブル(カテゴリ別)</h3>
    <div id="detailTableCategory" class="detail-table"></div>
  </div>


</div>

<!-- ============ ③ カテゴリ詳細(SR改善分析)ページ ============ -->
<!-- ②全カテゴリページから切り出したSR改善分析。カテゴリを選んで絞り込める。 -->
<div class="page-section" id="page-catdetail">

  <p class="drill-title"><span id="acDrillTitle">SR改善分析 ― 全カテゴリ</span> <span id="acImpPeriod" class="badge"></span></p>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。Ctrl/Cmd(Macは⌘)+クリックで複数選択すると合算します。「(全カテゴリ)」または未選択で全カテゴリ)</label>
      <select id="acCatMultiSelect" multiple size="8" style="min-width:260px;"></select>
    </div>
  </div>

  <div class="card insight-box"><div class="insight-text" id="acImpInsight"></div></div>

  <h2>小項目別の件数・損失額</h2>
  <div class="card major-chart-card"><canvas id="acMinorChart"></canvas></div>
  <div class="card table-section cause-pivot"><div id="acMinorTable"></div></div>

  <h2>小項目別の推移</h2>
  <div class="card major-chart-card"><canvas id="acMinorTrend"></canvas></div>

  <h2>原因分類 × 原因元</h2>
  <div class="card table-section"><div id="acCauseCross"></div></div>

  <h2>拠点 × 小項目 ヒートマップ</h2>
  <div class="card table-section"><div id="acMinorHeat"></div></div>

</div>

<!-- ============ ④ カテゴリ別ページ(1カテゴリドリルダウン) ============ -->
<!-- ============ ④ 拠点×カテゴリ ページ(2軸クロスフィルタ) ============ -->
<!-- ============ ⑤⑥ コンディション・価格帯別分析ページ ============ -->
<div class="page-section" id="page-condition">

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。未選択なら全カテゴリ)</label>
      <select id="condCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
    <div class="ctl">
      <label>&nbsp;</label>
      <span class="note" style="margin:0;">拠点は上部の「拠点」から選択します(「全拠点」も選べます)。</span>
    </div>
  </div>

  <p class="drill-title" id="condTitle">コンディションランク別分析</p>

  <div class="card insight-box" id="condInsightBox">
    <h3>コンディションランク別の所見(自動生成)</h3>
    <div class="insight-meta" id="condInsightMeta"></div>
    <div class="insight-text" id="condInsightText"></div>
  </div>

  <h2>コンディションランク別 内訳(出荷件数・粗利率) <span id="condPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="condChart"></canvas></div>

  <h2>コンディションランク別 SR・返金</h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="condSrChart"></canvas></div>
    <div class="card mini-chart-card"><canvas id="condRefundChart"></canvas></div>
  </div>

  <h2>コンディションランク別 出品数・質問</h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="condListedChart"></canvas></div>
    <div class="card mini-chart-card"><canvas id="condQuestionChart"></canvas></div>
  </div>

  <h2>コンディションランク別 推移</h2>
  <div class="card major-chart-card"><canvas id="condTrendChart"></canvas></div>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="condSrRateTrend"></canvas></div>
    <div class="card mini-chart-card"><canvas id="condRefundRateTrend"></canvas></div>
  </div>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="condListedTrend"></canvas></div>
    <div class="card mini-chart-card"><canvas id="condQuestionRateTrend"></canvas></div>
  </div>

  <div class="card table-section">
    <h3>コンディションランク別 詳細 <span id="condTablePeriodLabel" class="badge"></span></h3>
    <div id="condTable" class="detail-table"></div>
  </div>

  <p class="note">出荷件数・売上・粗利は商品_出荷(JPONベース)の出荷済み商品が対象。SR・問合せ・返金・質問は、各データの商品IDを商品マスタ(商品_出荷・商品_出品待)に突合して状態を付与したもの、出品数は商品_出品待の「状態」列そのものです。商品マスタに無い商品は「不明」に集約しています。SR率=SR件数÷出荷件数、返金率=返金額÷売上金額、質問率=質問数÷出品数。拠点はCSセンター・鳥取・北関東を除外。</p>

</div>

<div class="page-section" id="page-priceband">

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。未選択なら全カテゴリ)</label>
      <select id="pbCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
    <div class="ctl">
      <label>&nbsp;</label>
      <span class="note" style="margin:0;">拠点は上部の「拠点」から選択します(「全拠点」も選べます)。</span>
    </div>
  </div>

  <p class="drill-title" id="pbTitle">価格帯別分析</p>

  <div class="card insight-box" id="pbInsightBox">
    <h3>価格帯別の所見(自動生成)</h3>
    <div class="insight-meta" id="pbInsightMeta"></div>
    <div class="insight-text" id="pbInsightText"></div>
  </div>

  <h2>価格帯別 内訳(出荷件数・粗利率) <span id="pbPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="pbChart"></canvas></div>

  <h2>価格帯別 SR・返金</h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="pbSrChart"></canvas></div>
    <div class="card mini-chart-card"><canvas id="pbRefundChart"></canvas></div>
  </div>

  <h2>価格帯別 出品数・質問</h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="pbListedChart"></canvas></div>
    <div class="card mini-chart-card"><canvas id="pbQuestionChart"></canvas></div>
  </div>

  <h2>価格帯別 推移</h2>
  <div class="card major-chart-card"><canvas id="pbTrendChart"></canvas></div>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="pbSrRateTrend"></canvas></div>
    <div class="card mini-chart-card"><canvas id="pbRefundRateTrend"></canvas></div>
  </div>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="pbListedTrend"></canvas></div>
    <div class="card mini-chart-card"><canvas id="pbQuestionRateTrend"></canvas></div>
  </div>

  <div class="card table-section">
    <h3>価格帯別 詳細 <span id="pbTablePeriodLabel" class="badge"></span></h3>
    <div id="pbTable" class="detail-table"></div>
  </div>

  <p class="note">価格帯は10万円単位。出荷済み商品は落札価格、まだ売れていない商品は販売価格を基準に区分しています(SR・返金・質問は商品IDで商品マスタに突合、出品数は商品_出品待の価格)。価格が取得できない商品は「不明」に集約。SR率=SR件数÷出荷件数、返金率=返金額÷売上金額、質問率=質問数÷出品数。拠点はCSセンター・鳥取・北関東を除外。</p>

</div>

<!-- ============ ⑦ 粗利差異分析ページ(番号変更なし) ============ -->

<div class="page-section" id="page-profitvariance">

  <div class="kpi-grid" id="pvKpiGrid"></div>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>上振れ / 下振れ</label>
      <select id="pvSide">
        <option value="both">両方(差引)</option>
        <option value="up">上振れのみ</option>
        <option value="down">下振れのみ</option>
      </select>
    </div>
    <div class="ctl">
      <label>&nbsp;</label>
      <span class="note" style="margin:0;">拠点は上部の「拠点」から選択します(「全拠点」も選べます)。</span>
    </div>
  </div>

  <div class="card insight-box" id="pvInsightBox">
    <h3>粗利差異の所見(自動生成)</h3>
    <div class="insight-meta" id="pvInsightMeta"></div>
    <div class="insight-text" id="pvInsightText"></div>
  </div>

  <h2>推移(全社合計)</h2>
  <div class="card chart-card" style="height:300px;"><canvas id="pvTrendChart"></canvas></div>

  <h2>コンディション別・価格帯別の粗利差異 <span id="pvBreakPeriodLabel" class="badge"></span></h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><canvas id="pvCondChart"></canvas></div>
    <div class="card mini-chart-card"><canvas id="pvBandChart"></canvas></div>
  </div>
  <div class="card table-section"><h3>コンディション別</h3><div id="pvCondTable"></div></div>
  <div class="card table-section"><h3>価格帯別</h3><div id="pvBandTable"></div></div>
  <div class="card table-section"><h3>カテゴリ別</h3><div id="pvCatTable"></div></div>

  <h2>拠点別 粗利差異(件数・額) <span id="pvLocPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="pvLocChart"></canvas></div>
  <div class="card table-section">
    <h3>拠点別 詳細</h3>
    <div id="detailTablePvLocation" class="detail-table"></div>
  </div>

  <h2>カテゴリ別 粗利差異(件数・額) <span id="pvCatPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="pvCatChart"></canvas></div>
  <div class="card table-section">
    <h3>カテゴリ別 詳細</h3>
    <div id="detailTablePvCategory" class="detail-table"></div>
  </div>

  <h2>カテゴリ別 詳細粗利指標(数量・仕入額・売上額・粗利額・粗利差異・リード・粗利率・単価) <span id="cpdPeriodLabel" class="badge"></span></h2>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><h4>カテゴリ別 粗利率</h4><canvas id="cpdMarginChart"></canvas></div>
    <div class="card mini-chart-card"><h4>カテゴリ別 粗利単価</h4><canvas id="cpdProfitPriceChart"></canvas></div>
  </div>
  <div class="card table-section">
    <h3>カテゴリ別 詳細粗利指標</h3>
    <div id="detailTableCategoryProfitDetail" class="detail-table"></div>
  </div>
  <p class="note">数量=出荷点数/仕入額=買取価格(税抜)合計/売上額=落札価格合計/粗利額=実粗利合計/粗利差異=variance合計/リード=買取日→落札日の平均日数/粗利率=粗利額÷売上額/販売単価=売上額÷数量/粗利単価=粗利額÷数量。対象は商品_出荷(JPONベース)(拠点はCSセンター・鳥取・北関東を除外、20期・21期の全期間分)。</p>

  <h2>カテゴリ別 詳細粗利指標の推移(カテゴリ選択・合計/平均) <span id="cpdTrendPeriodLabel" class="badge"></span></h2>
  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可・Ctrl/Cmd+クリック)</label>
      <select id="cpdCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
  </div>
  <div class="mini-charts-grid">
    <div class="card mini-chart-card"><h4>販売単価・粗利単価・粗利率</h4><canvas id="cpdTrendPriceChart"></canvas></div>
    <div class="card mini-chart-card"><h4>粗利差異・リード</h4><canvas id="cpdTrendVarianceLeadChart"></canvas></div>
    <div class="card mini-chart-card"><h4>売上額・粗利率</h4><canvas id="cpdTrendSalesQtyChart"></canvas></div>
    <div class="card mini-chart-card"><h4>粗利額・数量</h4><canvas id="cpdTrendProfitMarginChart"></canvas></div>
  </div>
  <p class="note">実線=合計(選択カテゴリの値をそのまま合算。率・単価系は合計粗利額÷合計売上額等で再計算) / 破線=平均(選択カテゴリの値の単純平均。カテゴリごとの規模差を無視した「平均的な1カテゴリ」の値)。粒度・対象期間セレクタに連動して集計されます。</p>

  <p class="note">粗利差異 = 実粗利(落札価格-買取価格/1.1) - 見込み粗利(販売価格-買取価格/1.1) = 落札価格-販売価格。上振れ=差異がプラス(落札価格が販売価格を上回った)、下振れ=差異がマイナス(下回った)の件数・金額です。対象は商品_出荷(JPONベース)の出荷済み商品(拠点はCSセンター・鳥取・北関東を除外)。</p>

</div>

<!-- ============ ⑧ 赤字(原価割れ)分析ページ ============ -->
<div class="page-section" id="page-deficit">

  <!-- E項目: ⑧赤字ページの絞り込み。拠点は共通の拠点セレクタ(locFilter。「全拠点」を含む)を使い、
       カテゴリはこのページ専用の複数選択セレクタを使う。④・⑤ページと違い「未選択=全カテゴリ」。 -->
  <p class="drill-title" id="deficitDrillTitle">全拠点 × 全カテゴリ</p>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>損益の見方</label>
      <select id="deficitMode">
        <option value="acc">粗利損(落札価格-買取価格/1.1)</option>
        <option value="fin" selected>最終利益(粗利-返品時の発送送料/1.1-返送料/1.1)</option>
      </select>
    </div>
  </div>

  <div class="kpi-grid" id="deficitModeKpi"></div>

  <div class="card insight-box" id="deficitInsightBox">
    <h3>赤字(原価割れ)の所見(自動生成)</h3>
    <div class="insight-meta" id="deficitInsightMeta"></div>
    <div class="insight-text" id="deficitInsightText"></div>
  </div>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>カテゴリ(複数選択可。Ctrl/Cmd(Macは⌘)+クリックで複数選択すると合算表示します。未選択=全カテゴリ)</label>
      <select id="deficitCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
  </div>

  <div class="kpi-grid" id="deficitKpiGrid"></div>

  <!-- F項目: 「カテゴリ別 赤字比較」を「推移」より前に表示する -->
  <h2>カテゴリ別 赤字比較 <span id="deficitCatPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="deficitCatChart"></canvas></div>

  <details class="card insight-box" id="deficitItemBox" style="display:none;" open>
    <summary style="cursor:pointer;font-weight:700;color:#1c2b4a;font-size:15px;list-style:revert;">
      <span id="deficitItemTitle">カテゴリの商品明細</span>
      <span class="badge">クリックで開閉</span>
    </summary>
    <div class="insight-meta" style="margin-top:8px;" id="deficitItemSummary"></div>
    <div id="deficitItemTable" class="detail-table" style="margin-top:8px;"></div>
    <p class="note">表示対象は「赤字だった商品」と「SRが発生した商品」です。金額はすべて税抜です(販売価格・買取価格・返送料は元データが税込のため÷1.1)。見込差＝落札価格−見込販売価格で、マイナスなら見込より安く売れたことを意味します。検索欄でコンディション・買取者・SR分類などの絞り込みができます。</p>
  </details>

  <div class="card table-section">
    <h3>カテゴリ別 詳細 <span class="badge">カテゴリ名をクリックすると商品明細が開きます</span></h3>
    <div id="detailTableDeficitCategory" class="detail-table"></div>
  </div>

  <h2 id="deficitTrendHeading">推移(全社合計)</h2>
  <div class="card chart-card" style="height:300px;"><canvas id="deficitTrendChart"></canvas></div>

  <h2>仕入れ方法別 赤字比較 <span id="deficitProcPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="deficitProcChart"></canvas></div>
  <div class="card table-section">
    <h3>仕入れ方法別 詳細</h3>
    <div id="detailTableDeficitProc" class="detail-table"></div>
  </div>

  <!-- カテゴリ × 仕入れ方法 のクロス集計。どのカテゴリをどの仕入れ経路で
       取ったときに赤字になりやすいかを1枚で見るための表。 -->
  <h2>カテゴリ × 仕入れ方法 クロス集計 <span id="deficitCrossPeriodLabel" class="badge"></span></h2>
  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>表示する指標</label>
      <select id="deficitCrossMetric">
        <option value="rate" selected>赤字率(赤字点数÷出荷点数)</option>
        <option value="count">赤字点数</option>
        <option value="amount">赤字額合計(円)</option>
        <option value="per_ship">出荷1点あたり赤字額(円)</option>
        <option value="shipped">出荷点数</option>
      </select>
    </div>
  </div>
  <div class="card table-section">
    <div id="deficitCrossTable" style="overflow-x:auto;"></div>
    <p class="note">上部の「損益の見方」(粗利損／最終利益)と、拠点・カテゴリ・期間の絞り込みがそのまま反映されます。色が濃いほど値が大きい(＝悪い)ことを示します。セルにマウスを乗せると出荷点数・赤字点数・赤字額が表示されます。出荷点数の多い順に上位15カテゴリを表示しています。</p>
  </div>

  <!-- 雑損: 返品されたが再販不可と判断され、商品V2で「雑損」ステータスになった商品。
       売上が立たず買取価格がまるごと損失になるため、赤字商品とは別枠で集計する。 -->
  <h2>雑損(再販不可) <span id="writeoffPeriodLabel" class="badge"></span></h2>
  <div class="kpi-grid" id="writeoffKpi"></div>
  <div class="card chart-card" style="height:300px;"><canvas id="writeoffTrendChart"></canvas></div>
  <div class="mini-charts-grid">
    <div class="card table-section">
      <h3>カテゴリ別 雑損</h3>
      <div id="writeoffCatTable"></div>
    </div>
    <div class="card table-section">
      <h3>拠点別 雑損</h3>
      <div id="writeoffLocTable"></div>
    </div>
  </div>
  <p class="note">返品された商品のうち、再販できないと判断されて商品V2で「雑損」ステータスになったものです。売上が立たないため<b>買取価格がそのまま損失</b>になります(金額は税抜)。上の赤字(原価割れ)は「売れたが利益が出なかった商品」なので、雑損とは別の損失です。期間キーは他ページと同じ出荷日基準です。</p>

  <div class="card insight-box" style="background:#fff8ec;border-color:#f0dfb8;">
    <h3 style="color:#7a5b1e;">この表に入っていない「人の手間」のコスト</h3>
    <div class="insight-text" style="color:#4a3c1e;">
金額だけを見ると赤字でなくても、SRが起きた商品には作業時間という見えないコストがかかっています。1点あたりの目安は次のとおりです。

　・商品化〜出荷まで： 約30分（SRの有無にかかわらず必ず発生）
　・SR発生後のCS対応： 約30分（お客様対応・原因確認）
　・返送品の再検品〜返金対応〜再出品〜再出荷： 約30分

つまりSRが1件起きて返品・再販まで至ると、通常の30分に加えて約60分＝<b>通常の3倍の工数</b>がかかります。金額上は「再販できたので黒字」に見える商品でも、人件費を加味すると実質的に利益が消えている可能性があります。赤字件数と併せて、SR発生件数そのものを減らすことが利益改善に直結します。

<span style="font-size:11.5px;color:#7a5b1e;">※この工数コストは金額集計には含めていません（時給単価が拠点・雇用形態で異なるため）。判断材料としてご覧ください。</span>
    </div>
  </div>

  <p class="note" id="deficitNote">損益の2つの見方: <b>粗利損</b>＝落札価格-買取価格/1.1 がマイナスになったもの（仕入と売価の差だけを見る）。<b>最終利益</b>＝粗利-返送料/1.1 がマイナスになったもの（返品時の返送料は当社負担のため差し引く）。上部のセレクタで切り替えられます。<br>
  ・<b>発送時の送料は差し引いていません</b>。お客様からお預かりした送料をそのまま配送業者に支払うため当社の持ち出しにならず、返品時もお客様に返金されるためです（厳密には契約送料との差額が利益になりますが、ここでは利益として考慮していません）。<br>
  ・返品→再出品→再販が同じ期間内に起きた場合、落札価格は<b>最終的に売れた価格</b>を採用します。<br>
  ・例）買取10,000円・最終落札13,000円の場合 → 粗利損の判定=13,000-9,091=3,909円（プラスなので赤字ではない）。返品が発生していれば、さらに返送料(税抜)を差し引いた額で判定します。<br>
  ・<b>赤字率</b>＝赤字商品数 ÷ 出荷商品数。全体のうちどれくらいが赤字なのかを表します。<br>
  <br>
  赤字(原価割れ)の定義: 実質粗利(落札価格-買取価格/1.1-発送送料) が0未満の商品。加えて、その商品についてCS_返金に「返品」列が「あり」の行があれば、その商品の返送料(ヤフオク配送料)も赤字額に加算しています(total_deficit=正の値ほど赤字が大きいことを表します)。対象は商品_出荷(JPONベース)の出荷済み商品(拠点はCSセンター・鳥取・北関東を除外)。発送送料は「ヤフオク配送料」列を基準に、数値ならそのまま採用、"らくらく家財便"の場合は受注_通常_出荷の実際の送料を突合して採用、"直引"の場合は0円としています。<br>
  上部の拠点セレクタ(「全拠点」または1拠点)と、このページのカテゴリ複数選択で対象を絞り込めます。カテゴリは何も選択しない状態が「全カテゴリ」、複数選択した場合はその合算です。絞り込みはこのページの全セクション(KPI・カテゴリ別比較・推移・仕入れ方法別比較)に同じ条件で反映されます。</p>

</div>

<div class="page-section" id="page-customer">

  <div class="seg-tabs">
    <button class="seg-tab-btn active" id="segBtnSrRepeater" type="button">SRリピーター</button>
    <button class="seg-tab-btn" id="segBtnLoyalCustomer" type="button">ロイヤルカスタマー</button>
  </div>

  <div class="insight-box">
    <h3 id="customerSegTitle"></h3>
    <div class="insight-text" id="customerSegDesc"></div>
  </div>

  <div class="kpi-grid" id="customerKpiGrid"></div>

  <div class="controls" style="margin-bottom:14px;">
    <div class="ctl">
      <label>発送商品数の下限(件)</label>
      <select id="custMinShipped">
        <option value="0">絞り込みなし</option>
        <option value="10">10件以上</option>
        <option value="50">50件以上</option>
        <option value="100">100件以上</option>
        <option value="300">300件以上</option>
        <option value="500">500件以上</option>
        <option value="700">700件以上</option>
        <option value="1000">1000件以上</option>
      </select>
    </div>
    <div class="ctl">
      <label>顧客種別</label>
      <select id="custType">
        <option value="__ALL__">すべて</option>
        <option value="個人">個人のみ</option>
        <option value="業者拠点">業者拠点のみ(転送代行・法人窓口)</option>
      </select>
    </div>
    <div class="ctl">
      <label>SR発生件数の下限(件)</label>
      <select id="custMinSr">
        <option value="0">絞り込みなし</option>
        <option value="3">3件以上</option>
        <option value="5">5件以上</option>
        <option value="10">10件以上</option>
        <option value="20">20件以上</option>
        <option value="30">30件以上</option>
      </select>
    </div>
    <div class="ctl">
      <label>&nbsp;</label>
      <span class="note" style="margin:0;" id="custFilterNote"></span>
    </div>
  </div>

  <details class="card insight-box" id="custDrillBox" style="display:none;" open>
    <summary style="cursor:pointer;font-weight:700;color:#1c2b4a;font-size:15px;list-style:revert;">
      <span id="custDrillTitle">顧客の詳細</span>
      <span class="badge">クリックで開閉</span>
    </summary>
    <div class="insight-meta" style="margin-top:8px;">一覧の「顧客」名をクリックすると、その顧客の購入内訳とSR内訳を表示します。</div>
    <div id="custDrillBody"></div>
  </details>

  <div class="card table-section">
    <h3 id="customerTableTitle">顧客別 詳細</h3>
    <div id="detailTableCustomer" class="detail-table"></div>
  </div>

  <p class="note">
    顧客の名寄せ(同一人物判定)は「受注_通常_出荷」の氏名・住所(郵便番号+都道府県+住所)・電話番号・メールアドレスを正規化し、いずれか1つでも一致すれば同一人物とみなしてUnion-Find(素集合データ構造)でまとめています(空欄・欠損は照合キーに使用しません)。<br>
    匿名ラベル(顧客A・顧客B…)は全顧客を売上額(落札価格合計)の降順に並べた通し記号で、2つのセグメント間で共通です。個人を特定できる氏名・住所・電話番号・メールアドレスは、このダッシュボードのデータには一切含まれていません。<br>
    ・発送商品数: 受注_通常_出荷.受注ID = 受注_JPON_出荷.取引番号、受注_JPON_出荷.管理番号 = 商品_出荷(JPONベース).商品ID で辿れた出荷商品の件数<br>
    ・同梱率: 品数が2以上の受注件数 ÷ 全受注件数　・SR率: SR発生件数 ÷ 発送商品数　・返金額率: 返金額 ÷ 売上額(落札価格合計)<br>
    ・SR発生件数: CS_登録【分類用】の種別=SRの行を「受注ID」で紐付けた件数(拠点はCSセンター・鳥取・北関東を除外、ステータス=スルーの行は除外)<br>
    ・最終利益: 粗利(落札価格-買取価格/1.1-ヤフオク配送料/1.1)-返金額-返送料<br>
    ・セグメント内の各率は「分子と分母をそれぞれ合算した後に算出」しています(顧客ごとの率の単純平均ではありません)。<br>
    ・このページは全期間(20期+21期)の累計であり、上部の期間・拠点の絞り込みは適用されません。
  </p>

</div>

<div class="note">
  データ範囲: <span id="dataThrough"></span> まで反映(週次データが追加され次第更新)。<br>
  ※集計は各データの基準日(問合せ・SR=登録日 / 返金=返金日 / 質問=登録日 / 出荷・売上=出荷予定日 / 出品=出品待日 / コンディション・価格帯・粗利差異=出荷予定日)で<b>1日単位</b>に行っており、「期間粒度=週次」を選ぶと右の「週の起点」で月曜起点・金曜起点などを自由に切り替えられます(起点を変えても各指標の定義は変わりません)。<br>
  ※表示を軽くするため、終了済みの20期(2025年7月〜2026年6月)は月次にまとめて保持しています。月次・四半期・半期・通期の数値は従来と完全に同一ですが、20期は「週次」表示の対象外です(週次では21期のみ表示、「全期間合計」を選んだ場合は20期も合算されます)。<br>
  ※同じく軽量化のため、金額は行単位で円未満を四捨五入して保持しています。件数は完全一致、金額の合計値の誤差は最大でも0.0015%未満(数百万円に対し数十円)です。<br>
  ※率はいずれも「期間・絞り込み内で、分子と分母をそれぞれ合算した後に算出」しています(月次の率を単純平均していません)。<br>
  ※対象: 問合せ=CS_登録の種別「CS」(落札後の商品問合せ)、SR=CS_登録の種別「SR」、返金額=CS_返金の返金日基準、ヤフオク質問=質問_登録(出品中の質問対応、落札後の問合せとは別データ)。拠点はいずれもCSセンター・鳥取・北関東を除外集計。<br>
  ※粗利=落札価格-買取価格/1.1-ヤフオク配送料/1.1。最終利益=粗利-返金額/1.1(-返品ありの場合はさらに返送料/1.1=ヤフオク配送料/1.1)。<br>
  ※<b>金額はすべて税抜表示です</b>。元データが税込の項目(返金額・返送料・買取価格・販売価格・送料)は÷1.1して税抜に換算しています(落札価格は元から税抜)。<br>
  ※返金率=返金額(税抜)÷売上金額(落札価格・税抜)で算出しています。<br>
  ※ジャンクは商品の「状態」欄が「ジャンク」の商品を対象に、出荷数・出品数に対する比率で算出。<br>
  ※SR分類の大項目は「分類」列(CS_登録【分類用】)のうち種別=SRの行のみを対象に、一番上に選択されている項目を採用(複数選択時)。種別=CSの行(商品質問・支払質問など)はSR分類には含めていません。<br>
  ※全体サマリー所見・カテゴリ別所見は、その時点までのデータをもとに週次で書き直されるコメントです(自動生成のため、必ず数値側と併せてご確認ください)。
</div>


<!-- ============================================================================
     自作の最小限gzip解凍(pure JS、外部ライブラリ不使用)
     RFC1951(DEFLATE)+RFC1952(gzip)を実装したもの。Coworkアーティファクトの制約上
     Chart.js/Grid.js/Mermaid以外のCDNは使用できないため、データ本体(~20MB超)を
     10MBのアップロード上限に収めるためにPython側でgzip圧縮+base64化して埋め込み、
     ここで解凍する。このファイル用に新規に書いたコードで、既存のOSSからのコピーでは
     ないためライセンス上の問題はない。
     ============================================================================ -->
<script>
function inflateRaw(input, outSizeHint) {
  var pos = 0;
  var bitBuf = 0, bitCount = 0;
  function readBit() {
    if (bitCount === 0) { bitBuf = input[pos++]; bitCount = 8; }
    var b = bitBuf & 1; bitBuf >>>= 1; bitCount--; return b;
  }
  function readBits(n) {
    var v = 0;
    for (var i = 0; i < n; i++) v |= readBit() << i;
    return v;
  }
  var out = new Uint8Array(outSizeHint || (input.length * 3 + 64));
  var outPos = 0;
  function ensure(n) {
    if (outPos + n > out.length) {
      var bigger = new Uint8Array(Math.max(out.length * 2, outPos + n + 64));
      bigger.set(out.subarray(0, outPos));
      out = bigger;
    }
  }
  function buildHuffman(lengths) {
    var maxBits = 0;
    for (var i = 0; i < lengths.length; i++) if (lengths[i] > maxBits) maxBits = lengths[i];
    var blCount = new Array(maxBits + 1).fill(0);
    for (i = 0; i < lengths.length; i++) if (lengths[i] > 0) blCount[lengths[i]]++;
    var code = 0;
    var nextCode = new Array(maxBits + 1).fill(0);
    for (var bits = 1; bits <= maxBits; bits++) {
      code = (code + blCount[bits - 1]) << 1;
      nextCode[bits] = code;
    }
    var codesByLen = {};
    for (var sym = 0; sym < lengths.length; sym++) {
      var len = lengths[sym];
      if (len === 0) continue;
      if (!codesByLen[len]) codesByLen[len] = {};
      codesByLen[len][nextCode[len]] = sym;
      nextCode[len]++;
    }
    return {
      decode: function () {
        var c = 0;
        for (var len = 1; len <= maxBits; len++) {
          c = (c << 1) | readBit();
          if (codesByLen[len] && Object.prototype.hasOwnProperty.call(codesByLen[len], c)) {
            return codesByLen[len][c];
          }
        }
        throw new Error('inflate: bad huffman code');
      }
    };
  }
  var FIXED_LIT_LENGTHS, FIXED_DIST_LENGTHS;
  (function () {
    var l = new Array(288);
    var i2 = 0;
    for (; i2 < 144; i2++) l[i2] = 8;
    for (; i2 < 256; i2++) l[i2] = 9;
    for (; i2 < 280; i2++) l[i2] = 7;
    for (; i2 < 288; i2++) l[i2] = 8;
    FIXED_LIT_LENGTHS = l;
    FIXED_DIST_LENGTHS = new Array(30).fill(5);
  })();
  var LEN_BASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
  var LEN_EXTRA = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
  var DIST_BASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
  var DIST_EXTRA = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
  var CLEN_ORDER = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
  function inflateBlockDynamic() {
    var hlit = readBits(5) + 257;
    var hdist = readBits(5) + 1;
    var hclen = readBits(4) + 4;
    var clLengths = new Array(19).fill(0);
    for (var i = 0; i < hclen; i++) clLengths[CLEN_ORDER[i]] = readBits(3);
    var clHuff = buildHuffman(clLengths);
    var lengths = [];
    while (lengths.length < hlit + hdist) {
      var sym = clHuff.decode();
      if (sym < 16) {
        lengths.push(sym);
      } else if (sym === 16) {
        var rep = readBits(2) + 3;
        var prev = lengths[lengths.length - 1];
        for (var r = 0; r < rep; r++) lengths.push(prev);
      } else if (sym === 17) {
        var rep2 = readBits(3) + 3;
        for (var r2 = 0; r2 < rep2; r2++) lengths.push(0);
      } else {
        var rep3 = readBits(7) + 11;
        for (var r3 = 0; r3 < rep3; r3++) lengths.push(0);
      }
    }
    var litLengths = lengths.slice(0, hlit);
    var distLengths = lengths.slice(hlit, hlit + hdist);
    return { lit: buildHuffman(litLengths), dist: buildHuffman(distLengths) };
  }
  function inflateBlockData(litHuff, distHuff) {
    while (true) {
      var sym = litHuff.decode();
      if (sym < 256) {
        ensure(1);
        out[outPos++] = sym;
      } else if (sym === 256) {
        break;
      } else {
        var li = sym - 257;
        var len = LEN_BASE[li] + readBits(LEN_EXTRA[li]);
        var dsym = distHuff.decode();
        var dist = DIST_BASE[dsym] + readBits(DIST_EXTRA[dsym]);
        ensure(len);
        var from = outPos - dist;
        for (var k = 0; k < len; k++) { out[outPos] = out[from + k]; outPos++; }
      }
    }
  }
  var bfinal = 0;
  do {
    bfinal = readBit();
    var btype = readBits(2);
    if (btype === 0) {
      bitCount = 0; bitBuf = 0;
      var len0 = input[pos] | (input[pos + 1] << 8);
      pos += 4;
      ensure(len0);
      for (var b = 0; b < len0; b++) out[outPos++] = input[pos++];
    } else if (btype === 1) {
      var lh = buildHuffman(FIXED_LIT_LENGTHS);
      var dh = buildHuffman(FIXED_DIST_LENGTHS);
      inflateBlockData(lh, dh);
    } else if (btype === 2) {
      var huff = inflateBlockDynamic();
      inflateBlockData(huff.lit, huff.dist);
    } else {
      throw new Error('inflate: bad block type');
    }
  } while (!bfinal);
  return out.subarray(0, outPos);
}
function gunzip(bytes) {
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) throw new Error('not gzip');
  var flg = bytes[3];
  var pos = 10;
  if (flg & 0x04) { var xlen = bytes[pos] | (bytes[pos+1]<<8); pos += 2 + xlen; }
  if (flg & 0x08) { while (bytes[pos] !== 0) pos++; pos++; }
  if (flg & 0x10) { while (bytes[pos] !== 0) pos++; pos++; }
  if (flg & 0x02) { pos += 2; }
  var isize = (bytes[bytes.length-4] | (bytes[bytes.length-3]<<8) | (bytes[bytes.length-2]<<16) | (bytes[bytes.length-1]<<24)) >>> 0;
  var body = bytes.subarray(pos, bytes.length - 8);
  return inflateRaw(body, isize || undefined);
}
function base64ToBytes(b64) {
  var binStr = atob(b64);
  var bytes = new Uint8Array(binStr.length);
  for (var i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
  return bytes;
}
</script>
<script>
const COMPRESSED_DATA_B64 = "__DATA_JSON_GZ_B64__";
const DATA = JSON.parse(new TextDecoder('utf-8').decode(gunzip(base64ToBytes(COMPRESSED_DATA_B64))));

// Python側で {_c:[列名], _d:[[値,...],...]} に圧縮された行配列をオブジェクト配列へ復元する。
// week_end / year_month は日次粒度(week_end === week_start)なので week_start から補完する。
function rehydrateRows(packed) {
  if (!packed) return [];
  if (Array.isArray(packed)) return packed;
  const cols = packed._c, data = packed._d;
  const hasWeek = cols.indexOf('week_start') >= 0;
  const wi = cols.indexOf('week_start');
  const out = new Array(data.length);
  for (let i = 0; i < data.length; i++) {
    const src = data[i], o = {};
    for (let j = 0; j < cols.length; j++) o[cols[j]] = src[j];
    if (hasWeek) {
      const ws = src[wi];
      o.week_end = ws;
      o.year_month = ws ? ws.slice(0, 7) : ws;
    }
    out[i] = o;
  }
  return out;
}

const ROWS = rehydrateRows(DATA.rows);
const SR_MAJOR_ROWS = rehydrateRows(DATA.sr_major_rows);
const CAUSE_ROWS = rehydrateRows(DATA.cause_rows);
const CONDITION_ROWS = rehydrateRows(DATA.condition_rows);
const PRICE_BAND_ROWS = rehydrateRows(DATA.price_band_rows);
const PROFIT_VARIANCE_ROWS = rehydrateRows(DATA.profit_variance_rows);
const CATEGORY_PROFIT_DETAIL_ROWS = rehydrateRows(DATA.category_profit_detail_rows);
const DEFICIT_ROWS = rehydrateRows(DATA.deficit_rows);
// ⑦: 「会計上の粗利」と「最終利益」の2軸で集計した行
const DEFICIT_MODE_ROWS = rehydrateRows(DATA.deficit_mode_rows);
// ⑦赤字ページ: 雑損(返品後に再販不可と判断され、商品V2で「雑損」になった商品)
const WRITEOFF_ROWS = rehydrateRows(DATA.writeoff_rows);
// ⑦赤字ページ: カテゴリをクリックしたときに出す商品1件ごとの明細
// (対象は「赤字だった商品」と「SRが発生した商品」。商品名・SR自由記述は含まない)
const ITEM_DETAIL_ROWS = rehydrateRows(DATA.item_detail_rows);
// ⑥粗利差異ページ: コンディション別・価格帯別に上振れ/下振れを分解した行
const VARIANCE_CONDITION_ROWS = rehydrateRows(DATA.variance_condition_rows);
const VARIANCE_BAND_ROWS = rehydrateRows(DATA.variance_band_rows);
// ⑨ SRリピーター・ロイヤルカスタマー分析。1行=1顧客(名寄せ済みクラスタ)で、
// segment 列が 'sr_repeater' / 'loyal_customer' のどちらのセグメントかを表す。
// 匿名ラベルと数値指標のみで、氏名・住所・電話番号等の個人情報は含まれない。
const CUSTOMER_SEGMENT_ROWS = rehydrateRows(DATA.customer_segment_rows);
// ⑧で顧客をクリックしたときに表示する内訳(購入・SR)。件数と金額の集計のみで、
// 商品名やSRの自由記述は含めない(公開ページに載るため)。
const CUSTOMER_DETAIL_ROWS = rehydrateRows(DATA.customer_detail_rows);
const CUSTOMER_DETAIL_BY_LABEL = (() => {
  const map = new Map();
  CUSTOMER_DETAIL_ROWS.forEach(r => {
    if (!map.has(r.label)) map.set(r.label, []);
    map.get(r.label).push(r);
  });
  return map;
})();
const INSIGHTS = DATA.insights || {};
const CONDITION_ORDER = ['ジャンク(J)', '程度不良(D)', '一般中古(C)', '程度良好(B)', '美品(A)', '未使用品(S)', '新品(N)'];

// ---------- fiscal period helpers ----------
const [FY_START_Y, FY_START_M] = DATA.fiscal_year_start.split('-').map(Number);
const BASE_FY_NUM = parseInt(DATA.fiscal_year_label, 10);

function fiscalInfo(year_month) {
  const [y, m] = year_month.split('-').map(Number);
  const monthsDiff = (y - FY_START_Y) * 12 + (m - FY_START_M);
  const fyIndex = Math.floor(monthsDiff / 12);
  const monthInFY = ((monthsDiff % 12) + 12) % 12;
  const quarter = Math.floor(monthInFY / 3) + 1;
  const half = monthInFY < 6 ? '上期' : '下期';
  const fyNum = BASE_FY_NUM + fyIndex;
  return { fyNum, quarter, half, monthInFY, sortKey: fyIndex * 12 + monthInFY };
}

function fmtWeekLabel(ws, we) {
  const [, m1, d1] = ws.split('-');
  const [, m2, d2] = we.split('-');
  return parseInt(m1, 10) + '/' + parseInt(d1, 10) + '~' + parseInt(m2, 10) + '/' + parseInt(d2, 10);
}

// 対象期間セレクタの選択肢の先頭に、その期間がどちらの期(20th=2025年7月〜2026年6月、
// 21st=2026年7月〜)に属するかを追記するための英語序数サフィックス("20th"/"21st"/"22nd"等)。
function ordinalSuffix(n) {
  const j = n % 10, k = n % 100;
  if (j === 1 && k !== 11) return n + 'st';
  if (j === 2 && k !== 12) return n + 'nd';
  if (j === 3 && k !== 13) return n + 'rd';
  return n + 'th';
}

// 週次は「日次行(1行=1日)」を、選択された起点曜日の週にまとめて表示する。
// 例: 起点=金曜なら 金〜木 が1週。ETLは各データの基準日(登録日/出荷予定日/返金日/出品待日)で
// 日次集計しているため、起点を変えても分子・分母の定義は一切変わらない。
let WEEK_START_DOW = 1; // 0=日 .. 6=土 (既定は月曜起点)
const _weekBucketCache = new Map();

function weekBucketOf(ymd) {
  const cacheKey = ymd + '|' + WEEK_START_DOW;
  const hit = _weekBucketCache.get(cacheKey);
  if (hit) return hit;
  const [y, m, d] = ymd.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const shift = (dt.getUTCDay() - WEEK_START_DOW + 7) % 7;
  const start = new Date(dt.getTime() - shift * 86400000);
  const end = new Date(start.getTime() + 6 * 86400000);
  const iso = (x) => x.toISOString().slice(0, 10);
  const res = { start: iso(start), end: iso(end) };
  _weekBucketCache.set(cacheKey, res);
  return res;
}

// 20期(DAILY_FROMより前)は月次に丸めて埋め込んでいるため、週次表示の対象外にする。
// key=null にすると availablePeriods の一覧にも、個別週の絞り込みにも一致しなくなる
// (「全期間合計」を選んだ場合だけは従来どおり合算対象に含まれる)。
const DAILY_FROM = DATA.daily_from || '9999-12-31';

function periodKeyFor(row, granularity) {
  const fi = fiscalInfo(row.year_month);
  if (granularity === 'week') {
    if (row.week_start < DAILY_FROM) return { key: null, label: '', sort: '', fyNum: fi.fyNum };
    const b = weekBucketOf(row.week_start);
    const bfi = fiscalInfo(b.start.slice(0, 7));
    return { key: b.start, label: ordinalSuffix(bfi.fyNum) + ' ' + fmtWeekLabel(b.start, b.end), sort: b.start, fyNum: bfi.fyNum };
  }
  if (granularity === 'month') {
    const monthNum = parseInt(row.year_month.split('-')[1], 10);
    return { key: row.year_month, label: ordinalSuffix(fi.fyNum) + ' ' + monthNum + '月', sort: fi.sortKey, fyNum: fi.fyNum };
  }
  if (granularity === 'quarter') return { key: fi.fyNum + '期-Q' + fi.quarter, label: ordinalSuffix(fi.fyNum) + ' Q' + fi.quarter, sort: fi.fyNum * 10 + fi.quarter, fyNum: fi.fyNum };
  if (granularity === 'half') return { key: fi.fyNum + '期-' + fi.half, label: ordinalSuffix(fi.fyNum) + ' ' + fi.half, sort: fi.fyNum * 10 + (fi.half === '上期' ? 1 : 2), fyNum: fi.fyNum };
  return { key: fi.fyNum + '期', label: ordinalSuffix(fi.fyNum) + '(通期)', sort: fi.fyNum, fyNum: fi.fyNum };
}

// YoY(前年同期比)対応: 現在の期間キーから「ちょうど1年前の同じ期間」のキーを算出する。
// key の形式(内部キー、periodKeyFor参照)はいずれも先頭に(西暦年 または 期数)を含むため、
// その数字部分だけを1減らせば前年の同じ月/四半期/半期/通期のキーになる。週次では
// YoY比較は不要(仕様)。
function yoyPeriodKey(granularity, periodKey) {
  if (granularity === 'week' || periodKey === '__ALL__' || periodKey == null) return null;
  if (granularity === 'month') {
    const m = /^(\d{4})-(\d{2})$/.exec(periodKey);
    if (!m) return null;
    return (parseInt(m[1], 10) - 1) + '-' + m[2];
  }
  const m2 = /^(\d+)期(.*)$/.exec(periodKey);
  if (!m2) return null;
  return (parseInt(m2[1], 10) - 1) + '期' + m2[2];
}

// ---------- filters state ----------
const locations = Array.from(new Set(ROWS.map(r => r.location))).sort();
const categories = Array.from(new Set(ROWS.map(r => r.category))).sort();

// "(不明)"/"default" are data-quality placeholder values that happen to sort first
// alphabetically (ASCII punctuation / lowercase Latin sort before Japanese characters).
// A <select> auto-selects its first DOM option as soon as it has no explicit `selected`
// option, so without this, locFilter/catFilter would silently default to these
// placeholders on first page load, before any of our own JS ever runs.
const firstRealLocation = locations.find(l => l !== '(不明)') || locations[0];
const firstRealCategory = categories.find(c => c !== '不明' && c !== 'default') || categories[0];

const locSel = document.getElementById('locFilter');
// D項目: 「全拠点」(__ALL__)を先頭の選択肢として追加する。
// ⑤拠点×カテゴリページと⑧赤字ページでは「拠点で絞らない(全拠点合算)」を意味する。
// ②拠点別ページは1拠点ドリルダウン専用のため、従来どおり実在の最初の拠点にフォールバックする。
const ALL_LOC = '__ALL__';
{ const o = document.createElement('option'); o.value = ALL_LOC; o.textContent = '全拠点'; locSel.appendChild(o); }
locations.forEach(l => { const o = document.createElement('option'); o.value = l; o.textContent = l; locSel.appendChild(o); });
if (firstRealLocation) locSel.value = firstRealLocation;

function getMultiSelectValues(selectEl) {
  return Array.from(selectEl.selectedOptions).map(o => o.value);
}

// E項目: ④カテゴリ別ページ用の複数選択カテゴリセレクタ(⑤拠点×カテゴリページの
// locCatMultiSelectとは独立)。選択したカテゴリを合算して表示する。
const catMultiSel = document.getElementById('catMultiSelect');
if (catMultiSel) {
  categories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; catMultiSel.appendChild(o); });
  if (firstRealCategory) {
    const opt = Array.from(catMultiSel.options).find(o => o.value === firstRealCategory);
    if (opt) opt.selected = true;
  }
}

// ⑤拠点×カテゴリページ用の複数選択カテゴリセレクタ(④カテゴリ別ページのcatMultiSelectとは
// 別インスタンス。拠点は単一選択(locFilter)のまま、カテゴリだけ複数選択・合算に対応する)。
const locCatMultiSel = document.getElementById('locCatMultiSelect');
if (locCatMultiSel) {
  categories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; locCatMultiSel.appendChild(o); });
  if (firstRealCategory) {
    const opt = Array.from(locCatMultiSel.options).find(o => o.value === firstRealCategory);
    if (opt) opt.selected = true;
  }
}

// ②拠点×カテゴリ(統合ページ)用のカテゴリセレクタ。
// 旧④カテゴリ別(catMultiSelect)・旧⑤拠点×カテゴリ(locCatMultiSelect)の描画処理をそのまま
// 再利用するため、このセレクタの選択内容を両者へ同期してから各描画関数を呼ぶ。
// 「(全カテゴリ)」を選ぶと全カテゴリ合算になる。
const ALL_CAT = '__ALL__';
const detailCatMultiSel = document.getElementById('detailCatMultiSelect');
if (detailCatMultiSel) {
  const allOpt = document.createElement('option');
  allOpt.value = ALL_CAT; allOpt.textContent = '(全カテゴリ)';
  detailCatMultiSel.appendChild(allOpt);
  categories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; detailCatMultiSel.appendChild(o); });
  allOpt.selected = true;
}
// 統合前のページに付いていたカテゴリセレクタは重複するので隠す(値の同期先としては使い続ける)
[catMultiSel, locCatMultiSel].forEach(sel => {
  if (!sel) return;
  const box = sel.closest ? sel.closest('.controls') : null;
  if (box) box.style.display = 'none';
});

function syncDetailCategorySelection() {
  if (!detailCatMultiSel) return;
  const picked = getMultiSelectValues(detailCatMultiSel);
  const useAll = picked.includes(ALL_CAT) || picked.length === 0;
  const want = new Set(picked);
  [catMultiSel, locCatMultiSel].forEach(sel => {
    if (!sel) return;
    Array.from(sel.options).forEach(o => { o.selected = useAll ? true : want.has(o.value); });
  });
}

function renderDetailPage() {
  syncDetailCategorySelection();
  renderLocCatPage();
  // 「全拠点」選択時は、1拠点ドリルダウン(Bブロック)は意味を持たないので隠す
  const specificLoc = locSel.value && locSel.value !== ALL_LOC;
  const locBlock = document.getElementById('detailLocationBlock');
  if (locBlock) locBlock.style.display = specificLoc ? '' : 'none';
  if (specificLoc) renderLocationPage();
  renderCategoryPage();
  // 選択中の拠点×カテゴリに絞ったSR改善分析
  const cats = new Set(getMultiSelectValues(locCatMultiSel));
  const useCat = cats.size > 0 && cats.size < categories.length;
  renderImprovementSection('dt', r =>
    (!specificLoc || r.location === locSel.value) && (!useCat || cats.has(r.category)));
}

// ④コンディション / ⑤価格帯ページ用のカテゴリセレクタ(未選択=全カテゴリ)
['condCatMultiSelect', 'pbCatMultiSelect'].forEach(id => {
  const sel = document.getElementById(id);
  if (!sel) return;
  categories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; sel.appendChild(o); });
  sel.addEventListener('change', () => {
    if ((id === 'condCatMultiSelect' && currentPage === 'condition') ||
        (id === 'pbCatMultiSelect' && currentPage === 'priceband')) renderPageContent();
  });
});

// E項目: ⑧赤字(原価割れ)ページ用の複数選択カテゴリセレクタ。
// ④・⑤ページとは異なり、初期状態では何も選択しない(=全カテゴリ・絞り込みなし)。
// 選択肢は赤字データ(DEFICIT_ROWS)に実在するカテゴリのみとする。
const deficitCategories = Array.from(new Set(DEFICIT_ROWS.map(r => r.category))).sort();
const deficitCatMultiSel = document.getElementById('deficitCatMultiSelect');
if (deficitCatMultiSel) {
  const allOpt = document.createElement('option');
  allOpt.value = ALL_CAT; allOpt.textContent = '(全カテゴリ)';
  deficitCatMultiSel.appendChild(allOpt);
  allOpt.selected = true;
  deficitCategories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; deficitCatMultiSel.appendChild(o); });
}

// ③カテゴリ詳細(SR改善分析)ページ用の複数選択カテゴリセレクタ。
// 選択肢はSR分類データ(SR_MAJOR_ROWS)に実在するカテゴリ。初期状態は「(全カテゴリ)」。
const acCategories = Array.from(new Set(SR_MAJOR_ROWS.map(r => r.category))).filter(Boolean).sort();
const acCatMultiSel = document.getElementById('acCatMultiSelect');
if (acCatMultiSel) {
  const allOpt = document.createElement('option');
  allOpt.value = ALL_CAT; allOpt.textContent = '(全カテゴリ)';
  acCatMultiSel.appendChild(allOpt);
  allOpt.selected = true;
  acCategories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; acCatMultiSel.appendChild(o); });
  acCatMultiSel.addEventListener('change', () => { if (currentPage === 'catdetail') renderPageContent(); });
}

// F項目: ⑦ページのカテゴリ別詳細粗利指標(CATEGORY_PROFIT_DETAIL_ROWS)推移グラフ用の
// 複数選択カテゴリセレクタ。
const cpdCategories = Array.from(new Set(CATEGORY_PROFIT_DETAIL_ROWS.map(r => r.category))).sort();
const cpdCatMultiSel = document.getElementById('cpdCatMultiSelect');
if (cpdCatMultiSel) {
  // 先頭に「(全カテゴリ)」を用意し、既定でこれを選択する
  const allOpt = document.createElement('option');
  allOpt.value = ALL_CAT; allOpt.textContent = '(全カテゴリ)';
  cpdCatMultiSel.appendChild(allOpt);
  cpdCategories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; cpdCatMultiSel.appendChild(o); });
  allOpt.selected = true;
  cpdCatMultiSel.addEventListener('change', () => { if (currentPage === 'profitvariance') renderCpdTrendSection(); });
}

const granSel = document.getElementById('granularity');
const weekStartSel = document.getElementById('weekStart');
const ctlWeekStart = document.getElementById('ctlWeekStart');
const periodSel = document.getElementById('period');
const ctlLoc = document.getElementById('ctlLoc');

let currentPage = 'overall'; // 'overall' | 'location' | 'allcategory' | 'category'

const NUM_FIELDS = [
  'inquiry_count', 'sr_count', 'refund_amount', 'refund_count', 'return_shipping_cost',
  'sales_amount', 'gross_profit', 'question_count', 'shipped_count', 'listed_count',
  'junk_shipped_count', 'junk_listed_count'
];

function emptyAgg() { const o = {}; NUM_FIELDS.forEach(f => o[f] = 0); return o; }
function addInto(agg, row) { NUM_FIELDS.forEach(f => agg[f] += (row[f] || 0)); }

function deriveRates(agg) {
  const final_profit = agg.gross_profit - agg.refund_amount - agg.return_shipping_cost;
  return {
    inquiry_count: agg.inquiry_count,
    inquiry_rate: agg.shipped_count ? agg.inquiry_count / agg.shipped_count : null,
    sr_count: agg.sr_count,
    sr_rate: agg.shipped_count ? agg.sr_count / agg.shipped_count : null,
    refund_amount: agg.refund_amount,
    refund_rate: agg.sales_amount ? agg.refund_amount / agg.sales_amount : null,
    refund_count: agg.refund_count,
    avg_refund_amount: agg.refund_count ? agg.refund_amount / agg.refund_count : null,
    return_shipping_cost: agg.return_shipping_cost,
    question_count: agg.question_count,
    question_rate: agg.listed_count ? agg.question_count / agg.listed_count : null,
    shipped_count: agg.shipped_count,
    listed_count: agg.listed_count,
    sales_amount: agg.sales_amount,
    gross_profit: agg.gross_profit,
    final_profit: final_profit,
    profit_margin: agg.sales_amount ? final_profit / agg.sales_amount : null,
    junk_shipped_count: agg.junk_shipped_count,
    junk_shipped_rate: agg.shipped_count ? agg.junk_shipped_count / agg.shipped_count : null,
    junk_listed_count: agg.junk_listed_count,
    junk_listed_rate: agg.listed_count ? agg.junk_listed_count / agg.listed_count : null
  };
}

function fmtInt(n) { return (n === null || n === undefined) ? '-' : Math.round(n).toLocaleString('ja-JP'); }
function fmtYen(n) { return (n === null || n === undefined) ? '-' : '¥' + Math.round(n).toLocaleString('ja-JP'); }
function fmtPct(n) { return (n === null || n === undefined) ? '-' : (n * 100).toFixed(2) + '%'; }

// ---------- period universe (used to align comparison trends) ----------
function availablePeriods(granularity) {
  const map = new Map();
  ROWS.forEach(r => { const pk = periodKeyFor(r, granularity); if (pk.key != null) map.set(pk.key, pk); });
  return Array.from(map.values()).sort((a, b) => a.sort < b.sort ? -1 : (a.sort > b.sort ? 1 : 0));
}

function populatePeriodSelect() {
  const granularity = granSel.value;
  const periods = availablePeriods(granularity);
  const prev = periodSel.value;
  periodSel.innerHTML = '';
  const allOpt = document.createElement('option'); allOpt.value = '__ALL__'; allOpt.textContent = '全期間合計'; periodSel.appendChild(allOpt);
  periods.forEach(p => { const o = document.createElement('option'); o.value = p.key; o.textContent = p.label; periodSel.appendChild(o); });
  if (periods.length) periodSel.value = periods[periods.length - 1].key;
  if (prev && Array.from(periodSel.options).some(o => o.value === prev)) periodSel.value = prev;
}

function currentPeriodLabel() {
  const opt = periodSel.options[periodSel.selectedIndex];
  return opt ? opt.textContent : '';
}

// ---------- generic aggregation helpers (rowFilter is an optional function(row) => bool) ----------
function computeAgg(granularity, periodKey, rowFilter) {
  let rows = ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  if (rowFilter) rows = rows.filter(rowFilter);
  const agg = emptyAgg();
  rows.forEach(r => addInto(agg, r));
  return deriveRates(agg);
}

// trend aligned across the full period universe so that a filtered series (e.g. one location)
// and the company-wide series can be safely compared point-for-point even if the filtered
// series has no data in some periods.
function buildTrendAligned(granularity, rowFilter) {
  const periods = availablePeriods(granularity);
  let rows = ROWS;
  if (rowFilter) rows = rows.filter(rowFilter);
  const map = new Map();
  rows.forEach(r => {
    const pk = periodKeyFor(r, granularity);
    if (!map.has(pk.key)) map.set(pk.key, emptyAgg());
    addInto(map.get(pk.key), r);
  });
  return periods.map(p => ({ label: p.label, key: p.key, ...deriveRates(map.get(p.key) || emptyAgg()) }));
}

function buildBreakdown(granularity, axis, periodKey, rowFilter) {
  const dim = axis === 'location' ? 'location' : 'category';
  let rows = ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  if (rowFilter) rows = rows.filter(rowFilter);
  const map = new Map();
  rows.forEach(r => {
    const k = r[dim];
    if (!map.has(k)) map.set(k, emptyAgg());
    addInto(map.get(k), r);
  });
  return Array.from(map.entries()).map(([k, agg]) => ({ name: k, ...deriveRates(agg) }));
}

// ---------- generic helpers for CONDITION_ROWS / PRICE_BAND_ROWS / PROFIT_VARIANCE_ROWS ----------
// These row arrays share the same week_start/week_end/year_month shape as ROWS, so
// periodKeyFor()/availablePeriods() (built from ROWS) work unchanged for period filtering
// and for aligning trend series across the same period universe.
function sumFieldsInto(target, row, fields) { fields.forEach(f => target[f] = (target[f] || 0) + (row[f] || 0)); }

function buildDimBreakdownGeneric(rowsArr, dim, fields, granularity, periodKey, rowFilter) {
  let rows = rowsArr.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  if (rowFilter) rows = rows.filter(rowFilter);
  const map = new Map();
  rows.forEach(r => {
    const k = r[dim];
    if (!map.has(k)) map.set(k, {});
    sumFieldsInto(map.get(k), r, fields);
  });
  return Array.from(map.entries()).map(([k, o]) => ({ name: k, ...o }));
}

function buildDimTrendAlignedGeneric(rowsArr, dim, fields, granularity, rowFilter) {
  const periods = availablePeriods(granularity);
  let rows = rowsArr;
  if (rowFilter) rows = rows.filter(rowFilter);
  const dimsSet = new Set();
  const map = new Map(); // periodKey -> { dimValue -> {field: sum} }
  rows.forEach(r => {
    const pk = periodKeyFor(r, granularity).key;
    const k = dim ? r[dim] : '__ALL__';
    dimsSet.add(k);
    if (!map.has(pk)) map.set(pk, new Map());
    const inner = map.get(pk);
    if (!inner.has(k)) inner.set(k, {});
    sumFieldsInto(inner.get(k), r, fields);
  });
  return { periods, dims: Array.from(dimsSet), periodMap: map };
}

// ---------- chart helpers ----------
const chartRegistry = {};
function renderChart(canvasId, config) {
  if (chartRegistry[canvasId]) chartRegistry[canvasId].destroy();
  const el = document.getElementById(canvasId);
  if (!el) return;
  chartRegistry[canvasId] = new Chart(el, config);
}

const PALETTE = ['#5b8def', '#e0653a', '#2ecc71', '#f1c40f', '#9b59b6', '#1abc9c', '#e74c3c', '#34495e', '#95a5a6', '#16a085', '#d35400', '#7f8c8d'];

function tooltipLabelFn(isMoney) {
  return function (context) {
    const label = context.dataset.label || '';
    const v = context.parsed.y;
    if (v === null || v === undefined) return label + ': -';
    if (context.dataset.yAxisID === 'y1') return label + ': ' + (v * 100).toFixed(2) + '%';
    if (isMoney) return label + ': ¥' + Math.round(v).toLocaleString('ja-JP');
    return label + ': ' + Math.round(v).toLocaleString('ja-JP');
  };
}

function dualAxisConfig(labels, barData, lineData, barLabel, lineLabel, leftLabel, isMoney) {
  return {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { type: 'bar', label: barLabel, data: barData, backgroundColor: '#5b8def', yAxisID: 'y', order: 2 },
        { type: 'line', label: lineLabel, data: lineData, borderColor: '#e0653a', backgroundColor: '#e0653a', yAxisID: 'y1', order: 1, tension: 0, spanGaps: true }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, labels: { boxWidth: 10, font: { size: 10.5 } } },
        tooltip: { callbacks: { label: tooltipLabelFn(isMoney) } }
      },
      scales: {
        y: {
          position: 'left',
          title: { display: true, text: leftLabel, font: { size: 10.5 } },
          ticks: { font: { size: 10 }, callback: v => isMoney ? (v/1000).toLocaleString('ja-JP') + 'k' : v }
        },
        y1: {
          position: 'right', grid: { drawOnChartArea: false },
          title: { display: true, text: '率(%)', font: { size: 10.5 } },
          ticks: { font: { size: 10 }, callback: v => (v*100).toFixed(1)+'%' }
        },
        x: { ticks: { font: { size: 9.5 }, maxRotation: 55, minRotation: 0 } }
      }
    }
  };
}

function dualAxisWithBaselineConfig(labels, barData, lineData, baseLineData, barLabel, lineLabel, baseLabel, leftLabel, isMoney) {
  const cfg = dualAxisConfig(labels, barData, lineData, barLabel, lineLabel, leftLabel, isMoney);
  cfg.data.datasets.push({
    type: 'line', label: baseLabel, data: baseLineData, borderColor: '#888', backgroundColor: '#888',
    borderDash: [5, 4], yAxisID: 'y1', order: 0, pointRadius: 2, tension: 0, spanGaps: true
  });
  return cfg;
}

function singleAxisMoneyConfig(labels, values, barLabel) {
  return {
    type: 'bar',
    data: { labels, datasets: [{ label: barLabel, data: values, backgroundColor: '#5b8def' }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => (c.dataset.label||'') + ': ¥' + Math.round(c.parsed.y).toLocaleString('ja-JP') } }
      },
      scales: {
        y: { title: { display: true, text: '金額(¥)', font: { size: 10.5 } }, ticks: { font: { size: 10 }, callback: v => (v/1000).toLocaleString('ja-JP')+'k' } },
        x: { ticks: { font: { size: 9.5 }, maxRotation: 55 } }
      }
    }
  };
}

function singleAxisCountConfig(labels, values, barLabel, color, yTitle) {
  return {
    type: 'bar',
    data: { labels, datasets: [{ label: barLabel, data: values, backgroundColor: color || '#5b8def' }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => (c.dataset.label||'') + ': ' + Math.round(c.parsed.y).toLocaleString('ja-JP') } }
      },
      scales: {
        y: { title: { display: true, text: yTitle || '件数', font: { size: 10.5 } }, ticks: { font: { size: 10 } } },
        x: { ticks: { font: { size: 9.5 }, maxRotation: 55 } }
      }
    }
  };
}

// dualAxisConfigのバリエーション: 右軸を「率(%)」ではなく「金額(¥)」として表示する版。
// 赤字分析(⑧)のように「件数」と「金額」を組み合わせたい場合に使う。
function dualAxisMoneyRightConfig(labels, barData, lineData, barLabel, lineLabel, leftLabel) {
  return {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { type: 'bar', label: barLabel, data: barData, backgroundColor: '#5b8def', yAxisID: 'y', order: 2 },
        { type: 'line', label: lineLabel, data: lineData, borderColor: '#e0653a', backgroundColor: '#e0653a', yAxisID: 'y1', order: 1, tension: 0, spanGaps: true }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: true, labels: { boxWidth: 10, font: { size: 10.5 } } },
        tooltip: {
          callbacks: {
            label: c => {
              const v = c.parsed.y;
              if (v === null || v === undefined) return (c.dataset.label || '') + ': -';
              if (c.dataset.yAxisID === 'y1') return (c.dataset.label || '') + ': ¥' + Math.round(v).toLocaleString('ja-JP');
              return (c.dataset.label || '') + ': ' + Math.round(v).toLocaleString('ja-JP');
            }
          }
        }
      },
      scales: {
        y: { position: 'left', title: { display: true, text: leftLabel, font: { size: 10.5 } }, ticks: { font: { size: 10 } } },
        y1: {
          position: 'right', grid: { drawOnChartArea: false },
          title: { display: true, text: '金額(¥)', font: { size: 10.5 } },
          ticks: { font: { size: 10 }, callback: v => (v / 1000).toLocaleString('ja-JP') + 'k' }
        },
        x: { ticks: { font: { size: 9.5 }, maxRotation: 55 } }
      }
    }
  };
}

function stackedMajorConfig(labels, majors, mapByLabel) {
  return {
    type: 'bar',
    data: {
      labels,
      datasets: majors.map((m, i) => ({
        label: m,
        data: labels.map(l => (mapByLabel.get(l) || {})[m] || 0),
        backgroundColor: PALETTE[i % PALETTE.length]
      }))
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10, font: { size: 9.5 } } } },
      scales: {
        x: { stacked: true, ticks: { font: { size: 9.5 }, maxRotation: 55 } },
        y: { stacked: true, title: { display: true, text: '件数(SR)', font: { size: 10.5 } }, ticks: { font: { size: 10 } } }
      }
    }
  };
}

function donutMajorConfig(majors, counts) {
  return {
    type: 'doughnut',
    data: { labels: majors, datasets: [{ data: counts, backgroundColor: majors.map((_, i) => PALETTE[i % PALETTE.length]) }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'right', labels: { boxWidth: 10, font: { size: 10.5 } } } }
    }
  };
}

// ---------- ① 全拠点ページ ----------
// 期間の経過率(着地率)から着地見込みを計算する。
// 例: 月次で8/12までのデータなら 12/31日=38.7%経過 -> 実績÷0.387 が着地見込み。
// 週次は期間が短く振れが大きいので対象外。期間が既に終わっている場合は表示しない。
function periodProgress(granularity, periodKey) {
  if (granularity === 'week' || periodKey === '__ALL__' || !DATA.data_through) return null;
  const rows = ROWS.filter(r => periodKeyFor(r, granularity).key === periodKey);
  if (!rows.length) return null;
  const dates = rows.map(r => r.week_start).sort();
  const through = DATA.data_through;

  // 対象期間の開始日・終了日を粒度から求める
  const [fy, fm] = dates[0].split('-').map(Number);
  let start, end;
  if (granularity === 'month') {
    start = new Date(Date.UTC(fy, fm - 1, 1));
    end = new Date(Date.UTC(fy, fm, 0));
  } else {
    const fi = fiscalInfo(dates[0].slice(0, 7));
    const fyStartMonth = FY_START_M - 1;
    const baseYear = FY_START_Y + (fi.fyNum - BASE_FY_NUM);
    let offset = 0, months = 12;
    if (granularity === 'quarter') { offset = (fi.quarter - 1) * 3; months = 3; }
    else if (granularity === 'half') { offset = (fi.half === '上期' ? 0 : 6); months = 6; }
    start = new Date(Date.UTC(baseYear, fyStartMonth + offset, 1));
    end = new Date(Date.UTC(baseYear, fyStartMonth + offset + months, 0));
  }
  const t = new Date(through + 'T00:00:00Z');
  if (t >= end) return null;               // 期間が終了済み(見込み不要)
  if (t < start) return null;
  const totalDays = (end - start) / 86400000 + 1;
  const doneDays = (t - start) / 86400000 + 1;
  const ratio = doneDays / totalDays;
  if (ratio <= 0 || ratio >= 1) return null;
  return { ratio, doneDays: Math.round(doneDays), totalDays: Math.round(totalDays) };
}

function renderForecast(elId, actual, prog, isMoney) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!prog || actual == null) { el.textContent = ''; return; }
  const est = actual / prog.ratio;
  el.textContent = '着地見込み ' + (isMoney ? fmtYen(est) : fmtInt(est)) +
    '(進捗 ' + prog.doneDays + '/' + prog.totalDays + '日=' + (prog.ratio * 100).toFixed(0) + '%)';
}

function renderOverallKPIs() {
  const granularity = granSel.value;
  const periodKey = periodSel.value;
  const d = computeAgg(granularity, periodKey, null);

  document.getElementById('kpiInquiryCount').textContent = fmtInt(d.inquiry_count);
  document.getElementById('kpiInquiryRate').textContent = '率 ' + fmtPct(d.inquiry_rate);
  document.getElementById('kpiSrCount').textContent = fmtInt(d.sr_count);
  document.getElementById('kpiSrRate').textContent = '率 ' + fmtPct(d.sr_rate);
  document.getElementById('kpiRefundAmount').textContent = fmtYen(d.refund_amount);
  document.getElementById('kpiRefundRate').textContent = '率 ' + fmtPct(d.refund_rate);
  document.getElementById('kpiRefundCount').textContent = fmtInt(d.refund_count);
  document.getElementById('kpiRefundCountRate').textContent = '1件あたり ' + (d.avg_refund_amount == null ? '-' : fmtYen(d.avg_refund_amount));
  document.getElementById('kpiQuestionCount').textContent = fmtInt(d.question_count);
  document.getElementById('kpiQuestionRate').textContent = '率 ' + fmtPct(d.question_rate);
  document.getElementById('kpiProfitAmount').textContent = fmtYen(d.final_profit);
  document.getElementById('kpiProfitRate').textContent = '率 ' + fmtPct(d.profit_margin);

  // 着地見込み(SR発生件数・返金額・返金件数のみ。月次/四半期/半期/通期が対象)
  const prog = periodProgress(granularity, periodKey);
  renderForecast('kpiSrForecast', d.sr_count, prog, false);
  renderForecast('kpiRefundForecast', d.refund_amount, prog, true);
  renderForecast('kpiRefundCountForecast', d.refund_count, prog, false);

  ['Inquiry','Sr','Refund','RefundCount','Question','Profit'].forEach(id => document.getElementById('kpi'+id+'Delta').textContent = '');
  if (periodKey !== '__ALL__') {
    const periods = availablePeriods(granularity);
    const idx = periods.findIndex(p => p.key === periodKey);
    if (idx > 0) {
      const prevKey = periods[idx - 1].key;
      const pd = computeAgg(granularity, prevKey, null);
      const deltaText = (curr, prev) => {
        if (!prev) return '';
        const diff = curr - prev;
        const pct = (diff / Math.abs(prev)) * 100;
        const cls = diff > 0 ? 'up' : (diff < 0 ? 'down' : 'flat');
        const arrow = diff > 0 ? '▲' : (diff < 0 ? '▼' : '―');
        return '<span class="'+cls+'">'+arrow+' 前期比 '+ (pct>=0?'+':'') + pct.toFixed(1) + '%</span>';
      };
      document.getElementById('kpiInquiryDelta').innerHTML = deltaText(d.inquiry_count, pd.inquiry_count);
      document.getElementById('kpiSrDelta').innerHTML = deltaText(d.sr_count, pd.sr_count);
      document.getElementById('kpiRefundDelta').innerHTML = deltaText(d.refund_amount, pd.refund_amount);
      document.getElementById('kpiRefundCountDelta').innerHTML = deltaText(d.refund_count, pd.refund_count);
      document.getElementById('kpiQuestionDelta').innerHTML = deltaText(d.question_count, pd.question_count);
      document.getElementById('kpiProfitDelta').innerHTML = deltaText(d.final_profit, pd.final_profit);
    }
  }
  renderYoyBox('yoyBoxOverall', 'yoyTableOverall', 'yoyPeriodLabelOverall', granularity, periodKey, null);
}

// ---------- YoY(前年同期比)比較テーブル ----------
// 月次/四半期/半期/通期選択時のみ、選択中の期間と「ちょうど1年前の同じ期間」を比較する。
// 週次選択時はYoY比較は表示しない(仕様)。データが無い場合は「前年データなし」と表示する。
const YOY_METRICS = [
  { key: 'inquiry_count', label: '問合せ件数', money: false, isRate: false },
  { key: 'sr_count', label: 'SR発生件数', money: false, isRate: false },
  { key: 'refund_amount', label: '返金額', money: true, isRate: false },
  { key: 'refund_count', label: '返金件数', money: false, isRate: false },
  { key: 'question_count', label: '質問数', money: false, isRate: false },
  { key: 'shipped_count', label: '出荷商品数', money: false, isRate: false },
  { key: 'listed_count', label: '出品数', money: false, isRate: false },
  { key: 'junk_listed_rate', label: 'ジャンク出品比率', money: false, isRate: true },
  { key: 'gross_profit', label: '粗利', money: true, isRate: false },
  { key: 'profit_margin', label: '粗利率', money: false, isRate: true }
];

function renderYoyBox(boxId, tableId, labelId, granularity, periodKey, rowFilter) {
  const box = document.getElementById(boxId);
  if (!box) return;
  const yoyKey = yoyPeriodKey(granularity, periodKey);
  const labelEl = document.getElementById(labelId);
  if (!yoyKey) { box.style.display = 'none'; return; }
  box.style.display = '';
  if (labelEl) labelEl.textContent = currentPeriodLabel();
  const periods = availablePeriods(granularity);
  const exists = periods.some(p => p.key === yoyKey);
  const tableEl = document.getElementById(tableId);
  if (!exists) {
    tableEl.innerHTML = '<p class="note">前年同期データなし(比較対象期間: ' + yoyKey + ')</p>';
    return;
  }
  const cur = computeAgg(granularity, periodKey, rowFilter);
  const prev = computeAgg(granularity, yoyKey, rowFilter);
  let out = '<table style="width:100%;border-collapse:collapse;font-size:12.5px;"><thead><tr>' +
    '<th style="text-align:left;padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">指標</th>' +
    '<th style="padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">前年同期</th>' +
    '<th style="padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">今期</th>' +
    '<th style="padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">前年比</th></tr></thead><tbody>';
  YOY_METRICS.forEach(m => {
    const cv = cur[m.key], pv = prev[m.key];
    const fmt = v => (v === null || v === undefined) ? '-' : (m.isRate ? fmtPct(v) : (m.money ? fmtYen(v) : fmtInt(v)));
    let diffTxt = '-', cls = 'flat';
    if (cv != null && pv != null) {
      if (m.isRate) {
        const ptDiff = (cv - pv) * 100;
        diffTxt = (ptDiff >= 0 ? '+' : '') + ptDiff.toFixed(2) + 'pt';
        cls = ptDiff > 0 ? 'up' : (ptDiff < 0 ? 'down' : 'flat');
      } else if (pv !== 0) {
        const pct = ((cv - pv) / Math.abs(pv)) * 100;
        diffTxt = (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
        cls = pct > 0 ? 'up' : (pct < 0 ? 'down' : 'flat');
      } else if (cv !== 0) {
        diffTxt = '新規';
      } else {
        diffTxt = '±0%';
      }
    }
    out += '<tr><td style="padding:6px 10px;border:1px solid #e3e5e8;">' + m.label + '</td>' +
      '<td style="padding:6px 10px;border:1px solid #e3e5e8;text-align:right;">' + fmt(pv) + '</td>' +
      '<td style="padding:6px 10px;border:1px solid #e3e5e8;text-align:right;">' + fmt(cv) + '</td>' +
      '<td class="' + cls + '" style="padding:6px 10px;border:1px solid #e3e5e8;text-align:right;font-weight:600;">' + diffTxt + '</td></tr>';
  });
  out += '</tbody></table>';
  tableEl.innerHTML = out;
}

// ---------- H項目: 期間粒度別の動的な比較所見 ----------
// 「現在選択されている粒度の対象期間」と「その直前の同じ長さの期間」を比較する。
// 週次選択時=直近1週 vs 前週、月次=直近1ヶ月 vs 前月、四半期=直近1四半期 vs 前四半期、
// 半期=直近半期 vs 前半期、通期=今期(21期) vs 前期(20期)。いずれも periodKeyFor() の
// 粒度別ビニングと availablePeriods() の並び順(時系列昇順)に従い、単純に「最後の2期間」
// を比較するだけで上記すべてのケースに対応できる(通期の場合は年度境界でビニングされる
// ため、自然に「今期 vs 前期」になる)。
// 既存のKPIカードの「前期比」ロジック(availablePeriods()で対象期間の1つ前を取得し、
// computeAgg()で集計してから差分を計算する部分。renderOverallKPIsを参照)と同じ
// 組み立て方を再利用している。
const GRANULARITY_LEADIN = {
  week: '直近1週間', month: '直近1ヶ月', quarter: '直近1四半期', half: '直近半期', year: '今期'
};
const GRANULARITY_PREV_LABEL = {
  week: '前週', month: '前月', quarter: '前四半期', half: '前半期', year: '前期'
};

function fmtPtDelta(diffPt) {
  if (diffPt === null || diffPt === undefined || isNaN(diffPt)) return '';
  return '(' + (diffPt >= 0 ? '+' : '') + diffPt.toFixed(2) + 'pt)';
}

function buildDynamicPeriodInsight(rowFilter) {
  const granularity = granSel.value;
  const periods = availablePeriods(granularity);
  if (periods.length < 2) {
    return '(比較対象となる直前の期間のデータがまだありません。データが積み上がると自動的に比較コメントが表示されます)';
  }
  const curP = periods[periods.length - 1];
  const prevP = periods[periods.length - 2];
  const cur = computeAgg(granularity, curP.key, rowFilter);
  const prev = computeAgg(granularity, prevP.key, rowFilter);

  const srRateOf = d => d.shipped_count ? d.sr_count / d.shipped_count : null;
  const refundRateOf = d => d.shipped_count ? d.refund_count / d.shipped_count : null; // 対出荷(返金件数÷出荷件数)
  const marginRateOf = d => d.sales_amount ? d.gross_profit / d.sales_amount : null;

  const srCur = srRateOf(cur), srPrev = srRateOf(prev);
  const rfCur = refundRateOf(cur), rfPrev = refundRateOf(prev);
  const jkCur = cur.junk_listed_rate, jkPrev = prev.junk_listed_rate;
  const mgCur = marginRateOf(cur), mgPrev = marginRateOf(prev);

  const ptDiff = (a, b) => (a == null || b == null) ? null : (a - b) * 100;

  const leadIn = (GRANULARITY_LEADIN[granularity] || '直近期間') + '(' + curP.label + ')は、' +
    (GRANULARITY_PREV_LABEL[granularity] || '直前の期間') + '(' + prevP.label + ')と比べて';

  const parts = [
    'SR発生率が' + fmtPct(srPrev) + '→' + fmtPct(srCur) + fmtPtDelta(ptDiff(srCur, srPrev)),
    '返金率(対出荷)が' + fmtPct(rfPrev) + '→' + fmtPct(rfCur) + fmtPtDelta(ptDiff(rfCur, rfPrev)),
    'ジャンク出品比率が' + fmtPct(jkPrev) + '→' + fmtPct(jkCur) + fmtPtDelta(ptDiff(jkCur, jkPrev)),
    '粗利率が' + fmtPct(mgPrev) + '→' + fmtPct(mgCur) + fmtPtDelta(ptDiff(mgCur, mgPrev))
  ];

  if (cur.shipped_count === 0 && prev.shipped_count === 0) {
    return leadIn + '、出荷実績が無いため比較できる指標がありません。';
  }
  return leadIn + '、' + parts.join('、') + 'という結果でした。';
}

function renderInsights() {
  const overallEl = document.getElementById('insightOverall');
  if (overallEl) {
    const dynamicText = buildDynamicPeriodInsight(null);
    const staticText = INSIGHTS.overall || '(手動メモはまだありません)';
    overallEl.textContent = dynamicText + '\n\n(参考)手動メモ: ' + staticText;
  }
  const metaEl = document.getElementById('insightMeta');
  if (metaEl) metaEl.textContent = INSIGHTS.period_label || '';
  const catContainer = document.getElementById('categoryInsights');
  if (catContainer) {
    const byCat = INSIGHTS.by_category || {};
    const cats = categories.filter(c => c !== '不明' && c !== 'default');
    if (!cats.length) {
      catContainer.innerHTML = '<p class="note">カテゴリ別所見は次回の週次更新で追加されます。</p>';
    } else {
      // 全カテゴリを並べると読み切れないため、「特筆すべきカテゴリ」だけに絞る。
      //   悪化: 前期間比でSR率または返金率(対出荷)が0.30ポイント以上悪化
      //   高水準: SR率または返金率が全カテゴリ平均の1.3倍以上
      // どちらかに該当するものを、悪化幅・超過幅の大きい順に最大6件表示する。
      const gran = granSel.value, pkey = periodSel.value;
      const periods = availablePeriods(gran);
      const idx = periods.findIndex(p => p.key === pkey);
      const prevKey = idx > 0 ? periods[idx - 1].key : null;
      const whole = deriveRates(computeAgg(gran, pkey, null));
      const avgSr = whole.sr_rate || 0, avgRefund = whole.shipped_count ? (whole.refund_count / whole.shipped_count) : 0;

      const scored = cats.map(k => {
        const filt = r => r.category === k;
        const cur = deriveRates(computeAgg(gran, pkey, filt));
        const shipped = cur.shipped_count || 0;
        if (shipped < 30) return null;   // 母数が小さすぎるカテゴリは対象外
        const curRefundRate = shipped ? cur.refund_count / shipped : 0;
        let dSr = 0, dRefund = 0;
        if (prevKey) {
          const prv = deriveRates(computeAgg(gran, prevKey, filt));
          const prvShipped = prv.shipped_count || 0;
          if (prvShipped >= 30) {
            dSr = ((cur.sr_rate || 0) - (prv.sr_rate || 0)) * 100;
            dRefund = (curRefundRate - (prvShipped ? prv.refund_count / prvShipped : 0)) * 100;
          }
        }
        const worsened = Math.max(dSr, dRefund);
        const excess = Math.max(
          avgSr ? (cur.sr_rate || 0) / avgSr : 0,
          avgRefund ? curRefundRate / avgRefund : 0
        );
        const reasons = [];
        if (worsened >= 0.30) reasons.push('前期間比で' + worsened.toFixed(2) + 'pt悪化');
        if (excess >= 1.3) reasons.push('全体平均の' + excess.toFixed(1) + '倍の水準');
        if (!reasons.length) return null;
        return { name: k, score: Math.max(worsened, (excess - 1) * 10), reasons: reasons.join(' / ') };
      }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 6);

      if (!scored.length) {
        catContainer.innerHTML = '<p class="note">この期間は、悪化・高水準のいずれにも該当する特筆すべきカテゴリはありませんでした(母数30件以上のカテゴリが対象)。</p>';
      } else {
        catContainer.innerHTML = scored.map(x => {
          const dyn = buildDynamicPeriodInsight(r => r.category === x.name);
          const memo = byCat[x.name] ? ('<br>(参考)手動メモ: ' + byCat[x.name]) : '';
          return '<div class="category-insight-card"><h4>' + x.name +
                 ' <span class="badge">' + x.reasons + '</span></h4><div>' + dyn + memo + '</div></div>';
        }).join('');
      }
    }
  }
}

function renderOverallTrendCharts() {
  const granularity = granSel.value;
  const trend = buildTrendAligned(granularity, null);
  const labels = trend.map(t => t.label);
  const specs = [
    ['trend_inquiry', 'inquiry_count', 'inquiry_rate', '問合せ件数', '問合せ率', '件数', false],
    ['trend_sr', 'sr_count', 'sr_rate', 'SR発生件数', 'SR発生率', '件数', false],
    ['trend_refund', 'refund_amount', 'refund_rate', '返金額', '返金額率', '金額(¥)', true],
    ['trend_question', 'question_count', 'question_rate', '質問数', '質問率', '件数', false],
    ['trend_profit', 'final_profit', 'profit_margin', '最終利益', '利益率', '金額(¥)', true],
    ['trend_junk', 'junk_listed_count', 'junk_listed_rate', 'ジャンク出品件数', 'ジャンク出品率', '件数', false]
  ];
  // 月次のときは決算月(7月〜6月)を横軸に、期ごとの系列を重ねた昨対比較にする
  const yoy = isYoyMode() ? buildYoyMonthlySeries(null) : null;
  specs.forEach(([id, bar, line, barLabel, lineLabel, leftLabel, money]) => {
    if (yoy && yoy.fyNums.length > 1) {
      renderChart(id, yoyMonthlyConfig(yoy, bar, line, barLabel, lineLabel, leftLabel, money));
    } else {
      renderChart(id, dualAxisConfig(labels, trend.map(t => t[bar]), trend.map(t => t[line]),
        barLabel, lineLabel, leftLabel, money));
    }
  });
}

const COMMON_SPECS = [
  { key: 'question', bar: 'question_count', line: 'question_rate', barLabel: '質問数', lineLabel: '質問率', title: 'ヤフオク質問 件数・率', leftLabel: '件数', money: false },
  { key: 'sr', bar: 'sr_count', line: 'sr_rate', barLabel: 'SR発生件数', lineLabel: 'SR発生率', title: 'SR発生 件数・率', leftLabel: '件数', money: false },
  { key: 'refund', bar: 'refund_amount', line: 'refund_rate', barLabel: '返金額', lineLabel: '返金額率', title: '返金額・率', leftLabel: '金額(¥)', money: true },
  { key: 'inquiry', bar: 'inquiry_count', line: 'inquiry_rate', barLabel: '問合せ件数', lineLabel: '問合せ率', title: '問合せ 件数・率（落札後商品質問）', leftLabel: '件数', money: false },
  { key: 'profit', bar: 'final_profit', line: 'profit_margin', barLabel: '最終利益', lineLabel: '利益率', title: '最終利益・利益率', leftLabel: '金額(¥)', money: true }
];
// C項目: 「ジャンク出荷 件数・率」を「ジャンク出品 件数・率」に変更し、参照データを
// junk_shipped_count/shipped_count から junk_listed_count/listed_count に変更。
// D項目: 詳細テーブルの「出荷商品数・出品数・返金件数」も横棒グラフ的な複数系列バーで
// 比較できるようにする(renderBreakdownGridのgrouped対応、下記参照)。
const SHIP_LISTED_SPEC = {
  key: 'shiplisted', title: '出荷商品数・出品数・返金件数', grouped: true,
  fields: [
    { key: 'shipped_count', label: '出荷商品数', color: '#5b8def' },
    { key: 'listed_count', label: '出品数', color: '#2ecc71' },
    { key: 'refund_count', label: '返金件数', color: '#e74c3c' }
  ]
};
function groupedCountConfig(labels, series) {
  return {
    type: 'bar',
    data: { labels, datasets: series.map((s, i) => ({ label: s.label, data: s.data, backgroundColor: s.color || PALETTE[i % PALETTE.length] })) },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom', labels: { boxWidth: 10, font: { size: 9.5 } } },
        tooltip: { callbacks: { label: c => (c.dataset.label || '') + ': ' + Math.round(c.parsed.y).toLocaleString('ja-JP') } }
      },
      scales: {
        y: { title: { display: true, text: '件数', font: { size: 10.5 } }, ticks: { font: { size: 10 } } },
        x: { ticks: { font: { size: 9.5 }, maxRotation: 55 } }
      }
    }
  };
}

const JUNK_SPEC = { key: 'junk', bar: 'junk_listed_count', line: 'junk_listed_rate', barLabel: 'ジャンク出品件数', lineLabel: 'ジャンク出品率', title: 'ジャンク出品 件数・率', leftLabel: '件数', money: false };
const AVG_REFUND_SPEC = { key: 'avgrefund', bar: 'avg_refund_amount', barLabel: '1件あたり返金額', title: '1件あたりの返金額(返金額Average)', single: true };

function renderBreakdownGrid(gridId, data, specs) {
  const grid = document.getElementById(gridId);
  grid.innerHTML = specs.map(s => '<div class="card mini-chart-card"><h4>' + s.title + '</h4><canvas id="' + gridId + '_' + s.key + '"></canvas></div>').join('');
  const labels = data.map(d => d.name);
  specs.forEach(s => {
    const canvasId = gridId + '_' + s.key;
    if (s.grouped) {
      renderChart(canvasId, groupedCountConfig(labels, s.fields.map(f => ({ label: f.label, data: data.map(d => d[f.key]), color: f.color }))));
    } else if (s.single) {
      renderChart(canvasId, singleAxisMoneyConfig(labels, data.map(d => d[s.bar]), s.barLabel));
    } else {
      renderChart(canvasId, dualAxisConfig(labels, data.map(d => d[s.bar]), data.map(d => d[s.line]), s.barLabel, s.lineLabel, s.leftLabel, s.money));
    }
  });
}

function renderLocationBreakdown() {
  const granularity = granSel.value;
  const periodKey = periodSel.value;
  document.getElementById('periodLabelLoc').textContent = currentPeriodLabel();
  let data = buildBreakdown(granularity, 'location', periodKey, null);
  data.sort((a, b) => (b.inquiry_count + b.sr_count + b.question_count) - (a.inquiry_count + a.sr_count + a.question_count));
  // D項目: 返金件数・返金1件あたりの金額もグラフで比較できるようにする(カテゴリ別内訳と同じ仕様)
  renderBreakdownGrid('locChartsGrid', data, COMMON_SPECS.concat([AVG_REFUND_SPEC, JUNK_SPEC, SHIP_LISTED_SPEC]));
}

function renderCategoryBreakdown() {
  const granularity = granSel.value;
  const periodKey = periodSel.value;
  document.getElementById('periodLabelCat').textContent = currentPeriodLabel();
  let data = buildBreakdown(granularity, 'category', periodKey, null);
  data.sort((a, b) => (b.inquiry_count + b.sr_count + b.question_count) - (a.inquiry_count + a.sr_count + a.question_count));
  renderBreakdownGrid('catChartsGrid', data, COMMON_SPECS.concat([AVG_REFUND_SPEC, JUNK_SPEC, SHIP_LISTED_SPEC]));
}

// ---------- SR大項目(分類)の内訳 ----------
function buildSrMajorByDim(dim, granularity, periodKey, rowFilter) {
  let rows = SR_MAJOR_ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  if (rowFilter) rows = rows.filter(rowFilter);
  const majorsSet = new Set();
  const map = new Map();
  rows.forEach(r => {
    const k = dim ? r[dim] : '全社';
    majorsSet.add(r.major);
    if (!map.has(k)) map.set(k, {});
    map.get(k)[r.major] = (map.get(k)[r.major] || 0) + r.count;
  });
  return { labels: Array.from(map.keys()), majors: Array.from(majorsSet), map };
}

// ============================================================================
// SR改善分析(①全拠点 / ②全カテゴリ / ③拠点×カテゴリ に共通で表示)
//   ・小項目別の件数と損失額(返金額+返送料)
//   ・小項目別の推移
//   ・原因分類 × 原因元 のクロス集計(どの工程を直すべきかの特定)
//   ・拠点 × 小項目 のヒートマップ(拠点ごとの指導ポイント)
// 原因データは「CS_返金の管理用メモ」を優先し、無い週は「CS_登録【分類用】」で補完している。
// ============================================================================
function impCell(v, opt) {
  const o = opt || {};
  return '<td style="padding:5px 9px;border:1px solid #e3e5e8;text-align:' + (o.num ? 'right' : 'left') +
    (o.bg ? ';background:' + o.bg : '') + (o.bold ? ';font-weight:700' : '') + ';">' + v + '</td>';
}

function impTable(head, bodyHtml) {
  return '<table style="width:100%;border-collapse:collapse;font-size:12.5px;"><thead><tr>' +
    head.map(h => '<th style="padding:5px 9px;border:1px solid #e3e5e8;background:#f5f6f8;text-align:' +
      (h.num ? 'right' : 'left') + ';">' + h.name + '</th>').join('') +
    '</tr></thead><tbody>' + bodyHtml + '</tbody></table>';
}

function renderImprovementSection(prefix, rowFilter) {
  const granularity = granSel.value, periodKey = periodSel.value;
  const inPeriod = r => (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  const flt = r => inPeriod(r) && (!rowFilter || rowFilter(r));
  const el = id => document.getElementById(prefix + id);
  const pl = el('ImpPeriod'); if (pl) pl.textContent = currentPeriodLabel();

  // --- 小項目別: 件数と損失額(損失額は原因データの返金額を小項目に配分せず、
  //     同じ期間・同じ絞り込みの返金額を大項目経由で按分せずに「返金メモの原因別金額」を別表で示す) ---
  const srRows = SR_MAJOR_ROWS.filter(flt);
  const byMinor = new Map();
  srRows.forEach(r => {
    const k = r.major + ' > ' + (r.minor || '(小項目なし)');
    byMinor.set(k, (byMinor.get(k) || 0) + (r.count || 0));
  });
  const minorList = Array.from(byMinor.entries()).sort((a, b) => b[1] - a[1]);
  const srTotal = minorList.reduce((a, x) => a + x[1], 0);

  // 原因データ(返金メモ優先)から損失額を集計
  const causeRows = CAUSE_ROWS.filter(flt);
  const lossByCause = new Map();
  causeRows.forEach(r => {
    const k = r.cause_major || '(不明)';
    const o = lossByCause.get(k) || { n: 0, amt: 0 };
    o.n += r.count || 0; o.amt += r.refund_amount || 0;
    lossByCause.set(k, o);
  });

  renderChart(prefix + 'MinorChart', {
    type: 'bar',
    data: {
      labels: minorList.slice(0, 15).map(x => x[0]),
      datasets: [{ label: 'SR件数', data: minorList.slice(0, 15).map(x => x[1]), backgroundColor: '#5b8def' }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, title: { display: true, text: '小項目別のSR件数(上位15)' } },
      scales: { x: { beginAtZero: true } }
    }
  });

  // 「大項目(小計) → 小項目」の階層表示にする(原因元→原因分類の表と同じ形式)。
  // 元データは大項目・小項目・件数を持つので、既存のピボット表描画をそのまま使う。
  const minorPivotRows = srRows.map(r => ({
    major: r.major || '(大項目なし)',
    minor: r.minor || '(小項目なし)',
    count: r.count || 0
  }));
  renderCausePivotTableInto(prefix + 'MinorTable', minorPivotRows, 'major', 'minor', '大項目', '小項目');

  // --- 小項目別の推移(上位6) ---
  const periods = availablePeriods(granularity);
  const topMinors = minorList.slice(0, 6).map(x => x[0]);
  const acc = new Map();
  SR_MAJOR_ROWS.filter(r => (!rowFilter || rowFilter(r))).forEach(r => {
    const pk = periodKeyFor(r, granularity);
    if (pk.key == null) return;
    const k = r.major + ' > ' + (r.minor || '(小項目なし)');
    if (!topMinors.includes(k)) return;
    if (!acc.has(pk.label)) acc.set(pk.label, {});
    const o = acc.get(pk.label);
    o[k] = (o[k] || 0) + (r.count || 0);
  });
  // 月次のときは決算月(7月〜6月)を横軸に、期ごとの系列で昨対比較する
  let minorTrendDone = false;
  if (isYoyMode()) {
    const order12 = [];
    for (let i = 0; i < 12; i++) order12.push(((FY_START_M - 1 + i) % 12) + 1);
    const byFyMonth = new Map();   // fyNum -> month -> 件数(上位小項目の合計)
    SR_MAJOR_ROWS.forEach(r => {
      if (rowFilter && !rowFilter(r)) return;
      const k = r.major + ' > ' + (r.minor || '(小項目なし)');
      if (!topMinors.includes(k)) return;
      const fi = fiscalInfo(r.year_month);
      const mm = parseInt(r.year_month.split('-')[1], 10);
      if (!byFyMonth.has(fi.fyNum)) byFyMonth.set(fi.fyNum, new Map());
      const mp = byFyMonth.get(fi.fyNum);
      mp.set(mm, (mp.get(mm) || 0) + (r.count || 0));
    });
    const fys = Array.from(byFyMonth.keys()).sort((a, b) => a - b);
    if (fys.length > 1) {
      const barColors = ['#c9dbfa', '#8fb3f0', '#5b8def', '#12203f'];
      renderChart(prefix + 'MinorTrend', {
        type: 'bar',
        data: {
          labels: order12.map(m => m + '月'),
          datasets: fys.map((fy, i) => ({
            label: ordinalSuffix(fy) + ' 上位小項目のSR件数',
            data: order12.map(m => (byFyMonth.get(fy) || new Map()).get(m) || 0),
            backgroundColor: barColors[i % barColors.length]
          }))
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { title: { display: true, text: '上位小項目のSR件数(期別の昨対比較)' },
            legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } },
          scales: { y: { beginAtZero: true } }
        }
      });
      minorTrendDone = true;
    }
  }
  if (!minorTrendDone) renderChart(prefix + 'MinorTrend', {
    type: 'line',
    data: {
      labels: periods.map(p => p.label),
      datasets: topMinors.map((k, i) => ({
        label: k, data: periods.map(p => (acc.get(p.label) || {})[k] || 0),
        borderColor: PALETTE[i % PALETTE.length], backgroundColor: PALETTE[i % PALETTE.length], tension: 0.25
      }))
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { title: { display: true, text: '小項目別SR件数の推移(上位6)' }, legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } },
      scales: { y: { beginAtZero: true } }
    }
  });

  // --- 原因分類 × 原因元 のクロス集計 ---
  const parts = Array.from(new Set(causeRows.map(r => r.cause_part || '(不明)')));
  const majors = Array.from(new Set(causeRows.map(r => r.cause_major || '(不明)')));
  const cross = new Map();
  causeRows.forEach(r => {
    const k = (r.cause_major || '(不明)') + '|' + (r.cause_part || '(不明)');
    const o = cross.get(k) || { n: 0, amt: 0 };
    o.n += r.count || 0; o.amt += r.refund_amount || 0;
    cross.set(k, o);
  });
  const partTotals = parts.map(p => ({ p, n: majors.reduce((a, mj) => a + ((cross.get(mj + '|' + p) || {}).n || 0), 0) }))
    .sort((a, b) => b.n - a.n).slice(0, 8);
  const majorTotals = majors.map(mj => ({ mj, n: parts.reduce((a, p) => a + ((cross.get(mj + '|' + p) || {}).n || 0), 0),
    amt: parts.reduce((a, p) => a + ((cross.get(mj + '|' + p) || {}).amt || 0), 0) }))
    .sort((a, b) => b.n - a.n);
  const maxCross = Math.max(1, ...majorTotals.flatMap(x => partTotals.map(pt => (cross.get(x.mj + '|' + pt.p) || {}).n || 0)));
  const crossBody = majorTotals.map(x =>
    '<tr>' + impCell(x.mj) +
    partTotals.map(pt => {
      const n = (cross.get(x.mj + '|' + pt.p) || {}).n || 0;
      const bg = n ? 'rgba(224,101,58,' + (0.10 + 0.55 * (n / maxCross)).toFixed(2) + ')' : '';
      return impCell(n ? fmtInt(n) : '-', { num: 1, bg });
    }).join('') +
    impCell(fmtInt(x.n), { num: 1, bold: 1 }) + impCell(fmtYen(x.amt), { num: 1, bold: 1 }) + '</tr>').join('');
  const cc = el('CauseCross');
  if (cc) {
    cc.innerHTML = causeRows.length
      ? impTable([{ name: '原因分類 \\ 原因元' }].concat(partTotals.map(pt => ({ name: pt.p, num: 1 })))
          .concat([{ name: '合計件数', num: 1 }, { name: '返金額', num: 1 }]), crossBody) +
        '<p class="note">返金額は「CS_返金の管理用メモに原因が記載された行」の合計です(運用変更前の期間は分類用ファイルの原因を使うため0円表示になります)。</p>'
      : '<p class="note">この期間は原因データがありません。</p>';
  }

  // --- 拠点 × 小項目 ヒートマップ ---
  const locs = Array.from(new Set(srRows.map(r => r.location))).filter(Boolean);
  const heatMinors = minorList.slice(0, 8).map(x => x[0]);
  const heat = new Map();
  srRows.forEach(r => {
    const k = r.location + '|' + r.major + ' > ' + (r.minor || '(小項目なし)');
    heat.set(k, (heat.get(k) || 0) + (r.count || 0));
  });
  const locTotals = locs.map(l => ({ l, n: heatMinors.reduce((a, k) => a + (heat.get(l + '|' + k) || 0), 0) }))
    .sort((a, b) => b.n - a.n);
  const maxHeat = Math.max(1, ...locTotals.flatMap(x => heatMinors.map(k => heat.get(x.l + '|' + k) || 0)));
  const heatBody = locTotals.map(x =>
    '<tr>' + impCell(x.l) +
    heatMinors.map(k => {
      const n = heat.get(x.l + '|' + k) || 0;
      const bg = n ? 'rgba(91,141,239,' + (0.10 + 0.60 * (n / maxHeat)).toFixed(2) + ')' : '';
      return impCell(n ? fmtInt(n) : '-', { num: 1, bg });
    }).join('') + impCell(fmtInt(x.n), { num: 1, bold: 1 }) + '</tr>').join('');
  const hm = el('MinorHeat');
  if (hm) {
    hm.innerHTML = heatMinors.length
      ? impTable([{ name: '拠点 \\ 小項目' }].concat(heatMinors.map(k => ({ name: k.split(' > ')[1] || k, num: 1 })))
          .concat([{ name: '合計', num: 1 }]), heatBody)
      : '<p class="note">この期間はSRの小項目データがありません。</p>';
  }

  // --- 所見(自動生成) ---
  const ins = el('ImpInsight');
  if (ins) {
    const lossListAll = Array.from(lossByCause.entries()).sort((a, b) => b[1].amt - a[1].amt);
    if (!srTotal && !lossListAll.length) { ins.textContent = 'この期間のSRデータがありません。'; }
    else if (!srTotal) {
      // SR分類は未入力でも、返金メモの原因は入っていることがある(運用の切り替え期)
      const t = lossListAll[0];
      ins.textContent = 'この期間はSRの分類データがありませんが、返金メモの原因記載は' +
        fmtInt(lossListAll.reduce((a, x) => a + x[1].n, 0)) + '件あります。金額が最大の原因は「' + t[0] + '」で' +
        fmtYen(t[1].amt) + '(' + fmtInt(t[1].n) + '件)です。';
    }
    else {
      const lines = [];
      const top = minorList[0];
      lines.push('SR' + fmtInt(srTotal) + '件のうち最も多いのは「' + top[0] + '」で' + fmtInt(top[1]) + '件(' +
        fmtPct(top[1] / srTotal) + ')。上位3項目で' +
        fmtPct(minorList.slice(0, 3).reduce((a, x) => a + x[1], 0) / srTotal) + 'を占めます。');
      const lossList = Array.from(lossByCause.entries()).sort((a, b) => b[1].amt - a[1].amt);
      if (lossList.length && lossList[0][1].amt > 0) {
        lines.push('金額で見ると「' + lossList[0][0] + '」の返金が最大で' + fmtYen(lossList[0][1].amt) +
          '(' + fmtInt(lossList[0][1].n) + '件、1件あたり' + fmtYen(lossList[0][1].amt / lossList[0][1].n) + ')。');
      }
      if (locTotals.length > 1) {
        const worst = locTotals[0];
        const wk = heatMinors.map(k => ({ k, n: heat.get(worst.l + '|' + k) || 0 })).sort((a, b) => b.n - a.n)[0];
        if (wk && wk.n) lines.push('拠点別では「' + worst.l + '」が' + fmtInt(worst.n) + '件と最多で、内訳は「' +
          (wk.k.split(' > ')[1] || wk.k) + '」' + fmtInt(wk.n) + '件が中心です。');
      }
      lines.push('改善の着手順は、件数の多い小項目(検品・記載の標準化)と、1件あたり返金額の大きい原因(高額品の検品強化)の2軸で判断してください。');
      ins.textContent = lines.join('\n');
    }
  }
}

function renderOverallSrMajorChart() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('overallMajorPeriodLabel').textContent = currentPeriodLabel();
  const { majors, map } = buildSrMajorByDim(null, granularity, periodKey, null);
  if (!majors.length) {
    renderChart('overallMajorChart', { type: 'doughnut', data: { labels: ['データなし'], datasets: [{ data: [1], backgroundColor: ['#e3e5e8'] }] }, options: { plugins: { legend: { display: false } } } });
    return;
  }
  const counts = majors.map(m => (map.get('全社') || {})[m] || 0);
  renderChart('overallMajorChart', donutMajorConfig(majors, counts));
}

function renderLocationSrMajorChart() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('locMajorPeriodLabel').textContent = currentPeriodLabel();
  const { labels, majors, map } = buildSrMajorByDim('location', granularity, periodKey, null);
  labels.sort((a, b) => {
    const sum = obj => Object.values(obj || {}).reduce((s, v) => s + v, 0);
    return sum(map.get(b)) - sum(map.get(a));
  });
  renderChart('locMajorChart', stackedMajorConfig(labels, majors, map));
}

function renderCategorySrMajorChart() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('catMajorPeriodLabel').textContent = currentPeriodLabel();
  const { labels, majors, map } = buildSrMajorByDim('category', granularity, periodKey, null);
  labels.sort((a, b) => {
    const sum = obj => Object.values(obj || {}).reduce((s, v) => s + v, 0);
    return sum(map.get(b)) - sum(map.get(a));
  });
  renderChart('catMajorChart', stackedMajorConfig(labels, majors, map));
}

// ---------- SR原因分類ピボット(カメラ系専用、多方向) ----------
function buildCausePivotTree(rows, primaryField, secondaryField) {
  const tree = new Map();
  let total = 0;
  rows.forEach(r => {
    if (!tree.has(r[primaryField])) tree.set(r[primaryField], new Map());
    const m = tree.get(r[primaryField]);
    m.set(r[secondaryField], (m.get(r[secondaryField]) || 0) + r.count);
    total += r.count;
  });
  return { tree, total };
}

// D項目: 件数のみの小計付き表に、総計に対する割合(%、小数点以下1桁)の列を追加。
function renderCausePivotTableInto(containerId, rows, primaryField, secondaryField, primaryLabel, secondaryLabel) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const { tree, total } = buildCausePivotTree(rows, primaryField, secondaryField);
  if (total === 0) {
    container.innerHTML = '<p class="note">選択条件に合うデータがありません。</p>';
    return;
  }
  const pct = c => total ? (c / total * 100).toFixed(1) + '%' : '-';
  const entries = Array.from(tree.entries()).map(([k, m]) => {
    const arr = Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
    const sum = arr.reduce((s, [, c]) => s + c, 0);
    return { k, arr, sum };
  }).sort((a, b) => b.sum - a.sum);

  let out = '<table><thead><tr><th>' + primaryLabel + '</th><th>' + secondaryLabel + '</th><th>件数</th><th>割合</th></tr></thead><tbody>';
  entries.forEach(({ k, arr, sum }) => {
    out += '<tr class="major-row"><td>' + k + '(小計)</td><td></td><td>' + sum + '</td><td>' + pct(sum) + '</td></tr>';
    arr.forEach(([p, c]) => { out += '<tr><td></td><td>' + p + '</td><td>' + c + '</td><td>' + pct(c) + '</td></tr>'; });
  });
  out += '<tr class="total-row"><td>総計</td><td></td><td>' + total + '</td><td>100.0%</td></tr></tbody></table>';
  container.innerHTML = out;
}

function aggregateSingleDim(rows, field) {
  const m = new Map();
  rows.forEach(r => m.set(r[field], (m.get(r[field]) || 0) + r.count));
  return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
}

function renderCausePivotSection(prefix, rows) {
  const barMajorId = prefix + 'CauseMajorBarChart';
  const barPartId = prefix + 'CausePartBarChart';
  const pivotMajorId = prefix + 'CausePivotByMajor';
  const pivotPartId = prefix + 'CausePivotByPart';
  if (!rows.length) {
    renderChart(barMajorId, { type: 'bar', data: { labels: ['データなし'], datasets: [{ data: [0], backgroundColor: ['#e3e5e8'] }] }, options: { plugins: { legend: { display: false } } } });
    renderChart(barPartId, { type: 'bar', data: { labels: ['データなし'], datasets: [{ data: [0], backgroundColor: ['#e3e5e8'] }] }, options: { plugins: { legend: { display: false } } } });
    const m = document.getElementById(pivotMajorId), p = document.getElementById(pivotPartId);
    if (m) m.innerHTML = '<p class="note">選択条件に合うデータがありません。</p>';
    if (p) p.innerHTML = '<p class="note">選択条件に合うデータがありません。</p>';
    return;
  }
  renderCausePivotTableInto(pivotMajorId, rows, 'cause_major', 'cause_part', '原因分類', '原因元');
  renderCausePivotTableInto(pivotPartId, rows, 'cause_part', 'cause_major', '原因元', '原因分類');
  const byMajor = aggregateSingleDim(rows, 'cause_major');
  const byPart = aggregateSingleDim(rows, 'cause_part');
  renderChart(barMajorId, singleAxisCountConfig(byMajor.map(x => x[0]), byMajor.map(x => x[1]), '件数', '#5b8def'));
  renderChart(barPartId, singleAxisCountConfig(byPart.map(x => x[0]), byPart.map(x => x[1]), '件数', '#e0653a'));
}

// ---------- 拠点×カテゴリ別 ジャンク出品比率ヒートマップ(①で使用) ----------
// C項目: junk_shipped_count/shipped_count ベースから junk_listed_count/listed_count
// ベースに変更(「ジャンク出品」比率として統一)。
function heatColor(rate) {
  if (rate == null || isNaN(rate)) return '#f5f6f8';
  const capped = Math.max(0, Math.min(rate, 0.5)) / 0.5;
  const g = Math.round(255 - capped * 180), b = Math.round(255 - capped * 180);
  return 'rgb(255,' + g + ',' + b + ')';
}

function renderJunkHeatmap() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('junkHeatmapPeriodLabel').textContent = currentPeriodLabel();
  const rows = ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);

  const cellMap = new Map(), locListed = new Map(), catListed = new Map();
  rows.forEach(r => {
    const k = r.location + '||' + r.category;
    if (!cellMap.has(k)) cellMap.set(k, { junk: 0, listed: 0 });
    const o = cellMap.get(k);
    o.junk += (r.junk_listed_count || 0);
    o.listed += (r.listed_count || 0);
    locListed.set(r.location, (locListed.get(r.location) || 0) + (r.listed_count || 0));
    catListed.set(r.category, (catListed.get(r.category) || 0) + (r.listed_count || 0));
  });

  const locsSorted = Array.from(locListed.keys()).filter(l => l !== '(不明)' && locListed.get(l) > 0)
    .sort((a, b) => locListed.get(b) - locListed.get(a));
  const catsSorted = Array.from(catListed.keys()).filter(c => c !== '不明' && c !== 'default' && catListed.get(c) > 0)
    .sort((a, b) => catListed.get(b) - catListed.get(a)).slice(0, 10);

  const wrap = document.getElementById('junkHeatmapTable');
  if (!locsSorted.length || !catsSorted.length) {
    wrap.innerHTML = '<p class="note">選択条件に合うデータがありません。</p>';
    return;
  }

  let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>' +
    '<th style="text-align:left;padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">拠点＼カテゴリ</th>';
  catsSorted.forEach(c => { html += '<th style="padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;white-space:nowrap;">' + c + '</th>'; });
  html += '</tr></thead><tbody>';
  locsSorted.forEach(loc => {
    html += '<tr><td style="padding:6px 10px;border:1px solid #e3e5e8;font-weight:600;white-space:nowrap;">' + loc + '</td>';
    catsSorted.forEach(c => {
      const o = cellMap.get(loc + '||' + c);
      const listed = o ? o.listed : 0;
      const rate = listed ? o.junk / listed : null;
      const label = rate == null ? '-' : (rate * 100).toFixed(1) + '%';
      html += '<td style="padding:6px 10px;border:1px solid #e3e5e8;text-align:center;background:' + heatColor(rate) + ';" title="出品' + listed + '件・ジャンク' + (o ? o.junk : 0) + '件">' + label + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

// ---------- detail table (Grid.js, raw numeric cell values + formatter for correct sorting) ----------
// 詳細テーブルのヒートマップ色付け。
// 「値が大きいほど悪い」指標(SR率・返金額・返金額率・返金1件あたり・質問率・ジャンク出品率)を
// 列ごとの最大値で正規化し、赤の濃さで表す。列内の最大値は renderTableInto で毎回計算する。
let DETAIL_HEAT_MAX = {};

function heatCell(text, value, colKey) {
  const max = DETAIL_HEAT_MAX[colKey] || 0;
  if (!(value > 0) || !(max > 0)) return text;
  const ratio = Math.min(1, value / max);
  // 薄すぎると見えないので下限を持たせる。上位ほど濃い赤になる。
  const alpha = (0.06 + 0.44 * ratio).toFixed(3);
  return gridjs.html('<div style="background:rgba(224,101,58,' + alpha +
    ');margin:-8px -10px;padding:8px 10px;text-align:right;">' + text + '</div>');
}

const DETAIL_COLUMNS = (nameLabel) => [
  { name: nameLabel },
  { name: '問合せ件数', formatter: c => fmtInt(c) },
  { name: '問合せ率', formatter: c => c < 0 ? '-' : fmtPct(c) },
  { name: 'SR件数', formatter: c => fmtInt(c) },
  { name: 'SR率', formatter: c => c < 0 ? '-' : heatCell(fmtPct(c), c, 'sr_rate') },
  { name: '返金額(円)', formatter: c => heatCell(fmtYen(c), c, 'refund_amount') },
  { name: '返金額率', formatter: c => c < 0 ? '-' : heatCell(fmtPct(c), c, 'refund_rate') },
  { name: '返金件数', formatter: c => fmtInt(c) },
  { name: '返金1件あたり(円)', formatter: c => c < 0 ? '-' : heatCell(fmtYen(c), c, 'avg_refund') },
  { name: '質問数', formatter: c => fmtInt(c) },
  { name: '質問率', formatter: c => c < 0 ? '-' : heatCell(fmtPct(c), c, 'question_rate') },
  { name: 'ジャンク出品件数', formatter: c => fmtInt(c) },
  { name: 'ジャンク出品率', formatter: c => c < 0 ? '-' : heatCell(fmtPct(c), c, 'junk_listed_rate') },
  { name: '出荷商品数', formatter: c => fmtInt(c) },
  { name: '出品数', formatter: c => fmtInt(c) },
  { name: '売上金額(円)', formatter: c => fmtYen(c) },
  { name: '粗利(円)', formatter: c => fmtYen(c) },
  { name: '最終利益(円)', formatter: c => fmtYen(c) },
  { name: '利益率', formatter: c => c <= -999999 ? '-' : fmtPct(c) }
];

function detailRow(d) {
  const R = (v, sentinel) => (v === null || v === undefined) ? sentinel : v;
  return [
    d.name,
    d.inquiry_count, R(d.inquiry_rate, -1),
    d.sr_count, R(d.sr_rate, -1),
    d.refund_amount, R(d.refund_rate, -1),
    d.refund_count, R(d.avg_refund_amount, -1),
    d.question_count, R(d.question_rate, -1),
    d.junk_listed_count, R(d.junk_listed_rate, -1),
    d.shipped_count, d.listed_count, d.sales_amount,
    d.gross_profit, d.final_profit, R(d.profit_margin, -999999)
  ];
}

// Grid.js instances are cached per container and updated via updateConfig()+forceRender()
// rather than recreated each time. Recreating a `new gridjs.Grid()` on a container that
// already hosts a rendered grid (even after clearing innerHTML) leaves Grid.js's internal
// state out of sync with the DOM, so a later render can silently keep showing the row
// count from the very first render regardless of the new data passed in. Reusing the same
// instance and calling updateConfig()/forceRender() is the officially supported way to
// refresh an existing grid and avoids that bug entirely.
const gridRegistry = {};
function renderTableInto(containerId, data, nameLabel, limit) {
  const container = document.getElementById(containerId);
  // 色付けの基準になる列内の最大値を先に求める(値が大きいほど濃い赤にする)
  const maxOf = f => data.reduce((a, d) => Math.max(a, (d[f] != null && d[f] > 0) ? d[f] : 0), 0);
  DETAIL_HEAT_MAX = {
    sr_rate: maxOf('sr_rate'),
    refund_amount: maxOf('refund_amount'),
    refund_rate: maxOf('refund_rate'),
    avg_refund: maxOf('avg_refund_amount'),
    question_rate: maxOf('question_rate'),
    junk_listed_rate: maxOf('junk_listed_rate')
  };
  const columns = DETAIL_COLUMNS(nameLabel);
  const rows = data.map(detailRow);
  const paginationCfg = { limit: limit || 20 };
  if (gridRegistry[containerId]) {
    gridRegistry[containerId].updateConfig({ columns, data: rows, pagination: paginationCfg }).forceRender();
  } else {
    container.innerHTML = '';
    const grid = new gridjs.Grid({
      columns, data: rows, sort: true, search: true,
      pagination: paginationCfg,
      style: { table: { fontSize: '12px', width: '100%' } },
      width: '100%'
    });
    gridRegistry[containerId] = grid;
    grid.render(container);
  }
}

// renderTableInto の汎用版。呼び出し側が列定義を渡せる(⑥⑦ページの独自集計テーブル用)。
// 同じgridRegistryを使い、既存テーブルと同じ再描画バグ回避パターンを踏襲する。
function renderSimpleTableInto(containerId, columns, rows, limit) {
  const container = document.getElementById(containerId);
  const paginationCfg = { limit: limit || 20 };
  if (gridRegistry[containerId]) {
    gridRegistry[containerId].updateConfig({ columns, data: rows, pagination: paginationCfg }).forceRender();
  } else {
    container.innerHTML = '';
    const grid = new gridjs.Grid({
      columns, data: rows, sort: true, search: true,
      pagination: paginationCfg,
      style: { table: { fontSize: '12px', width: '100%' } },
      width: '100%'
    });
    gridRegistry[containerId] = grid;
    grid.render(container);
  }
}

// ---------- ⑥ コンディション・価格帯別分析ページ ----------
function conditionSortKey(c) {
  const idx = CONDITION_ORDER.indexOf(c);
  return idx === -1 ? 999 : idx;
}

function priceBandSortKey(label) {
  if (label === '0万円未満') return -1;
  if (label === '不明') return 999;
  const m = label.match(/^(\d+)/);
  return m ? parseInt(m[1], 10) : 999;
}

function buildConditionTrendMap(granularity) {
  const periods = availablePeriods(granularity);
  const map = new Map(); // periodLabel -> { condition: count }
  CONDITION_ROWS.forEach(r => {
    const pk = periodKeyFor(r, granularity);
    if (!map.has(pk.label)) map.set(pk.label, {});
    const o = map.get(pk.label);
    o[r.condition] = (o[r.condition] || 0) + (r.count || 0);
  });
  const labels = periods.map(p => p.label);
  const present = Array.from(new Set(CONDITION_ROWS.map(r => r.condition)));
  const majors = CONDITION_ORDER.filter(c => present.includes(c)).concat(present.filter(c => !CONDITION_ORDER.includes(c)));
  return { labels, majors, map };
}

// ============================================================================
// ④コンディション / ⑤価格帯ページ(共通実装)
// 出荷・売上・粗利に加えて、SR・問合せ・返金・質問・出品を同じ切り口で分析する。
// 率の定義は①〜③ページと完全に同じ: SR率=SR÷出荷、返金率=返金額÷売上、質問率=質問÷出品。
// ============================================================================
const CP_FIELDS = ['count', 'shipped_count', 'sales_amount', 'gross_profit',
                   'inquiry_count', 'sr_count', 'refund_count', 'refund_amount',
                   'question_count', 'listed_count'];

function cpRates(d) {
  const shipped = d.shipped_count || d.count || 0;
  return {
    shipped: shipped,
    sr_rate: shipped ? d.sr_count / shipped : null,
    refund_rate: d.sales_amount ? d.refund_amount / d.sales_amount : null,
    refund_cnt_rate: shipped ? d.refund_count / shipped : null,
    question_rate: d.listed_count ? d.question_count / d.listed_count : null,
    margin: d.sales_amount ? d.gross_profit / d.sales_amount : null
  };
}

// 選択されたカテゴリ・拠点による絞り込み条件を作る(両ページ共通)
function cpRowFilter(sel) {
  const cats = sel ? getMultiSelectValues(sel) : [];
  const catSet = cats.length ? new Set(cats) : null;
  const loc = locSel.value;
  const useLoc = loc && loc !== ALL_LOC;
  return r => (!catSet || catSet.has(r.category)) && (!useLoc || r.location === loc);
}

function cpSelectionLabel(sel) {
  const cats = sel ? getMultiSelectValues(sel) : [];
  const loc = locSel.value && locSel.value !== ALL_LOC ? locSel.value : '全拠点';
  const catLabel = !cats.length ? '全カテゴリ' : (cats.length === 1 ? cats[0] : cats.join(' + ') + '(' + cats.length + 'カテゴリ合算)');
  return loc + ' × ' + catLabel;
}

// 期間ごとの推移データ(指定の指標を dim 別に集計)を作る
function cpTrend(rowsArr, dim, granularity, rowFilter, valueFn) {
  const periods = availablePeriods(granularity);
  const labels = periods.map(p => p.label);
  const acc = new Map(); // label -> dim -> {分子,分母}
  rowsArr.forEach(r => {
    if (rowFilter && !rowFilter(r)) return;
    const pk = periodKeyFor(r, granularity);
    if (pk.key == null) return;
    if (!acc.has(pk.label)) acc.set(pk.label, {});
    const o = acc.get(pk.label);
    const k = r[dim];
    if (!o[k]) o[k] = { num: 0, den: 0 };
    valueFn(o[k], r);
  });
  return { labels, acc };
}

function cpTrendConfig(rowsArr, dim, granularity, rowFilter, order, valueFn, asRate, title) {
  const { labels, acc } = cpTrend(rowsArr, dim, granularity, rowFilter, valueFn);
  const datasets = order.map((name, i) => ({
    label: name,
    data: labels.map(l => {
      const o = acc.get(l) && acc.get(l)[name];
      if (!o) return null;
      if (!asRate) return o.num;
      return o.den ? o.num / o.den * 100 : null;
    }),
    borderColor: PALETTE[i % PALETTE.length],
    backgroundColor: PALETTE[i % PALETTE.length],
    tension: 0.25,
    spanGaps: true
  }));
  return {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { title: { display: true, text: title }, legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => asRate ? v + '%' : v } } }
    }
  };
}

// 所見の自動生成: 全体傾向と、特に悪い/良い区分を数値つきで指摘する
function cpBuildInsight(data, dimLabel, totals) {
  if (!data.length) return 'データがありません。';
  const t = cpRates(totals);
  const lines = [];
  lines.push('全体: 出荷' + fmtInt(t.shipped) + '件、SR' + fmtInt(totals.sr_count) + '件(SR率' + fmtPct(t.sr_rate) +
             ')、返金' + fmtInt(totals.refund_count) + '件・' + fmtYen(totals.refund_amount) + '円(返金率' + fmtPct(t.refund_rate) +
             ')、出品' + fmtInt(totals.listed_count) + '件に対し質問' + fmtInt(totals.question_count) + '件(質問率' + fmtPct(t.question_rate) + ')。');
  const sized = data.filter(d => (d.shipped_count || d.count || 0) >= 30);
  const pool = sized.length ? sized : data;
  const bySr = pool.slice().sort((a, b) => (cpRates(b).sr_rate || 0) - (cpRates(a).sr_rate || 0));
  const worst = bySr[0], best = bySr[bySr.length - 1];
  if (worst && cpRates(worst).sr_rate != null) {
    const w = cpRates(worst);
    lines.push('SR率が最も高いのは「' + worst.name + '」で' + fmtPct(w.sr_rate) + '(出荷' + fmtInt(w.shipped) + '件・SR' + fmtInt(worst.sr_count) +
               '件)。全体平均' + fmtPct(t.sr_rate) + 'に対し' + (t.sr_rate ? (w.sr_rate / t.sr_rate).toFixed(1) : '-') + '倍で、' + dimLabel + 'の中で最優先の改善対象です。');
  }
  if (best && best !== worst && cpRates(best).sr_rate != null) {
    lines.push('逆に「' + best.name + '」はSR率' + fmtPct(cpRates(best).sr_rate) + 'と最も低く、検品・記載の基準が機能している区分です。');
  }
  const byRefund = pool.slice().sort((a, b) => (cpRates(b).refund_rate || 0) - (cpRates(a).refund_rate || 0));
  if (byRefund[0] && cpRates(byRefund[0]).refund_rate != null) {
    lines.push('返金率(金額ベース)が最も高いのは「' + byRefund[0].name + '」で' + fmtPct(cpRates(byRefund[0]).refund_rate) +
               '(返金額' + fmtYen(byRefund[0].refund_amount) + '円)。SR率と併せて見ると、返金に直結する不具合が多い区分かどうかが判断できます。');
  }
  const byQ = pool.filter(d => d.listed_count >= 30).sort((a, b) => (cpRates(b).question_rate || 0) - (cpRates(a).question_rate || 0));
  if (byQ[0]) {
    lines.push('質問率が最も高いのは「' + byQ[0].name + '」で' + fmtPct(cpRates(byQ[0]).question_rate) +
               '(出品' + fmtInt(byQ[0].listed_count) + '件・質問' + fmtInt(byQ[0].question_count) + '件)。商品説明の情報不足が疑われ、SR発生の予兆としても確認する価値があります。');
  }
  const byMargin = pool.slice().sort((a, b) => (cpRates(a).margin || 0) - (cpRates(b).margin || 0));
  if (byMargin[0] && cpRates(byMargin[0]).margin != null) {
    lines.push('粗利率が最も低いのは「' + byMargin[0].name + '」で' + fmtPct(cpRates(byMargin[0]).margin) + '。SR・返金の負担と併せて、仕入れ基準の見直し余地を確認してください。');
  }
  return lines.join('\n');
}

function renderConditionPricePage(cfg) {
  const granularity = granSel.value, periodKey = periodSel.value;
  const sel = document.getElementById(cfg.selId);
  const rowFilter = cpRowFilter(sel);
  const label = currentPeriodLabel();
  document.getElementById(cfg.prefix + 'PeriodLabel').textContent = label;
  document.getElementById(cfg.prefix + 'TablePeriodLabel').textContent = label;
  document.getElementById(cfg.prefix + 'Title').textContent = cfg.dimLabel + '別分析 — ' + cpSelectionLabel(sel);

  let data = buildDimBreakdownGeneric(cfg.rows, cfg.dim, CP_FIELDS, granularity, periodKey, rowFilter);
  data.sort((a, b) => cfg.sortKey(a.name) - cfg.sortKey(b.name));
  const names = data.map(d => d.name);
  const totals = {};
  CP_FIELDS.forEach(f => totals[f] = data.reduce((s, d) => s + (d[f] || 0), 0));

  // 所見(自動生成)
  document.getElementById(cfg.prefix + 'InsightMeta').textContent = '対象期間: ' + label + ' / 絞り込み: ' + cpSelectionLabel(sel);
  document.getElementById(cfg.prefix + 'InsightText').textContent = cpBuildInsight(data, cfg.dimLabel, totals);

  // 内訳(件数 + 粗利率)
  renderChart(cfg.prefix + 'Chart', dualAxisConfig(names, data.map(d => d.shipped_count || d.count),
    data.map(d => cpRates(d).margin), '出荷件数', '粗利率', '件数', false));

  // SR: 件数 + SR率
  renderChart(cfg.prefix + 'SrChart', dualAxisConfig(names, data.map(d => d.sr_count),
    data.map(d => cpRates(d).sr_rate), 'SR件数', 'SR率', '件数', false));

  // 返金: 金額 + 返金率
  renderChart(cfg.prefix + 'RefundChart', dualAxisConfig(names, data.map(d => d.refund_amount),
    data.map(d => cpRates(d).refund_rate), '返金額(円)', '返金率', '金額', true));

  // 出品数
  renderChart(cfg.prefix + 'ListedChart', dualAxisConfig(names, data.map(d => d.listed_count),
    data.map(d => (totals.listed_count ? d.listed_count / totals.listed_count : null)), '出品数', '構成比', '件数', false));

  // 質問: 件数 + 質問率
  renderChart(cfg.prefix + 'QuestionChart', dualAxisConfig(names, data.map(d => d.question_count),
    data.map(d => cpRates(d).question_rate), '質問数', '質問率', '件数', false));

  // 推移(積み上げ出荷件数)
  const present = Array.from(new Set(cfg.rows.map(r => r[cfg.dim])));
  const order = present.sort((a, b) => cfg.sortKey(a) - cfg.sortKey(b));
  const trendMap = new Map();
  const periods = availablePeriods(granularity);
  cfg.rows.forEach(r => {
    if (!rowFilter(r)) return;
    const pk = periodKeyFor(r, granularity);
    if (pk.key == null) return;
    if (!trendMap.has(pk.label)) trendMap.set(pk.label, {});
    const o = trendMap.get(pk.label);
    o[r[cfg.dim]] = (o[r[cfg.dim]] || 0) + (r.shipped_count || r.count || 0);
  });
  if (isYoyMode()) {
    // 月次: 決算月×期で、選択中の絞り込みに対する出荷件数を昨対比較する
    const ser = buildYoyGeneric(cfg.rows, ['shipped_count', 'count', 'sr_count', 'sales_amount'], rowFilter);
    if (ser.fyNums.length > 1) {
      renderChart(cfg.prefix + 'TrendChart', yoyGenericConfig(ser, 'shipped_count',
        d => (d.shipped_count || d.count) ? d.sr_count / (d.shipped_count || d.count) : null,
        '出荷件数', 'SR率', '件数', false, cfg.dimLabel + '全体の出荷・SR率'));
    } else {
      renderChart(cfg.prefix + 'TrendChart', stackedMajorConfig(periods.map(p => p.label), order, trendMap));
    }
  } else {
    renderChart(cfg.prefix + 'TrendChart', stackedMajorConfig(periods.map(p => p.label), order, trendMap));
  }

  // 率の推移。月次のときは決算月×期の昨対比較にする。
  const rateSpecs = [
    ['SrRateTrend', 'sr_count', d => (d.shipped_count || d.count) ? d.sr_count / (d.shipped_count || d.count) : null,
      'SR件数', 'SR率', '件数', false, 'SR件数・SR率'],
    ['RefundRateTrend', 'refund_amount', d => d.sales_amount ? d.refund_amount / d.sales_amount : null,
      '返金額', '返金率', '金額(¥)', true, '返金額・返金率(金額ベース)'],
    ['ListedTrend', 'listed_count', null, '出品数', '', '件数', false, '出品数'],
    ['QuestionRateTrend', 'question_count', d => d.listed_count ? d.question_count / d.listed_count : null,
      '質問数', '質問率', '件数', false, '質問数・質問率(質問÷出品)']
  ];
  const cpYoy = isYoyMode()
    ? buildYoyGeneric(cfg.rows, ['shipped_count', 'count', 'sr_count', 'refund_amount', 'sales_amount',
        'listed_count', 'question_count'], rowFilter)
    : null;
  if (cpYoy && cpYoy.fyNums.length > 1) {
    rateSpecs.forEach(([id, bar, rateFn, barLabel, lineLabel, leftLabel, money, title]) => {
      renderChart(cfg.prefix + id, yoyGenericConfig(cpYoy, bar, rateFn, barLabel, lineLabel, leftLabel, money, title));
    });
  } else {
    renderChart(cfg.prefix + 'SrRateTrend', cpTrendConfig(cfg.rows, cfg.dim, granularity, rowFilter, order,
      (o, r) => { o.num += r.sr_count || 0; o.den += (r.shipped_count || r.count || 0); }, true, 'SR率の推移(%)'));
    renderChart(cfg.prefix + 'RefundRateTrend', cpTrendConfig(cfg.rows, cfg.dim, granularity, rowFilter, order,
      (o, r) => { o.num += r.refund_amount || 0; o.den += r.sales_amount || 0; }, true, '返金率の推移(金額ベース・%)'));
    renderChart(cfg.prefix + 'ListedTrend', cpTrendConfig(cfg.rows, cfg.dim, granularity, rowFilter, order,
      (o, r) => { o.num += r.listed_count || 0; o.den += 1; }, false, '出品数の推移(件)'));
    renderChart(cfg.prefix + 'QuestionRateTrend', cpTrendConfig(cfg.rows, cfg.dim, granularity, rowFilter, order,
      (o, r) => { o.num += r.question_count || 0; o.den += r.listed_count || 0; }, true, '質問率の推移(質問÷出品・%)'));
  }

  // 詳細テーブル
  renderSimpleTableInto(cfg.prefix + 'Table', [
    { name: cfg.dimLabel }, { name: '出荷件数', formatter: c => fmtInt(c) },
    { name: 'SR件数', formatter: c => fmtInt(c) }, { name: 'SR率', formatter: c => fmtPct(c) },
    { name: '返金件数', formatter: c => fmtInt(c) }, { name: '返金額(円)', formatter: c => fmtYen(c) },
    { name: '返金率', formatter: c => fmtPct(c) },
    { name: '出品数', formatter: c => fmtInt(c) }, { name: '質問数', formatter: c => fmtInt(c) },
    { name: '質問率', formatter: c => fmtPct(c) },
    { name: '売上金額(円)', formatter: c => fmtYen(c) }, { name: '粗利率', formatter: c => fmtPct(c) }
  ], data.map(d => {
    const R = cpRates(d);
    return [d.name, R.shipped, d.sr_count, R.sr_rate, d.refund_count, d.refund_amount, R.refund_rate,
            d.listed_count, d.question_count, R.question_rate, d.sales_amount, R.margin];
  }), 20);
}

function renderConditionPage() {
  renderConditionPricePage({
    rows: CONDITION_ROWS, dim: 'condition', dimLabel: 'コンディションランク',
    prefix: 'cond', selId: 'condCatMultiSelect', sortKey: conditionSortKey
  });
}

function renderPriceBandPage() {
  renderConditionPricePage({
    rows: PRICE_BAND_ROWS, dim: 'price_band', dimLabel: '価格帯',
    prefix: 'pb', selId: 'pbCatMultiSelect', sortKey: priceBandSortKey
  });
}


// ---------- ⑦ 粗利差異分析ページ ----------
function metricSimpleCardHtml(title, mainText) {
  return '<div class="card kpi-card"><div class="kpi-title">' + title + '</div>' +
    '<div class="kpi-row"><span class="kpi-main" style="font-size:16px;">' + mainText + '</span></div></div>';
}

const PV_FIELDS = ['count', 'upside_count', 'upside_amount', 'downside_count', 'downside_amount', 'variance_sum', 'expected_profit_sum', 'actual_profit_sum'];

function computeAggGeneric(rowsArr, fields, granularity, periodKey, rowFilter) {
  let rows = rowsArr.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  if (rowFilter) rows = rows.filter(rowFilter);
  const agg = {};
  fields.forEach(f => agg[f] = 0);
  rows.forEach(r => sumFieldsInto(agg, r, fields));
  return agg;
}

function pvTrendConfig(labels, upside, downside, netLine) {
  return {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { type: 'bar', label: '上振れ額', data: upside, backgroundColor: '#e0653a' },
        { type: 'bar', label: '下振れ額', data: downside, backgroundColor: '#5b8def' },
        { type: 'line', label: '差異合計', data: netLine, borderColor: '#333', backgroundColor: '#333', tension: 0, pointRadius: 2, borderWidth: 2 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom', labels: { boxWidth: 10, font: { size: 9.5 } } },
        tooltip: { callbacks: { label: c => (c.dataset.label || '') + ': ¥' + Math.round(c.parsed.y).toLocaleString('ja-JP') } }
      },
      scales: {
        y: { title: { display: true, text: '金額(¥)', font: { size: 10.5 } }, ticks: { font: { size: 10 }, callback: v => (v / 1000).toLocaleString('ja-JP') + 'k' } },
        x: { ticks: { font: { size: 9.5 }, maxRotation: 55 } }
      }
    }
  };
}

function PV_COLUMNS(nameLabel) {
  return [
    { name: nameLabel }, { name: '件数', formatter: c => fmtInt(c) },
    { name: '上振れ件数', formatter: c => fmtInt(c) }, { name: '上振れ額(円)', formatter: c => fmtYen(c) },
    { name: '下振れ件数', formatter: c => fmtInt(c) }, { name: '下振れ額(円)', formatter: c => fmtYen(c) },
    { name: '差異合計額(円)', formatter: c => fmtYen(c) },
    { name: '見込み粗利(円)', formatter: c => fmtYen(c) }, { name: '実粗利(円)', formatter: c => fmtYen(c) }
  ];
}
function pvRow(d) {
  return [d.name, d.count, d.upside_count, d.upside_amount, d.downside_count, d.downside_amount, d.variance_sum, d.expected_profit_sum, d.actual_profit_sum];
}

function renderProfitVarianceKPIs() {
  const granularity = granSel.value, periodKey = periodSel.value;
  const agg = computeAggGeneric(PROFIT_VARIANCE_ROWS, PV_FIELDS, granularity, periodKey, null);
  const html = [
    metricSimpleCardHtml('件数(合計)', fmtInt(agg.count)),
    metricSimpleCardHtml('上振れ 件数・額', fmtInt(agg.upside_count) + '件<br>' + fmtYen(agg.upside_amount)),
    metricSimpleCardHtml('下振れ 件数・額', fmtInt(agg.downside_count) + '件<br>' + fmtYen(agg.downside_amount)),
    metricSimpleCardHtml('差異合計額', fmtYen(agg.variance_sum)),
    metricSimpleCardHtml('見込み粗利 → 実粗利', fmtYen(agg.expected_profit_sum) + '<br>→ ' + fmtYen(agg.actual_profit_sum)),
  ].join('');
  document.getElementById('pvKpiGrid').innerHTML = html;
}

function renderProfitVarianceTrend() {
  const granularity = granSel.value;
  const trend = buildDimTrendAlignedGeneric(PROFIT_VARIANCE_ROWS, null, PV_FIELDS, granularity, null);
  const labels = trend.periods.map(p => p.label);
  const pick = (p, f) => { const inner = trend.periodMap.get(p.key); const o = inner && inner.get('__ALL__'); return o ? (o[f] || 0) : 0; };
  if (isYoyMode()) {
    // 月次は決算月×期の昨対比較(粗利差異の合計を期別に並べる)
    const ser = buildYoyGeneric(PROFIT_VARIANCE_ROWS, PV_FIELDS, null);
    if (ser.fyNums.length > 1) {
      renderChart('pvTrendChart', yoyGenericConfig(ser, 'variance_sum', null,
        '粗利差異', '', '金額(¥)', true, '粗利差異'));
      return;
    }
  }
  renderChart('pvTrendChart', pvTrendConfig(
    labels, trend.periods.map(p => pick(p, 'upside_amount')), trend.periods.map(p => pick(p, 'downside_amount')), trend.periods.map(p => pick(p, 'variance_sum'))
  ));
}

function renderProfitVarianceLocationSection() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('pvLocPeriodLabel').textContent = currentPeriodLabel();
  let data = buildDimBreakdownGeneric(PROFIT_VARIANCE_ROWS, 'location', PV_FIELDS, granularity, periodKey, null);
  data.sort((a, b) => b.count - a.count);
  renderChart('pvLocChart', pvTrendConfig(data.map(d => d.name), data.map(d => d.upside_amount), data.map(d => d.downside_amount), data.map(d => d.variance_sum)));
  renderSimpleTableInto('detailTablePvLocation', PV_COLUMNS('拠点'), data.map(pvRow), 20);
}

function renderProfitVarianceCategorySection() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('pvCatPeriodLabel').textContent = currentPeriodLabel();
  let data = buildDimBreakdownGeneric(PROFIT_VARIANCE_ROWS, 'category', PV_FIELDS, granularity, periodKey, null);
  data.sort((a, b) => b.count - a.count);
  renderChart('pvCatChart', pvTrendConfig(data.map(d => d.name), data.map(d => d.upside_amount), data.map(d => d.downside_amount), data.map(d => d.variance_sum)));
  renderSimpleTableInto('detailTablePvCategory', PV_COLUMNS('カテゴリ'), data.map(pvRow), 20);
}

// ---------- カテゴリ別詳細粗利指標(⑦ページ内に追加するセクション) ----------
// CATEGORY_PROFIT_DETAIL_ROWS は week_start/week_end/year_month/category 粒度のため、
// buildDimBreakdownGeneric等の汎用ヘルパーで単純合算できる項目(count/cost_amount/
// sales_amount/gross_profit/variance_amount)はそのまま合算し、平均系の指標
// (avg_lead_days/margin_rate/avg_sale_price/avg_profit_price)は合算後の値から
// 再計算する(単純平均ではなく、countで重み付けした加重平均にする)。
function buildCategoryProfitDetailAgg(granularity, periodKey) {
  let rows = CATEGORY_PROFIT_DETAIL_ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  const map = new Map();
  rows.forEach(r => {
    if (!map.has(r.category)) {
      map.set(r.category, { count: 0, cost_amount: 0, sales_amount: 0, gross_profit: 0, variance_amount: 0, lead_weighted: 0 });
    }
    const o = map.get(r.category);
    o.count += r.count || 0;
    o.cost_amount += r.cost_amount || 0;
    o.sales_amount += r.sales_amount || 0;
    o.gross_profit += r.gross_profit || 0;
    o.variance_amount += r.variance_amount || 0;
    if (r.avg_lead_days !== null && r.avg_lead_days !== undefined) o.lead_weighted += r.avg_lead_days * (r.count || 0);
  });
  return Array.from(map.entries()).map(([name, o]) => ({
    name,
    count: o.count,
    cost_amount: o.cost_amount,
    sales_amount: o.sales_amount,
    gross_profit: o.gross_profit,
    variance_amount: o.variance_amount,
    avg_lead_days: o.count ? o.lead_weighted / o.count : null,
    margin_rate: o.sales_amount ? o.gross_profit / o.sales_amount : null,
    avg_sale_price: o.count ? o.sales_amount / o.count : null,
    avg_profit_price: o.count ? o.gross_profit / o.count : null
  }));
}

function renderCategoryProfitDetailSection() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('cpdPeriodLabel').textContent = currentPeriodLabel();
  let data = buildCategoryProfitDetailAgg(granularity, periodKey);
  data.sort((a, b) => b.sales_amount - a.sales_amount);

  if (!data.length) {
    renderChart('cpdMarginChart', { type: 'bar', data: { labels: ['データなし'], datasets: [{ data: [0], backgroundColor: ['#e3e5e8'] }] }, options: { plugins: { legend: { display: false } } } });
    renderChart('cpdProfitPriceChart', { type: 'bar', data: { labels: ['データなし'], datasets: [{ data: [0], backgroundColor: ['#e3e5e8'] }] }, options: { plugins: { legend: { display: false } } } });
  } else {
    renderChart('cpdMarginChart', singleAxisCountConfig(
      data.map(d => d.name), data.map(d => d.margin_rate != null ? d.margin_rate * 100 : 0), '粗利率(%)', '#2ecc71', '粗利率(%)'
    ));
    renderChart('cpdProfitPriceChart', singleAxisMoneyConfig(data.map(d => d.name), data.map(d => d.avg_profit_price || 0), '粗利単価'));
  }

  // 上振れ・下振れの件数と率をカテゴリ別に足す(profit_variance_rows から同じ期間・同じ絞り込みで集計)
  const upDown = new Map();
  PROFIT_VARIANCE_ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey)
    .forEach(r => {
      const o = upDown.get(r.category) || { count: 0, up: 0, down: 0 };
      o.count += r.count || 0; o.up += r.upside_count || 0; o.down += r.downside_count || 0;
      upDown.set(r.category, o);
    });

  renderSimpleTableInto('detailTableCategoryProfitDetail', [
    { name: 'カテゴリ' },
    { name: '数量', formatter: c => fmtInt(c) },
    { name: '仕入額(円)', formatter: c => fmtYen(c) },
    { name: '売上額(円)', formatter: c => fmtYen(c) },
    { name: '粗利額(円)', formatter: c => fmtYen(c) },
    { name: '粗利差異(円)', formatter: c => fmtYen(c) },
    { name: '上振れ率', formatter: c => (c === null || c === undefined) ? '-' : fmtPct(c) },
    { name: '下振れ率', formatter: c => (c === null || c === undefined) ? '-' : fmtPct(c) },
    { name: '平均リード(日)', formatter: c => (c === null || c === undefined) ? '-' : c.toFixed(1) },
    { name: '粗利率', formatter: c => (c === null || c === undefined) ? '-' : fmtPct(c) },
    { name: '販売単価(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) },
    { name: '粗利単価(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) }
  ], data.map(d => {
    const ud = upDown.get(d.name);
    const upRate = ud && ud.count ? ud.up / ud.count : null;
    const downRate = ud && ud.count ? ud.down / ud.count : null;
    return [d.name, d.count, d.cost_amount, d.sales_amount, d.gross_profit, d.variance_amount,
      upRate, downRate, d.avg_lead_days, d.margin_rate, d.avg_sale_price, d.avg_profit_price];
  }), 20);
}

// F項目: カテゴリを選択(複数選択可)し、選択したカテゴリの「合計」「平均」それぞれの
// 系列を同じグラフに表示する。合計=該当カテゴリの値をそのまま合算。平均=単純に足し算・
// 平均するだけでは意味が壊れる指標(率・単価)に注意し、
//   - 加算してよい指標(数量/売上額/粗利額/粗利差異)は 合計÷選択カテゴリ数
//   - 率・単価系(粗利率/販売単価/粗利単価/リード)は、そのカテゴリの合計から
//     再計算した値(そのカテゴリの「実際の」率・単価)を選択カテゴリ間で単純平均
// という方針にしている。「合計」側の率・単価系は個々の値を単純合算するのではなく、
// pooled(合計粗利額÷合計売上額等)で再計算する。
function buildCpdTrendSelected(granularity, selectedCats) {
  const periods = availablePeriods(granularity);
  const catSet = new Set(selectedCats);
  const perPeriodCat = new Map();
  CATEGORY_PROFIT_DETAIL_ROWS.forEach(r => {
    if (!catSet.has(r.category)) return;
    const pk = periodKeyFor(r, granularity).key;
    if (!perPeriodCat.has(pk)) perPeriodCat.set(pk, new Map());
    const catMap = perPeriodCat.get(pk);
    if (!catMap.has(r.category)) catMap.set(r.category, { count: 0, sales_amount: 0, gross_profit: 0, variance_amount: 0, lead_weighted: 0 });
    const o = catMap.get(r.category);
    o.count += r.count || 0;
    o.sales_amount += r.sales_amount || 0;
    o.gross_profit += r.gross_profit || 0;
    o.variance_amount += r.variance_amount || 0;
    if (r.avg_lead_days != null && r.count) o.lead_weighted += r.avg_lead_days * r.count;
  });
  return periods.map(p => {
    const catMap = perPeriodCat.get(p.key) || new Map();
    const cats = Array.from(catMap.values());
    const sum = f => cats.reduce((s, o) => s + (o[f] || 0), 0);
    const sumCount = sum('count'), sumSales = sum('sales_amount'), sumProfit = sum('gross_profit'), sumVariance = sum('variance_amount'), sumLeadW = sum('lead_weighted');
    const total = {
      count: sumCount, sales_amount: sumSales, gross_profit: sumProfit, variance_amount: sumVariance,
      margin_rate: sumSales ? sumProfit / sumSales : null,
      avg_sale_price: sumCount ? sumSales / sumCount : null,
      avg_profit_price: sumCount ? sumProfit / sumCount : null,
      avg_lead_days: sumCount ? sumLeadW / sumCount : null
    };
    const perCatMetrics = cats.map(o => ({
      count: o.count, sales_amount: o.sales_amount, gross_profit: o.gross_profit, variance_amount: o.variance_amount,
      margin_rate: o.sales_amount ? o.gross_profit / o.sales_amount : null,
      avg_sale_price: o.count ? o.sales_amount / o.count : null,
      avg_profit_price: o.count ? o.gross_profit / o.count : null,
      avg_lead_days: o.count ? o.lead_weighted / o.count : null
    }));
    const n = perCatMetrics.length;
    const meanRaw = key => n ? perCatMetrics.reduce((s, m) => s + (m[key] || 0), 0) / n : null;
    const meanOf = key => {
      const vals = perCatMetrics.map(m => m[key]).filter(v => v != null);
      return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    };
    const avg = {
      count: meanRaw('count'), sales_amount: meanRaw('sales_amount'), gross_profit: meanRaw('gross_profit'), variance_amount: meanRaw('variance_amount'),
      margin_rate: meanOf('margin_rate'), avg_sale_price: meanOf('avg_sale_price'), avg_profit_price: meanOf('avg_profit_price'), avg_lead_days: meanOf('avg_lead_days')
    };
    return { label: p.label, key: p.key, total, avg };
  });
}

function cpdComboConfig(labels, barSpecs, lineSpecs) {
  return {
    type: 'bar',
    data: {
      labels,
      datasets: [
        ...barSpecs.map(b => ({ type: 'bar', label: b.label, data: b.data, backgroundColor: b.color, yAxisID: 'y' })),
        ...lineSpecs.map(l => ({
          type: 'line', label: l.label, data: l.data, borderColor: l.color, backgroundColor: l.color,
          yAxisID: 'y1', borderDash: l.dash ? [5, 4] : [], spanGaps: true, tension: 0, pointRadius: 2
        }))
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 9, font: { size: 9 } } } },
      scales: {
        y: { position: 'left', ticks: { font: { size: 9.5 } } },
        y1: { position: 'right', grid: { drawOnChartArea: false }, ticks: { font: { size: 9.5 } } },
        x: { ticks: { font: { size: 9 }, maxRotation: 55 } }
      }
    }
  };
}

function renderCpdTrendSection() {
  // 月次のときは、この節の各グラフも決算月×期の昨対比較にする(他のグラフは通常どおり描く)
  if (isYoyMode() && typeof CATEGORY_PROFIT_DETAIL_ROWS !== 'undefined' && cpdCatMultiSel) {
    const selCats = getMultiSelectValues(cpdCatMultiSel);
    const catSet = (!selCats.length || selCats.includes(ALL_CAT)) ? null : new Set(selCats);
    const ser = buildYoyGeneric(CATEGORY_PROFIT_DETAIL_ROWS,
      ['count', 'sales_amount', 'gross_profit', 'variance_amount', 'cost_amount'],
      r => !catSet || catSet.has(r.category));
    if (ser.fyNums.length > 1) {
      document.getElementById('cpdTrendPeriodLabel').textContent = currentPeriodLabel();
      renderChart('cpdTrendPriceChart', yoyGenericConfig(ser, 'sales_amount',
        d => d.count ? d.sales_amount / d.count / 1000000 : null,
        '売上額', '販売単価(百万円換算)', '金額(¥)', true, '売上額・販売単価'));
      renderChart('cpdTrendVarianceLeadChart', yoyGenericConfig(ser, 'variance_amount', null,
        '粗利差異', '', '金額(¥)', true, '粗利差異'));
      renderChart('cpdTrendSalesQtyChart', yoyGenericConfig(ser, 'count', null,
        '数量', '', '件数', false, '販売数量'));
      renderChart('cpdTrendProfitMarginChart', yoyGenericConfig(ser, 'gross_profit',
        d => d.sales_amount ? d.gross_profit / d.sales_amount : null,
        '粗利額', '粗利率', '金額(¥)', true, '粗利額・粗利率'));
      return;
    }
  }
  if (!cpdCatMultiSel) return;
  const granularity = granSel.value;
  document.getElementById('cpdTrendPeriodLabel').textContent = currentPeriodLabel();
  let selectedCats = getMultiSelectValues(cpdCatMultiSel);
  // 「(全カテゴリ)」または未選択なら全カテゴリを対象にする
  if (!selectedCats.length || selectedCats.includes(ALL_CAT)) selectedCats = cpdCategories.slice();
  const trend = buildCpdTrendSelected(granularity, selectedCats);
  const labels = trend.map(t => t.label);

  renderChart('cpdTrendPriceChart', cpdComboConfig(
    labels,
    [
      { label: '販売単価(合計)', data: trend.map(t => t.total.avg_sale_price), color: '#5b8def' },
      { label: '粗利単価(合計)', data: trend.map(t => t.total.avg_profit_price), color: '#2ecc71' }
    ],
    [
      { label: '粗利率(合計)', data: trend.map(t => t.total.margin_rate), color: '#e0653a' },
      { label: '販売単価(平均)', data: trend.map(t => t.avg.avg_sale_price), color: '#8fb1f5', dash: true },
      { label: '粗利単価(平均)', data: trend.map(t => t.avg.avg_profit_price), color: '#8fe0b3', dash: true },
      { label: '粗利率(平均)', data: trend.map(t => t.avg.margin_rate), color: '#f0a988', dash: true }
    ]
  ));

  renderChart('cpdTrendVarianceLeadChart', cpdComboConfig(
    labels,
    [{ label: '粗利差異(合計)', data: trend.map(t => t.total.variance_amount), color: '#9b59b6' }],
    [
      { label: 'リード日数(合計)', data: trend.map(t => t.total.avg_lead_days), color: '#e74c3c' },
      { label: '粗利差異(平均)', data: trend.map(t => t.avg.variance_amount), color: '#c39bd3', dash: true },
      { label: 'リード日数(平均)', data: trend.map(t => t.avg.avg_lead_days), color: '#f1948a', dash: true }
    ]
  ));

  renderChart('cpdTrendSalesQtyChart', cpdComboConfig(
    labels,
    [{ label: '売上額(合計)', data: trend.map(t => t.total.sales_amount), color: '#5b8def' }],
    [
      { label: '粗利率(合計)', data: trend.map(t => t.total.margin_rate), color: '#e0653a' },
      { label: '売上額(平均)', data: trend.map(t => t.avg.sales_amount), color: '#8fb1f5', dash: true },
      { label: '粗利率(平均)', data: trend.map(t => t.avg.margin_rate), color: '#f0a988', dash: true }
    ]
  ));

  renderChart('cpdTrendProfitMarginChart', cpdComboConfig(
    labels,
    [{ label: '粗利額(合計)', data: trend.map(t => t.total.gross_profit), color: '#2ecc71' }],
    [
      { label: '数量(合計)', data: trend.map(t => t.total.count), color: '#1abc9c' },
      { label: '粗利額(平均)', data: trend.map(t => t.avg.gross_profit), color: '#8fe0b3', dash: true },
      { label: '数量(平均)', data: trend.map(t => t.avg.count), color: '#7dd8c6', dash: true }
    ]
  ));
}

// 粗利差異ページの所見: 全体の上振れ/下振れ構成と、下振れが集中している拠点・カテゴリを指摘する
function renderPvInsight() {
  const granularity = granSel.value, periodKey = periodSel.value;
  const rows = PROFIT_VARIANCE_ROWS.filter(r => periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  const t = { count: 0, variance_sum: 0, upside_count: 0, upside_amount: 0, downside_count: 0, downside_amount: 0, expected: 0, actual: 0 };
  const byLoc = new Map(), byCat = new Map();
  rows.forEach(r => {
    t.count += r.count || 0; t.variance_sum += r.variance_sum || 0;
    t.upside_count += r.upside_count || 0; t.upside_amount += r.upside_amount || 0;
    t.downside_count += r.downside_count || 0; t.downside_amount += r.downside_amount || 0;
    t.expected += r.expected_profit_sum || 0; t.actual += r.actual_profit_sum || 0;
    for (const [map, key] of [[byLoc, r.location], [byCat, r.category]]) {
      if (!map.has(key)) map.set(key, { count: 0, variance: 0, down: 0, downAmt: 0 });
      const o = map.get(key);
      o.count += r.count || 0; o.variance += r.variance_sum || 0;
      o.down += r.downside_count || 0; o.downAmt += r.downside_amount || 0;
    }
  });
  const el = document.getElementById('pvInsightText');
  const meta = document.getElementById('pvInsightMeta');
  if (meta) meta.textContent = '対象期間: ' + currentPeriodLabel();
  if (!el) return;
  if (!t.count) { el.textContent = 'データがありません。'; return; }
  const lines = [];
  const downRate = t.count ? t.downside_count / t.count : 0;
  lines.push('全体: 対象' + fmtInt(t.count) + '点のうち、見込みを下回った商品が' + fmtInt(t.downside_count) + '点(' + (downRate * 100).toFixed(1) +
             '%)で' + fmtYen(t.downside_amount) + '、上回った商品が' + fmtInt(t.upside_count) + '点で' + fmtYen(t.upside_amount) +
             '。差引の粗利差異は' + fmtYen(t.variance_sum) + 'です。');
  lines.push('見込み粗利' + fmtYen(t.expected) + 'に対し実績は' + fmtYen(t.actual) + 'で、達成率は' +
             (t.expected ? (t.actual / t.expected * 100).toFixed(1) : '-') + '%。');
  const rank = (map, unit) => Array.from(map.entries()).filter(([k, o]) => o.count >= 30).sort((a, b) => a[1].variance - b[1].variance);
  const locRank = rank(byLoc), catRank = rank(byCat);
  if (locRank.length) {
    const [k, o] = locRank[0];
    lines.push('拠点別で差異が最も大きいマイナスは「' + k + '」の' + fmtYen(o.variance) + '(対象' + fmtInt(o.count) + '点、うち下振れ' + fmtInt(o.down) + '点)。販売価格の設定基準を重点的に確認すべき拠点です。');
    const best = locRank[locRank.length - 1];
    if (best && best[0] !== k) lines.push('逆に「' + best[0] + '」は' + fmtYen(best[1].variance) + 'と最も良く、値付けが実勢に合っています。');
  }
  if (catRank.length) {
    const [k, o] = catRank[0];
    lines.push('カテゴリ別では「' + k + '」が' + fmtYen(o.variance) + 'と最大のマイナス(対象' + fmtInt(o.count) + '点)。相場変動が大きいカテゴリは、出品価格の見直し頻度を上げる余地があります。');
  }
  el.textContent = lines.join('\n');
}

// ⑥粗利差異: 拠点・上振れ/下振れの絞り込みに応じて、コンディション別/価格帯別/カテゴリ別に分解する
// 金額目盛りの表記。データが空/小さいときに 0.000001M のような表示にならないようにする。
function moneyTick(v) {
  const a = Math.abs(v);
  if (a >= 1000000) return (v / 1000000).toFixed(1) + 'M';
  if (a >= 1000) return Math.round(v / 1000) + 'k';
  return Math.round(v);
}

function pvFilterState() {
  const loc = locSel.value;
  const useLoc = loc && loc !== ALL_LOC;
  const side = (document.getElementById('pvSide') || {}).value || 'both';
  const granularity = granSel.value, periodKey = periodSel.value;
  const rowFilter = r => (!useLoc || r.location === loc) &&
    (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  return { loc, useLoc, side, rowFilter, label: (useLoc ? loc : '全拠点') };
}

// side に応じて「見たい金額・件数」を取り出す
function pvPick(o, side) {
  if (side === 'up') return { amt: o.upside_amount || 0, n: o.upside_count || 0 };
  if (side === 'down') return { amt: o.downside_amount || 0, n: o.downside_count || 0 };
  return { amt: o.variance_sum || 0, n: o.count || 0 };
}

function renderPvBreakdowns() {
  const st = pvFilterState();
  const sideLabel = st.side === 'up' ? '上振れ' : (st.side === 'down' ? '下振れ' : '差引');
  const pl = document.getElementById('pvBreakPeriodLabel');
  if (pl) pl.textContent = currentPeriodLabel() + ' / ' + st.label + ' / ' + sideLabel;

  const agg = (rowsArr, dim) => {
    const map = new Map();
    rowsArr.filter(st.rowFilter).forEach(r => {
      const k = r[dim];
      const o = map.get(k) || { count: 0, variance_sum: 0, upside_count: 0, upside_amount: 0,
                                downside_count: 0, downside_amount: 0, sort: r.price_band_sort || 0 };
      ['count', 'variance_sum', 'upside_count', 'upside_amount', 'downside_count', 'downside_amount']
        .forEach(f => o[f] += r[f] || 0);
      map.set(k, o);
    });
    return Array.from(map.entries());
  };

  // 上振れ・下振れを常に併記する。以前は「差引」を選ぶと件数=対象商品数・比率=100%となり
  // 意味のない列になっていたため、件数と率は上振れ/下振れそれぞれで出す形に改めた。
  const mkTable = (list, dimName, sortFn) => {
    const rows = list.slice().sort(sortFn);
    const tot = rows.reduce((a, [, o]) => ({
      cnt: a.cnt + (o.count || 0), un: a.un + (o.upside_count || 0), ua: a.ua + (o.upside_amount || 0),
      dn: a.dn + (o.downside_count || 0), da: a.da + (o.downside_amount || 0), v: a.v + (o.variance_sum || 0)
    }), { cnt: 0, un: 0, ua: 0, dn: 0, da: 0, v: 0 });
    const line = (k, o, opt) => {
      const c = (v, sv) => impCell(v, { num: 1, bg: opt && opt.bg, bold: opt && opt.bold });
      return '<tr' + (opt && opt.total ? ' data-total="1"' : '') + '>' +
        impCell(k, { bg: opt && opt.bg, bold: opt && opt.bold }) +
        c(fmtInt(o.count)) +
        c(fmtInt(o.upside_count)) + c(o.count ? fmtPct(o.upside_count / o.count) : '-') + c(fmtYen(o.upside_amount)) +
        c(fmtInt(o.downside_count)) + c(o.count ? fmtPct(o.downside_count / o.count) : '-') + c(fmtYen(o.downside_amount)) +
        c(fmtYen(o.variance_sum)) + c(o.count ? fmtYen(o.variance_sum / o.count) : '-') + '</tr>';
    };
    const body = rows.map(([k, o]) => line(k, o)).join('') +
      line('合計', { count: tot.cnt, upside_count: tot.un, upside_amount: tot.ua,
                    downside_count: tot.dn, downside_amount: tot.da, variance_sum: tot.v },
           { bg: '#eef1f5', bold: 1, total: 1 });
    return { html: impTable([{ name: dimName }, { name: '対象商品数', num: 1 },
      { name: '上振れ件数', num: 1 }, { name: '上振れ率', num: 1 }, { name: '上振れ金額', num: 1 },
      { name: '下振れ件数', num: 1 }, { name: '下振れ率', num: 1 }, { name: '下振れ金額', num: 1 },
      { name: '差引金額', num: 1 }, { name: '1件あたり差引', num: 1 }], body), rows };
  };

  // コンディション別
  const condList = agg(VARIANCE_CONDITION_ROWS, 'condition');
  const condT = mkTable(condList, 'コンディション', (a, b) => conditionSortKey(a[0]) - conditionSortKey(b[0]));
  const ce = document.getElementById('pvCondTable');
  if (ce) ce.innerHTML = condT.html;
  renderChart('pvCondChart', {
    type: 'bar',
    data: {
      labels: condT.rows.map(x => x[0]),
      datasets: [
        { label: '上振れ金額', data: condT.rows.map(x => x[1].upside_amount), backgroundColor: '#2ecc71' },
        { label: '下振れ金額', data: condT.rows.map(x => x[1].downside_amount), backgroundColor: '#e0653a' }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { title: { display: true, text: 'コンディション別 上振れ/下振れ金額' },
        legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } },
      scales: { y: { ticks: { callback: v => moneyTick(v) } } } }
  });

  // 価格帯別
  const bandList = agg(VARIANCE_BAND_ROWS, 'price_band');
  const bandT = mkTable(bandList, '価格帯', (a, b) => (a[1].sort || 0) - (b[1].sort || 0));
  const be = document.getElementById('pvBandTable');
  if (be) be.innerHTML = bandT.html;
  renderChart('pvBandChart', {
    type: 'bar',
    data: {
      labels: bandT.rows.map(x => x[0]),
      datasets: [
        { label: '上振れ金額', data: bandT.rows.map(x => x[1].upside_amount), backgroundColor: '#2ecc71' },
        { label: '下振れ金額', data: bandT.rows.map(x => x[1].downside_amount), backgroundColor: '#e0653a' }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { title: { display: true, text: '価格帯別 上振れ/下振れ金額' },
        legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } },
      scales: { y: { ticks: { callback: v => moneyTick(v) } } } }
  });

  // カテゴリ別(既存の profit_variance_rows を流用)
  const catList = agg(PROFIT_VARIANCE_ROWS, 'category');
  const catT = mkTable(catList, 'カテゴリ', (a, b) => Math.abs(pvPick(b[1], st.side).amt) - Math.abs(pvPick(a[1], st.side).amt));
  // 「上振れのみ/下振れのみ」を選んだときは、その側の金額が大きい順に並べる
  const cte = document.getElementById('pvCatTable');
  if (cte) cte.innerHTML = catT.html;

  ['pvCondTable', 'pvBandTable', 'pvCatTable'].forEach(id => makeSortable(id, -1));
}

function renderProfitVariancePage() {
  renderPvInsight();
  renderPvBreakdowns();
  renderProfitVarianceKPIs();
  renderProfitVarianceTrend();
  renderProfitVarianceLocationSection();
  renderProfitVarianceCategorySection();
  renderCategoryProfitDetailSection();
  renderCpdTrendSection();
}

// ---------- ⑧ 赤字(原価割れ)分析ページ ----------
const DEFICIT_FIELDS = ['count', 'total_deficit', 'shipping_fee_total', 'return_shipping_total'];

function deficitDerive(o) {
  return Object.assign({}, o, { avg_deficit_per_item: o.count ? o.total_deficit / o.count : null });
}

function DEFICIT_COLUMNS(nameLabel, drill) {
  return [
    drill
      ? {
          name: nameLabel,
          // クリックでそのカテゴリの商品明細(赤字商品＋SR発生商品)を上部に表示する
          formatter: (cell) => gridjs.html(
            '<a href="#" class="deficit-drill" data-cat="' + cell + '" ' +
            'style="color:#2455c9;text-decoration:underline;cursor:pointer;">' + cell + '</a>')
        }
      : { name: nameLabel },
    { name: '出荷商品数', formatter: c => fmtInt(c) },
    { name: '赤字商品数', formatter: c => fmtInt(c) },
    { name: '赤字率', formatter: c => (c === null || c === undefined) ? '-' : fmtPct(c) },
    { name: '赤字額合計(円)', formatter: c => fmtYen(c) },
    { name: '1品あたり赤字額(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) },
    { name: '出荷1点あたり赤字額(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) },
    { name: 'SR返品送料合計(円)', formatter: c => fmtYen(c) }
  ];
}
function deficitRow(d) {
  return [d.name, d.shipped_count, d.count, d.deficit_rate, d.total_deficit,
          d.avg_deficit_per_item, d.deficit_per_shipped, d.return_shipping_total];
}

// 出荷商品数(deficit_mode_rows 由来)を次元別に引くための索引を作る。
// 赤字件数だけでは母数が分からないため、出荷数と赤字率を併記できるようにする。
function buildShippedIndex(dim, granularity, periodKey, rowFilter) {
  const map = new Map();
  if (typeof DEFICIT_MODE_ROWS === 'undefined') return map;
  DEFICIT_MODE_ROWS.forEach(r => {
    if (periodKey !== '__ALL__' && periodKeyFor(r, granularity).key !== periodKey) return;
    if (rowFilter && !rowFilter(r)) return;
    map.set(r[dim], (map.get(r[dim]) || 0) + (r.shipped_count || 0));
  });
  return map;
}

function attachShipped(data, shippedMap) {
  return data.map(d => {
    const ship = shippedMap.get(d.name) || 0;
    return Object.assign({}, d, {
      shipped_count: ship,
      deficit_rate: ship ? d.count / ship : null,
      deficit_per_shipped: ship ? d.total_deficit / ship : null
    });
  });
}

// E項目: ⑧赤字ページの絞り込み条件(拠点・カテゴリ)を1か所で組み立てる。
//   拠点  : locFilter が '__ALL__'(全拠点)なら拠点で絞らない。それ以外はその1拠点。
//   カテゴリ: deficitCatMultiSelect が未選択なら全カテゴリ。選択があればその複数カテゴリの合算。
// ページ内の全セクション(KPI/推移/カテゴリ別/仕入れ方法別)がこの同じ条件を使う。
function deficitFilterState() {
  const loc = locSel.value || ALL_LOC;
  const isAllLoc = (loc === ALL_LOC);
  let selectedCats = deficitCatMultiSel ? getMultiSelectValues(deficitCatMultiSel) : [];
  // 「(全カテゴリ)」または未選択なら全カテゴリ扱い
  const isAllCats = selectedCats.length === 0 || selectedCats.includes(ALL_CAT);
  if (isAllCats) selectedCats = [];
  const catSet = new Set(selectedCats);
  const rowFilter = (isAllLoc && isAllCats)
    ? null
    : (r => (isAllLoc ? true : r.location === loc) && (isAllCats ? true : catSet.has(r.category)));
  const locLabel = isAllLoc ? '全拠点' : loc;
  const catLabel = isAllCats
    ? '全カテゴリ'
    : (selectedCats.length === 1 ? selectedCats[0] : selectedCats.join(' + ') + '(合算・' + selectedCats.length + 'カテゴリ)');
  return { loc, isAllLoc, isAllCats, rowFilter, locLabel, catLabel };
}

function renderDeficitKPIs() {
  const granularity = granSel.value, periodKey = periodSel.value;
  const { rowFilter, locLabel, catLabel } = deficitFilterState();
  const titleEl = document.getElementById('deficitDrillTitle');
  if (titleEl) titleEl.textContent = locLabel + '  ×  カテゴリ: ' + catLabel;
  const trendHeading = document.getElementById('deficitTrendHeading');
  if (trendHeading) trendHeading.textContent = '推移(' + locLabel + ' / ' + catLabel + ')';
  const agg = computeAggGeneric(DEFICIT_ROWS, DEFICIT_FIELDS, granularity, periodKey, rowFilter);
  const avg = agg.count ? agg.total_deficit / agg.count : null;
  const html = [
    metricSimpleCardHtml('赤字商品数', fmtInt(agg.count)),
    metricSimpleCardHtml('赤字額合計', fmtYen(agg.total_deficit)),
    metricSimpleCardHtml('1品あたり赤字額', avg === null ? '-' : fmtYen(avg)),
    metricSimpleCardHtml('発送送料合計', fmtYen(agg.shipping_fee_total)),
    metricSimpleCardHtml('SR返品送料合計', fmtYen(agg.return_shipping_total))
  ].join('');
  document.getElementById('deficitKpiGrid').innerHTML = html;
}

function renderDeficitCategorySection() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('deficitCatPeriodLabel').textContent = currentPeriodLabel();
  const { rowFilter } = deficitFilterState();
  let data = buildDimBreakdownGeneric(DEFICIT_ROWS, 'category', DEFICIT_FIELDS, granularity, periodKey, rowFilter).map(deficitDerive);
  data = attachShipped(data, buildShippedIndex('category', granularity, periodKey, rowFilter));
  data.sort((a, b) => b.total_deficit - a.total_deficit);
  renderChart('deficitCatChart', dualAxisMoneyRightConfig(
    data.map(d => d.name), data.map(d => d.count), data.map(d => d.total_deficit), '赤字商品数', '赤字額合計', '件数'
  ));
  renderSimpleTableInto('detailTableDeficitCategory', DEFICIT_COLUMNS('カテゴリ', true), data.map(deficitRow), 20);
}

function renderDeficitProcSection() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('deficitProcPeriodLabel').textContent = currentPeriodLabel();
  const { rowFilter } = deficitFilterState();
  let data = buildDimBreakdownGeneric(DEFICIT_ROWS, 'procurement_type', DEFICIT_FIELDS, granularity, periodKey, rowFilter).map(deficitDerive);
  data = attachShipped(data, buildShippedIndex('procurement_type', granularity, periodKey, rowFilter));
  data.sort((a, b) => b.total_deficit - a.total_deficit);
  renderChart('deficitProcChart', dualAxisMoneyRightConfig(
    data.map(d => d.name), data.map(d => d.count), data.map(d => d.total_deficit), '赤字商品数', '赤字額合計', '件数'
  ));
  renderSimpleTableInto('detailTableDeficitProc', DEFICIT_COLUMNS('仕入れ方法'), data.map(deficitRow), 10);
}

function renderDeficitTrend() {
  const granularity = granSel.value;
  const { rowFilter } = deficitFilterState();
  const trend = buildDimTrendAlignedGeneric(DEFICIT_ROWS, null, DEFICIT_FIELDS, granularity, rowFilter);
  const labels = trend.periods.map(p => p.label);
  const pick = (p, f) => { const inner = trend.periodMap.get(p.key); const o = inner && inner.get('__ALL__'); return o ? (o[f] || 0) : 0; };
  if (isYoyMode()) {
    const ser = buildYoyGeneric(DEFICIT_ROWS, DEFICIT_FIELDS, rowFilter);
    if (ser.fyNums.length > 1) {
      renderChart('deficitTrendChart', yoyGenericConfig(ser, 'total_deficit', null,
        '赤字額合計', '', '金額(¥)', true, '赤字額'));
      return;
    }
  }
  renderChart('deficitTrendChart', dualAxisMoneyRightConfig(
    labels, trend.periods.map(p => pick(p, 'count')), trend.periods.map(p => pick(p, 'total_deficit')), '赤字商品数', '赤字額合計', '件数'
  ));
}

// 赤字ページの所見: 赤字の規模と、拠点・カテゴリ・仕入れ方法の偏りを指摘する
function renderDeficitInsight() {
  const granularity = granSel.value, periodKey = periodSel.value;
  const loc = locSel.value, useLoc = loc && loc !== ALL_LOC;
  const cats = deficitCatMultiSel ? getMultiSelectValues(deficitCatMultiSel) : [];
  // 「(全カテゴリ)」を選んでいる場合は絞り込まない
  const catSet = (cats.length && !cats.includes(ALL_CAT)) ? new Set(cats) : null;
  const rows = DEFICIT_ROWS.filter(r =>
    (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey) &&
    (!useLoc || r.location === loc) && (!catSet || catSet.has(r.category)));
  const meta = document.getElementById('deficitInsightMeta');
  if (meta) meta.textContent = '対象期間: ' + currentPeriodLabel() + ' / 絞り込み: ' + (useLoc ? loc : '全拠点') + ' × ' + (catSet ? cats.join(' + ') : '全カテゴリ');
  const el = document.getElementById('deficitInsightText');
  if (!el) return;
  let total = 0, cnt = 0;
  const byCat = new Map(), byLoc = new Map(), byProc = new Map();
  rows.forEach(r => {
    total += r.total_deficit || 0; cnt += r.count || 0;
    for (const [map, key] of [[byCat, r.category], [byLoc, r.location], [byProc, r.procurement_type]]) {
      if (!map.has(key)) map.set(key, { amt: 0, cnt: 0 });
      const o = map.get(key); o.amt += r.total_deficit || 0; o.cnt += r.count || 0;
    }
  });
  if (!cnt) { el.textContent = '対象データがありません。'; return; }
  const top = (map) => Array.from(map.entries()).sort((a, b) => b[1].amt - a[1].amt);
  const lines = [];
  const shippedTotal = Array.from(buildShippedIndex('category', granularity, periodKey,
    r => (!useLoc || r.location === loc) && (!catSet || catSet.has(r.category))).values())
    .reduce((a, v) => a + v, 0);
  lines.push('全体: 出荷' + fmtInt(shippedTotal) + '点のうち赤字(原価割れ)は' + fmtInt(cnt) + '点で、' +
    (shippedTotal ? '赤字率は' + fmtPct(cnt / shippedTotal) + '。' : '') +
    '赤字額の合計は' + fmtYen(total) + '(赤字1点あたり平均' + fmtYen(total / cnt) +
    (shippedTotal ? '、出荷1点あたり' + fmtYen(total / shippedTotal) : '') + ')。');
  const tc = top(byCat)[0], tl = top(byLoc)[0], tp = top(byProc)[0];
  if (tc) lines.push('カテゴリ別では「' + tc[0] + '」が最大で' + fmtYen(tc[1].amt) + '(' + fmtInt(tc[1].cnt) + '点、平均' + fmtYen(tc[1].amt / tc[1].cnt) + ')。全体の' + (total ? (tc[1].amt / total * 100).toFixed(1) : '-') + '%を占めます。');
  if (tl && !useLoc) lines.push('拠点別では「' + tl[0] + '」が最大で' + fmtYen(tl[1].amt) + '(' + fmtInt(tl[1].cnt) + '点)。買取査定の基準確認が有効です。');
  if (tp) lines.push('仕入れ方法別では「' + tp[0] + '」が' + fmtYen(tp[1].amt) + '(' + fmtInt(tp[1].cnt) + '点、平均' + fmtYen(tp[1].amt / tp[1].cnt) + ')と最も大きく、この経路の査定・値付けの見直しが赤字削減に直結します。');
  const worstAvg = Array.from(byCat.entries()).filter(([k, o]) => o.cnt >= 20).sort((a, b) => (b[1].amt / b[1].cnt) - (a[1].amt / a[1].cnt))[0];
  if (worstAvg) lines.push('1点あたりの赤字が最も大きいのは「' + worstAvg[0] + '」で平均' + fmtYen(worstAvg[1].amt / worstAvg[1].cnt) + '。件数は少なくても損失単価が大きい区分として注意が必要です。');
  el.textContent = lines.join('\n');
}

// ⑦: 「会計上の粗利」と「最終利益」の2軸サマリーを出す
function renderDeficitModeSummary() {
  const el = document.getElementById('deficitModeKpi');
  if (!el || typeof DEFICIT_MODE_ROWS === 'undefined') return;
  const granularity = granSel.value, periodKey = periodSel.value;
  const loc = locSel.value, useLoc = loc && loc !== ALL_LOC;
  const cats = deficitCatMultiSel ? getMultiSelectValues(deficitCatMultiSel) : [];
  const catSet = (cats.length && !cats.includes(ALL_CAT)) ? new Set(cats) : null;
  const rows = DEFICIT_MODE_ROWS.filter(r =>
    (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey) &&
    (!useLoc || r.location === loc) && (!catSet || catSet.has(r.category)));
  const t = rows.reduce((a, r) => {
    ['shipped_count', 'acc_deficit_count', 'acc_deficit_amount', 'acc_profit_sum',
     'fin_deficit_count', 'fin_deficit_amount', 'fin_profit_sum',
     'shipping_fee_total', 'lost_shipping_total', 'return_shipping_total',
     'returned_count'].forEach(f => a[f] = (a[f] || 0) + (r[f] || 0));
    return a;
  }, {});
  const mode = (document.getElementById('deficitMode') || {}).value || 'fin';
  const isFin = mode === 'fin';
  const cnt = isFin ? t.fin_deficit_count : t.acc_deficit_count;
  const amt = isFin ? t.fin_deficit_amount : t.acc_deficit_amount;
  const modeName = isFin ? '最終利益' : '粗利損';
  const ship = t.shipped_count || 0;
  el.innerHTML = [
    metricSimpleCardHtml('出荷商品数', fmtInt(ship) + '点'),
    metricSimpleCardHtml(modeName + 'ベースの赤字点数', fmtInt(cnt) + '点'),
    metricSimpleCardHtml('赤字率(赤字点数÷出荷点数)', ship ? fmtPct(cnt / ship) : '-'),
    metricSimpleCardHtml('赤字額(合計)', fmtYen(amt)),
    metricSimpleCardHtml('1点あたり赤字', cnt ? fmtYen(amt / cnt) : '-'),
    metricSimpleCardHtml('赤字額÷出荷点数', ship ? fmtYen(amt / ship) : '-'),
    metricSimpleCardHtml('粗利損: 点数 / 率', fmtInt(t.acc_deficit_count) + '点 / ' +
      (ship ? fmtPct(t.acc_deficit_count / ship) : '-')),
    metricSimpleCardHtml('最終利益: 点数 / 率', fmtInt(t.fin_deficit_count) + '点 / ' +
      (ship ? fmtPct(t.fin_deficit_count / ship) : '-')),
    metricSimpleCardHtml('返品点数', fmtInt(t.returned_count) + '点' +
      (ship ? '（' + fmtPct((t.returned_count || 0) / ship) + '）' : '')),
    metricSimpleCardHtml('返品分の送料損失(発送＋返送)',
      fmtYen((t.lost_shipping_total || 0) + (t.return_shipping_total || 0))),
    metricSimpleCardHtml('利益合計(' + modeName + ')', fmtYen(isFin ? t.fin_profit_sum : t.acc_profit_sum))
  ].join('');
}

// ⑦: カテゴリ × 仕入れ方法 のクロス集計表。
// deficit_mode_rows は「週×拠点×カテゴリ×仕入れ方法」で出荷点数と赤字点数・赤字額を
// 持っているため、この1つのデータだけで出荷母数まで含めたクロス集計が作れる。
function renderDeficitCrossTab() {
  const wrap = document.getElementById('deficitCrossTable');
  if (!wrap || typeof DEFICIT_MODE_ROWS === 'undefined') return;
  const granularity = granSel.value, periodKey = periodSel.value;
  const lbl = document.getElementById('deficitCrossPeriodLabel');
  if (lbl) lbl.textContent = currentPeriodLabel();
  const { rowFilter } = deficitFilterState();
  const isFin = ((document.getElementById('deficitMode') || {}).value || 'fin') === 'fin';
  const cntField = isFin ? 'fin_deficit_count' : 'acc_deficit_count';
  const amtField = isFin ? 'fin_deficit_amount' : 'acc_deficit_amount';

  const rows = DEFICIT_MODE_ROWS.filter(r =>
    (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey) &&
    (!rowFilter || rowFilter(r)));
  if (!rows.length) { wrap.innerHTML = '<p class="note">選択条件に合うデータがありません。</p>'; return; }

  const cell = new Map(), catShip = new Map(), procShip = new Map();
  const blank = () => ({ ship: 0, cnt: 0, amt: 0 });
  const bump = (map, key, r) => {
    if (!map.has(key)) map.set(key, blank());
    const o = map.get(key);
    o.ship += r.shipped_count || 0; o.cnt += r[cntField] || 0; o.amt += r[amtField] || 0;
  };
  rows.forEach(r => {
    bump(cell, r.category + '||' + r.procurement_type, r);
    bump(catShip, r.category, r);
    bump(procShip, r.procurement_type, r);
  });

  const cats = Array.from(catShip.keys()).filter(c => catShip.get(c).ship > 0)
    .sort((a, b) => catShip.get(b).ship - catShip.get(a).ship).slice(0, 15);
  const procs = Array.from(procShip.keys()).filter(p => procShip.get(p).ship > 0)
    .sort((a, b) => procShip.get(b).ship - procShip.get(a).ship);
  if (!cats.length || !procs.length) { wrap.innerHTML = '<p class="note">選択条件に合うデータがありません。</p>'; return; }

  const metric = (document.getElementById('deficitCrossMetric') || {}).value || 'rate';
  const valueOf = (o) => {
    if (!o) return null;
    if (metric === 'count') return o.cnt;
    if (metric === 'amount') return o.amt;
    if (metric === 'shipped') return o.ship;
    if (metric === 'per_ship') return o.ship ? o.amt / o.ship : null;
    return o.ship ? o.cnt / o.ship : null;   // rate
  };
  const fmtVal = (v) => {
    if (v === null || v === undefined) return '-';
    if (metric === 'rate') return fmtPct(v);
    if (metric === 'amount' || metric === 'per_ship') return fmtYen(v);
    return fmtInt(v);
  };
  // 色の濃さはセル値の最大値で正規化する(指標ごとにスケールが違うため)
  let maxV = 0;
  cats.forEach(c => procs.forEach(p => {
    const v = valueOf(cell.get(c + '||' + p));
    if (v != null && v > maxV) maxV = v;
  }));
  const bg = (v) => {
    if (v == null || !(maxV > 0) || !(v > 0)) return '#f5f6f8';
    return 'rgba(224,101,58,' + (0.06 + 0.44 * Math.min(1, v / maxV)).toFixed(3) + ')';
  };
  const th = 'padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;white-space:nowrap;';
  const td = 'padding:6px 10px;border:1px solid #e3e5e8;text-align:right;white-space:nowrap;';

  let html = '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr>' +
    '<th style="' + th + 'text-align:left;">カテゴリ＼仕入れ方法</th>';
  procs.forEach(p => { html += '<th style="' + th + '">' + p + '</th>'; });
  html += '<th style="' + th + '">合計</th></tr></thead><tbody>';
  const rowHtml = (label, getter, bold) => {
    let s = '<tr><td style="padding:6px 10px;border:1px solid #e3e5e8;white-space:nowrap;'
      + (bold ? 'font-weight:700;background:#f5f6f8;' : 'font-weight:600;') + '">' + label + '</td>';
    procs.concat(['__TOTAL__']).forEach(p => {
      const o = getter(p), v = valueOf(o);
      const tip = o ? '出荷' + fmtInt(o.ship) + '点 / 赤字' + fmtInt(o.cnt) + '点 / ' + fmtYen(o.amt) : '';
      s += '<td style="' + td + 'background:' + (bold ? '#f5f6f8' : bg(v)) + ';" title="' + tip + '">' + fmtVal(v) + '</td>';
    });
    return s + '</tr>';
  };
  const sumOf = (keys, getter) => keys.reduce((a, k) => {
    const o = getter(k); if (o) { a.ship += o.ship; a.cnt += o.cnt; a.amt += o.amt; } return a;
  }, blank());
  cats.forEach(c => {
    html += rowHtml(c, p => (p === '__TOTAL__' ? sumOf(procs, q => cell.get(c + '||' + q)) : cell.get(c + '||' + p)), false);
  });
  html += rowHtml('合計(表示中カテゴリ)',
    p => (p === '__TOTAL__' ? sumOf(cats, c => sumOf(procs, q => cell.get(c + '||' + q)))
                            : sumOf(cats, c => cell.get(c + '||' + p))), true);
  wrap.innerHTML = html + '</tbody></table>';
}

// ⑦: カテゴリ名クリックで開く「商品1件ごとの明細」
// 現在選択中の期間・拠点で絞り込み、そのカテゴリの商品を1行ずつ表示する。
let deficitDrillCat = null;

const ITEM_DETAIL_COLUMNS = [
  { name: '出荷日' },
  { name: '商品ID' },
  { name: '拠点' },
  { name: '仕入れ方法' },
  { name: 'コンディション' },
  { name: '価格帯' },
  { name: '買取者' },
  { name: '買取価格(円)', formatter: c => fmtYen(c) },
  { name: '見込販売価格(円)', formatter: c => fmtYen(c) },
  { name: '落札価格(円)', formatter: c => fmtYen(c) },
  { name: '見込差(円)', formatter: c => gridjs.html('<span style="color:' + (c < 0 ? '#c0392b' : '#1e7a46') + ';">' + fmtYen(c) + '</span>') },
  { name: '粗利(円)', formatter: c => fmtYen(c) },
  { name: '発送送料(損失・円)', formatter: c => fmtYen(c) },
  { name: '返送料(円)', formatter: c => fmtYen(c) },
  { name: '最終利益(円)', formatter: c => gridjs.html('<span style="color:' + (c < 0 ? '#c0392b' : '#1e7a46') + ';font-weight:600;">' + fmtYen(c) + '</span>') },
  { name: 'SR', formatter: c => (c > 0 ? 'あり(' + c + ')' : '-') },
  { name: 'SR分類' },
  { name: '原因' },
  { name: '返品' },
  { name: '返金額(円)', formatter: c => fmtYen(c) }
];

function itemDetailRow(r) {
  return [
    r.week_start, r.product_id || '-', r.location, r.procurement_type, r.condition, r.price_band,
    r.buyer, r.buy_price, r.expected_price, r.sale_price, r.variance,
    r.actual_profit, r.lost_shipping, r.return_shipping, r.final_profit,
    r.sr_count,
    r.sr_count ? (r.sr_major + (r.sr_minor && r.sr_minor !== '(小項目なし)' ? ' / ' + r.sr_minor : '')) : '-',
    r.sr_count ? (r.cause_major + (r.cause_part && r.cause_part !== '(不明)' ? ' / ' + r.cause_part : '')) : '-',
    r.returned ? 'あり' : '-',
    r.refund_amount
  ];
}

function renderDeficitItemDrill(cat) {
  const box = document.getElementById('deficitItemBox');
  if (!box) return;
  deficitDrillCat = cat;
  box.style.display = '';
  if (typeof ITEM_DETAIL_ROWS === 'undefined' || !ITEM_DETAIL_ROWS.length) {
    document.getElementById('deficitItemTitle').textContent = cat + ' の商品明細';
    document.getElementById('deficitItemSummary').textContent =
      '商品明細データがまだ作られていません(データを再集計すると表示されます)。';
    document.getElementById('deficitItemTable').innerHTML = '';
    return;
  }
  const granularity = granSel.value, periodKey = periodSel.value;
  const loc = locSel.value, useLoc = loc && loc !== ALL_LOC;
  const rows = ITEM_DETAIL_ROWS.filter(r =>
    r.category === cat &&
    (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey) &&
    (!useLoc || r.location === loc));

  document.getElementById('deficitItemTitle').textContent =
    cat + ' の商品明細(' + currentPeriodLabel() + ' / ' + (useLoc ? loc : '全拠点') + ')';

  const n = rows.length;
  const srN = rows.filter(r => r.sr_count > 0).length;
  const lossN = rows.filter(r => r.final_profit < 0).length;
  const lossAmt = rows.filter(r => r.final_profit < 0).reduce((a, r) => a + (-r.final_profit), 0);
  const refund = rows.reduce((a, r) => a + (r.refund_amount || 0), 0);
  const retN = rows.filter(r => r.returned).length;
  document.getElementById('deficitItemSummary').textContent = n
    ? ('該当' + fmtInt(n) + '点  /  うち最終利益マイナス' + fmtInt(lossN) + '点(' + fmtYen(lossAmt) + ')  /  '
       + 'SR発生' + fmtInt(srN) + '点  /  返品' + fmtInt(retN) + '点  /  返金額合計' + fmtYen(refund))
    : 'この条件に該当する商品はありません(期間・拠点の絞り込みを広げてみてください)。';

  rows.sort((a, b) => (a.final_profit || 0) - (b.final_profit || 0));
  renderSimpleTableInto('deficitItemTable', ITEM_DETAIL_COLUMNS, rows.map(itemDetailRow), 20);
}

// カテゴリ名クリックの受け口(Grid.jsは再描画で要素が入れ替わるためイベント委譲で拾う)
if (typeof document.addEventListener === 'function') {
  document.addEventListener('click', (ev) => {
    const t = ev.target;
    if (t && t.classList && t.classList.contains('deficit-drill')) {
      ev.preventDefault();
      renderDeficitItemDrill(t.getAttribute('data-cat'));
      const box = document.getElementById('deficitItemBox');
      if (box && box.scrollIntoView) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
}

// ⑦: 雑損(再販不可)の集計。売上ゼロで買取価格がまるごと損失になる商品。
const WRITEOFF_FIELDS = ['count', 'loss_amount'];

function renderWriteoffSection() {
  const el = document.getElementById('writeoffKpi');
  if (!el) return;
  const granularity = granSel.value, periodKey = periodSel.value;
  const { rowFilter, locLabel, catLabel } = deficitFilterState();
  const lbl = document.getElementById('writeoffPeriodLabel');
  if (lbl) lbl.textContent = currentPeriodLabel() + ' / ' + locLabel + ' × ' + catLabel;

  const inPeriod = (r) => (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey);
  const rows = (typeof WRITEOFF_ROWS === 'undefined' ? [] : WRITEOFF_ROWS)
    .filter(r => inPeriod(r) && (!rowFilter || rowFilter(r)));
  const cnt = rows.reduce((a, r) => a + (r.count || 0), 0);
  const loss = rows.reduce((a, r) => a + (r.loss_amount || 0), 0);
  // 母数の出荷点数は⑦の他のセクションと同じ deficit_mode_rows から取る
  const ship = Array.from(buildShippedIndex('category', granularity, periodKey, rowFilter).values())
    .reduce((a, v) => a + v, 0);

  el.innerHTML = [
    metricSimpleCardHtml('雑損 点数', fmtInt(cnt) + '点'),
    metricSimpleCardHtml('雑損率(雑損点数÷出荷点数)', ship ? fmtPct(cnt / ship) : '-'),
    metricSimpleCardHtml('雑損 損失額(買取価格・税抜)', fmtYen(loss)),
    metricSimpleCardHtml('1点あたり損失', cnt ? fmtYen(loss / cnt) : '-'),
    metricSimpleCardHtml('出荷1点あたり', ship ? fmtYen(loss / ship) : '-')
  ].join('');

  // 推移
  const trend = buildDimTrendAlignedGeneric(
    (typeof WRITEOFF_ROWS === 'undefined' ? [] : WRITEOFF_ROWS), null, WRITEOFF_FIELDS, granularity, rowFilter);
  const labels = trend.periods.map(p => p.label);
  const pick = (p, f) => { const inner = trend.periodMap.get(p.key); const o = inner && inner.get('__ALL__'); return o ? (o[f] || 0) : 0; };
  renderChart('writeoffTrendChart', dualAxisMoneyRightConfig(
    labels, trend.periods.map(p => pick(p, 'count')), trend.periods.map(p => pick(p, 'loss_amount')),
    '雑損 点数', '損失額', '件数'));

  // カテゴリ別・拠点別
  const cols = (nameLabel) => [
    { name: nameLabel },
    { name: '雑損 点数', formatter: c => fmtInt(c) },
    { name: '損失額(円)', formatter: c => fmtYen(c) },
    { name: '1点あたり(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) }
  ];
  [['category', 'カテゴリ', 'writeoffCatTable'], ['location', '拠点', 'writeoffLocTable']].forEach(([dim, label, id]) => {
    const data = buildDimBreakdownGeneric(
      (typeof WRITEOFF_ROWS === 'undefined' ? [] : WRITEOFF_ROWS), dim, WRITEOFF_FIELDS, granularity, periodKey, rowFilter);
    data.sort((a, b) => b.loss_amount - a.loss_amount);
    renderSimpleTableInto(id, cols(label),
      data.map(d => [d.name, d.count, d.loss_amount, d.count ? d.loss_amount / d.count : null]), 10);
  });
}

function renderDeficitPage() {
  renderDeficitModeSummary();
  renderDeficitInsight();
  renderDeficitKPIs();
  renderDeficitTrend();
  renderDeficitCategorySection();
  renderDeficitProcSection();
  renderDeficitCrossTab();
  renderWriteoffSection();
  // 商品明細を開いたまま期間・拠点を変えたときは、同じカテゴリで中身を更新する
  if (deficitDrillCat) renderDeficitItemDrill(deficitDrillCat);
}

// ---------- ⑨ SRリピーター・ロイヤルカスタマー分析ページ ----------
// 顧客数が多い(ロイヤルカスタマーは約2万人)ため、セグメントごとの行配列・集計値は
// 初回計算時にキャッシュし、タブを切り替えるたびに全行を走査し直さないようにする。
const CUSTOMER_SEGMENTS = {
  sr_repeater: {
    title: 'SRリピーター(SR発生件数 上位20%)',
    desc: 'SRが1件以上発生した顧客を母集団として、SR発生件数の多い順に並べた上位20%の顧客です。'
        + 'SRを繰り返し起こしている顧客層の規模と、その顧客層がもたらす返金額・最終利益への影響を確認できます。'
  },
  loyal_customer: {
    title: 'ロイヤルカスタマー(売上額 上位20%)',
    desc: '全顧客を売上額(落札価格の合計)の多い順に並べた上位20%の顧客です。'
        + '売上上位の優良顧客層のSR率・返金額率が、全体水準と比べてどうなっているかを確認できます。'
  }
};
let currentCustomerSegment = 'sr_repeater';
const customerSegmentCache = {};

function customerSegmentData(segment) {
  if (customerSegmentCache[segment]) return customerSegmentCache[segment];
  const rows = CUSTOMER_SEGMENT_ROWS.filter(r => r.segment === segment);
  const agg = {
    customer_count: rows.length, order_count: 0, bundle_order_count: 0, shipped_count: 0,
    sr_count: 0, sales_amount: 0, gross_profit: 0, refund_amount: 0,
    return_shipping_cost: 0, final_profit: 0
  };
  rows.forEach(r => {
    agg.order_count += r.order_count || 0;
    agg.bundle_order_count += r.bundle_order_count || 0;
    agg.shipped_count += r.shipped_count || 0;
    agg.sr_count += r.sr_count || 0;
    agg.sales_amount += r.sales_amount || 0;
    agg.gross_profit += r.gross_profit || 0;
    agg.refund_amount += r.refund_amount || 0;
    agg.return_shipping_cost += r.return_shipping_cost || 0;
    agg.final_profit += r.final_profit || 0;
  });
  agg.bundle_rate = agg.order_count ? agg.bundle_order_count / agg.order_count : null;
  agg.sr_rate = agg.shipped_count ? agg.sr_count / agg.shipped_count : null;
  agg.refund_rate = agg.sales_amount ? agg.refund_amount / agg.sales_amount : null;
  // Grid.js に渡す2次元配列も一度だけ作って使い回す(2万行の再生成を避ける)
  const tableRows = rows.map(r => [
    r.label,
    r.customer_type || '個人',
    r.shipped_count,
    r.order_count ? r.bundle_order_count / r.order_count : null,
    r.shipped_count ? r.sr_count / r.shipped_count : null,
    r.sr_count,
    r.refund_amount,
    r.sales_amount ? r.refund_amount / r.sales_amount : null,
    r.final_profit
  ]);
  customerSegmentCache[segment] = { rows, agg, tableRows };
  return customerSegmentCache[segment];
}

const CUSTOMER_TABLE_COLUMNS = [
  {
    name: '顧客',
    // クリックでその顧客の内訳(購入・SR)を上部に表示する
    formatter: (cell) => gridjs.html(
      '<a href="#" class="cust-drill" data-label="' + cell + '" ' +
      'style="color:#2455c9;text-decoration:underline;cursor:pointer;">' + cell + '</a>')
  },
  { name: '種別' },
  { name: '発送商品数', formatter: c => fmtInt(c) },
  { name: '同梱率', formatter: c => fmtPct(c) },
  { name: 'SR率', formatter: c => fmtPct(c) },
  { name: 'SR発生件数', formatter: c => fmtInt(c) },
  { name: '返金額(円)', formatter: c => fmtYen(c) },
  { name: '返金額率', formatter: c => fmtPct(c) },
  { name: '最終利益(円)', formatter: c => fmtYen(c) }
];

// ⑧顧客ページの絞り込み(発送商品数・SR発生件数の下限)。
// 絞り込み後の顧客集合に対して、KPIも「分子・分母をそれぞれ合算してから率を出す」方式で
// 再計算する(顧客ごとの率の平均ではない)。
function customerFilteredData(segment) {
  const base = customerSegmentData(segment);
  const minShipped = parseInt((document.getElementById('custMinShipped') || {}).value || '0', 10);
  const minSr = parseInt((document.getElementById('custMinSr') || {}).value || '0', 10);
  const custType = (document.getElementById('custType') || {}).value || '__ALL__';
  if (!minShipped && !minSr && custType === '__ALL__') {
    return { agg: base.agg, tableRows: base.tableRows, filtered: false, total: base.rows.length };
  }
  const rows = base.rows.filter(r => (r.shipped_count || 0) >= minShipped && (r.sr_count || 0) >= minSr &&
    (custType === '__ALL__' || (r.customer_type || '個人') === custType));
  const agg = {
    customer_count: rows.length, order_count: 0, bundle_order_count: 0, shipped_count: 0,
    sr_count: 0, sales_amount: 0, gross_profit: 0, refund_amount: 0, return_shipping_cost: 0, final_profit: 0
  };
  rows.forEach(r => {
    agg.order_count += r.order_count || 0;
    agg.bundle_order_count += r.bundle_order_count || 0;
    agg.shipped_count += r.shipped_count || 0;
    agg.sr_count += r.sr_count || 0;
    agg.sales_amount += r.sales_amount || 0;
    agg.gross_profit += r.gross_profit || 0;
    agg.refund_amount += r.refund_amount || 0;
    agg.return_shipping_cost += r.return_shipping_cost || 0;
    agg.final_profit += r.final_profit || 0;
  });
  agg.bundle_rate = agg.order_count ? agg.bundle_order_count / agg.order_count : null;
  agg.sr_rate = agg.shipped_count ? agg.sr_count / agg.shipped_count : null;
  agg.refund_rate = agg.sales_amount ? agg.refund_amount / agg.sales_amount : null;
  const tableRows = rows.map(r => [
    r.label, r.customer_type || '個人', r.shipped_count,
    r.order_count ? r.bundle_order_count / r.order_count : null,
    r.shipped_count ? r.sr_count / r.shipped_count : null,
    r.sr_count, r.refund_amount,
    r.sales_amount ? r.refund_amount / r.sales_amount : null,
    r.final_profit
  ]);
  return { agg, tableRows, filtered: true, total: base.rows.length, minShipped, minSr };
}

function renderCustomerPage() {
  const segment = currentCustomerSegment;
  const meta = CUSTOMER_SEGMENTS[segment];
  document.getElementById('segBtnSrRepeater').classList.toggle('active', segment === 'sr_repeater');
  document.getElementById('segBtnLoyalCustomer').classList.toggle('active', segment === 'loyal_customer');
  document.getElementById('customerSegTitle').textContent = meta.title;
  document.getElementById('customerSegDesc').textContent = meta.desc;

  const { agg, tableRows, filtered, total, minShipped, minSr } = customerFilteredData(segment);
  const noteEl = document.getElementById('custFilterNote');
  if (noteEl) {
    noteEl.textContent = filtered
      ? ('絞り込み中: ' + ((document.getElementById('custType') || {}).value === '__ALL__' ? '個人+業者拠点' : (document.getElementById('custType') || {}).value) +
         ' / 発送' + minShipped + '件以上 かつ SR' + minSr + '件以上 → ' + fmtInt(agg.customer_count) + '人 / 全' + fmtInt(total) + '人')
      : ('全' + fmtInt(total) + '人を表示中');
  }
  document.getElementById('customerKpiGrid').innerHTML = [
    metricSimpleCardHtml('該当人数', fmtInt(agg.customer_count) + '人'),
    metricSimpleCardHtml('発送商品数(合計)', fmtInt(agg.shipped_count) + '点'),
    metricSimpleCardHtml('同梱率(平均)', fmtPct(agg.bundle_rate)),
    metricSimpleCardHtml('SR率(平均)', fmtPct(agg.sr_rate)),
    metricSimpleCardHtml('SR発生件数(合計)', fmtInt(agg.sr_count) + '件'),
    metricSimpleCardHtml('返金額(合計)', fmtYen(agg.refund_amount)),
    metricSimpleCardHtml('返金額率', fmtPct(agg.refund_rate)),
    metricSimpleCardHtml('最終利益(合計)', fmtYen(agg.final_profit))
  ].join('');

  document.getElementById('customerTableTitle').textContent =
    meta.title + ' 顧客別詳細(' + fmtInt(agg.customer_count) + '人)';
  renderSimpleTableInto('detailTableCustomer', CUSTOMER_TABLE_COLUMNS, tableRows, 20);
}

// 顧客1人分の内訳を表示する(購入カテゴリ別のSR率 / SR大項目 / 返品有無 / コンディション・価格帯)
// 手組みの表をクリックでソートできるようにする共通処理。
// 各行に data-sort 属性(数値)を持たせ、ヘッダのクリックで昇順/降順を切り替える。
function makeSortable(tableId, defaultCol) {
  const tbl = document.getElementById(tableId);
  if (!tbl) return;
  const table = tbl.querySelector ? tbl.querySelector('table') : null;
  if (!table) return;
  const ths = table.querySelectorAll('thead th');
  const tbody = table.querySelector('tbody');
  if (!ths || !tbody) return;
  const state = { col: defaultCol == null ? -1 : defaultCol, asc: false };
  const apply = () => {
    const rows = Array.from(tbody.querySelectorAll('tr')).filter(tr => !tr.dataset.total);
    const totals = Array.from(tbody.querySelectorAll('tr')).filter(tr => tr.dataset.total);
    rows.sort((a, b) => {
      const av = a.children[state.col], bv = b.children[state.col];
      const an = av ? parseFloat(av.dataset.sort != null ? av.dataset.sort : av.textContent.replace(/[^0-9.\-]/g, '')) : 0;
      const bn = bv ? parseFloat(bv.dataset.sort != null ? bv.dataset.sort : bv.textContent.replace(/[^0-9.\-]/g, '')) : 0;
      if (isNaN(an) && isNaN(bn)) return 0;
      if (isNaN(an)) return 1;
      if (isNaN(bn)) return -1;
      return state.asc ? an - bn : bn - an;
    });
    rows.concat(totals).forEach(tr => tbody.appendChild(tr));
    ths.forEach((th, i) => {
      const base = th.dataset.label || th.textContent.replace(/[▲▼]/g, '').trim();
      th.dataset.label = base;
      th.textContent = base + (i === state.col ? (state.asc ? ' ▲' : ' ▼') : '');
    });
  };
  ths.forEach((th, i) => {
    th.style.cursor = 'pointer';
    th.title = 'クリックで並び替え';
    th.onclick = () => { if (state.col === i) state.asc = !state.asc; else { state.col = i; state.asc = false; } apply(); };
  });
  if (state.col >= 0) apply();
}

function renderCustomerDrill(label) {
  const box = document.getElementById('custDrillBox');
  const body = document.getElementById('custDrillBody');
  if (!box || !body) return;
  const rows = CUSTOMER_DETAIL_BY_LABEL.get(label) || [];
  const seg = CUSTOMER_SEGMENT_ROWS.find(r => r.label === label) || {};
  box.style.display = '';
  document.getElementById('custDrillTitle').textContent =
    label + ' の詳細(' + (seg.customer_type || '個人') + ')  発送' + fmtInt(seg.shipped_count) +
    '件 / SR' + fmtInt(seg.sr_count) + '件 / 返金' + fmtYen(seg.refund_amount);
  if (!rows.length) { body.innerHTML = '<p class="note">この顧客の内訳データはありません。</p>'; return; }

  const purchases = rows.filter(r => r.kind === 'purchase');
  const srs = rows.filter(r => r.kind === 'sr');
  // 返金はCS_返金が正(SR側の返金額列は未入力のことが多いため、金額・返品有無はこちらを使う)
  const refunds = rows.filter(r => r.kind === 'refund');
  const sum = (arr, f) => arr.reduce((a, r) => a + (r[f] || 0), 0);

  const tblIds = [];
  const tbl = (title, head, body2, tid) => {
    if (tid) tblIds.push(tid);
    return '<h4 style="margin:14px 0 6px;">' + title + '</h4>' +
    '<div' + (tid ? ' id="' + tid + '"' : '') + '>' +
    '<table style="width:100%;border-collapse:collapse;font-size:12.5px;"><thead><tr>' +
    head.map(h => '<th style="padding:5px 9px;border:1px solid #e3e5e8;background:#f5f6f8;text-align:' +
      (h.num ? 'right' : 'left') + ';">' + h.name + '</th>').join('') +
    '</tr></thead><tbody>' + body2 + '</tbody></table></div>';
  };
  const td = (v, num, sortVal) => '<td' + (sortVal != null ? ' data-sort="' + sortVal + '"' : '') +
    ' style="padding:5px 9px;border:1px solid #e3e5e8;text-align:' + (num ? 'right' : 'left') + ';">' + v + '</td>';

  // カテゴリ別: 購入数とSR件数を突き合わせてSR率を出す
  const byCat = new Map();
  purchases.forEach(r => {
    const o = byCat.get(r.category) || { buy: 0, sales: 0, profit: 0, sr: 0, refund: 0 };
    o.buy += r.count || 0; o.sales += r.sales_amount || 0; o.profit += r.gross_profit || 0;
    byCat.set(r.category, o);
  });
  srs.forEach(r => {
    const o = byCat.get(r.category) || { buy: 0, sales: 0, profit: 0, sr: 0, refund: 0 };
    o.sr += r.count || 0;
    byCat.set(r.category, o);
  });
  refunds.forEach(r => {
    const o = byCat.get(r.category) || { buy: 0, sales: 0, profit: 0, sr: 0, refund: 0 };
    o.refund += r.refund_amount || 0;
    byCat.set(r.category, o);
  });
  const catList = Array.from(byCat.entries()).sort((a, b) => (b[1].sr - a[1].sr) || (b[1].buy - a[1].buy));
  const catTotal = catList.reduce((a, [, o]) => ({
    buy: a.buy + o.buy, sr: a.sr + o.sr, sales: a.sales + o.sales, refund: a.refund + o.refund
  }), { buy: 0, sr: 0, sales: 0, refund: 0 });
  const totalTd = (v, num) => '<td style="padding:5px 9px;border:1px solid #e3e5e8;background:#eef1f5;font-weight:700;text-align:' +
    (num ? 'right' : 'left') + ';">' + v + '</td>';
  const catRows = catList
    .map(([k, o]) => '<tr>' + td(k, 0, k) + td(fmtInt(o.buy), 1, o.buy) + td(fmtInt(o.sr), 1, o.sr) +
      td(o.buy ? fmtPct(o.sr / o.buy) : '-', 1, o.buy ? o.sr / o.buy : -1) + td(fmtYen(o.sales), 1, o.sales) +
      td(fmtYen(o.refund), 1, o.refund) +
      td(o.sales ? fmtPct(o.refund / o.sales) : '-', 1, o.sales ? o.refund / o.sales : -1) + '</tr>').join('') +
    '<tr data-total="1">' + totalTd('合計') + totalTd(fmtInt(catTotal.buy), 1) + totalTd(fmtInt(catTotal.sr), 1) +
      totalTd(catTotal.buy ? fmtPct(catTotal.sr / catTotal.buy) : '-', 1) + totalTd(fmtYen(catTotal.sales), 1) +
      totalTd(fmtYen(catTotal.refund), 1) + totalTd(catTotal.sales ? fmtPct(catTotal.refund / catTotal.sales) : '-', 1) + '</tr>';

  // SR大項目 × 返品有無
  // SR大項目 + その中の小項目。総計に対する割合と、大項目内での割合の両方を出す。
  const byMajor = new Map();
  srs.forEach(r => {
    const key = r.major || '(未分類)';
    const o = byMajor.get(key) || { n: 0, minors: new Map() };
    o.n += r.count || 0;
    const mk = r.minor || '(小項目なし)';
    o.minors.set(mk, (o.minors.get(mk) || 0) + (r.count || 0));
    byMajor.set(key, o);
  });
  const srTotalAll = Array.from(byMajor.values()).reduce((a, o) => a + o.n, 0);
  const majorRows = Array.from(byMajor.entries()).sort((a, b) => b[1].n - a[1].n)
    .map(([k, o]) => {
      const head = '<tr>' + td('<b>' + k + '</b>', 0, k) + td(fmtInt(o.n), 1, o.n) +
        td(srTotalAll ? fmtPct(o.n / srTotalAll) : '-', 1, srTotalAll ? o.n / srTotalAll : -1) +
        td('—', 1, -1) + '</tr>';
      const kids = Array.from(o.minors.entries()).sort((a, b) => b[1] - a[1])
        .map(([mk, mv]) => '<tr>' + td('　└ ' + mk, 0, k + '/' + mk) + td(fmtInt(mv), 1, mv) +
          td(srTotalAll ? fmtPct(mv / srTotalAll) : '-', 1, srTotalAll ? mv / srTotalAll : -1) +
          td(o.n ? fmtPct(mv / o.n) : '-', 1, o.n ? mv / o.n : -1) + '</tr>').join('');
      return head + kids;
    }).join('') +
    '<tr data-total="1">' + totalTd('合計') + totalTd(fmtInt(srTotalAll), 1) +
      totalTd('100.00%', 1) + totalTd('—', 1) + '</tr>';

  // 返品有無 × 返金額(CS_返金ベース)
  const byRet = new Map();
  refunds.forEach(r => {
    const o = byRet.get(r.returned) || { n: 0, amt: 0 };
    o.n += r.count || 0; o.amt += r.refund_amount || 0;
    byRet.set(r.returned, o);
  });
  const totalSales = catTotal.sales;
  const retList = Array.from(byRet.entries()).sort((a, b) => b[1].amt - a[1].amt);
  const retTotalRow = retList.reduce((a, [, o]) => ({ n: a.n + o.n, amt: a.amt + o.amt }), { n: 0, amt: 0 });
  const retRows = retList
    .map(([k, o]) => '<tr>' + td(k, 0, k) + td(fmtInt(o.n), 1, o.n) + td(fmtYen(o.amt), 1, o.amt) +
      td(o.n ? fmtYen(o.amt / o.n) : '-', 1, o.n ? o.amt / o.n : -1) +
      td(totalSales ? fmtPct(o.amt / totalSales) : '-', 1, totalSales ? o.amt / totalSales : -1) + '</tr>').join('') +
    '<tr data-total="1">' + totalTd('合計') + totalTd(fmtInt(retTotalRow.n), 1) + totalTd(fmtYen(retTotalRow.amt), 1) +
      totalTd(retTotalRow.n ? fmtYen(retTotalRow.amt / retTotalRow.n) : '-', 1) +
      totalTd(totalSales ? fmtPct(retTotalRow.amt / totalSales) : '-', 1) + '</tr>';

  // コンディション・価格帯
  const agg = (field) => {
    const m2 = new Map();
    purchases.forEach(r => {
      const o = m2.get(r[field]) || { n: 0, sales: 0, sort: r.price_band_sort || 0 };
      o.n += r.count || 0; o.sales += r.sales_amount || 0;
      m2.set(r[field], o);
    });
    return Array.from(m2.entries());
  };
  const condRows = agg('condition').sort((a, b) => conditionSortKey(a[0]) - conditionSortKey(b[0]))
    .map(([k, o]) => '<tr>' + td(k) + td(fmtInt(o.n), 1) + td(fmtYen(o.sales), 1) + '</tr>').join('');
  const bandRows = agg('price_band').sort((a, b) => a[1].sort - b[1].sort)
    .map(([k, o]) => '<tr>' + td(k) + td(fmtInt(o.n), 1) + td(fmtYen(o.sales), 1) + '</tr>').join('');

  const retTotal = refunds.filter(r => r.returned === '返品あり').reduce((a, r) => a + (r.count || 0), 0);
  const tBuy = sum(purchases, 'count'), tSr = sum(srs, 'count');
  const tRefN = sum(refunds, 'count'), tRefAmt = sum(refunds, 'refund_amount');
  const tSales = sum(purchases, 'sales_amount'), tProfit = sum(purchases, 'gross_profit');
  body.innerHTML =
    '<div class="kpi-grid" style="margin:10px 0 4px;">' +
    metricSimpleCardHtml('購入数', fmtInt(tBuy) + '点') +
    metricSimpleCardHtml('SR発生件数', fmtInt(tSr) + '件') +
    metricSimpleCardHtml('SR率(平均)', tBuy ? fmtPct(tSr / tBuy) : '-') +
    metricSimpleCardHtml('返金件数', fmtInt(tRefN) + '件(返品' + fmtInt(retTotal) + '件)') +
    metricSimpleCardHtml('返金額', fmtYen(tRefAmt)) +
    metricSimpleCardHtml('返金額率(平均)', tSales ? fmtPct(tRefAmt / tSales) : '-') +
    metricSimpleCardHtml('売上', fmtYen(tSales)) +
    metricSimpleCardHtml('粗利率(平均)', tSales ? fmtPct(tProfit / tSales) : '-') +
    '</div>' +
    tbl('カテゴリ別のSR発生率・返金額率', [{ name: 'カテゴリ' }, { name: '購入数', num: 1 }, { name: 'SR件数', num: 1 },
      { name: 'SR率', num: 1 }, { name: '売上(円)', num: 1 }, { name: '返金額(円)', num: 1 },
      { name: '返金額率', num: 1 }], catRows) +
    (majorRows ? tbl('SR大項目・小項目の内訳', [{ name: 'SR大項目 / 小項目' }, { name: '件数', num: 1 },
      { name: '総計比', num: 1 }, { name: '大項目内の比率', num: 1 }], majorRows) : '') +
    (retRows ? tbl('返品有無・返金額', [{ name: '返品' }, { name: '返金件数', num: 1 },
      { name: '返金額(円)', num: 1 }, { name: '1件あたり', num: 1 }, { name: '返金額率(対売上)', num: 1 }], retRows) : '') +
    '<div class="mini-charts-grid" style="margin-top:6px;"><div>' +
    tbl('コンディション別の購入', [{ name: 'コンディション' }, { name: '購入数', num: 1 }, { name: '売上(円)', num: 1 }], condRows) +
    '</div><div>' +
    tbl('価格帯別の購入', [{ name: '価格帯' }, { name: '購入数', num: 1 }, { name: '売上(円)', num: 1 }], bandRows, 'custTblBand') +
    '</div></div>';
  // 表のヘッダをクリックで並び替えできるようにする(既定: カテゴリ表はSR率降順、他は件数降順)
  tblIds.forEach(id => makeSortable(id, id === 'custTblCat' ? 3 : (id === 'custTblMajor' ? -1 : 1)));
}

// 顧客名クリックの受け口(Grid.jsは再描画で要素が入れ替わるためイベント委譲で拾う)
if (typeof document.addEventListener === 'function') {
  document.addEventListener('click', (ev) => {
    const t = ev.target;
    if (t && t.classList && t.classList.contains('cust-drill')) {
      ev.preventDefault();
      renderCustomerDrill(t.getAttribute('data-label'));
      const box = document.getElementById('custDrillBox');
      if (box && box.scrollIntoView) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
}

function setCustomerSegment(segment) {
  currentCustomerSegment = segment;
  renderCustomerPage();
}

function renderDetailTable(axis) {
  const granularity = granSel.value;
  const periodKey = periodSel.value;
  let data = buildBreakdown(granularity, axis, periodKey, null);
  data.sort((a, b) => (b.inquiry_count + b.sr_count + b.question_count) - (a.inquiry_count + a.sr_count + a.question_count));
  const containerId = axis === 'location' ? 'detailTableLocation' : 'detailTableCategory';
  const titleId = axis === 'location' ? 'tableTitleLocation' : 'tableTitleCategory';
  document.getElementById(titleId).textContent = (axis === 'location' ? '詳細テーブル(拠点別) ' : '詳細テーブル(カテゴリ別) ') + currentPeriodLabel();
  renderTableInto(containerId, data, axis === 'location' ? '拠点' : 'カテゴリ', axis === 'location' ? 20 : 12);
}

// ---------- 生成: ①全拠点ページ ----------
function renderOverallPage() {
  renderOverallKPIs();
  renderInsights();
  renderOverallTrendCharts();
  renderOverallSrMajorChart();
  renderLocationBreakdown();
  renderLocationSrMajorChart();
  // C項目: ジャンク出品比率ヒートマップは⑤拠点×カテゴリページ(renderLocCatPage)へ移動した
  renderDetailTable('location');
  renderImprovementSection('ov', null);
}

// ---------- 生成: ③全カテゴリページ ----------
// ---------- ②全カテゴリ: 期間 × カテゴリ の推移比較マトリクス ----------
// 「カテゴリ同士を比べながら、時系列の動きを見る」ための表とグラフ。
// 件数系はそのまま合計し、率は分子・分母をそれぞれ合計してから割る(率の平均ではない)。
const CAT_MATRIX_METRICS = {
  refund_amount:  { label: '返金額',       kind: 'money', num: 'refund_amount',  den: null },
  refund_rate:    { label: '返金額率',     kind: 'pct',   num: 'refund_amount',  den: 'sales_amount' },
  sr_count:       { label: 'SR発生件数',   kind: 'int',   num: 'sr_count',       den: null },
  sr_rate:        { label: 'SR率',         kind: 'pct',   num: 'sr_count',       den: 'shipped_count' },
  question_count: { label: '質問数',       kind: 'int',   num: 'question_count', den: null },
  question_rate:  { label: '質問率',       kind: 'pct',   num: 'question_count', den: 'listed_count' },
  inquiry_count:  { label: '問合せ件数',   kind: 'int',   num: 'inquiry_count',  den: null },
  inquiry_rate:   { label: '問合せ率',     kind: 'pct',   num: 'inquiry_count',  den: 'shipped_count' }
};

function renderCategoryMatrix() {
  const wrap = document.getElementById('catMatrixTable');
  if (!wrap) return;
  const granularity = granSel.value;
  const metricKey = (document.getElementById('catMatrixMetric') || {}).value || 'sr_rate';
  const spec = CAT_MATRIX_METRICS[metricKey] || CAT_MATRIX_METRICS.sr_rate;
  const topN = parseInt((document.getElementById('catMatrixTopN') || {}).value || '8', 10);
  const span = parseInt((document.getElementById('catMatrixSpan') || {}).value || '18', 10);

  const granLabel = { week: '週次', month: '月次', quarter: '四半期', half: '半期', all: '通期' }[granularity] || granularity;
  const gl = document.getElementById('catMatrixGranLabel');
  if (gl) gl.textContent = granLabel + ' / ' + spec.label;
  const tt = document.getElementById('catMatrixTableTitle');
  if (tt) tt.textContent = '期間 × カテゴリ 一覧（' + spec.label + '・' + granLabel + '）';

  let periods = availablePeriods(granularity);
  if (span < periods.length) periods = periods.slice(-span);
  const periodIdx = new Map(periods.map((p, i) => [p.key, i]));

  // カテゴリ × 期間 の分子・分母を貯める
  const cats = new Map();  // category -> {num:[], den:[], numTotal, denTotal, ship}
  const blank = () => ({ num: new Array(periods.length).fill(0), den: new Array(periods.length).fill(0), ship: 0 });
  ROWS.forEach(r => {
    const pk = periodKeyFor(r, granularity);
    const i = periodIdx.get(pk.key);
    if (i === undefined) return;
    if (!cats.has(r.category)) cats.set(r.category, blank());
    const o = cats.get(r.category);
    o.num[i] += (r[spec.num] || 0);
    if (spec.den) o.den[i] += (r[spec.den] || 0);
    o.ship += (r.shipped_count || 0);
  });
  if (!cats.size || !periods.length) {
    wrap.innerHTML = '<p class="note">表示できるデータがありません。</p>';
    renderChart('catMatrixChart', { type: 'line', data: { labels: [], datasets: [] }, options: {} });
    return;
  }

  const valueAt = (o, i) => {
    if (spec.den) return o.den[i] ? o.num[i] / o.den[i] : null;
    return o.num[i];
  };
  const totalOf = (o) => {
    const n = o.num.reduce((a, v) => a + v, 0);
    if (!spec.den) return n;
    const d = o.den.reduce((a, v) => a + v, 0);
    return d ? n / d : null;
  };
  const fmtVal = (v) => {
    if (v === null || v === undefined) return '-';
    if (spec.kind === 'pct') return fmtPct(v);
    if (spec.kind === 'money') return fmtYen(v);
    return fmtInt(v);
  };

  // 合計値の大きい順に並べる(率も合計基準)。
  // ただし率の場合、母数が数件しかないカテゴリは「1件で50%」のような極端な値になり
  // 上位を占めてしまうため、母数が MIN_DEN 未満のものは下にまとめる。
  // 閾値は「全カテゴリの母数合計の0.5%」。母数が金額(売上)でも件数(出荷数・出品数)でも
  // 同じ考え方で効くように、絶対値ではなく全体に対する比率で判定する。
  const denTotal = (o) => o.den.reduce((a, v) => a + v, 0);
  let MIN_DEN = 0;
  if (spec.den) {
    let sum = 0;
    cats.forEach(o => { sum += denTotal(o); });
    MIN_DEN = sum * 0.005;
  }
  const ordered = Array.from(cats.entries())
    .map(([name, o]) => ({ name, o, total: totalOf(o), den: spec.den ? denTotal(o) : null }))
    .sort((a, b) => {
      if (spec.den) {
        const am = a.den >= MIN_DEN, bm = b.den >= MIN_DEN;
        if (am !== bm) return am ? -1 : 1;
      }
      return (b.total === null ? -1 : b.total) - (a.total === null ? -1 : a.total);
    });

  // ---- グラフ: 出荷数の多い上位カテゴリを折れ線で重ねる ----
  const chartCats = Array.from(cats.entries())
    .sort((a, b) => b[1].ship - a[1].ship)
    .slice(0, topN)
    .map(([name]) => name);
  const datasets = chartCats.map((name, idx) => {
    const o = cats.get(name);
    const color = PALETTE[idx % PALETTE.length];
    return {
      label: name,
      data: periods.map((p, i) => {
        const v = valueAt(o, i);
        return v === null ? null : (spec.kind === 'pct' ? v * 100 : v);
      }),
      borderColor: color, backgroundColor: color,
      borderWidth: 2, pointRadius: 2, tension: 0.25, spanGaps: true
    };
  });
  renderChart('catMatrixChart', {
    type: 'line',
    data: { labels: periods.map(p => p.label), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (c) => c.dataset.label + ': ' +
              (spec.kind === 'pct' ? (c.parsed.y === null ? '-' : c.parsed.y.toFixed(2) + '%')
                                   : fmtVal(c.parsed.y))
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: spec.label + (spec.kind === 'pct' ? ' (%)' : '') },
          ticks: { callback: (v) => spec.kind === 'pct' ? v + '%' : (spec.kind === 'money' ? moneyTick(v) : v) }
        }
      }
    }
  });

  // ---- 表: 行=カテゴリ / 列=期間 ----
  let maxV = 0;
  ordered.forEach(({ o }) => periods.forEach((p, i) => {
    const v = valueAt(o, i); if (v != null && v > maxV) maxV = v;
  }));
  const bg = (v) => {
    if (v == null || !(maxV > 0) || !(v > 0)) return '#fff';
    return 'rgba(224,101,58,' + (0.05 + 0.45 * Math.min(1, v / maxV)).toFixed(3) + ')';
  };
  const th = 'padding:5px 8px;border:1px solid #e3e5e8;background:#f5f6f8;white-space:nowrap;font-size:11.5px;';
  const td = 'padding:5px 8px;border:1px solid #e3e5e8;text-align:right;white-space:nowrap;font-size:11.5px;';
  let html = '<table style="border-collapse:collapse;"><thead><tr>' +
    '<th style="' + th + 'text-align:left;position:sticky;left:0;z-index:1;">カテゴリ</th>';
  periods.forEach(p => { html += '<th style="' + th + '">' + p.label + '</th>'; });
  html += '<th style="' + th + '">合計</th></tr></thead><tbody>';
  ordered.forEach(({ name, o, total, den }) => {
    const faint = (spec.den && den < MIN_DEN);
    html += '<tr><td style="padding:5px 8px;border:1px solid #e3e5e8;white-space:nowrap;font-weight:600;'
      + 'position:sticky;left:0;background:#fff;z-index:1;font-size:11.5px;'
      + (faint ? 'color:#98a2ad;' : '') + '" title="'
      + (spec.den ? '母数 合計 ' + fmtInt(den) : '') + '">' + name
      + (faint ? ' <span style="font-weight:400;font-size:10px;">(母数少)</span>' : '') + '</td>';
    periods.forEach((p, i) => {
      const v = valueAt(o, i);
      const tip = spec.den ? (fmtInt(o.num[i]) + ' / ' + fmtInt(o.den[i])) : '';
      html += '<td style="' + td + 'background:' + bg(v) + ';" title="' + tip + '">' + fmtVal(v) + '</td>';
    });
    html += '<td style="' + td + 'background:#f5f6f8;font-weight:700;">' + fmtVal(total) + '</td></tr>';
  });
  wrap.innerHTML = html + '</tbody></table>';
}

function renderAllCategoryPage() {
  renderInsights();
  const granularity = granSel.value, periodKey = periodSel.value;
  renderYoyBox('yoyBoxAllCategory', 'yoyTableAllCategory', 'yoyPeriodLabelAllCategory', granularity, periodKey, null);
  renderCategoryMatrix();
  renderCategoryBreakdown();
  renderCategorySrMajorChart();
  renderDetailTable('category');
}

// ---------- ③ カテゴリ詳細(SR改善分析)ページ ----------
// ②から切り出したSR改善分析。カテゴリを選んで絞り込める(未選択・(全カテゴリ)は全件)。
function renderCatDetailPage() {
  const cats = acCatMultiSel ? getMultiSelectValues(acCatMultiSel) : [];
  const isAll = cats.length === 0 || cats.includes(ALL_CAT);
  const catSet = isAll ? null : new Set(cats);
  const label = isAll ? '全カテゴリ'
    : (cats.length === 1 ? cats[0] : cats.join(' + ') + '(合算・' + cats.length + 'カテゴリ)');
  const t = document.getElementById('acDrillTitle');
  if (t) t.textContent = 'SR改善分析 ― ' + label;
  renderImprovementSection('ac', catSet ? (r => catSet.has(r.category)) : null);
}

// ---------- 比較KPIカード(②/④で使用) ----------
function metricCardHtml(title, countVal, rateVal, baseRateVal, isMoney) {
  const rateDiff = (rateVal != null && baseRateVal != null) ? (rateVal - baseRateVal) * 100 : null;
  const cls = rateDiff == null ? 'flat' : (rateDiff > 0 ? 'up' : (rateDiff < 0 ? 'down' : 'flat'));
  const arrow = rateDiff == null ? '' : (rateDiff > 0 ? '▲' : (rateDiff < 0 ? '▼' : '―'));
  const diffHtml = rateDiff == null ? '' :
    ('<div class="kpi-delta ' + cls + '">' + arrow + ' 平均比 ' + (rateDiff >= 0 ? '+' : '') + rateDiff.toFixed(2) + 'pt (平均 ' + fmtPct(baseRateVal) + ')</div>');
  return '<div class="card kpi-card"><div class="kpi-title">' + title + '</div>' +
    '<div class="kpi-row"><span class="kpi-main">' + (isMoney ? fmtYen(countVal) : fmtInt(countVal)) + '</span><span class="kpi-rate">率 ' + fmtPct(rateVal) + '</span></div>' +
    diffHtml + '</div>';
}

function renderComparisonKpis(containerId, current, baseline) {
  const refundCountRate = current.shipped_count ? current.refund_count / current.shipped_count : null;
  const baseRefundCountRate = baseline.shipped_count ? baseline.refund_count / baseline.shipped_count : null;
  const html = [
    metricCardHtml('質問 (出品中・ヤフオク)', current.question_count, current.question_rate, baseline.question_rate, false),
    metricCardHtml('サービスリクエスト発生件数', current.sr_count, current.sr_rate, baseline.sr_rate, false),
    metricCardHtml('返金額', current.refund_amount, current.refund_rate, baseline.refund_rate, true),
    metricCardHtml('返金件数', current.refund_count, refundCountRate, baseRefundCountRate, false),
    metricCardHtml('問合せ (CS_登録 種別=CS)', current.inquiry_count, current.inquiry_rate, baseline.inquiry_rate, false),
    metricCardHtml('最終利益', current.final_profit, current.profit_margin, baseline.profit_margin, true)
  ].join('');
  document.getElementById(containerId).innerHTML = html;
}

// 月次のときは「決算月(7月〜6月)を横軸に、期ごとの系列を重ねる」昨対比較表示にする。
// 例: 7月〜6月の12ヶ月を並べ、20期と21期の件数を棒、率を折れ線で比較できる。
function buildYoyMonthlySeries(rowFilter) {
  const FY_MONTH_ORDER = [];
  for (let i = 0; i < 12; i++) FY_MONTH_ORDER.push(((FY_START_M - 1 + i) % 12) + 1);
  const byFy = new Map();   // fyNum -> { monthNum -> agg }
  ROWS.forEach(r => {
    if (rowFilter && !rowFilter(r)) return;
    const fi = fiscalInfo(r.year_month);
    if (!byFy.has(fi.fyNum)) byFy.set(fi.fyNum, new Map());
    const mm = parseInt(r.year_month.split('-')[1], 10);
    const mp = byFy.get(fi.fyNum);
    if (!mp.has(mm)) mp.set(mm, emptyAgg());
    addInto(mp.get(mm), r);
  });
  const fyNums = Array.from(byFy.keys()).sort((a, b) => a - b);
  return {
    labels: FY_MONTH_ORDER.map(m => m + '月'),
    months: FY_MONTH_ORDER,
    fyNums,
    get: (fy, m) => {
      const a = (byFy.get(fy) || new Map()).get(m);
      return a ? deriveRates(a) : null;
    }
  };
}

function yoyMonthlyConfig(series, barField, lineField, barLabel, lineLabel, leftLabel, isMoney) {
  const barColors = ['#c9dbfa', '#8fb3f0', '#5b8def', '#12203f'];
  const lineColors = ['#f0b7a4', '#e8916f', '#e0653a', '#a83a17'];
  const datasets = [];
  series.fyNums.forEach((fy, i) => {
    datasets.push({
      type: 'bar', label: ordinalSuffix(fy) + ' ' + barLabel, yAxisID: 'y',
      data: series.months.map(m => { const d = series.get(fy, m); return d ? d[barField] : null; }),
      backgroundColor: barColors[i % barColors.length], order: 2
    });
  });
  series.fyNums.forEach((fy, i) => {
    datasets.push({
      type: 'line', label: ordinalSuffix(fy) + ' ' + lineLabel, yAxisID: 'y1',
      data: series.months.map(m => { const d = series.get(fy, m); const v = d ? d[lineField] : null; return v == null ? null : v * 100; }),
      borderColor: lineColors[i % lineColors.length], backgroundColor: lineColors[i % lineColors.length],
      tension: 0.25, spanGaps: true, order: 1
    });
  });
  return {
    type: 'bar',
    data: { labels: series.labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } },
        title: { display: true, text: barLabel + '・' + lineLabel + '(期別の昨対比較)' } },
      scales: {
        y: { position: 'left', beginAtZero: true, title: { display: true, text: leftLabel },
             ticks: { callback: v => isMoney ? (v / 1000) + 'k' : v } },
        y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
              title: { display: true, text: '率(%)' }, ticks: { callback: v => v + '%' } }
      }
    }
  };
}

// 任意の行配列(ROWS / CONDITION_ROWS / PROFIT_VARIANCE_ROWS など)を
// 「決算月(7月〜6月) × 期」の形に集計する汎用版。月次のときだけ使う。
function buildYoyGeneric(rowsArr, fields, rowFilter) {
  const order = [];
  for (let i = 0; i < 12; i++) order.push(((FY_START_M - 1 + i) % 12) + 1);
  const byFy = new Map();
  rowsArr.forEach(r => {
    if (rowFilter && !rowFilter(r)) return;
    const fi = fiscalInfo(r.year_month);
    if (!byFy.has(fi.fyNum)) byFy.set(fi.fyNum, new Map());
    const mm = parseInt(r.year_month.split('-')[1], 10);
    const mp = byFy.get(fi.fyNum);
    if (!mp.has(mm)) mp.set(mm, {});
    sumFieldsInto(mp.get(mm), r, fields);
  });
  return {
    labels: order.map(m => m + '月'), months: order,
    fyNums: Array.from(byFy.keys()).sort((a, b) => a - b),
    get: (fy, m) => (byFy.get(fy) || new Map()).get(m) || null
  };
}

// 昨対の棒(+任意で率の折れ線)グラフ設定を作る
function yoyGenericConfig(series, barField, rateFn, barLabel, lineLabel, leftLabel, isMoney, title) {
  const barColors = ['#c9dbfa', '#8fb3f0', '#5b8def', '#12203f'];
  const lineColors = ['#f0b7a4', '#e8916f', '#e0653a', '#a83a17'];
  const datasets = [];
  series.fyNums.forEach((fy, i) => datasets.push({
    type: 'bar', label: ordinalSuffix(fy) + ' ' + barLabel, yAxisID: 'y', order: 2,
    data: series.months.map(m => { const d = series.get(fy, m); return d ? (d[barField] || 0) : null; }),
    backgroundColor: barColors[i % barColors.length]
  }));
  if (rateFn) {
    series.fyNums.forEach((fy, i) => datasets.push({
      type: 'line', label: ordinalSuffix(fy) + ' ' + lineLabel, yAxisID: 'y1', order: 1,
      data: series.months.map(m => { const d = series.get(fy, m); const v = d ? rateFn(d) : null; return v == null ? null : v * 100; }),
      borderColor: lineColors[i % lineColors.length], backgroundColor: lineColors[i % lineColors.length],
      tension: 0.25, spanGaps: true
    }));
  }
  const scales = {
    y: { position: 'left', beginAtZero: true, title: { display: true, text: leftLabel },
         ticks: { callback: v => isMoney ? (v / 1000) + 'k' : v } }
  };
  if (rateFn) scales.y1 = { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false },
    title: { display: true, text: '率(%)' }, ticks: { callback: v => v + '%' } };
  return {
    type: 'bar', data: { labels: series.labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 9.5 } } },
        title: { display: true, text: (title || barLabel) + '(期別の昨対比較)' } },
      scales
    }
  };
}

// 月次かどうか(=昨対比較表示にするか)
function isYoyMode() { return granSel.value === 'month'; }

function renderComparisonTrendCharts(prefix, rowFilter) {
  const granularity = granSel.value;
  const trendSel = buildTrendAligned(granularity, rowFilter);
  const trendAll = buildTrendAligned(granularity, null);
  const labels = trendSel.map(t => t.label);
  const yoySeries = (granularity === 'month') ? buildYoyMonthlySeries(rowFilter) : null;

  const specs = [
    { key: 'inquiry', bar: 'inquiry_count', line: 'inquiry_rate', barLabel: '問合せ件数', lineLabel: '問合せ率', baseLabel: '平均問合せ率', leftLabel: '件数', money: false },
    { key: 'sr', bar: 'sr_count', line: 'sr_rate', barLabel: 'SR発生件数', lineLabel: 'SR発生率', baseLabel: '平均SR率', leftLabel: '件数', money: false },
    { key: 'refund', bar: 'refund_amount', line: 'refund_rate', barLabel: '返金額', lineLabel: '返金額率', baseLabel: '平均返金額率', leftLabel: '金額(¥)', money: true },
    { key: 'question', bar: 'question_count', line: 'question_rate', barLabel: '質問数', lineLabel: '質問率', baseLabel: '平均質問率', leftLabel: '件数', money: false },
    { key: 'profit', bar: 'final_profit', line: 'profit_margin', barLabel: '最終利益', lineLabel: '利益率', baseLabel: '平均利益率', leftLabel: '金額(¥)', money: true },
    { key: 'junk', bar: 'junk_listed_count', line: 'junk_listed_rate', barLabel: 'ジャンク出品件数', lineLabel: 'ジャンク出品率', baseLabel: '平均ジャンク出品率', leftLabel: '件数', money: false }
  ];

  specs.forEach(s => {
    if (yoySeries && yoySeries.fyNums.length > 1) {
      // 月次では期をまたいだ同月比較(昨対)にする
      renderChart(prefix + '_' + s.key, yoyMonthlyConfig(
        yoySeries, s.bar, s.line, s.barLabel, s.lineLabel, s.leftLabel, s.money));
    } else {
      renderChart(prefix + '_' + s.key, dualAxisWithBaselineConfig(
        labels, trendSel.map(t => t[s.bar]), trendSel.map(t => t[s.line]), trendAll.map(t => t[s.line]),
        s.barLabel, s.lineLabel, s.baseLabel, s.leftLabel, s.money
      ));
    }
  });
}

// ---------- 生成: ②拠点別ページ(1拠点ドリルダウン) ----------
function renderLocationPage() {
  if (!locations.length) return;
  const loc = locSel.value && locSel.value !== '__ALL__' ? locSel.value : locations[0];
  if (locSel.value !== loc) locSel.value = loc;
  document.getElementById('locationDrillTitle').textContent = '拠点: ' + loc + '(全社平均と比較)';

  const granularity = granSel.value, periodKey = periodSel.value;
  const current = computeAgg(granularity, periodKey, r => r.location === loc);
  const baseline = computeAgg(granularity, periodKey, null);
  renderComparisonKpis('locDrillKpiGrid', current, baseline);
  renderYoyBox('yoyBoxLocation', 'yoyTableLocation', 'yoyPeriodLabelLocation', granularity, periodKey, r => r.location === loc);

  const locInsightBox = document.getElementById('locDrillInsightBox');
  const byLoc = INSIGHTS.by_location || {};
  locInsightBox.style.display = '';
  document.getElementById('locDrillInsightTitle').textContent = loc + ' の所見';
  {
    const dyn = buildDynamicPeriodInsight(r => r.location === loc);
    const memo = byLoc[loc] ? ('\n\n(参考)手動メモ: ' + byLoc[loc]) : '';
    document.getElementById('locDrillInsightText').textContent = dyn + memo;
  }

  renderComparisonTrendCharts('locTrend', r => r.location === loc);

  let data = buildBreakdown(granularity, 'category', periodKey, r => r.location === loc);
  data.sort((a, b) => (b.inquiry_count + b.sr_count + b.question_count) - (a.inquiry_count + a.sr_count + a.question_count));
  document.getElementById('tableTitleLocationDrill').textContent = loc + 'のカテゴリ別内訳 ' + currentPeriodLabel();
  renderTableInto('detailTableLocationDrill', data, 'カテゴリ', 12);
}

// ---------- 生成: ④カテゴリ別ページ(複数カテゴリ選択・合算対応) ----------
// E項目: カテゴリを複数選択できるようにし、選択した複数カテゴリの合算値を表示する。
// rowFilterは「選択したカテゴリのいずれかに一致」に変更するだけで、既存の集計関数
// (computeAgg/buildBreakdown/buildTrendAligned等)はそのまま再利用できる
// (これらはrowFilterの内容を問わないため)。
function renderCategoryPage() {
  if (!categories.length || !catMultiSel) return;
  let selectedCats = getMultiSelectValues(catMultiSel);
  if (!selectedCats.length) {
    selectedCats = [firstRealCategory || categories[0]];
    const opt = Array.from(catMultiSel.options).find(o => o.value === selectedCats[0]);
    if (opt) opt.selected = true;
  }
  const catSet = new Set(selectedCats);
  const rowFilter = r => catSet.has(r.category);
  const label = selectedCats.length >= categories.length ? '全カテゴリ'
    : (selectedCats.length === 1 ? selectedCats[0] : selectedCats.join(' + ') + '(合算・' + selectedCats.length + 'カテゴリ)');
  document.getElementById('categoryDrillTitle').textContent = 'カテゴリ: ' + label + '(全カテゴリ平均と比較)';

  const granularity = granSel.value, periodKey = periodSel.value;
  const current = computeAgg(granularity, periodKey, rowFilter);
  const baseline = computeAgg(granularity, periodKey, null);
  renderComparisonKpis('catDrillKpiGrid', current, baseline);
  renderYoyBox('yoyBoxCategory', 'yoyTableCategory', 'yoyPeriodLabelCategory', granularity, periodKey, rowFilter);

  renderComparisonTrendCharts('catTrend', rowFilter);

  document.getElementById('catDrillCausePivotPeriodLabel').textContent = currentPeriodLabel();
  const causeRows = CAUSE_ROWS.filter(r => catSet.has(r.category) && (periodKey === '__ALL__' || periodKeyFor(r, granularity).key === periodKey));
  renderCausePivotSection('catDrill', causeRows);

  const insightBox = document.getElementById('catDrillInsightBox');
  const byCat = INSIGHTS.by_category || {};
  insightBox.style.display = '';
  document.getElementById('catDrillInsightTitle').textContent = label + ' の所見';
  {
    const dyn = buildDynamicPeriodInsight(rowFilter);
    const memo = selectedCats.length === 1 && byCat[selectedCats[0]] ? ('\n\n(参考)手動メモ: ' + byCat[selectedCats[0]]) : '';
    document.getElementById('catDrillInsightText').textContent = dyn + memo;
  }

  let data = buildBreakdown(granularity, 'location', periodKey, rowFilter);
  data.sort((a, b) => (b.inquiry_count + b.sr_count + b.question_count) - (a.inquiry_count + a.sr_count + a.question_count));
  document.getElementById('tableTitleCategoryDrill').textContent = label + 'の拠点別内訳 ' + currentPeriodLabel();
  renderTableInto('detailTableCategoryDrill', data, '拠点', 20);
}

// ---------- 生成: ⑤拠点×カテゴリページ(拠点・カテゴリの両方を選択した組合せ) ----------
function renderLocCatPage() {
  if (!locations.length || !categories.length || !locCatMultiSel) return;
  // D項目: 「全拠点」(__ALL__)が選ばれている場合は拠点で絞り込まず全拠点合算にする。
  // 未選択(空)の場合のみ、従来どおり実在の最初の拠点にフォールバックする。
  const loc = locSel.value ? locSel.value : firstRealLocation;
  if (locSel.value !== loc) locSel.value = loc;
  const isAllLoc = (loc === ALL_LOC);
  const locLabel = isAllLoc ? '全拠点' : loc;

  let selectedCats = getMultiSelectValues(locCatMultiSel);
  if (!selectedCats.length) {
    selectedCats = [firstRealCategory || categories[0]];
    const opt = Array.from(locCatMultiSel.options).find(o => o.value === selectedCats[0]);
    if (opt) opt.selected = true;
  }
  const catSet = new Set(selectedCats);
  // 全カテゴリが選ばれている場合は、38個の名前を並べず「全カテゴリ」とだけ表示する
  const isAllCats = selectedCats.length >= categories.length;
  const catLabel = isAllCats ? '全カテゴリ'
    : (selectedCats.length === 1 ? selectedCats[0] : selectedCats.join(' + ') + '(合算・' + selectedCats.length + 'カテゴリ)');
  document.getElementById('locCatDrillTitle').textContent = (isAllLoc ? '全拠点' : '拠点: ' + loc) + '  ×  カテゴリ: ' + catLabel;

  const granularity = granSel.value, periodKey = periodSel.value;
  const rowFilter = r => (isAllLoc ? true : r.location === loc) && catSet.has(r.category);
  const current = computeAgg(granularity, periodKey, rowFilter);
  const baseline = computeAgg(granularity, periodKey, null);
  renderComparisonKpis('locCatKpiGrid', current, baseline);

  renderComparisonTrendCharts('lcTrend', rowFilter);

  document.getElementById('locCatMajorPeriodLabel').textContent = currentPeriodLabel();
  const { majors, map: majorMap } = buildSrMajorByDim(null, granularity, periodKey, rowFilter);
  if (!majors.length) {
    renderChart('locCatMajorChart', { type: 'doughnut', data: { labels: ['データなし'], datasets: [{ data: [1], backgroundColor: ['#e3e5e8'] }] }, options: { plugins: { legend: { display: false } } } });
  } else {
    const counts = majors.map(m => (majorMap.get('全社') || {})[m] || 0);
    renderChart('locCatMajorChart', donutMajorConfig(majors, counts));
  }

  document.getElementById('locCatCondPeriodLabel').textContent = currentPeriodLabel();
  const condFields = ['count', 'sales_amount', 'gross_profit'];
  let condData = buildDimBreakdownGeneric(CONDITION_ROWS, 'condition', condFields, granularity, periodKey, rowFilter);
  condData.sort((a, b) => conditionSortKey(a.name) - conditionSortKey(b.name));
  const condRate = condData.map(d => d.sales_amount ? d.gross_profit / d.sales_amount : null);
  renderChart('locCatConditionChart', dualAxisConfig(condData.map(d => d.name), condData.map(d => d.count), condRate, '件数', '粗利率', '件数', false));

  let pbData = buildDimBreakdownGeneric(PRICE_BAND_ROWS, 'price_band', condFields, granularity, periodKey, rowFilter);
  pbData.sort((a, b) => priceBandSortKey(a.name) - priceBandSortKey(b.name));
  const pbRate = pbData.map(d => d.sales_amount ? d.gross_profit / d.sales_amount : null);
  renderChart('locCatPriceBandChart', dualAxisConfig(pbData.map(d => d.name), pbData.map(d => d.count), pbRate, '件数', '粗利率', '件数', false));

  document.getElementById('locCatPvPeriodLabel').textContent = currentPeriodLabel();
  const pvAgg = computeAggGeneric(PROFIT_VARIANCE_ROWS, PV_FIELDS, granularity, periodKey, rowFilter);
  document.getElementById('locCatPvKpiGrid').innerHTML = [
    metricSimpleCardHtml('件数(合計)', fmtInt(pvAgg.count)),
    metricSimpleCardHtml('上振れ 件数・額', fmtInt(pvAgg.upside_count) + '件<br>' + fmtYen(pvAgg.upside_amount)),
    metricSimpleCardHtml('下振れ 件数・額', fmtInt(pvAgg.downside_count) + '件<br>' + fmtYen(pvAgg.downside_amount)),
    metricSimpleCardHtml('差異合計額', fmtYen(pvAgg.variance_sum))
  ].join('');

  document.getElementById('locCatTableTitle').textContent = locLabel + ' × ' + catLabel + ' の期間別詳細 ' + currentPeriodLabel();
  const trendData = buildTrendAligned(granularity, rowFilter).map(t => Object.assign({ name: t.label }, t));
  renderTableInto('detailTableLocCat', trendData, '期間', 20);

  // C項目: ①全拠点ページから移設したジャンク出品比率ヒートマップ(拠点×カテゴリ全体を表示)
  renderJunkHeatmap();
}

// ---------- ページ切替 ----------
function renderPageContent() {
  if (currentPage === 'overall') renderOverallPage();
  else if (currentPage === 'detail') renderDetailPage();
  else if (currentPage === 'allcategory') renderAllCategoryPage();
  else if (currentPage === 'catdetail') renderCatDetailPage();
  else if (currentPage === 'condition') renderConditionPage();
  else if (currentPage === 'priceband') renderPriceBandPage();
  else if (currentPage === 'deficit') renderDeficitPage();
  else if (currentPage === 'customer') renderCustomerPage();
  else renderProfitVariancePage();
}

const ALL_PAGES = ['overall', 'allcategory', 'catdetail', 'detail', 'condition', 'priceband', 'profitvariance', 'deficit', 'customer'];
const PAGE_NAV_MAP = {
  overall: 'navBtnOverall', allcategory: 'navBtnAllCategory', catdetail: 'navBtnCatDetail',
  detail: 'navBtnDetail', condition: 'navBtnCondition', priceband: 'navBtnPriceBand',
  profitvariance: 'navBtnProfitVariance', deficit: 'navBtnDeficit', customer: 'navBtnCustomer'
};

// ⑧赤字ページを一度でも表示したか(初回表示時のみ拠点を「全拠点」に初期化するためのフラグ)
let deficitLocInitialized = false;

function setPage(page) {
  currentPage = page;
  Object.keys(PAGE_NAV_MAP).forEach(p => document.getElementById(PAGE_NAV_MAP[p]).classList.toggle('active', p === page));
  ALL_PAGES.forEach(p => document.getElementById('page-' + p).classList.toggle('active', p === page));
  // E項目: ⑧赤字ページでも拠点セレクタを使えるようにする
  ctlLoc.classList.toggle('visible', page === 'detail' || page === 'deficit' || page === 'condition' ||
    page === 'priceband' || page === 'profitvariance');
  // ⑨顧客セグメントページは全期間累計のため、期間・拠点の絞り込みUIは非表示にする
  const globalControls = document.getElementById('globalControls');
  if (globalControls) globalControls.style.display = (page === 'customer') ? 'none' : '';
  // ②拠点別は1拠点ドリルダウン専用なので、従来どおり「全拠点」が選ばれていたら実在の拠点に戻す。
  // ⑤拠点×カテゴリは「全拠点」をそのまま尊重し、値が空のときだけ実在の拠点にフォールバックする。
  if (page === 'detail' && !locSel.value && locations.length) locSel.value = ALL_LOC;
  // ⑧赤字ページの初期値は「全拠点」。一度表示した後はユーザーの選択をそのまま保持する。
  if (page === 'deficit' && !deficitLocInitialized) { locSel.value = ALL_LOC; deficitLocInitialized = true; }
  renderPageContent();
}

document.getElementById('navBtnOverall').addEventListener('click', () => setPage('overall'));
document.getElementById('navBtnDetail').addEventListener('click', () => setPage('detail'));
document.getElementById('navBtnAllCategory').addEventListener('click', () => setPage('allcategory'));
document.getElementById('navBtnPriceBand').addEventListener('click', () => setPage('priceband'));
document.getElementById('navBtnCondition').addEventListener('click', () => setPage('condition'));
document.getElementById('navBtnProfitVariance').addEventListener('click', () => setPage('profitvariance'));

document.getElementById('navBtnDeficit').addEventListener('click', () => setPage('deficit'));
document.getElementById('navBtnCustomer').addEventListener('click', () => setPage('customer'));
['custMinShipped', 'custMinSr', 'custType'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('change', () => { if (currentPage === 'customer') renderCustomerPage(); });
});
document.getElementById('segBtnSrRepeater').addEventListener('click', () => setCustomerSegment('sr_repeater'));
document.getElementById('segBtnLoyalCustomer').addEventListener('click', () => setCustomerSegment('loyal_customer'));

// 初期表示は「月次 × データがある最新の月」に固定する。
// ページを切り替えても、ユーザーが粒度・期間を変えるまではこの状態を保つ。
function applyDefaultPeriod() {
  granSel.value = 'month';
  syncWeekStartVisibility();
  const periods = availablePeriods('month');
  populatePeriodSelect();
  if (periods.length) periodSel.value = periods[periods.length - 1].key;
}

function renderAll() {
  applyDefaultPeriod();
  renderPageContent();
}

// 週の起点セレクタは「週次」を選んでいるときだけ表示する
function syncWeekStartVisibility() {
  if (ctlWeekStart) ctlWeekStart.style.display = (granSel.value === 'week') ? 'flex' : 'none';
}
syncWeekStartVisibility();

granSel.addEventListener('change', () => { syncWeekStartVisibility(); populatePeriodSelect(); renderPageContent(); });
if (weekStartSel) {
  weekStartSel.addEventListener('change', () => {
    WEEK_START_DOW = parseInt(weekStartSel.value, 10);
    _weekBucketCache.clear();
    populatePeriodSelect();
    renderPageContent();
  });
}
periodSel.addEventListener('change', () => { renderPageContent(); });
locSel.addEventListener('change', () => {
  if (['detail', 'deficit', 'condition', 'priceband', 'profitvariance'].includes(currentPage)) renderPageContent();
});
{
  const pvSideSel = document.getElementById('pvSide');
  if (pvSideSel) pvSideSel.addEventListener('change', () => { if (currentPage === 'profitvariance') renderPageContent(); });
}
if (detailCatMultiSel) detailCatMultiSel.addEventListener('change', () => { if (currentPage === 'detail') renderPageContent(); });

if (deficitCatMultiSel) deficitCatMultiSel.addEventListener('change', () => { if (currentPage === 'deficit') renderPageContent(); });
{
  // ②全カテゴリ: 期間×カテゴリ マトリクスのセレクタ(この表だけ描き直せばよい)
  ['catMatrixMetric', 'catMatrixTopN', 'catMatrixSpan'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', () => { if (currentPage === 'allcategory') renderCategoryMatrix(); });
  });
  const dm = document.getElementById('deficitMode');
  if (dm) dm.addEventListener('change', () => { if (currentPage === 'deficit') renderPageContent(); });
  // クロス集計の指標切替は、その表だけを描き直せばよい
  const dx = document.getElementById('deficitCrossMetric');
  if (dx) dx.addEventListener('change', () => { if (currentPage === 'deficit') renderDeficitCrossTab(); });
}

document.getElementById('subHeader').textContent =
  DATA.fiscal_year_label + '(' + DATA.fiscal_year_start + '～) の問合せ・SR・返金・質問データ　最終更新: ' + (DATA.generated_at || '').slice(0,16).replace('T',' ')
  + '　[ビルド __BUILD_STAMP__ / 商品明細' + (typeof ITEM_DETAIL_ROWS === 'undefined' ? 0 : ITEM_DETAIL_ROWS.length).toLocaleString() + '件]';
document.getElementById('dataThrough').textContent = DATA.data_through || '-';

renderAll();
</script>
</body>
</html>
'''

html = html.replace('__DATA_JSON_GZ_B64__', data_json_gz_b64)

# 「どのビルドを見ているか」をヘッダーに出す。表示が古いまま更新されていないときの切り分け用。
import datetime as _dt
html = html.replace('__BUILD_STAMP__', _dt.datetime.now().strftime('%m/%d %H:%M'))

out_path = 'cs_sr_dashboard.html'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(html)

out_bytes = len(html.encode('utf-8'))
print('written:', out_path, len(html), 'chars', out_bytes, 'bytes',
      f'({out_bytes/1024/1024:.2f} MB)')
