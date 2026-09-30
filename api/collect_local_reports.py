#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自治体の独自週報コレクタ（東京都23特別区 / 静岡市 / 京都市）

これら3自治体は、国のJIHS週報（都道府県単位・週1回）とは別に、
それぞれの公式週報を独自の週で公開している。
本スクリプトは各自治体の公式PDFから、その自治体自身の値
（報告数・定点当たり報告数）を抽出し spotlight_seed.json に取り込む。

出典:
  東京都  東京都感染症週報（保健所別・区単位）
          https://idsc.tmiph.metro.tokyo.lg.jp/weekly/
  静岡市  静岡市内の感染症発生状況（静岡市保健所）
          https://www.city.shizuoka.lg.jp/s2371/s013029.html
  京都市  京都市感染症週報（京都市感染症情報センター）
          https://www.city.kyoto.lg.jp/hokenfukushi/page/0000074152.html

使い方:
  python collect_local_reports.py --seed api/spotlight_seed.json --weeks 38
  python collect_local_reports.py --seed api/spotlight_seed.json  # 直近未収録週を自動
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

try:
    import pdfplumber
except ImportError:
    print('pdfplumber が必要です: pip install pdfplumber', file=sys.stderr)
    raise

YEAR = 2026
UA = {'User-Agent': 'Mozilla/5.0 (compatible; kansensho-weekly-nav/1.0)'}

# ---- 抽出対象の8疾患（アプリの疾患IDに合わせる） ----------------------------
# 日本語ラベル（自治体表記のゆれを吸収） -> 疾患ID
JP_TO_ID = {
    'インフルエンザ': 'influenza',
    '新型コロナウイルス': 'covid19',
    '新型コロナウイルス感染症': 'covid19',
    'ＲＳウイルス感染症': 'rsv',
    'RSウイルス感染症': 'rsv',
    '咽頭結膜熱': 'pharyngoconjunctival_fever',
    '感染性胃腸炎': 'infectious_gastroenteritis',
    '手足口病': 'hand_foot_mouth',
    'ヘルパンギーナ': 'herpangina',
    'マイコプラズマ肺炎': 'mycoplasma',
}
DISEASES = ['influenza', 'covid19', 'rsv', 'pharyngoconjunctival_fever',
            'infectious_gastroenteritis', 'hand_foot_mouth', 'herpangina',
            'mycoplasma']


def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def num(x):
    x = (x or '').replace(',', '').replace('\n', '').strip()
    if x in ('-', '', '…', '－'):
        return 0.0
    # 「4-」のように数字と記号が連結した場合の先頭数値も拾う
    m = re.match(r'^(\d+(?:\.\d+)?)', x)
    return float(m.group(1)) if m else 0.0


# ==========================================================================
# 東京都（保健所別 = 23特別区）
# ==========================================================================
PDF_LABEL_TO_WARD = {
    '千代田': '千代田区', '中央区': '中央区', 'みなと': '港区', '新宿区': '新宿区',
    '文京': '文京区', '台東': '台東区', '墨田区': '墨田区', '江東区': '江東区',
    '品川区': '品川区', '目黒区': '目黒区', '大田区': '大田区', '世田谷': '世田谷区',
    '渋谷区': '渋谷区', '中野区': '中野区', '杉並': '杉並区', '豊島区': '豊島区',
    '北区': '北区', '荒川区': '荒川区', '板橋区': '板橋区', '練馬区': '練馬区',
    '足立': '足立区', '葛飾区': '葛飾区', '江戸川': '江戸川区',
}
# 保健所別表の列位置（0始まり）
PEDIA_COL = {'rsv': 1, 'pharyngoconjunctival_fever': 2,
             'infectious_gastroenteritis': 4, 'hand_foot_mouth': 6, 'herpangina': 9}
RESP_COL = {'influenza': 1, 'covid19': 2, 'mycoplasma': 8}
DEC = re.compile(r'^\d+\.\d+$')


