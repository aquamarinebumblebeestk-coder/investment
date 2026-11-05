from datetime import date

import pytest

from jpstock.analyzer import Analyzer, build_report
from jpstock.plans import get_plan
from jpstock.snapshot import latest_snapshots, load_snapshot, save_snapshot

from fakes import FakeClient


def _snap(plan="free"):
    return Analyzer(FakeClient(get_plan(plan)), today=date(2026, 10, 27)).fetch("7203")


def test_save_and_load_roundtrip(tmp_path):
    snap = _snap("premium")
    path = save_snapshot(snap, tmp_path)
    loaded = load_snapshot(path)
    assert loaded == snap
    # 保存データからでも取得直後と同じレポートになる
    assert build_report(loaded) == build_report(snap)


def test_latest_snapshot_per_code(tmp_path):
    old, new, other = _snap(), _snap(), _snap()
    old.fetched_at, new.fetched_at = "2026-10-01T09:00:00", "2026-10-20T09:00:00"
    other.code, other.fetched_at = "6758", "2026-10-05T09:00:00"
    for s in (new, old, other):
        save_snapshot(s, tmp_path)
    latest = latest_snapshots(tmp_path)
    assert list(latest) == ["6758", "7203"]
    assert "20261020" in latest["7203"].name
    assert list(latest_snapshots(tmp_path, ["7203", "9999"])) == ["7203"]


def test_unknown_format_rejected(tmp_path):
    path = tmp_path / "7203_x.json"
    path.write_text('{"format_version": 99}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_snapshot(path)


def test_fetch_skips_plan_features_on_free():
    snap = _snap("free")
    assert snap.valuation is None and snap.dividends is None
    assert snap.price_to == "2026-08-04"
