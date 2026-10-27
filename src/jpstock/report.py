"""StockReport をターミナル向けテキストに整形する。"""

from __future__ import annotations

import unicodedata

from .analyzer import StockReport

RECENT_DAYS = 10


# ---- 書式 -------------------------------------------------------------------
def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int, right: bool) -> str:
    fill = " " * max(width - _width(s), 0)
    return fill + s if right else s + fill


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(_width(x) for x in col) for col in zip(headers, *rows)]
    lines = ["  ".join(_pad(h, w, i > 0) for i, (h, w) in enumerate(zip(headers, widths)))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(c, w, i > 0) for i, (c, w) in enumerate(zip(row, widths))))
    return "\n".join(lines)


def num(v: float | None, digits: int = 0) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def oku(v: float | None) -> str:
    """円 → 億円"""
    return "-" if v is None else f"{v / 1e8:,.1f}"


def pct(v: float | None) -> str:
    """比率 (0.123) → 12.3%。API が既に % で返すケース (12.3) も考慮する。"""
    if v is None:
        return "-"
    return f"{v * 100 if abs(v) <= 1.5 else v:.1f}%"


def times(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}倍"


def growth(cur: float | None, prev: float | None) -> str:
    if cur is None or prev is None or prev <= 0:
        return "-"
    return f"{(cur / prev - 1) * 100:+.1f}%"


# ---- セクション ---------------------------------------------------------------
def _price_section(r: StockReport) -> list[str]:
    bars = [b for b in r.prices if b.close is not None]
    if not bars:
        return ["■ 株価・出来高", "  データなし"]
    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else None
    vols = [b.volume for b in bars[-20:] if b.volume is not None]
    highs = [b.high for b in bars if b.high is not None]
    lows = [b.low for b in bars if b.low is not None]
    out = [
        f"■ 株価・出来高 ({bars[0].date} 〜 {last.date}, 調整後)",
        f"  終値 {num(last.close, 1)} 円  前日比 {_change(last.close, prev.close if prev else None)}"
        f"  出来高 {num(last.volume)} 株",
        f"  期間高値 {num(max(highs) if highs else None, 1)}  期間安値 {num(min(lows) if lows else None, 1)}"
        f"  20日平均出来高 {num(sum(vols) / len(vols) if vols else None)}",
        "",
    ]
    rows = [
        [b.date, num(b.open, 1), num(b.high, 1), num(b.low, 1), num(b.close, 1), num(b.volume)]
        for b in bars[-RECENT_DAYS:]
    ]
    out += ["  " + line for line in table(["日付", "始値", "高値", "安値", "終値", "出来高"], rows).splitlines()]
    return out


def _change(cur: float | None, prev: float | None) -> str:
    if cur is None or not prev:
        return "-"
    return f"{cur - prev:+,.1f} 円 ({(cur / prev - 1) * 100:+.2f}%)"


def _indicator_section(r: StockReport) -> list[str]:
    i = r.indicators
    if i is None:
        return ["■ 指標", "  データなし"]
    src = "J-Quants算出" if i.source == "api" else "株価と直近決算から計算"
    return [
        f"■ 指標 ({i.as_of or '-'} 時点, {src})",
        f"  EPS(実績) {num(i.eps, 2)}  EPS(予想) {num(i.forecast_eps, 2)}  BPS {num(i.bps, 2)}",
        f"  PER(実績) {times(i.per)}  PER(予想) {times(i.forward_per)}  PBR {times(i.pbr)}",
        f"  ROE {pct(i.roe)}  配当利回り {pct(i.dividend_yield)}  時価総額 {oku(i.market_cap)} 億円",
    ]


def _dividend_section(r: StockReport) -> list[str]:
    out = ["■ 配当金 (1株あたり, 円)"]
    rows = [[a.fiscal_year_end, "実績", num(a.dividend_per_share, 1), pct(a.payout_ratio)] for a in r.annual]
    if r.forecast:
        rows.append([r.forecast.fiscal_year_end, "会社予想", num(r.forecast.dividend_per_share, 1), "-"])
    if not rows:
        return out + ["  データなし"]
    out += ["  " + line for line in table(["決算期末", "区分", "年間配当", "配当性向"], rows).splitlines()]
    if r.dividends:
        out += ["", "  配当詳細 (/fins/dividend)"]
        drows = [[d.record_date, d.ex_date, d.pay_date, num(d.amount, 2)] for d in r.dividends[-8:]]
        out += ["  " + line for line in table(["基準日", "権利落ち日", "支払開始日", "1株配当"], drows).splitlines()]
    return out


def _performance_section(r: StockReport) -> list[str]:
    out = ["■ 業績推移 (通期, 億円)"]
    if not r.annual and not r.forecast:
        return out + ["  データなし"]
    rows = []
    prev = None
    for a in r.annual:
        rows.append(
            [
                a.fiscal_year_end,
                oku(a.sales),
                growth(a.sales, prev.sales if prev else None),
                oku(a.operating_profit),
                pct(_margin(a.operating_profit, a.sales)),
                oku(a.net_profit),
                num(a.eps, 2),
                pct(a.roe),
            ]
        )
        prev = a
    if r.forecast:
        f = r.forecast
        rows.append(
            [
                f"{f.fiscal_year_end}(予)",
                oku(f.sales),
                growth(f.sales, prev.sales if prev else None),
                oku(f.operating_profit),
                pct(_margin(f.operating_profit, f.sales)),
                oku(f.net_profit),
                num(f.eps, 2),
                "-",
            ]
        )
    headers = ["決算期末", "売上高", "増収率", "営業利益", "営業利益率", "純利益", "EPS", "ROE"]
    out += ["  " + line for line in table(headers, rows).splitlines()]
    return out


def _margin(profit: float | None, sales: float | None) -> float | None:
    if profit is None or not sales:
        return None
    return profit / sales


def render(r: StockReport) -> str:
    title = f"[{r.code}] {r.name}".rstrip()
    meta = " / ".join(x for x in (r.market, r.sector) if x)
    lines = ["=" * 72, title + (f"  ({meta})" if meta else ""), "=" * 72]
    if r.error:
        return "\n".join(lines + [f"  取得エラー: {r.error}"])
    for section in (_price_section, _indicator_section, _dividend_section, _performance_section):
        lines += section(r) + [""]
    lines += [f"※ {n}" for n in r.notes]
    return "\n".join(lines).rstrip()
