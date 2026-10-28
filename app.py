# -*- coding: utf-8 -*-
"""FastAPI 服务：给网页提供配置、提交作业、实时进度（SSE）和结果读取。

职责边界：
    路由只做「校验参数 → 调 xhs.* → 序列化」，业务逻辑都在 xhs/ 里，
    这样以后要加 CLI 或定时任务时可以复用同一套逻辑。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import queue
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from xhs import asr as asr_mod
from xhs import config as config_mod
from xhs import english as english_mod
from xhs import football as football_mod
from xhs import jobs as jobs_mod
from xhs import keywords as keywords_mod
from xhs import learning as learning_mod
from xhs import lottery as lottery_mod
from xhs import pipeline as pipeline_mod
from xhs import pronounce as pronounce_mod
from xhs import review as review_mod
from xhs import summarize as summarize_mod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
LOG = logging.getLogger("app")

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
# 每个工具一个页面：static/tools/<id>.html + 可选的 <id>.meta.json（名称/说明/图标/排序）
TOOLS_DIR = STATIC_DIR / "tools"
URL_RE = re.compile(r"https?://[^\s<>\"'）)】\]]+", re.I)
MAX_LINKS_PER_JOB = 20
FINAL_STATES = ("done", "partial", "failed")

app = FastAPI(title="工具箱", version="1.1.0")
# 工具之间共用的样式和计算库放这里（放在 static/tools/_shared/ 下），
# 避免同一个公式在每个工具里各写一遍、时间久了算法不一致
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
registry = jobs_mod.JobRegistry()
# 单个作业内部本来就是串行的，这里限制同时跑几个作业，避免把带宽和 API 配额打满
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="xhs-job")


# ------------------------------------------------------------------ 页面

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail="缺少 static/index.html")
    return HTMLResponse(page.read_text(encoding="utf-8"))


def discover_tools() -> list[dict]:
    """扫描 static/tools/，列出所有工具。

    新增工具只需要放两个文件（<id>.html 和可选的 <id>.meta.json），
    不用改任何 Python 代码 —— 工具箱要能长期往里加东西，扩展成本必须接近零。
    """
    tools: list[dict] = []
    if not TOOLS_DIR.is_dir():
        return tools
    for page in sorted(TOOLS_DIR.glob("*.html")):
        tool_id = page.stem
        meta: dict = {}
        meta_path = TOOLS_DIR / f"{tool_id}.meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                # 元数据坏了不影响使用，用文件名兜底显示
                LOG.warning("工具 %s 的 meta.json 读不出来：%s", tool_id, exc)
        tools.append(
            {
                "id": tool_id,
                "name": str(meta.get("name") or tool_id),
                "description": str(meta.get("description") or ""),
                "icon": str(meta.get("icon") or "🔧"),
                "order": int(meta.get("order") or 100),
                "url": f"/tool/{tool_id}",
            }
        )
    tools.sort(key=lambda item: (item["order"], item["id"]))
    return tools


@app.get("/api/tools")
async def list_tools() -> dict:
    return {"tools": discover_tools()}


@app.get("/tool/{tool_id}", response_class=HTMLResponse)
async def tool_page(tool_id: str) -> HTMLResponse:
    """返回某个工具的页面。只允许字母数字下划线短横，挡住路径穿越。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", tool_id or ""):
        raise HTTPException(status_code=400, detail="工具 id 不合法")
    page = TOOLS_DIR / f"{tool_id}.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail=f"没有这个工具：{tool_id}")
    return HTMLResponse(page.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 状态与配置

def _deps_status() -> dict:
    status = {"yt_dlp": False, "ffmpeg": False, "vendor_dir": str(PROJECT_ROOT / "_vendor")}
    try:
        import yt_dlp

        status["yt_dlp"] = True
        status["yt_dlp_version"] = yt_dlp.version.__version__
    except ImportError:
        pass
    try:
        from xhs import audio as audio_mod

        status["ffmpeg"] = True
        status["ffmpeg_path"] = audio_mod.ffmpeg_exe()
    except Exception as exc:  # 依赖没装好时说清楚，而不是等用户跑到一半才报错
        status["ffmpeg_error"] = str(exc)
    status["ready"] = status["yt_dlp"] and status["ffmpeg"]
    return status


@app.get("/api/config")
async def get_config() -> dict:
    config = config_mod.load()
    return {
        "config": config_mod.masked_view(config),
        "asr_presets": config_mod.ASR_PRESETS,
        "summarizer_presets": config_mod.SUMMARIZER_PRESETS,
        "deps": _deps_status(),
        "output_dir": str(config_mod.output_dir(config)),
    }


@app.post("/api/config")
async def update_config(payload: dict) -> dict:
    try:
        config = config_mod.load()
        merged = config_mod.apply_update(config, payload or {})
        config_mod.save(merged)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "config": config_mod.masked_view(config_mod.load())}


