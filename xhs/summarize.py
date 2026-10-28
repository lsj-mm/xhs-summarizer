# -*- coding: utf-8 -*-
"""总结层：先判断这条视频是什么内容，再按对应的结构成文。

三步走（而不是让模型一口气写完）：
    1. 分析：判断内容类型（工具推荐 / 问答 / 教程讲解 / 观点分享 / 资讯 / 其它），
       并按该类型抽出结构化素材（工具清单、问答对、分节要点）。
    2. 核实链接：把抽到的工具拿去 GitHub 验证/搜索（见 xhs/github.py）。
    3. 成文：按内容类型选文档结构，用「已核实链接清单」约束地址，写出最终 markdown。

为什么分三步：
    - 让模型直接写文档，它会顺手编 GitHub 地址（甚至编出不存在的仓库）；
    - 一条视频到底该怎么组织，取决于它是什么内容：讲工具就该给清单和仓库，
      讲问答就该一条条对应问题与回答，讲知识就该按知识点复述。
      先分类再成文，比用一套固定模板套所有视频靠谱得多。

长视频：先把转写文本分段提炼成要点，再走上面三步，避免超出上下文。
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests

LOG = logging.getLogger(__name__)

TIMEOUT = 600

# 内容类型 → 中文标签，用于 front matter 和界面显示
KIND_LABELS = {
    "tools": "工具推荐",
    "qa": "问答",
    "tutorial": "教程讲解",
    "opinion": "观点分享",
    "news": "资讯",
    "other": "其它",
}

ANALYZE_SYSTEM = """你是技术内容分析助手。任务分两件事：先判断这条视频属于哪类内容，再按该类内容抽出结构化素材。

只输出一个 JSON 对象，不要输出解释文字，不要用 markdown 代码块包裹。格式：
{
  "content_kind": "tools | qa | tutorial | opinion | news | other",
  "kind_reason": "一句话说明为什么归到这一类",
  "summary": "一句话总结视频讲了什么，不超过 60 字",
  "tools": [
    {
      "name": "工具名（保留原始拉丁字母与大小写，不要翻译、不要加书名号）",
      "purpose": "它是做什么的、能解决什么问题（1-3 句，写具体）",
      "github": "如果你确定它的 GitHub 仓库，写 owner/repo；不确定就留空字符串",
      "evidence": "视频里关于它的原话片段或出现的链接（供核对用）"
    }
  ],
  "qa": [{"question": "视频里提出的问题", "answer": "视频里给出的回答（要点式，不要漏掉结论）"}],
  "sections": [
    {"heading": "小节标题（用视频里讲的主题命名）", "points": ["这一节的要点，写清具体做法、数字、名称"]}
  ],
  "other_points": ["不好归入上面任何一项、但确实有信息量的要点"]
}

content_kind 怎么判：
- tools：主体是在介绍/推荐工具、项目、软件、开源库。
- qa：主体是一问一答（回答观众提问、答疑、常见问题）。
- tutorial：主体是教你怎么做某事（步骤、方法、教程、学习内容、知识讲解）。
- opinion：主体是表达看法、经验、行业判断，没有明确的操作步骤。
- news：主体是播报事件、发布、更新动态。
- other：以上都不像时用它。

各字段什么时候填：
- tools：只要视频里提到了工具/项目就填（哪怕不是工具类视频）；没提到就填空数组。
- qa：仅当 content_kind 是 qa 时填，一条问题一个对象，问题和回答要能对上。
- sections：qa 之外的类型用它承载内容，按视频讲述顺序分 2-5 节。
- analysis 里提到的每一个项目/工具都要列进 tools，不要只挑你认识的、也不要漏掉没听清名字的；
  没听清名字就用视频里的说法当 name（例如「PS5 移植项目」）。

绝对不要写进任何字段的信息：
- star 数、「本周新增 9.9k」、「本周涨了 18 万」、热榜排名、Trending 名次、下载量
- 「点点关注」「点赞收藏」「评论区聊聊」「一键三连」这类引导语
- 话题标签（形如 #xxx[话题]#）、口播寒暄（「大家好」「我们下期见」）、主播昵称与粉丝数
- 你自己的评价与展望（如「这个项目很有前景」），除非视频里明确说过"""

WRITE_SYSTEM = """你是中文技术编辑，负责把一条视频的内容整理成 markdown 笔记。
读者要的是「这条视频到底讲了什么」，请以语音转写内容为准来写。

