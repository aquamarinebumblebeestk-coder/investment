from datetime import date

import pytest

from jpstock.analyzer import Analyzer, build_annual_results, build_forecast
from jpstock.plans import get_plan
from jpstock.report import render

from fakes import SUMMARY, FakeClient


def test_annual_results_only_fy_with_actuals():
    annual = build_annual_results(SUMMARY)
    assert [a.fiscal_year_end for a in annual] == ["2025-03-31", "2026-03-31"]
    assert annual[-1].sales == 44e12
    assert annual[-1].roe == pytest.approx(0.1)


def test_forecast_uses_next_fy_after_annual_report():
    f = build_forecast(SUMMARY)
    assert f.fiscal_year_end == "2027-03-31"
    assert f.eps == 230 and f.dividend_per_share == 75


def test_free_plan_calculates_indicators_and_respects_delay():
    client = FakeClient(get_plan("free"))
    report = Analyzer(client, today=date(2026, 10, 27)).analyze("7203")
    assert report.error is None
    assert "valuation" not in client.calls and "dividends" not in client.calls
    _, start, end = client.calls[1]
    assert end == date(2026, 8, 4)  # 12週間遅延
    i = report.indicators
    assert i.source == "calc"
    assert i.close == 2850
    assert i.per == pytest.approx(2850 / 220)
    assert i.pbr == pytest.approx(2850 / 2200)
    assert i.dividend_yield == pytest.approx(75 / 2850)
    text = render(report)
    for heading in ("株価・出来高", "指標", "配当金", "業績推移", "トヨタ自動車"):
        assert heading in text


def test_premium_plan_uses_valuation_and_dividend_detail():
    client = FakeClient(get_plan("premium"))
    report = Analyzer(client, today=date(2026, 10, 27)).analyze("7203")
    assert report.indicators.source == "api"
    assert report.indicators.per == 13
    assert len(report.dividends) == 1
    assert "権利落ち日" in render(report)


def test_valuation_403_falls_back_to_calculation():
    client = FakeClient(get_plan("premium"), valuation_error=True)
    report = Analyzer(client, today=date(2026, 10, 27)).analyze("7203")
    assert report.indicators.source == "calc"
