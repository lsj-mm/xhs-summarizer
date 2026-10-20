# -*- coding: utf-8 -*-
"""下载层：包一层 yt-dlp，把它的日志翻译成前端进度。

为什么不做自己的解析器：
    小红书的直链带签名、且规则经常变，yt-dlp 的维护速度远超自己逆向，
    所以这里只负责「配置、cookie、日志适配、异常翻译」。

两个实际踩过的坑（都在这里处理掉了）：
1. 系统代理：yt-dlp 不像 requests 那样按 ProxyOverride 绕过 127.*，本机开着
   代理时连直连都会被拦。默认显式禁用代理，需要时再打开。
2. 浏览器 cookie 读取失败（浏览器没关、数据库被锁、临时目录不可写）会直接
   中断整个下载。这里改成「读不到 cookie 就不带 cookie 再试一次」。
"""

from __future__ import annotations

import logging
from pathlib import Path

LOG = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """下载失败，message 里带上人能看懂的原因和下一步建议。"""


class _YdlLogger:
    """把 yt-dlp 的日志转成进度回调，同时不吞掉它自己的 warning/error。"""

    def __init__(self, emit):
        self._emit = emit or (lambda message: None)

    def debug(self, message: str) -> None:
        text = str(message).strip()
        if text.startswith("[debug]"):
            return
        if text:
            self._emit(text)

    def info(self, message: str) -> None:
        self.debug(message)

    def warning(self, message: str) -> None:
        LOG.warning("yt-dlp: %s", message)
        self._emit(f"警告：{message}")

    def error(self, message: str) -> None:
        LOG.error("yt-dlp: %s", message)
        self._emit(f"错误：{message}")


def _ffmpeg_location() -> str:
    """yt-dlp 合并/转封装需要 ffmpeg，用项目内 _vendor 里带的那一份。"""
    try:
        import imageio_ffmpeg

        return str(Path(imageio_ffmpeg.get_ffmpeg_exe()).parent)
    except Exception as exc:  # pragma: no cover - 只在依赖缺失时走到
        LOG.warning("拿不到内置 ffmpeg（%s），交给 yt-dlp 自己找系统 ffmpeg", exc)
        return ""


def build_options(workdir: Path, config: dict, emit=None, use_cookies: bool = True) -> dict:
    download_cfg = config.get("download") or {}
    options: dict = {
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "format": download_cfg.get("format") or "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": _YdlLogger(emit),
        "retries": 3,
        "socket_timeout": int(download_cfg.get("timeout_seconds") or 120),
        "ignoreerrors": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.xiaohongshu.com/",
        },
    }

    if use_cookies:
        browser = (download_cfg.get("cookies_from_browser") or "").strip()
        cookie_file = (download_cfg.get("cookies_file") or "").strip()
        if cookie_file:
            options["cookiefile"] = cookie_file
        elif browser:
            options["cookiesfrombrowser"] = (browser,)

    proxy = (download_cfg.get("proxy") or "").strip()
    if proxy:
        options["proxy"] = proxy
    elif not download_cfg.get("use_system_proxy"):
        # 显式置空 = 不使用任何代理（yt-dlp 内部会翻译成 __noproxy__）。
        # 为什么默认要禁掉系统代理：本机常见开着 127.0.0.1:7897 这类代理，
        # 而 yt-dlp 不像 requests 那样按系统设置里的 ProxyOverride 绕过 127.*，
        # 于是连本地/直连都会被代理拦掉；更现实的是小红书属于国内站点，
        # 走海外节点容易触发风控。需要时可在设置里打开「使用系统代理下载」。
        options["proxy"] = ""

    location = _ffmpeg_location()
    if location:
        options["ffmpeg_location"] = location

    return options


_NO_VIDEO_HINTS = (
    "no video formats found",
    "unsupported url",
    "there's no video",
    "no media data",
    "unable to extract",
    "no video",
)

_LOGIN_HINTS = ("login", "sign in", "需要登录", "账号")

_COOKIE_HINTS = (
    "cookie",
    "winerror 5",
    "permission denied",
    "decrypt",
    "database is locked",
    "could not copy",
)


def _matches(message: str, hints: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in hints)


