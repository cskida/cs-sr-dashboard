#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS(問合せ・サービスリクエスト)分析ダッシュボード用 週次データ全期間集計ETL
=====================================================================

概要
----
Google Drive上の以下の階層に格納された週次CSV(Shift-JIS/CP932)を全期間分収集し、
年月(YYYY-MM) x 拠点 x カテゴリ の粒度で集計した結果を JSON に出力する。

    13.質問・SR分析　データ蓄積 (root)
      └ 21th (21期 = 2026/7/1〜2027/6/30)   <- FISCAL_ROOT_ID
          └ YYYY年M月 (月フォルダ)
              └ N-N日 (週フォルダ)
                  ├ CS_登録YYYY年M月D日～M月D日.csv
                  ├ CS_登録【分類用】YYYY年M月D日～M月D日.csv  (存在する場合はCS_登録の代わりにこちらを使用)
                  ├ CS_返金YYYY年M月D日～M月D日.csv
                  ├ 質問_登録YYYY年M月D日～M月D日.csv
                  ├ 受注_通常_出荷YYYY年M月D日～M月D日.csv
                  ├ 受注_JPON_出荷YYYY年M月D日～M月D日.csv
                  ├ 商品_出荷(JPONベース)YYYY年M月D日～M月D日.csv
                  └ 商品_出品待YYYY年M月D日～M月D日.csv

データ取得方法は2種類切替可能:
  1. --mode drive : Google Drive API v3 (googleapiclient) を用いて FISCAL_ROOT_ID から
                     動的にフォルダ探索・ファイル取得を行う「本番用」モード。
                     認証情報は環境変数 GOOGLE_APPLICATION_CREDENTIALS
                     (サービスアカウントJSON) または --credentials 引数で指定する。
                     ※ このモードは Google Drive API へのネットワークアクセスと
                        有効な認証情報が利用できる実行環境が必要。
  2. --mode local  : 事前にダウンロード済みのCSV群を、Drive上と同じフォルダ階層・
                     ファイル名で配置したローカルディレクトリから読み込む「オフライン/
                     検証用」モード。今回の実行はこちらを使用した
                     (チャットツール経由のDrive MCPでは大きなCSVを会話コンテキストに
                     載せられない制約があったため。詳細はレポート参照)。

いずれのモードでも、フォルダ探索・ファイル分類・集計ロジックは完全に共通であり、
将来フォルダが増えても(月が増えても、週が増えても)自動的に対応する
(フォルダIDのハードコードは 21th フォルダの起点のみ)。

集計ロジック(ビジネスルール)
----------------------------
- 除外拠点: 拠点名に "CSセンター" "cs_center" "鳥取" "北関東" のいずれかを含む行は
  小文字化・部分一致で判定し、分子・分母の両方から除外する。
- 商品ID統合キー: 先頭の英字1文字を除去し数字部分のみで比較する。
- カテゴリ: CS_登録/CS_返金は自身の「カテゴリ」列を使用。質問_登録は商品ID(数字部分)を
  商品_出荷(JPONベース)+商品_出品待から作成した商品マスタに突合して取得(不明な場合は「不明」)。
- 出荷商品数・売上金額: 受注_通常_出荷(受注ID,拠点,出荷予定日)と受注_JPON_出荷
  (取引番号=受注ID,管理番号=商品ID,落札金額)を受注IDで結合し、1商品=1行に展開。
  商品IDの数字部分で商品マスタに突合してカテゴリを付与。
- 出品数: 商品_出品待ファイルの「出品待」列(その週の出品日、ファイル名の週範囲に収まる方の列)
  を基準に拠点・カテゴリ別に件数を数える。
- 基準日:
    問合せ件数(種別=CS)・SR発生件数(種別=SR) : CS_登録(またはCS_登録【分類用】)の「登録」
    返金額                                    : CS_返金の「返金日」(登録日ではない)
    質問数                                     : 質問_登録の「登録」
    出荷商品数・売上金額                        : 受注_通常_出荷の「出荷予定日」
    出品数                                      : 商品_出品待の「出品待」
- 重複: 同一商品が再入庫・再出荷された場合は別発送として重複除去しない。
- CS_登録【分類用】が存在する週は CS_登録の代わりにそちらを使う。

出力
----
最も細かい粒度(年月×拠点×カテゴリ)の行のみを出力する。
四半期・半期・通期は表示側で月次データを合算して算出する設計であるため、本スクリプトでは
月次行のみを出力する。特に「率」は分子・分母をそれぞれ合算してから最後に割ること
(個々の月の率を平均するのは誤り)。

実行例
------
    python etl_cs_dashboard.py --mode local \
        --local-dir /path/to/drive_cache \
        --output /path/to/cs_sr_dashboard_data.json

    python etl_cs_dashboard.py --mode drive \
        --credentials /path/to/service_account.json \
        --output /path/to/cs_sr_dashboard_data.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 「13.質問・SR分析　データ蓄積」フォルダ配下の「21th」フォルダ(固定起点)
FISCAL_ROOT_ID = "1Ogk8yQsEEx_EpvEjf6lZmGflXwfrQ8iP"
FISCAL_YEAR_LABEL = "21期"
FISCAL_YEAR_START = "2026-07-01"

# 除外拠点キーワード(小文字化・部分一致で判定)
EXCLUDE_LOCATION_KEYWORDS = ["csセンター", "cs_center", "鳥取", "北関東"]

CSV_ENCODING = "cp932"

# ---------------------------------------------------------------------------
# 消費税の扱い(⑥粗利差異・⑦赤字ページの計算はすべて税抜で統一する)
#   税抜のまま使う : 落札価格(ヤフオク落札額)
#   税込 -> ÷1.1   : 販売価格 / 買取価格 / ヤフオク配送料 / 送料(受注_通常_出荷) / 返金額
# 元データの税表記は運用担当者への確認結果に基づく(2026-08時点)。
# ---------------------------------------------------------------------------
TAX_RATE = 1.1


def to_excl_tax(v):
    """税込金額を税抜に換算する。"""
    return v / TAX_RATE

# ファイル名 -> 内部カテゴリキー への分類ルール(優先順に評価)
# ファイル名は運用の時期によって新旧2通りある(どちらも中身は同じ):
#   新: CS_登録 / CS_返金 / 受注_通常_出荷 / 受注_JPON_出荷 / 商品_出荷(JPONベース) / 商品_出品待
#   旧: CSV_登録 / CSV_返金日 / 受注_通常_    / 受注_JPON_     / 商品V2_発送CSVアップロード / 商品V2_出品待ち_登録
# 自動更新がどちらの命名でも動くように、両方を同じ内部キーに分類する。
FILE_CLASSIFIERS = [
    ("cs_bunruiyou", re.compile(r"^(CS|CSV)_登録【分類用】")),
    ("cs_touroku", re.compile(r"^(CS|CSV)_登録(?!【分類用】)")),
    ("cs_henkin", re.compile(r"^(CS_返金|CSV_返金)")),
    # 質問_登録も CS_登録 と同じ考え方で「【分類用】が存在する週はそちらを優先」する。
    # 【分類用】は通常版(24列)に カテゴリ/対応部署(J列参照。順次修正)/原因詳細/原因元/原因分類
    # の5列が追加されたもので、カテゴリが直接入っているため商品マスタ突合が不要になる。
    ("shitsumon_bunruiyou", re.compile(r"^質問_登録【分類用】")),
    ("shitsumon", re.compile(r"^質問_登録(?!【分類用】)")),
    ("juchu_tsujo", re.compile(r"^受注_通常")),
    ("juchu_jpon", re.compile(r"^受注_JPON")),
    # 「商品V2_出品待ち_登録」を先に判定する(「商品V2_」で始まる点が発送用と共通のため)
    ("shohin_shuppinmachi", re.compile(r"^(商品_出品待|商品V2_出品待)")),
    ("shohin_shukka", re.compile(r"^(商品_出荷|商品V2_発送)")),
]

MONTH_FOLDER_RE = re.compile(r"^(\d{4})年(\d{1,2})月$")

# 週フォルダ名 (例 "20-26日" "1-5日") から終了日を取り出す
WEEK_RANGE_RE = re.compile(r"(\d{1,2})\s*[-～〜]\s*(\d{1,2})日")

# ファイル名自体に埋め込まれた日付範囲 (例 "CS_登録2026年7月1日～7月5日.csv",
# "質問_登録2026年7月27日～8月2日.csv" のような月またぎも含む) から週の開始日・終了日を
# 直接取り出す。フォルダの階層・命名規則(月フォルダ/週フォルダの2階層、週は同月内、等)に
# 依存しないため、フォルダ構成が変わっても(新しい21thの置き場所のようにフラットな1階層で
# 月またぎ週フォルダがあっても)壊れない。
FILENAME_DATE_RE = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*[-～〜]\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日"
)

GOOGLE_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


def parse_date_range_from_filename(name: str) -> Optional[tuple[str, str, str]]:
    """ファイル名から (week_start, week_end, year_month) を抽出する。

    year_month は週の開始日が属する月を採用する(月をまたぐ週は開始月に帰属させる)。
    """
    m = FILENAME_DATE_RE.search(name)
    if not m:
        return None
    y1, mo1, d1, y2, mo2, d2 = m.groups()
    year1, month1, day1 = int(y1), int(mo1), int(d1)
    year2 = int(y2) if y2 else year1
    month2, day2 = int(mo2), int(d2)
    try:
        start_dt = datetime(year1, month1, day1)
        end_dt = datetime(year2, month2, day2)
    except ValueError:
        return None
    year_month = f"{year1:04d}-{month1:02d}"
    return (start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), year_month)


def classify_filename(name: str) -> Optional[str]:
    for key, pattern in FILE_CLASSIFIERS:
        if pattern.match(name):
            return key
    return None


# ---------------------------------------------------------------------------
# Drive アクセス抽象化: local / drive の2バックエンドを同一インターフェースで提供
# ---------------------------------------------------------------------------


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str


class BaseDriveBackend:
    """フォルダ探索・ファイル取得のための共通インターフェース"""

    def list_children(self, folder_id: str) -> list[DriveFile]:
        raise NotImplementedError

    def download_bytes(self, file_id: str) -> bytes:
        raise NotImplementedError


class LiveDriveBackend(BaseDriveBackend):
    """Google Drive API v3 (googleapiclient) を用いた本番用バックエンド。

    認証: サービスアカウントJSON (credentials_path) を使用。
    スコープ: https://www.googleapis.com/auth/drive.readonly
    """

    def __init__(self, credentials_path: Optional[str] = None):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "google-api-python-client / google-auth がインストールされていません。 "
                "`pip install google-api-python-client google-auth` を実行してください。"
            ) from exc

        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        if credentials_path:
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=scopes
            )
        else:
            import os

            env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if not env_path:
                raise RuntimeError(
                    "認証情報が指定されていません。--credentials か環境変数 "
                    "GOOGLE_APPLICATION_CREDENTIALS でサービスアカウントJSONを指定してください。"
                )
            creds = service_account.Credentials.from_service_account_file(
                env_path, scopes=scopes
            )

        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def list_children(self, folder_id: str) -> list[DriveFile]:
        results: list[DriveFile] = []
        page_token = None
        query = f"'{folder_id}' in parents and trashed = false"
        while True:
            # 共有ドライブ(共有ドライブ配下のフォルダ)にも対応するため、
            # supportsAllDrives / includeItemsFromAllDrives を必ず付ける。
            # これが無いと共有ドライブ上のファイルが1件も返らない。
            resp = (
                self._service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType)",
                    pageSize=200,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                mime = f["mimeType"]
                name = f["name"]
                # フォルダはそのまま。それ以外は「拡張子が.csvの通常ファイル」だけを対象にする。
                # 手順書メモ(.txt)やGoogleスプレッドシート・ショートカットなどが同じフォルダに
                # 置かれていても、CSVとして読もうとして落ちないようにするため。
                if mime != GOOGLE_DRIVE_FOLDER_MIME:
                    if mime.startswith("application/vnd.google-apps."):
                        continue
                    if not name.lower().endswith(".csv"):
                        continue
                results.append(DriveFile(id=f["id"], name=name, mime_type=mime))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def download_bytes(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        request = self._service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()


class LocalCacheDriveBackend(BaseDriveBackend):
    """Drive上と同じフォルダ階層・ファイル名でローカルに配置されたCSV群を読むバックエンド。

    ディレクトリ構成:
        <root>/YYYY年M月/週フォルダ名/元のファイル名.csv

    "フォルダID" は本バックエンドではディレクトリの絶対パス文字列として扱う。
    FISCAL_ROOT_ID (Drive上のID文字列) はそのまま --local-dir のルートに対応させる。
    """

    def __init__(self, root_dir: str, fiscal_root_id: str):
        self._root_dir = Path(root_dir)
        self._fiscal_root_id = fiscal_root_id
        if not self._root_dir.exists():
            raise RuntimeError(f"ローカルキャッシュディレクトリが存在しません: {root_dir}")

    def _resolve(self, folder_id: str) -> Path:
        if folder_id == self._fiscal_root_id:
            return self._root_dir
        # サブフォルダは "フォルダID" として絶対パス文字列をそのまま使う運用にする
        return Path(folder_id)

    def list_children(self, folder_id: str) -> list[DriveFile]:
        path = self._resolve(folder_id)
        out: list[DriveFile] = []
        if not path.exists():
            return out
        for child in sorted(path.iterdir()):
            if child.is_dir():
                out.append(DriveFile(id=str(child), name=child.name, mime_type=GOOGLE_DRIVE_FOLDER_MIME))
            elif child.suffix.lower() == ".csv":
                out.append(DriveFile(id=str(child), name=child.name, mime_type="text/csv"))
        return out

    def download_bytes(self, file_id: str) -> bytes:
        return Path(file_id).read_bytes()


# ---------------------------------------------------------------------------
# フォルダ探索
# ---------------------------------------------------------------------------


@dataclass
class WeekFiles:
    month_label: str
    week_label: str
    year: int = 0
    month: int = 0
    week_start_day: int = 0
    week_end_day: int = 0
    week_start: str = ""  # YYYY-MM-DD (週の開始日)
    week_end: str = ""  # YYYY-MM-DD (週の終了日)
    year_month: str = ""  # YYYY-MM (週の属する月。週フォルダは月をまたがない前提)
    files: dict[str, bytes] = field(default_factory=dict)  # classify_key -> raw bytes


def _compute_week_bounds(month_label: str, week_label: str) -> Optional[tuple[int, int, int, int, str, str, str]]:
    m = MONTH_FOLDER_RE.match(month_label)
    wm = WEEK_RANGE_RE.search(week_label)
    if not m or not wm:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    start_day, end_day = int(wm.group(1)), int(wm.group(2))
    try:
        start_dt = datetime(year, month, start_day)
        end_dt = datetime(year, month, end_day)
    except ValueError:
        return None
    year_month = f"{year:04d}-{month:02d}"
    return (year, month, start_day, end_day, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), year_month)


def _collect_leaf_csv_groups(
    backend: BaseDriveBackend, folder_id: str, acc: list[tuple[str, list[DriveFile]]]
) -> None:
    """folder_id 配下を再帰的に探索し、CSVファイルを含む「葉フォルダ」ごとに
    (フォルダid, CSVファイル一覧) を acc に集める。

    フォルダの階層の深さ・命名規則(月フォルダ/週フォルダの2階層かどうか等)に
    依存しない。旧来の2階層構成(root/YYYY年M月/N-N日/)でも、新しいフラットな
    1階層構成(root/週フォルダ/)でも、同じロジックでそのまま動く。
    """
    children = backend.list_children(folder_id)
    csvs = [f for f in children if f.mime_type != GOOGLE_DRIVE_FOLDER_MIME]
    subdirs = [f for f in children if f.mime_type == GOOGLE_DRIVE_FOLDER_MIME]
    if csvs:
        acc.append((folder_id, csvs))
    for sd in subdirs:
        _collect_leaf_csv_groups(backend, sd.id, acc)


def discover_week_files(backend: BaseDriveBackend, fiscal_root_id: str) -> list[WeekFiles]:
    """フォルダ配下を再帰的に探索し、週(CSVファイルが置かれた葉フォルダ)ごとに
    分類済みファイルバイト列を返す。週の開始日・終了日・年月は、フォルダ名では
    なく各CSVファイル名自体に埋め込まれた日付範囲から決定する(月またぎ週や、
    フォルダ階層の変更に対して頑健にするため)。
    """

    groups: list[tuple[str, list[DriveFile]]] = []
    _collect_leaf_csv_groups(backend, fiscal_root_id, groups)

    weeks: list[WeekFiles] = []
    for folder_id, csvs in groups:
        bounds = None
        for f in csvs:
            bounds = parse_date_range_from_filename(f.name)
            if bounds is not None:
                break
        if bounds is None:
            # 日付範囲を解釈できないフォルダ(想定外のファイルのみ)は無視
            continue
        week_start, week_end, year_month = bounds
        week_files = WeekFiles(
            month_label=year_month,
            week_label=str(folder_id),
            week_start=week_start,
            week_end=week_end,
            year_month=year_month,
        )
        for f in csvs:
            key = classify_filename(f.name)
            if key is None:
                continue
            # CS_登録【分類用】がある週は CS_登録(通常)は無視する
            if key == "cs_touroku" and "cs_bunruiyou" in week_files.files:
                continue
            # 質問_登録【分類用】がある週は 質問_登録(通常)は無視する(CS_登録と同じ方針)
            if key == "shitsumon" and "shitsumon_bunruiyou" in week_files.files:
                continue
            raw = backend.download_bytes(f.id)
            # ここで一度パースしてみて、CSVとして読めないファイルは警告を出して除外する。
            # (1ファイルの不備で全体の集計が止まらないようにするため。パース結果は
            #  キャッシュされるので、この検証による二重パースのコストは発生しない)
            try:
                read_csv_bytes(raw)
            except Exception as exc:
                print(f"[警告] CSVとして読めないためスキップします: {f.name} ({type(exc).__name__}: {exc})", flush=True)
                continue
            if key == "cs_bunruiyou":
                # 分類用が来たら通常版を捨てる
                week_files.files.pop("cs_touroku", None)
            if key == "shitsumon_bunruiyou":
                # 分類用が来たら通常版を捨てる
                week_files.files.pop("shitsumon", None)
            week_files.files[key] = raw
        weeks.append(week_files)

    # 週の開始日の昇順(時系列順)にソートする
    weeks.sort(key=lambda w: w.week_start)
    return weeks


