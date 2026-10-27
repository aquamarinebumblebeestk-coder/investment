# jpstock — 日本株 銘柄分析ツール (たたき台)

[J-Quants API](https://jpx-jquants.com/ja) (V2) を使って、銘柄コードを入力すると以下を表示します。

| 表示内容 | 取得元 |
| --- | --- |
| 株価・出来高 (直近10営業日、期間高安値、20日平均出来高) | `/equities/bars/daily` |
| 売上高・営業利益・純利益 | `/fins/summary` (決算短信) |
| EPS・PER・PBR・ROE・配当利回り・時価総額 | Free〜Standard: 株価と決算から計算 / Premium: `/equities/valuation` |
| 配当金 (年間配当の実績・会社予想、配当性向) | `/fins/summary`。Premium では `/fins/dividend` の権利落ち日等も表示 |
| 過去数年間の業績推移 (増収率・営業利益率・来期会社予想) | `/fins/summary` |

## セットアップ (PC)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # JQUANTS_API_KEY にダッシュボードで発行した API キーを設定
```

## 使い方

```bash
jpstock 7203                 # 1銘柄
jpstock "7203,6758,9984"     # 複数銘柄はカンマ区切り
jpstock                      # 引数なしなら対話入力
jpstock 7203 --plan light    # プランを一時的に指定 (既定は .env の JQUANTS_PLAN)
jpstock 7203 --days 180      # 株価の取得日数
```

- 銘柄コードは4桁 (`130A` のような英字入り新コード可) または5桁 (`72030`)。全角数字・全角カンマも受け付けます。
- 終了コード: 0 正常 / 1 入力エラー / 2 取得できない環境のため中断 / 3 一部銘柄の取得に失敗

## 取得できない環境での動作

取得処理の前にプリフライトチェックを行い、以下のいずれかに当てはまると**警告を出して中断**します (終了コード 2)。

- クラウド/リモート実行環境 (スマホ等から Claude Code に実行を指示した場合など。`CLAUDE_CODE_REMOTE` で判定)
- API キー (`JQUANTS_API_KEY`) が未設定
- `api.jquants.com:443` に接続できない

ネットワーク許可を設定済みのクラウド環境など、接続できると分かっている場合に限り `JPSTOCK_ALLOW_REMOTE=1` でリモート判定を無効化できます。

## プランと拡張性

プランごとの差異は `src/jpstock/plans.py` に集約しています。有料プランに移行する場合は `.env` の `JQUANTS_PLAN` を変更するだけで、レート制限・取得期間・利用機能が切り替わります。

| プラン | リクエスト/分 | 提供期間 | 遅延 | 本ツールで追加される機能 |
| --- | --- | --- | --- | --- |
| Free | 5 | 過去2年 | 12週間 | — |
| Light | 60 | 過去5年 | なし | — |
| Standard | 120 | 過去10年 | なし | — |
| Premium | 500 | 過去20年 | なし | 配当詳細、J-Quants算出のバリュエーション指標 |

- 値は 2026-10 時点の公式ドキュメント調査に基づきます。最新情報は公式サイトで確認し、差異があれば `plans.py` を修正してください。
- `/equities/valuation` の対応プランは未確認のため Premium のみ有効にしています。403 が返った場合は自前計算にフォールバックします。
- Free プランは 5 リクエスト/分のため、1銘柄あたり約3リクエスト → 複数銘柄では待ち時間が発生します。
- Free プランは提供期間が2年のため、業績推移も直近2期程度になります。

## 開発

```bash
python -m pytest
```

構成:

```
src/jpstock/
  cli.py        コマンドライン入口
  codes.py      銘柄コード入力のパース
  config.py     .env / 環境変数の読み込み
  plans.py      J-Quants プラン定義 (レート制限・期間・機能)
  preflight.py  実行前チェック (取得できない環境なら中断)
  client.py     J-Quants API V2 クライアント
  analyzer.py   データ整形・指標計算
  report.py     テキスト表示
```

## 今後の拡張候補

- 取得結果のキャッシュ (Free プランのレート制限対策)
- CSV / Excel 出力、株価チャート (matplotlib) の画像出力
- 複数銘柄の比較表
