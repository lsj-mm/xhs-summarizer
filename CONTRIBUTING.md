# Contributing

感谢你愿意改进这个工具箱。提交前请先看下面的约定。

## 开发环境

- Windows 10/11 64 位
- Python 3.13（其它版本请用 `python bootstrap_deps.py` 重新下载对应依赖）
- 可选：Node.js，仅用于 `tools/check_finance.js`

```powershell
git clone https://github.com/lsj-mm/xhs-summarizer.git
cd xhs-summarizer
python bootstrap_deps.py
python run.py
```

不要提交 `config.json`、`output/`、`work/`、`_vendor/`、`_wheels/` 或任何 API key。
截图、日志和 Issue 内容也要先脱敏。

## 增加一个工具

1. 在 `static/tools/` 放 `<id>.html` 和 `<id>.meta.json`；
2. 在 `xhs/` 写后端逻辑（可选）；
3. 在 `app.py` 注册接口（可选）；
4. 在 `tools/` 加离线测试；
5. 更新 `README.md`、`tools/selfcheck.py` 和必要的文档。

新增工具默认不需要改工具列表代码；页面和 meta 会被自动扫描。

## 提交测试

```powershell
python tools/selfcheck.py
python tools/check_learning.py
python tools/check_trending.py
python tools/check_football.py
python tools/check_lottery.py
```

代码审查工具需要本机有 `_vendor/ocr/ocr.exe`，没有 OCR 二进制时不要强跑 `check_review.py`。

## 提交规范

- 一个提交只做一件事；
- 提交信息用中文或英文都可以，建议 `feat:`、`fix:`、`docs:` 前缀；
- 不要提交生成的笔记、视频、音频、缓存或密钥；
- 涉及模型调用的改动，尽量同时提供失败回退，不要让没有 key 的用户直接崩溃。

## 许可

提交代码即表示你同意以本仓库的 [MIT License](LICENSE) 授权你的贡献。
