# CLAUDE.md

## 開発方針
- このリポジトリの主言語は **Python** (3.10 以上)。新しいコード・スクリプト・ツールは原則 Python で書く。
- ソースは `src/jpstock/`、テストは `tests/` (pytest)。テストは `python -m pytest` で実行する。
- 外部依存は最小限にする (必須は `requests` のみ。`--ai` 用の `anthropic` は extras `ai`)。追加するときは `pyproject.toml` に記載する。

## J-Quants API
- V2 API (`https://api.jquants.com/v2`、`x-api-key` ヘッダ認証) を使う。V1 は廃止済み。
- プランごとの差 (レート制限・提供期間・遅延・利用可能機能) は `src/jpstock/plans.py` に集約する。
  プラン依存の分岐は `Plan.supports(Feature.X)` などプラン定義経由で行い、プラン名の直接比較はしない。
- 現在の契約は Free プランの想定 (5リクエスト/分、過去2年分、12週間遅延)。

## 実行環境についての注意 (Claude 向け)
- 判断基準は「どこから指示されたか」ではなく「どこで実行されているか」。
  - クラウド環境 (Claude Code on the web 等、`CLAUDE_CODE_REMOTE=true`) では J-Quants API に接続できない。
    そこで API 取得を伴う銘柄分析を指示されたら、取得を試みる前に「取得できない環境である」旨を警告して中断すること。
    ツール自体もプリフライトチェック (`src/jpstock/preflight.py`) で同じ判定を行い、終了コード 2 で中断する。
  - PC 上で動いているセッション (スマホから Remote Control で操作している場合を含む) では実行してよい。
- 保存済みデータからの分析 (`--from-saved`) は API を使わないため、どの環境でも実行してよい。
- コードの修正やテスト (フェイクデータを使うもの) はリモート環境でも行ってよい。
- J-Quants の取得データ (`data/`) は再配布不可。公開リポジトリのため、コミット・push しないこと。
