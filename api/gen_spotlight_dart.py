#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spotlight_seed.json から Flutter 同梱データ lib/data/spotlight_data.dart を生成する。

- 地域キー -> 疾患ID -> [[報告数, 定点当たり] × 週(古→新)] の形に整列
- 出典（kSpotlightSources）と出典URL（kSpotlightSourceUrls）も同時に出力
- 週ラベルは指定した週範囲から生成
"""
import argparse
import json
import os

DISEASES = ['influenza', 'covid19', 'rsv', 'pharyngoconjunctival_fever',
            'infectious_gastroenteritis', 'hand_foot_mouth', 'herpangina',
            'mycoplasma']

# 地域キー -> (出典名, 出典URL)
SOURCE_DEF = {
    '東京都': ('東京都感染症週報（保健所別）', 'https://idsc.tmiph.metro.tokyo.lg.jp/weekly/'),
    '静岡市': ('静岡市内の感染症発生状況（静岡市保健所）',
             'https://www.city.shizuoka.lg.jp/s2371/s013029.html'),
    '京都市': ('京都市感染症週報（京都市感染症情報センター）',
             'https://www.city.kyoto.lg.jp/hokenfukushi/page/0000074152.html'),
}

# 地域キーに含まれる接頭辞から自治体を判定
def source_for(key):
    if key.startswith('東京都'):
        return SOURCE_DEF['東京都']
    if key.startswith('静岡市'):
        return SOURCE_DEF['静岡市']
    if key.startswith('京都市'):
        return SOURCE_DEF['京都市']
    return ('公式週報', '')


def week_labels(weeks):
    from datetime import date, timedelta
    base = date(2026, 7, 13)  # 第29週の週初め（月曜）
    out = []
    for w in weeks:
        d = base + timedelta(days=(w - 29) * 7)
        out.append(f'{d.month}/{d.day}')
    return out


def fmt_series(vals):
    return '[' + ','.join(
        f'[{int(v[0]) if float(v[0]).is_integer() else v[0]},'
        f'{v[1] if not float(v[1]).is_integer() else int(v[1])}]'
        for v in vals) + ']'


def generate(seed_path, out_path, weeks):
    seed = json.load(open(seed_path, encoding='utf-8'))
    labels = week_labels(weeks)
    # 表示順: 静岡市 → 東京都23区 → 京都市（収録順を安定させる）
    def _prio(k):
        if k.startswith('静岡市'):
            return (0, k)
        if k.startswith('東京都'):
            return (1, k)
        if k.startswith('京都市'):
            return (2, k)
        return (3, k)
    keys = sorted(seed.keys(), key=_prio)

    lines = []
    lines.append('// 自動生成: 各自治体の公式週報データ（自治体ごとに独立して収集）')
    lines.append('// 出典: 東京都感染症週報(保健所別) / 静岡市保健所 / 京都市感染症情報センター')
    lines.append(f'// 対象: 2026年 第{weeks[0]}〜{weeks[-1]}週（{len(weeks)}週分）')
    lines.append('// 形式: 地域キー -> 疾患ID -> [[報告数, 1定点あたり報告数] × 週(古→新)]')
    lines.append('//')
    lines.append('// ▶ 運用方針（重要）:')
    lines.append('//   新しい地域を追加する際は、**必ずその地域の公式な感染症週報')
    lines.append('//   （当該自治体・保健所が公表する週報／オープンデータ）を直接取得して**')
    lines.append('//   解析した値をここに追加すること。他地域や親都道府県の値の流用は禁止。')
    lines.append('//   追加時は kSpotlightSources / kSpotlightSourceUrls にも出典を明記する。')
    lines.append('')
    lines.append('/// 週ラベル（古い→新しい）')
    lines.append(f'const List<String> kSpotlightWeekLabels = [{", ".join(chr(34)+l+chr(34) for l in labels)}];')
    lines.append('')
    lines.append('/// 地域キーごとの出典情報')
    lines.append('const Map<String, String> kSpotlightSources = {')
    for k in keys:
        name, _ = source_for(k)
        label = f'{name}・{k[len("東京都"):]}' if k.startswith('東京都') else name
        lines.append(f'  {json.dumps(k, ensure_ascii=False)}: {json.dumps(label, ensure_ascii=False)},')
    lines.append('};')
    lines.append('')
    lines.append('const Map<String, String> kSpotlightSourceUrls = {')
    for k in keys:
        _, url = source_for(k)
        lines.append(f'  {json.dumps(k, ensure_ascii=False)}: {json.dumps(url, ensure_ascii=False)},')
    lines.append('};')
    lines.append('')
    lines.append('const Map<String, Map<String, List<List<double>>>> kSpotlightData = {')
    for k in keys:
        lines.append(f'  {json.dumps(k, ensure_ascii=False)}: {{')
        dis = seed[k]
        for did in DISEASES:
            s = dis.get(did, {})
            if isinstance(s, dict):
                series = [s.get(str(w), [0, 0]) for w in weeks]
            else:
                series = s
            lines.append(f'    {json.dumps(did)}: {fmt_series(series)},')
        lines.append('  },')
    lines.append('};')
    lines.append('')

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'生成: {out_path}（{len(keys)}地域 × {len(weeks)}週）')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default=os.path.join(os.path.dirname(__file__), 'spotlight_seed.json'))
    ap.add_argument('--out', default=os.path.join(os.path.dirname(__file__), '..',
                                                  'lib', 'data', 'spotlight_data.dart'))
    ap.add_argument('--weeks', default='29-38')
    args = ap.parse_args()
    a, _, b = args.weeks.partition('-')
    weeks = list(range(int(a), int(b or a) + 1))
    generate(args.seed, args.out, weeks)
