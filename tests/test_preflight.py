import pytest

from jpstock.config import Settings
from jpstock.plans import get_plan
from jpstock.preflight import PreflightError, detect_remote_environment, run_preflight


def _settings(key="dummy"):
    return Settings(api_key=key, plan=get_plan("free"))


def test_remote_environment_aborts():
    with pytest.raises(PreflightError) as e:
        run_preflight(_settings(), env={"CLAUDE_CODE_REMOTE": "true"})
    assert "リモート" in e.value.issues[0].reason


def test_remote_override():
    assert detect_remote_environment({"CLAUDE_CODE_REMOTE": "true", "JPSTOCK_ALLOW_REMOTE": "1"}) is None


def test_missing_api_key_aborts():
    with pytest.raises(PreflightError) as e:
        run_preflight(_settings(key=""), check_network=False, env={})
    assert any("APIキー" in i.reason for i in e.value.issues)


def test_ok_locally():
    run_preflight(_settings(), check_network=False, env={})
