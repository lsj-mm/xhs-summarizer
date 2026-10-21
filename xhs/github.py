# -*- coding: utf-8 -*-
"""给提取出来的工具找 GitHub 仓库地址。

为什么不让大模型直接写链接：
    模型很会编一个「看起来很像」的地址，甚至编出不存在的仓库。所以这里的
    原则是「可以猜，但必须验证」：

    1. 原文里出现的 github 链接  → 调接口确认仓库真实存在，才采信；
    2. 模型给出的 owner/repo     → 同样验证，404 就丢掉；
    3. 都没有                   → 用搜索接口按仓库名严格匹配，名字对不上就不要；
    4. 实在找不到               → 老实写「未找到」。

    宁可少给链接，也不给错链接。每条结果都带来源标记，看文档的人能自己判断可信度。
"""

from __future__ import annotations

import logging
import re

import requests

LOG = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.github.com"
USER_AGENT = "xhs-summarizer/1.0"

# 来源标记：写进文档，让人知道这个链接是怎么来的
SOURCE_FROM_TEXT = "原文给出"
SOURCE_FROM_MODEL = "模型提供，已验证"
# 搜索匹配分两级：名字完全一致的把握大得多，前缀相近的可能是第三方包装
SOURCE_FROM_SEARCH = "自动匹配（名字完全一致）"
SOURCE_FROM_SEARCH_LOOSE = "自动匹配（名字相近，请核对）"
SOURCE_NONE = "未找到"
# 查不了 ≠ 不存在：限流时必须如实标注，不能谎报成「未找到」
SOURCE_RATE_LIMITED = "查询失败（GitHub 接口限流，配置 Token 可解决）"

GITHUB_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I
)
OWNER_REPO_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
# 仓库地址后面常跟着 #readme、/tree/main 之类的尾巴，要去掉
_TAIL_RE = re.compile(r"[#?].*$|\.git$|/(?:tree|blob|releases|wiki)/.*$", re.I)

# 这些「仓库名」其实是 GitHub 的功能路径，不能当成仓库
_RESERVED = {"topics", "search", "explore", "trending", "collections", "sponsors", "features", "about"}


def _norm(text: str) -> str:
    """归一化：只留字母数字，用于比较名字。"""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _score(query: str, repo_name: str) -> int:
    """给「搜索到的仓库名」和「工具名」的相似度打分，0 表示不认为匹配。"""
    q, r = _norm(query), _norm(repo_name)
    if not q or not r:
        return 0
    if q == r:
        return 100
    if r.startswith(q) or q.startswith(r):
        return 70
    if q in r:
        return 60
    return 0


