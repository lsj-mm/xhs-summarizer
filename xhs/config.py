# -*- coding: utf-8 -*-
"""配置的读取、合并与保存。

设计取舍：
- 用「默认值 + config.json 覆盖 + 环境变量兜底」三层，而不是把 key 写死在代码里。
  config.json 由网页设置面板写入，文件权限跟普通文件一样，所以界面上明确提示它是明文。
- 各家 API 的 base_url / model 放在预设里，用户通常只需要填一个 key。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

LOG = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

# ---------------------------------------------------------------- 默认配置

DEFAULTS: dict = {
    "output_dir": "output",
    "server": {"host": "127.0.0.1", "port": 8787},
    "download": {
        # 小红书对未登录请求有风控，用浏览器 cookie 成功率最高；
        # 留空表示不带 cookie（部分公开笔记仍可下载）。
        "cookies_from_browser": "edge",
        "cookies_file": "",
        # 默认直连：系统代理常常只放行特定站点，会把下载一起挡掉
        "proxy": "",
        "use_system_proxy": False,
        "format": "bestaudio/best",
        "timeout_seconds": 120,
        # 处理成功后是否保留原始视频（默认删掉，避免占满磁盘）
        "keep_media": False,
    },
    "asr": {
        "provider": "siliconflow",
        "api_key": "",
        "base_url": "",
        "model": "",
        "language": "zh",
        # 单次上传给云端 ASR 的音频切片长度；太长会被接口拒绝，太短会切断句子
        "segment_seconds": 600,
        # 单段音频遇到 5xx/限流时的重试次数（长视频里失败一段代价很大）
        "retry_attempts": 4,
    },
    "summarizer": {
        "provider": "deepseek",
        "api_key": "",
        "base_url": "",
        "model": "",
        "temperature": 0.3,
        # 超过这个字数就先用「分段概括 + 汇总」的方式跑，避免超出上下文
        "max_input_chars": 20000,
    },
    "summary": {
        "include_transcript": True,
        # 文档开头的 YAML 元数据块：默认不写，文档是给人读的
        "include_front_matter": False,
        # 把作者正文当「专有名词核对表」喂给模型（不会写进文档）；
        # 关掉后模型只看视频转写内容
        "use_note_text": True,
        "extra_instruction": "",
    },
    # 学习路径工具自己的模型选择；provider 为空/inherit 时跟随 summarizer
    "learning": {
        "provider": "inherit",
        "api_key": "",
        "base_url": "",
        "model": "",
    },
    "codereview": {
        "provider": "inherit",
        "api_key": "",
        "base_url": "",
        "model": "",
    },
    "github": {
        # 给提取到的工具名匹配 GitHub 仓库。匹配不到就写「未找到」，绝不编造链接。
        "enabled": True,
        "token": "",
        "api_base": "https://api.github.com",
        "timeout_seconds": 15,
        # 搜索兜底的最低相似度：100=名字完全一致，70=前缀一致，60=名字包含
        "min_score": 60,
        # 搜索兜底的 star 门槛：同名但没有 star 的仓库多半是无关项目，宁可不给链接
        "min_stars": 50,
        # 是否接受「名字相近」的匹配。默认关闭：实测里所有可疑链接都出自名字相近
        # （商业产品的同名小仓库、第三方包装项目），关闭后那些工具老实写「未找到」，
        # 而完全一致的匹配不受影响。
        "allow_loose_match": False,
        "max_tools": 20,
    },
    # 可选的发音评测：目前接 Azure Speech Pronunciation Assessment。
    # key 也可以放环境变量 AZURE_SPEECH_KEY / AZURE_SPEECH_REGION，避免写进文件。
    "pronunciation": {
        "provider": "azure",
        "azure": {"key": "", "region": "", "language": "en-US"},
        "xunfei": {
            "app_id": "",
            "api_key": "",
            "api_secret": "",
            "ent": "en_vip",
        },
    },
    "workdir": "work",
}

# ---------------------------------------------------------------- 厂商预设

ASR_PRESETS: dict = {
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "protocol": "openai_audio",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "FunAudioLLM/SenseVoiceSmall",
        "key_env": "SILICONFLOW_API_KEY",
        "key_url": "https://cloud.siliconflow.cn/account/ak",
        "note": "中文识别效果好、价格低，注册有免费额度。",
    },
    "openai": {
        "label": "OpenAI",
        "protocol": "openai_audio",
        "base_url": "https://api.openai.com/v1",
        "model": "whisper-1",
        "key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
        "note": "whisper-1 返回带时间戳的分段，逐字稿更完整；国内需要代理。",
    },
    "dashscope": {
        "label": "阿里云百炼（qwen3-asr-flash）",
        "protocol": "dashscope_chat",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3-asr-flash",
        "key_env": "DASHSCOPE_API_KEY",
        "key_url": "https://bailian.console.aliyun.com/?tab=model#/api-key",
        "note": "走 OpenAI 兼容的对话接口传音频，按文档实现，尚未用真实 key 验证过。",
    },
    "custom_openai": {
        "label": "自定义（OpenAI 兼容 /audio/transcriptions）",
        "protocol": "openai_audio",
        "base_url": "",
        "model": "",
        "key_env": "ASR_API_KEY",
        "key_url": "",
        "note": "自建服务或其它兼容厂商时选它，必须自己填 base_url 和 model。",
    },
}

SUMMARIZER_PRESETS: dict = {
    "deepseek": {
        "label": "DeepSeek",
        "protocol": "openai_chat",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
        "key_url": "https://platform.deepseek.com/api_keys",
        "note": "官方接口，中文总结质量好、价格低。",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容 /chat/completions）",
        "protocol": "openai_chat",
        "base_url": "",
        "model": "",
        "key_env": "LLM_API_KEY",
        "key_url": "",
        "note": "硅基流动、通义、Ollama、One-API 等都填这里。",
    },
    "local": {
        "label": "本地模型（OpenAI 兼容）",
        "protocol": "openai_chat",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "",
        "key_env": "LOCAL_LLM_API_KEY",
        "key_url": "",
        "key_optional": True,
        "note": "Ollama / LM Studio / vLLM 等本地 OpenAI 兼容服务；模型名必填，key 通常可留空。",
    },
}


# ---------------------------------------------------------------- 基础操作

def deep_merge(base: dict, override: dict) -> dict:
    """把 override 合并进 base 的副本，嵌套字典逐层合并。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def config_path() -> Path:
    """配置文件位置，可用环境变量 XHS_CONFIG 指到别处（便于同时维护多套配置）。"""
    override = (os.environ.get("XHS_CONFIG") or "").strip()
    return Path(override) if override else CONFIG_PATH


