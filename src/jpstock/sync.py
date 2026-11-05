"""J-Quants API から SQLite への同期。

全銘柄を「日付単位」で取得する (1日あたり数リクエスト)。銘柄単位で全銘柄を取ると
Free プラン (5回/分) では数十時間かかるため。取得済みの日付は sync_state に記録し、
中断しても次回は続きから再開する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from .client import JQuantsClient
from .store import Store

DATASETS = ("calendar", "master", "bars", "fins")
# 呼び出し回数の見積もりに使う、日付単位取得1日あたりの平均リクエスト数 (ページ分割を考慮した概算)
EST_CALLS_PER_DAY = {"bars": 2, "fins": 1}


@dataclass
class SyncPlan:
    start: date
    end: date
    days: dict[str, list[str]]  # dataset -> 取得が必要な日付

    @property
    def estimated_calls(self) -> int:
        return 2 + sum(len(v) * EST_CALLS_PER_DAY.get(k, 1) for k, v in self.days.items())


class Syncer:
    def __init__(
        self,
        client: JQuantsClient,
        store: Store,
        *,
        today: date | None = None,
        log: Callable[[str], None] = print,
    ):
        self.client = client
        self.store = store
        self.plan = client.plan
        self.today = today or date.today()
        self.log = log

    def window(self) -> tuple[date, date]:
        """プランで取得できる期間 (提供期間と遅延を考慮)。"""
        return self.plan.data_window(self.today)

    def clamp(self, start: date | None, end: date | None) -> tuple[date, date]:
        w_start, w_end = self.window()
        return max(start or w_start, w_start), min(end or w_end, w_end)

    # ---- 個別データセット -------------------------------------------------
    def sync_calendar(self, start: date, end: date) -> int:
        with self.store.logged_fetch("api", "calendar", {"from": str(start), "to": str(end)}) as res:
            res["rows"] = self.store.upsert_calendar(self.client.calendar(start, end))
        return res["rows"]

    def sync_master(self, d: date | None = None) -> int:
        """上場銘柄一覧。日付指定なしは最新 (取得日の一覧として記録)。"""
        with self.store.logged_fetch("api", "master", {"date": str(d) if d else None}) as res:
            res["rows"] = self.store.upsert_listings(self.client.master_on(d), as_of=str(d or self.today))
        return res["rows"]

    def _sync_daily(self, dataset: str, days: list[str], fetch: Callable[[str], list], upsert: Callable) -> int:
        total = 0
        for i, d in enumerate(days, 1):
            with self.store.logged_fetch("api", dataset, {"date": d}) as res:
                rows = fetch(d)
                res["rows"] = upsert(rows)
                # 株価が0件の営業日は「まだ提供されていない」可能性があるので取得済みにしない
                if rows or dataset != "bars":
                    self.store.mark_synced(dataset, d, len(rows))
            total += res["rows"]
            if i % 10 == 0 or i == len(days):
                self.log(f"  {dataset}: {i}/{len(days)} 日 ({d})")
        return total

    # ---- 全体 -------------------------------------------------------------
    def plan_sync(
        self, datasets: tuple[str, ...] = DATASETS, start: date | None = None, end: date | None = None,
        refresh: bool = False,
    ) -> SyncPlan:
        start, end = self.clamp(start, end)
        days: dict[str, list[str]] = {}
        trading = self.store.trading_days(start, end)
        if not trading:  # カレンダー未取得: 見積もり用に平日で代用する (取得時はカレンダーを先に取る)
            trading = [
                (start + timedelta(days=i)).isoformat()
                for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5
            ]
        for ds in ("bars", "fins"):
            if ds in datasets:
                done = set() if refresh else self.store.synced_dates(ds)
                days[ds] = [d for d in trading if d not in done]
        return SyncPlan(start, end, days)

    def run(
        self, datasets: tuple[str, ...] = DATASETS, start: date | None = None, end: date | None = None,
        refresh: bool = False,
    ) -> SyncPlan:
        start, end = self.clamp(start, end)
        if "calendar" in datasets or not self.store.trading_days(start, end):
            n = self.sync_calendar(start, end)
            self.log(f"取引カレンダー: {n} 日分")
        if "master" in datasets:
            n = self.sync_master()
            self.log(f"上場銘柄一覧: {n} 銘柄")
        plan = self.plan_sync(datasets, start, end, refresh)
        if "bars" in plan.days:
            self.log(f"株価: {len(plan.days['bars'])} 日分を取得")
            self._sync_daily("bars", plan.days["bars"], self.client.bars_on, self.store.upsert_bars)
        if "fins" in plan.days:
            self.log(f"財務情報: {len(plan.days['fins'])} 日分を取得")
            self._sync_daily("fins", plan.days["fins"], self.client.fin_summary_on, self.store.upsert_fins)
        return plan

    def sync_codes(self, codes: list[str], start: date | None = None, end: date | None = None) -> None:
        """指定銘柄だけを銘柄単位で取得する (少数銘柄をすぐ使いたい場合)。sync_state は更新しない。"""
        start, end = self.clamp(start, end)
        for code in codes:
            with self.store.logged_fetch("api", "code", {"code": code, "from": str(start), "to": str(end)}) as res:
                res["rows"] = self.store.upsert_listings(self.client.equities_master(code), as_of=str(self.today))
                res["rows"] += self.store.upsert_bars(self.client.daily_bars(code, start, end))
                res["rows"] += self.store.upsert_fins(self.client.fin_summary(code))
            self.log(f"  {code}: {res['rows']} 件")
