# -*- coding: utf-8 -*-
"""学习路径规划与阶段考核。

规则：
    - 每个阶段 3 次考核机会，80 分通过；
    - 3 次未通过，该阶段作废，必须点「重新学习」把次数清零后才能再考；
    - 前一阶段通过后，下一阶段才会解锁；
    - 计划和进度保存在 work/learning/plans.json。

AI 负责拆解路径和出题，资源只返回搜索入口（官方文档/教材/项目/视频），
不编造具体课程或链接；学习计划和考核结果只作学习管理参考。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from . import config as config_mod
from . import summarize as summarize_mod

LOG = logging.getLogger(__name__)

STATE_VERSION = 1
MAX_PLANS = 30
PASS_SCORE = 80
ATTEMPTS = 3
MAX_STAGES = 8
MAX_CONTENT = 10
MAX_RESOURCES = 8
EXAM_SIZE = 10


class LearningError(RuntimeError):
    """可直接展示给用户的错误。"""


def _path(config: dict) -> Path:
    return config_mod.workdir(config) / "learning" / "plans.json"


def load_store(config: dict | None = None) -> dict:
    config = config or config_mod.load()
    path = _path(config)
    if not path.is_file():
        return {"version": STATE_VERSION, "plans": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "plans": []}
    if not isinstance(data, dict) or not isinstance(data.get("plans"), list):
        return {"version": STATE_VERSION, "plans": []}
    return data


def _save(store: dict, config: dict) -> None:
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def settings_view(config: dict | None = None) -> dict:
    config = config or config_mod.load()
    section = dict(config.get("learning") or {})
    provider = (section.get("provider") or "inherit").strip() or "inherit"
    try:
        effective = config_mod.effective_learning_model(config)
        effective_info = {
            "label": effective.get("label", ""),
            "model": effective.get("model", ""),
            "base_url": effective.get("base_url", ""),
            "api_key_set": bool(effective.get("api_key")),
        }
        own_key_masked = config_mod.mask_key(section.get("api_key") or "")
    except Exception as exc:
        effective_info = {"error": str(exc), "api_key_set": False}
        own_key_masked = config_mod.mask_key(section.get("api_key") or "")
    options = [{"value": "inherit", "label": "跟随工具箱设置"}]
    options += [{"value": name, "label": preset["label"]}
                for name, preset in config_mod.SUMMARIZER_PRESETS.items()]
    return {
        "provider": provider,
        "model": section.get("model") or "",
        "base_url": section.get("base_url") or "",
        "api_key_masked": own_key_masked,
        "api_key_set": bool(section.get("api_key")),
        "effective": effective_info,
        "options": options,
    }


def save_settings(payload: dict, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    payload = payload or {}
    provider = str(payload.get("provider") or "inherit").strip() or "inherit"
    if provider not in ("inherit", *config_mod.SUMMARIZER_PRESETS.keys()):
        raise LearningError("未知的模型厂商")
    section = dict(config.get("learning") or {})
    old_provider = (section.get("provider") or "inherit").strip() or "inherit"
    model = str(payload.get("model") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    if provider == "custom" and (not model or not base_url):
        raise LearningError("自定义模型需要填写 Base URL 和模型名")
    if provider == "local" and not model:
        raise LearningError("本地模型需要填写模型名，例如 qwen2.5:7b")
    section["provider"] = provider
    section["base_url"] = base_url
    section["model"] = model
    key = str(payload.get("api_key") or "").strip()
    if key:
        section["api_key"] = key
    elif provider != old_provider:
        # 换厂商时旧 key 不能继续借用；留空则改用该厂商的环境变量/全局 key
        section["api_key"] = ""
    config["learning"] = section
    config_mod.save(config)
    return settings_view(config)


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise LearningError("模型没有返回 JSON 格式的计划，请重试")
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LearningError(f"学习计划 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise LearningError("学习计划格式不正确")
    return data


def _resource_url(kind: str, query: str) -> str:
    text = str(query or "").strip()
    if kind == "教材":
        return f"https://www.bing.com/search?q={quote_plus(text + ' 教材')}"
    if kind == "项目":
        return f"https://github.com/search?q={quote_plus(text)}"
    if kind == "视频":
        return f"https://www.bing.com/search?q={quote_plus(text + ' site:bilibili.com')}"
    if kind == "课程":
        return f"https://www.bing.com/search?q={quote_plus(text + ' 课程')}"
    if kind == "官方文档":
        return f"https://www.bing.com/search?q={quote_plus(text)}"
    return f"https://www.bing.com/search?q={quote_plus(text)}"


def _normalize_plan(data: dict) -> dict:
    title = str(data.get("title") or "").strip()
    goal = str(data.get("goal") or "").strip()
    stages_raw = data.get("stages")
    if not title or not goal:
        raise LearningError("模型返回的计划缺少标题或目标，请重试")
    if not isinstance(stages_raw, list) or not 2 <= len(stages_raw) <= MAX_STAGES:
        raise LearningError("计划阶段数应在 2~8 之间，请重试")

    stages = []
    for index, item in enumerate(stages_raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"阶段 {index + 1}").strip()
        objective = str(item.get("objective") or "").strip()
        content = [str(x).strip() for x in (item.get("content") or []) if str(x).strip()]
        if not objective or not content:
            continue
        resources = []
        for res in (item.get("resources") or [])[:MAX_RESOURCES]:
            if not isinstance(res, dict):
                continue
            r_title = str(res.get("title") or "").strip()
            r_query = str(res.get("query") or r_title).strip()
            if not r_title or not r_query:
                continue
            kind = str(res.get("type") or "搜索").strip()
            if kind not in ("官方文档", "教材", "课程", "项目", "视频", "搜索"):
                kind = "搜索"
            resources.append({
                "title": r_title,
                "type": kind,
                "query": r_query,
                "why": str(res.get("why") or "").strip(),
                "url": _resource_url(kind, r_query),
            })
        assessment = item.get("assessment") or {}
        pass_score = assessment.get("pass_score", PASS_SCORE)
        try:
            pass_score = int(pass_score)
        except (TypeError, ValueError):
            pass_score = PASS_SCORE
        pass_score = max(PASS_SCORE, min(100, pass_score))
        stages.append({
            "name": name[:80],
            "weeks": max(1, min(12, int(item.get("weeks") or 1))),
            "objective": objective[:500],
            "content": content[:MAX_CONTENT],
            "resources": resources,
            "assessment": {
                "pass_score": pass_score,
                "focus": [str(x).strip() for x in (assessment.get("focus") or []) if str(x).strip()][:8],
                "project": str(assessment.get("project") or "").strip()[:500],
            },
        })
    if len(stages) < 2:
        raise LearningError("有效阶段不足 2 个，请重试")
    return {"title": title[:100], "goal": goal[:1000], "stages": stages}


def _system_prompt() -> str:
    return (
        "你是一名严谨的学习路径规划导师。只输出 JSON，不要输出 Markdown 或解释。"
        "从学习者的当前基础出发，按最近发展区把目标拆成 2~8 个递进阶段。"
        "每个阶段必须有可衡量的目标、具体学习内容、可检索的资源名称和考核重点。"
        "资源不要编造课程、书或网址：只给资源名称和搜索词，链接由程序生成搜索入口。"
        "考核严格：每阶段 3 次机会，80 分通过，3 次不过该阶段作废重学。"
    )


def generate_plan(topic: str, goal: str, current_level: str = "零基础",
                  weekly_hours: int = 6, weeks: int = 12,
                  config: dict | None = None) -> dict:
    config = config or config_mod.load()
    topic = str(topic or "").strip()
    goal = str(goal or "").strip()
    current_level = str(current_level or "零基础").strip()
    if not topic:
        raise LearningError("请填写学习主题")
    if not goal:
        raise LearningError("请填写目标水平")
    try:
        weekly_hours = max(1, min(40, int(weekly_hours)))
        weeks = max(2, min(52, int(weeks)))
    except (TypeError, ValueError) as exc:
        raise LearningError("每周小时和周期需要是整数") from exc

    try:
        cfg = config_mod.effective_learning_model(config)
    except RuntimeError as exc:
        raise LearningError(str(exc)) from exc
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        raise LearningError("还没有配置总结模型 API key，请先在工具箱首页设置里填写")

    prompt = f"""请为下面的学习需求生成学习路径。

