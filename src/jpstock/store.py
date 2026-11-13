"""SQLite によるデータ保存 (第1段階: データ基盤)。

方針:
- 株価は「生の四本値 + 調整係数 (AdjFactor)」で保存し、調整後株価は読み出し時に計算する。
  分割・併合が起きると過去の調整後株価が全て変わるため、API の調整後値をそのまま溜めると不整合になる。
- 財務情報は開示番号 (DiscNo) 単位で保存する。訂正開示は別番号で届くので、過去の開示も残る。
- 同じキーのデータを再取得して値が変わった場合は、旧値を revisions に退避する (更新履歴)。
- API 呼び出しは fetch_log に記録し、日付単位の取得済み状況は sync_state で管理する (欠損検出・再開用)。
- as_of を指定すると、その日までに得られたデータだけを返す (バックテストで未来の情報を使わないため)。

J-Quants の取得データは再配布不可のため、DB ファイル (既定: data/jpstock.db) はコミットしないこと。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .snapshot import Snapshot

DEFAULT_DB = Path("data/jpstock.db")
SCHEMA_VERSION = 1
TRADING_HOL_DIV = ("1", "2")  # 取引カレンダーの休日区分: 1=営業日, 2=半日立会

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar (date TEXT PRIMARY KEY, hol_div TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listings (
    date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, market TEXT, sector33 TEXT, raw TEXT NOT NULL,
    PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS daily_bars (
    code TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL, adj_factor REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars (date);
CREATE TABLE IF NOT EXISTS fin_statements (
    disc_no TEXT PRIMARY KEY, code TEXT NOT NULL, disc_date TEXT NOT NULL, disc_time TEXT,
    doc_type TEXT, per_type TEXT, fy_end TEXT, raw TEXT NOT NULL, fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fin_code_date ON fin_statements (code, disc_date);
CREATE TABLE IF NOT EXISTS sync_state (
    dataset TEXT NOT NULL, date TEXT NOT NULL, rows INTEGER NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (dataset, date)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
    source TEXT NOT NULL, dataset TEXT NOT NULL, params TEXT, rows INTEGER, status TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, row_key TEXT NOT NULL,
    old_value TEXT NOT NULL, new_value TEXT NOT NULL, replaced_at TEXT NOT NULL
);
"""