def _explain(message: str) -> str:
    if _matches(message, _NO_VIDEO_HINTS):
        return (
            "没解析到视频。这条很可能是图文笔记（只有图片和文字），"
            "或者该笔记需要登录才能看。图文笔记工具会自动改用正文兜底。"
        )
    if _matches(message, _LOGIN_HINTS):
        return (
            "看起来被要求登录了。请在网页设置里把「浏览器 Cookie」选成你已登录小红书的浏览器"
            "（如 edge / chrome），并确保该浏览器已关闭后重试。"
        )
    return f"下载失败：{message}"


def _download_once(url: str, workdir: Path, options: dict) -> dict:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            # 只调一次 extract_info：先探测再下载会多打一次请求，
            # 而且 process_ie_result 属于 yt-dlp 内部接口，跨版本容易坏。
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("下载器没有返回任何信息，可能是不支持的链接")
            if "entries" in info:
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise DownloadError("链接里没有可下载的内容")
                info = entries[0]
    except DownloadError:
        raise
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_explain(str(exc))) from exc
    except Exception as exc:  # yt-dlp 会抛各种非 DownloadError 的异常
        raise DownloadError(_explain(str(exc))) from exc

    path = _resolve_path(info, workdir)
    if path is None or not path.exists():
        raise DownloadError(f"下载似乎成功了，但找不到落地文件（工作目录 {workdir}）")

    LOG.info("下载完成：%s（%.1f MB）", path.name, path.stat().st_size / 1024 / 1024)
    return {
        "path": path,
        "title": (info.get("title") or "").strip(),
        "author": (info.get("uploader") or info.get("creator") or "").strip(),
        "duration": info.get("duration"),
        "description": (info.get("description") or "").strip(),
        "webpage_url": info.get("webpage_url") or url,
        "note_id": str(info.get("id") or ""),
        "size": path.stat().st_size,
    }


def download_media(url: str, workdir: Path, config: dict, emit=None) -> dict:
    """下载单条笔记的媒体。返回文件路径与元信息。"""
    from . import sandbox

    # yt-dlp 读浏览器 cookie 时会用 tempfile，受限环境里那一步会失败
    sandbox.ensure_usable_tempdir()

    download_cfg = config.get("download") or {}
    wants_cookies = bool(
        (download_cfg.get("cookies_file") or "").strip()
        or (download_cfg.get("cookies_from_browser") or "").strip()
    )
    workdir.mkdir(parents=True, exist_ok=True)

    # 第一次带 cookie，若因为 cookie 本身失败，就退回不带 cookie 再来一次：
    # 公开笔记不需要登录，为了一个读不到的 cookie 整条任务失败不划算。
    plans = [True, False] if wants_cookies else [True]
    last_error: DownloadError | None = None
    cookie_issue = False

    for index, use_cookies in enumerate(plans):
        options = build_options(workdir, config, emit, use_cookies=use_cookies)
        try:
            return _download_once(url, workdir, options)
        except DownloadError as exc:
            last_error = exc
            if index == 0 and len(plans) > 1 and _matches(str(exc), _COOKIE_HINTS):
                cookie_issue = True
                LOG.warning("带 cookie 下载失败，尝试不带 cookie 重试：%s", exc)
                if emit:
                    emit("读取浏览器 cookie 失败（浏览器没关或数据库被占用），改为不带 cookie 重试…")
                continue
            raise

    detail = str(last_error)
    if cookie_issue:
        detail += (
            "\n（另外：读取浏览器 cookie 也失败了。请把要用的浏览器完全退出后再试，"
            "或在设置里改为「不使用 cookie」。）"
        )
    raise DownloadError(detail)


def _resolve_path(info: dict, workdir: Path) -> Path | None:
    """找实际落地的文件。

    请求了 bestaudio 时可能拿到 .m4a/.webm，prepare_filename 的猜测不一定准，
    所以优先用 yt-dlp 自己记录的最终路径。
    """
    for item in info.get("requested_downloads") or []:
        candidate = item.get("filepath") or item.get("_filename")
        if candidate:
            path = Path(candidate)
            if path.exists():
                return path
            # 转封装之后扩展名可能变了（.webm -> .mp4）
            for ext in (".mp4", ".m4a", ".mp3", ".webm", ".mkv"):
                if path.with_suffix(ext).exists():
                    return path.with_suffix(ext)

    guessed = info.get("_filename") or info.get("filepath")
    if guessed and Path(guessed).exists():
        return Path(guessed)

    note_id = str(info.get("id") or "")
    if note_id:
        matches = sorted(workdir.glob(f"{note_id}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None
