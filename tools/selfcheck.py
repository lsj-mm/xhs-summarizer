#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线自检：不用联网、不用 API key，验证整条流水线是否通畅。

做法：
    1. 用内置 ffmpeg 造一段 12 秒带声音的测试视频；
    2. 起一个本地文件服务把它当「视频源」；
    3. 起假的 OpenAI 兼容服务（转写 + 提取工具清单 + 成文）和假的 GitHub 接口；
    4. 走完整流程：解析链接 → 下载 → 抽音轨 → 切片 → 转写 → 提取工具 → 核实链接 → 写 md；
    5. 逐项断言结果。

重点覆盖三个容易出错的地方：
    - 临时目录权限（0o700 建目录会丢权限，导致读浏览器 cookie 失败）
    - 读不到 cookie 时必须自动降级，而不是让整条任务失败
    - GitHub 链接必须「验证过才写」：搜不到、仓库不存在，都要老实写「未找到」

用法：
    python tools/selfcheck.py
"""

from __future__ import annotations

import http.server
import json
import logging
import re
import shutil
import socket
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import audio as audio_mod  # noqa: E402
from xhs import config as config_mod  # noqa: E402
from xhs import github as github_mod  # noqa: E402
from xhs import jobs as jobs_mod  # noqa: E402
from xhs import pipeline as pipeline_mod  # noqa: E402
from xhs import sandbox as sandbox_mod  # noqa: E402

WORK = PROJECT_ROOT / "tools" / "_selfcheck"
FAKE_TRANSCRIPT = "本周 GitHub 热榜出来了，第一个项目 ExactTool 可以直接对程序做逆向分析。"
# 提取阶段靠系统提示里的这句话区分，避免依赖调用顺序
EXTRACT_MARKER = "只输出一个 JSON 对象"

# 成文阶段返回的 markdown（假模型，只用来验证拼装）
FAKE_BODY = """## 一句话总结
本周热榜集中在 AI 逆向工程方向。

