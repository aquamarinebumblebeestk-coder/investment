"""J-Quants API の契約プラン定義。

プランごとの差 (レート制限・データ提供期間・遅延・利用可能な機能) をここに集約し、
他のモジュールは `Plan` の属性だけを見て挙動を切り替える。
有料プランへ移行するときは環境変数 `JQUANTS_PLAN` を変えるだけで済む想定。

参考: https://jpx-jquants.com/ja/spec/rate-limits , https://jpx-jquants.com/ja/spec/data-spec
※ 値はドキュメント調査時点 (2026-10) のもの。公式の最新情報と食い違う場合はここを直すこと。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Feature(str, Enum):
    """プランによって使えたり使えなかったりする機能。"""

    # /fins/dividend : 配当金情報の詳細 (権利落ち日・支払日など)。Premium のみ
    DIVIDEND_DETAIL = "dividend_detail"
    # /equities/valuation : 日次の EPS/PER/PBR/ROE/時価総額。
    # 対応プランが公式資料で確認できていないため、Premium のみ有効にしている (要確認)。
    # 無効なプランでは株価と財務サマリから自前で計算する。
    VALUATION = "valuation"


@dataclass(frozen=True)
class Plan:
    name: str
    label: str
    requests_per_minute: int
    history_years: int | None  # None = 制限なし
    delay_weeks: int  # データの提供遅延 (無料プランは 12 週間遅れ)
    features: frozenset[Feature] = field(default_factory=frozenset)

    def supports(self, feature: Feature) -> bool:
        return feature in self.features


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", requests_per_minute=5, history_years=2, delay_weeks=12),
    "light": Plan("light", "Light", requests_per_minute=60, history_years=5, delay_weeks=0),
    "standard": Plan("standard", "Standard", requests_per_minute=120, history_years=10, delay_weeks=0),
    "premium": Plan(
        "premium",
        "Premium",
        requests_per_minute=500,
        history_years=20,
        delay_weeks=0,
        features=frozenset({Feature.DIVIDEND_DETAIL, Feature.VALUATION}),
    ),
}


def get_plan(name: str) -> Plan:
    try:
        return PLANS[name.strip().lower()]
    except KeyError:
        raise ValueError(f"未知のプランです: {name!r} (指定可能: {', '.join(PLANS)})") from None
