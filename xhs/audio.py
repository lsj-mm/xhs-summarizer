# -*- coding: utf-8 -*-
"""音频处理：抽音轨、转成 ASR 友好的格式、按需切片。

为什么要重编码而不是直接上传原视频：
    1. 原视频动辄几十上百 MB，多数云端转写接口有 20~25MB 上限；
    2. 16kHz 单声道 64kbps 的 mp3 已经足够语音识别，体积只有原来的几十分之一；
    3. 长音频必须切片（按时间切，不按字节切），否则会被接口拒或超时。
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)

# 云端接口普遍卡在 25MB，留出余量
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)")


class AudioError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    """优先用 _vendor 里随包自带的 ffmpeg，避免要求用户自己装。"""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise AudioError("找不到 ffmpeg：请先运行 python bootstrap_deps.py 安装依赖") from exc


def _run(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    # 不带 shell，避免中文路径/空格被再次解释；编码固定 utf-8，防止 GBK 控制台乱码
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        raise AudioError(f"无法执行 ffmpeg（{args[0]}）：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"ffmpeg 执行超时（{timeout} 秒）：{' '.join(args[:6])} …") from exc


def duration_seconds(path: Path) -> float | None:
    """从 ffmpeg 的 stderr 里读时长。

    不依赖 ffprobe：imageio-ffmpeg 只带 ffmpeg，少一个依赖就少一类环境问题。
    """
    result = _run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], timeout=120)
    text = (result.stderr or "") + (result.stdout or "")
    match = _DURATION_RE.search(text)
    if not match:
        LOG.debug("读不出时长：%s", path.name)
        return None
    hours, minutes, secs, frac = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + float(f"0.{frac}")


def extract_audio(source: Path, workdir: Path, emit=None) -> Path:
    """抽音轨并转成 16kHz 单声道 mp3。"""
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / f"{source.stem}.asr.mp3"
    if emit:
        emit("正在提取音轨并转码…")
    result = _run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k",
            str(target),
        ]
    )
    if result.returncode != 0 or not target.exists():
        raise AudioError(f"提取音轨失败：{(result.stderr or '').strip()[:400] or '未知错误'}")
    LOG.info("音轨已生成：%s（%.1f MB）", target.name, target.stat().st_size / 1024 / 1024)
    return target


def split_audio(audio: Path, segment_seconds: int, workdir: Path) -> list[Path]:
    """按时间切片。切片用 -c copy，不重新编码，所以很快。"""
    workdir.mkdir(parents=True, exist_ok=True)
    pattern = workdir / f"{audio.stem}.part%03d.mp3"
    result = _run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(audio),
            "-f", "segment", "-segment_time", str(int(segment_seconds)),
            "-reset_timestamps", "1", "-c", "copy",
            str(pattern),
        ]
    )
    if result.returncode != 0:
        raise AudioError(f"切分音频失败：{(result.stderr or '').strip()[:400] or '未知错误'}")
    parts = sorted(workdir.glob(f"{audio.stem}.part*.mp3"))
    if not parts:
        raise AudioError("切分音频后没有产出任何片段")
    return parts


def prepare_audio(source: Path, workdir: Path, config: dict, emit=None) -> dict:
    """抽音轨 + 按需切片，返回交给 ASR 的片段列表。"""
    asr_cfg = config.get("asr") or {}
    segment_seconds = int(asr_cfg.get("segment_seconds") or 600)

    audio = extract_audio(source, workdir, emit)
    total = duration_seconds(audio)
    size = audio.stat().st_size

    needs_split = size > MAX_UPLOAD_BYTES or (total and total > segment_seconds)
    if needs_split:
        if emit:
            reason = "体积超限" if size > MAX_UPLOAD_BYTES else f"时长超过 {segment_seconds} 秒"
            emit(f"音频需要切片（{reason}），正在切分…")
        parts = split_audio(audio, segment_seconds, workdir)
        if emit:
            emit(f"已切成 {len(parts)} 段，逐段转写")
    else:
        parts = [audio]

    return {
        "audio": audio,
        "parts": parts,
        "duration": total,
        "bytes": size,
        "segment_seconds": segment_seconds,
    }
