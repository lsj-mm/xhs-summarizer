# -*- coding: utf-8 -*-
"""学习路径与考核的离线测试：不调用 AI，验证计划校验、判分和 3 次机会状态。

    python tools/check_learning.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import learning  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


def sample_plan() -> dict:
    return learning._normalize_plan({
        "title": "测试计划",
        "goal": "能独立完成一个项目",
        "stages": [
            {
                "name": "基础",
                "weeks": 2,
                "objective": "掌握基础概念",
                "content": ["概念 A", "练习 B"],
                "resources": [
                    {"title": "官方文档", "type": "官方文档", "query": "Python 官方文档", "why": "权威"}
                ],
                "assessment": {"pass_score": 60, "focus": ["概念 A"], "project": "小练习"},
            },
            {
                "name": "进阶",
                "weeks": 3,
                "objective": "完成综合项目",
                "content": ["项目实战"],
                "resources": [{"title": "项目搜索", "type": "项目", "query": "Python 项目"}],
                "assessment": {"focus": ["项目"], "project": "完整项目"},
            },
        ],
    })


def sample_questions() -> list[dict]:
    return [
        {"question": f"题目 {i}", "options": ["A", "B", "C", "D"],
         "answer": i % 4, "explanation": "解析"}
        for i in range(10)
    ]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    plan = sample_plan()
    check(plan["title"] == "测试计划" and len(plan["stages"]) == 2, "计划结构解析")
    check(plan["stages"][0]["assessment"]["pass_score"] == 80,
          "通过分数最低锁定为 80")
    check(plan["stages"][0]["resources"][0]["url"].startswith("https://"),
          "资源生成可点击搜索入口")
    check("github.com" in plan["stages"][1]["resources"][0]["url"],
          "项目资源走 GitHub 搜索")
    check("bing.com" in plan["stages"][0]["resources"][0]["url"]
          and "douban.com" not in plan["stages"][0]["resources"][0]["url"],
          "教材/文档资源走 Bing 搜索入口，避免空白的豆瓣页")

    questions = sample_questions()
    result = learning.grade_answers(questions, [q["answer"] for q in questions])
    check(result["score"] == 100 and result["correct"] == 10, "全对判 100 分")
    wrong = [0 if q["answer"] != 0 else 1 for q in questions]
    result2 = learning.grade_answers(questions, wrong)
    check(result2["score"] == 0, "全错判 0 分")

    record = {
        "id": "test", "created_at": "2026-01-01", "topic": "测试", "goal": "目标",
        "current_level": "零基础", "weekly_hours": 6, "weeks": 8,
        "plan": plan,
        "stages": [
            {"status": "learning", "attempts": 0, "score": None, "exam": None, "history": []},
            {"status": "locked", "attempts": 0, "score": None, "exam": None, "history": []},
        ],
    }
    check(learning._apply_attempt(record, 0, 70)["remaining"] == 2, "第一次失败剩 2 次")
    check(learning._apply_attempt(record, 0, 70)["remaining"] == 1, "第二次失败剩 1 次")
    check(learning._apply_attempt(record, 0, 70)["status"] == "failed", "第三次失败作废")
    check(record["stages"][1]["status"] == "locked", "失败时下一阶段保持锁定")

    passed_record = {
        **record,
        "stages": [
            {"status": "learning", "attempts": 0, "score": None, "exam": None, "history": []},
            {"status": "locked", "attempts": 0, "score": None, "exam": None, "history": []},
        ],
    }
    transition = learning._apply_attempt(passed_record, 0, 80)
    check(transition["passed"] and passed_record["stages"][1]["status"] == "learning",
          "通过后解锁下一阶段")

    record["stages"][0]["exam"] = {
        "created_at": "2026-01-01",
        "questions": questions,
    }
    public = learning.public_plan(record)
    check("answer" not in public["stages"][0]["exam"]["questions"][0],
          "公开计划不泄露正确答案")

    failed = [
        {"question": "题", "options": ["A", "B", "C", "D"], "answer": 0, "explanation": "x"}
    ] * 10
    check(len(learning._normalize_exam({"questions": failed})) == 10, "试卷需要正好 10 题")

    bad = [{"question": "题", "options": ["A", "B"], "answer": 0, "explanation": "x"}] * 10
    try:
        learning._normalize_exam({"questions": bad})
        check(False, "选项不足时应报错")
    except learning.LearningError:
        check(True, "选项不足时明确报错")

    inherit_cfg = {
        "summarizer": {"provider": "deepseek", "model": "deepseek-chat", "api_key": "global-key"},
        "learning": {"provider": "inherit"},
    }
    check(learning.config_mod.effective_learning_model(inherit_cfg)["model"] == "deepseek-chat",
          "学习模型默认跟随工具箱总结模型")
    custom_cfg = {
        "summarizer": {"provider": "deepseek", "model": "deepseek-chat", "api_key": "global-key"},
        "learning": {"provider": "custom", "base_url": "https://example.com/v1",
                     "model": "custom-model", "api_key": "custom-key"},
    }
    custom = learning.config_mod.effective_learning_model(custom_cfg)
    check(custom["model"] == "custom-model" and custom["base_url"] == "https://example.com/v1",
          "学习工具可以单独指定厂商和模型")
    view = learning.settings_view(custom_cfg)
    check("api_key" not in view and view["effective"]["model"] == "custom-model",
          "模型设置视图不泄露明文 key")
    default_view = learning.config_mod.default_model_view(inherit_cfg)
    check(default_view["provider"] == "deepseek"
          and default_view["effective"]["api_key_set"]
          and "api_key" not in default_view,
          "工具箱默认模型视图可读且不泄露 key")
    masked_cfg = learning.config_mod.deep_merge(
        learning.config_mod.DEFAULTS,
        {"learning": {"provider": "custom", "api_key": "secret-learning-key"}},
    )
    masked = learning.config_mod.masked_view(masked_cfg)
    check(masked["learning"]["api_key"] == ""
          and masked["learning"]["api_key_set"],
          "配置视图会掩码 learning.api_key")

    failed_count = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed_count += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed_count}/{len(results)} 项")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