先看下面给的「内容类型」，按对应结构输出（不要输出 YAML front matter，不要重复一级标题）：

【内容类型 = tools（工具推荐）】
## 一句话总结
## 推荐清单
（用有序列表，按视频顺序，每个工具一条；格式如下）
1. **工具名**：一句话说明它是做什么的、能解决什么问题（1-2 句）。
   GitHub：[owner/repo](url)（没有可靠地址就写「未找到公开仓库」）
2. **工具名**：…
   GitHub：…
## 工具详解
（仅当视频还讲了推荐清单之外的使用方式、限制、参数或坑时写；没有就省略整节，
 不要重复推荐清单里的作用与 GitHub 链接）
### 工具名
- **补充信息**：…
## 内容要点
（视频里除工具之外的关键信息，3-6 条；确实没有就写「无」）

【内容类型 = qa（问答）】
## 一句话总结
## 问答
### 问：问题原文（可精简措辞，但不要改变问题的意思）
**答**：视频给出的回答，写清结论和关键做法
（每条问答一个这样的段落，按视频顺序排列）
## 内容要点
（3-6 条补充信息；确实没有就写「无」）

【内容类型 = tutorial（教程讲解）】
## 一句话总结
## 内容讲解
### 小节标题
（按视频讲解顺序写 2-5 节，每节 1-3 段，写清具体做法、参数、名称、顺序）
## 要点速查
（可以直接照着做的步骤或清单，用 "- " 或 "- [ ] "；没有就写「无」）
## 内容要点
（3-6 条补充信息；确实没有就写「无」）

【内容类型 = opinion（观点分享）】
## 一句话总结
## 核心观点
（3-6 条，每条一句话讲清一个观点）
## 论据与例子
（视频里用来支撑观点的事实、案例、数据；没有就写「无」）
## 内容要点

【内容类型 = news（资讯）】
## 一句话总结
## 事件要点
（发生了什么、谁、什么时候、影响范围）
## 背景与影响
## 内容要点

【内容类型 = other】
## 一句话总结
## 内容要点
（按视频内容自己组织成 2-4 个小节，用 "### " 起标题）

通用规则：
- 非 tools 类型的视频如果提到了工具 / 项目，在「内容要点」之前补一节「提到的工具」，
  用有序列表给出：**工具名**：作用（1-2 句），下一行 `GitHub：[owner/repo](url)`；
  不要用表格，地址同样只能用已核实清单里的。能找到 GitHub 就必须附上，不能只写工具名。
- tools 类型的工具已经在「推荐清单」里列过，不要再重复写「提到的工具」。
- 工具推荐类的主清单用「名称 + 作用 + GitHub」的有序列表，让读者一眼能点开仓库。
- 以语音转写为准。如果材料里还给了「作者正文（仅供核对）」，只用它纠正专有名词的写法，
  不要照抄它的结构，也不要引用它的互动话术。
- 保留视频里的具体信息：数字、金额、时间、步骤、方法名、产品名、机构名。
- 转写文本是语音识别结果，可能有同音字错误、缺标点；按上下文合理修正，但不要改变原意。
- 不要写「视频中提到」「作者认为」这类空话，直接把内容讲清楚。
- 全部用中文（专有名词可保留原文）。

严禁写入的内容：
- star 数、「本周新增 9.9k」「本周涨了 18 万」、热榜排名、Trending 名次、下载量
- 「点点关注」「点赞收藏」「评论区聊聊」「一键三连」这类引导语
- 话题标签（形如 #xxx[话题]#）、口播寒暄（「大家好」「我们下期见」）、主播昵称与粉丝数
- 你自己的评价和展望，除非视频里明确说过

链接规则（最重要）：
- 清单里的「GitHub」和「项目地址」只能原样使用下面「已核实链接清单」里给出的地址，一个字都不要改，
  也不要用你记忆里的地址替换。
- 能找到 GitHub 的工具，必须在「推荐清单」/「提到的工具」里直接附上链接，不要只写工具名。
- 清单里标为「未找到」的，就写「未找到公开仓库」，绝不自己编一个。
- 清单里标为「查询失败」的，写「本次未查询成功（GitHub 接口限流，配置 Token 后可重试）」，
  不要写成「未找到公开仓库」——「查不了」和「不存在」是两回事。
