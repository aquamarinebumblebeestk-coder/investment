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
