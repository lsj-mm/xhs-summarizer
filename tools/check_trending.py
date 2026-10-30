# -*- coding: utf-8 -*-
"""GitHub 趋势工具的离线测试：不联网、不调用模型。

    python tools/check_trending.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import trending  # noqa: E402

SAMPLE = """
<article class="Box-row">
  <h2><a href="/acme/alpha">acme / alpha</a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A tool for building agents.</p>
  <span itemprop="programmingLanguage">Python</span>
  <a href="/acme/alpha/stargazers">1,234</a>
  <span>12 stars today</span>
</article>
<article class="Box-row">
  <h2><a href="/acme/beta">acme / beta</a></h2>
  <span itemprop="programmingLanguage">TypeScript</span>
  <a href="/acme/beta/stargazers">98</a>
  <span>7 stars today</span>
</article>
"""

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    items = trending.parse_trending_html(SAMPLE, since="daily", limit=10)
    check(len(items) == 2, "能解析 Trending 项目", str(len(items)))
    check(items[0]["full_name"] == "acme/alpha"
          and items[0]["url"] == "https://github.com/acme/alpha",
          "仓库全名和链接正确", items[0]["url"])
    check(items[0]["description"] == "A tool for building agents.",
          "能解析项目描述")
    check(items[0]["language"] == "Python" and items[0]["stars"] == "1,234",
          "能解析语言和 Star 数")
    check(items[0]["stars_today"] == "12" and items[0]["stars_today_num"] == 12,
          "能解析今日新增 Star")
    check(trending._parse_int("2,345") == 2345, "数字清理正确")
    check(trending.parse_trending_html(SAMPLE, "weekly", limit=1)[0]["stars_today_label"] == "本周",
          "周榜标签正确")
    check(trending.parse_trending_html(SAMPLE, "monthly", limit=1)[0]["stars_today_label"] == "本月",
          "月榜标签正确")
    check(any(value == "python" for value, _ in trending.LANGUAGES),
          "语言筛选项包含 Python")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
