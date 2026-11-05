from jpstock import cli


def test_cli_aborts_with_warning_in_remote_env(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)  # 手元の .env を読まないように
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")
    monkeypatch.delenv("JPSTOCK_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("JQUANTS_API_KEY", "dummy")
    assert cli.main(["7203,6758"]) == cli.EXIT_PREFLIGHT
    assert "警告" in capsys.readouterr().err


def test_cli_rejects_unknown_section(monkeypatch, tmp_path):
    import pytest

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["7203", "--sections", "basic,foo"])


def test_cli_from_saved_works_without_api(monkeypatch, capsys, tmp_path):
    from datetime import date

    from fakes import FakeClient
    from jpstock.analyzer import Analyzer
    from jpstock.plans import get_plan
    from jpstock.snapshot import save_snapshot

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")  # リモート環境でも保存データなら分析できる
    analyzer = Analyzer(FakeClient(get_plan("free")), today=date(2026, 10, 27))
    a = analyzer.fetch("7203")
    b = analyzer.fetch("7203")
    b.code = "6758"
    for s in (a, b):
        save_snapshot(s, tmp_path / "snaps")
    assert cli.main(["--from-saved", str(tmp_path / "snaps")]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "[6758]" in out and "[7203]" in out and "銘柄比較" in out and "取得日時" in out


def test_cli_from_saved_missing_dir(tmp_path, capsys):
    assert cli.main(["7203", "--from-saved", str(tmp_path / "none")]) == cli.EXIT_INPUT_ERROR
