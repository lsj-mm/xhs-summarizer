#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""看门狗：保证工具箱一直在线，而且屏幕上不出现任何窗口。

为什么需要它：
    Windows 的「开机自启」只保证**登录时启动一次**。进程之后可能无声消失
    （隐藏的控制台宿主被重启、任务管理器结束进程、系统清理等），而自启不会补。
    结果就是「明明设过自启，用着用着就连不上了」。

做法（尽量简单）：
    - 用 pythonw 运行，压根没有控制台，所以不会闪黑窗口；
    - 每 CHECK_INTERVAL 秒探测一次 127.0.0.1:<端口>；
    - 端口没人监听就起一个 run.py，输出追加到 work/server.log；
    - 自己起的进程还活着却长时间连不上（疑似卡死），杀掉重来；
    - 用 127.0.0.1:<端口+1> 的独占绑定当单实例锁，不会重复开多个看门狗；
    - 端口上已有别人起的服务时，只观察不干扰。

用法：
    pythonw watchdog.py          # 常驻，开机自启就用这个
    python watchdog.py --once    # 只检查/启动一次，排查时用
    python watchdog.py --stop    # 结束正在运行的看门狗及其服务
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
SERVER_LOG = WORK / "server.log"
WATCHDOG_LOG = WORK / "watchdog.log"
LOCK_FILE = WORK / "watchdog.pid"

CHECK_INTERVAL = 15      # 探测间隔（秒）
SPAWN_COOLDOWN = 10      # 两次启动尝试之间至少间隔这么久
HANG_RESTART = 90        # 自己起的进程连续这么久连不上，判定卡死并重启
LOG_MAX_BYTES = 512 * 1024


def port_from_config() -> int:
    """读 config.json 里的 server.port，读不到就用 8787。"""
    try:
        data = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return int((data.get("server") or {}).get("port") or 8787)
    except (OSError, ValueError, TypeError):
        return 8787


def log(message: str) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} {message}\n"
    try:
        if WATCHDOG_LOG.is_file() and WATCHDOG_LOG.stat().st_size > LOG_MAX_BYTES:
            WATCHDOG_LOG.replace(WATCHDOG_LOG.with_suffix(".log.old"))
        with WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def listening(port: int) -> bool:
    """端口上有没有人在监听：能连上就算在跑。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def acquire_lock(port: int) -> socket.socket | None:
    """用端口+1 的独占绑定当单实例锁；拿不到说明已有看门狗在跑。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port + 1))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def spawn(port: int) -> subprocess.Popen:
    """启动 run.py，输出追加到 server.log。pythonw 下子进程同样没有窗口。"""
    WORK.mkdir(parents=True, exist_ok=True)
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    with SERVER_LOG.open("ab") as out:
        child = subprocess.Popen(
            [sys.executable, "run.py", "--no-browser", "--port", str(port)],
            cwd=str(ROOT),
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    log(f"已启动服务 pid={child.pid} port={port}")
    return child


def read_pid() -> int | None:
    try:
        return int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:  # noqa: BLE001 - 拿不到就当作已退出
        return False


def _list_script_pids() -> list[int]:
    """找出所有跑着 run.py / watchdog.py 的 python 进程。

    不用 wmic：Windows 11 已经默认移除它；改用 PowerShell 的 CIM 查询。
    """
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Where-Object { $_.CommandLine -match 'run\\.py|watchdog\\.py' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(token) for token in out.split() if token.strip().isdigit()]


def _kill(pid: int) -> bool:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _port_owner(port: int) -> int | None:
    """用 netstat 找占着端口的进程，作为 CIM 查询的兜底（PowerShell 冷启动偶尔会慢）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def stop_running(port: int) -> int:
    """结束看门狗和它拉起的服务；返回结束的进程数。

    先杀看门狗再杀服务：反过来的话，看门狗会在下一轮把服务又拉起来。
    """
    killed = 0
    me = os.getpid()
    mine = read_pid()
    if mine and mine != me and pid_alive(mine):
        killed += 1 if _kill(mine) else 0
        time.sleep(1.5)  # 等它放开单实例锁，别让残留进程抢跑

    # 注意排除自己：`--stop` 进程的命令行里也有 watchdog.py，不排掉会自杀
    for target in set(_list_script_pids()):
        if target in (me, mine) or not pid_alive(target):
            continue
        killed += 1 if _kill(target) else 0

    # 兜底：万一上面的查询没命中，直接揪出端口占用者
    if listening(port):
        owner = _port_owner(port)
        if owner and pid_alive(owner):
            killed += 1 if _kill(owner) else 0

    LOCK_FILE.unlink(missing_ok=True)
    return killed


def supervise(port: int) -> int:
    lock = acquire_lock(port)
    if lock is None:
        print(f"已经有一个看门狗在跑（127.0.0.1:{port + 1} 被占用），本次退出。")
        return 0
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")

    log(f"看门狗启动，监测 127.0.0.1:{port}，间隔 {CHECK_INTERVAL}s")
    child: subprocess.Popen | None = None
    last_spawn = 0.0
    failures = 0

    try:
        while True:
            up = listening(port)
            if child is not None and child.poll() is not None:
                log(f"服务进程 pid={child.pid} 退出，返回码 {child.returncode}")
                child = None
                failures = 0
            if up:
                failures = 0
            else:
                failures += 1
                if child is None and time.time() - last_spawn > SPAWN_COOLDOWN:
                    child = spawn(port)
                    last_spawn = time.time()
                elif child is not None and failures * CHECK_INTERVAL > HANG_RESTART:
                    log(f"服务连续 {failures * CHECK_INTERVAL} 秒无响应，重启")
                    try:
                        child.kill()
                    except OSError:
                        pass
                    child = None
                    failures = 0
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log("看门狗收到中断，退出")
    finally:
        lock.close()
        LOCK_FILE.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工具箱看门狗：保证服务在线且不留窗口")
    parser.add_argument("--once", action="store_true", help="只检查/启动一次就退出")
    parser.add_argument("--stop", action="store_true", help="结束正在运行的服务和看门狗")
    parser.add_argument("--port", type=int, default=None, help="覆盖 config.json 里的端口")
    args = parser.parse_args(argv)

    port = args.port or port_from_config()
    if args.stop:
        print(f"已结束 {stop_running(port)} 个进程")
        return 0
    if args.once:
        if listening(port):
            print(f"服务已在 127.0.0.1:{port} 运行，无需启动。")
            return 0
        child = spawn(port)
        print(f"已启动服务 pid={child.pid}，日志见 {SERVER_LOG}")
        return 0
    return supervise(port)


if __name__ == "__main__":
    raise SystemExit(main())
