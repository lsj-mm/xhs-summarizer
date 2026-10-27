# -*- coding: utf-8 -*-
"""命令行入口：对任意项目跑 OpenCodeReview，并生成 Markdown 报告。

    python tools/review_project.py --project E:\\path\\to\\project
    python tools/review_project.py --project . --mode scan --paths xhs,static
    python tools/review_project.py --project . --out review.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import review  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCodeReview 本地代码审查")
    parser.add_argument("--project", default=".", help="要审查的项目目录")
    parser.add_argument("--mode", choices=("scan", "review"), default="scan",
                        help="scan=全量扫描，review=Git 变更审查")
    parser.add_argument("--paths", default="", help="只审查这些相对路径，逗号分隔")
    parser.add_argument("--exclude", default="", help="排除路径，逗号分隔")
    parser.add_argument("--max-tokens", type=int, default=0, help="总 token 预算，0 不限")
    parser.add_argument("--out", default="", help="Markdown 报告输出路径；默认项目目录下 code-review-report.md")
    args = parser.parse_args()

    try:
        result = review.run(
            args.project, args.mode, args.paths, args.exclude, args.max_tokens
        )
    except review.ReviewError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    out = Path(args.out).expanduser() if args.out else (Path(args.project) / "code-review-report.md")
    out = out.resolve()
    out.write_text(result["markdown"], encoding="utf-8")
    summary = result.get("summary") or {}
    print(f"审查完成：{summary.get('files_reviewed', '—')} 个文件，"
          f"{summary.get('comments', len(result.get('comments') or []))} 个问题，"
          f"耗时 {result.get('elapsed')}s")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
