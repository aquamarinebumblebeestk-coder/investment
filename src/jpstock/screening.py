"""第2段階: 全銘柄スクリーニング。

SQLite に保存した全銘柄のデータから指標を計算し、条件に合う銘柄を抽出・並べ替える。
API は呼ばないので、データさえ保存してあればどの環境でも実行できる。

条件は「指標名 演算子 値」の文字列で指定する (例: "per<=15", "roe>=8%", "turnover>=1億")。
"""

from __future__ import annotations

import operator
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

from .analyzer import StockReport, build_report
from .financial import as_ratio
from .store import Store

# 既定で除外する業種 (33業種コード 9999 = その他: ETF・REIT 等)
EXCLUDED_SECTOR_CODES = ("9999",)
LIQUIDITY_DAYS = 20
PRICE_BARS = 250  # 分析に使う株価の本数 (約1年。個別分析の既定 --days 365 と同じ範囲)


@dataclass(frozen=True)
class Metric:
    label: str
    kind: str  # "ratio" (小数で保持し%表示) / "times" / "yen" / "oku" / "num" / "text"
    getter: Callable[[StockReport], Any]


def _close_n_ago(r: StockReport, n: int) -> float | None:
    closes = [b.close for b in r.prices if b.close is not None]
    return closes[-1 - n] if len(closes) > n else None


def _ret(r: StockReport, n: int) -> float | None:
    past = _close_n_ago(r, n)
    now = _close_n_ago(r, 0)
    return now / past - 1 if past and now else None


def _turnover(r: StockReport) -> float | None:
    """直近20営業日の平均売買代金 (円)。売買代金がない場合は 終値×出来高 で近似。"""
    vals = [b.close * b.volume for b in r.prices[-LIQUIDITY_DAYS:] if b.close is not None and b.volume is not None]
    return sum(vals) / len(vals) if vals else None


def _latest_year(r: StockReport):
    return r.financial.years[-1] if r.financial and r.financial.years else None


def _cat(name: str) -> Callable[[StockReport], float | None]:
    def get(r: StockReport) -> float | None:
        c = r.score.get(name) if r.score else None
        return c.score if c else None

    return get


def _fc_op_growth(r: StockReport) -> float | None:
    if r.forecast and r.forecast.operating_profit is not None and r.annual:
        prev = r.annual[-1].operating_profit
        if prev and prev > 0:
            return r.forecast.operating_profit / prev - 1
    return None


def _from_high(r: StockReport) -> float | None:
    ta = r.technical
    return ta.close / ta.high - 1 if ta and ta.close and ta.high else None


METRICS: dict[str, Metric] = {
    "code": Metric("コード", "text", lambda r: r.code),
    "name": Metric("銘柄名", "text", lambda r: r.name),
    "market": Metric("市場", "text", lambda r: r.market),
    "sector": Metric("業種", "text", lambda r: r.sector),
    "close": Metric("終値", "yen", lambda r: r.indicators.close if r.indicators else None),
    "turnover": Metric("売買代金(20日平均)", "oku", _turnover),
    "mcap": Metric("時価総額", "oku", lambda r: r.indicators.market_cap if r.indicators else None),
    "per": Metric("PER", "times", lambda r: r.indicators.per if r.indicators else None),
    "fper": Metric("予想PER", "times", lambda r: r.indicators.forward_per if r.indicators else None),
    "pbr": Metric("PBR", "times", lambda r: r.indicators.pbr if r.indicators else None),
    "yield": Metric("配当利回り", "ratio", lambda r: r.indicators.dividend_yield if r.indicators else None),
    "roe": Metric("ROE", "ratio", lambda r: as_ratio(r.indicators.roe) if r.indicators else None),
    "op_margin": Metric("営業利益率", "ratio", lambda r: y.op_margin if (y := _latest_year(r)) else None),
    "sales_cagr": Metric("売上CAGR", "ratio", lambda r: r.financial.cagr.get("売上高") if r.financial else None),
    "fc_op_growth": Metric("予想営業増益率", "ratio", _fc_op_growth),
    "equity_ratio": Metric("自己資本比率", "ratio", lambda r: y.equity_ratio if (y := _latest_year(r)) else None),
    "fcf": Metric("FCF", "oku", lambda r: y.fcf if (y := _latest_year(r)) else None),
    "rsi": Metric("RSI", "num", lambda r: r.technical.rsi if r.technical else None),
    "ret_1m": Metric("1か月騰落", "ratio", lambda r: _ret(r, 20)),
    "ret_3m": Metric("3か月騰落", "ratio", lambda r: _ret(r, 60)),
    "from_high": Metric("高値から", "ratio", _from_high),
    "tech": Metric("テクニカル", "num", lambda r: r.technical.score if r.technical and r.technical.signals else None),
    "value": Metric("割安", "num", _cat("割安性")),
    "profit": Metric("収益", "num", _cat("収益性")),
    "growth": Metric("成長", "num", _cat("成長性")),
    "safety": Metric("安全", "num", _cat("安全性")),
    "score": Metric("総合", "num", lambda r: r.score.total_100 if r.score else None),
}

