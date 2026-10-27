"""設定の読み込み (環境変数 / .env)。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .plans import Plan, get_plan

ENV_API_KEY = "JQUANTS_API_KEY"
ENV_PLAN = "JQUANTS_PLAN"


@dataclass(frozen=True)
class Settings:
    api_key: str
    plan: Plan


def load_dotenv(path: Path) -> None:
    """KEY=VALUE 形式の .env を読み、未設定の環境変数だけを埋める (依存ライブラリを増やさないための簡易版)。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_settings(plan_override: str | None = None, dotenv: Path | None = Path(".env")) -> Settings:
    if dotenv is not None:
        load_dotenv(dotenv)
    plan_name = plan_override or os.environ.get(ENV_PLAN, "free")
    return Settings(api_key=os.environ.get(ENV_API_KEY, "").strip(), plan=get_plan(plan_name))
