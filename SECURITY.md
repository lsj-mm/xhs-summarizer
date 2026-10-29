# Security Policy

## 报告安全问题

请不要在公开 Issue 里贴 API key、Cookie、完整配置或可复现的攻击细节。

优先使用 GitHub 的私密报告入口：

<https://github.com/lsj-mm/xhs-summarizer/security/advisories/new>

如果该入口不可用，可以在 Issue 里只说明「存在安全问题，希望私下联系」，不要附敏感内容。

## 安全边界

这个项目是本地工具，默认只监听 `127.0.0.1:8787`：

- 没有登录、权限和 CSRF 防护，**不要直接暴露到公网或局域网**；
- 代码审查会把代码发送给你配置的模型服务，敏感项目请先用本地模型或不要使用；
- 小红书 Cookie、API key、GitHub Token 只应保存在本机 `config.json` 或环境变量中；
- 不要把 `config.json`、`output/`、`work/`、`_vendor/`、截图里的 key 提交到仓库或发到公开群组。

## 支持范围

只对当前主分支的最新代码提供安全修复。历史版本不保证继续维护。

## 依赖与第三方

- OpenCodeReview 以 Apache-2.0 发布，二进制不随本仓库提交；
- yt-dlp、ffmpeg、FastAPI 等第三方依赖请使用可信来源，并关注上游安全公告；
- 如果发现的是上游依赖漏洞，请同时向上游项目报告。