class GitHubLookup:
    """把工具名解析成真实存在的 GitHub 仓库。"""

    def __init__(self, api_base: str = "", token: str = "", timeout: int = 15,
                 min_score: int = 60, min_stars: int = 50, enabled: bool = True,
                 max_tools: int = 20, allow_loose_match: bool = False) -> None:
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.token = (token or "").strip()
        self.timeout = int(timeout or 15)
        self.min_score = int(min_score or 60)
        self.min_stars = int(min_stars if min_stars is not None else 50)
        self.enabled = bool(enabled)
        self.max_tools = int(max_tools or 20)
        # 默认只接受「仓库名与工具名完全一致」的搜索匹配
        self.allow_loose_match = bool(allow_loose_match)
        self.session = requests.Session()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers.update(headers)
        self.lookups = 0
        # 未认证时两类配额是分开算的：core（仓库详情）每小时 60 次，
        # search（搜索）每分钟 10 次。一类被限流不该连累另一类，所以分开记。
        self.limited = {"core": False, "search": False}

    # ------------------------------------------------------------------ HTTP

    def _get(self, path: str, bucket: str = "core",
             params: dict | None = None) -> requests.Response | None:
        if self.limited.get(bucket):
            # 限流要等窗口重置，本作业内不会恢复，继续发请求只是白等
            return None
        url = f"{self.api_base}{path}"
        self.lookups += 1
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            LOG.warning("请求 GitHub 失败（%s）：%s", url, exc)
            return None
        if resp.status_code in (403, 429):
            self.limited[bucket] = True
            LOG.warning(
                "GitHub %s 接口限流（HTTP %s），本次不再查询这一类。"
                "未认证时 core 每小时 60 次、search 每分钟 10 次，"
                "代理出口 IP 常被多人共用，配置 GITHUB_TOKEN 可提高到 5000 次/小时",
                bucket, resp.status_code,
            )
        return resp

    def _get_repo(self, owner: str, repo: str) -> dict | None:
        """确认仓库存在。返回仓库信息，不存在或没权限则返回 None。"""
        if not owner or not repo or owner.lower() in _RESERVED:
            return None
        repo = _TAIL_RE.sub("", repo)
        if not repo:
            return None
        resp = self._get(f"/repos/{owner}/{repo}", bucket="core")
        if resp is None:
            return None
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                LOG.warning("GitHub 返回的不是 JSON：%s", resp.text[:120])
                return None
        if resp.status_code in (403, 429):
            LOG.warning("GitHub 接口被限流或拒绝（HTTP %s），本次不再继续查询", resp.status_code)
        return None

    def _search(self, name: str) -> tuple[dict, int] | None:
        """按名字搜索仓库，返回 (仓库信息, 匹配得分) ；名字对不上或不够像样则返回 None。

        为什么要卡 star 数：实测「NSA Ghidra」会搜到一个 0 star 的同名仓库
        DamianFekete/NSA-Ghidra —— 归一化后名字完全一致，得分反而高于官方
        ghidra（8.9 万 star）。所以这里先把明显是「同名无关项目」的候选剔掉，
        再在剩下的合格候选里按（名称相似度, star 数）取最优。
        """
        resp = self._get(
            "/search/repositories",
            bucket="search",
            params={"q": f"{name} in:name", "sort": "stars", "order": "desc", "per_page": 10},
        )
        if resp is None or resp.status_code != 200:
            if resp is not None and resp.status_code in (403, 429):
                LOG.warning("GitHub 搜索被限流（HTTP %s）", resp.status_code)
            return None
        try:
            items = (resp.json() or {}).get("items") or []
        except ValueError:
            return None

        eligible: list[tuple[int, int, dict]] = []
        rejected: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            score = _score(name, item.get("name") or "")
            if score < self.min_score:
                continue
            if score < 100 and not self.allow_loose_match:
                # 名字只是相近，多半是第三方包装或同名项目（商业产品尤其常见），
                # 宁可不给链接，也不要给一个看着像但其实不对的地址
                rejected.append(f"{item.get('full_name')}(名字相近)")
                continue
            stars = int(item.get("stargazers_count") or 0)
            if stars < self.min_stars:
                rejected.append(f"{item.get('full_name')}({stars}★)")
                continue
            eligible.append((score, stars, item))

        if not eligible:
            LOG.info("工具 %r 没有合适的仓库（被剔除：%s）", name, "、".join(rejected) or "无名字匹配")
            return None
        eligible.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        best = eligible[0]
        # 连得分一起返回：调用方据此区分「名字完全一致」和「名字相近」
        return best[2], best[0]

    # -------------------------------------------------------------- 对外接口

    @staticmethod
    def _extract_candidate(*texts: str) -> tuple[str, str] | None:
        """从若干段文本里找出一个 owner/repo 候选。"""
        for text in texts:
            if not text:
                continue
            match = GITHUB_URL_RE.search(text)
            if match:
                return match.group(1), _TAIL_RE.sub("", match.group(2))
        for text in texts:
            if not text:
                continue
            cleaned = text.strip().strip("`").strip()
            match = OWNER_REPO_RE.match(cleaned)
            if match and match.group(1).lower() not in _RESERVED:
                return match.group(1), _TAIL_RE.sub("", match.group(2))
        return None

    def resolve(self, tool: dict) -> dict:
        """解析单个工具。返回带 url 与 source 的字典。"""
        name = (tool.get("name") or "").strip()
        result = {
            "name": name,
            "purpose": (tool.get("purpose") or "").strip(),
            "url": "",
            "full_name": "",
            "source": SOURCE_NONE,
            "repo_description": "",
            "stars": 0,
        }
        if not name:
            return result
        if not self.enabled:
            result["source"] = "未查询（已关闭自动匹配）"
            return result

        # 说明：限流短路在 _get 里做，这里直接调用即可，被限流时它会返回 None

        # 1) 原文里出现的链接优先：这是最可信的来源
        candidate = self._extract_candidate(tool.get("evidence") or "", tool.get("purpose") or "")
        if candidate:
            repo = self._get_repo(*candidate)
            if repo:
                return {**result, "url": repo.get("html_url", ""),
                        "full_name": repo.get("full_name", ""), "source": SOURCE_FROM_TEXT,
                        "repo_description": repo.get("description") or "",
                        "stars": int(repo.get("stargazers_count") or 0)}

        # 2) 模型给的 owner/repo：验证通过才采信
        candidate = self._extract_candidate(tool.get("github") or "")
        if candidate:
            repo = self._get_repo(*candidate)
            if repo:
                return {**result, "url": repo.get("html_url", ""),
                        "full_name": repo.get("full_name", ""), "source": SOURCE_FROM_MODEL,
                        "repo_description": repo.get("description") or "",
                        "stars": int(repo.get("stargazers_count") or 0)}
            LOG.info("模型给的仓库 %s/%s 不存在或未验证成功，忽略", candidate[0], candidate[1])

        # 3) 搜索兜底：只认名字基本一致的仓库
        found = self._search(name)
        if found:
            item, score = found
            return {**result, "url": item.get("html_url", ""),
                    "full_name": item.get("full_name", ""),
                    "source": SOURCE_FROM_SEARCH if score >= 100 else SOURCE_FROM_SEARCH_LOOSE,
                    "repo_description": item.get("description") or "",
                    "stars": int(item.get("stargazers_count") or 0)}

        # 走到这里说明没找到链接。必须在查询「之后」判断限流状态：
        # 限流往往是查询当场才发现的，否则会把「查不了」误报成「这个工具没有仓库」。
        if self.limited["core"] or self.limited["search"]:
            result["source"] = SOURCE_RATE_LIMITED
        return result

    def resolve_all(self, tools: list[dict], emit=None) -> list[dict]:
        resolved: list[dict] = []
        for tool in (tools or [])[: self.max_tools]:
            if not isinstance(tool, dict):
                continue
            outcome = self.resolve(tool)
            if emit:
                if outcome["url"]:
                    emit(f"工具「{outcome['name']}」找到仓库：{outcome['full_name']}（{outcome['source']}）")
                else:
                    emit(f"工具「{outcome['name']}」没有找到可靠的 GitHub 仓库")
            resolved.append(outcome)
        if self.lookups and emit:
            emit(f"GitHub 查询完成，共请求 {self.lookups} 次")
        return resolved
