"""Claude による分析レポート生成。

ツールが計算した結果 (指標・財務分析・テクニカル分析・総合評価) を JSON にまとめて
Claude API に渡し、日本語の分析コメントを書いてもらう。
利用には `pip install -e ".[ai]"` と Anthropic の API キー (ANTHROPIC_API_KEY 等) が必要。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from .analyzer import StockReport

DEFAULT_MODEL = "claude-opus-5-5"
ENV_MODEL = "JPSTOCK_AI_MODEL"
EFFORT = "high"
MAX_TOKENS = 32000
RECENT_PRICE_DAYS = 60
# 安全性分類器による拒否時に、推奨モデルでサーバー側が自動再実行する
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
あなたは日本株を分析する証券アナリストです。ユーザーが渡す JSON は、J-Quants API から取得したデータと、
それを元にツールが計算した指標・財務分析・テクニカル分析・総合評価です。

分析の方針:
- 数値は JSON に含まれるものだけを根拠にしてください。JSON にない数値や最新ニュースを推測で補わないでください。
- 金額の単位は円、比率は小数 (0.12 = 12%) です。ただし API 由来の一部の比率は % 表記の可能性があるので、桁が不自然なら注意書きを添えてください。
- notes にデータの遅延や提供期間の制約が書かれている場合は、その前提を明記してください。
- 財務 (収益性・成長性・安全性・キャッシュフロー) とテクニカル (トレンド・過熱感) の両面から評価し、両者が食い違う場合はその点を説明してください。
- ツールの総合評価 (score) は機械的な採点です。業種特性などで評価が歪んでいると考えられる場合は指摘してください。
- 投資判断はユーザー自身が行う前提で、売買の推奨や目標株価の断定はしないでください。

出力形式 (Markdown):
- 銘柄ごとに「## [コード] 銘柄名」の見出しを付け、次の小見出しで書く:
  概要 / 業績と財務 / バリュエーション / テクニカル / 注目点・リスク / まとめ (3行以内)
- 銘柄が2つ以上ある場合は最後に「## 銘柄比較」を設け、観点別にどの銘柄が優れているかを比較する。
"""


class AIAnalysisError(RuntimeError):
    pass


def report_payload(r: StockReport) -> dict[str, Any]:
    """Claude に渡す1銘柄分のデータ。株価は直近分だけに絞って入力を小さくする。"""
    ta = r.technical
    return {
        "code": r.code,
        "name": r.name,
        "market": r.market,
        "sector": r.sector,
        "fetched_at": r.fetched_at,
        "notes": r.notes,
        "indicators": asdict(r.indicators) if r.indicators else None,
        "annual_results": [asdict(a) for a in r.annual],
        "forecast": asdict(r.forecast) if r.forecast else None,
        "quarter_progress": asdict(r.progress) if r.progress else None,
        "financial_analysis": asdict(r.financial) if r.financial else None,
        "technical_analysis": (
            {**asdict(ta), "overall": ta.overall, "score": ta.score} if ta and ta.close is not None else None
        ),
        "score": (
            {
                "total_100": r.score.total_100,
                "summary": r.score.summary(),
                "categories": [asdict(c) for c in r.score.categories],
            }
            if r.score
            else None
        ),
        "recent_prices": [
            {"date": b.date, "close": b.close, "volume": b.volume} for b in r.prices[-RECENT_PRICE_DAYS:]
        ],
        "dividend_events": [asdict(d) for d in r.dividends],
    }


def build_user_message(reports: list[StockReport]) -> str:
    data = [report_payload(r) for r in reports if r.error is None]
    return (
        f"次の {len(data)} 銘柄を分析してください。\n\n"
        f"```json\n{json.dumps(data, ensure_ascii=False, indent=1)}\n```"
    )


def generate_analysis(
    reports: list[StockReport], *, model: str | None = None, out: TextIO = sys.stdout, client: Any = None
) -> str:
    """分析レポートを生成し、ストリーミングで out に書き出しつつ全文を返す。"""
    try:
        import anthropic
    except ImportError:
        raise AIAnalysisError('anthropic パッケージがありません。pip install -e ".[ai]" を実行してください') from None

    if not any(r.error is None for r in reports):
        raise AIAnalysisError("分析できる銘柄がありません")
    client = client or anthropic.Anthropic()
    model = model or os.environ.get(ENV_MODEL, DEFAULT_MODEL)

    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_message(reports)}],
        ) as stream:
            for text in stream.text_stream:
                out.write(text)
                out.flush()
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AIAnalysisError("Anthropic API の認証に失敗しました。ANTHROPIC_API_KEY を確認してください") from None
    except anthropic.RateLimitError:
        raise AIAnalysisError("Anthropic API のレート制限に達しました。しばらく待って再実行してください") from None
    except anthropic.APIStatusError as e:
        raise AIAnalysisError(f"Anthropic API エラー ({e.status_code}): {e.message}") from None
    except anthropic.APIConnectionError:
        raise AIAnalysisError("Anthropic API に接続できません。ネットワーク設定を確認してください") from None

    out.write("\n")
    if message.stop_reason == "refusal":
        raise AIAnalysisError("Claude が分析を辞退しました (安全性フィルタ)。入力内容を確認してください")
    if message.stop_reason == "max_tokens":
        out.write("\n(出力が上限に達したため途中で終了しています)\n")
    return "".join(b.text for b in message.content if b.type == "text")
