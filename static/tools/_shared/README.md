# `static/tools/_shared/` 共用资源

这个目录里的文件不属于任何一个工具，而是被多个工具页面共同引用。`app.py` 扫描工具时只认 `static/tools/*.html`，不会把 `_shared` 当成工具。

| 文件 | 作用 |
|---|---|
| `base.css` | 基础颜色变量、卡片、表单、表格、按钮等通用样式 |
| `finance.js` | 金融计算纯函数：累计预扣个税、社保公积金、等额本息/本金、提前还款、IRR、组合贷 |
| `loan-ui.js` | 房贷/车贷/提前还款页面共用的结果卡片和表格渲染 |
| `presets.js` | 政策参数：成都 2026 年度社保/公积金基数上下限、比例、来源链接 |
| `markdown.js` | 模型回复的轻量 Markdown 渲染（标题、列表、代码块、粗体、链接） |
| `readback.js` | 跟读匹配：目标句与 ASR 转写的词级对比，返回匹配度和漏词/多词 |
| `keywords-data.js` | 多语言关键字本地词库：Python/C++/Java/前端/MySQL/Go 各 5 条 |

## 约定

- 这里只放「纯逻辑/纯样式」，不要绑死某一个工具的 DOM id。
- 计算类代码要能直接 `require()`，便于 `tools/check_finance.js`、`tools/check_readback.js` 回归测试。
- 修改共用文件后，记得刷新所有引用它的工具页面，并跑对应测试。
