# -*- coding: utf-8 -*-
"""GitHub Trending 趋势工具。

边界：
    - 只读取 GitHub Trending 页面，不调用 GitHub API，因此没有 API 限流；
    - 严格取前 10 条，链接和数字都来自页面；
    - 中文一句话摘要用默认模型生成，失败时回退到英文原描述；
    - 结果缓存 10 分钟，避免反复抓取。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import re
import threading
import time
from urllib.parse import quote

import requests

from . import config as config_mod
from . import summarize as summarize_mod

LOG = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
CACHE_TTL = 600
LIMIT = 10
LANGUAGES = [
    ("", "全部语言"), ("python", "Python"), ("typescript", "TypeScript"),
    ("javascript", "JavaScript"), ("go", "Go"), ("rust", "Rust"),
    ("java", "Java"), ("c++", "C++"), ("shell", "Shell"),
]

_CACHE: dict[tuple, tuple[float, dict]] = {}
_LOCK = threading.Lock()


class TrendingError(RuntimeError):
    """可直接展示给用户的错误。"""


def _clean(value: str) -> str:
    value = re.sub(r"<script.*?</script>", "", value or "", flags=re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _parse_int(value: str) -> int:
    try:
        return int(re.sub(r"[^\d]", "", value or "") or 0)
    except ValueError:
        return 0


def parse_trending_html(source: str, since: str = "daily", limit: int = LIMIT) -> list[dict]:
    """从 GitHub Trending HTML 里解析项目；纯函数，方便离线测试。"""
    articles = re.findall(r'<article class="Box-row">(.*?)</article>', source or "", re.S)
    items: list[dict] = []
    seen: set[str] = set()
    for article in articles:
        h2 = re.search(r"<h2[^>]*>.*?</h2>", article, re.S)
        if not h2:
            continue
        links = re.findall(r'href="(/[^"/]+/[^"/]+)"', h2.group(0))
        path = next((link for link in links if not link.startswith(("/login", "/sponsors"))), "")
        full_name = path.strip("/")
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        desc_match = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', article, re.S)
        language_match = re.search(
            r'itemprop="programmingLanguage"[^>]*>(.*?)<', article, re.S)
        star_match = re.search(
            r'href="[^"]*/stargazers"[^>]*>(.*?)</a>', article, re.S)
        today_match = re.search(
            r"([\d,]+)\s+stars\s+(today|this week)", article, re.S)
        stars = _clean(star_match.group(1)) if star_match else ""
        stars_today = today_match.group(1) if today_match else ""
        items.append({
            "rank": len(items) + 1,
            "full_name": full_name,
            "name": _clean(h2.group(0)),
            "url": f"https://github.com/{full_name}",
            "description": _clean(desc_match.group(1)) if desc_match else "",
            "language": _clean(language_match.group(1)) if language_match else "",
            "stars": stars,
            "stars_today": stars_today,
            "stars_today_label": "今日" if since == "daily" else "本周",
            "stars_today_num": _parse_int(stars_today),
        })
        if len(items) >= limit:
            break
    return items


def _readme_description(full_name: str) -> str:
    """Trending 有时不给描述；用 README 第一段补一条，仍然保持可核验。"""
    text = ""
    for branch in ("HEAD", "main"):
        try:
            response = requests.get(
                f"https://raw.githubusercontent.com/{full_name}/{branch}/README.md",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ToolboxTrending/1.0)"},
            )
            if response.ok and response.text.strip():
                text = response.text
                break
        except requests.RequestException:
            continue
    if not text:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[!", "![", "<", "|", "```", "---")):
            continue
        if len(line) < 20:
            continue
        return _clean(line)[:300]
    return ""


def _translate(items: list[dict], config: dict) -> tuple[list[str], bool]:
    try:
        cfg = config_mod.effective_summarizer(config)
    except RuntimeError as exc:
        LOG.warning("趋势摘要跳过：%s", exc)
        return [], False
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        return [], False
    payload = [
        {"i": i, "name": item["full_name"], "desc": item["description"]}
        for i, item in enumerate(items)
    ]
    prompt = (
        "下面是 GitHub Trending 的 10 个项目。请为每个项目写一句简体中文摘要，"
        "只依据 name 和 desc，不要猜测或编造功能。desc 为空时写"
        "「描述暂缺，建议打开仓库查看」，不要补功能。返回 JSON 数组，"
        "每项格式 {\"i\": 原索引, \"summary\": \"一句话中文摘要\"}，不要输出其它文字。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        raw = summarize_mod._chat(
            [{"role": "system", "content": "你是严谨的开源项目情报编辑，只输出 JSON。"},
             {"role": "user", "content": prompt}],
            cfg, json_mode=True,
        )
        raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw or "", flags=re.I)
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or data.get("summaries") or []
        if not isinstance(data, list):
            return [], False
        summaries = [""] * len(items)
        for row in data:
            index = int(row.get("i", -1))
            if 0 <= index < len(items):
                summaries[index] = str(row.get("summary") or "").strip()
        parsed = sum(1 for x in summaries if x)
        return summaries, parsed >= max(1, len(items) // 2)
    except Exception as exc:
        LOG.warning("趋势摘要生成失败：%s", exc)
        return [], False


def fetch_trending(since: str = "daily", language: str = "",
                   config: dict | None = None, refresh: bool = False) -> dict:
    since = "weekly" if since == "weekly" else "daily"
    language = (language or "").strip().lower()
    if language not in {value for value, _ in LANGUAGES}:
        language = ""
    key = (since, language)
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and not refresh and now - cached[0] < CACHE_TTL:
            return {**cached[1], "cached": True}

    url = f"{TRENDING_URL}?since={since}"
    if language:
        url += f"&language={quote(language)}"
    try:
        response = requests.get(
            url, timeout=25,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ToolboxTrending/1.0)"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TrendingError(f"抓取 GitHub Trending 失败：{exc}") from exc

    items = parse_trending_html(response.text, since=since, limit=LIMIT)
    if len(items) < LIMIT:
        raise TrendingError(f"GitHub Trending 只解析到 {len(items)} 条，少于 10 条，已停止展示")
    for item in items:
        if not item.get("description"):
            item["description"] = _readme_description(item["full_name"])
    summaries, ai_ok = _translate(items, config or config_mod.load())
    for index, item in enumerate(items):
        item["summary"] = summaries[index] if index < len(summaries) and summaries[index] else ""
        item["summary_source"] = "ai" if item["summary"] else "description"
        if not item["summary"]:
            item["summary"] = item["description"] or "描述暂缺，建议打开仓库查看"

    result = {
        "since": since,
        "language": language,
        "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": url,
        "count": len(items),
        "items": items,
        "ai_summary": ai_ok,
        "cached": False,
    }
    with _LOCK:
        _CACHE[key] = (now, result)
    return result
