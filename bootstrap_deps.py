#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本工具的第三方依赖装进项目内的 `_vendor/` 目录。

为什么不用 `pip install`：
    本机 DSH 沙箱下，pip 会先在自己的临时目录里解包 wheel，而这些临时目录
    创建时没有继承沙箱授予的访问权限（已用 ACL 诊断脚本确认：不是 ACL 缺失，
    而是机制问题）。结果是 pip 报 `[Errno 13] Permission denied`，装不进去。
    这里改成自己从 PyPI 下载 wheel、再用 zipfile 解压，全程只写工作区内的路径，
    于是不依赖任何临时目录，也不污染 Anaconda 的 site-packages。

顺带装 OpenCodeReview：
    代码审查用的 `ocr.exe` 不在 PyPI 上，也不随仓库提交（55 MB，且遵循上游
    许可证）。以前只能手工放文件，换电脑就会「依赖都装好了，一点代码审查就
    报找不到 ocr.exe」。现在它和 wheel 一起在这里下载，并校验官方 sha256。

用法：
    python bootstrap_deps.py            # 缺什么装什么（含 ocr 二进制）
    python bootstrap_deps.py --upgrade  # 强制重新下载（升级 yt-dlp 时用）
    python bootstrap_deps.py --list     # 只看当前已装版本
    python bootstrap_deps.py --skip-ocr # 只要 Python 依赖，暂不下 ocr
    python bootstrap_deps.py --only ocr # 只补 ocr 二进制
