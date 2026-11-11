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
    check(trending.LIMIT == 30, "榜单条数为 30", str(trending.LIMIT))

    # 搜索补充：把 API 结果转成和 Trending 一致的结构
    payload = {"items": [
        {"full_name": "new/hot", "html_url": "https://github.com/new/hot",
         "description": "A brand new agent framework.", "language": "Python",
         "stargazers_count": 12345, "created_at": "2026-11-05T00:00:00Z"},
        {"full_name": "old/dup", "html_url": "https://github.com/old/dup",
         "description": "already in trending", "language": "Go", "stargazers_count": 99},
        {"full_name": "", "html_url": "", "stargazers_count": 1},
    ]}
    extra = trending.parse_search_items(payload, start_rank=3, exclude={"old/dup"})
    check(len(extra) == 1, "搜索补充会跳过重复和空仓库名", str(len(extra)))
    check(extra[0]["source"] == "search" and extra[0]["rank"] == 3,
          "补充条目标记来源并按起始名次编号")
    check(extra[0]["stars"] == "12,345" and extra[0]["name"] == "new / hot",
          "补充条目带上 Star 数和可读名称", extra[0]["stars"])
    check(extra[0]["stars_today"] == "" and extra[0]["stars_today_num"] == 0,
          "补充条目没有「今日新增」数据时不编造")
    check(trending.parse_search_items({}, 1) == [], "搜索返回异常结构时返回空列表")

    # 合并：主榜在前、补位在后、按全名去重、名次连续
    primary = [{"full_name": "a/one", "source": "trending", "rank": 1},
               {"full_name": "b/two", "source": "trending", "rank": 2}]
    extra_items = [{"full_name": "B/Two", "source": "search"},
                   {"full_name": "c/three", "source": "search"}]
    merged = trending.merge_items(primary, extra_items, limit=30)
    check([row["full_name"] for row in merged] == ["a/one", "b/two", "c/three"],
          "合并时按仓库全名去重（忽略大小写）", ",".join(r["full_name"] for r in merged))
    check([row["rank"] for row in merged] == [1, 2, 3], "合并后名次连续",
          str([row["rank"] for row in merged]))
    check(len(trending.merge_items([{"full_name": f"x/r{i}"} for i in range(40)], [], limit=30)) == 30,
          "超过上限时只保留 30 条")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
