from datetime import date

from jpstock.quality import ERROR, WARN, counts, dataset_status, run_checks
from jpstock.store import Store

DAYS = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"]


def _store():
    s = Store(":memory:")
    s.upsert_calendar([{"Date": d, "HolDiv": "1"} for d in DAYS] + [{"Date": "2026-08-08", "HolDiv": "0"}])
    s.upsert_listings([{"Date": "2026-08-06", "Code": c} for c in ("72030", "67580", "13010")])
    return s


def _bar(d, code, close, o=None, factor=1):
    return {"Date": d, "Code": code, "O": o or close, "H": close + 5, "L": close - 5, "C": close, "Vo": 10,
            "AdjFactor": factor}


def test_detects_gaps_anomalies_and_jumps():
    s = _store()
    bars = [_bar(d, "72030", 100) for d in DAYS[:3]]
    bars += [_bar(DAYS[0], "67580", 100), _bar(DAYS[1], "67580", 200)]  # 急変 (調整係数なし)
    bars += [_bar(DAYS[2], "67580", 100, o=500)]  # 始値が高値を超える
    s.upsert_bars(bars)
    for d in DAYS[:3]:
        s.mark_synced("bars", d, 1)
    s.upsert_fins([{"Code": "72030", "DiscNo": "1", "DiscDate": DAYS[0]}])
    s.mark_synced("fins", DAYS[0], 1)
    s.commit()

    issues = run_checks(s, date(2026, 8, 1), date(2026, 8, 7))
    by_cat = {i.category: i for i in issues}
    assert by_cat["欠損(株価)"].examples == [DAYS[3]] and by_cat["欠損(株価)"].level == WARN
    assert by_cat["欠損(財務)"].level == ERROR  # 4日中3日未取得
    assert by_cat["異常値"].level == ERROR and "67580 2026-08-05" in by_cat["異常値"].examples
    assert "67580 2026-08-04 100→200" in by_cat["急変"].examples
    assert set(by_cat["欠損(財務・銘柄)"].examples) == {"67580", "13010"}


def test_split_is_not_reported_as_jump():
    s = _store()
    s.upsert_bars([_bar(DAYS[0], "72030", 1000), _bar(DAYS[1], "72030", 500, factor=0.5)])
    issues = run_checks(s, date(2026, 8, 1), date(2026, 8, 7))
    assert "急変" not in {i.category for i in issues}


def test_status_and_counts():
    s = _store()
    s.mark_synced("bars", DAYS[0], 2)
    st = {x.dataset: x for x in dataset_status(s, date(2026, 8, 1), date(2026, 8, 7))}
    assert st["bars"].synced_days == 1 and st["bars"].expected_days == 4 and st["bars"].coverage == 0.25
    assert counts(s)["銘柄 (最新一覧)"] == 3


def test_empty_calendar_is_error():
    issues = run_checks(Store(":memory:"), date(2026, 8, 1), date(2026, 8, 7))
    assert issues[0].level == ERROR
