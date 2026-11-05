"""取得結果 (API の生レスポンス) の保存と読み込み。

PC で取得したデータを JSON に保存しておけば、後から API なしで分析・表示できる。
J-Quants の利用規約上、取得データの再配布は認められていないため、
保存先 (既定: data/snapshots) は .gitignore 対象にしている。公開リポジトリにはコミットしないこと。
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


def latest_snapshots(directory: Path, codes: list[str] | None = None) -> dict[str, Path]:
    """銘柄ごとに最新の保存ファイルを返す。codes 指定時はその順序で、見つからない銘柄は含まない。"""
    latest: dict[str, Path] = {}
    for path in sorted(directory.glob("*_*.json")):  # ファイル名の日時順 = 取得順
        code = path.name.split("_", 1)[0]
        latest[code] = path
    if codes is None:
        return dict(sorted(latest.items()))
    return {c: latest[c] for c in codes if c in latest}
