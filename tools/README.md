# `tools/` 自检与回归测试

这些脚本不属于网页运行时，用来在改代码、换电脑或升级依赖后确认没有坏。

| 文件 | 作用 | 运行方式 |
|---|---|---|
| `selfcheck.py` | 离线自检：造测试视频、起假转写/假大模型/假 GitHub，把「下载→抽音轨→切片→转写→提取→核实→写 md」完整跑一遍 | `python tools/selfcheck.py` |
| `check_finance.js` | 金融计算回归测试：个税、社保、摊还、提前还款、IRR、组合贷 | `node tools/check_finance.js` |
| `check_readback.js` | 跟读匹配回归测试：完全匹配、漏词、多词、用错词、词序、空结果 | `node tools/check_readback.js` |
| `check_pronounce.py` | Azure 发音评测离线测试：假响应验证请求地址、`format=detailed`、WAV 类型、分数/音素解析 | `python tools/check_pronounce.py` |
| `check_xunfei.py` | 讯飞语音评测离线测试：签名 URL、PCM 分帧、XML 分数/词/音素解析、未配置提示 | `python tools/check_xunfei.py` |
| `check_lottery.py` | 彩票离线测试：注数公式、号码范围、胆拖约束、随机种子、奖级与奖金核对 | `python tools/check_lottery.py` |
| `check_football.py` | 足球离线测试：500.com 行解析、赔率概率、比分/半全场/总进球推荐结构 | `python tools/check_football.py` |

`check_pronounce.py` 和 `check_xunfei.py` 都不联网、不消耗 Azure/讯飞额度；`selfcheck.py` 也不联网、不用 API key。

## 约定

- 新增功能尽量把核心逻辑抽成纯函数，再在这里加一个可离线跑的测试。
- 测试输出统一用「✓ / ✗ + 一句话」，最后打印通过数；失败返回非 0。
- 不要在测试里写真实 key 或调用付费接口。
