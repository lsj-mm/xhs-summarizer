# -*- coding: utf-8 -*-
"""链接解析：短链跟跳转、抽取笔记 id、图文笔记兜底取正文。

为什么需要这一步：
    小红书 App 分享出来的是 `xhslink.com` 短链，而带 `xsec_token` 的完整地址才
    是下载器真正需要的东西；直接把它丢给 yt-dlp 常常拿到「不支持该链接」。
    先跟一次跳转，再交给下载器，成功率差别很大。
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import requests

LOG = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

NOTE_ID_RE = re.compile(r"/(?:explore|discovery/item|item)/([0-9a-zA-Z]{16,32})")
SHORT_HOSTS = ("xhslink.com", "xhslink.cn", "xhs.cn")
INITIAL_STATE_RE = re.compile(
    r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*(?:;)?\s*</script>", re.S
)


def is_short_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in SHORT_HOSTS)


def extract_note_id(url: str) -> str:
    match = NOTE_ID_RE.search(url or "")
    return match.group(1) if match else ""


def new_session(proxy: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def resolve(url: str, proxy: str = "", timeout: int = 30, session: requests.Session | None = None) -> dict:
    """跟跳转拿到最终地址。返回 {url, note_id, is_short, html}。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError(f"不是合法链接：{url!r}（需要以 http/https 开头）")

    session = session or new_session(proxy)
    short = is_short_link(url)
    html = ""
    final = url

    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        final = str(resp.url)
        if resp.headers.get("Content-Type", "").startswith("text/html"):
            html = resp.text
    except requests.RequestException as exc:
        # 短链跳转失败多半是网络/风控问题，但不该直接判死刑：
        # 原始地址可能本身就是完整链接，交给下载器再试一次。
        LOG.warning("跟跳转失败（%s），改用原始地址继续：%s", exc, url)
        return {"url": url, "note_id": extract_note_id(url), "is_short": short, "html": "", "resolved": False}

    if not extract_note_id(final) and html:
        # 有些短链落在中转页，真正的地址写在页面脚本里
        match = re.search(r"https?://(?:www\.)?xiaohongshu\.com/[^\s\"'<>\\)]+", html)
        if match:
            LOG.info("从中转页里取到真实地址")
            final = match.group(0)

    note_id = extract_note_id(final) or extract_note_id(url)
    LOG.info("解析结果：%s（note_id=%s）", final, note_id or "未识别")
    return {"url": final, "note_id": note_id, "is_short": short, "html": html, "resolved": final != url}


def parse_initial_state(html: str) -> dict | None:
    """从页面 HTML 里取出 __INITIAL_STATE__。

    小红书页面不是稳定 API，字段随时会变，所以这里只做「能取就取」，
    取不到就返回 None，由调用方决定降级行为。
    """
    match = INITIAL_STATE_RE.search(html or "")
    if not match:
        return None
    raw = match.group(1)
    # 页面里的 JSON 混着 JS 字面量，先替换成合法 JSON 再解析
    raw = re.sub(r"\bundefined\b", "null", raw)
    raw = re.sub(r"\bNaN\b", "null", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        LOG.warning("__INITIAL_STATE__ 不是合法 JSON（%s），跳过图文兜底", exc)
        return None


def note_from_state(state: dict, note_id: str = "") -> dict | None:
    try:
        detail_map = state["note"]["noteDetailMap"]
    except (KeyError, TypeError):
        return None
    if not isinstance(detail_map, dict) or not detail_map:
        return None

    key = note_id if note_id in detail_map else next(iter(detail_map))
    entry = detail_map.get(key) or {}
    note = entry.get("note") or entry
    if not isinstance(note, dict) or not (note.get("title") or note.get("desc")):
        return None

    published = ""
    if note.get("time"):
        try:
            import datetime as _dt

            published = _dt.datetime.fromtimestamp(int(note["time"]) / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError, TypeError):
            published = ""

    return {
        "note_id": key,
        "title": (note.get("title") or "").strip(),
        "desc": (note.get("desc") or "").strip(),
        "author": ((note.get("user") or {}).get("nickname") or "").strip(),
        "content_type": "图文" if note.get("type") == "normal" else "视频",
        "published": published,
        "images": len(note.get("imageList") or []),
    }


def fetch_note_text(url: str, note_id: str = "", proxy: str = "", timeout: int = 30) -> dict | None:
    """图文笔记（或下载失败时）的兜底：把正文当素材给模型总结。"""
    session = new_session(proxy)
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        LOG.warning("抓取笔记正文失败：%s", exc)
        return None
    state = parse_initial_state(resp.text)
    if not state:
        return None
    return note_from_state(state, note_id or extract_note_id(str(resp.url)))