# ---------------------------------------------------------------------------
# CSV パース・共通ユーティリティ
# ---------------------------------------------------------------------------


def compute_data_through(weeks: list[WeekFiles]) -> Optional[str]:
    """検出した週フォルダのうち最も新しい週の終了日を YYYY-MM-DD で返す。"""
    latest: Optional[str] = None
    for w in weeks:
        if not w.week_end:
            continue
        if latest is None or w.week_end > latest:
            latest = w.week_end
    return latest



_READ_CSV_CACHE: dict[str, pd.DataFrame] = {}

# ディスク上のパース結果キャッシュ(実行プロセスをまたいだ高速化用)。
# 生のCSVバイト列のMD5ハッシュをキーにpickle化したDataFrameを保存する。
# 内容が同一である限り(同じdrive_cacheファイル)、CP932デコード+CSVパースを
# 再実行せずに済むため、データ量が多い場合の実行時間を大幅に短縮できる。
# 業務ロジック・出力結果には一切影響しない、純粋なI/O高速化のためのキャッシュ。
_DISK_PARSE_CACHE_DIR = Path(__file__).resolve().parent / ".csv_parse_cache"


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """CSVバイト列をパースする。同じ週の同じファイルは複数の集計関数
    (カテゴリ/コスト/状態マスタ構築、コンディション・価格帯・粗利差異集計など)から
    繰り返し読まれるため、bytesオブジェクトの identity をキーにパース結果を
    キャッシュし、同一実行内での重複パースを避ける(実行時間短縮)。
    呼び出し側が結果を書き換えることがあるため、キャッシュからは必ずコピーを返す。

    加えて、実行プロセスをまたいだディスクキャッシュ(内容のMD5ハッシュ単位)も
    参照する。存在すればCP932でのCSVパースをスキップしてpickleから復元する。
    """
    # 【重要】キャッシュのキーは必ず「中身のハッシュ」にすること。
    # 以前は id(raw)(メモリアドレス)をキーにしていたが、Pythonは解放されたオブジェクトの
    # idを再利用するため、読み捨てたファイルのidを後続ファイルが再利用すると
    # 「別のファイルのパース結果」を返してしまい、集計値が実行ごとに変わる不具合があった
    # (同一データで SR件数が 490/566/703 と揺れる事象を確認)。
    import hashlib
    import pickle

    digest = hashlib.md5(raw).hexdigest()
    cached = _READ_CSV_CACHE.get(digest)
    if cached is not None:
        return cached.copy()

    disk_cache_path = _DISK_PARSE_CACHE_DIR / f"{digest}.pkl"
    df: Optional[pd.DataFrame] = None
    if disk_cache_path.exists():
        try:
            df = pickle.loads(disk_cache_path.read_bytes())
        except Exception:
            df = None
    if df is None:
        # 週次エクスポートは基本CP932だが、担当者がUTF-8で保存し直したファイルが
        # 混在することがある(Excelやスプレッドシート経由で保存すると起こりうる)。
        # 文字コード違いだけで全体の集計が止まらないよう、候補を順に試す。
        # 最後の手段として置換モードで読むが、その場合は警告を出す。
        last_err = None
        for enc in (CSV_ENCODING, "utf-8-sig", "utf-8", "cp932"):
            try:
                df = pd.read_csv(io.BytesIO(raw), encoding=enc, dtype=str, low_memory=False)
                if enc != CSV_ENCODING:
                    print(f"[情報] CP932以外の文字コードで読み込みました: {enc}", flush=True)
                break
            except UnicodeDecodeError as e:
                last_err = e
                df = None
        if df is None:
            print(f"[警告] 文字コードを判定できないため、読めない文字を置換して読み込みます: {last_err}", flush=True)
            df = pd.read_csv(
                io.BytesIO(raw), encoding=CSV_ENCODING, encoding_errors="replace",
                dtype=str, low_memory=False,
            )
        try:
            _DISK_PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            disk_cache_path.write_bytes(pickle.dumps(df))
        except Exception:
            pass
    _READ_CSV_CACHE[digest] = df
    return df.copy()


def concat_and_dedup(frames: list[pd.DataFrame], id_col: Optional[str] = None) -> pd.DataFrame:
    """週次ファイルを結合する。

    週次エクスポートの抽出タイミングが週境界と厳密に一致しないため、同一の
    レコード(CS ID・質問ID・受注ID・オークションID・商品IDなど)が隣接する週の
    ファイルに重複して出現するケースがある(実データで確認済み。例: 受注ID=2657873が
    6-12日分と13-19日分の両方に出荷予定日が更新されて再出現するなど)。
    id_col が指定されている場合は、そのカラムの値が空でない行に限り重複を検出し、
    最新週(=週フォルダを月次アーカイブの並び順に処理した際の最後の出現)の行を
    採用して重複を除去する。id_col の値が空/NaNの行(例:オークションIDが未設定の
    行)は照合キーとして使えないため対象外とし、そのまま残す。
    """
    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    if id_col and id_col in all_df.columns:
        id_str = all_df[id_col].astype(str).str.strip()
        has_id = all_df[id_col].notna() & (id_str != "") & (id_str.str.lower() != "nan")
        with_id = all_df[has_id]
        without_id = all_df[~has_id]
        before = len(with_id)
        with_id_dedup = with_id.drop_duplicates(subset=[id_col], keep="last")
        removed = before - len(with_id_dedup)
        if removed:
            print(
                f"[情報] {id_col} の重複 {removed} 行を除去しました"
                f"(複数週の抽出ファイルに同一レコードが出現。最新週の値を採用)"
            )
        all_df = pd.concat([with_id_dedup, without_id], ignore_index=True)
    return all_df


def is_excluded_location(loc) -> bool:
    if loc is None or (isinstance(loc, float) and pd.isna(loc)):
        return False
    s = str(loc).strip().lower()
    if not s:
        return False
    return any(kw in s for kw in EXCLUDE_LOCATION_KEYWORDS)


def normalize_product_id(pid) -> Optional[str]:
    """先頭の英字1文字を除去し、数字部分のみを返す。空/NaNはNoneを返す。"""
    if pid is None:
        return None
    s = str(pid).strip()
    if not s or s.lower() == "nan":
        return None
    s = re.sub(r"^[A-Za-z]", "", s)
    s = s.strip()
    return s or None


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    ).fillna(0)