def _tokyo_page_kind(pg):
    tbls = pg.extract_tables()
    if not tbls or not tbls[0] or len(tbls[0]) < 2:
        return None, None
    t0 = tbls[0]
    top = (t0[0][1] or '').replace('\n', '') if len(t0[0]) > 1 else ''
    if '小児科' in top:
        kind = 'pedia'
    elif '急性呼吸器' in top:
        kind = 'resp'
    else:
        return None, None
    # 保健所別表かどうか: 先頭データ行のラベルが区名であること
    # (年齢階級別表は '～５か月' 等になるため誤検出を防ぐ)
    label = (t0[2][0] or '').replace('\n', '').strip() if len(t0) > 2 and t0[2] else ''
    if label not in PDF_LABEL_TO_WARD:
        return None, None
    has_dec = any(c and DEC.match(c.replace(',', '').strip())
                  for row in t0[2:] for c in (row[1:] if row else []))
    return kind, ('per1' if has_dec else 'cnt')


def _tokyo_classify(pdf):
    idx = {}
    for i, pg in enumerate(pdf.pages):
        kind, mode = _tokyo_page_kind(pg)
        if kind:
            # per1(定点当たり) と cnt(報告数) を別々に、しかも小児科/呼吸器で分ける
            idx.setdefault((kind, mode), i)
    return idx


def _tokyo_parse(pdf, idx, cols):
    out = {}
    for tbl in pdf.pages[idx].extract_tables():
        for row in tbl:
            if not row:
                continue
            label = (row[0] or '').replace('\n', '').strip()
            if label in PDF_LABEL_TO_WARD:
                out[label] = {did: num(row[ci]) if ci < len(row) else 0.0
                              for did, ci in cols.items()}
    return out


def collect_tokyo(week):
    """東京都: 23特別区すべてを {区内: [[count, per]...]} で返す（1週分）"""
    url = f'https://idsc.tmiph.metro.tokyo.lg.jp/assets/weekly/{YEAR}/{week:02d}.pdf'
    raw = fetch(url)
    res = {}
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        idx = _tokyo_classify(pdf)
        need = [('pedia', 'per1'), ('resp', 'per1'), ('pedia', 'cnt'), ('resp', 'cnt')]
        if any(k not in idx for k in need):
            raise RuntimeError(f'東京都 week{week}: 保健所別表の検出に失敗 {list(idx)}')
        def _merge(*parsed):
            merged = {}
            for d in parsed:
                for label, dis in d.items():
                    merged.setdefault(label, {}).update(dis)
            return merged
        per = _merge(_tokyo_parse(pdf, idx[('pedia', 'per1')], PEDIA_COL),
                     _tokyo_parse(pdf, idx[('resp', 'per1')], RESP_COL))
        cnt = _merge(_tokyo_parse(pdf, idx[('pedia', 'cnt')], PEDIA_COL),
                     _tokyo_parse(pdf, idx[('resp', 'cnt')], RESP_COL))
    for label, ward in PDF_LABEL_TO_WARD.items():
        if label not in per or label not in cnt:
            continue
        res[ward] = [[cnt[label].get(d, 0.0), per[label].get(d, 0.0)] for d in DISEASES]
    return {'東京都' + w: v for w, v in res.items()}


# ==========================================================================
# 静岡市（市保健所の週報・直近1週の年齢階級別表）
# ==========================================================================
def _shizuoka_url(week):
    """週報一覧ページから該当週のPDFリンクを探す（ファイル名の揺れを吸収）"""
    list_url = 'https://www.city.shizuoka.lg.jp/s2371/s013029.html'
    html = fetch(list_url).decode('utf-8', 'ignore')
    links = re.findall(r'href="(/documents/55002/[^"]+\.pdf)"', html)
    # ファイル名に 2026 と {week}w を含む候補を優先
    cands = []
    for l in links:
        m = re.search(r'(\d+)[wW]?\.pdf$', l)
        if m and int(m.group(1)) == week:
            cands.append(l)
    if not cands:
        return None
    return 'https://www.city.shizuoka.lg.jp' + cands[0]