## 工具清单
| 工具 | 作用 | 项目地址 |
|---|---|---|
| ExactTool | 对程序做自动逆向分析 | [acme/ExactTool](https://github.com/acme/ExactTool) |
| NSA Ghidra | 软件逆向分析套件 | [NationalSecurityAgency/ghidra](https://github.com/NationalSecurityAgency/ghidra) |

## 工具详解
### ExactTool
- **作用**：对程序做自动逆向分析
- **项目地址**：https://github.com/acme/ExactTool（来源：自动匹配，请核对）

## 其它要点
- 本周热榜集中在逆向工程方向
"""

# 分析阶段返回的 JSON：工具类场景，四种链接情况各一个
FAKE_ANALYZE = json.dumps(
    {
        "content_kind": "tools",
        "kind_reason": "主体在介绍工具和开源项目",
        "summary": "本周热榜集中在 AI 逆向工程方向",
        "tools": [
            {"name": "ExactTool", "purpose": "对程序做自动逆向分析", "github": "",
             "evidence": "视频里提到 ExactTool"},
            {"name": "Ambiguous", "purpose": "名字很泛的工具", "github": "", "evidence": ""},
            {"name": "NSA Ghidra", "purpose": "软件逆向分析套件",
             "github": "NationalSecurityAgency/ghidra", "evidence": ""},
            {"name": "GhostTool", "purpose": "模型记错仓库的工具",
             "github": "nobody/definitely-not-exist", "evidence": ""},
        ],
        "qa": [],
        "sections": [],
        "other_points": ["热榜集中在逆向工程方向"],
    },
    ensure_ascii=False,
)

# 问答类场景：用来验证「按内容类型换结构」这条路
FAKE_ANALYZE_QA = json.dumps(
    {
        "content_kind": "qa",
        "kind_reason": "主体是一问一答",
        "summary": "回答观众关于模型部署的三个问题",
        "tools": [],
        "qa": [
            {"question": "显存不够怎么办？", "answer": "先用量化版本，再考虑把上下文长度调小。"},
            {"question": "能不能用 CPU 跑？", "answer": "可以，但速度会慢很多，只适合验证流程。"},
        ],
        "sections": [],
        "other_points": [],
    },
    ensure_ascii=False,
)

# 教程类场景
FAKE_ANALYZE_TUTORIAL = json.dumps(
    {
        "content_kind": "tutorial",
        "kind_reason": "主体是教怎么一步步做",
        "summary": "手把手讲怎么把本地模型接进自己的程序",
        "tools": [],
        "qa": [],
        "sections": [
            {"heading": "准备环境", "points": ["装好依赖", "把模型权重放到指定目录"]},
            {"heading": "写调用代码", "points": ["用 OpenAI 兼容接口", "注意超时和重试"]},
        ],
        "other_points": [],
    },
    ensure_ascii=False,
)

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))
    return ok


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 静音，别把自检输出冲乱
        pass


def start_file_server(directory: Path, port: int) -> http.server.ThreadingHTTPServer:
    handler = lambda *a, **kw: _Quiet(*a, directory=str(directory), **kw)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """假转写 + 假大模型，形状跟真实接口一致。"""

    calls = {"transcriptions": 0, "chat": 0, "extract": 0, "write": 0}
    last_write_request = ""  # 记录成文阶段的请求体，用来验证「已核实链接确实传给了模型」
    analyze_payload = FAKE_ANALYZE  # 测试可以替换它，模拟不同内容类型
    # 分片级故障模拟：partNNN -> "ok" / "fail"（一直 500）/ "flaky"（前两次 500）
    part_behaviour: dict = {}
    asr_attempts: dict = {}

    def log_message(self, *args):
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if self.path.endswith("/audio/transcriptions"):
            _StubHandler.calls["transcriptions"] += 1
            # 从 multipart 里取文件名，按分片模拟成功/失败
            name_match = re.search(rb'filename="([^"]+)"', raw)
            part = (re.search(r"part(\d+)", name_match.group(1).decode("utf-8", "replace"))
                    if name_match else None)
            key = part.group(1) if part else "0"
            _StubHandler.asr_attempts[key] = _StubHandler.asr_attempts.get(key, 0) + 1
            behaviour = _StubHandler.part_behaviour.get(key, "ok")
            if behaviour == "fail" or (behaviour == "flaky" and _StubHandler.asr_attempts[key] <= 2):
                return self._send({"message": "upstream error"}, 500)
            return self._send(
                {
                    "text": FAKE_TRANSCRIPT,
                    "language": "zh",
                    "segments": [{"start": 0.0, "end": 3.5, "text": FAKE_TRANSCRIPT}],
                }
            )
        if self.path.endswith("/chat/completions"):
            _StubHandler.calls["chat"] += 1
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return self._send({"error": "请求体不是 JSON"}, 400)

            sent = json.dumps(payload, ensure_ascii=False)
            if EXTRACT_MARKER in sent:
                _StubHandler.calls["extract"] += 1
                return self._send(
                    {"choices": [{"message": {"role": "assistant", "content": _StubHandler.analyze_payload}}]}
                )

            _StubHandler.calls["write"] += 1
            _StubHandler.last_write_request = sent
            return self._send({"choices": [{"message": {"role": "assistant", "content": FAKE_BODY}}]})
        return self._send({"error": f"未预期的路径 {self.path}"}, 404)


def start_stub_api(port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _GitHubStub(http.server.BaseHTTPRequestHandler):
    """假 GitHub：只认一个仓库，搜索只对 ExactTool 命中。

    用来验证「搜不到就不给链接」这条底线，避免真实网络和限流干扰。
    """

    calls = {"repos": 0, "search": 0}

    REPOS = {
        "acme/exacttool": {
            "full_name": "acme/ExactTool", "html_url": "https://github.com/acme/ExactTool",
            "description": "假的测试仓库", "stargazers_count": 9000,
        },
        "nationalsecurityagency/ghidra": {
            "full_name": "NationalSecurityAgency/ghidra",
            "html_url": "https://github.com/NationalSecurityAgency/ghidra",
            "description": "软件逆向工程框架", "stargazers_count": 50000,
        },
    }
    SEARCH = {
        "exacttool": [{"name": "ExactTool", "full_name": "acme/ExactTool",
                       "html_url": "https://github.com/acme/ExactTool",
                       "description": "假的测试仓库", "stargazers_count": 9000}],
        "ambiguous": [{"name": "totally-unrelated", "full_name": "someone/totally-unrelated",
                       "html_url": "https://github.com/someone/totally-unrelated",
                       "description": "名字对不上", "stargazers_count": 100}],
        # 真实踩过的坑：同名但 0 star 的仓库归一化后完全一致，会赢过官方仓库
        "tinytool": [{"name": "TinyTool", "full_name": "someone/TinyTool",
                      "html_url": "https://github.com/someone/TinyTool",
                      "description": "同名但没人用", "stargazers_count": 0}],
        "gadget": [
            {"name": "Gadget", "full_name": "ghost/Gadget",
             "html_url": "https://github.com/ghost/Gadget",
             "description": "同名但 0 star", "stargazers_count": 0},
            {"name": "GadgetSuite", "full_name": "acme/GadgetSuite",
             "html_url": "https://github.com/acme/GadgetSuite",
             "description": "名字略长但有实际使用", "stargazers_count": 5000},
        ],
        "nsa ghidra": [
            {"name": "NSA-Ghidra", "full_name": "DamianFekete/NSA-Ghidra",
             "html_url": "https://github.com/DamianFekete/NSA-Ghidra",
             "description": "同名但 0 star 的仓库", "stargazers_count": 0},
        ],
    }

    def log_message(self, *args):
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/repos/"):
            _GitHubStub.calls["repos"] += 1
            key = parsed.path[len("/repos/"):].lower()
            repo = self.REPOS.get(key)
            return self._send(repo, 200) if repo else self._send({"message": "Not Found"}, 404)
        if parsed.path == "/search/repositories":
            _GitHubStub.calls["search"] += 1
            query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            name = query.replace(" in:name", "").strip().lower()
            if name == "ratelimited":
                return self._send({"message": "API rate limit exceeded"}, 403)
            return self._send({"items": self.SEARCH.get(name, [])})
        return self._send({"message": "Not Found"}, 404)


def start_github_stub(port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _GitHubStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_test_video(target: Path) -> bool:
    """造测试视频。优先 h264，编不了就退回 mpeg4（不同 ffmpeg 构建带的编码器不同）。"""
    for codec, extras in (("libx264", ["-pix_fmt", "yuv420p"]), ("mpeg4", [])):
        args = [
            audio_mod.ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            # 输入必须写在编码参数之前，否则 -c:v 会被当成输入参数而报错
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "12",
            "-c:v", codec, *extras,
            "-c:a", "aac",
            "-shortest", "-y", str(target),
        ]
        audio_mod._run(args, timeout=180)
        if target.exists() and target.stat().st_size > 1024:
            return True
    return False


def base_config(out_dir: Path, work_dir: Path, stub_base: str, github_base: str = "") -> dict:
    config = config_mod.load()
    config["output_dir"] = str(out_dir)
    config["workdir"] = str(work_dir)
    config["download"]["format"] = "bestaudio/best"
    config["download"]["keep_media"] = False
    config["download"]["proxy"] = ""
    config["download"]["use_system_proxy"] = False
    config["asr"].update({"provider": "custom_openai", "base_url": stub_base, "model": "stub-asr",
                          "api_key": "stub-key"})
    config["summarizer"].update({"provider": "custom", "base_url": stub_base, "model": "stub-chat",
                                 "api_key": "stub-key"})
    config["summary"]["include_transcript"] = True
    if github_base:
        config["github"].update({"enabled": True, "api_base": github_base, "token": ""})
    return config


def main() -> int:
    # 日志走 stdout：写 stderr 会被 PowerShell 当成错误记录，导致「全过却返回退出码 1」
    logging.basicConfig(level=logging.INFO, format="  · %(message)s", stream=sys.stdout)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    print("=" * 64)
    print("  离线自检：下载 → 抽音轨 → 切片 → 转写 → 提取工具 → 核实链接 → 写 md")
    print("=" * 64)

    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    source_dir = WORK / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. ffmpeg -------------------------------------------------------
    try:
        ffmpeg = audio_mod.ffmpeg_exe()
        check(Path(ffmpeg).exists(), "找到内置 ffmpeg", Path(ffmpeg).name)
    except Exception as exc:
        check(False, "找到内置 ffmpeg", str(exc))
        return _report()

    # --- 2. 临时目录可用性 ----------------------------------------------
    patched = sandbox_mod.ensure_usable_tempdir()
    probe = Path(tempfile.mkdtemp(prefix="selfcheck-"))
    try:
        (probe / "probe.txt").write_text("ok", encoding="utf-8")
        writable = True
    except OSError:
        writable = False
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    check(writable, "tempfile 建出的目录可写", "已启用兼容补丁" if patched else "本机无需补丁")

    # --- 3. 纯函数：正文注入、话题清理 -----------------------------------
    from xhs import store as store_mod  # noqa: PLC0415
    from xhs import summarize as summarize_mod  # noqa: PLC0415

    sample_meta = {
        "title": "T", "url": "u", "author": "A", "content_type": "视频",
        "duration": 154, "note_text": "REA 项目 https://example.com/rea",
    }
    material = summarize_mod._material(sample_meta, "语音转写内容")
    check("REA" in material and "仅供核对专有名词" in material,
          "作者正文作为专有名词参考喂给模型")
    check("REA" not in summarize_mod._material(sample_meta, "语音转写内容", use_note_text=False),
          "关掉开关后不再喂作者正文")

    video_md = store_mod.render_markdown(
        sample_meta, "## 一句话总结\nx", {"text": "语音转写内容", "segments": []},
        {"summary": {"include_transcript": True}},
    )
    check("原笔记正文" not in video_md and "example.com/rea" not in video_md,
          "文档里不再输出作者正文",
          "仍出现" if "原笔记正文" in video_md else "已去掉")
    check(not video_md.startswith("---"), "默认不输出开头的 YAML 元数据块")
    with_front = store_mod.render_markdown(
        sample_meta, "## 一句话总结\nx", {"text": "语音转写内容", "segments": []},
        {"summary": {"include_front_matter": True}},
    )
    check(with_front.startswith("---") and "topic_kind:" in with_front,
          "打开开关后会输出元数据块（含内容类型）")

    image_material = summarize_mod._material(
        {**sample_meta, "content_type": "图文", "note_text": ""}, "图文正文"
    )
    check("仅供核对专有名词" not in image_material, "图文笔记不会重复注入正文")

    cleaned = store_mod.clean_note_text("看这个 #github[话题]#  #ai编程[话题]#\n\n多空行   与  多空格")
    check("[话题]" not in cleaned and "#github" in cleaned, "话题标记被清理但话题名保留", repr(cleaned[:40]))
    check(store_mod.clean_note_text("") == "", "空正文不报错")

    table = store_mod.parse_tool_table(
        "## 工具清单\n| 工具 | 作用 | 项目地址 |\n|---|---|---|\n"
        "| REA | 自动逆向 | [morluto/rea](https://github.com/morluto/rea) |\n"
        "| 某项目 | 做某事 | 未找到 |\n\n## 工具详解\n### REA\n"
    )
    check([t["name"] for t in table] == ["REA", "某项目"], "能从正文表格解析出工具名",
          "、".join(t["name"] for t in table))
    check(table[0]["url"] == "https://github.com/morluto/rea" and table[1]["url"] == "",
          "表格里的链接与「未找到」都能识别")
    check(store_mod.parse_tool_table("没有表格的正文") == [], "没有表格时返回空列表")
    alt = store_mod.parse_tool_table(
        "## 提到的工具\n| 工具 | 作用 | 项目地址 |\n|---|---|---|\n"
        "| Docling | 版面解析 | [docling-project/docling](https://github.com/docling-project/docling) |\n"
    )
    check([t["name"] for t in alt] == ["Docling"], "也能识别「提到的工具」这一节",
          "、".join(t["name"] for t in alt))

    # JSON 解析要能容忍代码块包裹和前后解释文字
    for raw in ('{"a": 1}', '```json\n{"a": 1}\n```', '好的，结果如下：\n{"a": 1}\n以上。'):
        try:
            parsed = summarize_mod._parse_json(raw) == {"a": 1}
        except Exception:
            parsed = False
        if not parsed:
            break
    check(parsed, "提取结果解析能容忍代码块与解释文字")
    try:
        summarize_mod._parse_json("这里没有任何 JSON")
        check(False, "乱码提取结果会明确报错")
    except summarize_mod.SummaryError:
        check(True, "乱码提取结果会明确报错")

    # --- 4. 测试素材 -----------------------------------------------------
    video = source_dir / "sample.mp4"
    if not make_test_video(video):
        check(False, "生成测试视频", "ffmpeg 无法编码测试视频")
        return _report()
    check(True, "生成测试视频", f"{video.stat().st_size / 1024:.0f} KB")

    # --- 5. 假 GitHub：验证「找不到就不给链接」 --------------------------
    gh_port = free_port()
    gh_server = start_github_stub(gh_port)
    gh_base = f"http://127.0.0.1:{gh_port}"

    lookup = github_mod.GitHubLookup(api_base=gh_base, token="", min_score=60)
    resolved = lookup.resolve_all([
        {"name": "ExactTool", "purpose": "对程序做自动逆向分析", "github": "", "evidence": ""},
        {"name": "Ambiguous", "purpose": "名字很泛的工具", "github": "", "evidence": ""},
        {"name": "NSA Ghidra", "purpose": "逆向套件", "github": "NationalSecurityAgency/ghidra",
         "evidence": ""},
        {"name": "GhostTool", "purpose": "模型记错仓库", "github": "nobody/definitely-not-exist",
         "evidence": ""},
    ])
    by_name = {item["name"]: item for item in resolved}
    check(by_name["ExactTool"]["full_name"] == "acme/ExactTool"
          and by_name["ExactTool"]["source"] == github_mod.SOURCE_FROM_SEARCH,
          "搜索到名字一致的仓库", by_name["ExactTool"]["source"])
    check(by_name["NSA Ghidra"]["source"] == github_mod.SOURCE_FROM_MODEL
          and "ghidra" in by_name["NSA Ghidra"]["url"],
          "模型提供的仓库经接口验证后才采信")
    check(by_name["Ambiguous"]["url"] == "" and by_name["Ambiguous"]["source"] == github_mod.SOURCE_NONE,
          "名字对不上就不给链接（不给错链接）")
    check(by_name["GhostTool"]["url"] == "", "不存在的仓库被丢弃")
    check("github.com" in by_name["ExactTool"]["url"], "只输出 github.com 域名")

    # 真实踩过的坑：同名 0 star 仓库得分最高，会把官方仓库挤掉
    tiny = lookup.resolve({"name": "TinyTool", "purpose": "", "github": "", "evidence": ""})
    check(tiny["url"] == "", "同名但 0 star 的仓库被剔除", tiny["source"])
    gadget = lookup.resolve({"name": "Gadget", "purpose": "", "github": "", "evidence": ""})
    check(gadget["url"] == "", "默认不接受「名字相近」的匹配（宁可没有链接）", gadget["source"])
    loose = github_mod.GitHubLookup(api_base=gh_base, token="", allow_loose_match=True)
    gadget_loose = loose.resolve({"name": "Gadget", "purpose": "", "github": "", "evidence": ""})
    check(gadget_loose["full_name"] == "acme/GadgetSuite"
          and gadget_loose["source"] == github_mod.SOURCE_FROM_SEARCH_LOOSE,
          "打开开关后会接受名字相近的匹配并标注出来",
          f"{gadget_loose['full_name']} / {gadget_loose['source']}")
    nsa = lookup.resolve({"name": "NSA Ghidra", "purpose": "", "github": "", "evidence": ""})
    check("DamianFekete" not in nsa["url"],
          "宁可写未找到，也不把同名小仓库当官方项目", nsa["full_name"] or "未找到")

    # 限流时不能说成「未找到」：查不了和不存在是两回事
    limited = github_mod.GitHubLookup(api_base=gh_base, token="", min_score=60)
    rl = limited.resolve({"name": "RateLimited", "purpose": "", "github": "", "evidence": ""})
    check(rl["url"] == "" and "限流" in rl["source"], "被限流时如实标注为查询失败", rl["source"])
    check(limited.lookups <= 2, "限流后不再发无谓请求", f"{limited.lookups} 次")

    # 关闭自动匹配时不应发起任何查询
    off = github_mod.GitHubLookup(api_base=gh_base, enabled=False).resolve_all(
        [{"name": "ExactTool", "purpose": "", "github": "", "evidence": ""}]
    )
    check(off[0]["url"] == "" and "未查询" in off[0]["source"], "关闭自动匹配后不查询、不写链接")

    # --- 6. 起本地服务，跑完整流程 --------------------------------------
    file_port, stub_port = free_port(), free_port()
    file_server = start_file_server(source_dir, file_port)
    stub_server = start_stub_api(stub_port)
    video_url = f"http://127.0.0.1:{file_port}/sample.mp4"
    stub_base = f"http://127.0.0.1:{stub_port}/v1"
    check(True, "本地假服务已启动", f"视频源 :{file_port}，接口 :{stub_port}，GitHub :{gh_port}")

    registry = jobs_mod.JobRegistry()

    # --- 7. 内容类型自适应：直接调 summarize，验证按类型选结构 -------------
    type_cfg = base_config(WORK / "types" / "out", WORK / "types" / "work", stub_base, gh_base)
    type_meta = {"title": "T", "url": "u", "author": "A", "content_type": "视频",
                 "duration": 120, "note_text": ""}
    type_transcript = {"text": "这里是转写内容", "segments": []}

    qa_write = ""
    for kind_name, label, payload, marker in (
        ("qa", "问答", FAKE_ANALYZE_QA, "【内容类型 = qa（问答）】"),
        ("tutorial", "教程讲解", FAKE_ANALYZE_TUTORIAL, "【内容类型 = tutorial（教程讲解）】"),
    ):
        _StubHandler.analyze_payload = payload
        lookups_before = dict(_GitHubStub.calls)
        outcome = summarize_mod.summarize(type_meta, type_transcript, type_cfg)
        check(outcome["content_kind"] == kind_name and outcome["kind_label"] == label,
              f"识别为「{label}」类型", f"{outcome['content_kind']}/{outcome['kind_label']}")
        check(marker in _StubHandler.last_write_request, f"{label} 类型使用了对应的文档结构")
        check(dict(_GitHubStub.calls) == lookups_before, f"{label} 类型没提到工具时不查 GitHub")
        if kind_name == "qa":
            qa_write = _StubHandler.last_write_request
    check("显存不够怎么办" in qa_write, "问答对逐条传给了成文阶段（不会被合并或丢掉）")

    _StubHandler.analyze_payload = FAKE_ANALYZE  # 恢复成工具类场景

    # 场景一：带浏览器 cookie（可能读不到，必须能自动降级）
    cookie_dir = WORK / "cookie_scenario"
    cookie_cfg = base_config(cookie_dir / "out", cookie_dir / "work", stub_base, gh_base)
    cookie_cfg["download"]["cookies_from_browser"] = "edge"
    cookie_cfg["asr"]["segment_seconds"] = 600
    cookie_job = registry.create([video_url], cookie_cfg)
    pipeline_mod.run_job(cookie_job, cookie_cfg)
    cookie_files = sorted((cookie_dir / "out").glob("*.md"))
    check(cookie_job.status == "done", "带 cookie 场景不因 cookie 失败而中断",
          f"状态 {cookie_job.status} {cookie_job.error[:100]}")
    check(len(cookie_files) == 1, "带 cookie 场景也生成了 md", f"{len(cookie_files)} 个")

    # 场景二：常规流程（12 秒音频按 5 秒切片 → 3 段）
    out_dir, work_dir = WORK / "out", WORK / "work"
    config = base_config(out_dir, work_dir, stub_base, gh_base)
    config["download"]["cookies_from_browser"] = ""
    config["asr"]["segment_seconds"] = 5

    before = dict(_StubHandler.calls)
    job = registry.create([video_url], config)
    pipeline_mod.run_job(job, config)

    check(job.status == "done", "作业状态为完成", f"实际 {job.status} {job.error[:120]}")
    markdown_files = sorted(out_dir.glob("*.md"))
    if not check(len(markdown_files) == 1, "生成 1 个 md 文件", f"实际 {len(markdown_files)} 个"):
        file_server.shutdown()
        stub_server.shutdown()
        gh_server.shutdown()
        return _report()

    content = markdown_files[0].read_text(encoding="utf-8")
    check(not content.startswith("---"), "文档开头没有 YAML 元数据块")
    check("原笔记" in content and video_url in content, "引用行里有来源链接")
    check("## 一句话总结" in content, "包含「一句话总结」章节")
    check("## 工具清单" in content, "包含「工具清单」章节")
    check("完整逐字稿" in content, "逐字稿按折叠块附在末尾")
    check("<details>" in content and "</details>" in content, "折叠块标签成对")
    check(FAKE_TRANSCRIPT in content, "逐字稿内容来自转写结果")

    # 工具清单以正文表格为准（成文阶段可能补上提取阶段漏掉的工具）
    check("| ExactTool |" in content and "| NSA Ghidra |" in content,
          "正文工具表包含解析出的工具")
    check(job.items[0].tools == ["ExactTool", "NSA Ghidra"],
          "作业条目带上的工具清单与正文一致")
    check("类型：工具推荐" in content, "引用行里有内容类型",
          next((l for l in content.splitlines() if l.startswith("> ")), "缺少引用行"))
    check(job.items[0].topic_kind == "工具推荐", "作业条目带上了内容类型")

    # 关键：成文阶段的请求里必须带上「已核实链接」，模型才有可能写对地址
    sent = _StubHandler.last_write_request
    check("https://github.com/acme/ExactTool" in sent, "已核实链接被传给成文阶段的模型")
    check("已核实链接清单" in sent, "成文阶段带有明确的链接使用约束")
    check(sent.count("未找到") >= 2, "未匹配到的工具在提示里标为未找到",
          f"{sent.count('未找到')} 处")

    calls = {
        "transcriptions": _StubHandler.calls["transcriptions"] - before["transcriptions"],
        "extract": _StubHandler.calls["extract"] - before["extract"],
        "write": _StubHandler.calls["write"] - before["write"],
    }
    check(calls["transcriptions"] >= 3, "长音频被切片并逐段转写", f"转写调用 {calls['transcriptions']} 次")
    check(calls["extract"] == 1, "只做一次工具提取", f"{calls['extract']} 次")
    check(calls["write"] == 1, "只做一次成文", f"{calls['write']} 次")
    check("[00:05]" in content or "[00:10]" in content, "多段转写的时间戳已按偏移拼接")

    # --- 8. 部分段落转写失败：不能把已成功的段落一起丢掉 ----------------
    degrade_dir = WORK / "degrade"
    degrade_cfg = base_config(degrade_dir / "out", degrade_dir / "work", stub_base, gh_base)
    degrade_cfg["download"]["cookies_from_browser"] = ""
    degrade_cfg["asr"]["segment_seconds"] = 5  # 12 秒视频 → 3 段
    degrade_cfg["asr"]["retry_attempts"] = 2   # 测试时少等一会儿
    _StubHandler.part_behaviour = {"001": "fail"}  # 中间那段始终 500
    _StubHandler.asr_attempts = {}
    degraded = registry.create([video_url], degrade_cfg)
    pipeline_mod.run_job(degraded, degrade_cfg)
    degrade_files = sorted((degrade_dir / "out").glob("*.md"))
    check(degraded.status == "done", "部分段落失败时仍然生成文档（不再整条失败）",
          f"状态 {degraded.status} {degraded.error[:80]}")
    check("1 段" in (degraded.items[0].warning or ""), "作业条目带上了缺段警告",
          (degraded.items[0].warning or "无")[:70])
    if degrade_files:
        dtext = degrade_files[0].read_text(encoding="utf-8")
        check("有 1 段音频转写失败" in dtext, "文档里如实标注了失败的段落")
        check("[00:00]" in dtext and "[00:10]" in dtext and "[00:05]" not in dtext,
              "已成功段落的内容被保留，丢失那段没有占位")
        check(FAKE_TRANSCRIPT in dtext, "成功段落的内容确实写进了文档")
    else:
        check(False, "部分失败场景也生成了文档")

    # --- 9. 全部段落失败：应当明确失败并说明原因 ------------------------
    # 注意：未切片的音频文件名没有 partNNN，假接口把它记成 "0"
    _StubHandler.part_behaviour = {"0": "fail"}
    all_bad = base_config(WORK / "allbad" / "out", WORK / "allbad" / "work", stub_base, gh_base)
    all_bad["download"]["cookies_from_browser"] = ""
    all_bad["asr"]["retry_attempts"] = 1
    bad_job = registry.create([video_url], all_bad)
    pipeline_mod.run_job(bad_job, all_bad)
    check(bad_job.status == "failed" and "都没能转写成功" in (bad_job.error or ""),
          "全部段落失败时明确报错", (bad_job.error or "")[:80])

    # --- 10. 瞬时 500：应当靠重试救回来 --------------------------------
    _StubHandler.part_behaviour = {"0": "flaky"}  # 前两次 500，第三次成功
    _StubHandler.asr_attempts = {}
    retry_cfg = base_config(WORK / "retry" / "out", WORK / "retry" / "work", stub_base, gh_base)
    retry_cfg["download"]["cookies_from_browser"] = ""
    retry_cfg["asr"]["retry_attempts"] = 3
    retry_job = registry.create([video_url], retry_cfg)
    pipeline_mod.run_job(retry_job, retry_cfg)
    check(retry_job.status == "done" and not retry_job.items[0].warning,
          "瞬时 500 经重试后成功且无警告", f"状态 {retry_job.status}")
    check(_StubHandler.asr_attempts.get("0", 0) == 3, "确实重试了两次",
          f"共请求 {_StubHandler.asr_attempts.get('0')} 次")
    _StubHandler.part_behaviour = {}

    # --- 11. 工具箱：工具自动发现与共用资源 ------------------------------
    import app as app_mod  # noqa: PLC0415  导入即构建应用，离线不影响

    tool_list = app_mod.discover_tools()
    check(len(tool_list) >= 5, "扫描到至少五个工具",
          "、".join(f"{t['icon']}{t['name']}" for t in tool_list))
    check([t["id"] for t in tool_list] == ["notes", "tax-calc", "english", "keywords", "lottery"],
          "按 order 排序（笔记、税率、英语、关键字、彩票）",
          "、".join(t["id"] for t in tool_list))
    check(all((app_mod.TOOLS_DIR / f"{t['id']}.html").is_file() for t in tool_list),
          "每个工具都有对应的页面文件")
    check(all(t["url"] == f"/tool/{t['id']}" for t in tool_list),
          "工具地址形如 /tool/<id>")
    check(all(t["name"] and t["icon"] for t in tool_list), "每个工具都有名称与图标")

    # 工具页里引用的 /static/... 资源必须真实存在（手写路径很容易打错）
    missing_refs = []
    for tool in tool_list:
        html = (app_mod.TOOLS_DIR / f"{tool['id']}.html").read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
            if not (PROJECT_ROOT / ref.lstrip("/")).is_file():
                missing_refs.append(f"{tool['id']} → {ref}")
    check(not missing_refs, "工具页引用的共用资源都存在", "、".join(missing_refs) or "全部存在")

    shared = ["static/tools/_shared/base.css", "static/tools/_shared/finance.js",
              "static/tools/_shared/loan-ui.js", "static/tools/_shared/presets.js"]
    check(all((PROJECT_ROOT / rel).is_file() for rel in shared), "共用样式、计算库与政策预设都在")
    presets_text = (PROJECT_ROOT / "static/tools/_shared/presets.js").read_text(encoding="utf-8")
    check("2026" in presets_text and "23493" in presets_text and "4699" in presets_text,
          "政策预设里写的是 2026 年度成都口径（23493 / 4699）")
    check("rst.sc.gov.cn" in presets_text, "政策预设里带了数据来源链接")
    check((PROJECT_ROOT / "tools" / "check_finance.js").is_file(),
          "金融计算回归测试脚本存在（node tools/check_finance.js）")

    # 便携性：整包拷到别的电脑也要能跑，所以 run.py 会用到的模块必须都在项目内的 _vendor 里，
    # 不能依赖这台机器上恰好装了什么（这正是 Web 框架曾经漏掉的原因）
    vendor = PROJECT_ROOT / "_vendor"
    for mod in ("fastapi", "uvicorn", "starlette", "pydantic", "requests", "yt_dlp", "imageio_ffmpeg"):
        present = (vendor / mod).is_dir() or (vendor / f"{mod}.py").is_file()
        check(present, f"_vendor 里自带 {mod}（换电脑不必再装）")

    # 缺 meta.json 时应当用文件名兜底，而不是漏掉这个工具
    temp_page = app_mod.TOOLS_DIR / "_selftest_tool.html"
    temp_page.write_text("<html><body>临时</body></html>", encoding="utf-8")
    try:
        found = {t["id"]: t for t in app_mod.discover_tools()}
        check("_selftest_tool" in found and found["_selftest_tool"]["name"] == "_selftest_tool",
              "缺 meta.json 时用文件名兜底")
    finally:
        temp_page.unlink(missing_ok=True)
    check("_selftest_tool" not in {t["id"] for t in app_mod.discover_tools()},
          "删掉页面后工具列表同步移除")
    check("_shared" not in {t["id"] for t in tool_list},
          "共用资源目录不会被当成一个工具")

    # 老页面要真的迁到了工具目录里，且接口没变
    notes_html = (app_mod.TOOLS_DIR / "notes.html").read_text(encoding="utf-8")
    check("小红书视频转笔记" in notes_html and "/api/jobs" in notes_html,
          "笔记工具页已迁移，接口调用未变")
    # 合并后的「税率计算」要把五个计算器都装进去
    tax_html = (app_mod.TOOLS_DIR / "tax-calc.html").read_text(encoding="utf-8")
    for tab, label in (("salary", "工资 / 个税"), ("mortgage", "房贷"),
                       ("prepay", "提前还款"), ("carloan", "车贷"), ("vat", "增值税换算")):
        check(f'id="pane-{tab}"' in tax_html, f"「税率计算」含标签页：{label}")
    check(all(k in tax_html for k in ("不含税金额", "税额", "价税合计")), "增值税换算的三项结果字段都在")
    check(all(k in tax_html for k in ("s_salary", "m_price", "p_principal", "c_price", "v_amount")),
          "五个标签页的输入项 id 都带前缀（避免互相覆盖）")
    check('id="s_salaryMonths"' in tax_html, "工资页支持任意发薪月数（13/14/15…）")
    check((app_mod.TOOLS_DIR / "salary.html").exists() is False,
          "旧的独立工具页已删除，不会重复出现在列表里")

    english_html = (app_mod.TOOLS_DIR / "english.html").read_text(encoding="utf-8")
    check("编程英语" in english_html and "/api/english/chat" in english_html,
          "英语工具页已接入 /api/english/chat")
    check('id="micBtn"' in english_html and "/api/english/transcribe" in english_html,
          "英语工具页支持麦克风录音并接入语音转写接口")
    check('id="speakInputBtn"' in english_html and "speechSynthesis" in english_html,
          "英语工具页支持美式发音朗读（单词/句子/输入框试听）")
    check('id="practiceBar"' in english_html and "DSH_READBACK" in english_html,
          "英语工具页支持跟读目标与识别匹配度")
    check('id="azureScoreBtn"' in english_html and "/api/english/pronounce" in english_html,
          "英语工具页接入 Azure 发音评分按钮")
    check('id="pronProvider"' in english_html and 'value="xunfei"' in english_html,
          "英语工具页支持 Azure / 讯飞发音评测切换")
    check((PROJECT_ROOT / "xhs" / "pronounce.py").is_file(),
          "Azure 发音评测的 REST 调用在 xhs/pronounce.py")
    check((PROJECT_ROOT / "xhs" / "xunfei.py").is_file(),
          "讯飞语音评测的 WebSocket 调用在 xhs/xunfei.py")
    check((PROJECT_ROOT / "tools" / "check_pronounce.py").is_file(),
          "Azure 发音评测有离线测试（tools/check_pronounce.py）")
    check((PROJECT_ROOT / "tools" / "check_xunfei.py").is_file(),
          "讯飞语音评测有离线测试（tools/check_xunfei.py）")
    check("pronunciation" in (PROJECT_ROOT / "config.example.json").read_text(encoding="utf-8"),
          "配置样例里有 pronunciation 段")
    check((PROJECT_ROOT / "static" / "tools" / "_shared" / "readback.js").is_file(),
          "跟读匹配的纯函数在 _shared/readback.js")
    check((PROJECT_ROOT / "tools" / "check_readback.js").is_file(),
          "跟读匹配有 node 回归测试（tools/check_readback.js）")
    check((PROJECT_ROOT / "xhs" / "english.py").is_file(),
          "英语助手的提示词与调用逻辑在 xhs/english.py")

    keywords_html = (app_mod.TOOLS_DIR / "keywords.html").read_text(encoding="utf-8")
    check("多语言关键字" in keywords_html and "/api/keywords/ai" in keywords_html
          and "/api/keywords/github" in keywords_html,
          "多语言关键字页已接入 AI 解释与 GitHub API 示例")
    check((PROJECT_ROOT / "xhs" / "keywords.py").is_file(),
          "关键字查询的后端逻辑在 xhs/keywords.py")
    check((PROJECT_ROOT / "static" / "tools" / "_shared" / "keywords-data.js").is_file(),
          "本地关键字词库 keywords-data.js 存在")

    lottery_html = (app_mod.TOOLS_DIR / "lottery.html").read_text(encoding="utf-8")
    check("彩票模拟摇号" in lottery_html and "/api/lottery/simulate" in lottery_html,
          "彩票工具页已接入 /api/lottery/simulate")
    check((PROJECT_ROOT / "xhs" / "lottery.py").is_file(),
          "彩票历史数据、注数公式与模拟算法在 xhs/lottery.py")
    check((PROJECT_ROOT / "tools" / "check_lottery.py").is_file(),
          "彩票注数/范围/可复现性有离线测试")
    check((app_mod.TOOLS_DIR / "lottery.meta.json").is_file(),
          "彩票工具有 meta.json")

    file_server.shutdown()
    stub_server.shutdown()
    gh_server.shutdown()
    print(f"\n生成的笔记：{markdown_files[0]}")
    return _report()


def _report() -> int:
    print("\n" + "-" * 64)
    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print("-" * 64)
    print(f"  通过 {len(results) - failed}/{len(results)} 项")
    print("-" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
