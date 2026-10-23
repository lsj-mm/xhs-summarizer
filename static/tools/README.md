# `static/tools/` 工具箱页面

工具箱采用「一个工具 = 一个 HTML + 一个可选 meta.json」的结构。`app.py` 启动/刷新时会扫描这个目录，按 `order` 排序后显示在左侧列表。`_shared/` 是共用资源，不会被当成一个工具。

| 文件 | 作用 |
|---|---|
| `notes.html` | 小红书视频转笔记：粘贴链接、批量处理、SSE 进度、查看/下载 md |
| `notes.meta.json` | 笔记工具在列表里的名称、说明、图标、排序 |
| `tax-calc.html` | 税率计算：工资个税/年终奖、房贷组合贷、提前还款、车贷真实年化、增值税 |
| `tax-calc.meta.json` | 税率计算工具的列表元数据 |
| `english.html` | 编程英语：词汇查询、口语练习、语音输入、美式发音播放、跟读匹配、Azure/讯飞发音评分 |
| `english.meta.json` | 编程英语工具的列表元数据 |
| `keywords.html` | 多语言关键字：本地速查、AI 解释、GitHub API 演示 |
| `keywords.meta.json` | 多语言关键字工具的列表元数据 |
| `lottery.html` | 彩票模拟摇号：大乐透/双色球/快乐8、玩法参数、走势统计、保存历史与开奖核对 |
| `lottery.meta.json` | 彩票模拟工具的列表元数据 |
| `_shared/base.css` | 各工具共用的基础样式、变量和卡片布局 |
| `_shared/finance.js` | 金融计算纯函数：个税、社保、摊还、提前还款、IRR，可在 node 里直接测 |
| `_shared/loan-ui.js` | 房贷/车贷/提前还款共用的结果卡片和表格渲染 |
| `_shared/presets.js` | 成都 2026 年度社保/公积金参数、比例和来源链接 |
| `_shared/markdown.js` | 模型回复用的轻量 Markdown 渲染，先转义再插标签 |
| `_shared/readback.js` | 跟读匹配：目标句和 ASR 转写的词级最长公共子序列对比 |
| `_shared/keywords-data.js` | 多语言关键字的本地词库（30 条，六种语言） |

## 约定

- 每个工具页面自己处理布局和交互，页面之间靠 iframe 隔离。
- 共用计算逻辑放 `_shared/`，不要在两个工具里各抄一份。
- 工具页里的接口用 `/api/...` 绝对路径，静态资源用 `/static/...` 绝对路径。
- `meta.json` 字段：`name`、`description`、`icon`、`order`；不写则用文件名和 🔧。
