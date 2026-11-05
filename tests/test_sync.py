from datetime import date, timedelta

import pytest

from jpstock.plans import get_plan
from jpstock.store import Store
from jpstock.sync import Syncer

TODAY = date(2026, 11, 5)


class BulkClient:
    """日付単位の取得をまねるフェイク。provided より後の日付の株価は 0 件 (未提供)。"""

    def __init__(self, plan, provided_until="2026-08-12"):
        self.plan = plan
        self.provided_until = provided_until
        self.calls = []

    def calendar(self, start, end):
        self.calls.append("calendar")
        d, out = start, []
        while d <= end:
            out.append({"Date": d.isoformat(), "HolDiv": "1" if d.weekday() < 5 else "0"})
            d += timedelta(days=1)
        return out

    def master_on(self, d=None):
        self.calls.append("master")
        return [{"Code": "72030", "CoName": "トヨタ自動車"}, {"Code": "67580", "CoName": "ソニーグループ"}]

    def bars_on(self, d):
        self.calls.append(("bars", d))
        if d > self.provided_until:
            return []
        return [{"Date": d, "Code": c, "O": 100, "H": 110, "L": 90, "C": 100, "Vo": 1, "AdjFactor": 1}
                for c in ("72030", "67580")]

    def fin_summary_on(self, d):
        self.calls.append(("fins", d))
        return [{"Code": "72030", "DiscNo": f"n{d}", "DiscDate": d}] if d.endswith("-10") else []


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def test_window_respects_free_plan():
    syncer = Syncer(BulkClient(get_plan("free")), Store(":memory:"), today=TODAY, log=lambda m: None)
    start, end = syncer.window()
    assert end == TODAY - timedelta(weeks=12)
    assert start == date(2024, 11, 6)
    assert syncer.clamp(date(2020, 1, 1), date(2030, 1, 1)) == (start, end)


def test_sync_is_resumable_and_skips_done_days(store):
    client = BulkClient(get_plan("free"))
    syncer = Syncer(client, store, today=TODAY, log=lambda m: None)
    start, end = date(2026, 8, 3), date(2026, 8, 13)  # 8/13 は株価未提供
    syncer.run(("calendar", "master", "bars", "fins"), start, end)
    bar_calls = [c for c in client.calls if isinstance(c, tuple) and c[0] == "bars"]
    assert len(bar_calls) == 9  # 営業日 9 日
    assert "2026-08-13" not in store.synced_dates("bars")  # 0 件の日は再取得対象
    assert "2026-08-13" in store.synced_dates("fins")  # 財務の 0 件は「開示なし」
    assert store.conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 16

    client.calls.clear()
    client.provided_until = "2026-08-31"
    plan = syncer.run(("bars", "fins"), start, end)
    assert plan.days["bars"] == ["2026-08-13"] and plan.days["fins"] == []
    assert client.calls == [("bars", "2026-08-13")]


def test_refresh_refetches(store):
    client = BulkClient(get_plan("free"))
    syncer = Syncer(client, store, today=TODAY, log=lambda m: None)
    syncer.run(("calendar", "bars"), date(2026, 8, 3), date(2026, 8, 4))
    plan = syncer.plan_sync(("bars",), date(2026, 8, 3), date(2026, 8, 4), refresh=True)
    assert plan.days["bars"] == ["2026-08-03", "2026-08-04"]
    assert plan.estimated_calls == 2 + 4
