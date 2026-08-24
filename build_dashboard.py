import json
import gzip
import base64

with open('cs_sr_dashboard_data.json', encoding='utf-8') as f:
    data = json.load(f)

# gzip圧縮対応: JSON文字列をコンパクト(区切り文字を最小化)にしてからgzip圧縮し、base64で
# テキスト化してHTMLに埋め込む。Coworkアーティファクトの10MBアップロード上限に収めるため
# (フルデータは~19-24MB程度あるが、テキストなのでgzipで10倍以上圧縮できる)。
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
  <button class="page-nav-btn" id="navBtnLocation" type="button">② 拠点別</button>
  <button class="page-nav-btn" id="navBtnAllCategory" type="button">③ 全カテゴリ</button>
  <button class="page-nav-btn" id="navBtnCategory" type="button">④ カテゴリ別</button>
  <button class="page-nav-btn" id="navBtnLocCat" type="button">⑤ 拠点×カテゴリ</button>
  <button class="page-nav-btn" id="navBtnCondition" type="button">⑥ コンディション・価格帯</button>
  <button class="page-nav-btn" id="navBtnProfitVariance" type="button">⑦ 粗利差異</button>
  <button class="page-nav-btn" id="navBtnDeficit" type="button">⑧ 赤字(原価割れ)</button>
  <button class="page-nav-btn" id="navBtnCustomer" type="button">⑨ SRリピーター・ロイヤルカスタマー</button>
</div>

<div class="controls" id="globalControls">
  <div class="ctl">
    <label>期間粒度</label>
    <select id="granularity">
      <option value="week">週次</option>
      <option value="month">月次</option>
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
    <label>拠点フィルタ(②拠点別・⑤拠点×カテゴリ・⑧赤字ページで使用。⑤⑧は「全拠点」も選べます)</label>
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
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">返金額</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiRefundAmount">-</span><span class="kpi-rate" id="kpiRefundRate">率 -</span></div>
      <div class="kpi-delta" id="kpiRefundDelta"></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-title">返金件数</div>
      <div class="kpi-row"><span class="kpi-main" id="kpiRefundCount">-</span><span class="kpi-rate" id="kpiRefundCountRate">1件あたり -</span></div>
      <div class="kpi-delta" id="kpiRefundCountDelta"></div>
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

</div>

<!-- ============ ② 拠点別ページ(1拠点ドリルダウン) ============ -->
<div class="page-section" id="page-location">

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

<!-- ============ ③ 全カテゴリページ ============ -->
<div class="page-section" id="page-allcategory">

  <h2>カテゴリ別 所見</h2>
  <div class="category-insights" id="categoryInsights"></div>

  <div class="card table-section" id="yoyBoxAllCategory" style="display:none;">
    <h3>前年同期比(YoY・全カテゴリ合計) <span id="yoyPeriodLabelAllCategory" class="badge"></span></h3>
    <div id="yoyTableAllCategory"></div>
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

<!-- ============ ④ カテゴリ別ページ(1カテゴリドリルダウン) ============ -->
<div class="page-section" id="page-category">

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

<!-- ============ ⑤ 拠点×カテゴリ ページ(2軸クロスフィルタ) ============ -->
<div class="page-section" id="page-loccat">

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

  <p class="note">拠点セレクタ・カテゴリ複数選択で組合せを指定すると、その組合せの問合せ/SR/返金/質問/出荷/出品/ジャンク・コンディション・価格帯・粗利差異を集約して表示します(既存の②拠点別・④カテゴリ別ページと同じ集計データを両方の軸で絞り込んだものです。ETL側で新しい集計を追加しているわけではありません)。カテゴリを複数選択した場合は選択カテゴリの合算値になります。</p>

</div>

