# Changelog

## 未发布

### 新增

- 外部工具收藏（`/tool/tool-shelf`）：按「名称 + 一句话作用 + 链接 + 标签」记录第三方工具，
  支持搜索、编辑、删除和标签筛选；数据存 `work/toolshelf/items.json`，不进 Git
  - 只做记录，不抓 GitHub、不校验仓库存在与否
  - 同一链接（忽略大小写与结尾斜杠）不允许重复收藏
  - 链接必须是 `http(s)` 且带主机名，长度超限直接报错，不做静默截断
  - 接口 `GET/POST /api/toolshelf/items`、`POST/DELETE /api/toolshelf/items/{id}`
  - 离线测试 `python tools/check_toolshelf.py`（34 项），已接入 CI

### 修复

- 换电脑或重新拉代码后，代码审查因缺少 `ocr.exe` 直接报错：`bootstrap_deps.py` 现在会按当前平台
  自动下载 OpenCodeReview 二进制（默认 v1.13.3），用官方 `sha256sum.txt` 校验后放进 `_vendor/ocr/`，
  版本记录在 `_vendor/ocr/installed.json`；二进制仍不进 Git
- 不想装或想单独升级：`python bootstrap_deps.py --skip-ocr` / `--only ocr --upgrade`
- macOS、Linux 上无后缀的 `ocr` 也能被找到，找不到时的提示直接给出自动安装命令

## v0.1.0 — 2026-10-29

首个公开版本。

### 工具

- 小红书视频转笔记：链接解析、下载、转写、按内容类型整理，工具清单附 GitHub 链接
- 税率计算：工资个税、房贷、提前还款、车贷、增值税
- 编程英语：词汇查询、例句、口语练习
- 多语言关键字：Python / C++ / Java / 前端 / MySQL / Go
- 彩票模拟摇号：大乐透、双色球、快乐8，竞彩足球参考
- 学习路径与考核：AI 规划、阶段考核、3 次机会 / 80 分通过
- 代码审查：OpenCodeReview 全量扫描 / Git 变更审查、中文报告、人工核查提示
- GitHub 趋势：Trending 前 10、中文摘要、语言/Star 信息与仓库链接

### 模型

- 支持 DeepSeek、自定义 OpenAI 兼容接口和本地模型
- 首页默认模型，工具可单独覆盖

### 工程

- 离线自检与各工具测试
- MIT License、SECURITY、CONTRIBUTING 文档
- GitHub Actions CI
