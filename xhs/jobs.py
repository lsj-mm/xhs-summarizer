# -*- coding: utf-8 -*-
"""作业（job）状态与事件总线。

为什么不用全局变量直接往前端推：
    前端可能刷新页面后重新连上（重连后要能补齐历史进度），也可能开多个标签页
    看同一个作业。所以每个作业维护一份事件列表 + 若干订阅队列：
    重连时先发历史，再发增量。
"""

from __future__ import annotations

import datetime as _dt
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field

LOG = logging.getLogger(__name__)

MAX_JOBS = 50
SUBSCRIBER_QUEUE_SIZE = 500


@dataclass
class JobItem:
    """一条链接的处理结果。"""

    url: str
    status: str = "pending"  # pending / running / done / failed
    title: str = ""
    output_path: str = ""
    note_id: str = ""
    content_type: str = ""
    duration: float | None = None
    transcript_chars: int = 0
    # 这条笔记提到的工具名，前端会显示成标签
    tools: list[str] = field(default_factory=list)
    # 内容类型（工具推荐 / 问答 / 教程讲解 / 观点分享 / 资讯 / 其它）
    topic_kind: str = ""
    # 非致命问题的说明（例如部分音频段转写失败）
    warning: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "status": self.status,
            "title": self.title,
            "output_path": self.output_path,
            "file_name": self.output_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] if self.output_path else "",
            "note_id": self.note_id,
            "content_type": self.content_type,
            "duration": self.duration,
            "transcript_chars": self.transcript_chars,
            "tools": self.tools,
            "topic_kind": self.topic_kind,
            "warning": self.warning,
            "error": self.error,
        }


@dataclass
class Job:
    id: str
    links: list[str]
    config: dict = field(default_factory=dict)
    status: str = "queued"  # queued / running / done / partial / failed
    stage: str = "排队中"
    progress: int = 0
    created_at: str = field(default_factory=lambda: _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    finished_at: str = ""
    error: str = ""
    items: list[JobItem] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _subscribers: list[queue.Queue] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.items:
            self.items = [JobItem(url=url) for url in self.links]

    # ---------------------------------------------------------------- 事件

    def emit(self, message: str = "", *, kind: str = "log", stage: str | None = None,
             progress: int | None = None, item_index: int | None = None, extra: dict | None = None) -> dict:
        with self._lock:
            self._seq += 1
            if stage:
                self.stage = stage
            if progress is not None:
                self.progress = max(0, min(100, int(progress)))
            event = {
                "seq": self._seq,
                "ts": _dt.datetime.now().strftime("%H:%M:%S"),
                "kind": kind,
                "stage": self.stage,
                "progress": self.progress,
                "message": message,
                "item_index": item_index,
            }
            if extra:
                event.update(extra)
            self.events.append(event)
            subscribers = list(self._subscribers)

        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # 前端卡住时丢事件而不是阻塞后台线程
                LOG.debug("订阅队列已满，丢弃一条事件")
        return event

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def events_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["seq"] > seq]

    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    # ---------------------------------------------------------------- 快照

    def finish(self, status: str, error: str = "") -> None:
        self.status = status
        self.error = error
        self.finished_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if status in ("done", "partial"):
            self.progress = 100
            self.stage = "完成" if status == "done" else "部分完成"
        else:
            self.stage = "失败"
        self.emit(
            error or ("全部处理完成" if status == "done" else "处理结束"),
            kind="finished",
            stage=self.stage,
            progress=self.progress,
            extra={"status": status, "items": [i.to_dict() for i in self.items]},
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "links": self.links,
            "items": [item.to_dict() for item in self.items],
            "event_count": len(self.events),
            "output_dir": self.config.get("_resolved_output_dir", ""),
            "asr": self.config.get("_asr_label", ""),
            "summarizer": self.config.get("_summarizer_label", ""),
        }


class JobRegistry:
    def __init__(self, max_jobs: int = MAX_JOBS) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def create(self, links: list[str], config: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], links=list(links), config=dict(config))
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._max_jobs:
                oldest = self._order.pop(0)
                self._jobs.pop(oldest, None)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))[:limit]
            return [self._jobs[i].to_dict() for i in ids if i in self._jobs]
