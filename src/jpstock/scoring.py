"""総合評価: 割安性・収益性・成長性・安全性・テクニカルを 5 段階で採点し、強み/弱みをまとめる。

各カテゴリは複数の指標を 1〜5 点で採点した平均。データがない指標は採点しない。
閾値は一般的な目安であり、業種による違いは考慮していない (自己資本比率の金融業除外のみ)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .financial import FINANCIAL_SECTORS, as_ratio

if TYPE_CHECKING:
    from .analyzer import StockReport

CATEGORIES = ("割安性", "収益性", "成長性", "安全性", "テクニカル")
STRENGTH_MIN, WEAKNESS_MAX = 4.0, 2.0


@dataclass
class CategoryScore:
    name: str
    score: float | None  # 1.0〜5.0 (None = 採点不能)
    details: list[str] = field(default_factory=list)


@dataclass
class ScoreCard:
    categories: list[CategoryScore] = field(default_factory=list)

    @property
    def total(self) -> float | None:
        """採点できたカテゴリの平均 (1〜5)。"""
        scores = [c.score for c in self.categories if c.score is not None]
        return sum(scores) / len(scores) if scores else None

    @property
    def total_100(self) -> int | None:
        return None if self.total is None else round((self.total - 1) / 4 * 100)

    @property
    def strengths(self) -> list[CategoryScore]:
        return [c for c in self.categories if c.score is not None and c.score >= STRENGTH_MIN]

    @property
    def weaknesses(self) -> list[CategoryScore]:
        return [c for c in self.categories if c.score is not None and c.score <= WEAKNESS_MAX]

    def get(self, name: str) -> CategoryScore | None:
        return next((c for c in self.categories if c.name == name), None)

    def summary(self) -> str:
        """一文の総評。"""
        if self.total is None:
            return "評価に必要なデータが不足しています"
        strong = "・".join(c.name for c in self.strengths)
        weak = "・".join(c.name for c in self.weaknesses)
        parts = []
        if strong:
            parts.append(f"{strong}が強み")
        if weak:
            parts.append(f"{weak}に課題")
        if not parts:
            parts.append("突出した強み・弱みはなく平均的")
        return "、".join(parts)


def grade(value: float | None, thresholds: tuple[float, float, float, float], higher_is_better: bool = True) -> int | None:
    """thresholds (4つ) を境に 1〜5 点を付ける。

    higher_is_better=True: value >= t[0] で5点, >= t[1] で4点 ... それ未満は1点。
    higher_is_better=False: value <= t[0] で5点, <= t[1] で4点 ... (PER のように低いほど良い指標)。
    """
    if value is None:
        return None
    for points, t in zip((5, 4, 3, 2), thresholds):
        if (value >= t) if higher_is_better else (value <= t):
            return points
    return 1


def _category(name: str, items: list[tuple[int | None, str]]) -> CategoryScore:
    graded = [(p, d) for p, d in items if p is not None]
    if not graded:
        return CategoryScore(name, None, ["データ不足"])
    return CategoryScore(name, sum(p for p, _ in graded) / len(graded), [f"{d}({p})" for p, d in graded])


def score_report(r: StockReport) -> ScoreCard:
    ind, fa, ta = r.indicators, r.financial, r.technical
    latest = fa.years[-1] if fa and fa.years else None
    cats: list[CategoryScore] = []

    # 割安性: PER は赤字 (負) の場合は採点しない
    per = ind.per if ind and ind.per and ind.per > 0 else None
    pbr = ind.pbr if ind and ind.pbr and ind.pbr > 0 else None
    yld = ind.dividend_yield if ind else None
    cats.append(
        _category(
            "割安性",
            [
                (grade(per, (10, 15, 20, 30), higher_is_better=False), f"PER {per:.1f}倍" if per else ""),
                (grade(pbr, (0.8, 1.0, 1.5, 3.0), higher_is_better=False), f"PBR {pbr:.2f}倍" if pbr else ""),
                (grade(yld, (0.04, 0.03, 0.02, 0.01)), f"配当利回り {yld:.1%}" if yld is not None else ""),
            ],
        )
    )

    # 収益性
    op_margin = latest.op_margin if latest else None
    roe = as_ratio(ind.roe) if ind and ind.roe is not None else (latest.roe if latest else None)
    cats.append(
        _category(
            "収益性",
            [
                (grade(op_margin, (0.15, 0.10, 0.05, 0.0)), f"営業利益率 {op_margin:.1%}" if op_margin is not None else ""),
                (grade(roe, (0.15, 0.10, 0.08, 0.05)), f"ROE {roe:.1%}" if roe is not None else ""),
            ],
        )
    )

    # 成長性: 売上の年平均成長率と、会社予想の営業増益率
    sales_cagr = fa.cagr.get("売上高") if fa else None
    fc_growth = None
    if r.forecast and r.forecast.operating_profit is not None and r.annual:
        prev = r.annual[-1].operating_profit
        if prev and prev > 0:
            fc_growth = r.forecast.operating_profit / prev - 1
    cats.append(
        _category(
            "成長性",
            [
                (grade(sales_cagr, (0.10, 0.05, 0.0, -0.05)), f"売上高CAGR {sales_cagr:+.1%}" if sales_cagr is not None else ""),
                (grade(fc_growth, (0.10, 0.05, 0.0, -0.05)), f"予想営業増益率 {fc_growth:+.1%}" if fc_growth is not None else ""),
            ],
        )
    )

    # 安全性: 金融業は自己資本比率を採点しない
    eq_ratio = latest.equity_ratio if latest else None
    if any(s in r.sector for s in FINANCIAL_SECTORS):
        eq_ratio = None
    cf_points = None
    if latest and latest.cfo is not None:
        cf_points = 1 if latest.cfo <= 0 else 5 if (latest.fcf or 0) > 0 else 3
    cats.append(
        _category(
            "安全性",
            [
                (grade(eq_ratio, (0.60, 0.40, 0.30, 0.20)), f"自己資本比率 {eq_ratio:.1%}" if eq_ratio is not None else ""),
                (cf_points, "営業CF・FCF"),
            ],
        )
    )

    # テクニカル: 強気シグナル数 - 弱気シグナル数
    tech_points = None
    if ta and ta.signals:
        tech_points = grade(ta.score, (3, 2, -1, -2))
    cats.append(_category("テクニカル", [(tech_points, f"シグナル {ta.score:+d}" if ta else "")]))
    return ScoreCard(cats)
