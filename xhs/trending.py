# -*- coding: utf-8 -*-
"""GitHub Trending 趋势工具。

边界：
    - 主榜读 GitHub Trending 页面，不调用 API，因此没有核心接口限流；
    - 页面匿名访问只列 10 条（且不分页），要凑满 30 条时用 GitHub 搜索接口
      补「最近创建、按 Star 排序」的仓库，补进来的条目会标 source=search，
      页面显示为「新晋」，和真正的 Trending 条目区分开；
    - 链接和数字都来自页面/接口，不自己编；
    - 中文一句话摘要用默认模型生成，失败时回退到英文原描述；
    - 结果缓存 10 分钟，避免反复抓取，也压住搜索接口的次数。
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
SEARCH_URL = "https://api.github.com/search/repositories"
CACHE_TTL = 600
LIMIT = 30
# 补齐时按 since 取「最近多久创建」的仓库，和榜单周期对应
SEARCH_WINDOW_DAYS = {"daily": 7, "weekly": 14, "monthly": 31}
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
            r"([\d,]+)\s+stars\s+(today|this week|this month)", article, re.S)
        stars = _clean(star_match.group(1)) if star_match else ""
        stars_today = today_match.group(1) if today_match else ""
        since_label = {"daily": "今日", "weekly": "本周", "monthly": "本月"}.get(since, "今日")
        items.append({
            "rank": len(items) + 1,
            "full_name": full_name,
            "name": _clean(h2.group(0)),
            "url": f"https://github.com/{full_name}",
            "description": _clean(desc_match.group(1)) if desc_match else "",
            "language": _clean(language_match.group(1)) if language_match else "",
            "stars": stars,
            "stars_today": stars_today,
            "stars_today_label": since_label,
            "stars_today_num": _parse_int(stars_today),
            "source": "trending",
        })
        if len(items) >= limit:
            break
    return items


def parse_search_items(payload: dict, start_rank: int = 1, exclude: set[str] | None = None) -> list[dict]:
    """把 GitHub 搜索接口的结果转成和 Trending 一样的条目；纯函数，便于离线测试。

    这些不是 Trending 页面里的项目，所以 source=search，前端会标成「新晋」，
    并且没有「今日新增 Star」这种数据（页面才有）。
    """
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    skip = {name.lower() for name in (exclude or set())}
    items: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        full_name = str(row.get("full_name") or "").strip()
        if not full_name or full_name.lower() in skip:
            continue
        skip.add(full_name.lower())
        stars = row.get("stargazers_count")
        items.append({
            "rank": start_rank + len(items),
            "full_name": full_name,
            "name": full_name.replace("/", " / "),
            "url": str(row.get("html_url") or f"https://github.com/{full_name}"),
            "description": _clean(str(row.get("description") or "")),
            "language": str(row.get("language") or ""),
            "stars": f"{stars:,}" if isinstance(stars, int) else "",
            "stars_today": "",
            "stars_today_label": "",
            "stars_today_num": 0,
            "source": "search",
            "created_at": str(row.get("created_at") or ""),
        })
    return items


def merge_items(primary: list[dict], extra: list[dict], limit: int = LIMIT) -> list[dict]:
    """主榜在前、补位在后，按仓库全名去重，最后统一重排名次。"""
    merged: list[dict] = []
    seen: set[str] = set()
    for item in list(primary) + list(extra):
        key = str(item.get("full_name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    for index, item in enumerate(merged, start=1):
        item["rank"] = index
    return merged


def _github_token(config: dict) -> str:
    return str(((config.get("github") or {}).get("token")) or "").strip()


def _search_supplement(need: int, since: str, exclude: set[str], config: dict,
                       language: str = "") -> list[dict]:
    """用搜索接口补「最近创建、按 Star 排序」的仓库，把榜单凑到 30 条。

    只在主榜不够时调用，一次刷新最多一次请求；配了 github.token 就走鉴权，
    没配时匿名也够用（搜索接口未鉴权限 10 次/分钟，而我们 10 分钟才刷新一次）。
    """
    if need <= 0:
        return []
    days = SEARCH_WINDOW_DAYS.get(since, 7)
    created = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ToolboxTrending/1.0)",
        "Accept": "application/vnd.github+json",
    }
    token = _github_token(config)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {
        "q": f"created:>={created}" + (f" language:{language}" if language else ""),
        "sort": "stars",
        "order": "desc",
        "per_page": min(100, need + len(exclude) + 10),
    }
    try:
        response = requests.get(SEARCH_URL, params=params, timeout=25, headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise TrendingError(f"补充榜单失败：{exc}") from exc
    return parse_search_items(payload, start_rank=len(exclude) + 1, exclude=exclude)[:need]


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
        f"下面是 GitHub 趋势榜单的 {len(items)} 个项目（含 Trending 页面和搜索补充）。"
        "请为每个项目写一句简体中文摘要，只依据 name 和 desc，不要猜测或编造功能。"
        "desc 为空时写「描述暂缺，建议打开仓库查看」，不要补功能。返回 JSON 数组，"
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
    since = since if since in ("daily", "weekly", "monthly") else "daily"
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
    if not items:
        raise TrendingError("GitHub Trending 页面没有解析到任何项目，可能是页面改版，请稍后再试")
    trending_count = len(items)

    # 页面匿名访问只给 10 条，剩下的用搜索接口补；补不到就只展示主榜，不报错
    supplement: list[dict] = []
    search_error = ""
    if len(items) < LIMIT:
        try:
            supplement = _search_supplement(
                LIMIT - len(items), since,
                {item["full_name"] for item in items},
                config or config_mod.load(),
                language,
            )
        except TrendingError as exc:
            search_error = str(exc)
            LOG.warning("趋势补充失败，只展示主榜 %s 条：%s", len(items), exc)
    items = merge_items(items, supplement, limit=LIMIT)

    # 描述缺失时最多回填 10 条，避免几十次请求把刷新拖慢
    for item in items[:10]:
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
        "trending_count": trending_count,
        "supplement_count": len([item for item in items if item.get("source") == "search"]),
        "search_error": search_error,
        "items": items,
        "ai_summary": ai_ok,
        "cached": False,
    }
    with _LOCK:
        _CACHE[key] = (now, result)
    return result
