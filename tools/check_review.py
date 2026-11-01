# -*- coding: utf-8 -*-
"""代码审查工具的离线测试：不启动 OCR、不调用模型。

    python tools/check_review.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import review  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    # 二进制是 55 MB 的第三方发布包，不随仓库走。没装就跳过这两项，
    # 让这个离线测试在任意机器/CI 上都能跑通；装了才验证完整性。
    try:
        ocr = review.find_ocr()
    except review.ReviewError:
        ocr = ""
        print("  - 没找到 ocr 可执行文件，跳过二进制检查；"
              "运行 python bootstrap_deps.py 可自动下载")
    if ocr:
        check(Path(ocr).is_file(), "能找到 ocr.exe", ocr)
        check(Path(ocr).stat().st_size > 10_000_000, "ocr.exe 文件完整",
              f"{Path(ocr).stat().st_size // 1024 // 1024} MB")

    # 自动安装逻辑的纯函数：映射错了就会下错包，所以单独盯住
    import bootstrap_deps
    check(bootstrap_deps.ocr_asset("Windows", "AMD64")
          == ("opencodereview-windows-amd64.exe", "ocr.exe"),
          "Windows/AMD64 映射到正确的发布包")
    check(bootstrap_deps.ocr_asset("Linux", "x86_64")
          == ("opencodereview-linux-amd64", "ocr"),
          "Linux/x86_64 映射到不带后缀的包名")
    check(bootstrap_deps.ocr_asset("Darwin", "arm64")
          == ("opencodereview-darwin-arm64", "ocr"),
          "macOS/arm64 映射正确")
    try:
        bootstrap_deps.ocr_asset("Plan9", "mips")
        check(False, "不认识的平台应给出人工处理提示")
    except RuntimeError as exc:
        check("OCR_BIN" in str(exc), "不认识的平台提示用 OCR_BIN 手工指定")
    check(bootstrap_deps.parse_sha256sums(
        "abc123  opencodereview-linux-amd64\n"
        "def456  opencodereview-windows-amd64.exe\n", "opencodereview-windows-amd64.exe"
    ) == "def456", "能从官方 sha256sum.txt 里取出对应包的哈希")
    check(bootstrap_deps.parse_sha256sums("abc123  other-file", "missing") == "",
          "sha256sum.txt 里没有该包时返回空")
    check(bootstrap_deps.OCR_VERSION.startswith("v")
          and bootstrap_deps.OCR_MIN_BYTES > 10_000_000,
          "ocr 版本和最小体积有明确定义", bootstrap_deps.OCR_VERSION)

    cfg = {
        "summarizer": {
            "provider": "deepseek", "api_key": "sk-test",
            "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
        },
        "learning": {"provider": "inherit"},
    }
    model = review._review_model(cfg)
    check(model["ocr_url"] == "https://api.deepseek.com/v1",
          "DeepSeek Base URL 自动补 /v1", model["ocr_url"])
    check(model["model"] == "deepseek-chat" and model["api_key"] == "sk-test",
          "审查使用默认模型和 key")

    data = {
        "status": "success",
        "summary": {"files_reviewed": 1, "comments": 1, "total_tokens": 100, "elapsed": "2s"},
        "comments": [{
            "path": "xhs/config.py", "start_line": 10, "end_line": 11,
            "severity": "high", "category": "security",
            "content": "不要在这里泄露 key。", "existing_code": "key = 'x'",
            "suggestion_code": "key = ''",
        }],
        "project_summary": "## Top Issues\n\n- 一个示例问题",
        "session_id": "s1",
    }
    md = review._render_markdown(data, {
        "project": "demo", "mode_name": "全量扫描",
        "model": "DeepSeek / deepseek-chat", "created_at": "2026-01-01",
    })
    check("# 代码审查报告" in md and "xhs/config.py:10-11" in md, "报告包含标题和行号")
    check("不要在这里泄露 key" in md and "建议代码" in md, "报告包含问题和建议")
    check("项目总结" in md and "Top Issues" in md, "报告包含项目总结")
    check("需要人工核查" in md and "[high]" in md, "报告列出需要人工核查的高危项")

    human = review._human_review(data["comments"])
    check(len(human) == 1 and human[0]["severity"] == "high",
          "人工核查列表会提取高危评论")

    empty = {**data, "comments": [], "summary": {"files_reviewed": 1, "comments": 0}}
    md2 = review._render_markdown(empty, {
        "project": "demo", "mode_name": "全量扫描",
        "model": "m", "created_at": "now",
    })
    check("未发现明显问题" in md2, "无问题报告有明确说明")

    try:
        review.report_json_path("../etc/passwd")
        check(False, "报告 id 路径穿越应被拒绝")
    except review.ReviewError:
        check(True, "报告 id 路径穿越被拒绝")

    local_cfg = {
        "summarizer": {"provider": "deepseek", "api_key": "sk", "model": "deepseek-chat"},
        "codereview": {"provider": "local", "model": "qwen2.5:7b",
                       "base_url": "http://127.0.0.1:11434/v1"},
    }
    local = review._review_model(local_cfg)
    check(local["key_optional"] and local["ocr_url"].startswith("http://127.0.0.1:11434"),
          "代码审查可以单独选择本地模型")
    view = review.settings_view(local_cfg)
    check("api_key" not in view and view["effective"]["model"] == "qwen2.5:7b",
          "代码审查模型设置不泄露 key")
    check(any(item["value"] == "local" for item in view["options"]),
          "代码审查模型设置包含本地模型选项")
    masked_cfg = review.config_mod.deep_merge(
        review.config_mod.DEFAULTS,
        {"codereview": {"provider": "custom", "api_key": "secret-review-key"}},
    )
    masked = review.config_mod.masked_view(masked_cfg)
    check(masked["codereview"]["api_key"] == ""
          and masked["codereview"]["api_key_set"],
          "配置视图会掩码 codereview.api_key")
    file_path = PROJECT_ROOT / "README.md"
    target, repo_root, single = review._target(str(file_path))
    check(target == file_path and repo_root == PROJECT_ROOT and single == "README.md",
          "单文件路径会拆成父目录 + 文件名")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