@app.post("/api/selftest")
async def selftest(payload: dict) -> dict:
    """联网验证 key / 模型名。ASR 用 1.5 秒静音，几乎不产生费用。"""
    target = (payload or {}).get("target")
    config = config_mod.load()
    try:
        if target == "asr":
            cfg = config_mod.effective_asr(config)
            result = await asyncio.to_thread(asr_mod.selftest, cfg, config_mod.workdir(config) / "_selftest")
        elif target == "llm":
            cfg = config_mod.effective_summarizer(config)
            result = await asyncio.to_thread(summarize_mod.selftest, cfg)
        else:
            raise HTTPException(status_code=400, detail="target 只能是 asr 或 llm")
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": str(exc)})
    return {"ok": True, "result": result}


@app.post("/api/english/chat")
async def english_chat(payload: dict) -> dict:
    """编程英语学习助手：词汇查询 / 口语练习。"""
    body = payload or {}
    config = config_mod.load()
    try:
        result = await asyncio.to_thread(
            english_mod.chat,
            body.get("messages"),
            body.get("mode") or "vocab",
            body.get("level") or "intermediate",
            config,
        )
    except english_mod.EnglishError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # 不把内部堆栈丢给前端，但日志里留全量
        LOG.exception("英语助手调用失败")
        raise HTTPException(status_code=502, detail=f"英语助手暂时不可用：{exc}") from exc
    return {"ok": True, **result}


@app.post("/api/english/transcribe")
async def english_transcribe(payload: dict) -> dict:
    """把浏览器录的一段语音转成文字（复用已配置的 ASR）。"""
    body = payload or {}
    try:
        result = await asyncio.to_thread(
            english_mod.transcribe,
            body.get("audio"),
            body.get("mime") or "",
            config_mod.load(),
        )
    except english_mod.EnglishError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("英语语音转写失败")
        raise HTTPException(status_code=502, detail=f"语音转写暂时不可用：{exc}") from exc
    return {"ok": True, **result}


@app.post("/api/english/pronounce")
async def english_pronounce(payload: dict) -> dict:
    """Azure 发音评测：跟读音频 + 目标句 → 音素级分数。"""
    body = payload or {}
    try:
        result = await asyncio.to_thread(
            pronounce_mod.assess,
            body.get("audio"),
            body.get("mime") or "",
            body.get("target") or "",
            config_mod.load(),
        )
    except pronounce_mod.PronounceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("Azure 发音评测失败")
        raise HTTPException(status_code=502, detail=f"发音评测暂时不可用：{exc}") from exc
    return {"ok": True, **result}


@app.post("/api/keywords/ai")
async def keywords_ai(payload: dict) -> dict:
    """多语言关键字：让模型按语言解释一个关键字。"""
    body = payload or {}
    try:
        result = await asyncio.to_thread(
            keywords_mod.explain,
            body.get("keyword") or "",
            body.get("lang") or "all",
            config_mod.load(),
        )
    except keywords_mod.KeywordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("多语言关键字 AI 解释失败")
        raise HTTPException(status_code=502, detail=f"AI 解释暂时不可用：{exc}") from exc
    return {"ok": True, **result}


@app.get("/api/keywords/github")
async def keywords_github(q: str = "", limit: int = 5) -> dict:
    """真实调用 GitHub REST API，演示 Python 如何查看/调用第三方 API。"""
    try:
        return await asyncio.to_thread(keywords_mod.github_search, q, limit, config_mod.load())
    except Exception as exc:
        LOG.exception("GitHub 搜索失败")
        return {"ok": False, "error": f"GitHub 搜索失败：{exc}"}


# ------------------------------------------------------------------ 彩票

@app.get("/api/lottery/games")
async def lottery_games() -> dict:
    """彩种与玩法规则，不触发历史数据请求。"""
    return {"ok": True, "games": lottery_mod.public_games()}