<!-- ============ ⑥ コンディション・価格帯別分析ページ ============ -->
<div class="page-section" id="page-condition">

  <h2>コンディションランク別 内訳(件数・売上・粗利率) <span id="conditionPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="conditionChart"></canvas></div>

  <h2>コンディションランク別 出荷件数の推移</h2>
  <div class="card major-chart-card">
    <canvas id="conditionTrendChart"></canvas>
  </div>

  <h2>価格帯別(落札価格10万円単位) 内訳 <span id="priceBandPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="priceBandChart"></canvas></div>

  <div class="card table-section">
    <h3>コンディションランク別 詳細</h3>
    <div id="detailTableCondition" class="detail-table"></div>
  </div>

  <div class="card table-section">
    <h3>価格帯別 詳細</h3>
    <div id="detailTablePriceBand" class="detail-table"></div>
  </div>

  <p class="note">対象: 商品_出荷(JPONベース)の出荷済み商品(拠点はCSセンター・鳥取・北関東を除外)。コンディションランクは「状態」列(ジャンク(J)/程度不良(D)/一般中古(C)/程度良好(B)/美品(A)/未使用品(S)/新品(N))、価格帯は落札価格を10万円単位で区切ったものです。</p>

</div>

<!-- ============ ⑦ 粗利差異分析ページ ============ -->
<div class="page-section" id="page-profitvariance">

  <div class="kpi-grid" id="pvKpiGrid"></div>

  <h2>推移(全社合計)</h2>
  <div class="card chart-card" style="height:300px;"><canvas id="pvTrendChart"></canvas></div>

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
      <label>カテゴリ(複数選択可。Ctrl/Cmd(Macは⌘)+クリックで複数選択すると合算表示します。未選択=全カテゴリ)</label>
      <select id="deficitCatMultiSelect" multiple size="6" style="min-width:240px;"></select>
    </div>
  </div>

  <div class="kpi-grid" id="deficitKpiGrid"></div>

  <!-- F項目: 「カテゴリ別 赤字比較」を「推移」より前に表示する -->
  <h2>カテゴリ別 赤字比較 <span id="deficitCatPeriodLabel" class="badge"></span></h2>
  <div class="card chart-card" style="height:300px;"><canvas id="deficitCatChart"></canvas></div>
  <div class="card table-section">
    <h3>カテゴリ別 詳細</h3>
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

  <p class="note" id="deficitNote">赤字(原価割れ)の定義: 実質粗利(落札価格-買取価格/1.1-発送送料) が0未満の商品。加えて、その商品についてCS_返金に「返品」列が「あり」の行があれば、その商品の返送料(ヤフオク配送料)も赤字額に加算しています(total_deficit=正の値ほど赤字が大きいことを表します)。対象は商品_出荷(JPONベース)の出荷済み商品(拠点はCSセンター・鳥取・北関東を除外)。発送送料は「ヤフオク配送料」列を基準に、数値ならそのまま採用、"らくらく家財便"の場合は受注_通常_出荷の実際の送料を突合して採用、"直引"の場合は0円としています。<br>
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
  ※率はいずれも「期間・絞り込み内で、分子と分母をそれぞれ合算した後に算出」しています(月次の率を単純平均していません)。<br>
  ※対象: 問合せ=CS_登録の種別「CS」(落札後の商品問合せ)、SR=CS_登録の種別「SR」、返金額=CS_返金の返金日基準、ヤフオク質問=質問_登録(出品中の質問対応、落札後の問合せとは別データ)。拠点はいずれもCSセンター・鳥取・北関東を除外集計。<br>
  ※粗利=落札価格-買取価格/1.1-ヤフオク配送料/1.1。最終利益=粗利-返金額(-返品ありの場合はさらに返送料=ヤフオク配送料そのもの)。<br>
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
// ⑨ SRリピーター・ロイヤルカスタマー分析。1行=1顧客(名寄せ済みクラスタ)で、
// segment 列が 'sr_repeater' / 'loyal_customer' のどちらのセグメントかを表す。
// 匿名ラベルと数値指標のみで、氏名・住所・電話番号等の個人情報は含まれない。
const CUSTOMER_SEGMENT_ROWS = rehydrateRows(DATA.customer_segment_rows);
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

