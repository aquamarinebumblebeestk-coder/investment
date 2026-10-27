import pytest

from jpstock.analyzer import AnnualResult, Forecast, QuarterProgress, build_annual_results, build_progress
from jpstock.financial import CAUTION, GOOD, analyze_financials, as_ratio, cagr

from fakes import SUMMARY


def _year(fy, sales, op, np_, cfo=None, cfi=None, eq_ratio=None, ta=None):
    return AnnualResult(fy, sales, op, np_, eps=np_ / 100, bps=None, dividend_per_share=None, payout_ratio=None,
                        roe=None, total_assets=ta, equity_ratio=eq_ratio, cfo=cfo, cfi=cfi)


def test_cagr_and_ratio_helpers():
    assert cagr(100, 121, 2) == pytest.approx(0.1)
    assert cagr(-1, 100, 2) is None
    assert as_ratio(38.9) == pytest.approx(0.389)
    assert as_ratio(0.389) == 0.389


def test_metrics_from_summary():
    fa = analyze_financials(build_annual_results(SUMMARY), None)
    y = fa.years[-1]
    assert y.op_margin == pytest.approx(0.1)
    assert y.roa == pytest.approx(1 / 25)
    assert y.equity_ratio == pytest.approx(0.4)
    assert y.fcf == pytest.approx(3.3e12 * 0.7)
    assert y.sales_growth == pytest.approx(0.1)
    assert fa.cagr_years == 1 and fa.cagr["売上高"] == pytest.approx(0.1)
    levels = {a.category: a.level for a in fa.assessments}
    assert levels["安全性"] == GOOD and levels["CF"] == GOOD


def test_weak_company_gets_caution():
    annual = [_year("2025-03-31", 100, 5, 3, cfo=1, cfi=-5, eq_ratio=0.1, ta=100),
              _year("2026-03-31", 90, 3, 2, cfo=-1, cfi=-5, eq_ratio=0.1, ta=100)]
    forecast = Forecast("2027-03-31", 85, 2, 1, 0.01, None)
    fa = analyze_financials(annual, forecast)
    cautions = [a.message for a in fa.assessments if a.level == CAUTION]
    assert any("営業利益率" in m for m in cautions)
    assert any("自己資本比率" in m for m in cautions)
    assert any("営業CFがマイナス" in m for m in cautions)
    assert any("会社予想の営業利益" in m for m in cautions)


def test_financial_sector_skips_equity_ratio_threshold():
    annual = [_year("2026-03-31", 100, 20, 10, eq_ratio=0.05, ta=1000)]
    fa = analyze_financials(annual, None, sector="銀行業")
    assert [a.level for a in fa.assessments if a.category == "安全性"] == ["参考"]


def test_progress_rate():
    rows = SUMMARY + [{"DiscDate": "2026-08-01", "CurPerType": "2Q", "CurFYEn": "2027-03-31",
                       "Sales": "23e12", "OP": "3e12", "NP": "1.7e12",
                       "FSales": "46e12", "FOP": "4.6e12", "FNP": "3.4e12"}]
    progress = build_progress(rows)
    assert progress.period == "2Q"
    fa = analyze_financials(build_annual_results(rows), None, progress)
    assert fa.progress_standard == 0.5
    assert fa.progress["売上高"] == pytest.approx(0.5)
    op = next(a for a in fa.assessments if a.category == "進捗")
    assert op.level == GOOD  # 65% vs 目安 50%


def test_no_progress_after_annual_report():
    assert build_progress(SUMMARY) is None
