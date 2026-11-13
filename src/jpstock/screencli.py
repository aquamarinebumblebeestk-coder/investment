"""全銘柄スクリーニングのコマンド: jpstock-screen"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .config import load_settings
from .plans import PLANS
from .report import table
from .screening import DEFAULT_COLUMNS, METRICS, PRESETS, ScreeningError, format_value, parse_condition, screen
from .store import DEFAULT_DB, Store


def build_parser() -> argparse.ArgumentParser:
    presets = ", ".join(f"{k}={label}" for k, (label, _) in PRESETS.items())
    p = argparse.ArgumentParser(
        prog="jpstock-screen",
        description="保存済みデータ (SQLite) から全銘柄をスクリーニングする (API は使わない)",
        epilog="条件の例: per<=15  roe>=8%%  turnover>=1億  market=プライム  /  指標一覧は --list-metrics",
    )
    p.add_argument("conditions", nargs="*", help="抽出条件 (複数指定は AND)")
    p.add_argument("--preset", action="append", choices=list(PRESETS), help=f"条件のセット ({presets})")
    p.add_argument("--market", action="append", help="市場で絞り込む (部分一致。例: プライム)")
    p.add_argument("--min-turnover", default="5000万", help="売買代金 (20日平均) の下限。0 で無効 (既定: 5000万)")
    p.add_argument("--sort", default="score", help="並べ替える指標 (既定: score=総合評価)")
    p.add_argument("--asc", action="store_true", help="昇順に並べる (既定は降順)")
    p.add_argument("--limit", type=int, default=50, help="表示件数 (既定: 50、0 で全件)")
    p.add_argument("--columns", help=f"表示する列 (カンマ区切り。既定: {','.join(DEFAULT_COLUMNS)})")
    p.add_argument("--as-of", metavar="YYYY-MM-DD", help="その日までに得られたデータだけでスクリーニングする")
    p.add_argument("--include-others", action="store_true", help="業種「その他」(ETF・REIT 等) も含める")
    p.add_argument("--csv", type=Path, metavar="FILE", help="結果を CSV に保存する (全列)")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"DB ファイル (既定: {DEFAULT_DB})")
    p.add_argument("--plan", choices=list(PLANS), help="契約プラン (表示の注記に使う)")
    p.add_argument("--list-metrics", action="store_true", help="条件・並べ替えに使える指標の一覧を表示")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_metrics:
        for key, m in METRICS.items():
            unit = {"ratio": "% または小数", "times": "倍", "oku": "円 (億・万の単位可)", "yen": "円"}.get(m.kind, "")
            print(f"  {key:13} {m.label}  {unit}")
        for key, (label, conds) in PRESETS.items():
            print(f"  --preset {key:9} {label}: {' '.join(conds)}")
        return 0

    texts = list(args.conditions)
    for name in args.preset or []:
        texts += PRESETS[name][1]
    if args.min_turnover not in ("0", ""):
        texts.append(f"turnover>={args.min_turnover}")
    try:
        conditions = [parse_condition(t) for t in texts]
        columns = [c.strip() for c in args.columns.split(",")] if args.columns else list(DEFAULT_COLUMNS)
        unknown = [c for c in columns + [args.sort] if c not in METRICS]
        if unknown:
            raise ScreeningError(f"未知の指標です: {', '.join(unknown)} (--list-metrics で一覧)")
    except ScreeningError as e:
        print(f"入力エラー: {e}", file=sys.stderr)
        return 1

    if not args.db.is_file():
        print(f"DB が見つかりません: {args.db}。先に jpstock-data sync で全銘柄を取得してください", file=sys.stderr)
        return 1

    def progress(i: int, n: int) -> None:
        if i % 200 == 0 or i == n:
            print(f"\r分析中 {i}/{n}", end="" if i < n else "\n", file=sys.stderr, flush=True)

    plan = load_settings(args.plan).plan
    with Store(args.db) as store:
        result = screen(
            store, conditions, plan=plan.name, as_of=args.as_of, markets=args.market, sort=args.sort,
            descending=not args.asc, limit=args.limit or None, include_others=args.include_others,
            progress=progress,
        )
    if result.listing_date is None:
        print("銘柄一覧がありません。jpstock-data sync を実行してください", file=sys.stderr)
        return 1

    print(f"条件: {' かつ '.join(c.text for c in conditions) or '(なし)'}")
    print(
        f"対象 {result.universe_size} 銘柄 (銘柄一覧 {result.listing_date}) のうち分析可能 {result.analyzed} 銘柄"
        f" → 該当 {result.matched} 銘柄 (並べ替え: {METRICS[args.sort].label}{'昇順' if args.asc else '降順'})"
    )
    if result.rows:
        headers = [METRICS[c].label for c in columns]
        body = [[format_value(c, row[c]) for c in columns] for row in result.rows]
        print(table(headers, body))
        if result.matched > len(result.rows):
            print(f"(上位 {len(result.rows)} 件を表示。--limit 0 で全件)")
    for n in result.notes:
        print(f"※ {n}")
    if plan.delay_weeks:
        print(f"※ {plan.label}プランのため株価・財務は約{plan.delay_weeks}週間遅れのデータです")
    print("※ 割安・収益・成長・安全は5段階、総合は100点満点の機械的な評価です")

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8-sig") as f:  # Excel で開けるよう BOM 付き
            w = csv.writer(f)
            w.writerow([METRICS[k].label for k in METRICS])
            for row in result.rows:
                w.writerow(["" if row[k] is None else row[k] for k in METRICS])
        print(f"(CSV に保存しました: {args.csv}。J-Quants のデータを含むため公開しないでください)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
