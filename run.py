#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""启动本地网页服务。

为什么要显式往 sys.path 里塞 `_vendor`：
    本机 pip 装不进依赖（见 bootstrap_deps.py 里的说明），依赖被放在项目内的
    `_vendor/`。这里把它加到搜索路径最前面，于是不用配置任何环境变量就能跑。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENDOR_DIR = PROJECT_ROOT / "_vendor"

# _vendor 放最前：让第三方依赖版本以项目内为准，不受系统环境影响
for extra in (str(VENDOR_DIR), str(PROJECT_ROOT)):
    if extra not in sys.path:
        sys.path.insert(0, extra)

LOG = logging.getLogger("run")

REQUIRED = [
    ("fastapi", "FastAPI（网页框架）"),
    ("uvicorn", "uvicorn（本地服务器）"),
    ("requests", "requests（HTTP 客户端）"),
    ("yt_dlp", "yt-dlp（视频下载）"),
    ("imageio_ffmpeg", "imageio-ffmpeg（自带 ffmpeg）"),
]


def check_dependencies() -> list[str]:
    """返回缺失依赖的说明列表，空列表表示齐全。"""
    missing = []
    for module, label in REQUIRED:
        try:
            __import__(module)
        except ImportError:
            missing.append(f"{label}  →  缺少模块 {module}")
    return missing


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        LOG.debug("当前解释器不支持重设 stdout 编码")

    from xhs import config as config_mod
    from xhs import sandbox as sandbox_mod

    # 有些受限环境里 tempfile 建出的目录写不进去（见 xhs/sandbox.py），
    # 启动时先探测一次，免得等用户跑到一半才发现
    sandbox_mod.ensure_usable_tempdir()

    config = config_mod.load()
    server_cfg = config.get("server") or {}

    parser = argparse.ArgumentParser(description="小红书视频转笔记（本地网页版）")
    parser.add_argument("--host", default=server_cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(server_cfg.get("port", 8787)))
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)

    missing = check_dependencies()
    if missing:
        print("依赖不完整，请先在本目录执行：\n    python bootstrap_deps.py\n")
        for item in missing:
            print("  - " + item)
        return 1

    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    print("=" * 58)
    print("  小红书视频转笔记")
    print(f"  网页地址：{url}")
    print(f"  输出目录：{config_mod.output_dir(config)}")
    print(f"  转写模型：{(config.get('asr') or {}).get('provider')}   |   "
          f"总结模型：{(config.get('summarizer') or {}).get('provider')}")
    print("  （首次使用请点右上角「设置」填写 API key）")
    print("=" * 58)

    if not args.no_browser:
        # 等服务器起来再开浏览器，否则会看到「无法访问」
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run("app:app", host=args.host, port=args.port, log_level="info", reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
