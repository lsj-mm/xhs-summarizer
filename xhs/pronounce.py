# -*- coding: utf-8 -*-
"""发音评测分发：Azure Speech Pronunciation Assessment 与讯飞语音评测。

为什么用 REST / WebSocket 而不是 SDK：
    Azure 的 Python SDK 要额外装包，而发音评测的 REST 接口只需要 key + region；
    讯飞官方的流式评测只有 WebSocket，项目里用轻量的 websocket-client 直接连。
    项目里已经有 requests、websocket-client 和自带 ffmpeg，符合「自包含」的取舍。

限制：
    - 这是「朗读评测」，需要一段参考文本（ReferenceText）；
    - 音频要转成 16kHz / 16bit / 单声道 PCM；
    - 每次调用都会产生 Azure 费用或消耗讯飞免费额度/套餐次数。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid

import requests

from . import audio as audio_mod
from . import config as config_mod
from . import xunfei as xunfei_mod

LOG = logging.getLogger(__name__)

_VOICE_EXT = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


class PronounceError(RuntimeError):
    """可以直接展示给用户的错误。"""


def _num(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _setup_message() -> str:
    return (
        "还没有配置 Azure 发音评测。\n"
        "1. 在 Azure 门户创建 Speech 资源，拿到「密钥」和「区域」（例如 eastasia）；\n"
        "2. 在「编程英语」页面的「Azure 发音评分」里填 key 和 region，"
        "或设置环境变量 AZURE_SPEECH_KEY / AZURE_SPEECH_REGION；\n"
        "3. 重启服务后再点「发音评分」。"
    )


def assess(audio_b64: object, mime: str, reference_text: str,
           config: dict | None = None) -> dict:
    """对一段朗读音频做 Azure 发音评测。"""
    text = str(reference_text or "").strip()
    if len(text) < 2:
        raise PronounceError("跟读目标太短，先选一句英文再评测")
    if len(text) > 500:
        raise PronounceError("跟读目标太长了，请选一句短一点的英文")

    if not isinstance(audio_b64, str) or not audio_b64.strip():
        raise PronounceError("没有收到录音数据")
    if len(audio_b64) > 12_000_000:
        raise PronounceError("录音太长了，请控制在 60 秒以内")

    config = config or config_mod.load()
    cfg = config_mod.effective_pronunciation(config)
    if cfg["provider"] == "xunfei":
        try:
            return xunfei_mod.assess(audio_b64, mime, text, config)
        except xunfei_mod.XunfeiError as exc:
            raise PronounceError(str(exc)) from exc
    if cfg["provider"] != "azure":
        raise PronounceError(f"暂不支持的发音评测厂商：{cfg['provider']!r}")
    azure = cfg["azure"]
    if not azure["key"] or not azure["region"]:
        raise PronounceError(_setup_message())

    payload = audio_b64.strip()
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise PronounceError("录音数据不完整，请重试") from exc
    if len(raw) < 800:
        raise PronounceError("录音太短，请读完一句再停止")

    mime = (mime or "").split(";")[0].strip().lower()
    ext = _VOICE_EXT.get(mime, "webm")
    workdir = config_mod.workdir(config) / "_pronounce"
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / f"pron-{uuid.uuid4().hex}.{ext}"
    wav = source.with_suffix(".wav")

    try:
        try:
            source.write_bytes(raw)
        except OSError as exc:
            raise PronounceError(f"保存录音失败：{exc}") from exc

        proc = audio_mod._run(
            [
                audio_mod.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(wav),
            ],
            timeout=120,
        )
        if proc.returncode != 0 or not wav.exists() or wav.stat().st_size < 100:
            detail = (proc.stderr or "").strip()[:200]
            raise PronounceError(f"音频转码失败：{detail or '未知错误'}")

        assessment = {
            "ReferenceText": text,
            "GradingSystem": "HundredMark",
            "Granularity": "Phoneme",
            "EnableMiscue": True,
        }
        header = base64.b64encode(
            json.dumps(assessment, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        region = azure["region"].strip().lower().replace(" ", "")
        url = (
            f"https://{region}.stt.speech.microsoft.com"
            "/speech/recognition/conversation/cognitiveservices/v1"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": azure["key"],
            "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
            "Accept": "application/json",
            "Pronunciation-Assessment": header,
        }
        try:
            resp = requests.post(
                url,
                params={"language": azure["language"] or "en-US", "format": "detailed"},
                headers=headers,
                data=wav.read_bytes(),
                timeout=60,
            )
        except requests.RequestException as exc:
            raise PronounceError(f"请求 Azure 失败：{exc}") from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise PronounceError(
                "Azure 拒绝了 key 或区域（401/403）。请检查 AZURE_SPEECH_KEY "
                "和 AZURE_SPEECH_REGION 是否对应同一个 Speech 资源。"
            )
        if resp.status_code == 429:
            raise PronounceError("Azure 触发限流（429），稍后再试。")
        if resp.status_code != 200:
            snippet = (resp.text or "").strip()[:300]
            raise PronounceError(f"Azure 返回 HTTP {resp.status_code}：{snippet or '无内容'}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise PronounceError(f"Azure 返回的不是 JSON：{resp.text[:200]}") from exc

        status = data.get("RecognitionStatus") or ""
        if status != "Success":
            raise PronounceError(f"Azure 没有识别成功（RecognitionStatus={status or '空'}）")
        nbest = data.get("NBest") or []
        if not nbest:
            raise PronounceError("Azure 没有返回评分结果，请读完整一句再试")
        best = nbest[0] or {}
        pa = best.get("PronunciationAssessment") or {}

        words = []
        for item in best.get("Words") or []:
            if not isinstance(item, dict):
                continue
            wp = item.get("PronunciationAssessment") or {}
            phonemes = []
            for ph in item.get("Phonemes") or []:
                if not isinstance(ph, dict):
                    continue
                pp = ph.get("PronunciationAssessment") or {}
                phonemes.append(
                    {
                        "phoneme": ph.get("Phoneme") or "",
                        "accuracy": _num(pp.get("AccuracyScore")),
                    }
                )
            words.append(
                {
                    "word": item.get("Word") or "",
                    "accuracy": _num(wp.get("AccuracyScore")),
                    "error_type": wp.get("ErrorType") or "",
                    "phonemes": phonemes,
                }
            )

        scores = {
            "pronunciation": _num(pa.get("PronScore")),
            "accuracy": _num(pa.get("AccuracyScore")),
            "fluency": _num(pa.get("FluencyScore")),
            "completeness": _num(pa.get("CompletenessScore")),
            "prosody": _num(pa.get("ProsodyScore")),
        }
        return {
            "provider": "azure",
            "scores": scores,
            "words": words,
            "display_text": best.get("DisplayText") or data.get("DisplayText") or "",
            "language": azure["language"] or "en-US",
        }
    finally:
        for path in (source, wav):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