def to_year_month(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce")
    return dt.dt.strftime("%Y-%m")


def tag_by_date(df: pd.DataFrame, w: "WeekFiles", date_col: str) -> pd.DataFrame:
    """dfの各行に、その行自身の基準日(date_col)から1日単位の期間キーを付与する。

    従来は週フォルダの範囲(tag_week)を期間キーにしていたため、集計粒度が
    「エクスポート週」に固定され、週の起点(月曜/金曜など)を後から変えられなかった。
    行ごとの実際の基準日を使って _week_start = _week_end = その日 とすることで、
    出力JSONは日次粒度になり、表示側で任意の曜日起点に再集計できる。
    基準日が空/不正な行は NaN になり、各集計関数の既存の有効日フィルタで除外される。
    """
    df = df.copy()
    dt = pd.to_datetime(df[date_col], errors="coerce")
    ds = dt.dt.strftime("%Y-%m-%d")
    df["_week_start"] = ds
    df["_week_end"] = ds
    df["_year_month"] = dt.dt.strftime("%Y-%m")
    return df


def tag_week(df: pd.DataFrame, w: "WeekFiles") -> pd.DataFrame:
    """dfの各行に、その行がどの週フォルダ由来かを示す列を付与する。"""
    df = df.copy()
    df["_week_start"] = w.week_start
    df["_week_end"] = w.week_end
    df["_year_month"] = w.year_month
    return df


# ---------------------------------------------------------------------------
# 集計ロジック
# ---------------------------------------------------------------------------


@dataclass
class ExclusionStats:
    cs_rows: int = 0
    henkin_rows: int = 0
    shitsumon_rows: int = 0
    juchu_rows: int = 0
    shuppinmachi_rows: int = 0
    shukka_rows: int = 0
    # 「ステータス」列が「スルー」の行の除外数(CS_登録/CS_登録【分類用】を読む集計関数
    # aggregate_cs_sr / aggregate_sr_major / aggregate_cause から積み上げて加算する)
    through_rows: int = 0

    @property
    def total(self) -> int:
        return (
            self.cs_rows
            + self.henkin_rows
            + self.shitsumon_rows
            + self.juchu_rows
            + self.shuppinmachi_rows
            + self.shukka_rows
        )


def build_product_cost_master(weeks: list[WeekFiles]) -> dict[str, tuple[float, float]]:
    """商品ID(数字部分) -> (買取価格, ヤフオク配送料) のマスタを作成する。

    粗利・最終利益の算出に使う。商品_出荷(JPONベース)・商品_出品待の両方から集め、
    同一IDが複数回出現する場合は最初に現れたものを採用する。
    """
    frames = []
    for w in weeks:
        for key in ("shohin_shukka", "shohin_shuppinmachi"):
            raw = w.files.get(key)
            if raw is None:
                continue
            df = read_csv_bytes(raw)
            needed = ["商品ID", "買取価格", "ヤフオク配送料"]
            if not all(c in df.columns for c in needed):
                continue
            sub = df[needed].copy()
            sub["_norm_id"] = sub["商品ID"].map(normalize_product_id)
            frames.append(sub[["_norm_id", "買取価格", "ヤフオク配送料"]])
    if not frames:
        return {}
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.dropna(subset=["_norm_id"])
    all_df["買取価格_num"] = to_numeric(all_df["買取価格"])
    all_df["配送料_num"] = to_numeric(all_df["ヤフオク配送料"])
    dedup = all_df.drop_duplicates(subset=["_norm_id"], keep="first")
    # iterrows()は行数が多いと非常に遅いため、列をまとめて取り出しzipで組み立てる
    ids = dedup["_norm_id"].tolist()
    costs = dedup["買取価格_num"].astype(float).tolist()
    ships = dedup["配送料_num"].astype(float).tolist()
    return dict(zip(ids, zip(costs, ships)))


def build_shipping_fee_master(weeks: list[WeekFiles]) -> dict[str, float]:
    """商品ID(数字部分) -> 実際の発送送料(受注_通常_出荷の「送料」列) のマスタを作成する。

    A項目対応: 商品_出荷(JPONベース)の「ヤフオク配送料」列が"らくらく家財便"という
    文字列になっている行では、その列自体には金額が入っていないため、実際の送料を
    別データソースから辿って取得する必要がある。実データで確認した2段階の結合方法:

        商品_出荷(JPONベース).商品ID(normalize) == 受注_JPON_出荷.管理番号(normalize)
        受注_JPON_出荷.取引番号 == 受注_通常_出荷.受注ID

    を経由して 受注_通常_出荷.送料 を取得する(aggregate_shipped_and_sales/
    _read_and_merge_shipped_raw が行っている tsujo.merge(jpon, left_on="受注ID",
    right_on="取引番号") と全く同じ結合パターン)。

    同一商品IDが複数回出現する場合は最初に現れたものを採用する(他のマスタ構築関数と
    同じ方針)。全期間分のtsujo/jponを読み込むため、Drive/ローカルキャッシュへのI/Oが
    発生する点は build_product_cost_master 等と同様。
    """
    tsujo_frames = []
    jpon_frames = []
    for w in weeks:
        traw = w.files.get("juchu_tsujo")
        jraw = w.files.get("juchu_jpon")
        if traw is not None:
            tsujo_frames.append(read_csv_bytes(traw))
        if jraw is not None:
            jpon_frames.append(read_csv_bytes(jraw))
    if not tsujo_frames or not jpon_frames:
        return {}

    tsujo = concat_and_dedup(tsujo_frames, id_col="受注ID")
    jpon = concat_and_dedup(jpon_frames, id_col="オークションID")

    needed_t = ["受注ID", "送料"]
    needed_j = ["取引番号", "管理番号"]
    if not all(c in tsujo.columns for c in needed_t) or not all(c in jpon.columns for c in needed_j):
        return {}

    # 列名の衝突(どちらのファイルにも「送料」に相当する列がある可能性)を避けるため、
    # 必要な列だけを抜き出してから改名してmergeする。
    tsub = tsujo[needed_t].rename(columns={"送料": "_tsujo_shipping_fee"})
    jsub = jpon[needed_j].copy()

    merged = jsub.merge(tsub, left_on="取引番号", right_on="受注ID", how="inner")
    merged["_norm_id"] = merged["管理番号"].map(normalize_product_id)
    merged = merged.dropna(subset=["_norm_id"])
    merged["_fee_num"] = to_numeric(merged["_tsujo_shipping_fee"])
    dedup = merged.drop_duplicates(subset=["_norm_id"], keep="first")
    ids = dedup["_norm_id"].tolist()
    fees = dedup["_fee_num"].astype(float).tolist()
    return dict(zip(ids, fees))


def build_ship_date_master(weeks: list[WeekFiles]) -> dict[str, str]:
    """商品ID(数字部分) -> 出荷予定日(YYYY-MM-DD) のマスタを作成する。

    商品_出荷(JPONベース)自体には出荷予定日が無いため、
        商品_出荷.商品ID(normalize) == 受注_JPON_出荷.管理番号(normalize)
        受注_JPON_出荷.取引番号 == 受注_通常_出荷.受注ID
    の2段結合(build_shipping_fee_master と同一パターン)で受注_通常_出荷の
    「出荷予定日」を引く。⑤⑥ページ(コンディション/価格帯/粗利差異)を
    ①〜④ページの出荷商品数と同じ日付基準で日次集計するために使う。
    """
    tsujo_frames, jpon_frames = [], []
    for w in weeks:
        traw = w.files.get("juchu_tsujo")
        jraw = w.files.get("juchu_jpon")
        if traw is not None:
            tsujo_frames.append(read_csv_bytes(traw))
        if jraw is not None:
            jpon_frames.append(read_csv_bytes(jraw))
    if not tsujo_frames or not jpon_frames:
        return {}
    tsujo = concat_and_dedup(tsujo_frames, id_col="受注ID")
    jpon = concat_and_dedup(jpon_frames, id_col="オークションID")
    if "出荷予定日" not in tsujo.columns or "取引番号" not in jpon.columns:
        return {}
    tsub = tsujo[["受注ID", "出荷予定日"]].rename(columns={"出荷予定日": "_ship_date"})
    jsub = jpon[["取引番号", "管理番号"]].copy()
    merged = jsub.merge(tsub, left_on="取引番号", right_on="受注ID", how="inner")
    merged["_norm_id"] = merged["管理番号"].map(normalize_product_id)
    merged = merged.dropna(subset=["_norm_id"])
    dt = pd.to_datetime(merged["_ship_date"], errors="coerce")
    merged["_ship_ymd"] = dt.dt.strftime("%Y-%m-%d")
    merged = merged.dropna(subset=["_ship_ymd"])
    dedup = merged.drop_duplicates(subset=["_norm_id"], keep="first")
    return dict(zip(dedup["_norm_id"].tolist(), dedup["_ship_ymd"].tolist()))


# コンディションランクは J/D/C/B/A/S の6段階(+ごく少数の新品(N))が正。
# 商品_出品待など一部のファイルには「中古」「良好」「未使用」といった略記が混在するため、
# 表記ゆれを正規のランク名に寄せる。判定できない値は「不明」に集約する。
CONDITION_ALIASES = {
    "ジャンク": "ジャンク(J)",
    "程度不良": "程度不良(D)",
    "中古": "一般中古(C)",
    "一般中古": "一般中古(C)",
    "良好": "程度良好(B)",
    "程度良好": "程度良好(B)",
    "美品": "美品(A)",
    "未使用": "未使用品(S)",
    "未使用品": "未使用品(S)",
    "新品": "新品(N)",
}
CANONICAL_CONDITIONS = {
    "ジャンク(J)", "程度不良(D)", "一般中古(C)", "程度良好(B)", "美品(A)", "未使用品(S)", "新品(N)",
}


def normalize_condition(value) -> str:
    """状態の表記ゆれを正規のコンディションランク名に揃える。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "不明"
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return "不明"
    if s in CANONICAL_CONDITIONS:
        return s
    if s in CONDITION_ALIASES:
        return CONDITION_ALIASES[s]
    # 「一般中古(C)」のように括弧付きで来た場合や前後に余計な文字がある場合に備える
    for alias, canon in CONDITION_ALIASES.items():
        if alias in s:
            return canon
    return "不明"


def is_junk_status(status) -> bool:
    if status is None or (isinstance(status, float) and pd.isna(status)):
        return False
    return "ジャンク" in str(status)


def build_product_status_master(weeks: list[WeekFiles]) -> dict[str, str]:
    """商品ID(数字部分) -> 状態(美品/一般中古/ジャンク等) のマスタを作成する。

    ジャンク比率算出に使う。商品_出荷(JPONベース)・商品_出品待の両方から集め、
    同一IDが複数回出現する場合は最初に現れたものを採用する。
    """
    frames = []
    for w in weeks:
        for key in ("shohin_shukka", "shohin_shuppinmachi"):
            raw = w.files.get(key)
            if raw is None:
                continue
            df = read_csv_bytes(raw)
            if "商品ID" not in df.columns or "状態" not in df.columns:
                continue
            sub = df[["商品ID", "状態"]].copy()
            sub["_norm_id"] = sub["商品ID"].map(normalize_product_id)
            frames.append(sub[["_norm_id", "状態"]])
    if not frames:
        return {}
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.dropna(subset=["_norm_id"])
    dedup = all_df.drop_duplicates(subset=["_norm_id"], keep="first").set_index("_norm_id")["状態"]
    return dedup.to_dict()


def build_product_category_master(weeks: list[WeekFiles]) -> dict[str, str]:
    frames = []
    for w in weeks:
        for key in ("shohin_shukka", "shohin_shuppinmachi"):
            raw = w.files.get(key)
            if raw is None:
                continue
            df = read_csv_bytes(raw)
            if "商品ID" not in df.columns or "カテゴリ" not in df.columns:
                continue
            sub = df[["商品ID", "カテゴリ"]].copy()
            sub["_norm_id"] = sub["商品ID"].map(normalize_product_id)
            frames.append(sub[["_norm_id", "カテゴリ"]])
    if not frames:
        return {}
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.dropna(subset=["_norm_id"])
    all_df = all_df[all_df["カテゴリ"].notna() & (all_df["カテゴリ"].astype(str).str.strip() != "")]
    # 同一IDに複数カテゴリがある場合は最初に現れたものを採用
    master = all_df.drop_duplicates(subset=["_norm_id"], keep="first").set_index("_norm_id")["カテゴリ"]
    return master.to_dict()


# 問合せ(inquiry_count, 種別=CS)の対象をさらに絞り込むための「商品名・案件名」フィルタ。
# ユーザー指示は「案件名に『出荷前CS』『出品中商品』と記載あるもので絞って」だが、実データ
# には完全一致するものがほとんど無く、実際には先頭タグが《出荷・引渡前CS》(「前CS」を含む)
# または【出品中商品質問】等(「出品中商品」を含む)の形で入っている(実データで確認済み)。
# そのため「前CS」を含む、または「出品中商品」を含む、の部分一致OR条件として実装する。
# ※ sr_count(種別=SR)にはこのフィルタは適用しない(ユーザー指示は問合せのみが対象)。
INQUIRY_NAME_FILTER_KEYWORDS = ["前CS", "出品中商品"]


def _matches_inquiry_name_filter(series: pd.Series) -> pd.Series:
    name = series.fillna("").astype(str)
    mask = pd.Series(False, index=series.index)
    for kw in INQUIRY_NAME_FILTER_KEYWORDS:
        mask = mask | name.str.contains(kw, regex=False)
    return mask


def aggregate_cs_sr(weeks: list[WeekFiles], stats: ExclusionStats) -> pd.DataFrame:
    frames = []
    for w in weeks:
        raw = w.files.get("cs_bunruiyou") or w.files.get("cs_touroku")
        if raw is None:
            continue
        df = read_csv_bytes(raw)
        frames.append(tag_by_date(df, w, "登録"))
    if not frames:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category", "inquiry_count", "sr_count"]
        )

    all_df = concat_and_dedup(frames, id_col="CS ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    stats.cs_rows += int(excluded_mask.sum())
    all_df = all_df[~excluded_mask].copy()

    # 「ステータス」列が「スルー」の行は問合せ・SR件数の集計対象外とする
    through_mask = all_df["ステータス"].fillna("").astype(str).str.strip() == "スルー"
    stats.through_rows += int(through_mask.sum())
    all_df = all_df[~through_mask].copy()

    # 登録日時が有効な行のみを対象とする(基準日そのものは週フォルダの範囲を採用)
    valid_date = pd.to_datetime(all_df["登録"], errors="coerce").notna()
    all_df = all_df[valid_date].copy()
    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    all_df["種別"] = all_df["種別"].fillna("")

    group_cols = ["_week_start", "_week_end", "_year_month", "location", "category"]

    # 問合せ件数(種別=CS)は、上記に加えて「商品名・案件名」が「前CS」または「出品中商品」を
    # 含む行のみを対象とする。SR発生件数(種別=SR)にはこの案件名フィルタを適用しない。
    is_cs = all_df["種別"] == "CS"
    is_sr = all_df["種別"] == "SR"
    name_match = _matches_inquiry_name_filter(all_df["商品名・案件名"])
    cs_df = all_df[is_cs & name_match]
    sr_df = all_df[is_sr]

    cs_grouped = cs_df.groupby(group_cols).size().reset_index(name="inquiry_count")
    sr_grouped = sr_df.groupby(group_cols).size().reset_index(name="sr_count")
    grouped = cs_grouped.merge(sr_grouped, on=group_cols, how="outer")
    for col in ("inquiry_count", "sr_count"):
        if col not in grouped.columns:
            grouped[col] = 0
        grouped[col] = grouped[col].fillna(0)
    grouped = grouped.rename(
        columns={
            "_week_start": "week_start",
            "_week_end": "week_end",
            "_year_month": "year_month",
        }
    )
    return grouped[["week_start", "week_end", "year_month", "location", "category", "inquiry_count", "sr_count"]]


BUNRUI_RE = re.compile(r"^(SR|CS)\s*>\s*([^>]+?)\s*>\s*(.+)$")


def extract_major_category(bunrui_cell) -> Optional[str]:
    """「分類」列のセル値から大項目を抽出する。

    セル内に複数選択(改行区切り)がある場合は、セル内で最初(一番上)に
    書かれている選択を優先する。例:
        "SR > 配送 > 配送事故\\nSR > 配送 > 梱包不備" -> "配送"
        "SR > 商品説明 > 商品説明違い" -> "商品説明"
    """
    if bunrui_cell is None or (isinstance(bunrui_cell, float) and pd.isna(bunrui_cell)):
        return None
    s = str(bunrui_cell).strip()
    if not s or s.lower() == "nan":
        return None
    first_line = s.splitlines()[0].strip()
    m = BUNRUI_RE.match(first_line)
    if not m:
        return None
    return m.group(2).strip()


def extract_minor_category(bunrui_cell) -> Optional[str]:
    """「分類」列から小項目(3階層目)を抽出する。

    例: "SR > 商品説明 > 商品説明不足" -> "商品説明不足"
    大項目と同じく、複数選択(改行区切り)のセルは最初の1つを採用する。
    """
    if bunrui_cell is None or (isinstance(bunrui_cell, float) and pd.isna(bunrui_cell)):
        return None
    s = str(bunrui_cell).strip()
    if not s or s.lower() == "nan":
        return None
    m = BUNRUI_RE.match(s.splitlines()[0].strip())
    if not m:
        return None
    minor = m.group(3).strip()
    return minor or None


# CS_返金の「管理用メモ」に運用で記載される原因情報を取り出すための正規表現。
# 実データの書式:
#   【原因元】\n本体\n【原因分類】\n動作不備\n【原因詳細】\nハードディスクの不具合
# 空行が入る場合もあるため、タグの直後の最初の非空行を値として拾う。
MEMO_TAG_RE = {
    "cause_part": re.compile(r"【原因元】\s*\n\s*([^\n【]+)"),
    "cause_major": re.compile(r"【原因分類】\s*\n\s*([^\n【]+)"),
}


def parse_memo_cause(memo) -> tuple[Optional[str], Optional[str]]:
    """管理用メモから (原因分類, 原因元) を取り出す。無ければ (None, None)。"""
    if memo is None or (isinstance(memo, float) and pd.isna(memo)):
        return (None, None)
    text = str(memo)
    if "【原因" not in text:
        return (None, None)
    out = {}
    for key, rx in MEMO_TAG_RE.items():
        m = rx.search(text)
        v = m.group(1).strip() if m else None
        out[key] = v or None
    return (out.get("cause_major"), out.get("cause_part"))


def aggregate_sr_major(weeks: list[WeekFiles], stats: ExclusionStats) -> pd.DataFrame:
    """「分類」列(CS_登録【分類用】)のうち種別=SRの行のみを対象に、大項目(動作/付属品/
    商品説明/欠品/配送/返金など)の内訳を week×拠点×カテゴリ×大項目 で集計する。

    種別=CS の行(商品質問/支払質問など、SRとは別のカテゴリ体系)は最初から対象外とする。
    「分類」列自体が存在しない週(CS_登録【分類用】が無く通常のCS_登録のみの週)は
    集計対象外(その週はこの内訳には出現しない)。
    """
    frames = []
    for w in weeks:
        raw = w.files.get("cs_bunruiyou")
        if raw is None:
            continue
        df = read_csv_bytes(raw)
        if "分類" not in df.columns:
            continue
        frames.append(tag_by_date(df, w, "登録"))
    if not frames:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category", "major", "count"]
        )

    all_df = concat_and_dedup(frames, id_col="CS ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    all_df = all_df[~excluded_mask].copy()

    # 「ステータス」列が「スルー」の行は対象外とする
    through_mask = all_df["ステータス"].fillna("").astype(str).str.strip() == "スルー"
    stats.through_rows += int(through_mask.sum())
    all_df = all_df[~through_mask].copy()

    valid_date = pd.to_datetime(all_df["登録"], errors="coerce").notna()
    all_df = all_df[valid_date].copy()

    # 種別=SR の行のみを対象とする(CS種別はSR分類の内訳に含めない)
    all_df = all_df[all_df["種別"].fillna("").astype(str).str.strip() == "SR"].copy()

    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    all_df["major"] = all_df["分類"].map(extract_major_category)
    all_df["minor"] = all_df["分類"].map(extract_minor_category).fillna("(小項目なし)")
    all_df = all_df[all_df["major"].notna()].copy()

    grouped = (
        all_df.groupby(["_week_start", "_week_end", "_year_month", "location", "category", "major", "minor"])
        .size()
        .reset_index(name="count")
        .rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})
    )
    return grouped


def aggregate_cause_from_refund(weeks: list[WeekFiles]) -> pd.DataFrame:
    """CS_返金の「管理用メモ」に記載された原因(原因分類・原因元)を集計する。

    運用変更(2026年8月〜)により、返金確定時に管理用メモの先頭へ
    【原因元】【原因分類】【原因詳細】を記載する運用になったため、こちらを主データとする。
    返金額・返送料も同時に集計し、「どの不備がいくらの損失になっているか」を金額で見られるようにする。
    メモに記載が無い週・行は対象外(この関数は0行を返し、呼び出し側が分類用ファイルで補う)。
    """
    frames = []
    for w in weeks:
        raw = w.files.get("cs_henkin")
        if raw is None:
            continue
        df = read_csv_bytes(raw)
        if "管理用メモ" not in df.columns:
            continue
        frames.append(tag_by_date(df, w, "返金日"))
    cols = ["week_start", "week_end", "year_month", "location", "category",
            "cause_major", "cause_part", "count", "refund_amount"]
    if not frames:
        return pd.DataFrame(columns=cols)

    all_df = concat_and_dedup(frames, id_col="CS ID")
    all_df = all_df[~all_df["拠点"].map(is_excluded_location)].copy()
    all_df = all_df[pd.to_datetime(all_df["返金日"], errors="coerce").notna()].copy()
    parsed = all_df["管理用メモ"].map(parse_memo_cause)
    all_df["cause_major"] = parsed.map(lambda t: t[0])
    all_df["cause_part"] = parsed.map(lambda t: t[1]).fillna("(不明)")
    all_df = all_df[all_df["cause_major"].notna()].copy()
    if all_df.empty:
        return pd.DataFrame(columns=cols)
    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    all_df["_amt"] = to_excl_tax(to_numeric(all_df["返金額"]))
    grouped = (
        all_df.groupby(["_week_start", "_week_end", "_year_month", "location", "category",
                        "cause_major", "cause_part"])
        .agg(count=("_amt", "size"), refund_amount=("_amt", "sum"))
        .reset_index()
        .rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})
    )
    return grouped


def aggregate_cause(weeks: list[WeekFiles], stats: ExclusionStats) -> pd.DataFrame:
    """「原因分類・原因元」列(CS_登録【分類用】、現状はカメラ・カメラ周辺機器のSRのみ入力あり)を
    week×拠点×カテゴリ×原因分類×原因元 で集計する。
    """
    frames = []
    for w in weeks:
        raw = w.files.get("cs_bunruiyou")
        if raw is None:
            continue
        df = read_csv_bytes(raw)
        if not all(c in df.columns for c in ("原因分類", "原因元")):
            continue
        frames.append(tag_by_date(df, w, "登録"))
    if not frames:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category", "cause_major", "cause_part", "count"]
        )

    all_df = concat_and_dedup(frames, id_col="CS ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    all_df = all_df[~excluded_mask].copy()

    # 「ステータス」列が「スルー」の行は対象外とする
    through_mask = all_df["ステータス"].fillna("").astype(str).str.strip() == "スルー"
    stats.through_rows += int(through_mask.sum())
    all_df = all_df[~through_mask].copy()

    valid_date = pd.to_datetime(all_df["登録"], errors="coerce").notna()
    all_df = all_df[valid_date].copy()

    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    all_df = all_df[
        all_df["原因分類"].notna() & (all_df["原因分類"].astype(str).str.strip() != "")
    ].copy()
    all_df["cause_major"] = all_df["原因分類"].astype(str).str.strip()
    all_df["cause_part"] = all_df["原因元"].fillna("(不明)").astype(str).str.strip().replace("", "(不明)")

    grouped = (
        all_df.groupby(["_week_start", "_week_end", "_year_month", "location", "category", "cause_major", "cause_part"])
        .size()
        .reset_index(name="count")
        .rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})
    )
    return grouped


def aggregate_refund(
    weeks: list[WeekFiles], cost_master: dict[str, tuple[float, float]], stats: ExclusionStats
) -> pd.DataFrame:
    frames = []
    for w in weeks:
        raw = w.files.get("cs_henkin")
        if raw is None:
            continue
        frames.append(tag_by_date(read_csv_bytes(raw), w, "返金日"))
    if not frames:
        return pd.DataFrame(
            columns=[
                "week_start", "week_end", "year_month", "location", "category",
                "refund_amount", "refund_count", "return_shipping_cost",
            ]
        )

    all_df = concat_and_dedup(frames, id_col="CS ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    stats.henkin_rows += int(excluded_mask.sum())
    all_df = all_df[~excluded_mask].copy()

    valid_date = pd.to_datetime(all_df["返金日"], errors="coerce").notna()
    skipped_no_date = int((~valid_date).sum())
    if skipped_no_date:
        print(f"[警告] CS_返金: 返金日が空/不正のため集計対象外とした行数 = {skipped_no_date}")
    all_df = all_df[valid_date].copy()

    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    # 返金額は税込で記録されているため税抜に換算して保持する(表示・率とも税抜で統一)
    all_df["返金額_num"] = to_excl_tax(to_numeric(all_df["返金額"]))

    # 返品欄が「あり」の場合、返送料(ヤフオク配送料そのもの。÷1.1しない)も最終利益から差し引く
    all_df["_norm_id"] = all_df["商品ID"].map(normalize_product_id)
    is_return = all_df["返品"].fillna("").astype(str).str.strip() == "あり"

    def _shipping_fee(norm_id):
        # 返送料(ヤフオク配送料)も税込のため税抜に換算する
        if norm_id is None:
            return 0.0
        info = cost_master.get(norm_id)
        return to_excl_tax(info[1]) if info else 0.0

    all_df["返送料_num"] = 0.0
    all_df.loc[is_return, "返送料_num"] = all_df.loc[is_return, "_norm_id"].map(_shipping_fee)

    grouped = (
        all_df.groupby(["_week_start", "_week_end", "_year_month", "location", "category"])
        .agg(
            refund_amount=("返金額_num", "sum"),
            refund_count=("CS ID", "size"),
            return_shipping_cost=("返送料_num", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "_week_start": "week_start",
                "_week_end": "week_end",
                "_year_month": "year_month",
            }
        )
    )
    return grouped


# 「対応部署」列の選び方:
#   質問_登録【分類用】には「対応部署」(元データそのまま)と
#   「対応部署(J列参照。順次修正)」(精査済み)の2列が存在する。
#   内部処理では常に後者(精査済み)を優先して参照する。カッコ内の文言は将来変わりうるため、
#   列名の完全一致ではなく「『対応部署』で始まり、かつ『対応部署』と完全一致ではない」列を
#   正規表現で探して優先採用する。該当が無ければ通常の「対応部署」列にフォールバックする。
#   ※ダッシュボード上のラベル表記は将来にわたって常に「対応部署」とする(内部列名はUIに出さない)。
DEPT_COLUMN_RE = re.compile(r"^対応部署.+")


def pick_dept_column(columns) -> Optional[str]:
    """DataFrameの列名一覧から、内部処理で使うべき「対応部署」列名を返す。

    優先度: 「対応部署」で始まる非完全一致列(精査済み) > 「対応部署」(完全一致) > None
    """
    cols = list(columns)
    for c in cols:
        if isinstance(c, str) and DEPT_COLUMN_RE.match(c.strip()):
            return c
    for c in cols:
        if isinstance(c, str) and c.strip() == "対応部署":
            return c
    return None


def aggregate_question(
    weeks: list[WeekFiles], category_master: dict[str, str], stats: ExclusionStats
) -> pd.DataFrame:
    """質問_登録(ヤフオク出品中商品への質問)を週×拠点×カテゴリで集計する。

    週ごとに「質問_登録【分類用】」があればそちらを優先して読み、無ければ通常版
    「質問_登録」にフォールバックする(CS_登録 / CS_登録【分類用】と同じ方針)。
    【分類用】ファイルは「カテゴリ」列を直接持つため商品マスタ(category_master)突合は行わず、
    その列をそのまま使う。通常版しかない週は従来どおり商品ID→商品マスタ突合でカテゴリを決める。
    拠点除外(CSセンター・鳥取・北関東)はどちらのファイルでも同じ is_excluded_location を適用する。
    """
    frames = []
    for w in weeks:
        raw = w.files.get("shitsumon_bunruiyou")
        is_bunruiyou = raw is not None
        if raw is None:
            raw = w.files.get("shitsumon")
        if raw is None:
            continue
        df = tag_by_date(read_csv_bytes(raw), w, "登録")
        # 「対応部署」の内部保持列(dept)。将来の対応部署別集計にすぐ使えるように保持するだけで、
        # 現時点ではETL・ダッシュボードのどの集計・表示にも使っていない。
        dept_col = pick_dept_column(df.columns)
        df["dept"] = df[dept_col].fillna("(不明)") if dept_col else "(不明)"
        if is_bunruiyou and "カテゴリ" in df.columns:
            # 【分類用】: ファイル内の「カテゴリ」列をそのまま採用(商品マスタ突合なし)
            df["category"] = df["カテゴリ"].fillna("不明").replace("", "不明")
        else:
            # 通常版: 商品ID(正規化)→商品マスタでカテゴリを突合(従来ロジック)
            df["category"] = df["商品ID"].map(normalize_product_id).map(category_master).fillna("不明")
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["week_start", "week_end", "year_month", "location", "category", "question_count"])

    all_df = concat_and_dedup(frames, id_col="質問ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    stats.shitsumon_rows += int(excluded_mask.sum())
    all_df = all_df[~excluded_mask].copy()

    valid_date = pd.to_datetime(all_df["登録"], errors="coerce").notna()
    all_df = all_df[valid_date].copy()
    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["category"].fillna("不明")

    grouped = (
        all_df.groupby(["_week_start", "_week_end", "_year_month", "location", "category"])
        .size()
        .reset_index(name="question_count")
        .rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})
    )
    return grouped


def _read_and_merge_shipped_raw(weeks: list[WeekFiles]) -> pd.DataFrame:
    """受注_通常_出荷と受注_JPON_出荷を週次で結合するための下準備(CSV読み込み・重複除去・
    受注IDでの結合)のみを行うヘルパー。全期間分のCSV読み込みが実行時間の大半を占めるため、
    run_stage.py 側でこの結果だけを個別にチェックポイントできるように aggregate_shipped_and_sales
    から分離した(45秒/呼び出しの制約対策。業務ロジックは変更していない)。
    """
    tsujo_frames = []
    jpon_frames = []
    for w in weeks:
        tsujo_raw = w.files.get("juchu_tsujo")
        jpon_raw = w.files.get("juchu_jpon")
        if tsujo_raw is not None:
            tsujo_frames.append(tag_by_date(read_csv_bytes(tsujo_raw), w, "出荷予定日"))
        if jpon_raw is not None:
            jpon_frames.append(read_csv_bytes(jpon_raw))

    if not tsujo_frames or not jpon_frames:
        return pd.DataFrame()

    tsujo = concat_and_dedup(tsujo_frames, id_col="受注ID")
    jpon = concat_and_dedup(jpon_frames, id_col="オークションID")

    tsujo = tsujo[["受注ID", "拠点", "出荷予定日", "_week_start", "_week_end", "_year_month"]].copy()
    jpon = jpon[["取引番号", "管理番号", "落札金額"]].copy()

    merged = tsujo.merge(jpon, left_on="受注ID", right_on="取引番号", how="inner")
    return merged


def aggregate_shipped_and_sales(
    weeks: list[WeekFiles],
    category_master: dict[str, str],
    cost_master: dict[str, tuple[float, float]],
    status_master: dict[str, str],
    stats: ExclusionStats,
    _merged_raw: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    empty_cols = [
        "week_start", "week_end", "year_month", "location", "category",
        "shipped_count", "sales_amount", "gross_profit", "junk_shipped_count",
    ]
    merged = _merged_raw if _merged_raw is not None else _read_and_merge_shipped_raw(weeks)
    if merged is None or merged.empty:
        return pd.DataFrame(columns=empty_cols)
    merged = merged.copy()

    excluded_mask = merged["拠点"].map(is_excluded_location)
    stats.juchu_rows += int(excluded_mask.sum())
    merged = merged[~excluded_mask].copy()

    # 出荷予定日が有効な行のみを対象とする(基準日そのものは週フォルダの範囲を採用)
    valid_date = pd.to_datetime(merged["出荷予定日"], errors="coerce").notna()
    merged = merged[valid_date].copy()

    merged["location"] = merged["拠点"].fillna("(不明)")
    merged["_norm_id"] = merged["管理番号"].map(normalize_product_id)
    merged["category"] = merged["_norm_id"].map(category_master).fillna("不明")
    merged["落札金額_num"] = to_numeric(merged["落札金額"])

    # 粗利 = 落札価格 - 買取価格/1.1 - ヤフオク配送料/1.1
    def _cost_pair(norm_id):
        if norm_id is None:
            return (0.0, 0.0)
        return cost_master.get(norm_id, (0.0, 0.0))

    cost_pairs = merged["_norm_id"].map(_cost_pair)
    merged["買取価格_num"] = cost_pairs.map(lambda t: t[0])
    merged["配送料_num"] = cost_pairs.map(lambda t: t[1])
    merged["粗利_row"] = merged["落札金額_num"] - merged["買取価格_num"] / 1.1 - merged["配送料_num"] / 1.1
    merged["is_junk"] = merged["_norm_id"].map(lambda nid: is_junk_status(status_master.get(nid) if nid else None))

    grouped = (
        merged.groupby(["_week_start", "_week_end", "_year_month", "location", "category"])
        .agg(
            shipped_count=("受注ID", "size"),
            sales_amount=("落札金額_num", "sum"),
            gross_profit=("粗利_row", "sum"),
            junk_shipped_count=("is_junk", "sum"),
        )
        .reset_index()
        .rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})
    )
    return grouped


def aggregate_listed(weeks: list[WeekFiles], stats: ExclusionStats) -> pd.DataFrame:
    frames = []
    for w in weeks:
        raw = w.files.get("shohin_shuppinmachi")
        if raw is None:
            continue
        frames.append(tag_by_date(read_csv_bytes(raw), w, "出品待"))
    if not frames:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category", "listed_count", "junk_listed_count"]
        )

    all_df = concat_and_dedup(frames, id_col="商品ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    stats.shuppinmachi_rows += int(excluded_mask.sum())
    all_df = all_df[~excluded_mask].copy()

    valid_date = pd.to_datetime(all_df["出品待"], errors="coerce").notna()
    all_df = all_df[valid_date].copy()
    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    all_df["is_junk"] = all_df["状態"].map(is_junk_status) if "状態" in all_df.columns else False

    grouped = (
        all_df.groupby(["_week_start", "_week_end", "_year_month", "location", "category"])
        .agg(listed_count=("商品ID", "size"), junk_listed_count=("is_junk", "sum"))
        .reset_index()
        .rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})
    )
    return grouped


METRIC_COLUMNS = [
    "inquiry_count",
    "sr_count",
    "refund_amount",
    "refund_count",
    "return_shipping_cost",
    "sales_amount",
    "gross_profit",
    "question_count",
    "shipped_count",
    "listed_count",
    "junk_shipped_count",
    "junk_listed_count",
]


# ---------------------------------------------------------------------------
# コンディションランク別・価格帯別・粗利差異分析
# 商品_出荷(JPONベース)には出荷済み商品1件ごとに 状態(コンディションランク)・
# 買取価格・販売価格(見込み)・落札価格(実績)・カテゴリ・拠点が直接含まれているため、
# これらは他ファイルとの結合なしにこのファイル単独で集計できる。
# ---------------------------------------------------------------------------


def price_band_of(v: float) -> tuple[str, int]:
    """落札価格を10万円単位のバンドに分類する。(表示ラベル, 並び替え用キー) を返す。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ("不明", -1)
    if v < 0:
        return ("0万円未満", -1)
    bucket = int(v // 100000)
    lo, hi = bucket * 10, bucket * 10 + 10
    return (f"{lo}〜{hi}万円", bucket)


SHUKKA_DETAIL_COLUMNS = [
    "week_start", "week_end", "year_month", "location", "category", "condition",
    "price_band", "price_band_sort", "落札価格_num", "販売価格_num", "買取価格_num",
    "expected_profit", "actual_profit", "variance",
    # 以下、種別(仕入れ方法)・送料・リードタイム分析用に追加した列
    "procurement_type", "shipping_fee", "lead_days", "norm_product_id",
]


def build_shukka_detail(
    weeks: list[WeekFiles],
    stats: ExclusionStats,
    shipping_fee_master: Optional[dict[str, float]] = None,
    ship_date_master: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """商品_出荷(JPONベース)を週次で結合・整形した明細を返す。

    列: week_start/week_end/year_month/location/category/condition/
        price_band/price_band_sort/落札価格_num/販売価格_num/買取価格_num/
        expected_profit(見込み粗利=販売価格-買取価格/1.1)/
        actual_profit(実粗利=落札価格-買取価格/1.1)/
        variance(実粗利-見込み粗利=落札価格-販売価格)/
        procurement_type(「種別」列=出張/店頭/宅配/迷中/不明)/
        shipping_fee(A項目対応: 「ヤフオク配送料」列を基準に、数値ならそのまま採用、
            "らくらく家財便"の場合は shipping_fee_master(build_shipping_fee_master参照)
            から受注_通常_出荷の実際の送料を引いて採用、"直引"の場合は0円、
            その他の非数値・空値は0円)/
        lead_days(「落札」-「買取」の日数。いずれかが不正な日付の場合はNaN)/
        norm_product_id(商品ID正規化キー。赤字分析でCS_返金と突合するために保持)
    """
    shipping_fee_master = shipping_fee_master or {}
    frames = []
    for w in weeks:
        raw = w.files.get("shohin_shukka")
        if raw is None:
            continue
        frames.append(tag_week(read_csv_bytes(raw), w))
    needed = ["商品ID", "拠点", "カテゴリ", "状態", "買取価格", "販売価格", "落札価格", "種別", "ヤフオク配送料", "買取", "落札"]
    if not frames or not all(c in frames[0].columns for c in needed):
        return pd.DataFrame(columns=SHUKKA_DETAIL_COLUMNS)

    all_df = concat_and_dedup(frames, id_col="商品ID")
    excluded_mask = all_df["拠点"].map(is_excluded_location)
    stats.shukka_rows += int(excluded_mask.sum())
    all_df = all_df[~excluded_mask].copy()

    all_df["location"] = all_df["拠点"].fillna("(不明)")
    all_df["category"] = all_df["カテゴリ"].fillna("不明").replace("", "不明")
    all_df["condition"] = all_df["状態"].map(normalize_condition)
    all_df["落札価格_num"] = to_numeric(all_df["落札価格"])
    all_df["販売価格_num"] = to_numeric(all_df["販売価格"])
    all_df["買取価格_num"] = to_numeric(all_df["買取価格"])
    # 税抜で統一する: 販売価格・買取価格は税込のため÷1.1、落札価格は税抜なのでそのまま。
    all_df["販売価格_税抜"] = to_excl_tax(all_df["販売価格_num"])
    all_df["買取価格_税抜"] = to_excl_tax(all_df["買取価格_num"])
    all_df["expected_profit"] = all_df["販売価格_税抜"] - all_df["買取価格_税抜"]
    all_df["actual_profit"] = all_df["落札価格_num"] - all_df["買取価格_税抜"]
    all_df["variance"] = all_df["actual_profit"] - all_df["expected_profit"]
    bands = all_df["落札価格_num"].map(price_band_of)
    all_df["price_band"] = bands.map(lambda t: t[0])
    all_df["price_band_sort"] = bands.map(lambda t: t[1])

    # 仕入れ方法(種別): 出張/店頭/宅配/迷中。空・不明値は「不明」に統一する。
    all_df["procurement_type"] = all_df["種別"].fillna("不明").astype(str).str.strip().replace("", "不明")
    all_df["norm_product_id"] = all_df["商品ID"].map(normalize_product_id)

    # 期間キーを「出荷予定日」基準の日次に置き換える(①〜④ページと同じ日付基準に揃えるため)。
    # 出荷予定日は受注_通常_出荷にしか無いので ship_date_master(2段結合)で引く。
    # 引けない行(受注_JPON_出荷が無い週など)は 落札日 → 週フォルダ終了日 の順にフォールバックする。
    ship_date_master = ship_date_master or {}
    ymd = all_df["norm_product_id"].map(lambda nid: ship_date_master.get(nid) if nid else None)
    ymd = pd.Series(ymd, index=all_df.index)
    fb_win = pd.to_datetime(all_df["落札"], errors="coerce").dt.strftime("%Y-%m-%d")
    ymd = ymd.fillna(fb_win).fillna(all_df["_week_end"])
    all_df["_week_start"] = ymd
    all_df["_week_end"] = ymd
    all_df["_year_month"] = ymd.str.slice(0, 7)

    # A項目対応: 発送送料(shipping_fee)は「ヤフオク配送料」列を基準に判定する。
    #   - 数値ならそのまま採用
    #   - "らくらく家財便"の場合は shipping_fee_master(商品ID正規化キー -> 受注_通常_出荷の
    #     実際の送料)から引いた値を採用(無ければ0)
    #   - "直引"の場合は0円
    #   - その他の非数値・空値は0円
    yahuoku_ship_raw = all_df["ヤフオク配送料"]
    yahuoku_ship_str = yahuoku_ship_raw.fillna("").astype(str).str.strip()
    is_rakuraku = yahuoku_ship_str == "らくらく家財便"
    # to_numeric()は"らくらく家財便"'直引'等の非数値文字列もエラー→0に変換するので、
    # これをベースにしておけば「直引」「その他の非数値・空値」は自動的に0円になる。
    all_df["shipping_fee"] = to_numeric(yahuoku_ship_raw)
    if is_rakuraku.any() and shipping_fee_master:
        looked_up = all_df.loc[is_rakuraku, "norm_product_id"].map(
            lambda nid: shipping_fee_master.get(nid, 0.0) if nid else 0.0
        )
        all_df.loc[is_rakuraku, "shipping_fee"] = looked_up
    # ヤフオク配送料・受注_通常_出荷の送料はいずれも税込なので税抜に換算する
    all_df["shipping_fee"] = to_excl_tax(all_df["shipping_fee"])

    # リードタイム = 落札日 - 買取日(日数)。いずれかが不正な日付の場合はNaN。
    buy_dt = pd.to_datetime(all_df["買取"], errors="coerce")
    win_dt = pd.to_datetime(all_df["落札"], errors="coerce")
    all_df["lead_days"] = (win_dt - buy_dt).dt.days

    return all_df[
        [
            "_week_start", "_week_end", "_year_month", "location", "category", "condition",
            "price_band", "price_band_sort", "落札価格_num", "販売価格_num", "買取価格_num",
            "expected_profit", "actual_profit", "variance",
            "procurement_type", "shipping_fee", "lead_days", "norm_product_id",
        ]
    ].rename(columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"})


def build_product_attr_master(weeks: list[WeekFiles]) -> dict[str, tuple[str, str, int]]:
    """商品ID(数字部分) -> (状態, 価格帯ラベル, 価格帯ソート値) のマスタを作成する。

    コンディション別・価格帯別のページで、SR/問合せ/返金/質問といった
    「商品_出荷(JPONベース)には無いデータ」を状態・価格帯に紐づけるために使う。
    CS_登録・CS_返金・質問_登録はいずれも商品ID列を持つので、このマスタに突合して
    状態と価格帯を後付けする。

    価格の基準は「落札価格」(売れた実績価格)を優先し、未売却などで落札価格が無い場合は
    「販売価格」(出品価格)にフォールバックする。どちらも無い場合は価格帯「不明」。
    商品_出荷(JPONベース)・商品_出品待の両方から集め、同一IDは最初に現れたものを採用する
    (build_product_status_master 等と同じ方針)。
    """
    frames = []
    for w in weeks:
        for key in ("shohin_shukka", "shohin_shuppinmachi"):
            raw = w.files.get(key)
            if raw is None:
                continue
            df = read_csv_bytes(raw)
            if "商品ID" not in df.columns or "状態" not in df.columns:
                continue
            cols = ["商品ID", "状態"]
            for c in ("落札価格", "販売価格"):
                if c in df.columns:
                    cols.append(c)
            sub = df[cols].copy()
            if "落札価格" not in sub.columns:
                sub["落札価格"] = None
            if "販売価格" not in sub.columns:
                sub["販売価格"] = None
            sub["_norm_id"] = sub["商品ID"].map(normalize_product_id)
            frames.append(sub[["_norm_id", "状態", "落札価格", "販売価格"]])
    if not frames:
        return {}
    all_df = pd.concat(frames, ignore_index=True).dropna(subset=["_norm_id"])
    all_df = all_df.drop_duplicates(subset=["_norm_id"], keep="first")
    win = to_numeric(all_df["落札価格"])
    sell = to_numeric(all_df["販売価格"])
    price = win.where(win > 0, sell)
    bands = price.map(lambda v: price_band_of(v) if v and v > 0 else ("不明", -1))
    cond = all_df["状態"].map(normalize_condition)
    return dict(
        zip(
            all_df["_norm_id"].tolist(),
            zip(cond.tolist(), bands.map(lambda t: t[0]).tolist(), bands.map(lambda t: t[1]).tolist()),
        )
    )


UNKNOWN_ATTR = ("不明", "不明", -1)


def _attach_attrs(df: pd.DataFrame, attr_master: dict[str, tuple[str, str, int]]) -> pd.DataFrame:
    """商品ID列から状態・価格帯を付与する。マスタに無い商品は「不明」に集約する。"""
    df = df.copy()
    norm = df["商品ID"].map(normalize_product_id) if "商品ID" in df.columns else pd.Series([None] * len(df), index=df.index)
    attrs = norm.map(lambda nid: attr_master.get(nid, UNKNOWN_ATTR) if nid else UNKNOWN_ATTR)
    df["condition"] = attrs.map(lambda t: normalize_condition(t[0]))
    df["price_band"] = attrs.map(lambda t: t[1])
    df["price_band_sort"] = attrs.map(lambda t: t[2])
    return df


def aggregate_condition_price_metrics(
    weeks: list[WeekFiles], attr_master: dict[str, tuple[str, str, int]]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """コンディション別・価格帯別の 問合せ/SR/返金/質問/出品 を日次集計する。

    ①〜④ページと同じ定義・同じ基準日を使い、切り口(状態・価格帯)だけを追加したもの:
      - 問合せ(種別=CS、案件名フィルタあり)・SR(種別=SR) : CS_登録の「登録」日
      - 返金額・返金件数                                   : CS_返金の「返金日」
      - 質問数                                             : 質問_登録の「登録」日
      - 出品数                                             : 商品_出品待の「出品待」日
    出品数のみ、商品_出品待ファイル自身が状態・販売価格を持つのでマスタ突合は不要。
    戻り値: (コンディション別DataFrame, 価格帯別DataFrame)
    """
    base = ["_week_start", "_week_end", "_year_month", "location", "category"]
    cond_parts, band_parts = [], []

    def _push(df: pd.DataFrame, value_cols: dict):
        for keys, parts in ((["condition"], cond_parts), (["price_band", "price_band_sort"], band_parts)):
            g = df.groupby(base + keys, dropna=False).agg(**value_cols).reset_index()
            parts.append(g)

    # --- CS_登録: 問合せ・SR ---
    frames = []
    for w in weeks:
        raw = w.files.get("cs_bunruiyou") or w.files.get("cs_touroku")
        if raw is not None:
            frames.append(tag_by_date(read_csv_bytes(raw), w, "登録"))
    if frames:
        df = concat_and_dedup(frames, id_col="CS ID")
        df = df[~df["拠点"].map(is_excluded_location)]
        df = df[df["ステータス"].fillna("").astype(str).str.strip() != "スルー"]
        df = df[pd.to_datetime(df["登録"], errors="coerce").notna()].copy()
        df["location"] = df["拠点"].fillna("(不明)")
        df["category"] = df["カテゴリ"].fillna("不明").replace("", "不明")
        df = _attach_attrs(df, attr_master)
        shubetsu = df["種別"].fillna("").astype(str).str.strip()
        df["_sr"] = (shubetsu == "SR").astype(int)
        df["_cs"] = ((shubetsu == "CS") & _matches_inquiry_name_filter(df["商品名・案件名"])).astype(int)
        _push(df, {"sr_count": ("_sr", "sum"), "inquiry_count": ("_cs", "sum")})

    # --- CS_返金: 返金件数・返金額 ---
    frames = []
    for w in weeks:
        raw = w.files.get("cs_henkin")
        if raw is not None:
            frames.append(tag_by_date(read_csv_bytes(raw), w, "返金日"))
    if frames:
        df = concat_and_dedup(frames, id_col="CS ID")
        df = df[~df["拠点"].map(is_excluded_location)]
        df = df[pd.to_datetime(df["返金日"], errors="coerce").notna()].copy()
        df["location"] = df["拠点"].fillna("(不明)")
        df["category"] = df["カテゴリ"].fillna("不明").replace("", "不明")
        df = _attach_attrs(df, attr_master)
        df["_amt"] = to_numeric(df["返金額"])
        df["_one"] = 1
        _push(df, {"refund_count": ("_one", "sum"), "refund_amount": ("_amt", "sum")})

    # --- 質問_登録: 質問数 ---
    frames = []
    for w in weeks:
        raw = w.files.get("shitsumon_bunruiyou") or w.files.get("shitsumon")
        if raw is not None:
            frames.append(tag_by_date(read_csv_bytes(raw), w, "登録"))
    if frames:
        df = concat_and_dedup(frames, id_col="質問ID")
        df = df[~df["拠点"].map(is_excluded_location)]
        df = df[pd.to_datetime(df["登録"], errors="coerce").notna()].copy()
        df["location"] = df["拠点"].fillna("(不明)")
        df = _attach_attrs(df, attr_master)
        if "カテゴリ" in df.columns:
            df["category"] = df["カテゴリ"].fillna("不明").replace("", "不明")
        else:
            df["category"] = "不明"
        df["_one"] = 1
        _push(df, {"question_count": ("_one", "sum")})

    # --- 商品_出品待: 出品数(ファイル自身が状態・販売価格を持つ) ---
    frames = []
    for w in weeks:
        raw = w.files.get("shohin_shuppinmachi")
        if raw is not None:
            frames.append(tag_by_date(read_csv_bytes(raw), w, "出品待"))
    if frames:
        df = concat_and_dedup(frames, id_col="商品ID")
        df = df[~df["拠点"].map(is_excluded_location)]
        df = df[pd.to_datetime(df["出品待"], errors="coerce").notna()].copy()
        df["location"] = df["拠点"].fillna("(不明)")
        df["category"] = df["カテゴリ"].fillna("不明").replace("", "不明")
        df["condition"] = df["状態"].map(normalize_condition)
        price = to_numeric(df["落札価格"]).where(to_numeric(df["落札価格"]) > 0, to_numeric(df["販売価格"]))
        bands = price.map(lambda v: price_band_of(v) if v and v > 0 else ("不明", -1))
        df["price_band"] = bands.map(lambda t: t[0])
        df["price_band_sort"] = bands.map(lambda t: t[1])
        df["_one"] = 1
        _push(df, {"listed_count": ("_one", "sum")})

    def _merge(parts, keys):
        if not parts:
            return pd.DataFrame(columns=base + keys)
        out = parts[0]
        for x in parts[1:]:
            out = out.merge(x, on=base + keys, how="outer")
        for c in out.columns:
            if c not in base + keys:
                out[c] = out[c].fillna(0)
        return out.rename(
            columns={"_week_start": "week_start", "_week_end": "week_end", "_year_month": "year_month"}
        )

    return _merge(cond_parts, ["condition"]), _merge(band_parts, ["price_band", "price_band_sort"])


def aggregate_condition(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category", "condition",
                     "count", "sales_amount", "gross_profit"]
        )
    grouped = (
        detail.groupby(["week_start", "week_end", "year_month", "location", "category", "condition"])
        .agg(count=("落札価格_num", "size"), sales_amount=("落札価格_num", "sum"), gross_profit=("actual_profit", "sum"))
        .reset_index()
    )
    return grouped


def aggregate_price_band(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category",
                     "price_band", "price_band_sort", "count", "sales_amount", "gross_profit"]
        )
    grouped = (
        detail.groupby(["week_start", "week_end", "year_month", "location", "category",
                         "price_band", "price_band_sort"])
        .agg(count=("落札価格_num", "size"), sales_amount=("落札価格_num", "sum"), gross_profit=("actual_profit", "sum"))
        .reset_index()
    )
    return grouped


def aggregate_profit_variance(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            columns=["week_start", "week_end", "year_month", "location", "category", "count",
                     "expected_profit_sum", "actual_profit_sum", "variance_sum",
                     "upside_count", "upside_amount", "downside_count", "downside_amount"]
        )
    d = detail.copy()
    d["is_upside"] = d["variance"] > 0
    d["is_downside"] = d["variance"] < 0
    d["upside_amount"] = d["variance"].where(d["is_upside"], 0.0)
    d["downside_amount"] = d["variance"].where(d["is_downside"], 0.0)
    grouped = (
        d.groupby(["week_start", "week_end", "year_month", "location", "category"])
        .agg(
            count=("variance", "size"),
            expected_profit_sum=("expected_profit", "sum"),
            actual_profit_sum=("actual_profit", "sum"),
            variance_sum=("variance", "sum"),
            upside_count=("is_upside", "sum"),
            upside_amount=("upside_amount", "sum"),
            downside_count=("is_downside", "sum"),
            downside_amount=("downside_amount", "sum"),
        )
        .reset_index()
    )
    return grouped


def aggregate_variance_breakdown(detail: pd.DataFrame, dim: str) -> pd.DataFrame:
    """粗利差異を「コンディション別」「価格帯別」に分解する(⑥粗利差異ページ用)。

    上振れ(見込みより高く売れた)と下振れ(安く売れた)を分けて件数・金額を持たせ、
    どの状態・価格帯で値付けが外れているかを特定できるようにする。
    dim には "condition" または "price_band" を指定する。
    """
    base = ["week_start", "week_end", "year_month", "location", "category"]
    keys = base + ([dim, "price_band_sort"] if dim == "price_band" else [dim])
    cols = keys + ["count", "expected_profit_sum", "actual_profit_sum", "variance_sum",
                   "upside_count", "upside_amount", "downside_count", "downside_amount"]
    if detail.empty or dim not in detail.columns:
        return pd.DataFrame(columns=cols)
    d = detail.copy()
    d["is_upside"] = d["variance"] > 0
    d["is_downside"] = d["variance"] < 0
    d["upside_amount"] = d["variance"].where(d["is_upside"], 0.0)
    d["downside_amount"] = d["variance"].where(d["is_downside"], 0.0)
    grouped = (
        d.groupby(keys)
        .agg(
            count=("variance", "size"),
            expected_profit_sum=("expected_profit", "sum"),
            actual_profit_sum=("actual_profit", "sum"),
            variance_sum=("variance", "sum"),
            upside_count=("is_upside", "sum"),
            upside_amount=("upside_amount", "sum"),
            downside_count=("is_downside", "sum"),
            downside_amount=("downside_amount", "sum"),
        )
        .reset_index()
    )
    return grouped[cols]


def build_variance_breakdown_rows(detail: pd.DataFrame, dim: str) -> list[dict]:
    df = aggregate_variance_breakdown(detail, dim)
    if df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        rec = {
            "week_start": r["week_start"], "week_end": r["week_end"], "year_month": r["year_month"],
            "location": r["location"], "category": r["category"],
            "count": int(r["count"]),
            "expected_profit_sum": _safe_float(r["expected_profit_sum"]),
            "actual_profit_sum": _safe_float(r["actual_profit_sum"]),
            "variance_sum": _safe_float(r["variance_sum"]),
            "upside_count": int(r["upside_count"]), "upside_amount": _safe_float(r["upside_amount"]),
            "downside_count": int(r["downside_count"]), "downside_amount": _safe_float(r["downside_amount"]),
        }
        if dim == "price_band":
            rec["price_band"] = r["price_band"]
            rec["price_band_sort"] = int(r["price_band_sort"])
        else:
            rec["condition"] = r["condition"]
        out.append(rec)
    return out


CATEGORY_PROFIT_DETAIL_COLUMNS = [
    "week_start", "week_end", "year_month", "category",
    "count", "cost_amount", "sales_amount", "gross_profit", "variance_amount",
    "avg_lead_days", "margin_rate", "avg_sale_price", "avg_profit_price",
]


def _safe_float(v) -> Optional[float]:
    """NaN/Infを含む可能性のある値を、JSONとして安全なfloatまたはNoneに変換する。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f) or f in (float("inf"), float("-inf")):
        return None
    return f


def aggregate_category_profit_detail(detail: pd.DataFrame) -> pd.DataFrame:
    """週×カテゴリ(全社合算)の詳細粗利指標を集計する(build_shukka_detailの出力を再利用)。

    count(数量)/cost_amount(仕入額=買取価格_num/1.1の合計)/sales_amount(売上額=落札価格_num合計)/
    gross_profit(粗利額=actual_profitの合計)/variance_amount(粗利差異=varianceの合計)/
    avg_lead_days(リード=lead_daysの平均、NaNは除外)/margin_rate(粗利率)/
    avg_sale_price(販売単価)/avg_profit_price(粗利単価)。
    """
    if detail.empty:
        return pd.DataFrame(columns=CATEGORY_PROFIT_DETAIL_COLUMNS)

    grouped = (
        detail.groupby(["week_start", "week_end", "year_month", "category"])
        .agg(
            count=("落札価格_num", "size"),
            buy_price_sum=("買取価格_num", "sum"),
            sales_amount=("落札価格_num", "sum"),
            gross_profit=("actual_profit", "sum"),
            variance_amount=("variance", "sum"),
            avg_lead_days=("lead_days", "mean"),
        )
        .reset_index()
    )
    grouped["cost_amount"] = grouped["buy_price_sum"] / 1.1
    grouped["margin_rate"] = grouped.apply(
        lambda r: (r["gross_profit"] / r["sales_amount"]) if r["sales_amount"] else None, axis=1
    )
    grouped["avg_sale_price"] = grouped.apply(
        lambda r: (r["sales_amount"] / r["count"]) if r["count"] else None, axis=1
    )
    grouped["avg_profit_price"] = grouped.apply(
        lambda r: (r["gross_profit"] / r["count"]) if r["count"] else None, axis=1
    )
    return grouped[CATEGORY_PROFIT_DETAIL_COLUMNS]


def build_category_profit_detail_rows(detail: pd.DataFrame) -> list[dict]:
    df = aggregate_category_profit_detail(detail)
    if df.empty:
        return []
    df = df.sort_values(["week_start", "category"])
    return [
        {
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "year_month": r["year_month"],
            "category": r["category"],
            "count": int(r["count"]),
            "cost_amount": _safe_float(r["cost_amount"]),
            "sales_amount": _safe_float(r["sales_amount"]),
            "gross_profit": _safe_float(r["gross_profit"]),
            "variance_amount": _safe_float(r["variance_amount"]),
            "avg_lead_days": _safe_float(r["avg_lead_days"]),
            "margin_rate": _safe_float(r["margin_rate"]),
            "avg_sale_price": _safe_float(r["avg_sale_price"]),
            "avg_profit_price": _safe_float(r["avg_profit_price"]),
        }
        for _, r in df.iterrows()
    ]


def build_dashboard_rows(weeks: list[WeekFiles]) -> tuple[list[dict], ExclusionStats, pd.DataFrame]:
    stats = ExclusionStats()
    category_master = build_product_category_master(weeks)
    cost_master = build_product_cost_master(weeks)
    status_master = build_product_status_master(weeks)

    cs_sr = aggregate_cs_sr(weeks, stats)
    refund = aggregate_refund(weeks, cost_master, stats)
    question = aggregate_question(weeks, category_master, stats)
    shipped_sales = aggregate_shipped_and_sales(weeks, category_master, cost_master, status_master, stats)
    listed = aggregate_listed(weeks, stats)

    key_cols = ["week_start", "week_end", "year_month", "location", "category"]
    merged = cs_sr
    for other in (refund, question, shipped_sales, listed):
        merged = merged.merge(other, on=key_cols, how="outer")

    for col in METRIC_COLUMNS:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = merged[col].fillna(0)

    # 実績が完全に0の行(全指標0)は出力しない
    nonzero_mask = merged[METRIC_COLUMNS].abs().sum(axis=1) > 0
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
    return rows, stats, merged


def build_sr_major_rows(weeks: list[WeekFiles], stats: ExclusionStats) -> list[dict]:
    df = aggregate_sr_major(weeks, stats)
    if df.empty:
        return []
    df = df.sort_values(["week_start", "location", "category", "major", "minor"])
    return [
        {
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "year_month": r["year_month"],
            "location": r["location"],
            "category": r["category"],
            "major": r["major"],
            "minor": r.get("minor", "(小項目なし)"),
            "count": int(r["count"]),
        }
        for _, r in df.iterrows()
    ]


def build_cause_rows(weeks: list[WeekFiles], stats: ExclusionStats) -> list[dict]:
    """原因(原因分類×原因元)の行を作る。

    データ源は2つあり、週(基準日)ごとに次の優先順で採用する:
      1. CS_返金の「管理用メモ」に記載された原因(運用変更後。返金額も分かる)
      2. CS_登録【分類用】の「原因分類」「原因元」列(運用変更前を後から分類したもの)
    同じ週で二重計上しないよう、1が1件でもある週は1のみを使う。
    どちらの出所かは source 列で区別できるようにしておく。
    """
    memo_df = aggregate_cause_from_refund(weeks)
    file_df = aggregate_cause(weeks, stats)
    memo_weeks = set(memo_df["week_start"]) if not memo_df.empty else set()
    if not file_df.empty and memo_weeks:
        file_df = file_df[~file_df["week_start"].isin(memo_weeks)].copy()

    out = []
    for df, source in ((memo_df, "返金メモ"), (file_df, "分類用ファイル")):
        if df.empty:
            continue
        df = df.sort_values(["week_start", "location", "category", "cause_major", "cause_part"])
        for _, r in df.iterrows():
            out.append({
                "week_start": r["week_start"],
                "week_end": r["week_end"],
                "year_month": r["year_month"],
                "location": r["location"],
                "category": r["category"],
                "cause_major": r["cause_major"],
                "cause_part": r["cause_part"],
                "count": int(r["count"]),
                "refund_amount": float(r["refund_amount"]) if "refund_amount" in df.columns else 0.0,
                "source": source,
            })
    out.sort(key=lambda r: (r["week_start"], r["location"], r["category"], r["cause_major"]))
    return out


EXTRA_METRIC_COLS = [
    "inquiry_count", "sr_count", "refund_count", "refund_amount", "question_count", "listed_count",
]


def _merge_extra_metrics(df: pd.DataFrame, extra: Optional[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    """出荷ベースの集計(df)に、コンディション別・価格帯別の新指標(extra)を外部結合する。

    出荷が0でもSR・返金・質問・出品だけが存在する組み合わせ(例: まだ売れていない出品)も
    行として残す必要があるため how="outer" で結合し、欠損は0で埋める。
    """
    base = ["week_start", "week_end", "year_month", "location", "category"]
    if extra is None or extra.empty:
        out = df.copy()
        for c in EXTRA_METRIC_COLS:
            out[c] = 0
        return out
    out = df.merge(extra, on=base + keys, how="outer")
    for c in EXTRA_METRIC_COLS:
        if c not in out.columns:
            out[c] = 0
    fill = {c: 0 for c in out.columns if c not in base + keys}
    out = out.fillna(value=fill)
    if "price_band" in keys and "price_band_sort" in out.columns:
        out["price_band_sort"] = out["price_band_sort"].astype(int)
    return out


def build_condition_rows(detail: pd.DataFrame, extra: Optional[pd.DataFrame] = None) -> list[dict]:
    df = _merge_extra_metrics(aggregate_condition(detail), extra, ["condition"])
    if df.empty:
        return []
    df = df.sort_values(["week_start", "location", "category", "condition"])
    return [
        {
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "year_month": r["year_month"],
            "location": r["location"],
            "category": r["category"],
            "condition": r["condition"],
            "count": int(r["count"]),
            "shipped_count": int(r["count"]),
            "sales_amount": float(r["sales_amount"]),
            "gross_profit": float(r["gross_profit"]),
            **{c: (float(r[c]) if c.endswith("_amount") else int(r[c])) for c in EXTRA_METRIC_COLS},
        }
        for _, r in df.iterrows()
    ]


def build_price_band_rows(detail: pd.DataFrame, extra: Optional[pd.DataFrame] = None) -> list[dict]:
    df = _merge_extra_metrics(aggregate_price_band(detail), extra, ["price_band", "price_band_sort"])
    if df.empty:
        return []
    df = df.sort_values(["week_start", "location", "category", "price_band_sort"])
    return [
        {
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "year_month": r["year_month"],
            "location": r["location"],
            "category": r["category"],
            "price_band": r["price_band"],
            "price_band_sort": int(r["price_band_sort"]),
            "count": int(r["count"]),
            "shipped_count": int(r["count"]),
            "sales_amount": float(r["sales_amount"]),
            "gross_profit": float(r["gross_profit"]),
            **{c: (float(r[c]) if c.endswith("_amount") else int(r[c])) for c in EXTRA_METRIC_COLS},
        }
        for _, r in df.iterrows()
    ]


def build_profit_variance_rows(detail: pd.DataFrame) -> list[dict]:
    df = aggregate_profit_variance(detail)
    if df.empty:
        return []
    df = df.sort_values(["week_start", "location", "category"])
    return [
        {
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "year_month": r["year_month"],
            "location": r["location"],
            "category": r["category"],
            "count": int(r["count"]),
            "expected_profit_sum": float(r["expected_profit_sum"]),
            "actual_profit_sum": float(r["actual_profit_sum"]),
            "variance_sum": float(r["variance_sum"]),
            "upside_count": int(r["upside_count"]),
            "upside_amount": float(r["upside_amount"]),
            "downside_count": int(r["downside_count"]),
            "downside_amount": float(r["downside_amount"]),
        }
        for _, r in df.iterrows()
    ]


DEFICIT_COLUMNS = [
    # location は⑧赤字ページの拠点フィルタ用に追加した次元(build_shukka_detail が持つ
    # location をそのまま引き継ぐだけで、赤字判定・金額計算のロジックは一切変えていない)。
    "week_start", "week_end", "year_month", "location", "category", "procurement_type",
    "count", "total_deficit", "avg_deficit_per_item", "shipping_fee_total", "return_shipping_total",
]


def build_return_product_ids(weeks: list[WeekFiles]) -> set[str]:
    """CS_返金のうち「返品」列が「あり」の行の商品ID(数字部分)の集合を返す。

    aggregate_refund の返送料ロジック(all_df["返品"].fillna("").astype(str).str.strip() == "あり")
    と同じ判定基準を用いる。除外拠点(CSセンター/cs_center/鳥取/北関東)はここでも除外して
    赤字分析用の返品有無判定の対象外とする(既存のaggregate_refundの拠点除外方針と合わせるため)。
    """
    frames = []
    for w in weeks:
        raw = w.files.get("cs_henkin")
        if raw is None:
            continue
        frames.append(read_csv_bytes(raw))
    if not frames:
        return set()
    all_df = pd.concat(frames, ignore_index=True)
    if "拠点" in all_df.columns:
        excluded_mask = all_df["拠点"].map(is_excluded_location)
        all_df = all_df[~excluded_mask].copy()
    if "返品" not in all_df.columns or "商品ID" not in all_df.columns:
        return set()
    is_return = all_df["返品"].fillna("").astype(str).str.strip() == "あり"
    ids = all_df.loc[is_return, "商品ID"].map(normalize_product_id)
    return set(ids.dropna())


def aggregate_deficit_modes(
    weeks: list[WeekFiles], detail: pd.DataFrame, cost_master: dict[str, tuple[float, float]]
) -> pd.DataFrame:
    """⑦赤字ページ用に、2つの見方の損益を1商品ごとに算出する。

    ① 会計上の粗利  = 落札価格 - 買取価格/1.1
         仕入と売価の差だけを見る、経理的な粗利。送料や返送料は含めない。
    ② 最終利益      = 落札価格 - 買取価格/1.1 - 発送送料/1.1 (- 返品ありなら返送料/1.1)
         実際に手元に残る利益。

    【返品→再出品→再販が同一期間内に起きた場合】
      商品_出荷(JPONベース)は同じ商品IDが再出現するため concat_and_dedup で
      「最後に現れた行(=再販時の落札価格)」を採用している。したがって落札価格は
      自動的に最終的な販売価格になる。送料は「最終の発送1回分」のみを計上する
      (運用確認: 発送時の送料はお客様負担で、受け取った送料をそのまま配送業者に
       支払うため当社の持ち出しにならない。厳密には契約送料との差額が利益になるが、
       ここでは利益として考慮しない)。

      例) 買取10,000円・最終落札13,000円・送料2,440円(税込)の場合
          会計上の粗利 = 13,000 - 9,091 = 3,909円
          最終利益     = 13,000 - 9,091 - 2,218 = 1,691円
    """
    if detail.empty:
        return pd.DataFrame()
    d = detail.copy()
    return_ids = build_return_product_ids(weeks)

    def _return_ship(norm_id):
        if not norm_id or norm_id not in return_ids:
            return 0.0
        info = cost_master.get(norm_id)
        return to_excl_tax(info[1]) if info else 0.0

    d["return_shipping_amount"] = d["norm_product_id"].map(_return_ship)
    # actual_profit は既に 落札価格 - 買取価格/1.1 (税抜)
    d["accounting_profit"] = d["actual_profit"]
    # shipping_fee は build_shukka_detail 時点で税抜換算済み
    d["final_profit_item"] = d["actual_profit"] - d["shipping_fee"] - d["return_shipping_amount"]
    return d


DEFICIT_MODE_COLUMNS = [
    "week_start", "week_end", "year_month", "location", "category", "procurement_type",
    "shipped_count",
    "acc_deficit_count", "acc_deficit_amount", "acc_profit_sum",
    "fin_deficit_count", "fin_deficit_amount", "fin_profit_sum",
    "shipping_fee_total", "return_shipping_total",
]


def build_deficit_mode_rows(
    weeks: list[WeekFiles], detail: pd.DataFrame, cost_master: dict[str, tuple[float, float]]
) -> list[dict]:
    """「会計上の粗利」と「最終利益」の2軸で、赤字件数・赤字額・利益合計を集計する。"""
    d = aggregate_deficit_modes(weeks, detail, cost_master)
    if d.empty:
        return []
    d["acc_is_deficit"] = d["accounting_profit"] < 0
    d["fin_is_deficit"] = d["final_profit_item"] < 0
    d["acc_deficit_amount"] = (-d["accounting_profit"]).where(d["acc_is_deficit"], 0.0)
    d["fin_deficit_amount"] = (-d["final_profit_item"]).where(d["fin_is_deficit"], 0.0)
    grouped = (
        d.groupby(["week_start", "week_end", "year_month", "location", "category", "procurement_type"])
        .agg(
            shipped_count=("accounting_profit", "size"),
            acc_deficit_count=("acc_is_deficit", "sum"),
            acc_deficit_amount=("acc_deficit_amount", "sum"),
            acc_profit_sum=("accounting_profit", "sum"),
            fin_deficit_count=("fin_is_deficit", "sum"),
            fin_deficit_amount=("fin_deficit_amount", "sum"),
            fin_profit_sum=("final_profit_item", "sum"),
            shipping_fee_total=("shipping_fee", "sum"),
            return_shipping_total=("return_shipping_amount", "sum"),
        )
        .reset_index()
    )
    return [
        {
            **{c: r[c] for c in ["week_start", "week_end", "year_month", "location", "category", "procurement_type"]},
            "shipped_count": int(r["shipped_count"]),
            "acc_deficit_count": int(r["acc_deficit_count"]),
            "acc_deficit_amount": _safe_float(r["acc_deficit_amount"]),
            "acc_profit_sum": _safe_float(r["acc_profit_sum"]),
            "fin_deficit_count": int(r["fin_deficit_count"]),
            "fin_deficit_amount": _safe_float(r["fin_deficit_amount"]),
            "fin_profit_sum": _safe_float(r["fin_profit_sum"]),
            "shipping_fee_total": _safe_float(r["shipping_fee_total"]),
            "return_shipping_total": _safe_float(r["return_shipping_total"]),
        }
        for _, r in grouped.iterrows()
    ]


def aggregate_deficit(
    weeks: list[WeekFiles], detail: pd.DataFrame, cost_master: dict[str, tuple[float, float]]
) -> pd.DataFrame:
    """赤字(原価割れ)商品を週×カテゴリ×procurement_type(仕入れ方法)で集計する。

    赤字の定義: 実質粗利(actual_profit - shipping_fee) が0未満の商品(発送送料も加味)。
    加えて、その商品についてCS_返金に「返品」列が「あり」の行があれば、その商品の
    返送料(cost_masterのヤフオク配送料)も追加で赤字額に加算する
    (aggregate_refund の返送料ロジックを参考に、赤字商品側にも同じ基準で反映する)。

    total_deficit は正の値=赤字の大きさとして統一する
    (赤字方向の金額の絶対値 + 返品時の追加返送料)。
    """
    if detail.empty:
        return pd.DataFrame(columns=DEFICIT_COLUMNS)

    d = detail.copy()
    d["net_profit"] = d["actual_profit"] - d["shipping_fee"]
    deficit = d[d["net_profit"] < 0].copy()
    if deficit.empty:
        return pd.DataFrame(columns=DEFICIT_COLUMNS)

    return_ids = build_return_product_ids(weeks)

    def _return_ship(norm_id):
        # 返送料はヤフオク配送料そのもの(税込)なので、税抜に換算して赤字額に加える
        if not norm_id or norm_id not in return_ids:
            return 0.0
        info = cost_master.get(norm_id)
        return to_excl_tax(info[1]) if info else 0.0

    deficit["return_shipping_amount"] = deficit["norm_product_id"].map(_return_ship)
    deficit["deficit_amount"] = (-deficit["net_profit"]) + deficit["return_shipping_amount"]

    grouped = (
        deficit.groupby(["week_start", "week_end", "year_month", "location", "category", "procurement_type"])
        .agg(
            count=("net_profit", "size"),
            total_deficit=("deficit_amount", "sum"),
            shipping_fee_total=("shipping_fee", "sum"),
            return_shipping_total=("return_shipping_amount", "sum"),
        )
        .reset_index()
    )
    grouped["avg_deficit_per_item"] = grouped.apply(
        lambda r: (r["total_deficit"] / r["count"]) if r["count"] else None, axis=1
    )
    return grouped[DEFICIT_COLUMNS]


def build_deficit_rows(
    weeks: list[WeekFiles], detail: pd.DataFrame, cost_master: dict[str, tuple[float, float]]
) -> list[dict]:
    df = aggregate_deficit(weeks, detail, cost_master)
    if df.empty:
        return []
    df = df.sort_values(["week_start", "location", "category", "procurement_type"])
    return [
        {
            "week_start": r["week_start"],
            "week_end": r["week_end"],
            "year_month": r["year_month"],
            "location": r["location"],
            "category": r["category"],
            "procurement_type": r["procurement_type"],
            "count": int(r["count"]),
            "total_deficit": _safe_float(r["total_deficit"]),
            "avg_deficit_per_item": _safe_float(r["avg_deficit_per_item"]),
            "shipping_fee_total": _safe_float(r["shipping_fee_total"]),
            "return_shipping_total": _safe_float(r["return_shipping_total"]),
        }
        for _, r in df.iterrows()
    ]


# ---------------------------------------------------------------------------
# ⑨ SRリピーター・ロイヤルカスタマー分析(顧客名寄せ)
# ---------------------------------------------------------------------------
#
# 【個人情報(PII)の取り扱いについて - 重要】
#   このダッシュボードのHTML/JSONはGitHub Pagesで一般公開されるため、
#   氏名・カナ・電話番号・郵便番号・住所・メールアドレスといった顧客の個人情報は
#   「同一人物判定(名寄せ)」を行うためだけにこのPythonプロセス内で使用し、
#   cs_sr_dashboard_data.json / cs_sr_dashboard.html には一切出力しない。
#   ダッシュボードに出るのは匿名ラベル(「顧客A」「顧客B」…)と数値指標のみ。
#   実名・連絡先の対応表は build_customer_segment_rows() が別途返す
#   lookup レコード(→ customer_lookup.csv)にのみ含める。
#
# 【名寄せ(同一人物判定)ロジック】
#   全期間の「受注_通常_出荷」(tsujo)の各行(=1受注)について
#     ・氏名 (正規化: NFKC + 空白除去)
#     ・住所 (正規化: 郵便番号(数字のみ)|都道府県|住所 を連結。住所が空なら照合キーにしない)
#     ・電話番号 (正規化: NFKC + 数字以外除去 + 先頭0補完)
#     ・メールアドレス (正規化: NFKC + 空白除去 + 小文字化。"@"を含まない値は無効)
#   の4種の照合キーを作り、「いずれか1つでも完全一致すれば同一人物」とみなして
#   Union-Find(素集合データ構造)でクラスタリングする。
#   空文字列・NaN・欠損値は照合キーとして使わない(常に不一致扱い)。
#   計算量は各キーごとに「キー値 -> 行インデックスのリスト」の辞書を作り、
#   同じキー値を持つ行同士を1本の鎖でunionしていくため、ほぼ O(n α(n)) で完了する
#   (素朴な O(n^2) 総当たり比較は行わない)。
#
# 【匿名ラベル】
#   全顧客(クラスタ)を売上額(落札価格合計)の降順に並べ、上位から
#   顧客A, 顧客B, ... 顧客Z, 顧客AA, 顧客AB, ... と26進の連番記号を割り当てる。
#   このラベルはセグメントをまたいで一意・不変であり、customer_lookup.csv の
#   「顧客コード」列と同じ値になるため、社内では対応表と突合できる。

_PII_TEL_STRIP_RE = re.compile(r"[^0-9]")
_PII_SPACE_RE = re.compile(r"\s+")


def _norm_pii_text(v) -> str:
    """氏名・住所などの文字列を照合用に正規化する(NFKCで全角/半角統一 + 空白除去)。

    空・NaN・"nan" は照合キーとして使えないため空文字を返す(=常に不一致扱い)。
    """
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    s = str(v)
    if not s or s.lower() == "nan":
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _PII_SPACE_RE.sub("", s)
    s = s.replace("　", "").strip()
    return s


def _norm_pii_tel(v) -> str:
    """電話番号を照合用に正規化する(ハイフン・括弧等を除去して数字のみにする)。

    CSVによって先頭の0が落ちている(数値として扱われた)ケースがあるため、
    先頭が0でない場合は0を補完して桁を揃える。
    """
    s = _norm_pii_text(v)
    if not s:
        return ""
    s = _PII_TEL_STRIP_RE.sub("", s)
    if not s:
        return ""
    if not s.startswith("0"):
        s = "0" + s
    # 明らかに桁数が足りない値(3桁以下)は照合キーとして信頼できないので使わない
    if len(s) < 9:
        return ""
    return s


def _norm_pii_mail(v) -> str:
    """メールアドレスを照合用に正規化する。"@"を含まない値は無効(空文字)とする。"""
    s = _norm_pii_text(v).lower()
    if "@" not in s:
        return ""
    return s


class _UnionFind:
    """配列ベースの Union-Find(素集合データ構造)。経路圧縮 + union by size。"""

    __slots__ = ("parent", "size")

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        parent = self.parent
        root = x
        while parent[root] != root:
            root = parent[root]
        # 経路圧縮(再帰なしでスタック溢れを避ける)
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def anon_customer_label(rank: int) -> str:
    """1始まりの順位から匿名ラベル(顧客A, 顧客B, ..., 顧客Z, 顧客AA, ...)を作る。

    26進のいわゆる bijective base-26(Excelの列名と同じ方式)なので、
    26人を超えても衝突せず一意なラベルを割り当てられる。
    """
    n = int(rank)
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return "顧客" + s


CUSTOMER_SEGMENT_COLUMNS = [
    "segment",
    "label",
    "rank",
    "order_count",
    "bundle_order_count",
    "shipped_count",
    "sr_count",
    "sales_amount",
    "gross_profit",
    "refund_amount",
    "return_shipping_cost",
    "final_profit",
]

# セグメント抽出時に上位何割を採用するか
SEGMENT_TOP_RATIO = 0.20


def build_customer_clusters(weeks: list[WeekFiles]) -> tuple[pd.DataFrame, list[str]]:
    """全期間の受注_通常_出荷(tsujo)を読み込み、名寄せ済みのクラスタID列を付けて返す。

    戻り値: (tsujo DataFrame(列 _cluster を追加), 使用した照合キー列名のログ)
    ※ この DataFrame には氏名等のPIIが含まれる。呼び出し側で必ずPIIを落とすこと。
    """
    frames = []
    for w in weeks:
        raw = w.files.get("juchu_tsujo")
        if raw is None:
            continue
        frames.append(tag_week(read_csv_bytes(raw), w))
    if not frames:
        return pd.DataFrame(), []

    tsujo = concat_and_dedup(frames, id_col="受注ID")
    # 他の集計と同じ拠点除外ルールを適用する(CSセンター・鳥取・北関東)
    excluded_mask = tsujo["拠点"].map(is_excluded_location)
    tsujo = tsujo[~excluded_mask].reset_index(drop=True)
    if tsujo.empty:
        return tsujo, []

    name_key = tsujo["氏名"].map(_norm_pii_text)
    addr_body = tsujo["住所"].map(_norm_pii_text)
    zip_key = tsujo["郵便番号"].map(lambda v: _PII_TEL_STRIP_RE.sub("", _norm_pii_text(v)))
    pref_key = tsujo["都道府県"].map(_norm_pii_text)
    # 住所が空の行は住所キーそのものを無効化する(郵便番号や都道府県だけの一致で
    # 無関係な人物が結合されるのを防ぐため)
    addr_key = pd.Series(
        [
            (z + "|" + p + "|" + a) if a else ""
            for z, p, a in zip(zip_key, pref_key, addr_body)
        ],
        index=tsujo.index,
    )
    tel_key = tsujo["電話番号"].map(_norm_pii_tel)
    mail_key = tsujo["メールアドレス"].map(_norm_pii_mail)

    n = len(tsujo)
    uf = _UnionFind(n)
    log: list[str] = []

    # 【名寄せキーの見直し(2026-08)】
    # 以前は「氏名だけの一致」でも同一人物とみなしていたため、同姓同名を起点に
    # 住所→電話→氏名…と連鎖的に結合し、4,499通りの氏名・7,021取引が1人に
    # まとめられる過剰結合が発生していた。対策として:
    #   1. 氏名単独では結合しない。氏名は「住所+氏名」の複合キーとしてのみ使う。
    #   2. 1つのキーに紐づく氏名が MAX_NAMES_PER_KEY を超える場合は、
    #      配送センター・代行業者・ダミー値とみなして結合に使わない。
    MAX_NAMES_PER_KEY = 5
    addr_name_key = pd.Series(
        [(a + "|" + nm) if (a and nm) else "" for a, nm in zip(addr_key, name_key)],
        index=tsujo.index,
    )

    # 共有キー(1つの値に多数の氏名がぶら下がる値)は「個人」ではなく
    # 転送代行業者・法人拠点とみなす。ただし単に無視するのではなく、
    # その拠点単位で1グループにまとめたうえで「業者拠点」と印を付ける。
    # こうすることで、
    #   ・個人顧客の統計に業者がまぎれ込まない
    #   ・業者拠点ごとのSR発生状況をまとめて確認できる
    # の両方を満たせる。
    biz_group_key = pd.Series([""] * n, index=tsujo.index)

    def _split_shared_keys(key_series, key_label):
        """個人用キーと、業者拠点用のグループキーに分ける。"""
        names_per_value: dict[str, set] = {}
        for value, nm in zip(key_series.to_numpy(), name_key.to_numpy()):
            if not value:
                continue
            names_per_value.setdefault(value, set()).add(nm)
        shared = {v for v, names in names_per_value.items() if len(names) > MAX_NAMES_PER_KEY}
        if shared:
            total = sum(1 for v in key_series.to_numpy() if v in shared)
            print(f"[情報] {key_label}: {len(shared)}件の値(受注{total:,}件)は氏名が{MAX_NAMES_PER_KEY}種類を"
                  f"超えるため「業者拠点」として個人とは別にまとめます", flush=True)
            for idx, value in enumerate(key_series.to_numpy()):
                if value in shared and not biz_group_key.iat[idx]:
                    biz_group_key.iat[idx] = key_label + ":" + value
        return key_series.map(lambda v: "" if (not v or v in shared) else v)

    # 氏名との複合キー。電話・メールが「共有キー」として除外された場合でも、
    # 氏名まで一致していれば同一人物とみなせるので、複合キーは除外の前に作っておく。
    name_tel_key = pd.Series(
        [(t + "|" + nm) if (t and nm) else "" for t, nm in zip(tel_key, name_key)], index=tsujo.index)
    name_mail_key = pd.Series(
        [(e + "|" + nm) if (e and nm) else "" for e, nm in zip(mail_key, name_key)], index=tsujo.index)

    # 住所を先に判定する(同じ倉庫が電話・メールも共有しているケースを1グループにまとめるため)
    addr_key_solo = _split_shared_keys(addr_key, "住所")
    tel_key = _split_shared_keys(tel_key, "電話番号")
    mail_key = _split_shared_keys(mail_key, "メールアドレス")

    for key_name, key_series in (
        ("業者拠点", biz_group_key),
        ("住所+氏名", addr_name_key),
        ("電話番号+氏名", name_tel_key),
        ("メールアドレス+氏名", name_mail_key),
        ("住所", addr_key_solo),
        ("電話番号", tel_key),
        ("メールアドレス", mail_key),
    ):
        buckets: dict[str, int] = {}
        merges = 0
        used = 0
        for idx, value in enumerate(key_series.to_numpy()):
            if not value:
                continue
            used += 1
            first = buckets.get(value)
            if first is None:
                buckets[value] = idx
            else:
                uf.union(first, idx)
                merges += 1
        log.append(f"{key_name}: 有効キー{used}行 / ユニーク{len(buckets)}値 / union {merges}回")

    tsujo = tsujo.copy()
    tsujo["_cluster"] = [uf.find(i) for i in range(n)]
    # クラスタ内に1件でも業者拠点キーがあれば、そのクラスタ全体を「業者拠点」とする
    biz_clusters = set(tsujo.loc[biz_group_key != "", "_cluster"].unique())
    tsujo["_customer_type"] = tsujo["_cluster"].map(lambda c: "業者拠点" if c in biz_clusters else "個人")
    print(f"[情報] 業者拠点として扱うグループ: {len(biz_clusters):,}件 "
          f"(受注{int((tsujo['_customer_type'] == '業者拠点').sum()):,}件)", flush=True)
    return tsujo, log


def build_customer_segment_rows(
    weeks: list[WeekFiles],
    cost_master: dict[str, tuple[float, float]],
    attr_master: Optional[dict[str, tuple[str, str, int]]] = None,
    category_master: Optional[dict[str, str]] = None,
) -> tuple[list[dict], list[dict], dict, list[dict]]:
    """顧客(名寄せクラスタ)単位の指標を算出し、
    (ダッシュボード用の匿名行, customer_lookup.csv用のPII行, 集計メタ情報) を返す。

    指標の定義:
      発送商品数 shipped_count  : 受注_通常_出荷.受注ID == 受注_JPON_出荷.取引番号、
                                  受注_JPON_出荷.管理番号(正規化) == 商品_出荷(JPONベース).商品ID(正規化)
                                  の3ファイル結合で辿れた出荷商品の件数
      同梱率     bundle_rate    : (品数>=2 の受注件数) ÷ (全受注件数)
      SR発生件数 sr_count       : CS_登録【分類用】の 種別=SR の行を「受注ID」で紐付けた件数
      SR率       sr_rate        : sr_count ÷ shipped_count
      売上額     sales_amount   : 受注_通常_出荷「落札価格」の合計
      粗利       gross_profit   : 既存ロジックと同じ 落札金額 - 買取価格/1.1 - ヤフオク配送料/1.1
      返金額     refund_amount  : CS_返金の返金額合計(受注IDで紐付け)
      返金額率   refund_rate    : refund_amount ÷ sales_amount
      最終利益   final_profit   : gross_profit - refund_amount - return_shipping_cost
    """
    meta: dict = {}
    tsujo, uf_log = build_customer_clusters(weeks)
    if tsujo is None or tsujo.empty:
        return [], [], meta
    meta["union_find_log"] = uf_log
    meta["order_rows"] = int(len(tsujo))

    tsujo["_sales"] = to_numeric(tsujo["落札価格"])
    tsujo["_items"] = to_numeric(tsujo["品数"])
    tsujo["_is_bundle"] = tsujo["_items"] >= 2

    cluster_of_order = dict(zip(tsujo["受注ID"].astype(str).str.strip(), tsujo["_cluster"]))

    per_cluster = (
        tsujo.groupby("_cluster")
        .agg(
            order_count=("受注ID", "size"),
            bundle_order_count=("_is_bundle", "sum"),
            sales_amount=("_sales", "sum"),
            customer_type=("_customer_type", "first"),
        )
        .reset_index()
    )
    meta["cluster_count"] = int(len(per_cluster))

    # --- 発送商品数・粗利(受注_通常_出荷 -> 受注_JPON_出荷 -> 商品_出荷 の3ファイル結合) ---
    jpon_frames = []
    shukka_ids: set[str] = set()
    for w in weeks:
        raw = w.files.get("juchu_jpon")
        if raw is not None:
            jpon_frames.append(read_csv_bytes(raw)[["オークションID", "取引番号", "管理番号", "落札金額"]])
        sraw = w.files.get("shohin_shukka")
        if sraw is not None:
            sdf = read_csv_bytes(sraw)
            if "商品ID" in sdf.columns:
                for nid in sdf["商品ID"].map(normalize_product_id):
                    if nid:
                        shukka_ids.add(nid)
    # 顧客ドリルダウン用の商品属性(状態・価格帯・カテゴリ)。未指定ならここで作る。
    attr_master = attr_master if attr_master is not None else build_product_attr_master(weeks)
    category_master = category_master if category_master is not None else build_product_category_master(weeks)
    purchase_detail = pd.DataFrame()
    sr_detail = pd.DataFrame()
    refund_detail = pd.DataFrame()
    shipped_agg = pd.DataFrame(columns=["_cluster", "shipped_count", "gross_profit"])
    if jpon_frames:
        jpon = concat_and_dedup(jpon_frames, id_col="オークションID")
        merged = tsujo[["受注ID", "_cluster"]].merge(
            jpon[["取引番号", "管理番号", "落札金額"]],
            left_on="受注ID",
            right_on="取引番号",
            how="inner",
        )
        merged["_norm_id"] = merged["管理番号"].map(normalize_product_id)
        before = len(merged)
        merged = merged[merged["_norm_id"].isin(shukka_ids)].copy()
        meta["shipped_join_rows"] = int(len(merged))
        meta["shipped_join_rate"] = (len(merged) / before) if before else 0.0

        merged["落札金額_num"] = to_numeric(merged["落札金額"])

        def _cost_pair(norm_id):
            if norm_id is None:
                return (0.0, 0.0)
            return cost_master.get(norm_id, (0.0, 0.0))

        cost_pairs = merged["_norm_id"].map(_cost_pair)
        merged["買取価格_num"] = cost_pairs.map(lambda t: t[0])
        merged["配送料_num"] = cost_pairs.map(lambda t: t[1])
        merged["粗利_row"] = (
            merged["落札金額_num"] - merged["買取価格_num"] / 1.1 - merged["配送料_num"] / 1.1
        )
        shipped_agg = (
            merged.groupby("_cluster")
            .agg(shipped_count=("受注ID", "size"), gross_profit=("粗利_row", "sum"))
            .reset_index()
        )
        # 顧客詳細(⑧のドリルダウン)用: 購入をカテゴリ・コンディション・価格帯で分解する。
        # 商品名などの自由記述は公開ページに出さないため、ここでは集計値のみを持つ。
        attrs = merged["_norm_id"].map(lambda nid: attr_master.get(nid, UNKNOWN_ATTR) if nid else UNKNOWN_ATTR)
        merged["_condition"] = attrs.map(lambda t: normalize_condition(t[0]))
        merged["_price_band"] = attrs.map(lambda t: t[1])
        merged["_band_sort"] = attrs.map(lambda t: t[2])
        merged["_category"] = merged["_norm_id"].map(category_master).fillna("不明")
        purchase_detail = (
            merged.groupby(["_cluster", "_category", "_condition", "_price_band", "_band_sort"])
            .agg(count=("受注ID", "size"), sales_amount=("落札金額_num", "sum"), gross_profit=("粗利_row", "sum"))
            .reset_index()
        )

    # --- SR発生件数(CS_登録【分類用】の 種別=SR を「受注ID」で紐付け) ---
    cs_frames = []
    for w in weeks:
        raw = w.files.get("cs_bunruiyou") or w.files.get("cs_touroku")
        if raw is None:
            continue
        cs_frames.append(read_csv_bytes(raw))
    sr_agg = pd.DataFrame(columns=["_cluster", "sr_count"])
    if cs_frames:
        cs = concat_and_dedup(cs_frames, id_col="CS ID")
        cs = cs[~cs["拠点"].map(is_excluded_location)].copy()
        # 既存集計と同じく「ステータス」列が「スルー」の行は対象外
        cs = cs[cs["ステータス"].fillna("").astype(str).str.strip() != "スルー"]
        sr = cs[cs["種別"].fillna("").astype(str).str.strip() == "SR"].copy()
        meta["sr_rows_total"] = int(len(sr))
        sr["_cluster"] = sr["受注ID"].astype(str).str.strip().map(cluster_of_order)
        matched = sr[sr["_cluster"].notna()]
        meta["sr_rows_matched"] = int(len(matched))
        meta["sr_match_rate"] = (len(matched) / len(sr)) if len(sr) else 0.0
        if not matched.empty:
            sr_agg = (
                matched.groupby("_cluster").size().reset_index(name="sr_count")
            )
            sr_agg["_cluster"] = sr_agg["_cluster"].astype(per_cluster["_cluster"].dtype)
            # 顧客詳細用: SRをカテゴリ×大項目×返品有無で分解する(自由記述は含めない)
            md = matched.copy()
            md["_category"] = md["カテゴリ"].fillna("不明").replace("", "不明")
            if "分類" in md.columns:
                md["_major"] = md["分類"].map(extract_major_category).fillna("(未分類)")
                md["_minor"] = md["分類"].map(extract_minor_category).fillna("(小項目なし)")
            else:
                md["_major"] = "(未分類)"
                md["_minor"] = "(小項目なし)"
            md["_returned"] = md["返品"].fillna("").astype(str).str.strip().map(lambda v: "返品あり" if v == "あり" else "返品なし")
            md["_refund"] = to_numeric(md["返金額"]) if "返金額" in md.columns else 0
            sr_detail = (
                md.groupby(["_cluster", "_category", "_major", "_minor", "_returned"])
                .agg(count=("受注ID", "size"), refund_amount=("_refund", "sum"))
                .reset_index()
            )

    # --- 返金額・返送料(CS_返金を「受注ID」で紐付け) ---
    henkin_frames = []
    for w in weeks:
        raw = w.files.get("cs_henkin")
        if raw is None:
            continue
        henkin_frames.append(read_csv_bytes(raw))
    refund_agg = pd.DataFrame(columns=["_cluster", "refund_amount", "return_shipping_cost"])
    if henkin_frames:
        hen = concat_and_dedup(henkin_frames, id_col="CS ID")
        hen = hen[~hen["拠点"].map(is_excluded_location)].copy()
        hen["_cluster"] = hen["受注ID"].astype(str).str.strip().map(cluster_of_order)
        meta["refund_rows_total"] = int(len(hen))
        hen = hen[hen["_cluster"].notna()].copy()
        meta["refund_rows_matched"] = int(len(hen))
        if not hen.empty:
            # 返金額・返送料はいずれも税込のため税抜に換算する
            hen["返金額_num"] = to_excl_tax(to_numeric(hen["返金額"]))
            hen["_norm_id"] = hen["商品ID"].map(normalize_product_id)
            is_return = hen["返品"].fillna("").astype(str).str.strip() == "あり"

            def _shipping_fee(norm_id):
                if norm_id is None:
                    return 0.0
                info = cost_master.get(norm_id)
                return to_excl_tax(info[1]) if info else 0.0

            hen["返送料_num"] = 0.0
            hen.loc[is_return, "返送料_num"] = hen.loc[is_return, "_norm_id"].map(_shipping_fee)
            # 顧客詳細用: 返金をカテゴリ×返品有無で分解する(返金額はCS_返金が正)
            hen["_category"] = hen["カテゴリ"].fillna("不明").replace("", "不明") if "カテゴリ" in hen.columns else "不明"
            hen["_returned"] = is_return.map(lambda v: "返品あり" if v else "返品なし")
            refund_detail = (
                hen.groupby(["_cluster", "_category", "_returned"])
                .agg(count=("返金額_num", "size"), refund_amount=("返金額_num", "sum"),
                     return_shipping_cost=("返送料_num", "sum"))
                .reset_index()
            )
            refund_agg = (
                hen.groupby("_cluster")
                .agg(refund_amount=("返金額_num", "sum"), return_shipping_cost=("返送料_num", "sum"))
                .reset_index()
            )
            refund_agg["_cluster"] = refund_agg["_cluster"].astype(per_cluster["_cluster"].dtype)

    # --- 結合して顧客単位の指標テーブルを作る ---
    cust = per_cluster
    for other in (shipped_agg, sr_agg, refund_agg):
        if other is not None and not other.empty:
            cust = cust.merge(other, on="_cluster", how="left")
    for col, default in (
        ("shipped_count", 0),
        ("gross_profit", 0.0),
        ("sr_count", 0),
        ("refund_amount", 0.0),
        ("return_shipping_cost", 0.0),
    ):
        if col not in cust.columns:
            cust[col] = default
        cust[col] = cust[col].fillna(default)
    cust["bundle_order_count"] = cust["bundle_order_count"].astype(int)
    cust["shipped_count"] = cust["shipped_count"].astype(int)
    cust["sr_count"] = cust["sr_count"].astype(int)
    cust["final_profit"] = cust["gross_profit"] - cust["refund_amount"] - cust["return_shipping_cost"]

    # --- 匿名ラベル: 全顧客を売上額の降順に並べた通し記号(セグメント間で共通・不変) ---
    cust = cust.sort_values(
        ["sales_amount", "shipped_count", "_cluster"], ascending=[False, False, True]
    ).reset_index(drop=True)
    cust["label"] = [anon_customer_label(i + 1) for i in range(len(cust))]
    cust["sales_rank"] = range(1, len(cust) + 1)

    def _top_slice(df: pd.DataFrame, sort_cols, ascending) -> pd.DataFrame:
        if df.empty:
            return df
        ordered = df.sort_values(sort_cols, ascending=ascending).reset_index(drop=True)
        take = max(1, int(math.ceil(len(ordered) * SEGMENT_TOP_RATIO)))
        top = ordered.head(take).copy()
        top["rank"] = range(1, len(top) + 1)
        return top

    # ロイヤルカスタマー: 全顧客を売上額(合計落札価格)の降順に並べた上位20%
    loyal = _top_slice(
        cust, ["sales_amount", "shipped_count", "_cluster"], [False, False, True]
    )
    # SRリピーター: SRが1件以上発生した顧客を母集団とし、SR発生件数の降順で上位20%。
    #   ※全顧客(約10万人)を母集団にすると、上位20%(約2万人)の大半がSR0件の同着に
    #     なってしまい「リピーター」の抽出として意味を成さないため、SR発生実績のある
    #     顧客に母集団を限定している(この判断は報告書に明記)。
    sr_pool = cust[cust["sr_count"] >= 1]
    meta["sr_pool_count"] = int(len(sr_pool))
    repeater = _top_slice(
        sr_pool, ["sr_count", "sales_amount", "_cluster"], [False, False, True]
    )

    def _to_rows(df: pd.DataFrame, segment: str) -> list[dict]:
        out = []
        for _, r in df.iterrows():
            out.append(
                {
                    "segment": segment,
                    "label": r["label"],
                    # 個人 / 業者拠点(転送代行・法人窓口とみなしたグループ)
                    "customer_type": r.get("customer_type", "個人"),
                    "rank": int(r["rank"]),
                    "order_count": int(r["order_count"]),
                    "bundle_order_count": int(r["bundle_order_count"]),
                    "shipped_count": int(r["shipped_count"]),
                    "sr_count": int(r["sr_count"]),
                    "sales_amount": round(float(r["sales_amount"]), 2),
                    "gross_profit": round(float(r["gross_profit"]), 2),
                    "refund_amount": round(float(r["refund_amount"]), 2),
                    "return_shipping_cost": round(float(r["return_shipping_cost"]), 2),
                    "final_profit": round(float(r["final_profit"]), 2),
                }
            )
        return out

    rows = _to_rows(repeater, "sr_repeater") + _to_rows(loyal, "loyal_customer")
    meta["sr_repeater_count"] = int(len(repeater))
    meta["loyal_customer_count"] = int(len(loyal))

    # --- customer_lookup.csv 用のPII行(ダッシュボードには一切出さない) ---
    shown_clusters = set(repeater["_cluster"]) | set(loyal["_cluster"])
    label_of_cluster = dict(zip(cust["_cluster"], cust["label"]))
    lookup = _build_customer_lookup_records(tsujo, shown_clusters, label_of_cluster)
    meta["lookup_count"] = len(lookup)

    # --- 顧客ドリルダウン用の内訳(⑧で顧客をクリックしたときに表示する) ---
    # 一覧に出る顧客(SRリピーター+ロイヤルカスタマー)のぶんだけ作る。
    # 商品名やSRの自由記述は含めず、件数・金額の集計のみ(公開ページに載るため)。
    # すべての内訳行で同じキーを持たせる(埋め込み時に列配列化するため、
    # 種類ごとにキーが違うと後ろの種類の列が失われてしまう)
    DETAIL_FIELDS = {
        "label": "", "kind": "", "category": "", "condition": "", "price_band": "",
        "price_band_sort": 0, "major": "", "minor": "", "returned": "",
        "count": 0, "sales_amount": 0.0, "gross_profit": 0.0, "refund_amount": 0.0,
    }

    def _detail(**kw):
        rec = dict(DETAIL_FIELDS)
        rec.update(kw)
        return rec

    detail_rows: list[dict] = []
    if not purchase_detail.empty:
        pd_shown = purchase_detail[purchase_detail["_cluster"].isin(shown_clusters)]
        for _, r in pd_shown.iterrows():
            detail_rows.append(_detail(
                label=label_of_cluster.get(r["_cluster"], ""), kind="purchase",
                category=r["_category"], condition=r["_condition"],
                price_band=r["_price_band"], price_band_sort=int(r["_band_sort"]),
                count=int(r["count"]), sales_amount=round(float(r["sales_amount"]), 2),
                gross_profit=round(float(r["gross_profit"]), 2)))
    if not sr_detail.empty:
        sr_shown = sr_detail[sr_detail["_cluster"].isin(shown_clusters)]
        for _, r in sr_shown.iterrows():
            detail_rows.append(_detail(
                label=label_of_cluster.get(r["_cluster"], ""), kind="sr",
                category=r["_category"], major=r["_major"], minor=r["_minor"], returned=r["_returned"],
                count=int(r["count"]), refund_amount=round(float(r["refund_amount"]), 2)))
    if not refund_detail.empty:
        rf_shown = refund_detail[refund_detail["_cluster"].isin(shown_clusters)]
        for _, r in rf_shown.iterrows():
            detail_rows.append(_detail(
                label=label_of_cluster.get(r["_cluster"], ""), kind="refund",
                category=r["_category"], returned=r["_returned"],
                count=int(r["count"]), refund_amount=round(float(r["refund_amount"]), 2)))
    meta["customer_detail_count"] = len(detail_rows)
    print(f"[情報] 顧客詳細(内訳)行: {len(detail_rows):,}行", flush=True)
    return rows, lookup, meta, detail_rows


def _mode_value(series: pd.Series) -> str:
    """空でない値のうち最頻値を返す(同数の場合は先に出現したもの)。"""
    vals = [str(v).strip() for v in series if v is not None and str(v).strip() not in ("", "nan", "NaN")]
    if not vals:
        return ""
    return Counter(vals).most_common(1)[0][0]


def _build_customer_lookup_records(
    tsujo: pd.DataFrame, shown_clusters: set, label_of_cluster: dict
) -> list[dict]:
    """ダッシュボードに表示される顧客について、実名・連絡先の対応表レコードを作る。

    ※ 個人情報を含むため、この戻り値は customer_lookup.csv にのみ書き出すこと。
      cs_sr_dashboard_data.json / cs_sr_dashboard.html には絶対に含めない。
    """
    sub = tsujo[tsujo["_cluster"].isin(shown_clusters)]
    records = []
    for cluster, g in sub.groupby("_cluster"):
        names = [str(v).strip() for v in g["氏名"] if str(v).strip() not in ("", "nan")]
        rep_name = Counter(names).most_common(1)[0][0] if names else ""
        variations = sorted(set(names))
        # 連絡先は「代表氏名の受注行」に絞った上で各列の最頻値を採る。
        # クラスタ全体から列ごとに独立して最頻値を採ると、都道府県は大阪府なのに
        # 住所は東京都の値…といった行をまたいだ食い違いが起こりうるため。
        rep_rows = g[g["氏名"].astype(str).str.strip() == rep_name] if rep_name else g
        if rep_rows.empty:
            rep_rows = g
        records.append(
            {
                "顧客コード": label_of_cluster.get(cluster, ""),
                "種別": g["_customer_type"].iloc[0] if "_customer_type" in g.columns else "個人",
                "代表氏名": rep_name,
                "カナ": _mode_value(rep_rows["カナ"]),
                "電話番号": _mode_value(rep_rows["電話番号"]),
                "郵便番号": _mode_value(rep_rows["郵便番号"]),
                "都道府県": _mode_value(rep_rows["都道府県"]),
                "住所": _mode_value(rep_rows["住所"]),
                "メールアドレス": _mode_value(rep_rows["メールアドレス"]),
                "取引件数": int(len(g)),
                "紐づく氏名バリエーション一覧": " | ".join(variations),
            }
        )
    # 顧客コード(=売上順の通し記号)の順に並べる
    order = {lab: i for i, lab in enumerate(label_of_cluster.values())}
    records.sort(key=lambda r: order.get(r["顧客コード"], 10**9))
    return records


CUSTOMER_LOOKUP_FIELDS = [
    "顧客コード",
    "種別",
    "代表氏名",
    "カナ",
    "電話番号",
    "郵便番号",
    "都道府県",
    "住所",
    "メールアドレス",
    "取引件数",
    "紐づく氏名バリエーション一覧",
]


def write_customer_lookup_csv(records: list[dict], path) -> None:
    """実名・連絡先の対応表をCSVに書き出す(社内限定。外部共有・アップロードはしない)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CUSTOMER_LOOKUP_FIELDS)
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def print_monthly_summary(merged: pd.DataFrame) -> None:
    if merged.empty:
        print("[集計結果] データがありません。")
        return
    summary = merged.groupby("year_month")[METRIC_COLUMNS].sum().reset_index()
    print("\n=== 月別合計値(検証用) ===")
    for _, r in summary.iterrows():
        final_profit = r["gross_profit"] - r["refund_amount"] - r["return_shipping_cost"]
        print(
            f"{r['year_month']}: "
            f"問合せ件数={int(r['inquiry_count'])}, "
            f"SR発生件数={int(r['sr_count'])}, "
            f"返金額={r['refund_amount']:,.0f}円(件数={int(r['refund_count'])}), "
            f"返送料={r['return_shipping_cost']:,.0f}円, "
            f"質問数={int(r['question_count'])}, "
            f"出荷商品数={int(r['shipped_count'])}, "
            f"出品数={int(r['listed_count'])}, "
            f"売上金額={r['sales_amount']:,.0f}円, "
            f"粗利={r['gross_profit']:,.0f}円, "
            f"最終利益={final_profit:,.0f}円, "
            f"ジャンク出荷={int(r['junk_shipped_count'])}, "
            f"ジャンク出品={int(r['junk_listed_count'])}"
        )


def print_exclusion_stats(stats: ExclusionStats) -> None:
    print("\n=== 除外拠点(CSセンター/cs_center/鳥取/北関東)による除外件数 ===")
    print(f"CS_登録(または分類用): {stats.cs_rows} 行")
    print(f"CS_返金: {stats.henkin_rows} 行")
    print(f"質問_登録: {stats.shitsumon_rows} 行")
    print(f"受注(通常×JPON結合後): {stats.juchu_rows} 行")
    print(f"商品_出品待: {stats.shuppinmachi_rows} 行")
    print(f"商品_出荷(JPONベース)(コンディション/価格帯/粗利差異用): {stats.shukka_rows} 行")
    print(f"合計: {stats.total} 行")
    print(
        f"\n=== ステータス「スルー」による除外件数"
        f"(CS_登録/CS_登録【分類用】が対象。aggregate_cs_sr/aggregate_sr_major/aggregate_causeの合計) ==="
    )
    print(f"スルー除外: {stats.through_rows} 行")


# ---------------------------------------------------------------------------
# 手動メモ(所見)
# ---------------------------------------------------------------------------
# 元々は cs_sr_dashboard_data.json に直接追記されていた手書きの所見コメント
# (period_label/overall/by_category/by_location)。本ETLの再実行でJSONが上書きされても
# 消えないよう、ここに定数として保持し、output["insights"] にそのまま含める。
# H項目の対応により、フロント側(build_dashboard.py)では既存のこの手動メモの下に
# 「粒度・期間に応じて動的に計算される所見」を追加表示する(手動メモ自体は削除しない)。
STATIC_INSIGHTS: dict = {'period_label': '2025年7月〜2026年8月2日(20期通期+21期直近5週、13ヶ月相当)', 'overall': '20期通期(2025年7月〜2026年6月)と21期直近5週(2026年7月〜8月2日)を合わせた13ヶ月分で見ると、SR発生率(出荷数に対するSR件数の割合)は月次で2.10%〜3.28%の範囲で推移しており、大きな悪化傾向は見られません。一方でジャンク出荷比率は2025年7月の10.25%から2026年に入り14〜17%台へ緩やかに上昇し、粗利率も同時期に66.2%(2025年7月)から41〜43%台へ低下しています(初月の高さは、通期データの商品マスタ突合(同一商品IDの初出データを採用する仕様)の影響も考えられるため参考値としてご覧ください)。返金額は月1,350万円〜2,180万円の範囲で変動しています。', 'by_category': {'カメラ': 'SR発生率4.60%・返金率4.08%・ジャンク出荷比率31.90%と、いずれも全カテゴリ中で最も高い水準です。出荷点数22,011件・売上11.1億円と主要カテゴリの一つのため、影響額も大きくなっています。', 'パソコン': 'SR発生率3.98%・返金率2.62%とカメラに次いで高く、売上高13.5億円は全カテゴリ中最大です。単価が高い分、返金1件あたりの金額インパクトも大きいと考えられます。', '家電': '出荷点数50,958件は全カテゴリ中最大ですが、ジャンク出荷比率は7.00%と低水準に抑えられています。一方でSR発生率は3.31%とやや高めです。', '音響機材・カメラ周辺機器': 'ジャンク出荷比率がそれぞれ23.01%・25.05%と高く、SR発生率も3%前後とやや高めです。', 'フィギュア': 'SR発生率0.85%・返金率0.53%・ジャンク出荷比率7.12%といずれも低く、比較的トラブルの少ないカテゴリです。'}, 'by_location': {'東京': '出荷点数38,995件で全拠点中最大、売上16.2億円も突出しています。SR発生率も3.21%とやや高めです。', '神戸': 'ジャンク出荷比率27.23%と全拠点中最も高い一方、SR発生率2.18%・返金率1.59%は低水準です。ジャンク品の多さが必ずしもSR増加に直結していない点は引き続き注目に値します。', '仙台': 'ジャンク出荷比率19.75%と高めですが、返金率は1.63%と低水準です。売上規模8.1億円は拠点別で3番目に大きく、出荷点数の割に高単価な傾向がうかがえます。', '東住吉': 'SR発生率3.39%・返金率2.43%と、主要拠点の中では最も高い水準です。', '名古屋': 'SR発生率2.30%・返金率1.59%・ジャンク出荷比率14.17%と、いずれも中位からやや低めの水準で安定しています。', '札幌': 'SR発生率2.13%・返金率1.55%と全拠点中もっとも低い部類で、出荷規模8,589件は最小です。'}}


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["drive", "local"], required=True)
    parser.add_argument("--local-dir", help="local モード時のキャッシュディレクトリルート")
    parser.add_argument("--credentials", help="drive モード時のサービスアカウントJSONパス")
    parser.add_argument("--output", required=True, help="出力JSONパス")
    args = parser.parse_args()

    if args.mode == "local":
        if not args.local_dir:
            parser.error("--mode local の場合は --local-dir が必要です")
        backend: BaseDriveBackend = LocalCacheDriveBackend(args.local_dir, FISCAL_ROOT_ID)
    else:
        backend = LiveDriveBackend(args.credentials)

    print(f"[INFO] {FISCAL_YEAR_LABEL} フォルダ(root)を探索中...")
    weeks = discover_week_files(backend, FISCAL_ROOT_ID)
    print(f"[INFO] 検出した週フォルダ数: {len(weeks)}")
    for w in weeks:
        found = sorted(w.files.keys())
        print(f"  - {w.week_start}~{w.week_end}: {found}")

    rows, stats, merged = build_dashboard_rows(weeks)
    sr_major_rows = build_sr_major_rows(weeks, stats)
    cause_rows = build_cause_rows(weeks, stats)

    shipping_fee_master = build_shipping_fee_master(weeks)
    shukka_detail = build_shukka_detail(weeks, stats, shipping_fee_master)
    condition_rows = build_condition_rows(shukka_detail)
    price_band_rows = build_price_band_rows(shukka_detail)
    profit_variance_rows = build_profit_variance_rows(shukka_detail)

    # D/F項目: カテゴリ別詳細粗利指標・赤字(原価割れ)分析(build_shukka_detailの出力を再利用)
    cost_master = build_product_cost_master(weeks)
    category_profit_detail_rows = build_category_profit_detail_rows(shukka_detail)
    deficit_rows = build_deficit_rows(weeks, shukka_detail, cost_master)

    # ⑨ SRリピーター・ロイヤルカスタマー分析(顧客名寄せ)。
    # PIIを含む lookup は customer_lookup.csv にのみ書き出し、JSONには入れない。
    customer_segment_rows, customer_lookup, customer_meta = build_customer_segment_rows(weeks, cost_master)
    write_customer_lookup_csv(customer_lookup, Path(args.output).parent / "customer_lookup.csv")
    print(f"[INFO] customer_segment_rows={len(customer_segment_rows)}件, 名寄せ顧客数={customer_meta.get('cluster_count')}")

    print_monthly_summary(merged)
    print_exclusion_stats(stats)
    print(f"\n[INFO] sr_major_rows={len(sr_major_rows)}件, cause_rows={len(cause_rows)}件")
    print(
        f"[INFO] condition_rows={len(condition_rows)}件, price_band_rows={len(price_band_rows)}件, "
        f"profit_variance_rows={len(profit_variance_rows)}件"
    )
    print(
        f"[INFO] category_profit_detail_rows={len(category_profit_detail_rows)}件, "
        f"deficit_rows={len(deficit_rows)}件"
    )
    if not shukka_detail.empty:
        v = shukka_detail["variance"]
        print(
            f"[INFO] 粗利差異(全期間合計): 件数={len(v)}, 上振れ={int((v>0).sum())}件/{v[v>0].sum():,.0f}円, "
            f"下振れ={int((v<0).sum())}件/{v[v<0].sum():,.0f}円, 差異合計={v.sum():,.0f}円"
        )

    data_through = compute_data_through(weeks)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fiscal_year_label": FISCAL_YEAR_LABEL,
        "fiscal_year_start": FISCAL_YEAR_START,
        "data_through": data_through,
        "rows": rows,
        "sr_major_rows": sr_major_rows,
        "cause_rows": cause_rows,
        "condition_rows": condition_rows,
        "price_band_rows": price_band_rows,
        "profit_variance_rows": profit_variance_rows,
        "category_profit_detail_rows": category_profit_detail_rows,
        "deficit_rows": deficit_rows,
        "customer_segment_rows": customer_segment_rows,
        "insights": STATIC_INSIGHTS,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[INFO] 出力完了: {out_path} (rows={len(rows)})")


if __name__ == "__main__":
    main()
