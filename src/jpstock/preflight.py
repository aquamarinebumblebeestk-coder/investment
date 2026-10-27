"""実行前チェック。

J-Quants API からデータを取れない状態 (スマホ経由のクラウド実行、APIキー未設定、
api.jquants.com に到達できない等) で実行されたら、取得を始める前に警告して中断する。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from .client import API_BASE
from .config import ENV_API_KEY, Settings

# クラウド実行環境 (スマホ・Web から指示した Claude Code など) で設定される環境変数
REMOTE_ENV_MARKERS = ("CLAUDE_CODE_REMOTE",)
# クラウド上でもネットワーク許可などを整えて実行したい場合に限り "1" を設定する
ENV_ALLOW_REMOTE = "JPSTOCK_ALLOW_REMOTE"


@dataclass(frozen=True)
class PreflightIssue:
    reason: str
    hint: str


class PreflightError(RuntimeError):
    def __init__(self, issues: list[PreflightIssue]):
        self.issues = issues
        super().__init__("; ".join(i.reason for i in issues))


def detect_remote_environment(env: dict[str, str] | None = None) -> str | None:
    env = os.environ if env is None else env
    if env.get(ENV_ALLOW_REMOTE) == "1":
        return None
    for key in REMOTE_ENV_MARKERS:
        if env.get(key, "").lower() in ("1", "true", "yes"):
            return key
    return None


def can_reach_api(timeout: float = 5.0) -> bool:
    host = urlparse(API_BASE).hostname or ""
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            return True
    except OSError:
        return False


def run_preflight(settings: Settings, *, check_network: bool = True, env: dict[str, str] | None = None) -> None:
    """問題があれば PreflightError を送出する。"""
    issues: list[PreflightIssue] = []

    marker = detect_remote_environment(env)
    if marker:
        issues.append(
            PreflightIssue(
                f"クラウド/リモート実行環境で起動されています ({marker})。"
                "スマホ等から指示された実行では J-Quants API に接続できない想定です。",
                f"PC のローカル環境で実行してください (接続可能と分かっている場合のみ {ENV_ALLOW_REMOTE}=1 で回避可)。",
            )
        )
    if not settings.api_key:
        issues.append(
            PreflightIssue(
                f"APIキーが設定されていません ({ENV_API_KEY})。",
                "J-Quants のダッシュボードで API キーを発行し、.env か環境変数に設定してください。",
            )
        )
    # リモート判定済みなら接続確認は省略 (どのみち中断するため)
    if check_network and not marker and not can_reach_api():
        issues.append(
            PreflightIssue(
                f"{urlparse(API_BASE).hostname} に接続できません。",
                "ネットワーク接続・プロキシ設定を確認してください。",
            )
        )
    if issues:
        raise PreflightError(issues)
