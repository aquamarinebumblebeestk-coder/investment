"""1銘柄分の取得結果 (API の生レスポンス) と、JSON 形式での保存・読み込み。

Snapshot は API・DB から分析 (analyzer.build_report) へデータを渡す入れ物。
保存の本体は SQLite (store.py) に移行済みで、JSON は旧形式の取り込み
(jpstock-data import-snapshots) とテスト用に残している。
J-Quants の取得データは再配布不可のため、公開リポジトリにはコミットしないこと。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path("data/snapshots")
FORMAT_VERSION = 1


@dataclass
class Snapshot:
    code: str
    fetched_at: str  # ISO 8601 (取得日時)
    plan: str
    price_from: str
    price_to: str
    master: list[dict[str, Any]] = field(default_factory=list)
    bars: list[dict[str, Any]] = field(default_factory=list)
    summary: list[dict[str, Any]] = field(default_factory=list)
    valuation: list[dict[str, Any]] | None = None  # None = 取得していない
    dividends: list[dict[str, Any]] | None = None
    notes: list[str] = field(default_factory=list)


def snapshot_path(snap: Snapshot, directory: Path) -> Path:
    stamp = snap.fetched_at[:19].replace("-", "").replace(":", "").replace("T", "_")
    return directory / f"{snap.code}_{stamp}.json"


def save_snapshot(snap: Snapshot, directory: Path = DEFAULT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(snap, directory)
    payload = {"format_version": FORMAT_VERSION, **asdict(snap)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> Snapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.pop("format_version", None)
    if version != FORMAT_VERSION:
        raise ValueError(f"{path}: 対応していない保存形式です (format_version={version})")
    return Snapshot(**data)