PRESETS: dict[str, tuple[str, list[str]]] = {
    "value": ("割安株", ["per>0", "per<=15", "pbr<=1.2", "yield>=2.5%"]),
    "growth": ("成長株", ["sales_cagr>=10%", "fc_op_growth>=5%", "op_margin>=5%"]),
    "quality": ("高収益・財務健全", ["roe>=10%", "op_margin>=10%", "equity_ratio>=40%", "fcf>0"]),
    "dividend": ("高配当", ["yield>=3.5%", "per>0", "equity_ratio>=30%"]),
    "momentum": ("上昇トレンド", ["tech>=2", "ret_3m>=0"]),
}

DEFAULT_COLUMNS = (
    "code", "name", "close", "per", "pbr", "yield", "roe", "op_margin", "sales_cagr", "equity_ratio", "tech", "score",
)

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<=": operator.le, ">=": operator.ge, "!=": operator.ne, "==": operator.eq, "<": operator.lt, ">": operator.gt,
    "=": operator.eq,
}
_COND_RE = re.compile(r"^\s*([a-z_0-9]+)\s*(<=|>=|!=|==|<|>|=)\s*(.+?)\s*$")
_UNITS = {"%": 0.01, "億": 1e8, "万": 1e4, "兆": 1e12}


class ScreeningError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    metric: str
    op: str
    value: float | str
    text: str

    def test(self, row: dict[str, Any]) -> bool:
        v = row.get(self.metric)
        if v is None:
            return False  # データがない銘柄は条件を満たさない扱い
        if isinstance(self.value, str):
            if self.op in ("=", "=="):
                return self.value in str(v)  # 文字列は部分一致
            if self.op == "!=":
                return self.value not in str(v)
            raise ScreeningError(f"文字列の条件は = と != だけ使えます: {self.text}")
        return _OPS[self.op](v, self.value)


def parse_condition(text: str) -> Condition:
    norm = unicodedata.normalize("NFKC", text)
    m = _COND_RE.match(norm)
    if not m:
        raise ScreeningError(f"条件の形式が不正です: {text!r} (例: per<=15, roe>=8%, turnover>=1億)")
    name, op, raw = m.groups()
    if name not in METRICS:
        raise ScreeningError(f"未知の指標です: {name} (指定可能: {', '.join(METRICS)})")
    if METRICS[name].kind == "text":
        return Condition(name, op, raw, text)
    mult = 1.0
    for unit, factor in _UNITS.items():
        if raw.endswith(unit):
            raw, mult = raw[: -len(unit)], factor
            break
    try:
        value = float(raw) * mult
    except ValueError:
        raise ScreeningError(f"値が数値ではありません: {text!r}") from None
    return Condition(name, op, value, text)


@dataclass
class ScreenResult:
    rows: list[dict[str, Any]]  # 条件に合った銘柄 (limit 適用後)
    matched: int  # 条件に合った銘柄数 (limit 適用前)
    universe_size: int
    analyzed: int
    listing_date: str | None
    conditions: list[Condition] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def metrics_row(r: StockReport) -> dict[str, Any]:
    return {key: m.getter(r) for key, m in METRICS.items()}


def screen(
    store: Store,
    conditions: list[Condition],
    *,
    plan: str = "free",
    as_of: str | None = None,
    markets: list[str] | None = None,
    sort: str = "score",
    descending: bool = True,
    limit: int | None = 50,
    include_others: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> ScreenResult:
    """全銘柄 (as_of 時点の上場一覧) を分析して条件で絞り込む。"""
    if sort not in METRICS:
        raise ScreeningError(f"未知の並べ替え指標です: {sort}")
    listing_date, universe = store.universe(as_of)
    notes = []
    if as_of and listing_date and listing_date > as_of:
        notes.append(f"{as_of} 以前の銘柄一覧がないため {listing_date} の一覧を使っています (上場廃止銘柄が漏れます)")
    targets = [
        u for u in universe
        if (include_others or u.get("S33") not in EXCLUDED_SECTOR_CODES)
        and (not markets or any(m in (u.get("MktNm") or "") for m in markets))
    ]
    rows = []
    analyzed = 0
    for i, u in enumerate(targets, 1):
        code = u["Code"][:4] if u["Code"].endswith("0") else u["Code"]
        snap = store.snapshot(code, plan, as_of=as_of)
        snap.bars = snap.bars[-PRICE_BARS:]
        if snap.bars:
            report = build_report(snap)
            if report.error is None:
                analyzed += 1
                row = metrics_row(report)
                if all(c.test(row) for c in conditions):
                    rows.append(row)
        if progress:
            progress(i, len(targets))

    # 値のある銘柄を並べ、値のない銘柄は末尾に回す
    has = sorted((r for r in rows if r.get(sort) is not None), key=lambda r: r[sort], reverse=descending)
    rows = has + [r for r in rows if r.get(sort) is None]
    return ScreenResult(
        rows=rows[:limit] if limit else rows,
        matched=len(rows),
        universe_size=len(targets),
        analyzed=analyzed,
        listing_date=listing_date,
        conditions=conditions,
        notes=notes,
    )


def format_value(key: str, v: Any) -> str:
    if v is None:
        return "-"
    kind = METRICS[key].kind
    if kind == "text":
        return str(v)
    if kind == "ratio":
        return f"{v * 100:.1f}%"
    if kind == "times":
        return f"{v:.1f}"
    if kind == "oku":
        return f"{v / 1e8:,.1f}"
    if kind == "yen":
        return f"{v:,.0f}"
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.1f}"