function periodKeyFor(row, granularity) {
  const fi = fiscalInfo(row.year_month);
  if (granularity === 'week') {
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

// E項目: ⑧赤字(原価割れ)ページ用の複数選択カテゴリセレクタ。
// ④・⑤ページとは異なり、初期状態では何も選択しない(=全カテゴリ・絞り込みなし)。
// 選択肢は赤字データ(DEFICIT_ROWS)に実在するカテゴリのみとする。
const deficitCategories = Array.from(new Set(DEFICIT_ROWS.map(r => r.category))).sort();
const deficitCatMultiSel = document.getElementById('deficitCatMultiSelect');
if (deficitCatMultiSel) {
  deficitCategories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; deficitCatMultiSel.appendChild(o); });
}

// F項目: ⑦ページのカテゴリ別詳細粗利指標(CATEGORY_PROFIT_DETAIL_ROWS)推移グラフ用の
// 複数選択カテゴリセレクタ。
const cpdCategories = Array.from(new Set(CATEGORY_PROFIT_DETAIL_ROWS.map(r => r.category))).sort();
const cpdCatMultiSel = document.getElementById('cpdCatMultiSelect');
if (cpdCatMultiSel) {
  cpdCategories.forEach(c => { const o = document.createElement('option'); o.value = c; o.textContent = c; cpdCatMultiSel.appendChild(o); });
  const firstCpdCat = cpdCategories.find(c => c !== '不明' && c !== 'default') || cpdCategories[0];
  if (firstCpdCat) {
    const opt = Array.from(cpdCatMultiSel.options).find(o => o.value === firstCpdCat);
    if (opt) opt.selected = true;
  }
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
  ROWS.forEach(r => { const pk = periodKeyFor(r, granularity); map.set(pk.key, pk); });
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
    '<th style="padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">今期</th>' +
    '<th style="padding:6px 10px;border:1px solid #e3e5e8;background:#f5f6f8;">前年同期</th>' +
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
      '<td style="padding:6px 10px;border:1px solid #e3e5e8;text-align:right;">' + fmt(cv) + '</td>' +
      '<td style="padding:6px 10px;border:1px solid #e3e5e8;text-align:right;">' + fmt(pv) + '</td>' +
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
      catContainer.innerHTML = cats.map(k => {
        const dyn = buildDynamicPeriodInsight(r => r.category === k);
        const memo = byCat[k] ? ('<br>(参考)手動メモ: ' + byCat[k]) : '';
        return '<div class="category-insight-card"><h4>' + k + '</h4><div>' + dyn + memo + '</div></div>';
      }).join('');
    }
  }
}

