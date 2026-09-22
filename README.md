# 感染症週報ナビ 中継API（permanent hosting）

感染症週報ナビ（Flutter アプリ）向けの **中継API** です。
国立健康危機管理研究機構（JIHS）が公開する「感染症発生動向調査 週報（速報値）」を
取得・整形して JSON で配信します。GitHub Pages で恒久的に公開され、
GitHub Actions により **毎週自動更新** されます。

## エンドポイント

```
GET https://jx3t0shak1052-creator.github.io/infectionnews/api/surveillance.json
```

## レスポンス（抜粋）

```json
{
  "version": "20260922162331",
  "generatedAt": "2026-09-22T16:23:31",
  "period": "2026年第29〜37週",
  "weekLabels": ["7/13", "7/20", "7/27", "8/3", "8/10", "8/17", "8/24", "8/31", "9/7"],
  "prefectures": {
    "東京都": { "influenza": [[97, 0.23], [138, 0.33]] }
  },
  "spotlight": {
    "静岡市": { "influenza": [[4, 0.22]] }
  }
}
```

- `prefectures`: 47都道府県 × 8疾患。各値は `[報告数, 定点当たり]`（古→新）。
- `spotlight`: 静岡市・東京都中央区・京都市の市区町村別公式データ。

## 仕組み

```
JIHS 週報CSV ──(毎週 cron)──> build_api.py ──> api/surveillance.json ──> GitHub Pages ──> アプリ
```

- `.github/workflows/update-data.yml` が毎週火曜 06:00 UTC に実行。
- `api/build_api.py` が JIHS の都道府県別週報 CSV を取得し、`api/surveillance.json` を生成。
- 生成物を自動コミット → GitHub Pages が配信。

## ローカルでの再生成

```bash
cd api
python build_api.py --refresh
```

## データ出典

- 感染症発生動向調査 週報（速報値）: 国立健康危機管理研究機構 (JIHS)
- 静岡市 感染症発生動向調査（週報）
- 東京都感染症週報（保健所別） / 京都府 感染症発生動向調査 週報（地域別集計）

数値は速報値であり、後日修正される場合があります。