- 不要写成 Gitee、GitLab 或其它域名。"""

MAP_PROMPT = """下面是一条视频转写文本的第 {index}/{total} 段。
请提炼这一段，输出要点列表（"- " 开头），保留其中的工具名、项目名、数字和具体做法。
不要点评、不要总结整条视频，只处理这一段。这一段没有实质内容就输出"（无实质内容）"。"""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.I)


class SummaryError(RuntimeError):
    pass


def _friendly_error(status: int, body: str, cfg: dict) -> str:
    snippet = (body or "").strip()[:300]
    if status == 401:
        return f"总结模型拒绝了这个 key（401）。当前模型：{cfg.get('label')}，请在设置里重新填写。"
    if status == 402:
        return f"总结模型余额不足（402）。原始信息：{snippet}"
    if status == 404:
        return f"模型 {cfg.get('model')!r} 不存在或无权访问（404）。原始信息：{snippet}"
    if status == 429:
        return f"总结模型触发限流（429），稍后再试。原始信息：{snippet}"
    return f"总结模型报错（HTTP {status}）：{snippet or '无返回内容'}"


def _chat(messages: list[dict], cfg: dict, json_mode: bool = False) -> str:
    url = f"{cfg['base_url']}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    payload: dict = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.3),
        "stream": False,
    }
    if json_mode:
        # 让模型走 JSON 模式能明显减少解析失败；个别厂商不支持就退回普通模式
        payload["response_format"] = {"type": "json_object"}

    last_body = ""
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_body = str(exc)
            LOG.warning("调用总结模型失败（第 %d 次）：%s", attempt + 1, exc)
            time.sleep(2 * (attempt + 1))
            continue

        if resp.status_code == 400 and json_mode:
            LOG.info("该厂商不支持 JSON 模式，改用普通模式重试")
            payload.pop("response_format", None)
            json_mode = False
            resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)

        if resp.status_code == 200:
            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                raise SummaryError(f"总结模型返回结构不符合预期：{resp.text[:300]}") from exc
            if not (content or "").strip():
                raise SummaryError("总结模型返回了空内容，请稍后重试或换一个模型")
            return content.strip()

        last_body = resp.text
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        raise SummaryError(_friendly_error(resp.status_code, last_body, cfg))

    raise SummaryError(_friendly_error(429, last_body, cfg))


def _chunk(text: str, size: int, overlap: int = 200) -> list[str]:
    """按段落边界切块，尽量不在句子中间断开。"""
    if size <= 0 or len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[end : end + overlap]
            cut = None
            for sep in ("\n\n", "。", "\n", "，"):
                pos = window.find(sep)
                if pos != -1:
                    cut = end + pos + len(sep)
                    break
            if cut:
                end = cut
        chunks.append(text[start:end])
        start = end
    return chunks


def _material(meta: dict, transcript_text: str, use_note_text: bool = True) -> str:
    lines = [
        f"标题：{meta.get('title') or '（无标题）'}",
        f"内容类型：{meta.get('content_type') or '视频'}",
    ]
    if meta.get("duration"):
        lines.append(f"时长：约 {int(meta['duration'] // 60)} 分 {int(meta['duration'] % 60)} 秒")

    parts = ["\n".join(lines)]

    # 作者正文只当「专有名词核对表」用：视频里听不清的项目名、软件名以它为准。
    # 但不要让它主导内容结构——重点是视频讲了什么。
    note_text = (meta.get("note_text") or "").strip()
    if use_note_text and note_text and meta.get("content_type") != "图文":
        parts.append(
            "--- 以下是作者正文，仅供核对专有名词写法，不要照抄结构、不要引用其中话术 ---\n" + note_text
        )

    kind = "笔记正文" if meta.get("content_type") == "图文" else "语音转写文本"
    parts.append(f"--- 以下是{kind}（主要内容，请以它为准）---\n{transcript_text}")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ 三个步骤

def _digest(text: str, cfg: dict, emit=None) -> str:
    """长文本：分段提炼成要点，代替原文喂给后面步骤。"""
    limit = cfg.get("max_input_chars") or 20000
    chunks = _chunk(text, limit, overlap=200)
    if emit:
        emit(f"材料较长（{len(text)} 字），先分 {len(chunks)} 段提炼…")
    digests = []
    for index, chunk in enumerate(chunks, start=1):
        if emit:
            emit(f"提炼第 {index}/{len(chunks)} 段…")
        digests.append(
            _chat(
                [
                    {"role": "system", "content": "你是一位严谨的内容提炼助手。"},
                    {"role": "user",
                     "content": MAP_PROMPT.format(index=index, total=len(chunks)) + "\n\n" + chunk},
                ],
                cfg,
            )
        )
    return "\n\n".join(f"【第 {i} 段要点】\n{d}" for i, d in enumerate(digests, start=1))


def _parse_json(reply: str) -> dict:
    """解析模型返回的 JSON：容忍代码块包裹、前后有解释文字。"""
    text = _FENCE_RE.sub("", (reply or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise SummaryError(f"分析结果不是合法 JSON：{exc}；原始内容前 200 字：{text[:200]}") from exc
    raise SummaryError(f"分析结果里找不到 JSON：{text[:200]}")


def _analyze(material: str, cfg: dict, emit=None) -> dict:
    """第一步：判断内容类型 + 抽结构化素材。"""
    if emit:
        emit("正在判断内容类型并提取素材…")
    messages = [
        {"role": "system", "content": ANALYZE_SYSTEM},
        {"role": "user", "content": "下面是一条视频笔记的材料，请按上述要求输出 JSON：\n\n" + material},
    ]
    try:
        result = _parse_json(_chat(messages, cfg, json_mode=True))
    except SummaryError as exc:
        # 分析失败不算致命：退回「让模型直接写文档」，只是没有已核实的链接
        LOG.warning("内容分析失败，退回直接成文：%s", exc)
        if emit:
            emit(f"内容分析未成功（{exc}），改为直接生成文档（链接可能不全）")
        return {"content_kind": "other", "summary": "", "tools": [], "qa": [],
                "sections": [], "other_points": [], "analyze_failed": True}

    kind = str(result.get("content_kind") or "other").strip().lower()
    if kind not in KIND_LABELS:
        LOG.info("模型给了未知的内容类型 %r，按 other 处理", kind)
        kind = "other"
    result["content_kind"] = kind

    result["tools"] = [
        t for t in (result.get("tools") or [])
        if isinstance(t, dict) and (t.get("name") or "").strip()
    ]
    result["qa"] = [
        q for q in (result.get("qa") or [])
        if isinstance(q, dict) and (q.get("question") or "").strip()
    ]
    result["sections"] = [
        s for s in (result.get("sections") or [])
        if isinstance(s, dict) and (s.get("heading") or "").strip()
    ]
    if not isinstance(result.get("other_points"), list):
        result["other_points"] = []

    if emit:
        detail = f"，提到 {len(result['tools'])} 个工具" if result["tools"] else ""
        if result["qa"]:
            detail += f"，{len(result['qa'])} 组问答"
        emit(f"判断为「{KIND_LABELS[kind]}」{detail}")
    return result


def _links_block(links: list[dict]) -> str:
    """给成文步骤用的「已核实链接清单」。"""
    if not links:
        return "（本次没有需要核对的工具）"
    lines = []
    for item in links:
        if item.get("url"):
            lines.append(
                f"- {item['name']}：{item['url']}（仓库 {item['full_name']}，来源：{item['source']}）"
            )
        else:
            # source 本身已经写明是「未找到」还是「查询失败」
            lines.append(f"- {item['name']}：{item['source']}")
    return "\n".join(lines)


def _write(material: str, analyzed: dict, links: list[dict], cfg: dict,
           extra: str, emit=None) -> str:
    """第二步：按内容类型成文。"""
    kind = analyzed.get("content_kind", "other")
    if emit:
        emit(f"正在生成文档（按「{KIND_LABELS.get(kind, '其它')}」的结构）…")

    user_parts = [f"--- 内容类型：{kind}（{KIND_LABELS.get(kind, '其它')}）---", material]

    if analyzed.get("summary"):
        user_parts.append(f"--- 已提取的一句话总结（可参考，可改写）---\n{analyzed['summary']}")
    if analyzed.get("sections"):
        user_parts.append(
            "--- 已提取的分节内容（可参考，可补全）---\n"
            + json.dumps(analyzed["sections"], ensure_ascii=False, indent=1)
        )
    if analyzed.get("qa"):
        user_parts.append(
            "--- 已提取的问答对（逐条对应，不要合并或漏掉）---\n"
            + json.dumps(analyzed["qa"], ensure_ascii=False, indent=1)
        )
    if analyzed.get("tools"):
        user_parts.append(
            "--- 已提取的工具（作用可直接采用或润色）---\n"
            + json.dumps(analyzed["tools"], ensure_ascii=False, indent=1)
        )
    if analyzed.get("other_points"):
        user_parts.append(
            "--- 已提取的其它要点 ---\n" + "\n".join(f"- {p}" for p in analyzed["other_points"])
        )
    user_parts.append("--- 已核实链接清单（只能用这里的地址）---\n" + _links_block(links))
    if analyzed.get("analyze_failed"):
        user_parts.append(
            "--- 注意 ---\n上面没能结构化提取素材，请你直接阅读材料自行判断内容类型并整理；"
            "由于没有经过核实的链接，项目地址一律写「未找到公开仓库」，不要凭记忆填写。"
        )
    if extra:
        user_parts.append(f"--- 额外要求 ---\n{extra}")

    return _chat(
        [
            {"role": "system", "content": WRITE_SYSTEM},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        cfg,
    )


def summarize(meta: dict, transcript: dict, config: dict, emit=None) -> dict:
    """返回 {"body", "tools", "content_kind", "kind_label", "summary"}。"""
    from . import config as config_mod
    from . import github as github_mod

    cfg = config_mod.effective_summarizer(config)
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        raise SummaryError(
            f"没有配置总结模型 API key（当前模型：{cfg['label']}）。"
            "请在网页「设置」里填写，或设置环境变量 DEEPSEEK_API_KEY。"
        )

    text = (transcript.get("text") or "").strip()
    if not text:
        raise SummaryError("转写文本是空的，没有可供总结的内容")

    summary_cfg = config.get("summary") or {}
    extra = (summary_cfg.get("extra_instruction") or "").strip()
    use_note_text = bool(summary_cfg.get("use_note_text", True))
    limit = cfg.get("max_input_chars") or 20000

    # 材料太长时先压成要点，后面两步共用
    source_text = text
    if len(_material(meta, text, use_note_text)) > limit:
        source_text = _digest(text, cfg, emit)
    material = _material(meta, source_text, use_note_text)

    analyzed = _analyze(material, cfg, emit)

    github_cfg = config_mod.effective_github(config)
    links = github_mod.GitHubLookup(
        api_base=github_cfg["api_base"],
        token=github_cfg["token"],
        timeout=github_cfg["timeout_seconds"],
        min_score=github_cfg["min_score"],
        min_stars=github_cfg["min_stars"],
        enabled=github_cfg["enabled"],
        max_tools=github_cfg["max_tools"],
        allow_loose_match=github_cfg["allow_loose_match"],
    ).resolve_all(analyzed.get("tools") or [], emit)

    body = _write(material, analyzed, links, cfg, extra, emit)
    kind = analyzed.get("content_kind", "other")
    return {
        "body": body,
        "tools": links,
        "content_kind": kind,
        "kind_label": KIND_LABELS.get(kind, "其它"),
        "summary": analyzed.get("summary", ""),
    }


def selftest(cfg: dict, emit=None) -> dict:
    """发一条极短的请求，验证 key、模型名、网络。"""
    if not cfg.get("api_key") and not cfg.get("key_optional"):
        # 没有 key 就直接说清楚，不必白跑一次请求再被 401 顶回来
        raise SummaryError(
            f"还没有配置总结模型 API key（当前模型：{cfg.get('label')}）。"
            "请在网页「设置」里填写，或设置环境变量 DEEPSEEK_API_KEY。"
        )
    if emit:
        emit(f"用最小请求测试总结模型（{cfg['label']} / {cfg['model']}）…")
    reply = _chat(
        [
            {"role": "system", "content": "你只回复被要求的词，不要多说。"},
            {"role": "user", "content": "只回复两个字：可用"},
        ],
        cfg,
    )
    return {"ok": True, "provider": cfg["label"], "model": cfg["model"], "reply": reply[:40]}
