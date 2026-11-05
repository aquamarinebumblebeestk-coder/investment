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


def test_cli_save_then_from_saved_without_api(monkeypatch, capsys, tmp_path):
    from datetime import date

    from fakes import FakeClient
    from jpstock.analyzer import Analyzer
    from jpstock.plans import get_plan
    from jpstock.store import Store

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "true")  # リモート環境でも保存データなら分析できる
    analyzer = Analyzer(FakeClient(get_plan("free")), today=date(2026, 10, 27))
    a = analyzer.fetch("7203")
    b = analyzer.fetch("7203")
    b.code = "6758"
    b.master = [dict(b.master[0], Code="67580", CoName="ソニーグループ")]
    b.bars = [dict(x, Code="67580") for x in b.bars]
    b.summary = [dict(x, Code="67580", DiscNo=f"s{i}") for i, x in enumerate(b.summary)]
    with Store(tmp_path / "db.sqlite") as store:
        store.save_snapshot(a)
        store.save_snapshot(b)
    assert cli.main(["7203,6758", "--from-saved", str(tmp_path / "db.sqlite")]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "[6758] ソニーグループ" in out and "[7203] トヨタ自動車" in out and "銘柄比較" in out
    assert "財務分析" in out and "テクニカル分析" in out


def test_cli_from_saved_unknown_code_is_partial(tmp_path, capsys):
    from jpstock.store import Store

    Store(tmp_path / "db.sqlite").close()
    assert cli.main(["7203", "--from-saved", str(tmp_path / "db.sqlite")]) == cli.EXIT_PARTIAL
    assert "保存データがありません" in capsys.readouterr().out


def test_cli_from_saved_missing_db(tmp_path):
    assert cli.main(["7203", "--from-saved", str(tmp_path / "none.db")]) == cli.EXIT_INPUT_ERROR