def collect_shizuoka(week):
    """静岡市: {静岡市: [[count, per]...]}（1週分）。page0 の年齢階級別表の総数を使用。"""
    url = _shizuoka_url(week)
    if not url:
        raise RuntimeError(f'静岡市 week{week}: PDFリンクが見つかりません')
    raw = fetch(url)
    out = {}
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        text = pdf.pages[0].extract_text() or ''
    lines = [l.strip() for l in text.split('\n')]
    for i, ln in enumerate(lines):
        if ln.startswith('報告 ') or ln.startswith('報告\t'):
            # 次の行が疾患名、その次が「定当 ...」
            name = lines[i + 1] if i + 1 < len(lines) else ''
            per_line = lines[i + 2] if i + 2 < len(lines) else ''
            if not per_line.startswith('定当'):
                continue
            did = JP_TO_ID.get(name.strip())
            if not did:
                continue
            cnt = num(ln.split()[1]) if len(ln.split()) > 1 else 0.0
            per = num(per_line.split()[1]) if len(per_line.split()) > 1 else 0.0
            out[did] = [cnt, per]
    if not out:
        raise RuntimeError(f'静岡市 week{week}: 表の解析に失敗')
    return {'静岡市': [out.get(d, [0.0, 0.0]) for d in DISEASES]}


# ==========================================================================
# 京都市（行政区別の報告数・定点当たり、京都市計）
# ==========================================================================
KYOTO_URL = 'https://www.city.kyoto.lg.jp/hokenfukushi/cmsfiles/contents/0000074/74152/syuho{year}{week}.pdf'
KYOTO_LABELS = {
    'インフルエンザ': 'influenza',
    '新型コロナウイルス感染症': 'covid19',
    'ＲＳウイルス感染症': 'rsv',
    '咽頭結膜熱': 'pharyngoconjunctival_fever',
    '感染性胃腸炎': 'infectious_gastroenteritis',
    '手足口病': 'hand_foot_mouth',
    'ヘルパンギーナ': 'herpangina',
    'マイコプラズマ肺炎': 'mycoplasma',
}


def _kyoto_norm(s):
    s = (s or '').replace('\n', '').replace(' ', '')
    s = re.sub(r'[（(].*?[）)]', '', s)  # 注記 (※１) 等を除去
    return s.strip()


def _kyoto_disease(label):
    """ラベル文字列から疾患IDを判定（ロタウイルス等の誤検出を除外）"""
    if 'ロタ' in label:
        return None
    if 'インフルエンザ' in label:
        return 'influenza'
    if '新型コロナ' in label:
        return 'covid19'
    if 'ＲＳウイルス' in label or 'RSウイルス' in label:
        return 'rsv'
    if '咽頭結膜熱' in label:
        return 'pharyngoconjunctival_fever'
    if '感染性胃腸炎' in label:
        return 'infectious_gastroenteritis'
    if '手足口病' in label:
        return 'hand_foot_mouth'
    if 'ヘルパンギーナ' in label:
        return 'herpangina'
    if 'マイコプラズマ肺炎' in label:
        return 'mycoplasma'
    return None


def collect_kyoto(week):
    """京都市: {京都市: [[count, per]...]}（1週分）。

    行政区別ページ（行政区別※４）の「京都市計」列を用いる。
    報告数（整数）と定点当たり報告数（小数）の両方が同じページに載る。
    """
    url = KYOTO_URL.format(year=YEAR, week=week)
    raw = fetch(url)
    counts, pers = {}, {}
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page_text = None
        for pg in pdf.pages[:8]:
            t = pg.extract_text() or ''
            if '行政区別' in t and '京都市計' in t:
                page_text = t
                break
        if page_text is None:
            raise RuntimeError(f'京都市 week{week}: 行政区別ページが見つかりません')
    for ln in page_text.split('\n'):
        tokens = ln.split()
        if len(tokens) < 2:
            continue
        # 末尾の数値/記号トークンを値、それ以外の先頭部分をラベルとする
        vals, i = [], len(tokens)
        while i > 0:
            tk = tokens[i - 1]
            if re.match(r'^[\d.,]+$', tk) or tk in ('-', '…', '－', '‐'):
                vals.insert(0, tk)
                i -= 1
            else:
                break
        if not vals:
            continue
        label = ' '.join(tokens[:i])
        did = _kyoto_disease(label)
        if not did:
            continue
        city = vals[-1]  # 最終列 = 京都市計
        is_per = '.' in city
        (pers if is_per else counts)[did] = num(city)
    if not counts and not pers:
        raise RuntimeError(f'京都市 week{week}: 表の解析に失敗')
    return {'京都市': [[counts.get(d, 0.0), pers.get(d, 0.0)] for d in DISEASES]}


