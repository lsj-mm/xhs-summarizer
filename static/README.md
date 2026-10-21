# `static/` 前端资源

工具箱的外壳和所有工具页面都在这里。页面通过 `/static/...` 提供，不需要单独的前端构建。

| 文件/目录 | 作用 |
|---|---|
| `index.html` | 工具箱外壳：左侧工具列表、右侧 iframe 面板、地址栏 `#/<工具id>` 记忆 |
| `tools/` | 每个工具一个 HTML + 可选的 `<id>.meta.json`；详见 [tools/README.md](tools/README.md) |
| `tools/_shared/` | 多个工具共用的 CSS/JS，不会被当成一个工具 |

## 新增工具

往 `tools/` 放两个文件即可，不用改 Python：

```text
tools/<工具id>.html
tools/<工具id>.meta.json
```

`<工具id>` 只允许字母、数字、下划线、短横。刷新页面，左侧列表会自动出现。