学习主题：{topic}
当前基础：{current_level}
目标水平：{goal}
每周可投入：{weekly_hours} 小时
计划周期：{weeks} 周

输出 JSON 结构：
{{
  "title": "计划标题",
  "goal": "对目标的可衡量描述",
  "stages": [
    {{
      "name": "阶段名称",
      "weeks": 2,
      "objective": "阶段结束时能做到什么（可衡量）",
      "content": ["知识点或练习，3~8 条"],
      "resources": [
        {{"title": "资源名称", "type": "官方文档|教材|课程|项目|视频|搜索", "query": "用于搜索的关键词", "why": "为什么适合这个阶段"}}
      ],
      "assessment": {{
        "pass_score": 80,
        "focus": ["考核重点，3~6 条"],
        "project": "一个可提交的实操作品或任务"
      }}
    }}
  ]
}}

要求：
1. 阶段从当前基础出发，最终出口必须覆盖目标水平；不要跳步。
2. 每个阶段 1~12 周，阶段总周数尽量接近 {weeks} 周。
3. 资源给真实存在的经典教材/官方文档/公开课程/项目方向，但不要写 URL，也不要编造具体课程编号；query 用 2~8 个可检索关键词，不要整段论文标题，教材/课程优先用中文通用名。
4. 考核重点要能出选择题，也要有实操作品；难度逐阶段提高。
5. 只输出 JSON。"""

    try:
        raw = summarize_mod._chat(
            [{"role": "system", "content": _system_prompt()},
             {"role": "user", "content": prompt}],
            cfg,
            json_mode=True,
        )
    except summarize_mod.SummaryError as exc:
        raise LearningError(str(exc)) from exc
    plan = _normalize_plan(_extract_json(raw))

    record = {
        "id": dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3),
        "created_at": _now(),
        "topic": topic[:100],
        "goal": goal[:1000],
        "current_level": current_level[:100],
        "weekly_hours": weekly_hours,
        "weeks": weeks,
        "plan": plan,
        "stages": [
            {
                "status": "learning" if i == 0 else "locked",
                "attempts": 0,
                "score": None,
                "exam": None,
                "history": [],
            }
            for i in range(len(plan["stages"]))
        ],
        "version": STATE_VERSION,
    }
    store = load_store(config)
    store["version"] = STATE_VERSION
    store["plans"].insert(0, record)
    store["plans"] = store["plans"][:MAX_PLANS]
    _save(store, config)
    return public_plan(record)


def _find(store: dict, plan_id: str) -> dict:
    record = next((p for p in store.get("plans", []) if p.get("id") == plan_id), None)
    if record is None:
        raise LearningError("找不到这份学习计划")
    return record


def _public_questions(exam: dict | None):
    if not exam:
        return None
    return {
        "created_at": exam.get("created_at"),
        "questions": [
            {"question": q.get("question"), "options": q.get("options")}
            for q in exam.get("questions", [])
        ],
    }


def public_plan(record: dict) -> dict:
    stages = []
    for i, (stage, state) in enumerate(zip(record["plan"]["stages"], record["stages"])):
        resources = [
            {**res, "url": _resource_url(
                str(res.get("type") or "搜索"),
                str(res.get("query") or res.get("title") or ""),
            )}
            for res in (stage.get("resources") or [])
        ]
        stages.append({
            **stage,
            "resources": resources,
            "index": i,
            "status": state.get("status", "locked"),
            "attempts": state.get("attempts", 0),
            "score": state.get("score"),
            "exam": _public_questions(state.get("exam")),
            "history": state.get("history", []),
        })
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "topic": record["topic"],
        "goal": record["goal"],
        "current_level": record["current_level"],
        "weekly_hours": record["weekly_hours"],
        "weeks": record["weeks"],
        "plan": {"title": record["plan"]["title"], "goal": record["plan"]["goal"]},
        "stages": stages,
        "rules": {"attempts": ATTEMPTS, "pass_score": PASS_SCORE},
    }


def list_plans(config: dict | None = None) -> list[dict]:
    store = load_store(config)
    return [
        {
            "id": p["id"], "created_at": p["created_at"], "topic": p["topic"],
            "goal": p["goal"], "title": p["plan"]["title"],
            "stage_count": len(p["stages"]),
            "passed": sum(1 for s in p["stages"] if s.get("status") == "passed"),
            "failed": sum(1 for s in p["stages"] if s.get("status") == "failed"),
        }
        for p in store.get("plans", [])
    ]


def get_plan(plan_id: str, config: dict | None = None) -> dict:
    store = load_store(config)
    return public_plan(_find(store, plan_id))


def _exam_prompt(record: dict, stage: dict) -> str:
    content = "\n".join(f"- {x}" for x in stage.get("content") or [])
    focus = "、".join(stage.get("assessment", {}).get("focus") or [])
    return f"""请为下面的学习阶段出 {EXAM_SIZE} 道单选题，输出 JSON。

