# -*- coding: utf-8 -*-
"""编排：一条链接从解析到落盘 markdown 的完整流程。

阶段划分（前端进度条按这个走）：
    解析链接 → 下载媒体 → 提取音频 → 语音转写 → 生成总结 → 写入文件

失败处理原则：
    单条链接失败不影响同批其它链接；失败时保留中间产物（视频、音频）方便排查，
    成功时清理中间产物以省磁盘。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import asr as asr_mod
from . import audio as audio_mod
from . import config as config_mod
from . import download as download_mod
from . import jobs as jobs_mod
from . import resolve as resolve_mod
from . import store as store_mod
from . import summarize as summarize_mod

LOG = logging.getLogger(__name__)

# 每个阶段在「单条链接」内的进度占比
_STEPS = {
    "解析链接": (0, 6),
    "下载媒体": (6, 32),
    "提取音频": (32, 45),
    "语音转写": (45, 76),
    "生成总结": (76, 96),
    "写入文件": (96, 100),
}


class Pipeline:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.output_dir = config_mod.output_dir(config)
        self.workdir = config_mod.workdir(config)
        self.keep_media = bool((config.get("download") or {}).get("keep_media", False))

    # ------------------------------------------------------------ 进度计算

    def _pct(self, index: int, total: int, stage: str, within: float = 0.0) -> int:
        """把「第几条 × 阶段内进度」换算成整批的百分比。"""
        low, high = _STEPS.get(stage, (0, 100))
        stage_value = low + (high - low) * max(0.0, min(1.0, within))
        per_link = 100.0 / max(1, total)
        return int(index * per_link + stage_value * per_link / 100.0)

    # ------------------------------------------------------------ 单条处理

    def process_link(self, job: jobs_mod.Job, item: jobs_mod.JobItem, index: int, total: int) -> None:
        url = item.url
        item.status = "running"

        def emit_step(stage: str, message: str = "", within: float = 0.0) -> None:
            job.emit(message or stage, kind="stage", stage=stage,
                     progress=self._pct(index, total, stage, within), item_index=index)

        # 1. 解析链接 -------------------------------------------------------
        emit_step("解析链接", f"解析链接：{url}", 0.2)
        resolved = resolve_mod.resolve(
            url,
            proxy=(self.config.get("download") or {}).get("proxy", ""),
        )
        final_url = resolved["url"]
        item.note_id = resolved["note_id"] or item.note_id
        emit_step("解析链接", f"解析完成：{final_url}", 1.0)

        workdir = self.workdir / (item.note_id or f"job{index + 1}")
        if workdir.exists():
            # 同一条笔记重复处理时，清掉上次的半成品，避免混用旧音频
            shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)

        meta: dict = {
            "url": final_url,
            "note_id": item.note_id,
            "title": "",
            "author": "",
            "duration": None,
            "content_type": "视频",
            "published": "",
            # 作者写的正文：常含项目名、链接、清单，比语音转写更准确，必须喂给模型
            "note_text": "",
        }
        transcript: dict | None = None
        media_path: Path | None = None

        # 2. 下载媒体 -------------------------------------------------------
        emit_step("下载媒体", "正在下载媒体（需要已登录的浏览器 cookie 时请先在设置里选好）…", 0.0)
        download_error = ""
        try:
            media = download_mod.download_media(
                final_url,
                workdir,
                self.config,
                emit=lambda message: job.emit(message, kind="log", item_index=index),
            )
            media_path = media["path"]
            meta.update(
                {
                    "title": media["title"],
                    "author": media["author"],
                    "duration": media["duration"],
                    "published": meta.get("published") or "",
                    "note_text": store_mod.clean_note_text(media.get("description") or ""),
                }
            )
            if media.get("webpage_url"):
                meta["url"] = media["webpage_url"]
            if not meta["note_id"] and media.get("note_id"):
                meta["note_id"] = media["note_id"]

            # 下载器给不出作者（小红书常见）或没带正文时，去页面里补一次；
            # 正文里有项目名和链接，缺了会明显影响总结质量
            if not meta["author"] or not meta["note_text"]:
                extra = resolve_mod.fetch_note_text(
                    meta["url"],
                    meta["note_id"],
                    proxy=(self.config.get("download") or {}).get("proxy", ""),
                )
                if extra:
                    meta["author"] = meta["author"] or extra.get("author") or ""
                    meta["note_text"] = meta["note_text"] or store_mod.clean_note_text(extra.get("desc") or "")
                    meta["published"] = meta["published"] or extra.get("published") or ""

            emit_step("下载媒体", f"下载完成：{media_path.name}（{media['size'] / 1024 / 1024:.1f} MB）", 1.0)
        except download_mod.DownloadError as exc:
            download_error = str(exc)
            LOG.warning("下载失败，尝试图文正文兜底：%s", exc)
            emit_step("下载媒体", f"下载失败：{download_error}", 1.0)

        # 3. 图文笔记（或下载失败）时退回正文 -------------------------------
        if media_path is None:
            note = resolve_mod.fetch_note_text(
                final_url,
                item.note_id,
                proxy=(self.config.get("download") or {}).get("proxy", ""),
            )
            if not note or not (note.get("desc") or note.get("title")):
                raise RuntimeError(
                    f"{download_error}\n并且没能抓到图文正文（可能是私密笔记或需要登录）。"
                    "请检查笔记是否公开，或换个浏览器 cookie 再试。"
                )
            meta.update(
                {
                    "title": note["title"] or note["desc"][:30],
                    "author": note["author"],
                    "content_type": note["content_type"],
                    "published": note["published"],
                }
            )
            if note.get("note_id"):
                item.note_id = note["note_id"]
                meta["note_id"] = note["note_id"]
            body_text = note["desc"] or note["title"]
            transcript = {"text": body_text, "segments": []}
            emit_step("提取音频", "这是图文笔记，跳过音频处理", 1.0)
            emit_step("语音转写", f"直接使用笔记正文（{len(body_text)} 字）", 1.0)

        # 4~5. 提取音频 + 转写 ---------------------------------------------
        else:
            emit_step("提取音频", "正在提取音轨并转码…", 0.2)
            prepared = audio_mod.prepare_audio(
                media_path,
                workdir,
                self.config,
                emit=lambda message: job.emit(message, kind="log", item_index=index),
            )
            if not meta.get("duration") and prepared.get("duration"):
                meta["duration"] = prepared["duration"]
            emit_step(
                "提取音频",
                f"音频就绪：{prepared['bytes'] / 1024 / 1024:.1f} MB，"
                f"{len(prepared['parts'])} 个片段，时长 {store_mod.format_duration(prepared.get('duration'))}",
                1.0,
            )

            asr_cfg = config_mod.effective_asr(self.config)

            def on_part(done: int, total_parts: int) -> None:
                job.emit(
                    f"正在转写第 {done + 1}/{total_parts} 段",
                    kind="stage",
                    stage="语音转写",
                    progress=self._pct(index, total, "语音转写", done / max(1, total_parts)),
                    item_index=index,
                )

            transcript = asr_mod.transcribe_parts(
                prepared["parts"],
                asr_cfg,
                emit=lambda message: job.emit(message, kind="log", item_index=index),
                on_part=on_part,
            )
            meta["asr"] = transcript.get("model", "")
            failures = transcript.get("failures") or []
            if failures:
                meta["asr_failures"] = failures
                detail = "；".join(
                    f"第 {f['index']}/{f['total']} 段（{f['error']}）" for f in failures
                )
                item.warning = f"有 {len(failures)} 段音频转写失败，笔记缺少这部分内容：{detail}"
                job.emit(f"⚠ {item.warning}", kind="log", item_index=index)
                emit_step(
                    "语音转写",
                    f"转写完成：{len(prepared['parts']) - len(failures)}/{len(prepared['parts'])} 段成功"
                    f"，{len(failures)} 段失败（已在文档中标注）",
                    1.0,
                )
            else:
                emit_step("语音转写", f"转写完成，共 {len(transcript['text'])} 字", 1.0)

            if not self.keep_media:
                # 成功后清理中间产物：原视频和切片通常几十上百 MB
                for leftover in list(workdir.glob("*")):
                    try:
                        leftover.unlink()
                    except OSError as exc:
                        LOG.debug("清理 %s 失败：%s", leftover, exc)

        item.title = meta.get("title") or ""
        item.content_type = meta.get("content_type", "")
        item.duration = meta.get("duration")
        item.transcript_chars = len((transcript or {}).get("text") or "")

        # 6. 生成总结 -------------------------------------------------------
        emit_step("生成总结", "正在调用大模型生成结构化总结…", 0.2)
        summarizer_cfg = config_mod.effective_summarizer(self.config)
        meta["summarizer"] = f"{summarizer_cfg['label']}/{summarizer_cfg['model']}"
        outcome = summarize_mod.summarize(
            meta,
            transcript,
            self.config,
            emit=lambda message: job.emit(message, kind="log", item_index=index),
        )
        body = outcome["body"]
        # 工具清单以「正文里真正写出来的表格」为准：成文阶段可能补上提取阶段漏掉的工具，
        # 若用提取结果写 front matter，就会出现 front matter 与正文对不上的情况
        documented = store_mod.parse_tool_table(body)
        final_tools = documented or [
            {"name": t.get("name", ""), "url": t.get("url", "")} for t in outcome.get("tools") or []
        ]
        meta["tools"] = [t["name"] for t in final_tools if t.get("name")]
        meta["github_links"] = sum(1 for t in final_tools if t.get("url"))
        meta["topic_kind"] = outcome.get("kind_label", "")
        item.tools = list(meta["tools"])
        item.topic_kind = meta["topic_kind"]
        if meta["tools"]:
            emit_step(
                "生成总结",
                f"判断为「{meta['topic_kind']}」：{len(meta['tools'])} 个工具，"
                f"其中 {meta['github_links']} 个找到仓库地址",
                1.0,
            )
        else:
            emit_step("生成总结", f"判断为「{meta['topic_kind']}」，内容整理完成", 1.0)

        # 7. 落盘 -----------------------------------------------------------
        emit_step("写入文件", "正在写入 markdown…", 0.3)
        path = store_mod.write_markdown(self.output_dir, meta, body, transcript, self.config)
        item.output_path = str(path)
        item.status = "done"
        emit_step("写入文件", f"已保存：{path.name}", 1.0)
        job.emit(
            f"完成（{index + 1}/{total}）：{item.title or path.name}",
            kind="item",
            # stage 必须保持六个标准阶段之一，否则前端步骤条会失去高亮
            stage="写入文件",
            progress=self._pct(index, total, "写入文件", 1.0),
            item_index=index,
            extra={"item": item.to_dict()},
        )


def run_job(job: jobs_mod.Job, config: dict) -> None:
    """在后台线程里跑完一个作业。异常一律收敛成作业状态，不让线程静默死掉。"""
    pipeline = Pipeline(config)
    total = max(1, len(job.items))
    job.status = "running"

    # 把解析后的实际信息写进作业快照，前端右上角能直接看到用了什么模型
    try:
        asr_cfg = config_mod.effective_asr(config)
        job.config["_asr_label"] = f"{asr_cfg['label']} / {asr_cfg['model']}"
    except Exception as exc:
        job.config["_asr_label"] = f"配置不完整：{exc}"
    try:
        sum_cfg = config_mod.effective_summarizer(config)
        job.config["_summarizer_label"] = f"{sum_cfg['label']} / {sum_cfg['model']}"
    except Exception as exc:
        job.config["_summarizer_label"] = f"配置不完整：{exc}"
    job.config["_resolved_output_dir"] = str(pipeline.output_dir)

    successes = 0
    failures: list[str] = []

    for index, item in enumerate(job.items):
        try:
            pipeline.process_link(job, item, index, total)
            successes += 1
        except Exception as exc:  # 单条失败不拖垮整批
            LOG.exception("处理失败：%s", item.url)
            item.status = "failed"
            item.error = str(exc)
            failures.append(f"{item.url}：{exc}")
            job.emit(
                f"这条失败了：{exc}",
                kind="error",
                item_index=index,
                progress=pipeline._pct(index, total, "写入文件", 1.0),
                extra={"item": item.to_dict()},
            )

    if successes and not failures:
        job.finish("done")
    elif successes and failures:
        job.finish("partial", error="；".join(failures)[:800])
    else:
        job.finish("failed", error="；".join(failures)[:800] or "全部链接都处理失败了")
