# -*- coding: utf-8 -*-
"""编程英语学习助手：复用已配置的总结模型，按固定角色回答。

为什么单独一层：
    网页端只负责对话和展示，提示词、长度限制、错误翻译都在这里，
    以后要做 CLI 或别的入口也能直接复用。
"""

from __future__ import annotations

import base64
import binascii
import logging
import uuid

from . import asr as asr_mod
from . import audio as audio_mod
from . import config as config_mod
from . import summarize as summarize_mod

LOG = logging.getLogger(__name__)

MODES = {"vocab": "编程词汇查询", "speaking": "日常口语练习"}
LEVELS = {"beginner": "入门", "intermediate": "进阶", "advanced": "高级"}

SYSTEM_PROMPT = """你是「编程英语学习助手」，面向中文母语的程序员和技术学习者。
你只负责两件事：解释编程英语词汇/表达，以及带用户练日常英语口语。

通用规则：
- 用中文讲解，英文例句保留英文；除非用户明确要求全英文。
- 先给结论或示范，再展开。结构清晰，用短段落和列表，不要用表格。
- 不编造术语。没把握时明确说「这个词在编程里不常见 / 我不确定」，不要硬编一个解释。
- 用户问与英语学习无关的代码调试、架构选型等问题时，用一句话说明你只负责英语表达，
  然后把问题转成「这句话用英语怎么说」，不要展开技术解答。
- 用户是来学习的：一次不要塞太多，必要时问一句「要不要再给两个例句？」。

【词汇查询模式】
用户给一个词、短语或句子时，按下面的结构回答：
1. 音标：给 IPA；并说明「文字音标仅供参考，建议跟读音频」。
2. 释义：词性 + 中文含义，优先给编程语境下的含义。
3. 技术例句：2 个有真实感的英文句子（技术文档 / 代码注释 / 报错 / 代码评审），每句配中文。
4. 常见搭配：3-4 个，例如 `race condition`、`handle an exception`。
5. 易混辨析：有近义词时说明区别；没有就省略。
6. 记忆提示：词根、联想或一句话用法。
如果用户给的是中文、想翻成英文：先给 2-3 种自然说法（正式 / 日常），再说明语气差别。

【口语练习模式】
用户选一个场景或直接用英语说话时：
- 第一轮：给 3-5 句示范表达（英文 + 中文 + 使用时机），然后邀请用户用英语回复。
- 用户回复后：先肯定至少一个做得好的地方；再指出 1-3 个最值得改的问题（语法 / 用词 / 语气）；
  给出更自然的版本；最后留一个追问或下一轮练习。
- 不要一次纠正所有小错，优先改影响沟通的错误。
- 发音问题用文字描述（重音、连读、易错音），并说明文字指导的局限。

输出控制在 400 字以内，除非用户要求详细展开。"""


class EnglishError(RuntimeError):
    """可以直接展示给用户的错误。"""


def _clean_messages(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise EnglishError("messages 需要是数组")
    if len(raw) > 20:
        raise EnglishError("这轮对话太长了，点「新对话」再继续")

    cleaned: list[dict] = []
    total = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if len(content) > 4000:
            content = content[:4000]
        total += len(content)
        cleaned.append({"role": role, "content": content})

    if not cleaned or cleaned[-1]["role"] != "user":
        raise EnglishError("最后一条消息需要是用户输入")
    if total > 12000:
        raise EnglishError("对话内容太长了，点「新对话」再继续")
    return cleaned


_VOICE_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def transcribe(audio_b64: object, mime: str = "", config: dict | None = None) -> dict:
    """把浏览器录的一段语音转成文字。

    浏览器录出来通常是 webm/opus，云端 ASR 对 mp3 最稳，所以先用项目自带的
    ffmpeg 转成 16kHz 单声道 mp3，再走已有的转写模块。
    """
    if not isinstance(audio_b64, str) or not audio_b64.strip():
        raise EnglishError("没有收到录音数据")
    if len(audio_b64) > 12_000_000:
        raise EnglishError("录音太长了，请控制在 60 秒以内")

    payload = audio_b64.strip()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise EnglishError("录音数据不完整，请重试") from exc
    if len(raw) < 800:
        raise EnglishError("录音太短，请说完一句话再停止")

    mime = (mime or "").split(";")[0].strip().lower()
    ext = _VOICE_EXT.get(mime, "webm")
    config = config or config_mod.load()
    workdir = config_mod.workdir(config) / "_english_voice"
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / f"voice-{uuid.uuid4().hex}.{ext}"
    mp3 = source.with_suffix(".mp3")

    try:
        try:
            source.write_bytes(raw)
        except OSError as exc:
            raise EnglishError(f"保存录音失败：{exc}") from exc

        proc = audio_mod._run(
            [
                audio_mod.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(mp3),
            ],
            timeout=120,
        )
        if proc.returncode != 0 or not mp3.exists() or mp3.stat().st_size < 100:
            detail = (proc.stderr or "").strip()[:200]
            raise EnglishError(f"音频转码失败：{detail or '未知错误'}")

        cfg = config_mod.effective_asr(config)
        # 不强制中文：用户可能说英文，也可能用中文问「这句英语怎么说」
        cfg["language"] = ""
        result = asr_mod.transcribe_file(mp3, cfg)
        text = (result.get("text") or "").strip()
        if not text:
            raise EnglishError("没有识别到语音，请靠近麦克风、说完整一句话再试")
        return {"text": text, "model": f"{cfg['label']} / {cfg['model']}"}
    except (asr_mod.AsrError, audio_mod.AudioError) as exc:
        raise EnglishError(str(exc)) from exc
    except RuntimeError as exc:
        raise EnglishError(str(exc)) from exc
    finally:
        for path in (source, mp3):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def chat(messages: object, mode: str = "vocab", level: str = "intermediate",
         config: dict | None = None) -> dict:
    """调用总结模型回答一条英语学习消息。"""
    config = config or config_mod.load()
    cfg = config_mod.effective_summarizer(config)
    if not cfg.get("api_key"):
        raise EnglishError(
            "还没有配置总结模型 API key。请回到工具箱首页，点右上角「设置」填写后再试。"
        )

    mode = mode if mode in MODES else "vocab"
    level = level if level in LEVELS else "intermediate"
    system = (
        SYSTEM_PROMPT
        + f"\n\n当前模式：{MODES[mode]}；用户自评水平：{LEVELS[level]}。"
        + "\n请根据水平和模式调整讲解深度，不要输出表格。"
    )

    try:
        reply = summarize_mod._chat(
            [{"role": "system", "content": system}, *_clean_messages(messages)],
            cfg,
        )
    except summarize_mod.SummaryError as exc:
        raise EnglishError(str(exc)) from exc

    return {
        "reply": reply.strip(),
        "model": f"{cfg['label']} / {cfg['model']}",
    }
