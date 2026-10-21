# -*- coding: utf-8 -*-
"""Azure 发音评测的离线测试：不联网，用假响应验证请求形状与解析。

    python tools/check_pronounce.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import config as config_mod  # noqa: E402
from xhs import pronounce  # noqa: E402

FAKE_RESPONSE = {
    "RecognitionStatus": "Success",
    "DisplayText": "Please retry the request.",
    "NBest": [
        {
            "DisplayText": "Please retry the request.",
            "PronunciationAssessment": {
                "PronScore": 88.0,
                "AccuracyScore": 85.0,
                "FluencyScore": 90.0,
                "CompletenessScore": 100.0,
            },
            "Words": [
                {
                    "Word": "Please",
                    "PronunciationAssessment": {"AccuracyScore": 95.0},
                    "Phonemes": [
                        {"Phoneme": "p", "PronunciationAssessment": {"AccuracyScore": 90.0}}
                    ],
                },
                {
                    "Word": "retry",
                    "PronunciationAssessment": {"AccuracyScore": 70.0,
                                                "ErrorType": "Mispronunciation"},
                    "Phonemes": [],
                },
            ],
        }
    ],
}

captured: dict = {}
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return FAKE_RESPONSE


def _fake_post(url, **kwargs):
    captured["url"] = url
    captured.update(kwargs)
    return _FakeResponse()


def _fake_run(args, timeout=0):
    # assess() 期望 ffmpeg 生成 wav；这里直接写一个假的 RIFF 文件
    Path(args[-1]).write_bytes(b"RIFF" + b"\0" * 2048)

    class _Proc:
        returncode = 0
        stderr = ""

    return _Proc()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    pronounce.requests.post = _fake_post
    pronounce.audio_mod._run = _fake_run

    workdir = Path(tempfile.mkdtemp(prefix="pronounce-check-"))
    config = config_mod.deep_merge(
        config_mod.DEFAULTS,
        {
            "workdir": str(workdir),
            "pronunciation": {"azure": {"key": "fake-key", "region": "eastasia"}},
        },
    )
    audio = base64.b64encode(b"\0" * 2000).decode("ascii")

    try:
        result = pronounce.assess(audio, "audio/wav", "Please retry the request.", config)
    except Exception as exc:  # pragma: no cover - 测试失败时打印
        check(False, "发音评测调用成功", str(exc))
        return _report()

    check(captured.get("url", "").startswith(
        "https://eastasia.stt.speech.microsoft.com/speech/recognition/conversation/"),
        "请求发到 Azure 对应区域的 REST 地址", captured.get("url", ""))
    check(captured.get("params", {}).get("format") == "detailed",
        "带上 format=detailed（否则拿不到 NBest）")
    check(captured.get("params", {}).get("language") == "en-US",
        "语言参数是 en-US")
    check(captured.get("headers", {}).get("Content-Type", "").startswith("audio/wav"),
        "音频以 16k PCM WAV 上传", captured.get("headers", {}).get("Content-Type", ""))
    header = captured.get("headers", {}).get("Pronunciation-Assessment", "")
    try:
        assessment = json.loads(base64.b64decode(header).decode("utf-8"))
    except Exception:
        assessment = {}
    check(assessment.get("ReferenceText") == "Please retry the request.",
        "Pronunciation-Assessment 头里带参考文本")
    check(assessment.get("Granularity") == "Phoneme" and assessment.get("EnableMiscue") is True,
        "按音素粒度并开启漏读/多读检测")

    check(result["scores"]["pronunciation"] == 88.0, "解析出发音总分", str(result["scores"]))
    check(result["scores"]["accuracy"] == 85.0 and result["scores"]["fluency"] == 90.0,
        "解析出准确度/流利度")
    check(len(result["words"]) == 2, "解析出词级结果", str(len(result["words"])))
    check(result["words"][1]["accuracy"] == 70.0
          and result["words"][1]["error_type"] == "Mispronunciation",
          "解析出低分词与错误类型")
    check(result["words"][0]["phonemes"][0]["accuracy"] == 90.0, "解析出音素分数")

    # 未配置 key 时应给明确提示，而不是发请求
    captured.clear()
    bad = config_mod.deep_merge(
        config_mod.DEFAULTS,
        {"workdir": str(workdir), "pronunciation": {"azure": {"key": "", "region": ""}}},
    )
    try:
        pronounce.assess(audio, "audio/wav", "Please retry the request.", bad)
        check(False, "未配置 key 时拒绝调用")
    except pronounce.PronounceError as exc:
        check("还没有配置 Azure 发音评测" in str(exc), "未配置 key 时给出明确的接入提示")
        check(not captured, "未配置时没有发出网络请求")

    return _report()


def _report() -> int:
    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