function renderOverallTrendCharts() {
  const granularity = granSel.value;
  const trend = buildTrendAligned(granularity, null);
  const labels = trend.map(t => t.label);

  renderChart('trend_inquiry', dualAxisConfig(labels, trend.map(t => t.inquiry_count), trend.map(t => t.inquiry_rate), '問合せ件数', '問合せ率', '件数', false));
  renderChart('trend_sr', dualAxisConfig(labels, trend.map(t => t.sr_count), trend.map(t => t.sr_rate), 'SR発生件数', 'SR発生率', '件数', false));
  renderChart('trend_refund', dualAxisConfig(labels, trend.map(t => t.refund_amount), trend.map(t => t.refund_rate), '返金額', '返金額率', '金額(¥)', true));
  renderChart('trend_question', dualAxisConfig(labels, trend.map(t => t.question_count), trend.map(t => t.question_rate), '質問数', '質問率', '件数', false));
  renderChart('trend_profit', dualAxisConfig(labels, trend.map(t => t.final_profit), trend.map(t => t.profit_margin), '最終利益', '利益率', '金額(¥)', true));
  renderChart('trend_junk', dualAxisConfig(labels, trend.map(t => t.junk_listed_count), trend.map(t => t.junk_listed_rate), 'ジャンク出品件数', 'ジャンク出品率', '件数', false));
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
const DETAIL_COLUMNS = (nameLabel) => [
  { name: nameLabel },
  { name: '問合せ件数', formatter: c => fmtInt(c) },
  { name: '問合せ率', formatter: c => c < 0 ? '-' : fmtPct(c) },
  { name: 'SR件数', formatter: c => fmtInt(c) },
  { name: 'SR率', formatter: c => c < 0 ? '-' : fmtPct(c) },
  { name: '返金額(円)', formatter: c => fmtYen(c) },
  { name: '返金額率', formatter: c => c < 0 ? '-' : fmtPct(c) },
  { name: '返金件数', formatter: c => fmtInt(c) },
  { name: '返金1件あたり(円)', formatter: c => c < 0 ? '-' : fmtYen(c) },
  { name: '質問数', formatter: c => fmtInt(c) },
  { name: '質問率', formatter: c => c < 0 ? '-' : fmtPct(c) },
  { name: 'ジャンク出品件数', formatter: c => fmtInt(c) },
  { name: 'ジャンク出品率', formatter: c => c < 0 ? '-' : fmtPct(c) },
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

function renderConditionPage() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('conditionPeriodLabel').textContent = currentPeriodLabel();
  document.getElementById('priceBandPeriodLabel').textContent = currentPeriodLabel();

  const condFields = ['count', 'sales_amount', 'gross_profit'];
  let condData = buildDimBreakdownGeneric(CONDITION_ROWS, 'condition', condFields, granularity, periodKey, null);
  condData.sort((a, b) => conditionSortKey(a.name) - conditionSortKey(b.name));
  const condRate = condData.map(d => d.sales_amount ? d.gross_profit / d.sales_amount : null);
  renderChart('conditionChart', dualAxisConfig(condData.map(d => d.name), condData.map(d => d.count), condRate, '件数', '粗利率', '件数', false));

  const condTrend = buildConditionTrendMap(granularity);
  renderChart('conditionTrendChart', stackedMajorConfig(condTrend.labels, condTrend.majors, condTrend.map));

  let pbData = buildDimBreakdownGeneric(PRICE_BAND_ROWS, 'price_band', condFields, granularity, periodKey, null);
  pbData.sort((a, b) => priceBandSortKey(a.name) - priceBandSortKey(b.name));
  const pbRate = pbData.map(d => d.sales_amount ? d.gross_profit / d.sales_amount : null);
  renderChart('priceBandChart', dualAxisConfig(pbData.map(d => d.name), pbData.map(d => d.count), pbRate, '件数', '粗利率', '件数', false));

  renderSimpleTableInto('detailTableCondition', [
    { name: 'コンディション' }, { name: '件数', formatter: c => fmtInt(c) }, { name: '売上金額(円)', formatter: c => fmtYen(c) },
    { name: '粗利(円)', formatter: c => fmtYen(c) }, { name: '粗利率', formatter: c => fmtPct(c) }
  ], condData.map(d => [d.name, d.count, d.sales_amount, d.gross_profit, d.sales_amount ? d.gross_profit / d.sales_amount : null]), 10);

  renderSimpleTableInto('detailTablePriceBand', [
    { name: '価格帯' }, { name: '件数', formatter: c => fmtInt(c) }, { name: '売上金額(円)', formatter: c => fmtYen(c) },
    { name: '粗利(円)', formatter: c => fmtYen(c) }, { name: '粗利率', formatter: c => fmtPct(c) }
  ], pbData.map(d => [d.name, d.count, d.sales_amount, d.gross_profit, d.sales_amount ? d.gross_profit / d.sales_amount : null]), 15);
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

  renderSimpleTableInto('detailTableCategoryProfitDetail', [
    { name: 'カテゴリ' },
    { name: '数量', formatter: c => fmtInt(c) },
    { name: '仕入額(円)', formatter: c => fmtYen(c) },
    { name: '売上額(円)', formatter: c => fmtYen(c) },
    { name: '粗利額(円)', formatter: c => fmtYen(c) },
    { name: '粗利差異(円)', formatter: c => fmtYen(c) },
    { name: '平均リード(日)', formatter: c => (c === null || c === undefined) ? '-' : c.toFixed(1) },
    { name: '粗利率', formatter: c => (c === null || c === undefined) ? '-' : fmtPct(c) },
    { name: '販売単価(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) },
    { name: '粗利単価(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) }
  ], data.map(d => [
    d.name, d.count, d.cost_amount, d.sales_amount, d.gross_profit, d.variance_amount,
    d.avg_lead_days, d.margin_rate, d.avg_sale_price, d.avg_profit_price
  ]), 20);
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
  if (!cpdCatMultiSel) return;
  const granularity = granSel.value;
  document.getElementById('cpdTrendPeriodLabel').textContent = currentPeriodLabel();
  let selectedCats = getMultiSelectValues(cpdCatMultiSel);
  if (!selectedCats.length) {
    const firstCpdCat = cpdCategories.find(c => c !== '不明' && c !== 'default') || cpdCategories[0];
    if (firstCpdCat) {
      selectedCats = [firstCpdCat];
      const opt = Array.from(cpdCatMultiSel.options).find(o => o.value === firstCpdCat);
      if (opt) opt.selected = true;
    }
  }
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

function renderProfitVariancePage() {
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

function DEFICIT_COLUMNS(nameLabel) {
  return [
    { name: nameLabel },
    { name: '赤字商品数', formatter: c => fmtInt(c) },
    { name: '赤字額合計(円)', formatter: c => fmtYen(c) },
    { name: '1品あたり赤字額(円)', formatter: c => (c === null || c === undefined) ? '-' : fmtYen(c) },
    { name: '発送送料合計(円)', formatter: c => fmtYen(c) },
    { name: 'SR返品送料合計(円)', formatter: c => fmtYen(c) }
  ];
}
function deficitRow(d) {
  return [d.name, d.count, d.total_deficit, d.avg_deficit_per_item, d.shipping_fee_total, d.return_shipping_total];
}

// E項目: ⑧赤字ページの絞り込み条件(拠点・カテゴリ)を1か所で組み立てる。
//   拠点  : locFilter が '__ALL__'(全拠点)なら拠点で絞らない。それ以外はその1拠点。
//   カテゴリ: deficitCatMultiSelect が未選択なら全カテゴリ。選択があればその複数カテゴリの合算。
// ページ内の全セクション(KPI/推移/カテゴリ別/仕入れ方法別)がこの同じ条件を使う。
function deficitFilterState() {
  const loc = locSel.value || ALL_LOC;
  const isAllLoc = (loc === ALL_LOC);
  const selectedCats = deficitCatMultiSel ? getMultiSelectValues(deficitCatMultiSel) : [];
  const isAllCats = selectedCats.length === 0;
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
  data.sort((a, b) => b.total_deficit - a.total_deficit);
  renderChart('deficitCatChart', dualAxisMoneyRightConfig(
    data.map(d => d.name), data.map(d => d.count), data.map(d => d.total_deficit), '赤字商品数', '赤字額合計', '件数'
  ));
  renderSimpleTableInto('detailTableDeficitCategory', DEFICIT_COLUMNS('カテゴリ'), data.map(deficitRow), 20);
}

function renderDeficitProcSection() {
  const granularity = granSel.value, periodKey = periodSel.value;
  document.getElementById('deficitProcPeriodLabel').textContent = currentPeriodLabel();
  const { rowFilter } = deficitFilterState();
  let data = buildDimBreakdownGeneric(DEFICIT_ROWS, 'procurement_type', DEFICIT_FIELDS, granularity, periodKey, rowFilter).map(deficitDerive);
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
  renderChart('deficitTrendChart', dualAxisMoneyRightConfig(
    labels, trend.periods.map(p => pick(p, 'count')), trend.periods.map(p => pick(p, 'total_deficit')), '赤字商品数', '赤字額合計', '件数'
  ));
}

function renderDeficitPage() {
  renderDeficitKPIs();
  renderDeficitTrend();
  renderDeficitCategorySection();
  renderDeficitProcSection();
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
  { name: '顧客' },
  { name: '発送商品数', formatter: c => fmtInt(c) },
  { name: '同梱率', formatter: c => fmtPct(c) },
  { name: 'SR率', formatter: c => fmtPct(c) },
  { name: 'SR発生件数', formatter: c => fmtInt(c) },
  { name: '返金額(円)', formatter: c => fmtYen(c) },
  { name: '返金額率', formatter: c => fmtPct(c) },
  { name: '最終利益(円)', formatter: c => fmtYen(c) }
];

function renderCustomerPage() {
  const segment = currentCustomerSegment;
  const meta = CUSTOMER_SEGMENTS[segment];
  document.getElementById('segBtnSrRepeater').classList.toggle('active', segment === 'sr_repeater');
  document.getElementById('segBtnLoyalCustomer').classList.toggle('active', segment === 'loyal_customer');
  document.getElementById('customerSegTitle').textContent = meta.title;
  document.getElementById('customerSegDesc').textContent = meta.desc;

  const { agg, tableRows } = customerSegmentData(segment);
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
}

// ---------- 生成: ③全カテゴリページ ----------
function renderAllCategoryPage() {
  renderInsights();
  const granularity = granSel.value, periodKey = periodSel.value;
  renderYoyBox('yoyBoxAllCategory', 'yoyTableAllCategory', 'yoyPeriodLabelAllCategory', granularity, periodKey, null);
  renderCategoryBreakdown();
  renderCategorySrMajorChart();
  renderDetailTable('category');
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

function renderComparisonTrendCharts(prefix, rowFilter) {
  const granularity = granSel.value;
  const trendSel = buildTrendAligned(granularity, rowFilter);
  const trendAll = buildTrendAligned(granularity, null);
  const labels = trendSel.map(t => t.label);

  const specs = [
    { key: 'inquiry', bar: 'inquiry_count', line: 'inquiry_rate', barLabel: '問合せ件数', lineLabel: '問合せ率', baseLabel: '平均問合せ率', leftLabel: '件数', money: false },
    { key: 'sr', bar: 'sr_count', line: 'sr_rate', barLabel: 'SR発生件数', lineLabel: 'SR発生率', baseLabel: '平均SR率', leftLabel: '件数', money: false },
    { key: 'refund', bar: 'refund_amount', line: 'refund_rate', barLabel: '返金額', lineLabel: '返金額率', baseLabel: '平均返金額率', leftLabel: '金額(¥)', money: true },
    { key: 'question', bar: 'question_count', line: 'question_rate', barLabel: '質問数', lineLabel: '質問率', baseLabel: '平均質問率', leftLabel: '件数', money: false },
    { key: 'profit', bar: 'final_profit', line: 'profit_margin', barLabel: '最終利益', lineLabel: '利益率', baseLabel: '平均利益率', leftLabel: '金額(¥)', money: true },
    { key: 'junk', bar: 'junk_listed_count', line: 'junk_listed_rate', barLabel: 'ジャンク出品件数', lineLabel: 'ジャンク出品率', baseLabel: '平均ジャンク出品率', leftLabel: '件数', money: false }
  ];

  specs.forEach(s => {
    renderChart(prefix + '_' + s.key, dualAxisWithBaselineConfig(
      labels, trendSel.map(t => t[s.bar]), trendSel.map(t => t[s.line]), trendAll.map(t => t[s.line]),
      s.barLabel, s.lineLabel, s.baseLabel, s.leftLabel, s.money
    ));
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
  const label = selectedCats.length === 1 ? selectedCats[0] : selectedCats.join(' + ') + '(合算・' + selectedCats.length + 'カテゴリ)';
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
  const catLabel = selectedCats.length === 1 ? selectedCats[0] : selectedCats.join(' + ') + '(合算・' + selectedCats.length + 'カテゴリ)';
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
  else if (currentPage === 'location') renderLocationPage();
  else if (currentPage === 'allcategory') renderAllCategoryPage();
  else if (currentPage === 'category') renderCategoryPage();
  else if (currentPage === 'condition') renderConditionPage();
  else if (currentPage === 'loccat') renderLocCatPage();
  else if (currentPage === 'deficit') renderDeficitPage();
  else if (currentPage === 'customer') renderCustomerPage();
  else renderProfitVariancePage();
}

const ALL_PAGES = ['overall', 'location', 'allcategory', 'category', 'loccat', 'condition', 'profitvariance', 'deficit', 'customer'];
const PAGE_NAV_MAP = {
  overall: 'navBtnOverall', location: 'navBtnLocation', allcategory: 'navBtnAllCategory',
  category: 'navBtnCategory', loccat: 'navBtnLocCat', condition: 'navBtnCondition',
  profitvariance: 'navBtnProfitVariance', deficit: 'navBtnDeficit', customer: 'navBtnCustomer'
};

// ⑧赤字ページを一度でも表示したか(初回表示時のみ拠点を「全拠点」に初期化するためのフラグ)
let deficitLocInitialized = false;

function setPage(page) {
  currentPage = page;
  Object.keys(PAGE_NAV_MAP).forEach(p => document.getElementById(PAGE_NAV_MAP[p]).classList.toggle('active', p === page));
  ALL_PAGES.forEach(p => document.getElementById('page-' + p).classList.toggle('active', p === page));
  // E項目: ⑧赤字ページでも拠点セレクタを使えるようにする
  ctlLoc.classList.toggle('visible', page === 'location' || page === 'loccat' || page === 'deficit');
  // ⑨顧客セグメントページは全期間累計のため、期間・拠点の絞り込みUIは非表示にする
  const globalControls = document.getElementById('globalControls');
  if (globalControls) globalControls.style.display = (page === 'customer') ? 'none' : '';
  // ②拠点別は1拠点ドリルダウン専用なので、従来どおり「全拠点」が選ばれていたら実在の拠点に戻す。
  // ⑤拠点×カテゴリは「全拠点」をそのまま尊重し、値が空のときだけ実在の拠点にフォールバックする。
  if (page === 'location' && (!locSel.value || locSel.value === ALL_LOC) && locations.length) locSel.value = firstRealLocation;
  if (page === 'loccat' && !locSel.value && locations.length) locSel.value = firstRealLocation;
  // ⑧赤字ページの初期値は「全拠点」。一度表示した後はユーザーの選択をそのまま保持する。
  if (page === 'deficit' && !deficitLocInitialized) { locSel.value = ALL_LOC; deficitLocInitialized = true; }
  renderPageContent();
}

document.getElementById('navBtnOverall').addEventListener('click', () => setPage('overall'));
document.getElementById('navBtnLocation').addEventListener('click', () => setPage('location'));
document.getElementById('navBtnAllCategory').addEventListener('click', () => setPage('allcategory'));
document.getElementById('navBtnCategory').addEventListener('click', () => setPage('category'));
document.getElementById('navBtnCondition').addEventListener('click', () => setPage('condition'));
document.getElementById('navBtnProfitVariance').addEventListener('click', () => setPage('profitvariance'));
document.getElementById('navBtnLocCat').addEventListener('click', () => setPage('loccat'));
document.getElementById('navBtnDeficit').addEventListener('click', () => setPage('deficit'));
document.getElementById('navBtnCustomer').addEventListener('click', () => setPage('customer'));
document.getElementById('segBtnSrRepeater').addEventListener('click', () => setCustomerSegment('sr_repeater'));
document.getElementById('segBtnLoyalCustomer').addEventListener('click', () => setCustomerSegment('loyal_customer'));

function renderAll() {
  populatePeriodSelect();
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
locSel.addEventListener('change', () => { if (currentPage === 'location' || currentPage === 'loccat' || currentPage === 'deficit') renderPageContent(); });
if (catMultiSel) catMultiSel.addEventListener('change', () => { if (currentPage === 'category') renderPageContent(); });
if (locCatMultiSel) locCatMultiSel.addEventListener('change', () => { if (currentPage === 'loccat') renderPageContent(); });
if (deficitCatMultiSel) deficitCatMultiSel.addEventListener('change', () => { if (currentPage === 'deficit') renderPageContent(); });

document.getElementById('subHeader').textContent =
  DATA.fiscal_year_label + '(' + DATA.fiscal_year_start + '～) の問合せ・SR・返金・質問データ　最終更新: ' + (DATA.generated_at || '').slice(0,16).replace('T',' ');
document.getElementById('dataThrough').textContent = DATA.data_through || '-';

renderAll();
</script>
</body>
</html>
'''

html = html.replace('__DATA_JSON_GZ_B64__', data_json_gz_b64)

out_path = 'cs_sr_dashboard.html'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(html)

out_bytes = len(html.encode('utf-8'))
print('written:', out_path, len(html), 'chars', out_bytes, 'bytes',
      f'({out_bytes/1024/1024:.2f} MB)')
