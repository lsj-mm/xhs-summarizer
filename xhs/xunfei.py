# -*- coding: utf-8 -*-
"""讯飞语音评测（流式版）接入。

协议要点（来自讯飞官方 WebAPI 文档）：
    - WebSocket 地址：wss://ise-api.xfyun.cn/v2/open-ise
    - 鉴权：HMAC-SHA256 签 host/date/request-line，再 base64 放进 query
    - 音频：16kHz、16bit、单声道 PCM；按 1280 字节一帧上传
    - 参数：sub=ise、ent=en_vip、category=read_sentence、cmd=ssb
    - 结果：服务端返回 base64 的 XML，解析 read_sentence/word/phone 节点

限制：
    - 这是「朗读评测」，需要参考文本；
    - 每次调用会消耗讯飞免费额度/套餐次数；
    - 讯飞返回的词/音素错误用 dp_message 标记，不是每个音素都有 0-100 分。
"""

from __future__ import annotations

import base64
import binascii
import email.utils
import hashlib
import hmac
import json
import logging
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import websocket

from . import audio as audio_mod
from . import config as config_mod

LOG = logging.getLogger(__name__)

HOST = "ise-api.xfyun.cn"
PATH = "/v2/open-ise"
CHUNK_SIZE = 1280

_VOICE_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}

_DP_MESSAGE = {
    0: "",
    16: "漏读",
    32: "多读",
    64: "回读",
    128: "替换",
}


class XunfeiError(RuntimeError):
    """可以直接展示给用户的错误。"""