@app.post("/api/lottery/simulate")
async def lottery_simulate(payload: dict) -> dict:
    """基于官方历史数据生成模拟摇号结果。"""
    body = payload or {}
    try:
        return await asyncio.to_thread(
            lottery_mod.simulate,
            body.get("game") or "dlt",
            body.get("play") or "single",
            body.get("params") or {},
            body.get("strategy") or "hot",
            body.get("window") or 100,
            body.get("seed"),
            bool(body.get("append")),
            config_mod.load(),
        )
    except lottery_mod.LotteryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("彩票模拟摇号失败")
        raise HTTPException(status_code=502, detail=f"模拟摇号暂时不可用：{exc}") from exc


@app.get("/api/lottery/saved")
async def lottery_saved() -> dict:
    """历史摇号记录。"""
    return await asyncio.to_thread(
        lambda: {"ok": True, "records": lottery_mod.load_saved(config_mod.load()).get("records", [])}
    )


@app.post("/api/lottery/saved")
async def lottery_save(payload: dict) -> dict:
    """保存一次模拟结果。"""
    try:
        record = await asyncio.to_thread(lottery_mod.save_result, payload or {}, config_mod.load())
        return {"ok": True, "record": record}
    except lottery_mod.LotteryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("保存彩票记录失败")
        raise HTTPException(status_code=502, detail=f"保存失败：{exc}") from exc


@app.delete("/api/lottery/saved/{record_id}")
async def lottery_delete(record_id: str) -> dict:
    """删除一条历史记录。"""
    ok = await asyncio.to_thread(lottery_mod.delete_saved, record_id, config_mod.load())
    if not ok:
        raise HTTPException(status_code=404, detail="找不到这条记录")
    return {"ok": True}


@app.post("/api/lottery/saved/{record_id}/check")
async def lottery_check(record_id: str, payload: dict | None = None) -> dict:
    """用下一期开奖核对是否中奖。"""
    issue = str((payload or {}).get("issue") or "")
    try:
        record = await asyncio.to_thread(
            lottery_mod.check_saved, record_id, config_mod.load(), issue
        )
        return {"ok": True, "record": record, "check": record.get("check")}
    except lottery_mod.LotteryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("核对彩票记录失败")
        raise HTTPException(status_code=502, detail=f"核对失败：{exc}") from exc


@app.get("/api/football/daily")
async def football_daily(day: str = "") -> dict:
    """500.com 竞彩赛程/赔率，返回当日足球分析。"""
    target = day or dt.date.today().isoformat()
    try:
        return await asyncio.to_thread(football_mod.daily, target, config_mod.load())
    except football_mod.FootballError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("足球赛程分析失败")
        raise HTTPException(status_code=502, detail=f"足球数据暂时不可用：{exc}") from exc


# ------------------------------------------------------------------ 学习路径

@app.get("/api/model/settings")
async def model_settings() -> dict:
    """工具箱默认模型（未单独设置的工具使用它）。"""
    return {"ok": True, "settings": config_mod.default_model_view(config_mod.load())}


@app.post("/api/model/settings")
async def model_save_settings(payload: dict) -> dict:
    try:
        settings = await asyncio.to_thread(
            config_mod.save_default_model, payload or {}, config_mod.load())
        return {"ok": True, "settings": settings}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("保存默认模型设置失败")
        raise HTTPException(status_code=502, detail=f"保存设置失败：{exc}") from exc


@app.get("/api/learning/settings")
async def learning_settings() -> dict:
    return {"ok": True, "settings": await asyncio.to_thread(
        learning_mod.settings_view, config_mod.load())}


@app.post("/api/learning/settings")
async def learning_save_settings(payload: dict) -> dict:
    try:
        settings = await asyncio.to_thread(
            learning_mod.save_settings, payload or {}, config_mod.load())
        return {"ok": True, "settings": settings}
    except learning_mod.LearningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("保存学习模型设置失败")
        raise HTTPException(status_code=502, detail=f"保存设置失败：{exc}") from exc


@app.get("/api/learning/plans")
async def learning_plans() -> dict:
    return await asyncio.to_thread(
        lambda: {"ok": True, "plans": learning_mod.list_plans(config_mod.load())}
    )


