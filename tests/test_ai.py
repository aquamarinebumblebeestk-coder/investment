import io
import json
from datetime import date
from types import SimpleNamespace

import pytest

from jpstock.ai import AIAnalysisError, build_user_message, generate_analysis
from jpstock.analyzer import Analyzer, StockReport
from jpstock.plans import get_plan

from fakes import FakeClient

anthropic = pytest.importorskip("anthropic")


def _report():
    return Analyzer(FakeClient(get_plan("free")), today=date(2026, 10, 27)).analyze("7203")[0]


class FakeStream:
    def __init__(self, chunks, stop_reason="end_turn"):
        self.text_stream = iter(chunks)
        self._msg = SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text="".join(chunks))]
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class FakeAnthropic:
    def __init__(self, stream):
        self.kwargs = None
        self._stream = stream
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._open))

    def _open(self, **kwargs):
        self.kwargs = kwargs
        return self._stream


def test_payload_is_json_with_analysis_results():
    msg = build_user_message([_report(), StockReport(code="9999", error="x")])
    data = json.loads(msg.split("```json\n", 1)[1].rsplit("```", 1)[0])
    assert len(data) == 1  # エラー銘柄は除外
    d = data[0]
    assert d["code"] == "7203" and d["technical_analysis"]["overall"]
    assert d["score"]["categories"] and len(d["recent_prices"]) <= 60


def test_generate_streams_and_sets_request_params():
    client = FakeAnthropic(FakeStream(["## [7203]", " 分析"]))
    out = io.StringIO()
    text = generate_analysis([_report()], client=client, out=out)
    assert text == "## [7203] 分析"
    assert out.getvalue().startswith("## [7203] 分析")
    kw = client.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"] == {"effort": "high"}


def test_refusal_raises():
    client = FakeAnthropic(FakeStream([], stop_reason="refusal"))
    with pytest.raises(AIAnalysisError):
        generate_analysis([_report()], client=client, out=io.StringIO())


def test_real_sdk_accepts_request_shape():
    # 実際の SDK でリクエストを組み立て、到達不能な宛先で接続エラーになること (パラメータ不正なら別の例外)
    client = anthropic.Anthropic(api_key="dummy", base_url="http://127.0.0.1:9", max_retries=0)
    with pytest.raises(AIAnalysisError, match="接続できません"):
        generate_analysis([_report()], client=client, out=io.StringIO())
