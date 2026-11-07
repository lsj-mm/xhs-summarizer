# -*- coding: utf-8 -*-
"""外部工具收藏的离线测试：不联网、不碰真实 work 目录。

    python tools/check_toolshelf.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import toolshelf  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


def expects_error(func, label: str, needle: str = "") -> None:
    try:
        func()
    except toolshelf.ShelfError as exc:
        ok = needle in str(exc) if needle else True
        check(ok, label, "" if ok else str(exc))
    except Exception as exc:  # noqa: BLE001
        check(False, label, f"抛了别的异常：{exc}")
    else:
        check(False, label, "没有报错")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    # 每个测试用自己的临时 workdir，绝不写真实的 work/toolshelf/items.json
    with tempfile.TemporaryDirectory(prefix="shelf-test-") as tmp:
        config = {"workdir": tmp}

        empty = toolshelf.list_items(config)
        check(empty["total"] == 0 and empty["items"] == [], "初始收藏为空")
        check(not toolshelf._path(config).exists(), "只查询不会创建数据文件")

        item = toolshelf.add_item(
            "  gstack ",
            "  https://github.com/garrytan/gstack  ",
            "  Garry Tan 的 Claude Code 配置集，23 个角色化工具  ",
            "Claude Code, 工作流",
            config,
        )
        check(item["name"] == "gstack" and item["url"] == "https://github.com/garrytan/gstack",
              "添加时会去掉首尾空白", item["name"])
        check(item["tags"] == ["Claude Code", "工作流"], "标签按逗号拆分，空格保留在标签内",
              ", ".join(item["tags"]))
        check(len(item["id"]) == 8 and item["added_at"], "自动生成 id 和添加时间")
        check(toolshelf._path(config).is_file(), "数据落到 workdir/toolshelf/items.json")

        stored = toolshelf.load_store(config)
        check(len(stored["items"]) == 1 and stored["version"] == toolshelf.STATE_VERSION,
              "写入后能重新读出来", f"version={stored['version']}")

        # 同一链接（大小写、结尾斜杠不同）不允许重复收藏
        expects_error(
            lambda: toolshelf.add_item("重复", "https://github.com/GarryTan/gstack/", "", "", config),
            "重复链接被拒绝", "已经在收藏里了",
        )
        check(len(toolshelf.list_items(config)["items"]) == 1, "被拒绝的重复项没有写进去")

        expects_error(lambda: toolshelf.add_item("", "https://example.com/a", "", "", config),
                      "名称为空被拒绝", "工具名称")
        expects_error(lambda: toolshelf.add_item("x", "", "", "", config),
                      "链接为空被拒绝", "链接")
        expects_error(lambda: toolshelf.add_item("x", "javascript:alert(1)", "", "", config),
                      "非 http(s) 链接被拒绝", "http")
        expects_error(lambda: toolshelf.add_item("x", "https://", "", "", config),
                      "没有主机的链接被拒绝")
        expects_error(lambda: toolshelf.add_item("x", "ftp://example.com/f", "", "", config),
                      "ftp 链接被拒绝", "http")
        expects_error(lambda: toolshelf.add_item("x" * 81, "https://example.com/b", "", "", config),
                      "名称超长被拒绝", "太长了")
        expects_error(lambda: toolshelf.add_item("x", "https://example.com/c", "y" * 301, "", config),
                      "作用超长被拒绝", "太长了")
        expects_error(
            lambda: toolshelf.add_item("x", "https://example.com/d", "", "一,二,三,四,五,六,七,八,九", config),
            "标签超过 8 个被拒绝", "最多 8 个",
        )
        expects_error(
            lambda: toolshelf.add_item("x", "https://example.com/e", "", "标签" * 11, config),
            "单个标签超长被拒绝", "太长",
        )

        second = toolshelf.add_item("第二条", "https://example.com/two", "用来测排序和搜索", ["工具"], config)
        listing = toolshelf.list_items(config)
        check([entry["name"] for entry in listing["items"]] == ["第二条", "gstack"],
              "新收藏排在最前面")
        check(listing["total"] == 2 and listing["count"] == 2, "总数与条数一致")
        check(sorted(listing["tags"]) == sorted(["Claude Code", "工作流", "工具"]),
              "标签汇总包含所有已用标签", ",".join(listing["tags"]))

        found = toolshelf.list_items(config, "claude")
        check(len(found["items"]) == 1 and found["items"][0]["name"] == "gstack",
              "关键词能搜到标签和名称")
        check(toolshelf.list_items(config, "排序")["items"][0]["name"] == "第二条",
              "关键词能搜到作用")
        check(toolshelf.list_items(config, "garrytan/gstack")["count"] == 1,
              "关键词能搜到链接")
        check(toolshelf.list_items(config, "不存在的词")["count"] == 0, "搜不到时返回 0 条")

        updated = toolshelf.update_item(second["id"], {"purpose": "改过的作用", "tags": "新标签"}, config)
        check(updated["purpose"] == "改过的作用" and updated["tags"] == ["新标签"], "可以改作用和标签")
        check(updated["name"] == "第二条" and updated["added_at"] == second["added_at"],
              "没传的字段保持不变")

        expects_error(lambda: toolshelf.update_item(second["id"], {"url": "https://github.com/garrytan/gstack"}, config),
                      "改成已有链接被拒绝", "已经在收藏里了")
        expects_error(lambda: toolshelf.update_item(second["id"], {"name": "   "}, config),
                      "名称不能改成空", "不能为空")
        expects_error(lambda: toolshelf.update_item("不存在的id", {"name": "x"}, config),
                      "改不存在的收藏会报错", "没有这一条")

        deleted = toolshelf.delete_item(second["id"], config)
        check(deleted["name"] == "第二条", "删除会返回被删的那条")
        check(toolshelf.list_items(config)["total"] == 1, "删除后总数减一")
        expects_error(lambda: toolshelf.delete_item(second["id"], config),
                      "重复删除会报错", "没有这一条")

        # 数据文件坏了不应该让页面崩，按空库处理
        toolshelf._path(config).write_text("{ 不是 json", encoding="utf-8")
        broken = toolshelf.list_items(config)
        check(broken["total"] == 0, "数据文件损坏时按空库处理，不抛异常")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