学习主题：{record['topic']}
当前基础：{record['current_level']}
阶段：{stage['name']}
阶段目标：{stage['objective']}
学习内容：
{content}
考核重点：{focus or '阶段目标和学习内容'}

输出格式：
{{"questions": [
  {{"question": "题干", "options": ["A", "B", "C", "D"], "answer": 0, "explanation": "解析"}}
]}}

要求：
1. 正好 {EXAM_SIZE} 题，每题 4 个选项，answer 是 0~3 的整数，只有一个正确答案。
2. 覆盖阶段重点，既考理解也考应用，不要出依赖图片或外部链接的题。
3. 题干和选项用中文；不要输出 Markdown 代码块。
4. 只输出 JSON。"""


def _normalize_exam(data: dict) -> list[dict]:
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != EXAM_SIZE:
        raise LearningError(f"模型没有返回 {EXAM_SIZE} 道题，请重试")
    out = []
    for q in questions:
        if not isinstance(q, dict):
            raise LearningError("题目格式不正确")
        text = str(q.get("question") or "").strip()
        options = [str(x).strip() for x in (q.get("options") or [])]
        answer = q.get("answer")
        if not text or len(options) != 4 or any(not x for x in options):
            raise LearningError("题目缺少题干或选项，请重试")
        try:
            answer = int(answer)
        except (TypeError, ValueError):
            raise LearningError("题目答案格式不正确，请重试")
        if answer not in (0, 1, 2, 3):
            raise LearningError("题目答案超出范围，请重试")
        out.append({
            "question": text[:1000],
            "options": [x[:500] for x in options],
            "answer": answer,
            "explanation": str(q.get("explanation") or "").strip()[:1000],
        })
    return out


def generate_exam(plan_id: str, stage_index: int, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    store = load_store(config)
    record = _find(store, plan_id)
    if not 0 <= stage_index < len(record["stages"]):
        raise LearningError("阶段不存在")
    state = record["stages"][stage_index]
    if state.get("status") == "failed":
        raise LearningError("该阶段 3 次考核未通过，已作废，请先点「重新学习」")
    if state.get("status") == "passed":
        raise LearningError("该阶段已经通过")
    if state.get("status") == "locked":
        raise LearningError("前一阶段还没通过，当前阶段未解锁")
    if state.get("exam"):
        return {"questions": _public_questions(state["exam"])["questions"],
                "created_at": state["exam"].get("created_at")}

    stage = record["plan"]["stages"][stage_index]
    try:
        cfg = config_mod.effective_learning_model(config)
    except RuntimeError as exc:
        raise LearningError(str(exc)) from exc
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        raise LearningError("还没有配置总结模型 API key")
    try:
        raw = summarize_mod._chat(
            [{"role": "system", "content": _system_prompt()},
             {"role": "user", "content": _exam_prompt(record, stage)}],
            cfg,
            json_mode=True,
        )
    except summarize_mod.SummaryError as exc:
        raise LearningError(str(exc)) from exc
    questions = _normalize_exam(_extract_json(raw))
    state["exam"] = {"created_at": _now(), "questions": questions}
    _save(store, config)
    return {"questions": _public_questions(state["exam"])["questions"],
            "created_at": state["exam"]["created_at"]}


def grade_answers(questions: list[dict], answers: list) -> dict:
    details = []
    correct = 0
    for i, q in enumerate(questions):
        selected = answers[i] if i < len(answers) else -1
        try:
            selected = int(selected)
        except (TypeError, ValueError):
            selected = -1
        ok = selected == q.get("answer")
        correct += 1 if ok else 0
        details.append({
            "question": q.get("question"),
            "options": q.get("options"),
            "selected": selected,
            "answer": q.get("answer"),
            "correct": ok,
            "explanation": q.get("explanation"),
        })
    total = len(questions)
    score = round(correct * 100 / total) if total else 0
    return {"correct": correct, "total": total, "score": score, "details": details}


def _apply_attempt(record: dict, stage_index: int, score: int) -> dict:
    state = record["stages"][stage_index]
    stage = record["plan"]["stages"][stage_index]
    pass_score = int(stage.get("assessment", {}).get("pass_score") or PASS_SCORE)
    state["attempts"] = int(state.get("attempts") or 0) + 1
    state["score"] = score
    state["history"].append({"attempt": state["attempts"], "score": score, "at": _now()})
    if score >= pass_score:
        state["status"] = "passed"
        if stage_index + 1 < len(record["stages"]):
            next_state = record["stages"][stage_index + 1]
            if next_state.get("status") == "locked":
                next_state["status"] = "learning"
        return {"passed": True, "remaining": 0, "status": "passed"}
    if state["attempts"] >= ATTEMPTS:
        state["status"] = "failed"
        return {"passed": False, "remaining": 0, "status": "failed"}
    state["status"] = "learning"
    return {"passed": False, "remaining": ATTEMPTS - state["attempts"], "status": "learning"}


def submit_exam(plan_id: str, stage_index: int, answers: list,
                config: dict | None = None) -> dict:
    config = config or config_mod.load()
    store = load_store(config)
    record = _find(store, plan_id)
    if not 0 <= stage_index < len(record["stages"]):
        raise LearningError("阶段不存在")
    state = record["stages"][stage_index]
    if state.get("status") == "failed":
        raise LearningError("该阶段已作废，请先重新学习")
    if state.get("status") == "passed":
        raise LearningError("该阶段已经通过")
    exam = state.get("exam")
    if not exam or not exam.get("questions"):
        raise LearningError("还没有生成考核试卷")
    if not isinstance(answers, list) or len(answers) != len(exam["questions"]):
        raise LearningError(f"需要回答 {len(exam['questions'])} 道题")

    result = grade_answers(exam["questions"], answers)
    transition = _apply_attempt(record, stage_index, result["score"])
    state["exam"] = None
    _save(store, config)
    return {
        **result,
        **transition,
        "attempts": state["attempts"],
        "max_attempts": ATTEMPTS,
        "pass_score": int(record["plan"]["stages"][stage_index]
                          .get("assessment", {}).get("pass_score") or PASS_SCORE),
        "stage_status": state["status"],
        "plan": public_plan(record),
    }


def reset_stage(plan_id: str, stage_index: int, config: dict | None = None) -> dict:
    """3 次失败后的「重新学习」：清零次数、重新开放本阶段。"""
    config = config or config_mod.load()
    store = load_store(config)
    record = _find(store, plan_id)
    if not 0 <= stage_index < len(record["stages"]):
        raise LearningError("阶段不存在")
    state = record["stages"][stage_index]
    if state.get("status") != "failed":
        raise LearningError("只有已作废的阶段才需要重新学习")
    state.update({"status": "learning", "attempts": 0, "score": None, "exam": None})
    state.setdefault("history", []).append({"event": "relearn", "at": _now()})
    _save(store, config)
    return public_plan(record)


def delete_plan(plan_id: str, config: dict | None = None) -> bool:
    config = config or config_mod.load()
    store = load_store(config)
    before = len(store["plans"])
    store["plans"] = [p for p in store["plans"] if p.get("id") != plan_id]
    if len(store["plans"]) == before:
        return False
    _save(store, config)
    return True
