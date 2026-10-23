# -*- coding: utf-8 -*-
"""竞彩足球分析的离线测试：不联网，验证赔率换算、泊松拟合和推荐结构。

    python tools/check_football.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import football  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    check(abs(football._decimal_odds("+210") - 3.1) < 1e-9, "美式正赔率转小数赔率")
    check(abs(football._decimal_odds("-155") - (1 + 100 / 155)) < 1e-9,
          "美式负赔率转小数赔率")
    check(football._decimal_odds("not-a-number") is None, "非法赔率返回 None")
    check(football.zh_team("Brisbane Roar FC") == "布里斯班狮吼", "球队中文名映射")
    check(football.zh_team("Nigeria U17") == "尼日利亚U17", "U17 国家队中文名")
    check(football.zh_team("Unknown FC") == "", "未收录球队保留英文回退")
    check(football.zh_league("A-League Men") == "澳超", "联赛中文名映射")

    matrix = football._score_matrix(1.5, 1.2)
    total = sum(sum(row) for row in matrix)
    check(abs(total - 1.0) < 0.01, "比分概率矩阵总和为 1", f"{total:.4f}")
    home, draw, away = football._outcomes(matrix)
    check(abs(home + draw + away - 1.0) < 1e-6, "胜平负概率归一")
    over = football._over_prob(matrix, 2.5)
    check(0 < over < 1, "大小球概率在 0~1 之间", f"{over:.3f}")

    lh, la = football._fit_lambdas(0.42, 0.25, 0.33, 0.54, 2.5)
    fitted = football._score_matrix(lh, la)
    fh, fd, fa = football._outcomes(fitted)
    check(abs(fh - 0.42) < 0.04 and abs(fd - 0.25) < 0.04 and abs(fa - 0.33) < 0.04,
          "泊松模型能拟合目标胜平负概率",
          f"{fh:.3f}/{fd:.3f}/{fa:.3f}")
    check(abs(football._over_prob(fitted, 2.5) - 0.54) < 0.06,
          "泊松模型能拟合大小球概率",
          f"{football._over_prob(fitted, 2.5):.3f}")

    match = {
        "id": "test",
        "league": "测试联赛",
        "kickoff": "2026-10-23T20:00+08:00",
        "home": {"name": "主队", "form": "WWDLW", "record": "5-2-3"},
        "away": {"name": "客队", "form": "LDWLL", "record": "3-2-5"},
        "odds": {
            "home": "+110", "draw": "+240", "away": "+260",
            "total_line": 2.5, "over": "-110", "under": "-110",
            "provider": "TestBook",
        },
    }
    analysis = football._analyze(match)
    check(bool(analysis), "有赔率的比赛能生成分析")
    if analysis:
        check(sum(analysis["probabilities"].values()) > 99
              and sum(analysis["probabilities"].values()) < 101,
              "推荐概率接近 100%", str(analysis["probabilities"]))
        check(len(analysis["scores"]) >= 3 and len(analysis["htft"]) >= 3
              and len(analysis["totals"]) >= 2,
              "比分/半全场/总进球推荐都有结果")
        check("赔率" in analysis["basis"] and "未接入伤停" in analysis["data_note"],
              "分析依据和缺失数据说明都在")

    no_odds = {**match, "odds": None}
    check(football._analyze(no_odds) is None, "没有赔率时不编造推荐")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
