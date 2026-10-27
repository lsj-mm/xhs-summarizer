# -*- coding: utf-8 -*-
"""多语言关键字查询：本地词库在前端，AI 解释与 GitHub API 示例在这里。

只围绕六种技术栈：Python、C++、Java、前端（JavaScript）、MySQL、Go。
"""

from __future__ import annotations

import logging

import requests

from . import config as config_mod
from . import summarize as summarize_mod

LOG = logging.getLogger(__name__)

LANGS = {
    "all": "全部",
    "python": "Python",
    "cpp": "C++",
    "java": "Java",
    "frontend": "前端（JavaScript）",
    "mysql": "MySQL",
    "go": "Go",
}

SYSTEM_PROMPT = """你是「多语言关键字查询工具」的开发专家。只围绕这六种技术栈回答：
Python、C++、Java、前端（JavaScript）、MySQL、Go。

用户会给你一个关键字，以及可选的语言范围。请按语言分别输出：
## 语言名
- 一句话定义：这个关键字在该语言/技术里是什么。
- 最小示例：一个能直接运行或直接粘贴的代码块，标注语言。
- 注意点：1-3 条实际开发中容易踩的坑。

规则：
- 只输出用户要求的语言；如果要求「全部」，六种都写，顺序为 Python、C++、Java、前端、MySQL、Go。
- 如果某个语言没有直接对应的概念，明确写「该语言没有直接对应概念」，不要硬编。
- 不要用表格；用二级标题和列表，代码块用 ``` 标注语言。
- 不编造不存在的 API、库或语法。不确定就说不确定。
- 总长度控制在 900 字以内。"""


class KeywordError(RuntimeError):
    """可以直接展示给用户的错误。"""


def explain(keyword: str, lang: str = "all", config: dict | None = None) -> dict:
    keyword = str(keyword or "").strip()
    if not keyword:
        raise KeywordError("请先输入要查询的关键字")
    if len(keyword) > 80:
        raise KeywordError("关键字太长了，请控制在 80 个字符以内")
    if lang not in LANGS:
        lang = "all"

    config = config or config_mod.load()
    cfg = config_mod.effective_summarizer(config)
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        raise KeywordError(
            "还没有配置总结模型 API key。请回到工具箱首页，点右上角「设置」填写后再试。"
        )

    scope = "全部六种技术栈" if lang == "all" else LANGS[lang]
    system = SYSTEM_PROMPT + f"\n\n本次范围：{scope}。"
    try:
        reply = summarize_mod._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"请解释关键字：{keyword}"},
            ],
            cfg,
        )
    except summarize_mod.SummaryError as exc:
        raise KeywordError(str(exc)) from exc

    return {
        "reply": reply.strip(),
        "model": f"{cfg['label']} / {cfg['model']}",
        "keyword": keyword,
        "lang": lang,
    }


def github_search(q: str, limit: int = 5, config: dict | None = None) -> dict:
    """真实调用 GitHub REST API 搜索仓库，作为 Python 调第三方 API 的示例。"""
    query = str(q or "").strip()
    if not query:
        return {"ok": False, "error": "请先输入要搜索的关键字"}
    if len(query) > 80:
        return {"ok": False, "error": "关键字太长了，请控制在 80 个字符以内"}

    config = config or config_mod.load()
    gh = config_mod.effective_github(config)
    limit = max(1, min(10, int(limit or 5)))
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xhs-summarizer-keywords/1.0",
    }
    if gh["token"]:
        headers["Authorization"] = f"Bearer {gh['token']}"

    url = f"{gh['api_base']}/search/repositories"
    try:
        resp = requests.get(
            url,
            params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
            headers=headers,
            timeout=gh["timeout_seconds"],
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": f"请求 GitHub 失败：{exc}"}

    if resp.status_code in (403, 429):
        return {
            "ok": False,
            "error": "GitHub 接口限流了。未认证时搜索接口约每分钟 10 次；"
                     "可以在设置里填一个 GitHub Token 提高额度。",
        }
    if resp.status_code != 200:
        return {"ok": False, "error": f"GitHub 返回 HTTP {resp.status_code}：{resp.text[:200]}"}

    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "error": "GitHub 返回的不是 JSON"}

    items = []
    for repo in (payload.get("items") or [])[:limit]:
        if not isinstance(repo, dict):
            continue
        items.append(
            {
                "full_name": repo.get("full_name") or "",
                "url": repo.get("html_url") or "",
                "description": repo.get("description") or "",
                "stars": int(repo.get("stargazers_count") or 0),
                "language": repo.get("language") or "",
            }
        )
    return {"ok": True, "query": query, "items": items}
