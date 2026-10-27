"""API から取得したデータを銘柄レポート用に整形・集計する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .client import JQuantsClient, JQuantsError, PlanNotSupportedError
from .financial import FinancialAnalysis, analyze_financials
from .plans import Feature
from .technical import TechnicalAnalysis, analyze_technicals


def to_float(value: Any) -> float | None:
    """API の数値項目 (空文字や "-" を含む) を float に変換する。"""
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


@dataclass
class PriceBar:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None


@dataclass
class AnnualResult:
    """通期 (FY) 決算の実績。金額は円。"""

    fiscal_year_end: str
    sales: float | None
    operating_profit: float | None
    net_profit: float | None
    eps: float | None
    bps: float | None
    dividend_per_share: float | None
    payout_ratio: float | None
    roe: float | None
    total_assets: float | None = None
    equity: float | None = None
    equity_ratio: float | None = None
    cfo: float | None = None  # 営業CF
    cfi: float | None = None  # 投資CF
    cff: float | None = None  # 財務CF
    cash: float | None = None  # 現金及び現金同等物


@dataclass
class Forecast:
    """直近開示時点の会社予想 (対象期の通期)。"""

    fiscal_year_end: str
    sales: float | None
    operating_profit: float | None
    net_profit: float | None
    eps: float | None
    dividend_per_share: float | None


@dataclass
class QuarterProgress:
    """期中 (1Q〜3Q) 決算の累計実績と、同じ開示での通期会社予想。"""

    period: str  # "1Q" / "2Q" / "3Q"
    fiscal_year_end: str
    sales: float | None
    operating_profit: float | None
    net_profit: float | None
    forecast_sales: float | None
    forecast_operating_profit: float | None
    forecast_net_profit: float | None


@dataclass
class Indicators:
    as_of: str | None
    close: float | None
    eps: float | None
    forecast_eps: float | None
    bps: float | None
    per: float | None
    forward_per: float | None
    pbr: float | None
    roe: float | None
    dividend_yield: float | None
    market_cap: float | None
    source: str  # "api" (/equities/valuation) or "calc" (自前計算)


@dataclass
class DividendEvent:
    record_date: str
    ex_date: str
    pay_date: str
    amount: float | None
    kind: str


@dataclass
class StockReport:
    code: str
    name: str = ""
    market: str = ""
    sector: str = ""
    prices: list[PriceBar] = field(default_factory=list)
    annual: list[AnnualResult] = field(default_factory=list)
    forecast: Forecast | None = None
    progress: QuarterProgress | None = None
    indicators: Indicators | None = None
    dividends: list[DividendEvent] = field(default_factory=list)
    financial: FinancialAnalysis | None = None
    technical: TechnicalAnalysis | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# 整形ロジック (API 非依存なので単体テストしやすいよう関数として分離)
# ---------------------------------------------------------------------------
def build_prices(rows: list[dict[str, Any]]) -> list[PriceBar]:
    """株価四本値。分割・併合の影響を除くため調整済み値 (Adj*) を優先する。"""
    bars = [
        PriceBar(
            date=r.get("Date", ""),
            open=to_float(r.get("AdjO", r.get("O"))),
            high=to_float(r.get("AdjH", r.get("H"))),
            low=to_float(r.get("AdjL", r.get("L"))),
            close=to_float(r.get("AdjC", r.get("C"))),
            volume=to_float(r.get("AdjVo", r.get("Vo"))),
        )
        for r in rows
    ]
    return sorted(bars, key=lambda b: b.date)


def _pick(row: dict[str, Any], key: str, nc_key: str | None = None) -> float | None:
    """連結値を優先し、なければ非連結値 (既定は NC{key}) を使う。"""
    value = to_float(row.get(key))
    if value is None:
        value = to_float(row.get(nc_key or f"NC{key}"))
    return value


# 会社予想の (連結キー, 非連結キー)。通期決算の開示では翌期予想 (NxF*) を見る
_FORECAST_KEYS = {
    "current": [("FSales", "FNCSales"), ("FOP", "FNCOP"), ("FNP", "FNCNP"), ("FEPS", "FNCEPS")],
    "next": [("NxFSales", "NxFNCSales"), ("NxFOP", "NxFNCOP"), ("NxFNp", "NxFNCNP"), ("NxFEPS", "NxFNCEPS")],
}


def build_annual_results(rows: list[dict[str, Any]]) -> list[AnnualResult]:
    """財務サマリから通期決算 (CurPerType == "FY") の実績を年度ごとに 1 件ずつ抽出する。"""
    latest_by_fy: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("CurPerType") != "FY" or _pick(r, "Sales") is None:
            continue  # 四半期や予想修正のみの開示は除外
        fy_end = r.get("CurFYEn", "")
        prev = latest_by_fy.get(fy_end)
        if prev is None or (r.get("DiscDate", ""), r.get("DiscTime", "")) >= (
            prev.get("DiscDate", ""),
            prev.get("DiscTime", ""),
        ):
            latest_by_fy[fy_end] = r  # 訂正開示があれば新しいものを採用

    results = []
    for fy_end, r in sorted(latest_by_fy.items()):
        roe = to_float(r.get("ROE"))
        if roe is None:
            roe = _ratio(_pick(r, "NP"), _pick(r, "Eq"))
        results.append(
            AnnualResult(
                fiscal_year_end=fy_end,
                sales=_pick(r, "Sales"),
                operating_profit=_pick(r, "OP"),
                net_profit=_pick(r, "NP"),
                eps=_pick(r, "EPS"),
                bps=_pick(r, "BPS"),
                dividend_per_share=to_float(r.get("DivAnn")),
                payout_ratio=to_float(r.get("PayoutRatioAnn")),
                roe=roe,
                total_assets=_pick(r, "TA"),
                equity=_pick(r, "Eq"),
                equity_ratio=_pick(r, "EqAR"),
                cfo=to_float(r.get("CFO")),
                cfi=to_float(r.get("CFI")),
                cff=to_float(r.get("CFF")),
                cash=to_float(r.get("CashEq")),
            )
        )
    return results


def build_forecast(rows: list[dict[str, Any]]) -> Forecast | None:
    """最新の開示から会社予想を取り出す。

    通期決算の開示では当期が終わっているため、予想は翌期 (NxF*) の項目に入る。
    """
    candidates = sorted(rows, key=lambda r: (r.get("DiscDate", ""), r.get("DiscTime", "")))
    for r in reversed(candidates):
        if r.get("CurPerType") == "FY":
            fy_end, keys, div_key = r.get("NxtFYEn", ""), _FORECAST_KEYS["next"], "NxFDivAnn"
        else:
            fy_end, keys, div_key = r.get("CurFYEn", ""), _FORECAST_KEYS["current"], "FDivAnn"
        sales, op, np_, eps = (_pick(r, k, nc) for k, nc in keys)
        div = to_float(r.get(div_key))
        if any(v is not None for v in (sales, op, np_, eps, div)):
            return Forecast(fy_end, sales, op, np_, eps, div)
    return None


def build_progress(rows: list[dict[str, Any]]) -> QuarterProgress | None:
    """進行期の最新の期中決算 (累計) を取り出す。通期決算がそれより新しければ None。"""
    latest = max(
        (r for r in rows if _pick(r, "Sales") is not None or _pick(r, "OP") is not None),
        key=lambda r: (r.get("DiscDate", ""), r.get("DiscTime", "")),
        default=None,
    )
    if latest is None or latest.get("CurPerType") not in ("1Q", "2Q", "3Q"):
        return None
    sales_key, op_key, np_key = _FORECAST_KEYS["current"][:3]
    return QuarterProgress(
        period=latest["CurPerType"],
        fiscal_year_end=latest.get("CurFYEn", ""),
        sales=_pick(latest, "Sales"),
        operating_profit=_pick(latest, "OP"),
        net_profit=_pick(latest, "NP"),
        forecast_sales=_pick(latest, *sales_key),
        forecast_operating_profit=_pick(latest, *op_key),
        forecast_net_profit=_pick(latest, *np_key),
    )


def calc_indicators(
    prices: list[PriceBar], annual: list[AnnualResult], forecast: Forecast | None, shares_outstanding: float | None
) -> Indicators:
    """株価と財務サマリから指標を計算する (実績 EPS/BPS は直近通期決算の値)。"""
    last = next((b for b in reversed(prices) if b.close is not None), None)
    close = last.close if last else None
    latest = annual[-1] if annual else None
    eps = latest.eps if latest else None
    bps = latest.bps if latest else None
    fwd_eps = forecast.eps if forecast else None
    div = (forecast.dividend_per_share if forecast and forecast.dividend_per_share is not None else None) or (
        latest.dividend_per_share if latest else None
    )
    return Indicators(
        as_of=last.date if last else None,
        close=close,
        eps=eps,
        forecast_eps=fwd_eps,
        bps=bps,
        per=_ratio(close, eps) if eps and eps > 0 else None,
        forward_per=_ratio(close, fwd_eps) if fwd_eps and fwd_eps > 0 else None,
        pbr=_ratio(close, bps) if bps and bps > 0 else None,
        roe=latest.roe if latest else None,
        dividend_yield=_ratio(div, close),
        market_cap=close * shares_outstanding if close and shares_outstanding else None,
        source="calc",
    )


def indicators_from_valuation(rows: list[dict[str, Any]], dividend_per_share: float | None) -> Indicators | None:
    """/equities/valuation の最新行を Indicators に変換する (MktCap は百万円単位)。"""
    if not rows:
        return None
    r = max(rows, key=lambda x: x.get("Date", ""))
    mcap = to_float(r.get("MktCap"))
    eps, per = to_float(r.get("EPS")), to_float(r.get("PER"))
    close = eps * per if eps is not None and per is not None else None
    return Indicators(
        as_of=r.get("Date"),
        close=close,
        eps=eps,
        forecast_eps=to_float(r.get("FwdEPS")),
        bps=to_float(r.get("BPS")),
        per=per,
        forward_per=to_float(r.get("FwdPER")),
        pbr=to_float(r.get("PBR")),
        roe=to_float(r.get("ROE")),
        dividend_yield=_ratio(dividend_per_share, close),
        market_cap=mcap * 1_000_000 if mcap is not None else None,
        source="api",
    )


def build_dividend_events(rows: list[dict[str, Any]]) -> list[DividendEvent]:
    events = [
        DividendEvent(
            record_date=r.get("RecDate", ""),
            ex_date=r.get("ExDate", ""),
            pay_date=r.get("PayDate", ""),
            amount=to_float(r.get("DivRate")),
            kind=r.get("IFCode", ""),
        )
        for r in rows
    ]
    return sorted(events, key=lambda e: e.record_date)


def _shares_outstanding(rows: list[dict[str, Any]]) -> float | None:
    """発行済株式数 - 自己株式数 (最新の開示値)。"""
    for r in sorted(rows, key=lambda x: x.get("DiscDate", ""), reverse=True):
        issued = to_float(r.get("ShOutFY"))
        if issued:
            return issued - (to_float(r.get("TrShFY")) or 0)
    return None


# ---------------------------------------------------------------------------
# 取得 + 集計
# ---------------------------------------------------------------------------
class Analyzer:
    def __init__(self, client: JQuantsClient, *, price_days: int = 365, today: date | None = None):
        self.client = client
        self.plan = client.plan
        self.price_days = price_days
        self.today = today or date.today()

    def price_range(self) -> tuple[date, date]:
        """プランのデータ遅延・提供期間に収まる株価取得期間。"""
        end = self.today - timedelta(weeks=self.plan.delay_weeks)
        start = end - timedelta(days=self.price_days)
        if self.plan.history_years is not None:
            start = max(start, self.today - timedelta(days=365 * self.plan.history_years))
        return start, end

    def analyze(self, code: str) -> StockReport:
        report = StockReport(code=code)
        try:
            self._fill(report)
        except JQuantsError as e:
            report.error = str(e)
        return report

    def _fill(self, report: StockReport) -> None:
        code = report.code
        master = self.client.equities_master(code)
        if not master:
            report.error = "銘柄が見つかりません (上場廃止・コード誤り・プランの提供期間外の可能性)"
            return
        m = master[-1]
        report.name = m.get("CoName", "")
        report.market = m.get("MktNm", "")
        report.sector = m.get("S33Nm", "")

        start, end = self.price_range()
        report.prices = build_prices(self.client.daily_bars(code, start, end))

        summary = self.client.fin_summary(code)
        report.annual = build_annual_results(summary)
        report.forecast = build_forecast(summary)
        report.progress = build_progress(summary)

        if self.plan.supports(Feature.VALUATION):
            try:
                div = report.forecast.dividend_per_share if report.forecast else None
                report.indicators = indicators_from_valuation(self.client.valuation(code, start, end), div)
            except PlanNotSupportedError:
                report.notes.append("バリュエーションAPIが使えないため指標は自前計算しています")
        if report.indicators is None:
            report.indicators = calc_indicators(
                report.prices, report.annual, report.forecast, _shares_outstanding(summary)
            )

        if self.plan.supports(Feature.DIVIDEND_DETAIL):
            report.dividends = build_dividend_events(self.client.dividends(code))

        # 追加の API 呼び出しなしで、取得済みデータから分析する
        report.financial = analyze_financials(report.annual, report.forecast, report.progress, report.sector)
        report.technical = analyze_technicals(report.prices)

        if self.plan.delay_weeks:
            report.notes.append(f"{self.plan.label}プランのためデータは約{self.plan.delay_weeks}週間遅れです")
        if self.plan.history_years is not None:
            report.notes.append(f"{self.plan.label}プランの提供期間は過去{self.plan.history_years}年分です")