def load(path: Path | None = None) -> dict:
    path = Path(path or config_path())
    raw = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # 配置坏了不能静默用默认值启动，否则用户以为保存成功了
            raise RuntimeError(f"配置文件 {path} 不是合法 JSON（第 {exc.lineno} 行）：{exc.msg}") from exc
    return deep_merge(DEFAULTS, raw)


def save(config: dict, path: Path | None = None) -> Path:
    path = Path(path or config_path())
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("配置已写入 %s", path)
    return path


def _env_key(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def effective_asr(config: dict) -> dict:
    """把 asr 配置补齐成可直接调用的形式（预设 + 环境变量兜底）。"""
    section = dict(config.get("asr") or {})
    name = section.get("provider") or "siliconflow"
    preset = ASR_PRESETS.get(name)
    if preset is None:
        raise RuntimeError(f"未知的转写厂商 {name!r}，可选：{', '.join(ASR_PRESETS)}")
    resolved = {
        "provider": name,
        "protocol": preset["protocol"],
        "base_url": (section.get("base_url") or preset["base_url"]).rstrip("/"),
        "model": section.get("model") or preset["model"],
        "api_key": section.get("api_key") or _env_key(preset["key_env"]) or _env_key("ASR_API_KEY"),
        "language": section.get("language") or "zh",
        "segment_seconds": int(section.get("segment_seconds") or 600),
        "retry_attempts": int(section.get("retry_attempts") or 4),
        "label": preset["label"],
    }
    if not resolved["base_url"] or not resolved["model"]:
        raise RuntimeError(f"厂商 {preset['label']} 需要自己填写 base_url 和 model")
    return resolved


def effective_summarizer(config: dict) -> dict:
    section = dict(config.get("summarizer") or {})
    name = section.get("provider") or "deepseek"
    preset = SUMMARIZER_PRESETS.get(name)
    if preset is None:
        raise RuntimeError(f"未知的总结模型 {name!r}，可选：{', '.join(SUMMARIZER_PRESETS)}")
    resolved = {
        "provider": name,
        "protocol": preset["protocol"],
        "base_url": (section.get("base_url") or preset["base_url"]).rstrip("/"),
        "model": section.get("model") or preset["model"],
        "api_key": section.get("api_key") or _env_key(preset["key_env"]) or _env_key("LLM_API_KEY"),
        "temperature": float(section.get("temperature", 0.3)),
        "max_input_chars": int(section.get("max_input_chars") or 20000),
        "label": preset["label"],
        "key_optional": bool(preset.get("key_optional")),
    }
    if not resolved["base_url"] or not resolved["model"]:
        raise RuntimeError(f"模型 {preset['label']} 需要自己填写 base_url 和 model")
    return resolved


def effective_learning_model(config: dict) -> dict:
    """学习路径工具使用的模型；默认跟随 summarizer，也可单独指定。"""
    section = dict(config.get("learning") or {})
    provider = (section.get("provider") or "inherit").strip()
    if provider in ("", "inherit"):
        return effective_summarizer(config)

    preset = SUMMARIZER_PRESETS.get(provider)
    if preset is None:
        raise RuntimeError(f"未知的学习模型厂商 {provider!r}")

    # 只有和全局总结模型是同一家时，才复用全局 key，避免把 key 混到别家
    global_section = dict(config.get("summarizer") or {})
    global_provider = (global_section.get("provider") or "deepseek").strip()
    key = (section.get("api_key") or "").strip()
    if not key and provider == global_provider:
        key = (global_section.get("api_key") or "").strip()
    key = key or _env_key(preset["key_env"]) or _env_key("LLM_API_KEY")

    base_url = (section.get("base_url") or preset["base_url"] or "").rstrip("/")
    model = section.get("model") or preset["model"]
    if not base_url or not model:
        raise RuntimeError(f"模型 {preset['label']} 需要自己填写 base_url 和 model")
    return {
        "provider": provider,
        "protocol": preset["protocol"],
        "base_url": base_url,
        "model": model,
        "api_key": key,
        "temperature": float(global_section.get("temperature", 0.3)),
        "max_input_chars": int(global_section.get("max_input_chars") or 20000),
        "label": preset["label"],
        "key_optional": bool(preset.get("key_optional")),
    }


def effective_review_model(config: dict) -> dict:
    """代码审查工具使用的模型；默认跟随 summarizer，也可单独指定。"""
    section = dict(config.get("codereview") or {})
    provider = (section.get("provider") or "inherit").strip()
    if provider in ("", "inherit"):
        return effective_summarizer(config)
    preset = SUMMARIZER_PRESETS.get(provider)
    if preset is None:
        raise RuntimeError(f"未知的代码审查模型厂商 {provider!r}")
    global_section = dict(config.get("summarizer") or {})
    global_provider = (global_section.get("provider") or "deepseek").strip()
    key = (section.get("api_key") or "").strip()
    if not key and provider == global_provider:
        key = (global_section.get("api_key") or "").strip()
    key = key or _env_key(preset["key_env"]) or _env_key("LLM_API_KEY")
    base_url = (section.get("base_url") or preset["base_url"] or "").rstrip("/")
    model = section.get("model") or preset["model"]
    if not base_url or not model:
        raise RuntimeError(f"模型 {preset['label']} 需要自己填写 base_url 和 model")
    return {
        "provider": provider,
        "protocol": preset["protocol"],
        "base_url": base_url,
        "model": model,
        "api_key": key,
        "temperature": float(global_section.get("temperature", 0.3)),
        "max_input_chars": int(global_section.get("max_input_chars") or 20000),
        "label": preset["label"],
        "key_optional": bool(preset.get("key_optional")),
    }


def default_model_view(config: dict) -> dict:
    """工具箱默认模型（summarizer）的设置视图。"""
    section = dict(config.get("summarizer") or {})
    provider = (section.get("provider") or "deepseek").strip()
    try:
        effective = effective_summarizer(config)
        effective_info = {
            "label": effective.get("label", ""),
            "model": effective.get("model", ""),
            "base_url": effective.get("base_url", ""),
            "api_key_set": bool(effective.get("api_key")),
        }
    except Exception as exc:
        effective_info = {"error": str(exc), "api_key_set": False}
    return {
        "provider": provider,
        "model": section.get("model") or "",
        "base_url": section.get("base_url") or "",
        "api_key_masked": mask_key(section.get("api_key") or ""),
        "api_key_set": bool(section.get("api_key")),
        "effective": effective_info,
        "options": [{"value": name, "label": preset["label"]}
                    for name, preset in SUMMARIZER_PRESETS.items()],
    }


def save_default_model(payload: dict, config: dict) -> dict:
    """保存工具箱默认模型；不会动各工具自己的覆盖设置。"""
    payload = payload or {}
    provider = str(payload.get("provider") or "deepseek").strip()
    preset = SUMMARIZER_PRESETS.get(provider)
    if preset is None:
        raise RuntimeError("未知的默认模型厂商")
    section = dict(config.get("summarizer") or {})
    old_provider = (section.get("provider") or "deepseek").strip()
    base_url = str(payload.get("base_url") or "").strip()
    model = str(payload.get("model") or "").strip()
    if provider == "custom" and (not base_url or not model):
        raise RuntimeError("自定义模型需要填写 Base URL 和模型名")
    if provider == "local" and not model:
        raise RuntimeError("本地模型需要填写模型名（例如 qwen2.5:7b）")
    section["provider"] = provider
    section["base_url"] = base_url
    section["model"] = model
    key = str(payload.get("api_key") or "").strip()
    if key:
        section["api_key"] = key
    elif provider != old_provider:
        # 换厂商时不沿用旧厂商 key
        section["api_key"] = ""
    config["summarizer"] = section
    save(config)
    return default_model_view(config)


def effective_github(config: dict) -> dict:
    """GitHub 链接匹配的配置（含 GITHUB_TOKEN 环境变量兜底）。"""
    section = dict(config.get("github") or {})
    return {
        "enabled": bool(section.get("enabled", True)),
        "token": (section.get("token") or "").strip() or _env_key("GITHUB_TOKEN"),
        "api_base": (section.get("api_base") or "https://api.github.com").rstrip("/"),
        "timeout_seconds": int(section.get("timeout_seconds") or 15),
        "min_score": int(section.get("min_score") or 60),
        "min_stars": int(section.get("min_stars") if section.get("min_stars") is not None else 50),
        "allow_loose_match": bool(section.get("allow_loose_match", False)),
        "max_tools": int(section.get("max_tools") or 20),
    }


def effective_pronunciation(config: dict) -> dict:
    """发音评测配置（Azure / 讯飞，含环境变量兜底）。"""
    section = dict(config.get("pronunciation") or {})
    azure = dict(section.get("azure") or {})
    xunfei = dict(section.get("xunfei") or {})
    return {
        "provider": section.get("provider") or "azure",
        "azure": {
            "key": (azure.get("key") or "").strip() or _env_key("AZURE_SPEECH_KEY"),
            "region": (azure.get("region") or "").strip() or _env_key("AZURE_SPEECH_REGION"),
            "language": (azure.get("language") or "en-US").strip() or "en-US",
        },
        "xunfei": {
            "app_id": (xunfei.get("app_id") or "").strip() or _env_key("XUNFEI_APP_ID"),
            "api_key": (xunfei.get("api_key") or "").strip() or _env_key("XUNFEI_API_KEY"),
            "api_secret": (xunfei.get("api_secret") or "").strip() or _env_key("XUNFEI_API_SECRET"),
            "ent": (xunfei.get("ent") or "en_vip").strip() or "en_vip",
        },
    }


def output_dir(config: dict) -> Path:
    raw = Path(config.get("output_dir") or "output").expanduser()
    path = raw if raw.is_absolute() else PROJECT_ROOT / raw
    path.mkdir(parents=True, exist_ok=True)
    return path


def workdir(config: dict) -> Path:
    raw = Path(config.get("workdir") or "work").expanduser()
    path = raw if raw.is_absolute() else PROJECT_ROOT / raw
    path.mkdir(parents=True, exist_ok=True)
    return path


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "*" * (len(key) - 2)
    return f"{key[:6]}{'*' * 6}{key[-4:]}"


def masked_view(config: dict) -> dict:
    """给网页设置面板用的视图：key 只回显掩码，不回传明文。"""
    view = copy.deepcopy(config)
    for section in ("asr", "summarizer"):
        key = (view.get(section) or {}).get("api_key") or ""
        view[section]["api_key_masked"] = mask_key(key)
        view[section]["api_key_set"] = bool(key)
        view[section]["api_key"] = ""
        preset = (ASR_PRESETS if section == "asr" else SUMMARIZER_PRESETS).get(view[section].get("provider"), {})
        view[section]["key_env"] = preset.get("key_env", "")
        view[section]["env_key_set"] = bool(_env_key(preset.get("key_env", "")))
    # GitHub token 同样是密钥，一律只回显掩码
    github = view.get("github")
    if isinstance(github, dict):
        token = github.get("token") or ""
        github["token_masked"] = mask_key(token)
        github["token_set"] = bool(token)
        github["env_token_set"] = bool(_env_key("GITHUB_TOKEN"))
        github["token"] = ""
    learning = view.get("learning")
    if isinstance(learning, dict):
        key = learning.get("api_key") or ""
        learning["api_key_masked"] = mask_key(key)
        learning["api_key_set"] = bool(key)
        learning["api_key"] = ""
    pron = view.get("pronunciation")
    if isinstance(pron, dict):
        azure = pron.get("azure")
        if isinstance(azure, dict):
            key = azure.get("key") or ""
            azure["key_masked"] = mask_key(key)
            azure["key_set"] = bool(key)
            azure["env_key_set"] = bool(_env_key("AZURE_SPEECH_KEY"))
            azure["key"] = ""
        xunfei = pron.get("xunfei")
        if isinstance(xunfei, dict):
            for field in ("api_key", "api_secret"):
                value = xunfei.get(field) or ""
                xunfei[f"{field}_masked"] = mask_key(value)
                xunfei[f"{field}_set"] = bool(value)
                xunfei[field] = ""
            xunfei["env_app_id_set"] = bool(_env_key("XUNFEI_APP_ID"))
            xunfei["env_api_key_set"] = bool(_env_key("XUNFEI_API_KEY"))
            xunfei["env_api_secret_set"] = bool(_env_key("XUNFEI_API_SECRET"))
            xunfei["env_key_set"] = bool(
                xunfei["env_app_id_set"]
                or xunfei["env_api_key_set"]
                or xunfei["env_api_secret_set"]
            )
    return view


def apply_update(config: dict, patch: dict) -> dict:
    """应用网页提交的改动。

    密钥字段（api_key / token）留空表示「不修改」，否则界面上的掩码会被当成新值写回去。
    """
    secret_fields = {"asr": "api_key", "summarizer": "api_key",
                     "learning": "api_key", "github": "token"}
    incoming = copy.deepcopy(patch or {})
    for section, field in secret_fields.items():
        if not isinstance(incoming.get(section), dict):
            continue
        # clear_xxx 为真表示「显式清空」，与「留空=不修改」区分开
        clear = bool(incoming[section].pop(f"clear_{field}", None))
        for noise in (f"{field}_masked", f"{field}_set",
                      "key_env", "env_key_set", "token_masked", "token_set", "env_token_set"):
            incoming[section].pop(noise, None)
        if clear:
            incoming[section][field] = ""
        elif not incoming[section].get(field):
            incoming[section].pop(field, None)

    # 发音评测的 Azure key 是嵌套字段，单独按「留空=不修改」处理
    pron = incoming.get("pronunciation")
    if isinstance(pron, dict):
        azure = pron.get("azure")
        if isinstance(azure, dict):
            clear = bool(azure.pop("clear_key", None))
            for noise in ("key_masked", "key_set", "env_key_set"):
                azure.pop(noise, None)
            if clear:
                azure["key"] = ""
            elif not azure.get("key"):
                azure.pop("key", None)
        xunfei = pron.get("xunfei")
        if isinstance(xunfei, dict):
            for field in ("api_key", "api_secret"):
                clear = bool(xunfei.pop(f"clear_{field}", None))
                for noise in (f"{field}_masked", f"{field}_set"):
                    xunfei.pop(noise, None)
                if clear:
                    xunfei[field] = ""
                elif not xunfei.get(field):
                    xunfei.pop(field, None)
            for noise in ("env_app_id_set", "env_api_key_set",
                          "env_api_secret_set", "env_key_set"):
                xunfei.pop(noise, None)
    return deep_merge(config, incoming)