@app.post("/api/learning/plans")
async def learning_create(payload: dict) -> dict:
    body = payload or {}
    try:
        plan = await asyncio.to_thread(
            learning_mod.generate_plan,
            body.get("topic") or "",
            body.get("goal") or "",
            body.get("current_level") or "零基础",
            body.get("weekly_hours") or 6,
            body.get("weeks") or 12,
            config_mod.load(),
        )
        return {"ok": True, "plan": plan}
    except learning_mod.LearningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("生成学习计划失败")
        raise HTTPException(status_code=502, detail=f"生成学习计划失败：{exc}") from exc


@app.get("/api/learning/plans/{plan_id}")
async def learning_get(plan_id: str) -> dict:
    try:
        return {"ok": True, "plan": await asyncio.to_thread(
            learning_mod.get_plan, plan_id, config_mod.load())}
    except learning_mod.LearningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/learning/plans/{plan_id}")
async def learning_delete(plan_id: str) -> dict:
    ok = await asyncio.to_thread(learning_mod.delete_plan, plan_id, config_mod.load())
    if not ok:
        raise HTTPException(status_code=404, detail="找不到这份学习计划")
    return {"ok": True}


@app.post("/api/learning/plans/{plan_id}/stages/{stage_index}/exam")
async def learning_exam(plan_id: str, stage_index: int) -> dict:
    try:
        result = await asyncio.to_thread(
            learning_mod.generate_exam, plan_id, stage_index, config_mod.load())
        return {"ok": True, **result}
    except learning_mod.LearningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("生成阶段考核失败")
        raise HTTPException(status_code=502, detail=f"生成考核失败：{exc}") from exc


@app.post("/api/learning/plans/{plan_id}/stages/{stage_index}/submit")
async def learning_submit(plan_id: str, stage_index: int, payload: dict) -> dict:
    answers = (payload or {}).get("answers")
    try:
        result = await asyncio.to_thread(
            learning_mod.submit_exam, plan_id, stage_index, answers, config_mod.load())
        return {"ok": True, **result}
    except learning_mod.LearningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("提交阶段考核失败")
        raise HTTPException(status_code=502, detail=f"提交考核失败：{exc}") from exc


@app.post("/api/learning/plans/{plan_id}/stages/{stage_index}/reset")
async def learning_reset(plan_id: str, stage_index: int) -> dict:
    try:
        plan = await asyncio.to_thread(
            learning_mod.reset_stage, plan_id, stage_index, config_mod.load())
        return {"ok": True, "plan": plan}
    except learning_mod.LearningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------------------------ 代码审查

@app.get("/api/review/settings")
async def review_settings() -> dict:
    return {"ok": True, "settings": await asyncio.to_thread(
        review_mod.settings_view, config_mod.load())}


@app.post("/api/review/pick")
async def review_pick(payload: dict | None = None) -> dict:
    kind = str((payload or {}).get("kind") or "dir")
    try:
        result = await asyncio.to_thread(review_mod.pick_path, kind)
        return {"ok": True, **result}
    except review_mod.ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("打开路径选择框失败")
        raise HTTPException(status_code=502, detail=f"打开选择框失败：{exc}") from exc


@app.post("/api/review/settings")
async def review_save_settings(payload: dict) -> dict:
    try:
        settings = await asyncio.to_thread(
            review_mod.save_settings, payload or {}, config_mod.load())
        return {"ok": True, "settings": settings}
    except review_mod.ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("保存代码审查模型设置失败")
        raise HTTPException(status_code=502, detail=f"保存设置失败：{exc}") from exc


@app.get("/api/review/info")
async def review_info() -> dict:
    return {"ok": True, **await asyncio.to_thread(review_mod.info, config_mod.load())}


@app.post("/api/review/run")
async def review_run(payload: dict) -> dict:
    body = payload or {}
    try:
        result = await asyncio.to_thread(
            review_mod.run,
            body.get("project") or "",
            body.get("mode") or "scan",
            body.get("paths") or "",
            body.get("exclude") or "",
            int(body.get("max_tokens") or 0),
            config_mod.load(),
            bool(body.get("quick")),
        )
        return {"ok": True, **result}
    except review_mod.ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("代码审查失败")
        raise HTTPException(status_code=502, detail=f"代码审查失败：{exc}") from exc


@app.get("/api/review/report/{report_id}")
async def review_report(report_id: str, download: bool = False):
    try:
        text = await asyncio.to_thread(review_mod.read_report, report_id)
        headers = {}
        if download:
            headers["Content-Disposition"] = (
                f'attachment; filename="code-review-{report_id}.md"'
            )
        return PlainTextResponse(
            text, media_type="text/markdown; charset=utf-8", headers=headers
        )
    except review_mod.ReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/review/json/{report_id}")
