"""データ基盤のコマンド: jpstock-data sync / status / check / history / import-snapshots"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .client import JQuantsClient, JQuantsError
from .codes import InvalidCodeError, parse_codes
from .config import load_settings
from .plans import PLANS
from .preflight import PreflightError, run_preflight
from .quality import ERROR, counts, dataset_status, run_checks
from .snapshot import DEFAULT_DIR, load_snapshot
from .store import DEFAULT_DB, Store, now_iso
from .sync import DATASETS, Syncer


def _date(s: str) -> date:
    return date.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jpstock-data", description="J-Quants データの保存 (SQLite) と品質管理")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"DB ファイル (既定: {DEFAULT_DB})")
    p.add_argument("--plan", choices=list(PLANS), help="契約プラン (既定: 環境変数 JQUANTS_PLAN か free)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sync", help="API から取得して DB を更新する (中断しても続きから再開)")
    s.add_argument("--datasets", default=",".join(DATASETS), help=f"取得対象 ({'/'.join(DATASETS)})")
    s.add_argument("--from", dest="start", type=_date, help="開始日 YYYY-MM-DD (既定: プランの提供期間の最初)")
    s.add_argument("--to", dest="end", type=_date, help="終了日 YYYY-MM-DD (既定: 取得可能な最新日)")
    s.add_argument("--codes", help="指定銘柄だけを銘柄単位で取得する (カンマ区切り)")
    s.add_argument("--refresh", action="store_true", help="取得済みの日付も取り直す (値が変わったものは更新履歴に残る)")
    s.add_argument("--dry-run", action="store_true", help="取得せずに必要なリクエスト数と所要時間の見積もりだけ表示")

    sub.add_parser("status", help="保存データの件数・期間・鮮度を表示")
    sub.add_parser("check", help="欠損・異常値・鮮度をチェック")
    h = sub.add_parser("history", help="取得ログを表示")
    h.add_argument("--limit", type=int, default=20)
    i = sub.add_parser("import-snapshots", help="旧形式 (JSON の保存データ) を DB に取り込む")
    i.add_argument("dir", nargs="?", type=Path, default=DEFAULT_DIR)
    return p


def cmd_sync(args: argparse.Namespace, store: Store) -> int:
    settings = load_settings(args.plan)
    datasets = tuple(x.strip() for x in args.datasets.split(",") if x.strip())
    unknown = [d for d in datasets if d not in DATASETS]
    if unknown:
        print(f"不明なデータセット: {', '.join(unknown)}", file=sys.stderr)
        return 1
    if not args.dry_run:
        try:
            run_preflight(settings)
        except PreflightError as e:
            print("警告: J-Quants API からデータを取得できない状態のため、処理を中断します。", file=sys.stderr)
            for issue in e.issues:
                print(f"  - {issue.reason}\n    → {issue.hint}", file=sys.stderr)
            return 2
    syncer = Syncer(JQuantsClient(settings.api_key, settings.plan), store)
    plan = settings.plan

    if args.codes:
        try:
            codes = parse_codes(args.codes)
        except InvalidCodeError as e:
            print(f"入力エラー: {e}", file=sys.stderr)
            return 1
        if args.dry_run:
            print(f"{len(codes)} 銘柄 × 約3リクエスト")
            return 0
        syncer.sync_codes(codes, args.start, args.end)
        return 0

    est = syncer.plan_sync(datasets, args.start, args.end, args.refresh)
    minutes = est.estimated_calls / plan.requests_per_minute
    print(
        f"期間 {est.start} 〜 {est.end} / 未取得: "
        + ", ".join(f"{k} {len(v)}日" for k, v in est.days.items())
        + f" / 約{est.estimated_calls}リクエスト (≒{minutes:.0f}分, {plan.label}プラン {plan.requests_per_minute}回/分)"
    )
    if not store.trading_days(est.start, est.end):
        print("(取引カレンダー未取得のため平日数で概算しています。祝日の分だけ多めの見積もりです)")
    if args.dry_run:
        return 0
    try:
        syncer.run(datasets, args.start, args.end, args.refresh)
    except KeyboardInterrupt:
        print("\n中断しました。取得済みの分は保存されています。再実行すると続きから取得します。", file=sys.stderr)
        return 130
    except JQuantsError as e:
        print(f"取得エラー: {e}\n取得済みの分は保存されています。", file=sys.stderr)
        return 3
    print("完了しました。jpstock-data check で品質を確認できます。")
    return 0


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    start, end = load_settings(args.plan).plan.data_window(date.today())
    print(f"DB: {store.path}")
    for k, v in counts(store).items():
        print(f"  {k}: {v:,}")
    print(f"対象期間 (プランの提供期間): {start} 〜 {end}")
    for st in dataset_status(store, start, end):
        label = "株価" if st.dataset == "bars" else "財務"
        cov = "-" if st.coverage is None else f"{st.coverage:.0%}"
        print(
            f"  {label}: {st.first or '-'} 〜 {st.last or '-'}  取得済み {st.synced_days}/{st.expected_days} 営業日 ({cov})"
            f"  最終取得 {st.last_fetched_at or '-'}"
        )
    return 0


def cmd_check(args: argparse.Namespace, store: Store) -> int:
    start, end = load_settings(args.plan).plan.data_window(date.today())
    issues = run_checks(store, start, end)
    if not issues:
        print("問題は見つかりませんでした")
    for i in issues:
        print(f"[{i.level}] {i.category}: {i.message}")
        if i.examples:
            print(f"    例: {', '.join(i.examples)}")
    return 1 if any(i.level == ERROR for i in issues) else 0


def cmd_history(args: argparse.Namespace, store: Store) -> int:
    for r in store.fetch_history(args.limit):
        err = f"  {r['error']}" if r["error"] else ""
        print(f"{r['started_at']}  {r['status']:5}  {r['source']}/{r['dataset']}  {r['params']}  {r['rows'] or 0}件{err}")
    return 0


def cmd_import(args: argparse.Namespace, store: Store) -> int:
    paths = sorted(args.dir.glob("*.json")) if args.dir.is_dir() else []
    if not paths:
        print(f"{args.dir} に JSON の保存データがありません", file=sys.stderr)
        return 1
    for path in paths:
        try:
            snap = load_snapshot(path)
        except (OSError, ValueError, TypeError) as e:
            print(f"  スキップ: {path} ({e})", file=sys.stderr)
            continue
        with store.logged_fetch("snapshot", "import", {"file": path.name}) as res:
            store.save_snapshot(snap)
            res["rows"] = len(snap.bars) + len(snap.summary)
        print(f"  取り込み: {path.name}")
    print(f"{len(paths)} ファイルを {store.path} に取り込みました ({now_iso()})")
    return 0


COMMANDS = {
    "sync": cmd_sync,
    "status": cmd_status,
    "check": cmd_check,
    "history": cmd_history,
    "import-snapshots": cmd_import,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with Store(args.db) as store:
        return COMMANDS[args.command](args, store)


if __name__ == "__main__":
    sys.exit(main())
