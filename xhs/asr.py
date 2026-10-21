# -*- coding: utf-8 -*-
"""云端语音转写：OpenAI 兼容协议 + 阿里云百炼协议。

两个协议路径：
- `openai_audio`：POST {base_url}/audio/transcriptions，multipart 上传文件。
  硅基流动、OpenAI、以及大多数自建服务都走这条。
- `dashscope_chat`：POST {base_url}/chat/completions，音频以 base64 放进消息体。

兼容性处理：
    不同厂商对 `response_format` 和 `language` 的支持不一致，硬用一个组合会
    在部分厂商上直接 400。这里按「信息量从多到少」依次降级重试，
    并在日志里说明最后用了哪个组合，出问题时好定位。
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)

TIMEOUT = 600
# verbose_json 能拿到分段和时间戳；后面两档是给不支持它的厂商兜底
_OPENAI_ATTEMPTS = (
    {"response_format": "verbose_json", "with_language": True},
    {"response_format": "json", "with_language": True},
    {"response_format": "json", "with_language": False},
)


class AsrError(RuntimeError):
    pass


def _friendly_error(status: int, body: str, cfg: dict) -> str:
    snippet = (body or "").strip()[:300]
    if status == 401:
        return (
            f"转写接口拒绝了这个 key（401）。当前厂商：{cfg.get('label')}。"
            "请在网页设置里重新填写 API key。"
        )
    if status == 402:
        return f"转写账户余额不足（402）。原始信息：{snippet}"
    if status == 404:
        return f"模型 {cfg.get('model')!r} 不存在或该账号无权访问（404）。原始信息：{snippet}"
    if status == 413:
        return "上传的音频太大被拒绝（413）。可在设置里把「切片时长」调小一些再试。"
    if status == 429:
        return f"触发限流（429），稍后重试即可。原始信息：{snippet}"
    return f"转写接口报错（HTTP {status}）：{snippet or '无返回内容'}"


def _post_with_retry(url: str, *, headers: dict, timeout: int, emit=None,
                     attempts: int = 4, **kwargs) -> requests.Response:
    """对限流和服务端错误做多次重试；4xx（除 429）不重试，避免浪费额度。

    为什么重试次数要给够：5xx 基本都是服务端瞬时问题，重试往往就过了；
    而这里失败一次，整个视频的转写（可能已成功好几段）就全废了，代价太大。
    """
    last: requests.Response | None = None
    last_exc: Exception | None = None
    attempts = max(1, int(attempts))

    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            LOG.warning("请求 %s 失败（第 %d/%d 次）：%s", url, attempt, attempts, exc)
        else:
            last = resp
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            LOG.warning("接口返回 HTTP %s（第 %d/%d 次）",
                        resp.status_code, attempt, attempts)
        if attempt < attempts:
            wait = min(2 ** attempt, 16)  # 2、4、8、16 秒，封顶 16 秒
            if emit:
                emit(f"接口异常，{wait} 秒后重试（第 {attempt + 1}/{attempts} 次）…")
            time.sleep(wait)

    if last is not None:
        return last
    raise AsrError(f"请求 {url} 连续 {attempts} 次失败：{last_exc}")


def _extract_segments(payload: dict) -> list[dict]:
    segments = []
    for item in payload.get("segments") or []:
        if not isinstance(item, dict):
            continue
        segments.append(
            {
                "start": item.get("start"),
                "end": item.get("end"),
                "text": (item.get("text") or "").strip(),
            }
        )
    return [s for s in segments if s["text"]]


def _openai_audio(path: Path, cfg: dict, emit=None) -> dict:
    url = f"{cfg['base_url']}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    last_body = ""

    for index, attempt in enumerate(_OPENAI_ATTEMPTS):
        data: dict = {"model": cfg["model"], "response_format": attempt["response_format"]}
        if attempt["with_language"] and cfg.get("language"):
            data["language"] = cfg["language"]
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "audio/mpeg")}
            resp = _post_with_retry(url, headers=headers, timeout=TIMEOUT, emit=emit,
                                    attempts=cfg.get("retry_attempts", 4), data=data, files=files)

        if resp.status_code == 200:
            try:
                payload = resp.json()
            except json.JSONDecodeError as exc:
                raise AsrError(f"转写接口返回的不是 JSON：{resp.text[:200]}") from exc
            text = (payload.get("text") or "").strip()
            if index > 0 and emit:
                emit(f"提示：该厂商不支持 verbose_json，已用 {attempt['response_format']} 模式")
            return {
                "text": text,
                "segments": _extract_segments(payload),
                "raw": payload,
            }

        last_body = resp.text
        # 只有「参数不被支持」才值得换参数重试，鉴权/额度问题重试没意义
        if resp.status_code == 400 and index < len(_OPENAI_ATTEMPTS) - 1:
            LOG.info("转写接口拒绝参数组合 %s，降级重试", data)
            continue
        raise AsrError(_friendly_error(resp.status_code, last_body, cfg))

    raise AsrError(_friendly_error(400, last_body, cfg))


def _dashscope_chat(path: Path, cfg: dict, emit=None) -> dict:
    """qwen3-asr-flash 走 OpenAI 兼容的对话接口，音频放在消息体里。"""
    url = f"{cfg['base_url']}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    audio_url = f"data:audio/mpeg;base64,{encoded}"

    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [{"type": "input_audio", "input_audio": {"data": audio_url}}],
            }
        ],
        "stream": False,
    }
    if cfg.get("language"):
        payload["asr_options"] = {"language": cfg["language"], "enable_lid": True}

    resp = _post_with_retry(url, headers=headers, timeout=TIMEOUT, emit=emit,
                            attempts=cfg.get("retry_attempts", 4), json=payload)
    if resp.status_code == 400 and "asr_options" in payload:
        # 部分模型不认识 asr_options，去掉再来一次
        LOG.info("接口拒绝 asr_options，去掉后重试")
        payload.pop("asr_options")
        resp = _post_with_retry(url, headers=headers, timeout=TIMEOUT, emit=emit,
                                attempts=cfg.get("retry_attempts", 4), json=payload)
    if resp.status_code != 200:
        raise AsrError(_friendly_error(resp.status_code, resp.text, cfg))

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AsrError(f"转写接口返回结构不符合预期：{resp.text[:300]}") from exc

    # 兼容 content 是字符串或分段数组两种情况
    if isinstance(content, list):
        text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    else:
        text = str(content)
    return {"text": text.strip(), "segments": [], "raw": data}


def transcribe_file(path: Path, cfg: dict, emit=None) -> dict:
    if not cfg.get("api_key"):
        raise AsrError(
            f"没有配置转写 API key（当前厂商：{cfg.get('label')}）。"
            "请在网页右上角「设置」里填写，或设置环境变量 "
            f"{cfg.get('provider', '').upper()}_API_KEY。"
        )
    protocol = cfg.get("protocol")
    if protocol == "openai_audio":
        return _openai_audio(path, cfg, emit)
    if protocol == "dashscope_chat":
        return _dashscope_chat(path, cfg, emit)
    raise AsrError(f"未知的转写协议：{protocol!r}")


def transcribe_parts(
    parts: list[Path],
    cfg: dict,
    emit=None,
    on_part=None,
) -> dict:
    """逐段转写并拼起来。切片后的时间戳要加上前面各段的偏移。

    `on_part(done, total)` 在每段开始前调用，让上层能报真实进度
    （而不是提前一次性把进度发完）。
    """
    texts: list[str] = []
    segments: list[dict] = []
    failures: list[dict] = []
    offset = 0.0
    total = len(parts)

    for index, part in enumerate(parts, start=1):
        if on_part:
            on_part(index - 1, total)
        if emit:
            emit(f"转写第 {index}/{total} 段（{part.name}，{part.stat().st_size / 1024:.0f} KB）…")
        try:
            result = transcribe_file(part, cfg, emit)
        except AsrError as exc:
            # 单段失败不能把整条视频的成果一起丢掉：记下来，继续转后面的段落
            failures.append({"index": index, "total": total, "name": part.name, "error": str(exc)})
            LOG.warning("第 %d/%d 段转写失败：%s", index, total, exc)
            if emit:
                emit(f"第 {index}/{total} 段转写失败（{exc}），继续处理后面的段落")
        else:
            texts.append(result["text"])
            for seg in result["segments"]:
                start = seg.get("start")
                end = seg.get("end")
                segments.append(
                    {
                        "start": None if start is None else float(start) + offset,
                        "end": None if end is None else float(end) + offset,
                        "text": seg["text"],
                    }
                )
        # 段末不再回报一次进度：那会让文案变成「正在转写第 N+1/N 段」，
        # 而且紧随其后的「转写完成」事件本来就会把进度推到 100%
        offset += float(cfg.get("segment_seconds") or 600)
        if index < total:
            time.sleep(1)  # 段与段之间稍作停顿，别把接口打得太急

    text = "\n".join(t for t in texts if t).strip()
    if not text and not segments:
        reasons = "；".join(f"第 {f['index']}/{f['total']} 段：{f['error']}" for f in failures)
        raise AsrError(
            f"全部 {total} 段都没能转写成功。"
            + (reasons or "转写结果为空（音频里可能没有人声，或该模型不支持这个音频格式）")
        )
    return {
        "text": text,
        "segments": segments,
        "failures": failures,
        "model": f"{cfg.get('label')}/{cfg.get('model')}",
    }


def selftest(cfg: dict, workdir: Path, emit=None) -> dict:
    """用一段 1.5 秒静音验证 key、模型名和网络是否都通。

    静音是故意的：不花钱、不产生内容，只要接口返回 200 就说明配置没问题。
    """
    from . import audio as audio_mod

    workdir.mkdir(parents=True, exist_ok=True)
    probe = workdir / "selftest_silence.mp3"
    result = audio_mod._run(
        [
            audio_mod.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1.5",
            "-c:a", "libmp3lame", "-b:a", "64k", str(probe),
        ]
    )
    if result.returncode != 0 or not probe.exists():
        raise AsrError(f"生成测试音频失败：{(result.stderr or '').strip()[:200]}")

    if emit:
        emit("用 1.5 秒静音测试转写接口…")
    outcome = transcribe_file(probe, cfg, emit)
    return {
        "ok": True,
        "provider": cfg.get("label"),
        "model": cfg.get("model"),
        "text": outcome["text"][:80],
    }
