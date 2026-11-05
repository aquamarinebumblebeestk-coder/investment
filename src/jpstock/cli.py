"""コマンドライン入口: python -m jpstock "7203,6758" """

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .analyzer import Analyzer, StockReport, build_report
from .client import JQuantsClient
from .codes import InvalidCodeError, parse_codes
from .config import load_settings
from .plans import PLANS
from .preflight import PreflightError, run_preflight
from .report import SECTIONS, render, render_comparison
from .snapshot import DEFAULT_DIR, latest_snapshots, load_snapshot, save_snapshot

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_PREFLIGHT = 2
EXIT_PARTIAL = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jpstock", description="J-Quants API を使った日本株の銘柄分析ツール")
    p.add_argument(
        "codes",
        nargs="?",
        help='銘柄コード。複数はカンマ区切り (例: "7203,6758")。省略時は対話入力 (--from-saved では保存済み全銘柄)',
    )
    p.add_argument("--plan", choices=list(PLANS), help="J-Quants の契約プラン (既定: 環境変数 JQUANTS_PLAN か free)")
    p.add_argument("--days", type=int, default=365, help="株価を取得する日数 (既定: 365)")
    p.add_argument(
        "--sections",
        default=",".join(SECTIONS),
        help=f"表示する内容をカンマ区切りで指定 ({'/'.join(SECTIONS)}。既定: すべて)。"
        " basic=株価・指標・配当・業績, financial=財務分析, technical=テクニカル分析, summary=総合評価",
    )
    p.add_argument(
        "--save",
        nargs="?",
        const=DEFAULT_DIR,
        type=Path,
        metavar="DIR",
        help=f"取得結果を JSON で保存する (既定の保存先: {DEFAULT_DIR})",
    )
    p.add_argument(
        "--from-saved",
        nargs="?",
        const=DEFAULT_DIR,
        type=Path,
        metavar="DIR",
        help=f"API を使わず保存済みの取得結果 (銘柄ごとに最新) から分析する (既定: {DEFAULT_DIR})",
    )
    p.add_argument("--no-compare", action="store_true", help="複数銘柄のときの比較表を表示しない")
    p.add_argument("--ai", action="store_true", help="Claude による分析レポートを生成する (要: anthropic パッケージと API キー)")
    p.add_argument("--ai-model", help="--ai で使う Claude のモデル (既定: 環境変数 JPSTOCK_AI_MODEL か claude-opus-5-5)")
    p.add_argument("--ai-out", type=Path, metavar="FILE", help="--ai のレポートを Markdown ファイルにも保存する")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _parse_sections(parser: argparse.ArgumentParser, text: str) -> tuple[str, ...]:
    sections = tuple(x.strip() for x in text.split(",") if x.strip())
    unknown = [x for x in sections if x not in SECTIONS]
    if unknown or not sections:
        parser.error(f"--sections に不明な値があります: {', '.join(unknown) or '(空)'}")
    return sections


def _reports_from_saved(directory: Path, codes: list[str] | None) -> list[StockReport] | None:
    if not directory.is_dir():
        print(f"保存先が見つかりません: {directory}", file=sys.stderr)
        return None
    paths = latest_snapshots(directory, codes)
    missing = [c for c in codes or [] if c not in paths]
    if missing:
        print(f"保存データがない銘柄: {', '.join(missing)}", file=sys.stderr)
    if not paths:
        print(f"{directory} に保存データがありません。先に --save 付きで取得してください", file=sys.stderr)
        return None
    reports = []
    for code, path in paths.items():
        try:
            reports.append(build_report(load_snapshot(path)))
        except (OSError, ValueError, TypeError) as e:
            reports.append(StockReport(code=code, error=f"保存データを読めません ({path}): {e}"))
    return reports


def _reports_from_api(args: argparse.Namespace) -> tuple[list[StockReport] | None, int]:
    settings = load_settings(args.plan)
    try:
        run_preflight(settings)
    except PreflightError as e:
        print("警告: J-Quants API からデータを取得できない状態のため、処理を中断します。", file=sys.stderr)
        for issue in e.issues:
            print(f"  - {issue.reason}\n    → {issue.hint}", file=sys.stderr)
        print("  (保存済みデータがあれば --from-saved で API なしに分析できます)", file=sys.stderr)
        return None, EXIT_PREFLIGHT

    # 取得できない環境で対話入力を待たないよう、プリフライト後に入力を受け付ける
    text = args.codes if args.codes is not None else input("銘柄コード (カンマ区切り): ")
    try:
        codes = parse_codes(text)
    except InvalidCodeError as e:
        print(f"入力エラー: {e}", file=sys.stderr)
        return None, EXIT_INPUT_ERROR

    plan = settings.plan
    calls = len(codes) * (3 + len(plan.features))
    if calls > plan.requests_per_minute:
        print(
            f"({plan.label}プランのレート制限 {plan.requests_per_minute}回/分 に合わせて取得するため、"
            f"約{calls // plan.requests_per_minute}分以上かかります)",
            file=sys.stderr,
        )

    analyzer = Analyzer(JQuantsClient(settings.api_key, plan), price_days=args.days)
    reports = []
    for code in codes:
        report, snap = analyzer.analyze(code)
        if snap is not None and args.save is not None:
            path = save_snapshot(snap, args.save)
            print(f"(取得結果を保存しました: {path})", file=sys.stderr)
        reports.append(report)
    return reports, EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sections = _parse_sections(parser, args.sections)

    if args.from_saved is not None:
        try:
            codes = parse_codes(args.codes) if args.codes else None
        except InvalidCodeError as e:
            print(f"入力エラー: {e}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        reports = _reports_from_saved(args.from_saved, codes)
        if reports is None:
            return EXIT_INPUT_ERROR
    else:
        reports, code = _reports_from_api(args)
        if reports is None:
            return code

    for i, report in enumerate(reports):
        print(("\n" if i else "") + render(report, sections), flush=True)
    if not args.no_compare:
        comparison = render_comparison(reports)
        if comparison:
            print("\n" + comparison, flush=True)

    failed = any(r.error is not None for r in reports)
    if args.ai:
        from .ai import AIAnalysisError, generate_analysis

        print("\n" + "=" * 72 + "\nClaude による分析\n" + "=" * 72, flush=True)
        try:
            text = generate_analysis(reports, model=args.ai_model)
        except AIAnalysisError as e:
            print(f"AI分析をスキップしました: {e}", file=sys.stderr)
            failed = True
        else:
            if args.ai_out:
                args.ai_out.parent.mkdir(parents=True, exist_ok=True)
                args.ai_out.write_text(text, encoding="utf-8")
                print(f"(AI分析を保存しました: {args.ai_out})", file=sys.stderr)
    return EXIT_PARTIAL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
