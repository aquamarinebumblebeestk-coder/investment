"""財務分析: 決算短信の実績・予想から収益性・成長性・安全性・CF・進捗率を評価する。

API 呼び出しは行わず、analyzer が整形したデータだけを入力にする。
判定の閾値は一般的な目安であり、業種によって妥当な水準は異なる点に注意。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analyzer import AnnualResult, Forecast, QuarterProgress

# 判定の目安 (比率は小数)
OP_MARGIN_GOOD, OP_MARGIN_WEAK = 0.10, 0.05
ROE_GOOD, ROE_WEAK = 0.08, 0.05
EQUITY_RATIO_GOOD, EQUITY_RATIO_WEAK = 0.40, 0.20
PROGRESS_TOLERANCE = 0.10  # 期中進捗率の目安 (1Q=25% 等) からの許容幅
# 自己資本比率の目安が当てはまらない業種 (預金・保険契約等で負債が大きくなる)
FINANCIAL_SECTORS = ("銀行業", "証券、商品先物取引業", "保険業", "その他金融業")

GOOD, CAUTION, INFO = "良好", "注意", "参考"


@dataclass
class YearMetrics:
    fiscal_year_end: str
    sales_growth: float | None
    op_growth: float | None
    np_growth: float | None
    op_margin: float | None
    net_margin: float | None
    roe: float | None
    roa: float | None
    equity_ratio: float | None
    cfo: float | None
    fcf: float | None  # 営業CF + 投資CF
    cfo_margin: float | None


@dataclass
class Assessment:
    category: str
    level: str  # GOOD / CAUTION / INFO
    message: str


@dataclass
class FinancialAnalysis:
    years: list[YearMetrics] = field(default_factory=list)
    cagr_years: int = 0
    cagr: dict[str, float | None] = field(default_factory=dict)  # 売上高/営業利益/純利益/EPS
    progress_period: str | None = None
    progress_standard: float | None = None  # 期中の目安 (1Q=25%, 2Q=50%, 3Q=75%)
    progress: dict[str, float | None] = field(default_factory=dict)
    assessments: list[Assessment] = field(default_factory=list)


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1


def as_ratio(v: float | None) -> float | None:
    """API が % 表記 (38.9) で返す場合も小数 (0.389) にそろえる。"""
    if v is None:
        return None
    return v / 100 if abs(v) > 1.5 else v


def cagr(first: float | None, last: float | None, years: int) -> float | None:
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def year_metrics(annual: list[AnnualResult]) -> list[YearMetrics]:
    out = []
    prev = None
    for a in annual:
        cfo, cfi = a.cfo, a.cfi
        out.append(
            YearMetrics(
                fiscal_year_end=a.fiscal_year_end,
                sales_growth=_growth(a.sales, prev.sales) if prev else None,
                op_growth=_growth(a.operating_profit, prev.operating_profit) if prev else None,
                np_growth=_growth(a.net_profit, prev.net_profit) if prev else None,
                op_margin=_div(a.operating_profit, a.sales),
                net_margin=_div(a.net_profit, a.sales),
                roe=as_ratio(a.roe),
                roa=_div(a.net_profit, a.total_assets),
                equity_ratio=as_ratio(a.equity_ratio) or _div(a.equity, a.total_assets),
                cfo=cfo,
                fcf=cfo + cfi if cfo is not None and cfi is not None else None,
                cfo_margin=_div(cfo, a.sales),
            )
        )
        prev = a
    return out


def _assess(
    fa: FinancialAnalysis, annual: list[AnnualResult], forecast: Forecast | None, sector: str
) -> list[Assessment]:
    out: list[Assessment] = []
    if not fa.years:
        return out
    y = fa.years[-1]
    latest = annual[-1]

    # 収益性
    if y.op_margin is not None:
        level = GOOD if y.op_margin >= OP_MARGIN_GOOD else CAUTION if y.op_margin < OP_MARGIN_WEAK else INFO
        out.append(Assessment("収益性", level, f"営業利益率 {y.op_margin:.1%}"))
    if y.roe is not None:
        level = GOOD if y.roe >= ROE_GOOD else CAUTION if y.roe < ROE_WEAK else INFO
        out.append(Assessment("収益性", level, f"ROE {y.roe:.1%} (目安 {ROE_GOOD:.0%} 以上)"))

    # 成長性
    if fa.cagr.get("売上高") is not None:
        g = fa.cagr["売上高"]
        out.append(
            Assessment("成長性", GOOD if g > 0 else CAUTION, f"売上高 年平均成長率 {g:+.1%} ({fa.cagr_years}年)")
        )
    if forecast and forecast.operating_profit is not None and latest.operating_profit:
        g = _growth(forecast.operating_profit, latest.operating_profit)
        if g is not None:
            out.append(
                Assessment("成長性", GOOD if g >= 0 else CAUTION, f"会社予想の営業利益は前期比 {g:+.1%}")
            )

    # 安全性
    if y.equity_ratio is not None:
        if any(s in sector for s in FINANCIAL_SECTORS):
            out.append(Assessment("安全性", INFO, f"自己資本比率 {y.equity_ratio:.1%} (金融業のため目安対象外)"))
        else:
            level = (
                GOOD
                if y.equity_ratio >= EQUITY_RATIO_GOOD
                else CAUTION
                if y.equity_ratio < EQUITY_RATIO_WEAK
                else INFO
            )
            out.append(Assessment("安全性", level, f"自己資本比率 {y.equity_ratio:.1%}"))

    # キャッシュフロー
    if y.cfo is not None:
        if y.cfo < 0:
            out.append(Assessment("CF", CAUTION, "営業CFがマイナス (本業で現金を生めていない)"))
        elif y.fcf is not None and y.fcf < 0:
            out.append(Assessment("CF", INFO, "営業CFはプラスだがFCFはマイナス (投資先行)"))
        else:
            out.append(Assessment("CF", GOOD, "営業CF・FCFともにプラス"))
        if latest.net_profit and latest.net_profit > 0 and y.cfo < latest.net_profit * 0.5:
            out.append(Assessment("CF", CAUTION, "営業CFが純利益に比べて小さい (利益の質に注意)"))

    # 期中の進捗
    if fa.progress_standard is not None:
        op = fa.progress.get("営業利益")
        if op is not None:
            diff = op - fa.progress_standard
            level = GOOD if diff >= PROGRESS_TOLERANCE else CAUTION if diff <= -PROGRESS_TOLERANCE else INFO
            out.append(
                Assessment(
                    "進捗",
                    level,
                    f"{fa.progress_period}時点の営業利益進捗率 {op:.1%} (目安 {fa.progress_standard:.0%})",
                )
            )
    return out


def analyze_financials(
    annual: list[AnnualResult],
    forecast: Forecast | None,
    progress: QuarterProgress | None = None,
    sector: str = "",
) -> FinancialAnalysis:
    fa = FinancialAnalysis(years=year_metrics(annual))
    if len(annual) >= 2:
        first, last = annual[0], annual[-1]
        fa.cagr_years = len(annual) - 1
        fa.cagr = {
            "売上高": cagr(first.sales, last.sales, fa.cagr_years),
            "営業利益": cagr(first.operating_profit, last.operating_profit, fa.cagr_years),
            "純利益": cagr(first.net_profit, last.net_profit, fa.cagr_years),
            "EPS": cagr(first.eps, last.eps, fa.cagr_years),
        }
    if progress is not None:
        fa.progress_period = progress.period
        fa.progress_standard = int(progress.period[0]) / 4
        fa.progress = {
            "売上高": _div(progress.sales, progress.forecast_sales),
            "営業利益": _div(progress.operating_profit, progress.forecast_operating_profit),
            "純利益": _div(progress.net_profit, progress.forecast_net_profit),
        }
    fa.assessments = _assess(fa, annual, forecast, sector)
    return fa
