# -*- coding: utf-8 -*-
"""把结果写成 markdown 文件：命名、去重、正文组装。

为什么单独一层：
    文件名清洗和去重是会出 bug 的地方（标题里有 `/`、`:`、emoji、超长、重名），
    跟 HTTP、AI 调用混在一起很难测，所以隔离成纯函数。
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path

LOG = logging.getLogger(__name__)

# Windows 文件名里不允许出现这些字符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2b00-\u2bff\ufe0f\u200d]"
)
_SPACES = re.compile(r"\s+")
# 小红书正文里的「#话题[话题]#」标记，保留话题名、去掉标记
_TOPIC_MARK = re.compile(r"\[话题\]#?")
_MAX_STEM = 60


def clean_note_text(text: str) -> str:
    """清理作者正文：去掉话题标记、压掉多余空格，正文本身一个字都不删。

    这份文本既要进 md（给人看），也要喂给模型（帮它写对专有名词），
    所以在入口处清洗一次，两处都受益。
    """
    if not text:
        return ""
    text = _TOPIC_MARK.sub(" ", text)
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def slugify(title: str, fallback: str = "小红书笔记") -> str:
    """把标题清洗成安全的文件名主干。"""
    text = _EMOJI.sub("", title or "")
    text = _ILLEGAL.sub(" ", text)
    text = _SPACES.sub(" ", text).strip(" .")
    # 纯标点/空标题的情况
    if not text or not re.search(r"[\w\u4e00-\u9fff]", text):
        text = fallback
    if len(text) > _MAX_STEM:
        text = text[:_MAX_STEM].rstrip(" .")
    return text


def unique_path(directory: Path, stem: str, suffix: str = ".md") -> Path:
    """目录内已存在同名文件时加 -2 / -3，不覆盖旧笔记。"""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def build_filename(meta: dict, when: _dt.date | None = None) -> str:
    when = when or _dt.date.today()
    parts = [when.isoformat(), slugify(meta.get("title") or "")]
    note_id = (meta.get("note_id") or "").strip()
    if note_id:
        parts.append(note_id[-8:])
    return "_".join(p for p in parts if p)


def format_duration(seconds: float | int | None) -> str:
    if not seconds:
        return "未知"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _yaml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _stamp(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"[{hours}:{minutes:02d}:{secs:02d}] "
    return f"[{minutes:02d}:{secs:02d}] "


_TABLE_ROW_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# 工具表可能出现在两种标题下：工具类视频用「工具清单」，其它类型用「提到的工具」
_TOOL_SECTION_KEYS = ("工具清单", "推荐清单", "提到的工具")
_TOOL_LIST_ITEM = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*\s*[：:]?\s*(.*)$")


def parse_tool_table(body: str) -> list[dict]:
    """从正文的工具表/有序清单里解析工具名与链接。

    为什么以正文为准写 front matter：
        成文阶段可能补上提取阶段漏掉的工具，如果 front matter 用的是提取结果，
        就会出现「front matter 说 3 个、正文写了 5 个」这种自相矛盾。
    支持两种格式：
        - 旧版 markdown 表格：| 工具 | 作用 | 项目地址 |
        - 新版有序清单：1. **工具名**：作用，下一行 GitHub：[owner/repo](url)
    """
    lines = (body or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and any(key in stripped for key in _TOOL_SECTION_KEYS):
            start = i
            break
    if start is None:
        return []

    tools: list[dict] = []
    current = None
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):  # 进入下一节
            break
        if stripped.startswith("|"):
            current = None
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) < 3:
                continue
            name = cells[0]
            if not name or name == "工具" or set(name) <= set("-: "):
                continue
            match = _TABLE_ROW_LINK.search(cells[2])
            tools.append({
                "name": name,
                "url": match.group(2) if match else "",
                "full_name": match.group(1) if match else "",
            })
            continue
        item = _TOOL_LIST_ITEM.match(stripped)
        if item:
            name = item.group(1).strip().rstrip("：:，,")
            rest = item.group(2).strip()
            match = _TABLE_ROW_LINK.search(rest)
            current = {
                "name": name,
                "url": match.group(2) if match else "",
                "full_name": match.group(1) if match else "",
            }
            tools.append(current)
            continue
        # 编号项下一行的 GitHub 链接
        if current and not current["url"]:
            match = _TABLE_ROW_LINK.search(stripped)
            if match and "github.com" in match.group(2).lower():
                current["url"] = match.group(2)
                current["full_name"] = match.group(1)
    return tools


def render_transcript(transcript: dict, include_timestamps: bool = True) -> str:
    segments = transcript.get("segments") or []
    if segments:
        lines = []
        for seg in segments:
            prefix = _stamp(seg.get("start")) if include_timestamps else ""
            text = (seg.get("text") or "").strip()
            if text:
                lines.append(f"{prefix}{text}")
        return "\n\n".join(lines)
    text = (transcript.get("text") or "").strip()
    # 没有时间戳时按句号断行，免得整篇挤成一坨
    return re.sub(r"(?<=[。！？!?])\s*", "\n\n", text) if text else ""


def render_markdown(meta: dict, body: str, transcript: dict | None, config: dict) -> str:
    """组装最终 md：正文 + 折叠的逐字稿（元数据块默认不输出）。"""
    transcript = transcript or {}
    text = (transcript.get("text") or "").strip()
    summary_cfg = config.get("summary") or {}
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 开头那段 YAML 元数据默认不写进文档：文档是给人读的，
    # 来源、作者、类型、时长这些已经在下面的引用行里了。
    # 需要机器可读元数据时，把设置里的开关打开即可。
    front: list[str] = []
    if summary_cfg.get("include_front_matter"):
        front = [
            "---",
            f"title: {_yaml_quote(meta.get('title') or '未命名笔记')}",
            f"source: {meta.get('url') or ''}",
            f"note_id: {meta.get('note_id') or ''}",
            f"author: {_yaml_quote(meta.get('author') or '未知')}",
            f"platform: 小红书",
            f"content_type: {meta.get('content_type') or '视频'}",
            f"topic_kind: {meta.get('topic_kind') or ''}",
            f"duration: {format_duration(meta.get('duration'))}",
            f"published: {meta.get('published') or '未知'}",
            f"summary_model: {_yaml_quote(meta.get('summarizer') or '')}",
            f"asr_model: {_yaml_quote(meta.get('asr') or '')}",
            f"transcript_chars: {len(text)}",
            f"tools: [{', '.join(_yaml_quote(t) for t in (meta.get('tools') or []))}]",
            f"github_links: {meta.get('github_links', 0)}",
            f"generated_at: {now}",
            "---",
            "",
        ]

    header = [f"# {meta.get('title') or '未命名笔记'}", ""]
    facts = [f"来源：[原笔记]({meta.get('url')})" if meta.get("url") else "来源：本地文件"]
    facts.append(f"作者：{meta.get('author') or '未知'}")
    if meta.get("topic_kind"):
        facts.append(f"类型：{meta['topic_kind']}")
    if meta.get("duration"):
        facts.append(f"时长：{format_duration(meta['duration'])}")
    if meta.get("tools"):
        facts.append(f"工具 {len(meta['tools'])} 个")
    header += ["> " + " ｜ ".join(facts), ""]

    # 有段落没转写成功时如实标注：读者得知道这份笔记缺了哪部分
    failures = meta.get("asr_failures") or []
    if failures:
        detail = "；".join(f"第 {f.get('index')}/{f.get('total')} 段（{f.get('error')}）" for f in failures)
        header += [f"> ⚠️ 有 {len(failures)} 段音频转写失败，本笔记缺少这部分内容：{detail}", ""]

    chunks = front + header + [body.strip(), ""]

    # 这里刻意不输出「原笔记正文」：文档要讲的是视频里的内容，
    # 作者正文只作为专有名词的核对参考喂给模型，不往文档里塞。

    if summary_cfg.get("include_transcript", True) and text:
        preview = text[:120].replace("\n", " ")
        chunks += [
            "---",
            "",
            "<details>",
            f"<summary>完整逐字稿（展开查看，共 {len(text)} 字）：{preview}…</summary>",
            "",
            render_transcript(transcript),
            "",
            "</details>",
            "",
        ]

    return "\n".join(chunks).rstrip() + "\n"


def write_markdown(directory: Path, meta: dict, body: str, transcript: dict | None, config: dict) -> Path:
    path = unique_path(directory, build_filename(meta))
    path.write_text(render_markdown(meta, body, transcript, config), encoding="utf-8")
    LOG.info("已写入 %s", path)
    return path
