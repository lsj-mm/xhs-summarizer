# `xhs/` 后端模块

这里放 FastAPI 之外的全部业务逻辑。一条小红书链接的处理顺序大致是：

```text
resolve.py → download.py → audio.py → asr.py → summarize.py → github.py → store.py
```

`jobs.py` / `pipeline.py` 负责把上面的步骤串成可看进度的作业；`english.py`、`pronounce.py`、`xunfei.py`、`keywords.py` 是另外几个工具的后端。

| 文件 | 作用 |
|---|---|
| `__init__.py` | Python 包标记 |
| `config.py` | 默认配置 + `config.json` 覆盖 + 环境变量兜底；保存时对 API key 做掩码 |
| `resolve.py` | 小红书短链跟跳转、抽 `note_id`、从页面 `__INITIAL_STATE__` 取图文正文 |
| `download.py` | yt-dlp 封装：浏览器 cookie、代理开关、失败原因翻译、cookie 读不到时降级重试 |
| `audio.py` | 用自带 ffmpeg 抽音轨、转成 16kHz 单声道 mp3、按秒切片 |
| `asr.py` | 云端转写：OpenAI 兼容 `/audio/transcriptions` + 百炼协议；5xx/429 重试，部分片段失败可降级 |
| `summarize.py` | 三步总结：判断内容类型 → 提取 JSON 素材 → 核实 GitHub 链接 → 按类型成文 |
| `github.py` | 仓库核实/搜索；每条链接带「原文给出 / 模型提供 / 自动匹配 / 未找到 / 限流」来源标记 |
| `store.py` | 文件名清洗、重名加 `-2/-3`、markdown 组装、逐字稿折叠块 |
| `jobs.py` | `Job` / `JobItem` 状态、SSE 事件总线、订阅队列 |
| `pipeline.py` | 解析→下载→抽音轨→转写→总结→落盘的编排、阶段进度、单条失败隔离 |
| `sandbox.py` | 受限环境下 `tempfile` 目录权限兼容补丁 |
| `english.py` | 编程英语：提示词、词汇/口语模式、长度限制、语音转写 |
| `pronounce.py` | 发音评测分发：Azure Pronunciation Assessment / 讯飞 |
| `xunfei.py` | 讯飞语音评测：HMAC 签名、16k PCM 分帧、XML 分数/词/音素解析 |
| `keywords.py` | 多语言关键字：AI 解释、GitHub Search API 代理请求 |
| `lottery.py` | 彩票模拟：官方历史数据抓取/缓存、走势统计、注数公式、模拟选号 |

## 约定

- 配置里的 key 只从 `config.json` 或环境变量读取，不写死在代码里。
- 单条链接失败不会拖垮同批其它链接；错误信息尽量翻译成用户能看懂的中文。
- 算钱的逻辑不在这里，集中在 `static/tools/_shared/finance.js`，方便 node 回归测试。
