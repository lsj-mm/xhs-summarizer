# -*- coding: utf-8 -*-
"""彩票模拟摇号的离线测试：不联网，验证注数公式、号码范围和可复现性。

    python tools/check_lottery.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "_vendor"))

from xhs import lottery  # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label + (f"  [{detail}]" if detail else "")))


def make_draws(game: str, n: int = 20) -> list[dict]:
    rng = random.Random(42)
    draws = []
    if game == "dlt":
        for i in range(n):
            draws.append({
                "issue": str(26000 - i), "date": "2026-01-01",
                "main": sorted(rng.sample(range(1, 36), 5)),
                "extra": sorted(rng.sample(range(1, 13), 2)),
            })
    elif game == "ssq":
        for i in range(n):
            draws.append({
                "issue": str(2026000 - i), "date": "2026-01-01",
                "main": sorted(rng.sample(range(1, 34), 6)),
                "extra": sorted(rng.sample(range(1, 17), 1)),
            })
    else:
        for i in range(n):
            draws.append({
                "issue": str(2026000 - i), "date": "2026-01-01",
                "main": sorted(rng.sample(range(1, 81), 20)),
                "extra": [],
            })
    return draws


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    check(set(lottery.public_games()) == {"dlt", "ssq", "kl8"}, "三种彩种规则可读取")
    check(lottery._comb(6, 5) == 6 and lottery._comb(7, 6) == 7, "组合数计算正确")

    cases = [
        ("dlt", "compound", {"main_count": 6, "extra_count": 2}, 6),
        ("dlt", "compound", {"main_count": 6, "extra_count": 3}, 18),
        ("dlt", "dantuo", {"main_dan": 2, "main_tuo": 4}, 4),
        ("ssq", "compound", {"main_count": 7, "extra_count": 2}, 14),
        ("ssq", "dantuo", {"main_dan": 2, "main_tuo": 5, "extra_count": 2}, 10),
        ("kl8", "single", {"pick": 5, "count": 3}, 3),
    ]
    for game, play, params, expected in cases:
        parsed = lottery._parse_params(game, play, params)
        count = lottery._bet_count(game, parsed)
        check(count == expected, f"{lottery.GAMES[game]['name']} {lottery.PLAY_NAMES[play]} 注数正确",
              f"{count}（期望 {expected}）")

    dlt_stats = lottery.compute_stats(make_draws("dlt"), "dlt", 20)
    check(dlt_stats["window"] == 20 and len(dlt_stats["main"]["frequency"]) == 35,
          "大乐透走势覆盖 35 个前区号码")
    check(dlt_stats["sum"]["avg"] > 0 and dlt_stats["last"]["main"],
          "和值与上期开奖可计算")

    parsed = lottery._parse_params("dlt", "single", {"count": 3})
    tickets, _ = lottery._build_tickets("dlt", parsed, dlt_stats, "hot", random.Random("same"))
    check(len(tickets) == 3, "大乐透单式多注生成 3 注", str(len(tickets)))
    check(all(len(t["main"]) == 5 and len(t["extra"]) == 2 for t in tickets),
          "大乐透每注是 5 个前区 + 2 个后区")
    check(all(1 <= n <= 35 for t in tickets for n in t["main"]), "前区号码在 1~35")
    check(all(1 <= n <= 12 for t in tickets for n in t["extra"]), "后区号码在 1~12")
    check(all(len(set(t["main"])) == 5 for t in tickets), "前区号码不重复")

    tickets2, _ = lottery._build_tickets("dlt", parsed, dlt_stats, "hot", random.Random("same"))
    check(tickets == tickets2, "相同随机种子生成相同结果")

    kl8_stats = lottery.compute_stats(make_draws("kl8"), "kl8", 20)
    kl8_params = lottery._parse_params("kl8", "single", {"pick": 7, "count": 2})
    kl8_tickets, _ = lottery._build_tickets("kl8", kl8_params, kl8_stats, "cold", random.Random("k"))
    check(all(len(t["main"]) == 7 for t in kl8_tickets), "快乐8选 7 生成 7 个号码")
    check(all(1 <= n <= 80 for t in kl8_tickets for n in t["main"]), "快乐8号码在 1~80")

    try:
        lottery._parse_params("dlt", "dantuo", {"main_dan": 2, "main_tuo": 2})
        check(False, "胆码+拖码不足时应报错")
    except lottery.LotteryError:
        check(True, "胆码+拖码不足时明确报错")

    # 2026 新规则：大乐透 7 个奖级
    level_cases = [
        ((5, 2), 1), ((5, 1), 2), ((5, 0), 3), ((4, 2), 3),
        ((4, 1), 4), ((4, 0), 5), ((3, 2), 5), ((3, 1), 6),
        ((2, 2), 6), ((3, 0), 7), ((2, 1), 7), ((1, 2), 7), ((0, 2), 7),
    ]
    check(all(lottery._dlt_level(a, b) == expected for (a, b), expected in level_cases),
          "大乐透 7 个奖级条件正确")

    dlt_draw = {
        "issue": "1", "date": "2026-01-01", "main": [1, 2, 3, 4, 5], "extra": [1, 2],
        "prizes": {"一等奖": 10000000, "二等奖": 300000, "三等奖": 6666,
                   "四等奖": 380, "五等奖": 200, "六等奖": 18, "七等奖": 7},
    }
    dlt_record = {
        "game": "dlt", "play": "single", "bet_count": 1, "append": False,
        "tickets": [{"main": [1, 2, 3, 4, 5], "extra": [1, 2]}],
    }
    jackpot = lottery.score_record(dlt_record, dlt_draw)
    check(jackpot["winning"] and jackpot["amount"] == 10000000,
          "大乐透一等奖金额来自当期公告", str(jackpot["amount"]))
    append_record = {**dlt_record, "append": True}
    check(lottery.score_record(append_record, dlt_draw)["amount"] == 18000000,
          "大乐透追加一等奖按 1.8 倍计算")
    third_record = {**dlt_record, "tickets": [{"main": [1, 2, 3, 4, 8], "extra": [1, 2]}]}
    check(lottery.score_record(third_record, dlt_draw)["amount"] == 6666,
          "大乐透三等奖 5+0 能核对")
    check(lottery.score_record({**third_record, "append": True}, dlt_draw)["amount"] == 6666,
          "新规则追加只参与浮动奖，三等奖不追加")

    ssq_draw = {
        "issue": "1", "date": "2026-01-01", "main": [1, 2, 3, 4, 5, 6], "extra": [1],
        "prizes": {"1": 8000000, "2": 300000, "3": 3000, "4": 200, "5": 10, "6": 5},
    }
    ssq_record = {
        "game": "ssq", "play": "single", "bet_count": 1, "append": False,
        "tickets": [{"main": [1, 2, 3, 4, 5, 6], "extra": [1]}],
    }
    check(lottery.score_record(ssq_record, ssq_draw)["amount"] == 8000000,
          "双色球一等奖核对")
    ssq_second = {**ssq_record, "tickets": [{"main": [1, 2, 3, 4, 5, 6], "extra": [2]}]}
    check(lottery.score_record(ssq_second, ssq_draw)["amount"] == 300000,
          "双色球二等奖核对")

    kl8_draw = {"issue": "1", "date": "2026-01-01", "main": list(range(1, 21)),
                "extra": [], "prizes": {"x10z10": 5000000}}
    kl8_record = {
        "game": "kl8", "play": "single", "bet_count": 1, "append": False,
        "tickets": [{"main": list(range(1, 11)), "extra": []}],
    }
    check(lottery.score_record(kl8_record, kl8_draw)["amount"] == 5000000,
          "快乐8选十中十核对")

    dlt_compound = {
        "game": "dlt", "play": "compound", "bet_count": 6, "append": False,
        "tickets": [],
        "selection": {"main": [1, 2, 3, 4, 5, 6], "extra": [1, 2]},
    }
    check(len(list(lottery._expanded_tickets(dlt_compound))) == 6,
          "大乐透前区复式 6+2 展开为 6 注")
    dlt_dantuo = {
        "game": "dlt", "play": "dantuo", "bet_count": 4, "append": False,
        "tickets": [],
        "selection": {"dan": [1, 2], "tuo": [3, 4, 5, 6], "extra": [1, 2]},
    }
    check(len(list(lottery._expanded_tickets(dlt_dantuo))) == 4,
          "大乐透胆拖展开注数正确")

    kl8_compound_params = lottery._parse_params(
        "kl8", "compound", {"pick": 5, "main_count": 7}
    )
    check(lottery._bet_count("kl8", kl8_compound_params) == 21,
          "快乐8选五复式选 7 个号 = 21 注")
    kl8_compound_tickets, kl8_selection = lottery._build_tickets(
        "kl8", kl8_compound_params, kl8_stats, "random", random.Random("kl8-c")
    )
    check(len(kl8_compound_tickets[0]["main"]) == 7,
          "快乐8复式生成 7 个号码")
    kl8_compound_record = {
        "game": "kl8", "play": "compound", "bet_count": 21, "append": False,
        "tickets": kl8_compound_tickets, "selection": kl8_selection,
        "params": {"pick": 5, "main_count": 7},
    }
    check(len(list(lottery._expanded_tickets(kl8_compound_record))) == 21,
          "快乐8复式展开为 21 注单式")

    kl8_dantuo_params = lottery._parse_params(
        "kl8", "dantuo", {"pick": 5, "main_dan": 4, "main_tuo": 2}
    )
    check(lottery._bet_count("kl8", kl8_dantuo_params) == 2,
          "快乐8选五 4 胆拖 2 = 2 注")
    kl8_dantuo_tickets, kl8_dantuo_selection = lottery._build_tickets(
        "kl8", kl8_dantuo_params, kl8_stats, "random", random.Random("kl8-dt")
    )
    check(len(kl8_dantuo_selection["dan"]) == 4 and len(kl8_dantuo_selection["tuo"]) == 2,
          "快乐8胆拖生成 4 胆 + 2 拖")
    kl8_dantuo_record = {
        "game": "kl8", "play": "dantuo", "bet_count": 2, "append": False,
        "tickets": kl8_dantuo_tickets, "selection": kl8_dantuo_selection,
        "params": {"pick": 5, "main_dan": 4, "main_tuo": 2},
    }
    check(len(list(lottery._expanded_tickets(kl8_dantuo_record))) == 2,
          "快乐8胆拖展开为 2 注单式")

    full_drag_params = lottery._parse_params(
        "kl8", "dantuo", {"pick": 5, "main_dan": 4, "main_tuo": 76}
    )
    check(lottery._bet_count("kl8", full_drag_params) == 76,
          "快乐8选五 4 胆全拖 76 个号 = 76 注")

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
