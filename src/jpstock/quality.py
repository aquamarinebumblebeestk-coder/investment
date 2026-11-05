"""保存データの鮮度・欠損・品質のチェック。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .store import Store

PRICE_JUMP = 0.5  # 調整係数の変化なしで前日比 ±50% を超えたら要確認
STALE_TRADING_DAYS = 5  # 取得可能な最新日から何営業日遅れたら「古い」とみなすか
MISSING_RATIO_WARN = 0.2  # 期間内の営業日のうち株価がない割合がこれを超える銘柄を報告
EXAMPLES = 5

OK, WARN, ERROR = "OK", "注意", "要対応"


@dataclass
class Issue:
    level: str
    category: str
    message: str
    examples: list[str] = field(default_factory=list)


@dataclass
class DatasetStatus:
    dataset: str
    first: str | None
    last: str | None
    synced_days: int
    expected_days: int
    last_fetched_at: str | None

    @property
    def coverage(self) -> float | None:
        return self.synced_days / self.expected_days if self.expected_days else None


def dataset_status(store: Store, start: date, end: date) -> list[DatasetStatus]:
    trading = store.trading_days(start, end)
    out = []
    for ds in ("bars", "fins"):
        row = store.conn.execute(
            "SELECT MIN(date) AS a, MAX(date) AS b, COUNT(*) AS n, MAX(fetched_at) AS t FROM sync_state "
            "WHERE dataset=? AND date BETWEEN ? AND ?",
            (ds, start.isoformat(), end.isoformat()),
        ).fetchone()
        out.append(DatasetStatus(ds, row["a"], row["b"], row["n"], len(trading), row["t"]))
    return out


def counts(store: Store) -> dict[str, int]:
    q = {
        "銘柄 (最新一覧)": "SELECT COUNT(*) FROM listings WHERE date = (SELECT MAX(date) FROM listings)",
        "株価 (行)": "SELECT COUNT(*) FROM daily_bars",
        "株価 (銘柄)": "SELECT COUNT(DISTINCT code) FROM daily_bars",
        "財務開示 (件)": "SELECT COUNT(*) FROM fin_statements",
        "更新履歴 (件)": "SELECT COUNT(*) FROM revisions",
    }
    return {k: store.conn.execute(sql).fetchone()[0] for k, sql in q.items()}


def run_checks(store: Store, start: date, end: date) -> list[Issue]:
    conn = store.conn
    issues: list[Issue] = []
    s, e = start.isoformat(), end.isoformat()
    trading = store.trading_days(start, end)

    # --- 鮮度・日付の欠損 ---
    if not trading:
        issues.append(Issue(ERROR, "カレンダー", "取引カレンダーがありません。jpstock-data sync を実行してください"))
        return issues
    for st in dataset_status(store, start, end):
        label = "株価" if st.dataset == "bars" else "財務"
        done = store.synced_dates(st.dataset)
        missing = [d for d in trading if d not in done]
        if missing:
            level = ERROR if len(missing) > len(trading) // 2 else WARN
            issues.append(
                Issue(level, f"欠損({label})", f"未取得の営業日が {len(missing)}/{len(trading)} 日あります", missing[:EXAMPLES])
            )
        if st.last is not None:
            behind = len([d for d in trading if d > st.last])
            if behind >= STALE_TRADING_DAYS:
                issues.append(Issue(WARN, f"鮮度({label})", f"取得可能な最新日 {trading[-1]} より {behind} 営業日古いデータです"))
        if st.last_fetched_at:
            age = (datetime.now() - datetime.fromisoformat(st.last_fetched_at)).days
            if age >= 7:
                issues.append(Issue(WARN, f"鮮度({label})", f"最終取得から {age} 日経過しています ({st.last_fetched_at})"))

    # --- 銘柄ごとの株価欠損 (売買停止の可能性もある) ---
    synced = sorted(store.synced_dates("bars") & set(trading))
    if synced:
        rows = conn.execute(
            "SELECT code, COUNT(*) AS n FROM daily_bars WHERE date BETWEEN ? AND ? GROUP BY code", (synced[0], synced[-1])
        ).fetchall()
        sparse = [
            f"{r['code']}({r['n']}/{len(synced)}日)" for r in rows if 1 - r["n"] / len(synced) > MISSING_RATIO_WARN
        ]
        if sparse:
            issues.append(
                Issue(WARN, "欠損(銘柄)", f"株価の欠けが {MISSING_RATIO_WARN:.0%} を超える銘柄が {len(sparse)} 件 (新規上場・売買停止を含む)", sparse[:EXAMPLES])
            )
        no_close = conn.execute(
            "SELECT code, date FROM daily_bars WHERE date BETWEEN ? AND ? AND close IS NULL", (s, e)
        ).fetchall()
        if no_close:
            issues.append(
                Issue(OK if len(no_close) < 50 else WARN, "欠損(値)", f"終値のない行が {len(no_close)} 件 (売買不成立の日)",
                      [f"{r['code']} {r['date']}" for r in no_close[:EXAMPLES]])
            )

    # --- 異常値 ---
    bad = conn.execute(
        "SELECT code, date FROM daily_bars WHERE date BETWEEN ? AND ? AND ("
        " high < low OR close > high OR close < low OR open > high OR open < low"
        " OR close <= 0 OR volume < 0)",
        (s, e),
    ).fetchall()
    if bad:
        issues.append(Issue(ERROR, "異常値", f"四本値の整合が取れない行が {len(bad)} 件", [f"{r['code']} {r['date']}" for r in bad[:EXAMPLES]]))

    jumps = conn.execute(
        """
        SELECT code, date, close, prev_close FROM (
            SELECT code, date, close, adj_factor,
                   LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
            FROM daily_bars WHERE date BETWEEN ? AND ?
        )
        WHERE prev_close > 0 AND close IS NOT NULL AND COALESCE(adj_factor, 1) = 1
          AND ABS(close / prev_close - 1) > ?
        """,
        (s, e, PRICE_JUMP),
    ).fetchall()
    if jumps:
        issues.append(
            Issue(WARN, "急変", f"調整係数の変化なしで前日比 ±{PRICE_JUMP:.0%} 超の変動が {len(jumps)} 件",
                  [f"{r['code']} {r['date']} {r['prev_close']:.0f}→{r['close']:.0f}" for r in jumps[:EXAMPLES]])
        )

    # --- 財務 ---
    no_fin = conn.execute(
        "SELECT l.code FROM listings l WHERE l.date = (SELECT MAX(date) FROM listings) "
        "AND NOT EXISTS (SELECT 1 FROM fin_statements f WHERE f.code = l.code)"
    ).fetchall()
    if no_fin:
        issues.append(Issue(WARN, "欠損(財務・銘柄)", f"財務情報が1件もない上場銘柄が {len(no_fin)} 件 (ETF・REIT 等を含む)", [r["code"] for r in no_fin[:EXAMPLES]]))

    # --- 更新履歴 ---
    rev = conn.execute("SELECT table_name, COUNT(*) AS n FROM revisions GROUP BY table_name").fetchall()
    for r in rev:
        issues.append(Issue(OK, "更新履歴", f"{r['table_name']}: 再取得で値が変わった記録が {r['n']} 件"))

    failed = conn.execute("SELECT COUNT(*) FROM fetch_log WHERE status != 'ok'").fetchone()[0]
    if failed:
        issues.append(Issue(WARN, "取得ログ", f"失敗/中断した取得が {failed} 件あります (jpstock-data history で確認)"))
    return issues
