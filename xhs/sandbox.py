# -*- coding: utf-8 -*-
"""临时目录兼容性处理。

背景（本机实测可复现）：
    Windows 上 Python 用 `os.mkdir(path, 0o700)` 建目录时，会顺便写一个显式的
    权限表，把从父目录继承来的权限项**替换掉**。在 DSH 这类受限沙箱里，父目录
    的授权项正是让进程能写文件的那一项，结果就是：目录能建出来，但往里写文件
    直接 `[Errno 13] Permission denied`。
    而 `tempfile.mkdtemp()` 恰好用的是 0o700 —— 于是 pip 解包 wheel、yt-dlp 复制
    浏览器 cookie 库这类操作统统失败，且报错信息完全看不出真正原因。

处理办法：
    启动时探测一次。只有真的复现了这个现象才打补丁，把 `tempfile.mkdtemp`
    换成「用默认权限建目录」的实现；正常机器上不做任何改动。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile

LOG = logging.getLogger(__name__)

_patched = False
_checked = False


def _writable(directory: str) -> bool:
    """真往目录里写一个文件，而不是只看权限位——只有实测才靠得住。"""
    probe = os.path.join(directory, ".write-probe")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _patch_mkdtemp() -> None:
    """把 mkdtemp 换成默认权限版本。

    唯一的语义差异：不再把目录收紧成 0o700。对本地小工具来说这没有实际风险
    （目录本来就是临时目录，且父目录已有权限约束），换来的是沙箱/受限环境下能用。
    """
    global _patched
    if _patched:
        return

    original = tempfile.mkdtemp

    def mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | None = None) -> str:
        suffix = suffix or ""
        prefix = prefix if prefix is not None else tempfile.gettempprefix()
        base = dir or tempfile.gettempdir()
        for _ in range(1000):
            candidate = os.path.join(base, prefix + next(tempfile._get_candidate_names()) + suffix)
            try:
                # 关键：不传 mode，让新目录继承父目录的权限项
                os.mkdir(candidate)
            except FileExistsError:
                continue
            except OSError as exc:
                raise FileExistsError(f"无法在 {base} 下创建临时目录：{exc}") from exc
            return candidate
        raise FileExistsError(f"在 {base} 下尝试 1000 次都没能找到可用的临时目录名")

    mkdtemp.__doc__ = (original.__doc__ or "") + "\n（已被 xhs.sandbox 替换为默认权限版本）"
    tempfile.mkdtemp = mkdtemp  # type: ignore[assignment]
    _patched = True
    LOG.warning(
        "检测到临时目录不可写（0o700 建目录会丢权限），已启用兼容补丁；"
        "这会影响所有使用 tempfile 的库（yt-dlp 读浏览器 cookie、pip 解包等）"
    )


def ensure_usable_tempdir() -> bool:
    """确保 tempfile 建出的目录能写文件。返回 True 表示打了补丁。"""
    global _checked
    if _checked:
        return _patched

    try:
        probe_dir = tempfile.mkdtemp(prefix="xhs-probe-")
    except OSError as exc:
        # 连目录都建不出来，只能先打补丁再试
        LOG.warning("创建探测目录失败（%s），直接启用兼容补丁", exc)
        _patch_mkdtemp()
        _checked = True
        return True

    try:
        ok = _writable(probe_dir)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    if not ok:
        _patch_mkdtemp()
        # 补丁生效后再验一次，确认问题真的解决了，而不是换个地方继续失败
        try:
            verify_dir = tempfile.mkdtemp(prefix="xhs-verify-")
            verified = _writable(verify_dir)
            shutil.rmtree(verify_dir, ignore_errors=True)
            if not verified:
                LOG.error("兼容补丁未能解决临时目录不可写的问题，yt-dlp 读取浏览器 cookie 可能仍会失败")
        except OSError as exc:
            LOG.error("补丁后的临时目录仍然不可用：%s", exc)

    _checked = True
    return _patched
