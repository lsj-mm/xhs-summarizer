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

    failed = 0
    for ok, label in results:
        print(("  ✓ " if ok else "  ✗ ") + label)
        failed += 0 if ok else 1
    print(f"\n  通过 {len(results) - failed}/{len(results)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