"""

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
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

# ---------------------------------------------------------------- OpenCodeReview
# 升级时改 OCR_VERSION，然后跑 `python bootstrap_deps.py --only ocr --upgrade`。
# 官方在同一个 release 下提供 sha256sum.txt，下载后必须对得上才落盘，
# 避免半截文件或中间人替换被当成可执行文件跑起来。
OCR_REPO = "alibaba/open-code-review"
OCR_VERSION = "v1.13.3"
OCR_RELEASE_BASE = "https://github.com/{repo}/releases/download/{version}"
OCR_DIRNAME = "ocr"
OCR_MANIFEST = "installed.json"
# 低于这个大小基本可以断定是没下完的残文件（真实包约 55 MB）
OCR_MIN_BYTES = 20 * 1024 * 1024

# Go 的 GOOS/GOARCH 命名和常见写法不一样，这里统一映射
_OCR_OS = {
    "windows": "windows", "win32": "windows", "win": "windows",
    "linux": "linux",
    "darwin": "darwin", "macos": "darwin", "mac": "darwin",
}
_OCR_ARCH = {
    "amd64": "amd64", "x86_64": "amd64", "x64": "amd64", "x86-64": "amd64",
    "arm64": "arm64", "aarch64": "arm64",
}


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


def ocr_asset(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    """返回 (发布包名, 落地文件名)；看不懂的平台抛 RuntimeError 让人工处理。

    单独拆成纯函数，好让 tools/check_review.py 在不下载的情况下验证映射。
    """
    osname = (system or platform.system() or "").strip().lower()
    arch = (machine or platform.machine() or "").strip().lower()
    goos, goarch = _OCR_OS.get(osname), _OCR_ARCH.get(arch)
    if not goos or not goarch:
        raise RuntimeError(
            f"OpenCodeReview 没有适配当前平台（{osname or '?'}/{arch or '?'}）的发布包。"
            "请从 https://github.com/alibaba/open-code-review/releases 手工下载，"
            "再用环境变量 OCR_BIN 指向它。"
        )
    asset = f"opencodereview-{goos}-{goarch}"
    if goos == "windows":
        return asset + ".exe", "ocr.exe"
    return asset, "ocr"


def parse_sha256sums(text: str, asset: str) -> str:
    """从官方 sha256sum.txt 里取出某个包名的哈希；找不到返回空字符串。"""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == asset:
            return parts[0].strip().lower()
    return ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ocr_dir() -> Path:
    return VENDOR_DIR / OCR_DIRNAME


def ocr_installed() -> dict:
    manifest = _ocr_dir() / OCR_MANIFEST
    if manifest.is_file():
        try:
            return json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            LOG.debug("ocr 清单读取失败，按未安装处理")
    return {}


def install_ocr(force: bool = False) -> dict:
    """下载并校验 OpenCodeReview 二进制，返回安装记录。

    已有文件且版本一致时只做复用，不重复下 55 MB；`force` 才重下。
    """
    asset, filename = ocr_asset()
    dest = _ocr_dir() / filename
    record = ocr_installed()

    if dest.is_file() and not force:
        size = dest.stat().st_size
        if size < OCR_MIN_BYTES:
            LOG.warning("%s 只有 %.1f MB，像是没下完，重新下载", dest.name, size / 1024 / 1024)
        elif record.get("version") == OCR_VERSION and record.get("sha256"):
            LOG.info("OpenCodeReview %s 已就位，跳过", OCR_VERSION)
            return record
        else:
            # 手工放进来或者换了版本：能校验就校验，通过就补个清单，省一次下载
            LOG.info("发现已有的 %s，校验后复用", dest.name)
            expected = _remote_sha256(asset)
            actual = _sha256_file(dest)
            if expected and actual != expected:
                LOG.warning("%s 校验不通过（%s ≠ %s），重新下载", dest.name, actual[:12], expected[:12])
            elif expected or size >= OCR_MIN_BYTES:
                if not expected:
                    LOG.warning("没拿到官方 sha256，仅按文件大小判断 %s 可用", dest.name)
                return _write_ocr_manifest(filename, asset, actual)

    LOG.info("下载 OpenCodeReview %s -> %s", OCR_VERSION, dest)
    _ocr_dir().mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    try:
        _download(_release_url(asset), partial)
        expected = _remote_sha256(asset)
        actual = _sha256_file(partial)
        if expected and actual != expected:
            raise RuntimeError(
                f"{asset} 的 sha256 校验不通过（本地 {actual}，官方 {expected}），已丢弃。"
                "可能是网络中间环节改写了文件，请重试或手工下载。"
            )
        if not expected:
            LOG.warning("没取到官方 sha256sum.txt，跳过哈希校验")
        if partial.stat().st_size < OCR_MIN_BYTES:
            raise RuntimeError(f"{asset} 下载不完整（{partial.stat().st_size} 字节）")
        partial.replace(dest)
        if os.name != "nt":
            dest.chmod(0o755)
        return _write_ocr_manifest(filename, asset, actual)
    finally:
        partial.unlink(missing_ok=True)


def _write_ocr_manifest(filename: str, asset: str, sha256: str) -> dict:
    record = {
        "version": OCR_VERSION,
        "asset": asset,
        "file": filename,
        "sha256": sha256,
        "installed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (_ocr_dir() / OCR_MANIFEST).write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return record


def _release_url(name: str) -> str:
    base = OCR_RELEASE_BASE.format(repo=OCR_REPO, version=OCR_VERSION)
    return f"{base}/{name}"


def _remote_sha256(asset: str) -> str:
    """取官方 sha256sum.txt；网络异常时返回空字符串（调用方降级为只查大小）。"""
    try:
        req = urllib.request.Request(_release_url("sha256sum.txt"), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 校验失败不该阻断安装
        LOG.warning("读取 sha256sum.txt 失败：%s", exc)
        return ""
    digest = parse_sha256sums(text, asset)
    if not digest:
        LOG.warning("sha256sum.txt 里没有 %s", asset)
    return digest


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
    parser.add_argument("--skip-ocr", action="store_true",
                        help="不下 OpenCodeReview 二进制（代码审查会因此不可用）")
    args = parser.parse_args(argv)

    if args.list:
        versions = installed_versions()
        ocr = ocr_installed()
        if not versions and not ocr:
            print("_vendor 还没有内容，先运行：python bootstrap_deps.py")
            return 0
        for name, version in versions.items():
            print(f"{name:20s} {version}")
        if ocr:
            print(f"{'opencode-review':20s} {ocr.get('version', '?')}  ({ocr.get('file', '?')})")
        else:
            print(f"{'opencode-review':20s} 未安装（代码审查不可用，--only ocr 补装）")
        return 0

    want_ocr = not args.skip_ocr and (not args.only or OCR_DIRNAME in args.only)
    versions = install(force=args.upgrade, only=args.only)
    ocr_error = ""
    ocr = {}
    if want_ocr:
        try:
            ocr = install_ocr(force=args.upgrade)
        except Exception as exc:  # noqa: BLE001 - 装不上 ocr 不该让整个引导失败
            ocr_error = str(exc)
            LOG.warning("OpenCodeReview 安装失败：%s", exc)

    print("\n完成，_vendor/ 现有：")
    for name, version in sorted(versions.items()):
        print(f"  {name:20s} {version}")
    if ocr:
        print(f"  {'opencode-review':20s} {ocr.get('version')}  -> {ocr.get('file')}")
    if ocr_error:
        print("\n⚠ 代码审查工具暂时不可用：" + ocr_error)
        print("  可稍后单独重试：python bootstrap_deps.py --only ocr")
        try:
            print(f"  或手工下载：{_release_url(ocr_asset()[0])}")
        except RuntimeError:
            print("  当前平台没有官方发布包，请用 OCR_BIN 指向手工下载的二进制。")
    print(f"\n目录：{VENDOR_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
