# -*- coding: utf-8 -*-
"""代码审查：把阿里的 OpenCodeReview CLI 接进工具箱。

边界：
    - OCR 二进制默认从 _vendor/ocr/ocr.exe 查找，也可用 OCR_BIN 指定；
    - 使用工具箱当前默认模型（DeepSeek / 自定义 OpenAI 兼容）；
    - 只做本地项目审查，报告写到 work/codereview/；
    - AI 审查有误报/漏报，报告只作参考，不替代人工评审。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from . import config as config_mod

LOG = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUICK_LIMIT = 5
DEFAULT_EXCLUDES = ",".join([
    "**/.git/**", "**/_vendor/**", "**/node_modules/**", "**/__pycache__/**",
    "**/work/**", "**/output/**", "**/dist/**", "**/build/**", "**/.next/**",
    "**/coverage/**", "**/.venv/**", "**/venv/**", "**/*.min.js", "**/*.map",
    "**/*.lock", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock",
])
CODE_EXTS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".go",
    ".java", ".kt", ".kts", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".rs", ".php", ".rb", ".swift", ".sql", ".sh", ".ps1", ".html", ".css", ".scss",
}
SKIP_DIRS = {
    ".git", "_vendor", "node_modules", "__pycache__", "work", "output",
    "dist", "build", ".next", "coverage", ".venv", "venv", "site-packages",
}


class ReviewError(RuntimeError):
    """可直接展示给用户的错误。"""


def find_ocr() -> str:
    env = (os.environ.get("OCR_BIN") or "").strip()
    candidates = [
        Path(env) if env else None,
        config_mod.PROJECT_ROOT / "_vendor" / "ocr" / "ocr.exe",
        config_mod.PROJECT_ROOT / "ocr" / "ocr.exe",
        config_mod.PROJECT_ROOT.parent / "open-code-review-main" / "bin" / "ocr.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    found = shutil.which("ocr")
    if found:
        return found
    raise ReviewError(
        "没有找到 ocr.exe。请把 OpenCodeReview 的 Windows 版放到 "
        "_vendor/ocr/ocr.exe，或设置环境变量 OCR_BIN 指向它。"
    )


def _review_model(config: dict) -> dict:
    try:
        cfg = config_mod.effective_review_model(config)
    except RuntimeError as exc:
        raise ReviewError(str(exc)) from exc
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        raise ReviewError("还没有配置默认模型 API key，请先在首页「默认模型设置」里填写")
    url = (cfg.get("base_url") or "").rstrip("/")
    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    if cfg.get("provider") == "deepseek" and not url.endswith("/v1"):
        url += "/v1"
    if not url:
        raise ReviewError("默认模型的 Base URL 为空")
    return {**cfg, "ocr_url": url}


def _report_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)


def _target(project: str):
    """目录 → (repo_root, path_filter)；单文件 → (父目录, 文件名)。"""
    target = Path(project or str(PROJECT_ROOT)).expanduser().resolve()
    if target.is_dir():
        return target, target, ""
    if target.is_file():
        return target, target.parent, target.name
    raise ReviewError(f"项目路径不存在：{target}")


def _quick_paths(root: Path, limit: int = QUICK_LIMIT) -> list[str]:
    """挑最近修改的少量源码文件，避免一上来就全项目扫描。"""
    candidates = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() not in CODE_EXTS:
                continue
            try:
                size = path.stat().st_size
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if size <= 0 or size > 300_000:
                continue
            candidates.append((mtime, path.relative_to(root).as_posix()))
    candidates.sort(reverse=True)
    return [rel for _, rel in candidates[:limit]]


def pick_path(kind: str = "dir") -> dict:
    """弹出 Windows 原生选择框，返回绝对路径。"""
    if os.name != "nt":
        raise ReviewError("原生文件夹选择只支持 Windows；其它平台请直接粘贴路径")
    kind = "file" if kind == "file" else "dir"
    if kind == "dir":
        body = r"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = '选择要审查的项目文件夹'
$d.ShowNewFolderButton = $false
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.WindowState = 'Minimized'
$owner.Show()
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($d.SelectedPath)
}
$owner.Close()
"""
    else:
        body = r"""
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = '选择要审查的单个文件'
$d.CheckFileExists = $true
$d.Multiselect = $false
$d.Filter = '所有文件|*.*'
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.WindowState = 'Minimized'
$owner.Show()
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($d.FileName)
}
$owner.Close()
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", body],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, creationflags=0x08000000,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"打开选择框失败：{exc}") from exc
    path = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise ReviewError(f"选择框异常：{(proc.stderr or '')[-300:]}")
    return {"path": path, "cancelled": not bool(path), "kind": kind}


def settings_view(config: dict | None = None) -> dict:
    config = config or config_mod.load()
    section = dict(config.get("codereview") or {})
    provider = (section.get("provider") or "inherit").strip() or "inherit"
    try:
        effective = config_mod.effective_review_model(config)
        effective_info = {
            "label": effective.get("label", ""),
            "model": effective.get("model", ""),
            "base_url": effective.get("base_url", ""),
            "api_key_set": bool(effective.get("api_key")) or bool(effective.get("key_optional")),
        }
    except Exception as exc:
        effective_info = {"error": str(exc), "api_key_set": False}
    options = [{"value": "inherit", "label": "跟随工具箱设置"}]
    options += [{"value": name, "label": preset["label"]}
                for name, preset in config_mod.SUMMARIZER_PRESETS.items()]
    return {
        "provider": provider,
        "model": section.get("model") or "",
        "base_url": section.get("base_url") or "",
        "api_key_masked": config_mod.mask_key(section.get("api_key") or ""),
        "api_key_set": bool(section.get("api_key")),
        "effective": effective_info,
        "options": options,
    }


def save_settings(payload: dict, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    payload = payload or {}
    provider = str(payload.get("provider") or "inherit").strip() or "inherit"
    if provider not in ("inherit", *config_mod.SUMMARIZER_PRESETS.keys()):
        raise ReviewError("未知的模型厂商")
    base_url = str(payload.get("base_url") or "").strip()
    model = str(payload.get("model") or "").strip()
    if provider == "custom" and (not base_url or not model):
        raise ReviewError("自定义模型需要填写 Base URL 和模型名")
    if provider == "local" and not model:
        raise ReviewError("本地模型需要填写模型名，例如 qwen2.5:7b")
    section = dict(config.get("codereview") or {})
    old_provider = (section.get("provider") or "inherit").strip() or "inherit"
    section["provider"] = provider
    section["base_url"] = base_url
    section["model"] = model
    key = str(payload.get("api_key") or "").strip()
    if key:
        section["api_key"] = key
    elif provider != old_provider:
        section["api_key"] = ""
    config["codereview"] = section
    config_mod.save(config)
    return settings_view(config)


def _report_dir(config: dict) -> Path:
    path = config_mod.workdir(config) / "codereview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _human_review(comments: list[dict]) -> list[dict]:
    """挑出最需要人工确认的项，AI 审查不能直接当成结论。"""
    priority = {"high": 0, "medium": 1, "low": 2}
    selected = []
    for comment in comments:
        severity = str(comment.get("severity") or "unknown").lower()
        if severity not in priority:
            continue
        content = str(comment.get("content") or "").strip().replace("\n", " ")
        selected.append({
            "path": comment.get("path") or "",
            "line": comment.get("start_line") or 0,
            "severity": severity,
            "category": comment.get("category") or "",
            "reason": ("高危问题，优先确认" if severity == "high"
                       else "中危问题，建议复核" if severity == "medium"
                       else "低危建议，可择机确认"),
            "summary": content[:120],
        })
    selected.sort(key=lambda x: priority.get(x["severity"], 9))
    return selected


def _render_markdown(data: dict, meta: dict) -> str:
    summary = data.get("summary") or {}
    comments = data.get("comments") or []
    warnings = data.get("warnings") or []
    lines = [
        "# 代码审查报告",
        "",
        f"- 项目：`{meta['project']}`",
        f"- 模式：{meta['mode_name']}",
        f"- 模型：{meta['model']}",
        f"- 生成时间：{meta['created_at']}",
        f"- 状态：{data.get('status') or 'unknown'}",
        f"- 会话：`{data.get('session_id') or '—'}`",
        "",
        "## 概览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 审查文件 | {summary.get('files_reviewed', '—')} |",
        f"| 问题数 | {summary.get('comments', len(comments))} |",
        f"| Token | {summary.get('total_tokens', '—')} |",
        f"| 耗时 | {summary.get('elapsed', '—')} |",
        "",
    ]
    if warnings:
        lines += ["## 运行警告", ""]
        for warning in warnings:
            where = warning.get("file") or "全局"
            lines.append(f"- `{where}`：{warning.get('message') or warning}")
        lines.append("")
    project_summary = (data.get("project_summary") or "").strip()
    if project_summary:
        lines += ["## 项目总结", "", project_summary, ""]
    human = _human_review(comments)
    lines += ["## 需要人工核查", ""]
    if human:
        for item in human:
            where = item["path"] + (f":{item['line']}" if item["line"] else "")
            lines.append(f"- [{item['severity']}] `{where}`：{item['reason']}。{item['summary']}")
    else:
        lines.append("- 本次没有高/中危评论；仍建议人工确认关键路径和业务语义。")
    lines += [
        "",
        "- AI 未自动验证：运行时行为、业务规则、依赖漏洞、测试覆盖、性能、真实并发竞态。",
        "- AI 可能误报或漏报；报告中的建议不要未经确认直接批量应用。",
        "",
    ]
    lines += ["## 问题明细", ""]
    if not comments:
        if warnings:
            lines += ["本次没有生成有效评论，原因见上方运行警告。", ""]
        else:
            lines += ["未发现明显问题。", ""]
    else:
        for index, comment in enumerate(comments, 1):
            severity = comment.get("severity") or "unknown"
            category = comment.get("category") or "general"
            path = comment.get("path") or "—"
            start = comment.get("start_line") or 0
            end = comment.get("end_line") or start
            where = f"{path}:{start}" + (f"-{end}" if end and end != start else "")
            lines += [
                f"### {index}. [{severity}] {where}",
                "",
                f"- 类别：{category}",
                "",
                str(comment.get("content") or "").strip(),
                "",
            ]
            existing = str(comment.get("existing_code") or "").strip()
            suggestion = str(comment.get("suggestion_code") or "").strip()
            if existing:
                lines += ["**现有代码**", "", "```text", existing, "```", ""]
            if suggestion:
                lines += ["**建议代码**", "", "```text", suggestion, "```", ""]
    lines += [
        "---",
        "",
        "> 本报告由 OpenCodeReview + AI 模型自动生成，可能存在误报或漏报，仅供代码审查参考，不替代人工评审。",
        "",
    ]
    return "\n".join(lines)


def info(config: dict | None = None) -> dict:
    config = config or config_mod.load()
    try:
        ocr = find_ocr()
        ocr_error = ""
    except ReviewError as exc:
        ocr = ""
        ocr_error = str(exc)
    try:
        model = _review_model(config)
        model_label = f"{model.get('label')} / {model.get('model')}"
    except ReviewError as exc:
        model_label = ""
        ocr_error = ocr_error or str(exc)
    return {
        "ocr": ocr,
        "ocr_error": ocr_error,
        "model": model_label,
        "default_project": str(PROJECT_ROOT),
        "modes": [{"value": "scan", "label": "全量扫描"}, {"value": "review", "label": "Git 变更审查"}],
    }


def run(project: str, mode: str = "scan", paths: str = "", exclude: str = "",
        max_tokens: int = 0, config: dict | None = None,
        quick: bool = False) -> dict:
    config = config or config_mod.load()
    mode = "review" if mode == "review" else "scan"
    project_path, repo_root, single_file = _target(project)
    if single_file and mode == "review":
        raise ReviewError("选择单个文件时请使用「全量扫描」")
    if mode == "review":
        git_check = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if git_check.returncode != 0:
            raise ReviewError("Git 变更审查需要目标目录是 Git 仓库")

    ocr = find_ocr()
    try:
        # 让 OCR 输出中文；失败不影响审查
        subprocess.run(
            [ocr, "config", "set", "language", "zh"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        LOG.warning("设置 OCR 语言为中文失败")
    model = _review_model(config)
    report_id = _report_id()
    report_dir = _report_dir(config)
    json_path = report_dir / f"{report_id}.json"
    md_path = report_dir / f"{report_id}.md"

    args = [
        ocr, mode, "--repo", str(repo_root),
        "--format", "json", "--output", str(json_path),
        "--timeout", "15", "--concurrency", "4", "--color", "never",
    ]
    quick_used = bool(quick and not single_file and not paths.strip())
    if mode == "scan":
        scan_paths = single_file or paths.strip()
        if quick and not single_file and not scan_paths:
            picked = _quick_paths(repo_root)
            if not picked:
                raise ReviewError("没有找到可快速审查的源码文件，请改用全量扫描或指定路径")
            scan_paths = ",".join(picked)
        if scan_paths:
            args += ["--path", scan_paths]
        excludes = ",".join(x for x in (DEFAULT_EXCLUDES, exclude.strip()) if x)
        if excludes:
            args += ["--exclude", excludes]
        if int(max_tokens or 0) > 0:
            args += ["--max-tokens-budget", str(int(max_tokens))]

    env = os.environ.copy()
    env.update({
        "OCR_LLM_URL": model["ocr_url"],
        "OCR_LLM_TOKEN": model.get("api_key") or ("local" if model.get("key_optional") else ""),
        "OCR_LLM_MODEL": model.get("model") or "",
        "OCR_LLM_PROTOCOL": "openai",
        "OCR_USE_ANTHROPIC": "false",
        "OCR_LLM_TIMEOUT": "300",
    })
    created_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    started = dt.datetime.now()
    try:
        proc = subprocess.run(
            args, cwd=str(repo_root), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=16 * 60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError("代码审查超过 16 分钟，已停止；可以先用 --path 缩小范围") from exc
    except OSError as exc:
        raise ReviewError(f"启动 ocr 失败：{exc}") from exc

    if proc.returncode != 0:
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-1800:]
        raise ReviewError(f"OCR 审查失败（退出码 {proc.returncode}）：{tail.strip()}")
    if not json_path.is_file():
        raise ReviewError("OCR 没有生成 JSON 报告，请检查运行日志")

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"OCR 报告解析失败：{exc}") from exc

    meta = {
        "project": str(project_path),
        "mode_name": ("单文件扫描" if single_file else
                      ("快速扫描（最多 5 个文件）" if quick_used else
                       ("Git 变更审查" if mode == "review" else "全量扫描"))),
        "model": f"{model.get('label')} / {model.get('model')}",
        "created_at": created_at,
    }
    markdown = _render_markdown(data, meta)
    md_path.write_text(markdown, encoding="utf-8")
    elapsed = (dt.datetime.now() - started).total_seconds()
    return {
        "id": report_id,
        "project": str(project_path),
        "mode": mode,
        "mode_name": meta["mode_name"],
        "model": meta["model"],
        "created_at": created_at,
        "elapsed": round(elapsed, 1),
        "summary": data.get("summary") or {},
        "comments": data.get("comments") or [],
        "human_review": _human_review(data.get("comments") or []),
        "warnings": data.get("warnings") or [],
        "status": data.get("status") or "",
        "project_summary": data.get("project_summary") or "",
        "session_id": data.get("session_id") or "",
        "markdown": markdown,
        "report_url": f"/api/review/report/{report_id}",
        "json_url": f"/api/review/json/{report_id}",
    }


def read_report(report_id: str) -> str:
    if not re.fullmatch(r"[0-9A-Za-z_-]+", report_id or ""):
        raise ReviewError("报告 id 不合法")
    path = _report_dir(config_mod.load()) / f"{report_id}.md"
    if not path.is_file():
        raise ReviewError("找不到这份审查报告")
    return path.read_text(encoding="utf-8")


def report_json_path(report_id: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z_-]+", report_id or ""):
        raise ReviewError("报告 id 不合法")
    path = _report_dir(config_mod.load()) / f"{report_id}.json"
    if not path.is_file():
        raise ReviewError("找不到这份审查报告")
    return path
