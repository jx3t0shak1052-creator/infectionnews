#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
感染症週報ナビ 中継APIビルダー
- 週1回、JIHS(厚労省)の都道府県別週報CSVを自動取得してJSONを生成
- 市区町村レベルのスポットデータ(静岡市/東京都中央区/京都市)は同梱シードを整列して配信

出力JSONの形（Flutter 側 SurveillanceDataset.fromJson がそのまま読める形）:
{
  "version": "...",
  "generatedAt": "...",
  "period": "2026年第29〜37週",
  "weekLabels": ["7/13", ..., "9/7"],           # 全週（古→新）
  "prefectures": { "<都道府県>": { "<疾患ID>": [[報告数, 定点当たり] × 取得できた週] } },
  "spotlight":   { "<地域キー>": { "<疾患ID>": [[報告数, 定点当たり] × 全週] } }  # 欠測週は [0,0]
}
"""
import os
import json
import re
import time
import urllib.request
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 出力先: API はリポジトリ直下の api/surveillance.json（Web配信・GitHub Pages 共通）
API_DIR = BASE_DIR
os.makedirs(API_DIR, exist_ok=True)

# 中継APIの成果物（このファイルを Web・GitHub Pages がそのまま配信する）
OUTPUT_JSON = os.path.join(API_DIR, 'surveillance.json')

# --- 対象週（新しい週が公開されたら自動で拾う） ------------------------------
YEAR = 2026
BASE_WEEK = 29                 # 週番号の起点
BASE_DATE = date(2026, 7, 13)  # 第29週の週初め（月曜）
MAX_WEEK = 40                  # これ以降は探索しない
JIHS_URL = 'https://id-info.jihs.go.jp/surveillance/idwr/provisional/{y}/{w}/{y}-{w}-teiten.csv'

# JIHSの列位置 -> アプリの疾患ID（0始まりの列インデックス）
DISEASE_COLS = [
    ('influenza', 1),
    ('rsv', 3),
    ('pharyngoconjunctival_fever', 5),
    ('infectious_gastroenteritis', 9),
    ('hand_foot_mouth', 13),
    ('herpangina', 19),
    ('mycoplasma', 31),
    ('covid19', 37),
]
PREF_NAMES = [
    '北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県', '群馬県',
    '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県',
    '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県',
    '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県', '香川県', '愛媛県', '高知県', '福岡県',
    '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県',
]


def week_label(w):
    """週番号 -> 'M/D'（例: 29 -> '7/13', 37 -> '9/7'）"""
    d = BASE_DATE + timedelta(days=(w - BASE_WEEK) * 7)
    return f'{d.month}/{d.day}'


def _to_num(x):
    x = (x or '').strip()
    if x in ('-', '', '…'):
        return 0.0
    try:
        return float(x)
    except ValueError:
        return 0.0


def _fetch_week(w):
    url = JIHS_URL.format(y=YEAR, w=w)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    for enc in ('shift_jis', 'cp932', 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError('decode failed')


def build_provisional():
    """JIHSから都道府県別データを構築。

    Returns:
        (data, weeks)
        data:  pref -> diseaseId -> [[cases, per], ...]（取得できた週のみ・古→新）
        weeks: 取得できた週番号のリスト（古→新）
    """
    data = {p: {d[0]: [] for d in DISEASE_COLS} for p in PREF_NAMES}
    weeks = []
    for w in range(BASE_WEEK, MAX_WEEK + 1):
        try:
            text = _fetch_week(w)
        except Exception:  # 未公開週(404)などはスキップ
            continue
        weeks.append(w)
        lines = text.replace('\r\n', '\n').split('\n')
        for ln in lines[5:]:
            cols = [c.strip('"') for c in ln.split(',')]
            if not cols or cols[0] not in PREF_NAMES:
                continue
            pref = cols[0]
            for did, idx in DISEASE_COLS:
                if idx + 1 < len(cols):
                    data[pref][did].append([_to_num(cols[idx]), _to_num(cols[idx + 1])])
    return data, weeks


def _load_spotlight_seed():
    path = os.path.join(BASE_DIR, 'spotlight_seed.json')
    if os.path.exists(path):
        return json.load(open(path, encoding='utf-8'))
    return {}


def _align_spotlight(seed, all_weeks):
    """シード({week: [cases,per]}) を all_weeks に整列（欠測週は [0,0]）。"""
    out = {}
    for key, diseases in seed.items():
        dm = {}
        for did, series in diseases.items():
            if isinstance(series, dict):
                dm[did] = [list(series.get(str(w), [0.0, 0.0])) for w in all_weeks]
            elif isinstance(series, list):
                # すでに整列済みのリストならそのまま
                dm[did] = series
        out[key] = dm
    return out


def refresh(output_path):
    prov, pref_weeks = build_provisional()
    seed = _load_spotlight_seed()

    # スポット側の週番号を収集
    spot_weeks = set()
    for diseases in seed.values():
        for series in diseases.values():
            if isinstance(series, dict):
                for k in series.keys():
                    try:
                        spot_weeks.add(int(k))
                    except ValueError:
                        pass

    all_weeks = sorted(set(pref_weeks) | spot_weeks)
    if not all_weeks:
        all_weeks = list(range(BASE_WEEK, BASE_WEEK + 9))

    payload = {
        'version': time.strftime('%Y%m%d%H%M%S'),
        'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'period': f'{YEAR}年第{all_weeks[0]}〜{all_weeks[-1]}週',
        'weekLabels': [week_label(w) for w in all_weeks],
        'prefectures': prov,
        'spotlight': _align_spotlight(seed, all_weeks),
    }
    tmp = output_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, output_path)
    return payload


def maybe_refresh():
    """1日1回だけ更新（ファイルの更新日時で判定）"""
    out = OUTPUT_JSON
    if os.path.exists(out) and time.time() - os.path.getmtime(out) < 86400:
        return
    try:
        refresh(out)
        print(f'[api] refreshed {out}')
    except Exception as e:  # noqa
        print(f'[api] refresh failed: {e}')


def ensure_data():
    out = OUTPUT_JSON
    if not os.path.exists(out):
        try:
            refresh(out)
        except Exception as e:  # noqa
            print(f'[api] initial build failed: {e}')


if __name__ == '__main__':
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='今すぐ再取得')
    args = ap.parse_args()
    out = OUTPUT_JSON
    if args.refresh or not os.path.exists(out):
        p = refresh(out)
        print('done:', p['period'], p['weekLabels'])
    else:
        ensure_data()
        print('up to date')