async def review_json(report_id: str):
    try:
        path = await asyncio.to_thread(review_mod.report_json_path, report_id)
        return FileResponse(path, media_type="application/json", filename=path.name)
    except review_mod.ReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------------ 作业

@app.post("/api/jobs")
async def create_job(payload: dict) -> dict:
    raw = (payload or {}).get("links")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="links 需要是字符串或字符串数组")

    # 分享文案里常常混着别的话，直接抠出 URL；顺便去重
    links: list[str] = []
    for chunk in raw:
        for match in URL_RE.findall(str(chunk)):
            link = match.rstrip("，。、；！？")  # 中文标点常被粘在链接尾巴上
            if link not in links:
                links.append(link)

    if not links:
        raise HTTPException(status_code=400, detail="没有识别到链接，请粘贴小红书分享链接")
    if len(links) > MAX_LINKS_PER_JOB:
        raise HTTPException(status_code=400, detail=f"一次最多处理 {MAX_LINKS_PER_JOB} 条，当前 {len(links)} 条")

    config = config_mod.load()
    deps = _deps_status()
    if not deps["ready"]:
        raise HTTPException(
            status_code=400,
            detail=f"依赖未就绪：{'；'.join(filter(None, [deps.get('ffmpeg_error', ''), '' if deps['yt_dlp'] else '缺少 yt-dlp']))}",
        )

    job = registry.create(links, config)
    job.emit(f"已加入队列，共 {len(links)} 条链接", kind="queued", progress=0)
    executor.submit(pipeline_mod.run_job, job, config)
    LOG.info("作业 %s 已提交：%d 条链接", job.id, len(links))
    return {"job_id": job.id, "count": len(links)}


@app.get("/api/jobs")
async def list_jobs(limit: int = 20) -> dict:
    return {"jobs": registry.recent(limit)}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到这个作业（服务重启后历史会清空）")
    return job.to_dict()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="找不到这个作业")

    async def stream():
        # 先订阅再取历史：否则这两步之间产生的进度会丢
        channel = job.subscribe()
        last_seq = 0
        try:
            for event in job.events_since(0):
                last_seq = event["seq"]
                yield _sse(event)

            # 作业在补历史的过程中就结束了，就没必要保持连接
            if job.status in FINAL_STATES and last_seq >= job.latest_seq():
                return

            while True:
                try:
                    event = await asyncio.to_thread(channel.get, True, 15)
                except queue.Empty:
                    yield ": keepalive\n\n"  # 心跳，防止中间层掐掉长连接
                    if job.status in FINAL_STATES:
                        return
                    continue
                if event["seq"] <= last_seq:
                    continue
                last_seq = event["seq"]
                yield _sse(event)
                if event["kind"] == "finished":
                    return
        except asyncio.CancelledError:  # 用户切走页面
            raise
        finally:
            job.unsubscribe(channel)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------ 结果文件

def _safe_output_file(name: str) -> Path:
    """只允许读取输出目录下的文件，挡住 ../ 之类的路径穿越。"""
    if not name or any(sep in name for sep in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="文件名不合法")
    config = config_mod.load()
    target = (config_mod.output_dir(config) / name).resolve()
    if target.parent != config_mod.output_dir(config).resolve():
        raise HTTPException(status_code=400, detail="文件名不合法")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


@app.get("/api/files")
async def list_files(limit: int = 100) -> dict:
    config = config_mod.load()
    directory = config_mod.output_dir(config)
    files = sorted(directory.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return {
        "output_dir": str(directory),
        "files": [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "modified": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            for p in files
        ],
    }


@app.get("/api/files/{name}")
async def read_file(name: str) -> dict:
    path = _safe_output_file(name)
    return {"name": path.name, "content": path.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/files/{name}/download")
async def download_file(name: str) -> FileResponse:
    path = _safe_output_file(name)
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@app.post("/api/open-folder")
async def open_folder() -> dict:
    """在资源管理器里打开输出目录（仅 Windows 本地使用）。"""
    config = config_mod.load()
    directory = config_mod.output_dir(config)
    try:
        import os

        os.startfile(str(directory))  # noqa: S606 - 本地工具，打开自己的输出目录
    except (AttributeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"打不开目录 {directory}：{exc}") from exc
    return {"ok": True, "output_dir": str(directory)}