# ==========================================================================
# 最新週の探索とシード更新
# ==========================================================================
def latest_week_tokyo(max_probe=60):
    last = None
    for w in range(1, max_probe + 1):
        url = f'https://idsc.tmiph.metro.tokyo.lg.jp/assets/weekly/{YEAR}/{w:02d}.pdf'
        try:
            req = urllib.request.Request(url, headers=UA, method='HEAD')
            with urllib.request.urlopen(req, timeout=30):
                last = w
        except Exception:
            if last is not None and w > last + 1:
                break
    return last


def latest_week_kyoto(max_probe=60):
    last = None
    for w in range(1, max_probe + 1):
        url = KYOTO_URL.format(year=YEAR, week=w)
        try:
            req = urllib.request.Request(url, headers=UA, method='HEAD')
            with urllib.request.urlopen(req, timeout=30):
                last = w
        except Exception:
            if last is not None and w > last + 1:
                break
    return last


def latest_week_shizuoka(max_probe=60):
    html = fetch('https://www.city.shizuoka.lg.jp/s2371/s013029.html').decode('utf-8', 'ignore')
    links = re.findall(r'href="(/documents/55002/[^"]+\.pdf)"', html)
    weeks = []
    for l in links:
        m = re.search(r'(\d+)[wW]?\.pdf$', l)
        if m and int(m.group(1)) <= max_probe:
            weeks.append(int(m.group(1)))
    return max(weeks) if weeks else None


COLLECTORS = {
    'tokyo': (collect_tokyo, latest_week_tokyo),
    'shizuoka': (collect_shizuoka, latest_week_shizuoka),
    'kyoto': (collect_kyoto, latest_week_kyoto),
}


def week_label(week):
    from datetime import date, timedelta
    d = date(YEAR, 7, 13) + timedelta(days=(week - 29) * 7)
    return f'{d.month}/{d.day}'


def update_seed(seed, weeks, sources=('tokyo', 'shizuoka', 'kyoto'), verbose=True):
    """指定週の各自治体データを seed に追記・更新する。"""
    for src in sources:
        fn, _ = COLLECTORS[src]
        for w in weeks:
            try:
                data = fn(w)
            except Exception as e:
                if verbose:
                    print(f'  [{src}] week{w} 取得失敗: {e}', file=sys.stderr)
                continue
            for key, vals in data.items():
                if key not in seed:
                    continue  # 収録対象外のキーは追加しない
                for did, val in zip(DISEASES, vals):
                    seed[key].setdefault(did, {})
                    if isinstance(seed[key][did], list):
                        # 既に整列済みリストの場合はスキップ
                        continue
                    seed[key][did][str(w)] = [round(v, 2) for v in val]
            if verbose:
                print(f'  [{src}] week{w} 取り込み: {len(data)}地域')
    return seed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='spotlight_seed.json')
    ap.add_argument('--weeks', type=str, default=None,
                    help='カンマ区切りの週番号。未指定なら最新週を自動判定')
    ap.add_argument('--sources', type=str, default='tokyo,shizuoka,kyoto')
    args = ap.parse_args()

    seed_path = args.seed
    seed = json.load(open(seed_path, encoding='utf-8')) if os.path.exists(seed_path) else {}

    srcs = tuple(s.strip() for s in args.sources.split(',') if s.strip())
    if args.weeks:
        weeks = [int(w) for w in args.weeks.split(',')]
    else:
        weeks = []
        for src in srcs:
            _, latest_fn = COLLECTORS[src]
            try:
                latest = latest_fn()
            except Exception as e:
                print(f'[{src}] 最新週の判定に失敗: {e}', file=sys.stderr)
                continue
            if latest:
                print(f'[{src}] 最新週 = 第{latest}週 ({week_label(latest)})')
                weeks.append(latest)
        weeks = sorted(set(weeks))

    if not weeks:
        print('取得対象の週がありません', file=sys.stderr)
        return 1

    update_seed(seed, weeks, sources=srcs)
    with open(seed_path, 'w', encoding='utf-8') as f:
        json.dump(seed, f, ensure_ascii=False, indent=1)
    print(f'保存: {seed_path}  週={weeks}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