def _num(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _setup_message() -> str:
    return (
        "还没有配置讯飞语音评测。\n"
        "1. 登录讯飞开放平台，创建应用并添加「语音评测（流式版）」；\n"
        "2. 在控制台拿到 APPID、APIKey、APISecret；\n"
        "3. 在「编程英语」的「Azure / 讯飞发音评分」里选「讯飞」并填写，"
        "或设置环境变量 XUNFEI_APP_ID / XUNFEI_API_KEY / XUNFEI_API_SECRET；\n"
        "4. 重启服务后再点「发音评分」。"
    )


def signed_url(api_key: str, api_secret: str) -> str:
    """生成带 HMAC 鉴权的 WebSocket URL。"""
    date = email.utils.formatdate(usegmt=True)
    origin = f"host: {HOST}\ndate: {date}\nGET {PATH} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(api_secret.encode("utf-8"), origin.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("ascii")
    return f"wss://{HOST}{PATH}?" + urlencode(
        {"authorization": authorization, "date": date, "host": HOST}
    )


def parse_result(xml_text: str) -> dict:
    """解析讯飞返回的 XML。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise XunfeiError(f"讯飞返回的 XML 解析失败：{exc}") from exc

    # 有的版本会给节点加 XML 命名空间，统一去掉，后面按本地名查找
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.rsplit("}", 1)[1]

    primary = None
    for tag in ("read_sentence", "read_chapter", "read_word"):
        primary = root.find(f".//{tag}")
        if primary is not None:
            break
    if primary is None:
        raise XunfeiError("讯飞返回里没有 read_sentence/read_chapter 节点")

    score_keys = ("total_score", "accuracy_score", "fluency_score",
                  "integrity_score", "standard_score")

    def has_scores(element) -> bool:
        return any(element.get(key) is not None for key in score_keys)

    # 不同题型/版本可能把分数放在 read_sentence，也可能放在内层 sentence/rec_paper
    node = primary if has_scores(primary) else None
    if node is None:
        for element in root.iter():
            if has_scores(element):
                node = element
                break
    if node is None:
        raise XunfeiError(
            "讯飞没有返回分数，请读完整一句再试；原始 XML 片段："
            + xml_text[:400]
        )

    def attr(name):
        return _num(node.get(name))

    scores = {
        "pronunciation": attr("total_score"),
        "accuracy": attr("accuracy_score"),
        "fluency": attr("fluency_score"),
        "completeness": attr("integrity_score"),
        "standard": attr("standard_score"),
    }
    if all(value is None for value in scores.values()):
        raise XunfeiError("讯飞没有返回分数，请读完整一句再试")

    words = []
    for item in root.findall(".//word"):
        if item.get("content") is None:
            continue
        dp = _num(item.get("dp_message"))
        phones = []
        for phone in item.findall(".//phone"):
            phones.append(
                {
                    "phoneme": phone.get("content") or "",
                    "accuracy": None,
                    "error": _DP_MESSAGE.get(int(_num(phone.get("dp_message")) or 0), ""),
                }
            )
        words.append(
            {
                "word": item.get("content") or "",
                "accuracy": _num(item.get("total_score")),
                "error_type": _DP_MESSAGE.get(int(dp or 0), ""),
                "phonemes": phones,
            }
        )

    return {
        "provider": "xunfei",
        "scores": scores,
        "words": words,
        "display_text": " ".join(item["word"] for item in words),
        "language": "en-US",
    }


def _convert_pcm(source, pcm) -> None:
    proc = audio_mod._run(
        [
            audio_mod.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
            "-f", "s16le", "-acodec", "pcm_s16le", str(pcm),
        ],
        timeout=120,
    )
    if proc.returncode != 0 or not pcm.exists() or pcm.stat().st_size < 100:
        detail = (proc.stderr or "").strip()[:200]
        raise XunfeiError(f"音频转码失败：{detail or '未知错误'}")


def _send_audio(ws, pcm_bytes: bytes) -> None:
    total = len(pcm_bytes)
    if not total:
        raise XunfeiError("音频是空的")

    offset = 0
    index = 0
    while offset < total:
        chunk = pcm_bytes[offset: offset + CHUNK_SIZE]
        offset += len(chunk)
        last = offset >= total
        if index == 0 and last:
            aus = 4
        elif index == 0:
            aus = 1
        elif last:
            aus = 4
        else:
            aus = 2
        ws.send(
            json.dumps(
                {
                    "business": {"cmd": "auw", "aus": aus},
                    "data": {
                        "status": 2 if last else 1,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    },
                },
                ensure_ascii=False,
            )
        )
        index += 1
        if not last:
            time.sleep(0.01)


def assess(audio_b64: object, mime: str, reference_text: str,
           config: dict | None = None) -> dict:
    text = str(reference_text or "").strip()
    if len(text) < 2:
        raise XunfeiError("跟读目标太短，先选一句英文再评测")
    if len(text) > 500:
        raise XunfeiError("跟读目标太长了，请选一句短一点的英文")

    if not isinstance(audio_b64, str) or not audio_b64.strip():
        raise XunfeiError("没有收到录音数据")
    if len(audio_b64) > 12_000_000:
        raise XunfeiError("录音太长了，请控制在 60 秒以内")

    config = config or config_mod.load()
    cfg = config_mod.effective_pronunciation(config)
    xf = cfg.get("xunfei") or {}
    if not xf.get("app_id") or not xf.get("api_key") or not xf.get("api_secret"):
        raise XunfeiError(_setup_message())

    payload = audio_b64.strip()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise XunfeiError("录音数据不完整，请重试") from exc
    if len(raw) < 800:
        raise XunfeiError("录音太短，请读完一句再停止")

    mime = (mime or "").split(";")[0].strip().lower()
    ext = _VOICE_EXT.get(mime, "webm")
    workdir = config_mod.workdir(config) / "_xunfei"
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / f"xf-{uuid.uuid4().hex}.{ext}"
    pcm = source.with_suffix(".pcm")

    try:
        try:
            source.write_bytes(raw)
        except OSError as exc:
            raise XunfeiError(f"保存录音失败：{exc}") from exc
        _convert_pcm(source, pcm)
        pcm_bytes = pcm.read_bytes()

        try:
            ws = websocket.create_connection(signed_url(xf["api_key"], xf["api_secret"]), timeout=20)
        except websocket.WebSocketBadStatusException as exc:
            raise XunfeiError(
                "讯飞握手失败（401/403）。请检查 APPID、APIKey、APISecret "
                "是否属于同一个应用，并确认已添加「语音评测（流式版）」服务。"
            ) from exc
        except websocket.WebSocketException as exc:
            raise XunfeiError(f"连接讯飞失败：{exc}") from exc

        try:
            ws.settimeout(60)
            ws.send(
                json.dumps(
                    {
                        "common": {"app_id": xf["app_id"]},
                        "business": {
                            "sub": "ise",
                            "ent": xf.get("ent") or "en_vip",
                            "category": "read_sentence",
                            "cmd": "ssb",
                            "text": "\ufeff" + text,
                            "tte": "utf-8",
                            "ttp_skip": True,
                            "aue": "raw",
                            "auf": "audio/L16;rate=16000",
                            "rstcd": "utf8",
                            "rst": "entirety",
                            "ise_unite": "1",
                            "extra_ability": "multi_dimension",
                        },
                        "data": {"status": 0},
                    },
                    ensure_ascii=False,
                )
            )
            _send_audio(ws, pcm_bytes)

            final_xml = ""
            while True:
                message = ws.recv()
                if not message:
                    break
                data = json.loads(message)
                code = data.get("code")
                if code not in (0, None):
                    raise XunfeiError(f"讯飞返回错误 {code}：{data.get('message') or ''}")
                chunk = (data.get("data") or {})
                encoded = chunk.get("data")
                if encoded:
                    final_xml = base64.b64decode(encoded).decode("utf-8", "replace")
                if chunk.get("status") == 2:
                    break
        except websocket.WebSocketTimeoutException as exc:
            raise XunfeiError("讯飞评测超时，请检查网络后重试") from exc
        except websocket.WebSocketException as exc:
            raise XunfeiError(f"讯飞连接中断：{exc}") from exc
        finally:
            try:
                ws.close()
            except Exception:
                pass

        if not final_xml:
            raise XunfeiError("讯飞没有返回评测结果")
        return parse_result(final_xml)
    finally:
        for path in (source, pcm):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
