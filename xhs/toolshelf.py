# -*- coding: utf-8 -*-
"""外部工具收藏：把看到的第三方工具存成「名称 + 作用 + 链接」清单。

为什么不塞进 config.json：
    config.json 是运行参数和密钥，设置页会整段读写；收藏是独立数据，
    改坏了不该连累启动配置。所以单独放 work/toolshelf/items.json，
    和 work/learning/plans.json、work/lottery/saved.json 一个套路。

设计边界：
    - 只做「记录」，不去抓 GitHub、不校验仓库是否存在。链接是不是活的、
      项目靠不靠谱，由使用者自己判断，避免接口限流和误判把简单工具搞复杂。
    - 同一链接（忽略大小写与结尾斜杠）只允许存一条，防止反复添加同一工具。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse

from . import config as config_mod

STATE_VERSION = 1
MAX_ITEMS = 500
MAX_NAME = 80
MAX_PURPOSE = 300
MAX_URL = 500
MAX_TAGS = 8
MAX_TAG_LEN = 20


class ShelfError(RuntimeError):
    """可直接展示给用户的错误。"""


def _path(config: dict) -> Path:
    return config_mod.workdir(config) / "toolshelf" / "items.json"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_store(config: dict | None = None) -> dict:
    config = config or config_mod.load()
    path = _path(config)
    if not path.is_file():
        return {"version": STATE_VERSION, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"version": STATE_VERSION, "items": []}
    return data


def _save(store: dict, config: dict) -> None:
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再替换：中途出错不会留下半个 JSON 把整份收藏读废
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _text(raw, limit: int, label: str) -> str:
    """压掉多余空白后限长；超长直接报错，不做静默截断。"""
    value = " ".join(str(raw or "").split())
    if len(value) > limit:
        raise ShelfError(f"{label}太长了：最多 {limit} 个字符，当前 {len(value)} 个")
    return value


def normalize_url(raw) -> str:
    """校验并返回链接原文。只认 http(s)，挡掉 javascript: 之类的写法。"""
    value = str(raw or "").strip()
    if not value:
        raise ShelfError("请填写工具链接")
    if len(value) > MAX_URL:
        raise ShelfError(f"链接太长了：最多 {MAX_URL} 个字符")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ShelfError("链接必须以 http:// 或 https:// 开头")
    host = (parsed.netloc or "").lower()
    if not host or "." not in host:
        raise ShelfError(f"看起来不是有效网址：{value}")
    return value


def _normalize_tags(raw) -> list[str]:
    """标签用逗号/分号分隔；空格不算分隔符，否则「Claude Code」会被拆成两个。"""
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        chunks = re.split(r"[,，;；]+", raw)
    elif isinstance(raw, (list, tuple)):
        chunks = []
        for entry in raw:
            chunks.extend(re.split(r"[,，;；]+", str(entry)))
    else:
        raise ShelfError("标签需要是字符串或字符串数组")

    tags: list[str] = []
    for chunk in chunks:
        tag = " ".join(str(chunk).split())
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise ShelfError(f"标签「{tag}」太长：单个标签最多 {MAX_TAG_LEN} 个字符")
        if tag.lower() in {existing.lower() for existing in tags}:
            continue
        tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ShelfError(f"标签最多 {MAX_TAGS} 个")
    return tags


def _url_key(url: str) -> tuple[str, str]:
    parsed = urlparse(url or "")
    return (parsed.netloc.lower(), parsed.path.rstrip("/").lower())


def _find(store: dict, item_id: str) -> dict:
    for item in store["items"]:
        if item.get("id") == item_id:
            return item
    raise ShelfError("收藏里没有这一条（可能已被删除）")


def add_item(name, url, purpose="", tags=None, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    store = load_store(config)
    if len(store["items"]) >= MAX_ITEMS:
        raise ShelfError(f"最多收藏 {MAX_ITEMS} 条，请先删掉一些再添加")

    clean_name = _text(name, MAX_NAME, "名称")
    if not clean_name:
        raise ShelfError("请填写工具名称")
    clean_url = normalize_url(url)
    for existing in store["items"]:
        if _url_key(existing.get("url", "")) == _url_key(clean_url):
            raise ShelfError(f"这条链接已经在收藏里了（名称：{existing.get('name')}）")

    now = _now()
    item = {
        "id": secrets.token_hex(4),
        "name": clean_name,
        "url": clean_url,
        "purpose": _text(purpose, MAX_PURPOSE, "作用"),
        "tags": _normalize_tags(tags),
        "added_at": now,
        "updated_at": now,
    }
    store["items"].insert(0, item)
    _save(store, config)
    return item


def update_item(item_id: str, changes: dict, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    store = load_store(config)
    item = _find(store, item_id)
    changes = changes or {}

    if "name" in changes:
        clean_name = _text(changes.get("name"), MAX_NAME, "名称")
        if not clean_name:
            raise ShelfError("名称不能为空")
        item["name"] = clean_name
    if "url" in changes:
        clean_url = normalize_url(changes.get("url"))
        for other in store["items"]:
            if other is not item and _url_key(other.get("url", "")) == _url_key(clean_url):
                raise ShelfError(f"这条链接已经在收藏里了（名称：{other.get('name')}）")
        item["url"] = clean_url
    if "purpose" in changes:
        item["purpose"] = _text(changes.get("purpose"), MAX_PURPOSE, "作用")
    if "tags" in changes:
        item["tags"] = _normalize_tags(changes.get("tags"))

    item["updated_at"] = _now()
    _save(store, config)
    return item


def delete_item(item_id: str, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    store = load_store(config)
    item = _find(store, item_id)
    store["items"] = [entry for entry in store["items"] if entry.get("id") != item_id]
    _save(store, config)
    return item


def list_items(config: dict | None = None, keyword: str = "") -> dict:
    """列出收藏；keyword 同时匹配名称、作用、标签和链接。"""
    config = config or config_mod.load()
    store = load_store(config)
    items = [item for item in store["items"] if isinstance(item, dict)]
    query = " ".join(str(keyword or "").lower().split())
    if query:
        items = [
            item
            for item in items
            if query in " ".join(
                [
                    str(item.get("name", "")),
                    str(item.get("purpose", "")),
                    str(item.get("url", "")),
                    " ".join(item.get("tags") or []),
                ]
            ).lower()
        ]

    counter: dict[str, int] = {}
    for item in store["items"]:
        for tag in item.get("tags") or []:
            counter[str(tag)] = counter.get(str(tag), 0) + 1
    tags = sorted(counter, key=lambda tag: (-counter[tag], tag.lower()))
    return {
        "items": items,
        "count": len(items),
        "total": len(store["items"]),
        "tags": tags,
        "max_items": MAX_ITEMS,
    }
