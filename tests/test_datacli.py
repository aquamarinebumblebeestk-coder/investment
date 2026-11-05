from datetime import date

from jpstock import datacli
from jpstock.analyzer import Analyzer
from jpstock.plans import get_plan
from jpstock.snapshot import save_snapshot
from jpstock.store import Store

from fakes import FakeClient


def test_sync_aborts_in_remote_env(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.delenv("JPSTOCK_ALLOW_REMOTE", raising=False)
    assert datacli.main(["--db", str(tmp_path / "x.db"), "sync"]) == 2
    assert "警告" in capsys.readouterr().err


def test_dry_run_estimates_without_api(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    assert datacli.main(["--db", str(tmp_path / "x.db"), "sync", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "リクエスト" in out and "平日数で概算" in out
    assert "bars 0日" not in out


def test_import_status_check_history(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    snap = Analyzer(FakeClient(get_plan("free")), today=date(2026, 10, 27)).fetch("7203")
    save_snapshot(snap, tmp_path / "snaps")
    db = str(tmp_path / "x.db")
    assert datacli.main(["--db", db, "import-snapshots", str(tmp_path / "snaps")]) == 0
    with Store(db) as s:
        assert len(s.bars("7203")) == 30
    assert datacli.main(["--db", db, "status"]) == 0
    assert "株価 (行): 30" in capsys.readouterr().out
    assert datacli.main(["--db", db, "check"]) == 1  # カレンダー未取得 → 要対応
    assert datacli.main(["--db", db, "history"]) == 0
    assert "snapshot/import" in capsys.readouterr().out