_BAR_COLS = ("open", "high", "low", "close", "volume", "turnover", "adj_factor")
_BAR_KEYS = ("O", "H", "L", "C", "Vo", "Va", "AdjFactor")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def api_code(code: str) -> str:
    """4桁コードを API の5桁形式に (5桁はそのまま)。"""
    return code if len(code) == 5 else code + "0"


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso(d: date | str) -> str:
    return d.isoformat() if isinstance(d, date) else d


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- 取得ログ ---------------------------------------------------------
    @contextmanager
    def logged_fetch(self, source: str, dataset: str, params: dict[str, Any]) -> Iterator[dict[str, int]]:
        """API 呼び出し等を fetch_log に記録する。yield した dict の rows に件数を入れる。"""
        cur = self.conn.execute(
            "INSERT INTO fetch_log (started_at, source, dataset, params, status) VALUES (?, ?, ?, ?, 'running')",
            (now_iso(), source, dataset, json.dumps(params, ensure_ascii=False)),
        )
        log_id = cur.lastrowid
        self.conn.commit()
        result = {"rows": 0}
        try:
            yield result
        except BaseException as e:
            self.conn.rollback()
            self.conn.execute(
                "UPDATE fetch_log SET finished_at=?, status='error', error=? WHERE id=?",
                (now_iso(), f"{type(e).__name__}: {e}", log_id),
            )
            self.conn.commit()
            raise
        self.conn.execute(
            "UPDATE fetch_log SET finished_at=?, rows=?, status='ok' WHERE id=?", (now_iso(), result["rows"], log_id)
        )
        self.conn.commit()

    def fetch_history(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM fetch_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---- 書き込み ---------------------------------------------------------
    def _record_revision(self, table: str, key: str, old: dict[str, Any], new: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO revisions (table_name, row_key, old_value, new_value, replaced_at) VALUES (?, ?, ?, ?, ?)",
            (table, key, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), now_iso()),
        )

    def upsert_calendar(self, rows: list[dict[str, Any]]) -> int:
        self.conn.executemany(
            "INSERT OR REPLACE INTO calendar (date, hol_div) VALUES (?, ?)",
            [(r["Date"], str(r.get("HolDiv", ""))) for r in rows if r.get("Date")],
        )
        return len(rows)

    def upsert_listings(self, rows: list[dict[str, Any]], as_of: str | None = None) -> int:
        """上場銘柄一覧。Date がない行は as_of (取得日) で記録する。"""
        data = [
            (
                r.get("Date") or as_of,
                r["Code"],
                r.get("CoName"),
                r.get("MktNm"),
                r.get("S33Nm"),
                json.dumps(r, ensure_ascii=False, sort_keys=True),
            )
            for r in rows
            if r.get("Code") and (r.get("Date") or as_of)
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO listings (date, code, name, market, sector33, raw) VALUES (?, ?, ?, ?, ?, ?)", data
        )
        return len(data)

    def upsert_bars(self, rows: list[dict[str, Any]], fetched_at: str | None = None) -> int:
        """株価 (生値 + 調整係数)。既存行と値が違えば旧値を revisions に残す。"""
        fetched_at = fetched_at or now_iso()
        count = 0
        for r in rows:
            code, d = r.get("Code"), r.get("Date")
            if not code or not d:
                continue
            new = {c: _num(r.get(k)) for c, k in zip(_BAR_COLS, _BAR_KEYS)}
            old_row = self.conn.execute(
                f"SELECT {', '.join(_BAR_COLS)} FROM daily_bars WHERE code=? AND date=?", (code, d)
            ).fetchone()
            if old_row is not None:
                old = dict(old_row)
                if old == new:
                    continue  # 同じ値なら取得日時も更新しない (初回取得日時を残す)
                self._record_revision("daily_bars", f"{code}:{d}", old, new)
            self.conn.execute(
                f"INSERT OR REPLACE INTO daily_bars (code, date, {', '.join(_BAR_COLS)}, fetched_at) "
                f"VALUES (?, ?, {', '.join('?' * len(_BAR_COLS))}, ?)",
                (code, d, *new.values(), fetched_at),
            )
            count += 1
        return count

    def upsert_fins(self, rows: list[dict[str, Any]], fetched_at: str | None = None) -> int:
        fetched_at = fetched_at or now_iso()
        count = 0
        for r in rows:
            code = r.get("Code")
            disc_no = r.get("DiscNo") or f"{code}:{r.get('DiscDate')}:{r.get('DiscTime')}:{r.get('DocType')}"
            if not code or not r.get("DiscDate"):
                continue
            raw = json.dumps(r, ensure_ascii=False, sort_keys=True)
            old = self.conn.execute("SELECT raw FROM fin_statements WHERE disc_no=?", (disc_no,)).fetchone()
            if old is not None:
                if old["raw"] == raw:
                    continue
                self._record_revision("fin_statements", disc_no, json.loads(old["raw"]), r)
            self.conn.execute(
                "INSERT OR REPLACE INTO fin_statements "
                "(disc_no, code, disc_date, disc_time, doc_type, per_type, fy_end, raw, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    disc_no,
                    code,
                    r["DiscDate"],
                    r.get("DiscTime"),
                    r.get("DocType"),
                    r.get("CurPerType"),
                    r.get("CurFYEn"),
                    raw,
                    fetched_at,
                ),
            )
            count += 1
        return count

    def mark_synced(self, dataset: str, d: date | str, rows: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sync_state (dataset, date, rows, fetched_at) VALUES (?, ?, ?, ?)",
            (dataset, _iso(d), rows, now_iso()),
        )

    def commit(self) -> None:
        self.conn.commit()

    def save_snapshot(self, snap: Snapshot) -> None:
        """銘柄単位の取得結果 (Snapshot) を DB に取り込む。"""
        self.upsert_listings(snap.master, as_of=snap.fetched_at[:10])
        self.upsert_bars(snap.bars, fetched_at=snap.fetched_at)
        self.upsert_fins(snap.summary, fetched_at=snap.fetched_at)
        self.commit()

    # ---- 読み出し ---------------------------------------------------------
    def trading_days(self, start: date | str, end: date | str) -> list[str]:
        rows = self.conn.execute(
            f"SELECT date FROM calendar WHERE date BETWEEN ? AND ? AND hol_div IN ({','.join('?' * len(TRADING_HOL_DIV))}) "
            "ORDER BY date",
            (_iso(start), _iso(end), *TRADING_HOL_DIV),
        ).fetchall()
        return [r["date"] for r in rows]

    def calendar_range(self) -> tuple[str | None, str | None]:
        row = self.conn.execute("SELECT MIN(date) AS a, MAX(date) AS b FROM calendar").fetchone()
        return row["a"], row["b"]

    def synced_dates(self, dataset: str) -> set[str]:
        return {r["date"] for r in self.conn.execute("SELECT date FROM sync_state WHERE dataset=?", (dataset,))}

    def bars(self, code: str, start: str | None = None, as_of: str | None = None) -> list[dict[str, Any]]:
        """API と同じキー (O/H/L/C/Vo と AdjO/AdjH/AdjL/AdjC/AdjVo) の行を返す。

        調整後株価は as_of 時点までの調整係数で計算するため、as_of 以降の分割は反映されない
        (= その時点で見えていた株価になる)。
        """
        code5 = api_code(code)
        rows = self.conn.execute(
            "SELECT * FROM daily_bars WHERE code=? AND (? IS NULL OR date <= ?) ORDER BY date",
            (code5, as_of, as_of),
        ).fetchall()
        out: list[dict[str, Any]] = []
        mult = 1.0  # 以降の日付の調整係数の積
        for r in reversed(rows):
            if start is None or r["date"] >= start:
                o, h, lo, c, vo = (r[k] for k in ("open", "high", "low", "close", "volume"))
                out.append(
                    {
                        "Date": r["date"],
                        "Code": code5,
                        "O": o, "H": h, "L": lo, "C": c, "Vo": vo, "Va": r["turnover"],
                        "AdjFactor": r["adj_factor"],
                        "AdjO": o * mult if o is not None else None,
                        "AdjH": h * mult if h is not None else None,
                        "AdjL": lo * mult if lo is not None else None,
                        "AdjC": c * mult if c is not None else None,
                        "AdjVo": vo / mult if vo is not None and mult else None,
                    }
                )
            mult *= r["adj_factor"] or 1.0
        out.reverse()
        return out

    def fins(self, code: str, as_of: str | None = None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT raw FROM fin_statements WHERE code=? AND (? IS NULL OR disc_date <= ?) ORDER BY disc_date, disc_time",
            (api_code(code), as_of, as_of),
        ).fetchall()
        return [json.loads(r["raw"]) for r in rows]

    def listing(self, code: str, as_of: str | None = None) -> list[dict[str, Any]]:
        """as_of 時点の銘柄情報。それ以前の一覧がなければ最も古い一覧の情報を使う (universe と同じ扱い)。"""
        row = self.conn.execute(
            "SELECT raw FROM listings WHERE code=? ORDER BY (? IS NULL OR date <= ?) DESC,"
            " CASE WHEN ? IS NULL OR date <= ? THEN date END DESC, date ASC LIMIT 1",
            (api_code(code), as_of, as_of, as_of, as_of),
        ).fetchone()
        return [json.loads(row["raw"])] if row else []

    def universe(self, as_of: str | None = None) -> tuple[str | None, list[dict[str, Any]]]:
        """as_of 時点 (省略時は最新) の上場銘柄一覧と、その一覧の日付。

        as_of より前の一覧がない場合は最も古い一覧を使う (その後に上場廃止した銘柄が漏れる点に注意)。
        """
        row = self.conn.execute(
            "SELECT COALESCE((SELECT MAX(date) FROM listings WHERE ? IS NULL OR date <= ?),"
            " (SELECT MIN(date) FROM listings)) AS d",
            (as_of, as_of),
        ).fetchone()
        if row["d"] is None:
            return None, []
        rows = self.conn.execute("SELECT raw FROM listings WHERE date=? ORDER BY code", (row["d"],)).fetchall()
        return row["d"], [json.loads(r["raw"]) for r in rows]

    def last_fetched_at(self, code: str) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(fetched_at) AS t FROM daily_bars WHERE code=?", (api_code(code),)
        ).fetchone()
        return row["t"]

    def snapshot(self, code: str, plan: str, *, price_from: str | None = None, as_of: str | None = None) -> Snapshot:
        """DB から分析用の Snapshot を組み立てる (API は呼ばない)。"""
        bars = self.bars(code, start=price_from, as_of=as_of)
        return Snapshot(
            code=code,
            fetched_at=self.last_fetched_at(code) or "",
            plan=plan,
            price_from=bars[0]["Date"] if bars else (price_from or ""),
            price_to=bars[-1]["Date"] if bars else (as_of or ""),
            master=self.listing(code, as_of),
            bars=bars,
            summary=self.fins(code, as_of),
            notes=[f"{as_of} 時点のデータで分析しています"] if as_of else [],
        )
