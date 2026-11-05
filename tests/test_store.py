from datetime import date

import pytest

from jpstock.analyzer import Analyzer, build_report
from jpstock.plans import get_plan
from jpstock.store import Store

from fakes import FakeClient


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def _bar(d, close, factor=1.0, code="72030", vol=1000):
    return {"Date": d, "Code": code, "O": close, "H": close + 10, "L": close - 10, "C": close, "Vo": vol,
            "Va": close * vol, "AdjFactor": factor}


def test_adjusted_prices_are_computed_from_factors(store):
    # 2026-01-07 に 1:2 の株式分割 (調整係数 0.5)
    store.upsert_bars([_bar("2026-01-05", 1000), _bar("2026-01-06", 1020), _bar("2026-01-07", 515, 0.5),
                       _bar("2026-01-08", 520)])
    bars = store.bars("7203")
    assert [b["AdjC"] for b in bars] == [500, 510, 515, 520]
    assert bars[0]["AdjVo"] == 2000 and bars[0]["C"] == 1000
    # 分割前の時点では、分割を知らないので調整なしの値になる (未来の情報を使わない)
    before = store.bars("7203", as_of="2026-01-06")
    assert [b["AdjC"] for b in before] == [1000, 1020]
    assert [b["AdjC"] for b in store.bars("7203", start="2026-01-07")] == [515, 520]


def test_revisions_recorded_only_when_values_change(store):
    store.upsert_bars([_bar("2026-01-05", 1000)])
    assert store.upsert_bars([_bar("2026-01-05", 1000)]) == 0  # 同じ値: 更新なし
    assert store.upsert_bars([_bar("2026-01-05", 1001)]) == 1
    rev = store.conn.execute("SELECT * FROM revisions").fetchall()
    assert len(rev) == 1 and rev[0]["row_key"] == "72030:2026-01-05"

    fin = {"Code": "72030", "DiscNo": "1", "DiscDate": "2026-05-08", "Sales": "1"}
    store.upsert_fins([fin])
    store.upsert_fins([fin])
    store.upsert_fins([dict(fin, Sales="2")])
    assert store.conn.execute("SELECT COUNT(*) FROM revisions WHERE table_name='fin_statements'").fetchone()[0] == 1


def test_fins_and_listing_as_of(store):
    store.upsert_fins([
        {"Code": "72030", "DiscNo": "a", "DiscDate": "2025-05-08"},
        {"Code": "72030", "DiscNo": "b", "DiscDate": "2026-05-08"},
    ])
    store.upsert_listings([{"Date": "2025-01-01", "Code": "72030", "CoName": "旧社名"},
                           {"Date": "2026-01-01", "Code": "72030", "CoName": "新社名"}])
    assert [f["DiscNo"] for f in store.fins("7203", as_of="2025-12-31")] == ["a"]
    assert store.listing("7203", as_of="2025-06-01")[0]["CoName"] == "旧社名"
    assert store.listing("7203")[0]["CoName"] == "新社名"


def test_logged_fetch_records_success_and_error(store):
    with store.logged_fetch("api", "bars", {"date": "2026-01-05"}) as res:
        res["rows"] = 3
    with pytest.raises(RuntimeError):
        with store.logged_fetch("api", "bars", {"date": "2026-01-06"}):
            raise RuntimeError("boom")
    logs = store.fetch_history()
    assert [(r["status"], r["rows"]) for r in logs] == [("error", None), ("ok", 3)]
    assert "boom" in logs[0]["error"]


def test_db_snapshot_gives_same_report_as_api(store):
    snap = Analyzer(FakeClient(get_plan("free")), today=date(2026, 10, 27)).fetch("7203")
    store.save_snapshot(snap)
    from_db = build_report(store.snapshot("7203", "free"))
    from_api = build_report(snap)
    assert from_db.name == from_api.name
    assert from_db.indicators == from_api.indicators
    assert from_db.financial == from_api.financial
    assert from_db.technical == from_api.technical
    assert from_db.score.total == from_api.score.total
