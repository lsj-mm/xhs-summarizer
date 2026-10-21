# -*- coding: utf-8 -*-
"""讯飞语音评测的离线测试：不联网，验证鉴权 URL、音频分帧和 XML 解析。

    python tools/check_xunfei.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import config as config_mod  # noqa: E402
from xhs import xunfei  # noqa: E402

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xml_result>
  <read_sentence lan="en" type="study" total_score="88.5"
      accuracy_score="85.0" fluency_score="90.0"
      integrity_score="100.0" standard_score="80.0">
    <rec_paper>
      <sentence total_score="88.5">
        <word content="Please" total_score="95.0" dp_message="0">
          <syll syll_score="95.0">
            <phone content="p" dp_message="0"></phone>
            <phone content="l" dp_message="0"></phone>
          </syll>
        </word>
        <word content="retry" total_score="70.0" dp_message="128">
          <syll syll_score="70.0">
            <phone content="r" dp_message="128"></phone>
          </syll>
        </word>
      </sentence>
    </rec_paper>
  </read_sentence>
</xml_result>"""

# 真实调用里遇到过分数不在 read_sentence、而在内层 sentence 上的情况
NESTED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xml_result>
  <read_sentence lan="en" type="study">
    <rec_paper>
      <sentence total_score="77.0" accuracy_score="75.0"
          fluency_score="80.0" integrity_score="100.0" standard_score="70.0">
        <word content="hello" total_score="80.0" dp_message="0"></word>
      </sentence>
    </rec_paper>
  </read_sentence>
</xml_result>"""

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    # 1) 鉴权 URL
    url = xunfei.signed_url("test-key", "test-secret")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    check(parsed.scheme == "wss" and parsed.hostname == "ise-api.xfyun.cn",
          "WebSocket 地址是讯飞 ISE", parsed.hostname or "")
    check(parsed.path == "/v2/open-ise", "路径是 /v2/open-ise", parsed.path)
    auth = base64.b64decode(query.get("authorization", [""])[0]).decode("utf-8", "replace")
    check('api_key="test-key"' in auth and 'algorithm="hmac-sha256"' in auth,
          "authorization 里带 api_key 和算法")
    check('headers="host date request-line"' in auth, "签名 headers 正确")
    check("signature=\"" in auth and "host" in query and "date" in query,
          "URL 带 signature/date/host 参数")

    # 2) 音频分帧：第一帧 aus=1/status=1，最后一帧 aus=4/status=2
    fake = _FakeWS()
    xunfei._send_audio(fake, b"\0" * (xunfei.CHUNK_SIZE * 2 + 10))
    check(len(fake.sent) == 3, "两帧半音频切成 3 帧", str(len(fake.sent)))
    check(fake.sent[0]["business"]["aus"] == 1 and fake.sent[0]["data"]["status"] == 1,
          "第一帧 aus=1/status=1")
    check(fake.sent[-1]["business"]["aus"] == 4 and fake.sent[-1]["data"]["status"] == 2,
          "最后一帧 aus=4/status=2")
    check(all(base64.b64decode(frame["data"]["data"]) for frame in fake.sent),
          "每帧音频都做了 base64 编码")

    # 3) XML 解析
    result = xunfei.parse_result(SAMPLE_XML)
    check(result["scores"]["pronunciation"] == 88.5, "解析总分", str(result["scores"]))
    check(result["scores"]["accuracy"] == 85.0 and result["scores"]["fluency"] == 90.0,
          "解析准确度/流利度")
    check(result["scores"]["standard"] == 80.0, "解析标准度")
    check(len(result["words"]) == 2, "解析出词级结果", str(len(result["words"])))
    check(result["words"][1]["word"] == "retry" and result["words"][1]["accuracy"] == 70.0,
          "解析低分词")
    check(result["words"][1]["error_type"] == "替换", "解析 dp_message 错误类型")
    check(result["words"][1]["phonemes"][0]["error"] == "替换", "解析音素错误")

    nested = xunfei.parse_result(NESTED_XML)
    check(nested["scores"]["pronunciation"] == 77.0
          and nested["scores"]["accuracy"] == 75.0,
          "分数在内层 sentence 时也能解析", str(nested["scores"]))

    # 4) 未配置时明确提示，不建立连接
    workdir = Path(tempfile.mkdtemp(prefix="xunfei-check-"))
    cfg = config_mod.deep_merge(
        config_mod.DEFAULTS,
        {
            "workdir": str(workdir),
            "pronunciation": {
                "provider": "xunfei",
                "xunfei": {"app_id": "", "api_key": "", "api_secret": ""},
            },
        },
    )
    audio = base64.b64encode(b"\0" * 2000).decode("ascii")
    try:
        xunfei.assess(audio, "audio/wav", "Please retry the request.", cfg)
        check(False, "未配置时拒绝调用")
    except xunfei.XunfeiError as exc:
        check("还没有配置讯飞语音评测" in str(exc), "未配置时给出明确的接入提示")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
