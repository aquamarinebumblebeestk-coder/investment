"""コマンドライン入口: python -m jpstock "7203,6758" """

from __future__ import annotations

import argparse
import sys

from . import __version__
from .analyzer import Analyzer
from .client import JQuantsClient
from .codes import InvalidCodeError, parse_codes
from .config import load_settings
from .plans import PLANS
from .preflight import PreflightError, run_preflight
from .report import SECTIONS, render

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_PREFLIGHT = 2
EXIT_PARTIAL = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jpstock", description="J-Quants API を使った日本株の銘柄分析ツール")
    p.add_argument("codes", nargs="?", help='銘柄コード。複数はカンマ区切り (例: "7203,6758")。省略時は対話入力')
    p.add_argument("--plan", choices=list(PLANS), help="J-Quants の契約プラン (既定: 環境変数 JQUANTS_PLAN か free)")
    p.add_argument("--days", type=int, default=365, help="株価を取得する日数 (既定: 365)")
    p.add_argument(
        "--sections",
        default=",".join(SECTIONS),
        help=f"表示する内容をカンマ区切りで指定 ({'/'.join(SECTIONS)}。既定: すべて)。"
        " basic=株価・指標・配当・業績, financial=財務分析, technical=テクニカル分析",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sections = tuple(x.strip() for x in args.sections.split(",") if x.strip())
    unknown = [x for x in sections if x not in SECTIONS]
    if unknown or not sections:
        parser.error(f"--sections に不明な値があります: {', '.join(unknown) or '(空)'}")
    settings = load_settings(args.plan)
    try:
        run_preflight(settings)
    except PreflightError as e:
        print("警告: J-Quants API からデータを取得できない状態のため、処理を中断します。", file=sys.stderr)
        for issue in e.issues:
            print(f"  - {issue.reason}\n    → {issue.hint}", file=sys.stderr)
        return EXIT_PREFLIGHT

    # 取得できない環境で対話入力を待たないよう、プリフライト後に入力を受け付ける
    text = args.codes if args.codes is not None else input("銘柄コード (カンマ区切り): ")
    try:
        codes = parse_codes(text)
    except InvalidCodeError as e:
        print(f"入力エラー: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    plan = settings.plan
    calls = len(codes) * (3 + len(plan.features))
    if calls > plan.requests_per_minute:
        print(
            f"({plan.label}プランのレート制限 {plan.requests_per_minute}回/分 に合わせて取得するため、"
            f"約{calls // plan.requests_per_minute}分以上かかります)",
            file=sys.stderr,
        )

    analyzer = Analyzer(JQuantsClient(settings.api_key, plan), price_days=args.days)
    failed = 0
    for i, code in enumerate(codes):
        report = analyzer.analyze(code)
        failed += report.error is not None
        print(("\n" if i else "") + render(report, sections), flush=True)
    return EXIT_PARTIAL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
