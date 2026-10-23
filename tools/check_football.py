# -*- coding: utf-8 -*-
"""竞彩足球分析的离线测试：不联网，验证 500.com 赔率解析和推荐结构。

    python tools/check_football.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import football  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


SAMPLE_ROW = '''
<tr class="bet-tb-tr" data-fixtureid="1450899" data-homesxname="布里斯班"
 data-awaysxname="珀斯" data-matchdate="2026-10-23" data-matchtime="17:00"
 data-rangqiu="-1" data-simpleleague="澳超" data-processdate="2026-10-23"
 data-matchnum="周五001">
<td><a class="team-l" title="布里斯班狮吼">布里斯班</a></td>
<td><a class="team-r" title="珀斯光荣">珀斯</a></td>
<p data-type="nspf" data-value="3" data-sp="2.10"></p>
<p data-type="nspf" data-value="1" data-sp="3.32"></p>
<p data-type="nspf" data-value="0" data-sp="2.84"></p>
<p data-type="jqs" data-value="2" data-sp="3.75"></p>
<p data-type="bqc" data-value="3-3" data-sp="3.18"></p>
<p data-type="bf" data-value="1:0" data-sp="7.75"></p>
</tr>
'''


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    norm = football._normalize({"3": 2.0, "1": 3.0, "0": 4.0})
    check(abs(sum(norm.values()) - 1.0) < 1e-9, "赔率隐含概率归一",
          f"{sum(norm.values()):.4f}")
    check(football._score_label("1:0") == ("1-0", "主胜"), "比分赔率标签")
    check(football._score_label("胜其它")[0] == "主胜其他", "比分其他选项标签")
    check(football._htft_label("3-1") == "胜/平", "半全场赔率标签")
    check(football._row_belongs(
        {"matchnum": "周五002", "date": "2026-10-24", "processdate": "2026-10-23"},
        dt.date(2026, 10, 23)), "凌晨比赛归入周五销售日")
    check(not football._row_belongs(
        {"matchnum": "周六001", "date": "2026-10-24", "processdate": "2026-10-24"},
        dt.date(2026, 10, 23)), "非周五场次被过滤")

    rows = football._parse_rows(SAMPLE_ROW)
    check(len(rows) == 1, "解析一条 500.com 比赛行")
    row = next(iter(rows.values()))
    check(row["home"] == "布里斯班狮吼" and row["away"] == "珀斯光荣",
          "球队使用中文全名", f"{row['home']} / {row['away']}")
    check(row["markets"]["one_x_two"]["3"] == 2.10,
          "解析竞彩胜平负赔率")

    match = {
        "matchnum": "周五001", "league": "澳超", "home": "布里斯班狮吼", "away": "珀斯光荣",
        "markets": {
            "one_x_two": {"3": 2.10, "1": 3.32, "0": 2.84},
            "score": {"1:0": 7.75, "2:1": 7.25, "1:1": 6.50, "0:1": 9.25},
            "htft": {"3-3": 3.18, "3-1": 14.0, "3-0": 30.0,
                     "1-3": 4.80, "1-1": 5.10, "0-0": 5.35},
            "total": {"0": 15.5, "1": 6.0, "2": 3.75, "3": 3.30,
                      "4": 4.80, "5": 8.50, "6": 13.5, "7": 19.0},
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
        check(analysis["scores"][0]["score"] == "1-1",
              "比分按赔率概率排序", str(analysis["scores"][0]))
        check("500.com" in analysis["basis"] and "未接入伤停" in analysis["data_note"],
              "分析依据和缺失数据说明都在")

    no_one = {**match, "markets": {"score": match["markets"]["score"]}}
    check(football._analyze(no_one) is None, "没有胜平负赔率时不编造推荐")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
