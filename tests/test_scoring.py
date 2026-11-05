from datetime import date

from jpstock.analyzer import Analyzer, Indicators, StockReport
from jpstock.plans import get_plan
from jpstock.report import render, render_comparison
from jpstock.scoring import grade, score_report

from fakes import FakeClient


def test_grade():
    assert grade(0.2, (0.15, 0.10, 0.05, 0.0)) == 5
    assert grade(-0.1, (0.15, 0.10, 0.05, 0.0)) == 1
    assert grade(12, (10, 15, 20, 30), higher_is_better=False) == 4
    assert grade(None, (1, 2, 3, 4)) is None


def _report(code="7203"):
    return Analyzer(FakeClient(get_plan("free")), today=date(2026, 10, 27)).analyze(code)[0]


def test_score_card_from_report():
    sc = _report().score
    assert [c.name for c in sc.categories] == ["割安性", "収益性", "成長性", "安全性", "テクニカル"]
    # PER 2850/220≈13→4, PBR 1.30→3, 利回り 2.6%→3
    assert sc.get("割安性").score == (4 + 3 + 3) / 3
    assert sc.get("安全性").score == (4 + 5) / 2  # 自己資本比率40%→4, CFプラス→5
    assert 0 <= sc.total_100 <= 100
    assert "強み" in sc.summary() or "課題" in sc.summary() or "平均的" in sc.summary()


def test_missing_data_is_not_scored():
    sc = score_report(StockReport(code="0000", indicators=Indicators(*([None] * 11), source="calc")))
    assert sc.total is None
    assert "不足" in sc.summary()


def test_loss_making_per_not_scored():
    r = _report()
    r.indicators.per = -5.0
    detail = " ".join(score_report(r).get("割安性").details)
    assert "PER" not in detail


def test_summary_section_and_comparison():
    a, b = _report("7203"), _report("6758")
    b.code, b.name = "6758", "ソニーグループ"
    b.indicators.per = 40.0
    b.score = score_report(b)
    assert "総合評価" in render(a, ("summary",))
    table = render_comparison([a, b])
    assert "銘柄比較" in table
    lines = [line for line in table.splitlines() if line.lstrip().startswith(("1 ", "2 "))]
    assert "7203" in lines[0]  # PER の低い 7203 の方が総合評価が高い
    assert render_comparison([a]) == ""
