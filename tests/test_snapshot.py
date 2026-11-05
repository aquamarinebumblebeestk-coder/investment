from datetime import date

import pytest

from jpstock.analyzer import Analyzer, build_report
from jpstock.plans import get_plan
from jpstock.snapshot import load_snapshot, save_snapshot

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


def test_unknown_format_rejected(tmp_path):
    path = tmp_path / "7203_x.json"
    path.write_text('{"format_version": 99}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_snapshot(path)


def test_fetch_skips_plan_features_on_free():
    snap = _snap("free")
    assert snap.valuation is None and snap.dividends is None
    assert snap.price_to == "2026-08-04"
