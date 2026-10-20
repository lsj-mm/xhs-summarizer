#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本工具的第三方依赖装进项目内的 `_vendor/` 目录。

为什么不用 `pip install`：
    本机 DSH 沙箱下，pip 会先在自己的临时目录里解包 wheel，而这些临时目录
    创建时没有继承沙箱授予的访问权限（已用 ACL 诊断脚本确认：不是 ACL 缺失，
    而是机制问题）。结果是 pip 报 `[Errno 13] Permission denied`，装不进去。
    这里改成自己从 PyPI 下载 wheel、再用 zipfile 解压，全程只写工作区内的路径，
    于是不依赖任何临时目录，也不污染 Anaconda 的 site-packages。

用法：
    python bootstrap_deps.py            # 缺什么装什么
    python bootstrap_deps.py --upgrade  # 强制重新下载（升级 yt-dlp 时用）
    python bootstrap_deps.py --list     # 只看当前已装版本
"""

import argparse
import json
import logging
import platform
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

LOG = logging.getLogger("bootstrap")

PROJECT_ROOT = Path(__file__).resolve().parent
VENDOR_DIR = PROJECT_ROOT / "_vendor"
WHEEL_DIR = PROJECT_ROOT / "_wheels"

# yt-dlp 与小红书站点的解析规则强相关，锁版本便于复现；升级时改这里。
# 依赖清单。分三组，顺序无所谓，但必须覆盖 run.py 里会 import 的一切：
# 漏掉任何一个，工具箱在别的电脑上就会「第一次运行才发现缺包」。
#   - 下载与转码：yt-dlp、自带 ffmpeg 的 imageio-ffmpeg
#   - HTTP 客户端：requests 及其依赖（转写、总结、GitHub 接口都用它）
#   - Web 框架：网页界面靠它提供。这一组以前是从系统 Python 里借来的，
#     结果整包拷到别的电脑就起不来 —— 现在一并装进项目内，做到真正自包含。
# 依赖关系取自各包 wheel 里的 METADATA（Requires-Dist），升级某个包时请对照更新。
PACKAGES = [
    # 下载与转码
    ("yt-dlp", "2026.8.19"),
    ("imageio-ffmpeg", None),
    # HTTP 客户端
    ("requests", None),
    ("urllib3", None),
    ("certifi", None),
    ("charset-normalizer", None),
    ("idna", None),
    # 讯飞语音评测走 WebSocket
    ("websocket-client", None),
    # Web 框架（fastapi → starlette → anyio；fastapi → pydantic → pydantic-core）
    ("fastapi", None),
    ("starlette", None),
    ("pydantic", None),
    ("pydantic-core", None),
    ("annotated-types", None),
    ("annotated-doc", None),
    ("typing-extensions", None),
    ("typing-inspection", None),
    ("opentelemetry-api", None),
    ("anyio", None),
    ("h11", None),
    ("click", None),
    ("uvicorn", None),
]

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "xhs-summarizer-bootstrap/1.0"


def _wheel_rank(filename: str) -> int | None:
    """给 wheel 文件名打分，返回 None 表示这个轮子在本机装不了。

    打分越小越优先：纯 Python 轮子最稳，其次是本机 CPython 版本的原生轮子。
    """
    m = re.match(r"^[^-]+-[^-]+(?:-[^-]+)?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$", filename)
    if not m:
        return None

    py, abi, plat = m.group("py"), m.group("abi"), m.group("plat")
    machine = platform.machine().lower()
    if plat == "any":
        plat_ok = 0
    elif plat == "win_amd64" and machine in ("amd64", "x86_64"):
        plat_ok = 1
    else:
        return None

    cp = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if py in ("py3", "py2.py3"):
        py_ok = 0
    elif py == cp:
        py_ok = 1
    else:
        return None

    if abi == "none":
        abi_ok = 0
    elif abi == cp:
        abi_ok = 1
    elif abi == "abi3":
        abi_ok = 2
    else:
        return None

    return plat_ok * 100 + py_ok * 10 + abi_ok


def _pick_wheel(files: list[dict]) -> dict | None:
    candidates = []
    for f in files:
        if f.get("packagetype") != "bdist_wheel":
            continue
        rank = _wheel_rank(f["filename"])
        if rank is not None:
            candidates.append((rank, f))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def installed_versions() -> dict:
    manifest = VENDOR_DIR / ".installed.json"
    if manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8"))
    return {}


def install(force: bool = False, only: list[str] | None = None) -> dict:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    current = installed_versions()
    result = dict(current)

    for name, pinned in PACKAGES:
        if only and name not in only:
            continue
        if not force and name in current:
            LOG.info("%s 已就位 (%s)，跳过", name, current[name])
            continue

        meta = _fetch_json(PYPI_JSON.format(name=name))
        version = pinned or meta["info"]["version"]
        files = meta["releases"].get(version) or []
        wheel = _pick_wheel(files)
        if wheel is None:
            raise RuntimeError(f"{name}=={version} 没有适用于本机的 wheel，需要人工处理")

        dest = WHEEL_DIR / wheel["filename"]
        if dest.exists() and not force:
            LOG.info("%s==%s 复用已下载的 %s", name, version, dest.name)
        else:
            LOG.info("下载 %s==%s -> %s (%.1f MB)", name, version, dest.name, wheel["size"] / 1024 / 1024)
            _download(wheel["url"], dest)

        LOG.info("解压 %s 到 _vendor/", dest.name)
        with zipfile.ZipFile(dest) as zf:
            zf.extractall(VENDOR_DIR)

        result[name] = version

    (VENDOR_DIR / ".installed.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    # 日志写 stdout：Windows 上写 stderr 会让调用方（PowerShell 等）把正常输出
    # 当成错误记录，最终表现为「明明装成功了却返回退出码 1」。
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    # 中文日志在 GBK 控制台会乱码，这里强制按 UTF-8 输出，遇到无法编码的字符也不炸
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        LOG.debug("当前解释器不支持重设 stdout 编码，保持默认")
    parser = argparse.ArgumentParser(description="把依赖装进项目内的 _vendor/ 目录")
    parser.add_argument("--upgrade", "-u", action="store_true", help="强制重新下载并覆盖")
    parser.add_argument("--list", "-l", action="store_true", help="只列出已装版本")
    parser.add_argument("--only", nargs="*", default=None, help="只处理指定包，例如 --only yt-dlp")
    args = parser.parse_args(argv)

    if args.list:
        versions = installed_versions()
        if not versions:
            print("_vendor 还没有内容，先运行：python bootstrap_deps.py")
            return 0
        for name, version in versions.items():
            print(f"{name:20s} {version}")
        return 0

    versions = install(force=args.upgrade, only=args.only)
    print("\n完成，_vendor/ 现有：")
    for name, version in sorted(versions.items()):
        print(f"  {name:20s} {version}")
    print(f"\n目录：{VENDOR_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
