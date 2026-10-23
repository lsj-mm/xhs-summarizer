# -*- coding: utf-8 -*-
"""彩票模拟摇号：真实历史数据、走势统计、模拟选号。

重要边界：
    - 这里只做「模拟摇号」和统计展示，不预测开奖，不构成购彩建议；
    - 历史数据来自官方公开接口，缓存到 work/lottery/，并记录抓取时间和期号；
    - 复式/胆拖的注数按组合公式计算；为控制规模，复式上限比官方允许值更保守，
      页面会明确标注「本工具上限」。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import random
import secrets
from itertools import combinations
from pathlib import Path

import requests

from . import config as config_mod

LOG = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 6 * 3600
CACHE_VERSION = 2
FETCH_SIZE = 100

GAMES = {
    "dlt": {
        "name": "大乐透",
        "short": "DLT",
        "main_label": "前区",
        "main_max": 35,
        "main_pick": 5,
        "extra_label": "后区",
        "extra_max": 12,
        "extra_pick": 2,
        "price": 2,
        "append_price": 3,
        "plays": ["single", "multiple", "compound", "dantuo", "package"],
        "compound": {"main_min": 6, "main_max": 12, "extra_min": 2, "extra_max": 6},
        "api": (
            "https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"
            "?gameNo=85&provinceId=0&pageSize=100&isVerify=1&pageNo=1"
        ),
        "source_name": "中国体育彩票 · 超级大乐透历史开奖",
    },
    "ssq": {
        "name": "双色球",
        "short": "SSQ",
        "main_label": "红球",
        "main_max": 33,
        "main_pick": 6,
        "extra_label": "蓝球",
        "extra_max": 16,
        "extra_pick": 1,
        "price": 2,
        "plays": ["single", "multiple", "compound", "dantuo", "package"],
        "compound": {"main_min": 7, "main_max": 12, "extra_min": 1, "extra_max": 6},
        "api": (
            "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
            "?name=ssq&issueCount=&issueStart=&issueEnd=&dayStart=&dayEnd="
            "&pageNo=1&pageSize=100&week=&systemType=PC"
        ),
        "source_name": "中国福利彩票 · 双色球历史开奖",
    },
    "kl8": {
        "name": "快乐8",
        "short": "KL8",
        "main_label": "号码",
        "main_max": 80,
        "main_pick": 10,
        "extra_label": "",
        "extra_max": 0,
        "extra_pick": 0,
        "price": 2,
        "plays": ["single", "multiple", "compound", "dantuo", "package"],
        "pick_min": 1,
        "pick_max": 10,
        "compound_max": 15,
        "api": (
            "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
            "?name=kl8&issueCount=&issueStart=&issueEnd=&dayStart=&dayEnd="
            "&pageNo=1&pageSize=100&week=&systemType=PC"
        ),
        "source_name": "中国福利彩票 · 快乐8历史开奖",
    },
}

PLAY_NAMES = {
    "single": "单式",
    "multiple": "单式多注",
    "compound": "复式",
    "dantuo": "胆拖",
    "package": "套餐票（模拟）",
}

STRATEGIES = {
    "random": "随机均匀",
    "hot": "热号加权",
    "cold": "冷号加权",
    "trend": "近期趋势",
    "balanced": "区间均衡",
}

# 给前端的规则，不包含内部缓存细节
def public_games() -> dict:
    out = {}
    for key, game in GAMES.items():
        out[key] = {
            "name": game["name"],
            "main_label": game["main_label"],
            "main_max": game["main_max"],
            "main_pick": game["main_pick"],
            "extra_label": game["extra_label"],
            "extra_max": game["extra_max"],
            "extra_pick": game["extra_pick"],
            "price": game["price"],
            "append_price": game.get("append_price", game["price"]),
            "plays": game["plays"],
            "compound": game.get("compound"),
            "pick_min": game.get("pick_min"),
            "pick_max": game.get("pick_max"),
            "compound_max": game.get("compound_max"),
            "source_name": game["source_name"],
            "source_url": game["api"],
        }
    return out


class LotteryError(RuntimeError):
    """可以直接展示给用户的错误。"""


def _comb(n: int, k: int) -> int:
    if k < 0 or n < 0 or k > n:
        return 0
    return math.comb(n, k)


def _int(value, default=0, lo=None, hi=None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _amount(value) -> int:
    try:
        return int(round(float(str(value or "0").replace(",", "").strip())))
    except (TypeError, ValueError):
        return 0


def _iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _cache_path(game: str, config: dict) -> Path:
    return config_mod.workdir(config) / "lottery" / f"{game}.json"


def _read_cache(game: str, config: dict):
    path = _cache_path(game, config)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("draws"), list):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    return data


def _write_cache(game: str, config: dict, payload: dict) -> None:
    path = _cache_path(game, config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        LOG.warning("写彩票缓存失败：%s", exc)


def _age_seconds(payload: dict) -> float:
    try:
        when = dt.datetime.fromisoformat(payload.get("fetched_at"))
        return max(0.0, (dt.datetime.now().astimezone() - when).total_seconds())
    except (TypeError, ValueError):
        return CACHE_TTL_SECONDS + 1


def _parse_dlt(payload: dict) -> list[dict]:
    draws = []
    for item in ((payload.get("value") or {}).get("list") or []):
        if not isinstance(item, dict):
            continue
        raw = str(item.get("lotteryDrawResult") or "").split()
        try:
            nums = [int(x) for x in raw]
        except ValueError:
            continue
        if len(nums) < 7:
            continue
        prizes = {}
        for prize in item.get("prizeLevelList") or []:
            name = str(prize.get("prizeLevel") or "").strip()
            if name not in ("一等奖", "二等奖", "三等奖", "四等奖", "五等奖", "六等奖", "七等奖"):
                continue
            amount = _amount(prize.get("stakeAmountFormat") or prize.get("stakeAmount"))
            prizes[name] = amount
        draws.append(
            {
                "issue": str(item.get("lotteryDrawNum") or ""),
                "date": str(item.get("lotteryDrawTime") or ""),
                "main": nums[:5],
                "extra": nums[5:7],
                "prizes": prizes,
            }
        )
    return draws


def _parse_cwl(payload: dict, game: str) -> list[dict]:
    draws = []
    for item in (payload.get("result") or []):
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "").split("(")[0].strip()
        prizes = {}
        for prize in item.get("prizegrades") or []:
            key = str(prize.get("type") or "").strip()
            if key:
                prizes[key] = _amount(prize.get("typemoney"))
        if game == "ssq":
            try:
                main = [int(x) for x in str(item.get("red") or "").split(",") if x.strip()]
                extra = [int(item.get("blue"))]
            except (TypeError, ValueError):
                continue
            if len(main) != 6 or len(extra) != 1:
                continue
        else:
            try:
                main = [int(x) for x in str(item.get("red") or "").split(",") if x.strip()]
            except ValueError:
                continue
            if len(main) != 20:
                continue
            extra = []
        draws.append(
            {
                "issue": str(item.get("code") or ""),
                "date": date,
                "main": main,
                "extra": extra,
                "prizes": prizes,
            }
        )
    return draws


def _fetch_remote(game: str, config: dict) -> list[dict]:
    spec = GAMES[game]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.cwl.gov.cn/" if game in ("ssq", "kl8") else "https://www.sporttery.cn/",
    }
    try:
        # 官方站点国内直连；不跟随系统代理，避免被海外节点挡掉
        session = requests.Session()
        session.trust_env = False
        resp = session.get(spec["api"], headers=headers, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise LotteryError(f"获取{spec['name']}历史数据失败：{exc}") from exc

    draws = _parse_dlt(payload) if game == "dlt" else _parse_cwl(payload, game)
    if not draws:
        raise LotteryError(f"{spec['name']}接口没有返回有效开奖数据，可能接口已变更")
    draws.sort(key=lambda x: int(x["issue"]) if str(x["issue"]).isdigit() else 0, reverse=True)
    return draws[:FETCH_SIZE]


def fetch_history(game: str, config: dict | None = None, force: bool = False) -> dict:
    if game not in GAMES:
        raise LotteryError("未知彩种")
    config = config or config_mod.load()
    cached = _read_cache(game, config)
    if not force and cached and _age_seconds(cached) < CACHE_TTL_SECONDS and len(cached["draws"]) >= 10:
        return {
            "draws": cached["draws"],
            "source": cached.get("source") or GAMES[game]["api"],
            "source_name": GAMES[game]["source_name"],
            "fetched_at": cached.get("fetched_at") or "",
            "cached": True,
            "stale": False,
            "warning": "",
        }

    try:
        draws = _fetch_remote(game, config)
        fetched_at = _iso_now()
        _write_cache(
            game,
            config,
            {
                "version": CACHE_VERSION,
                "game": game,
                "fetched_at": fetched_at,
                "source": GAMES[game]["api"],
                "draws": draws,
            },
        )
        return {
            "draws": draws,
            "source": GAMES[game]["api"],
            "source_name": GAMES[game]["source_name"],
            "fetched_at": fetched_at,
            "cached": False,
            "stale": False,
            "warning": "",
        }
    except LotteryError as exc:
        if cached and cached.get("draws"):
            return {
                "draws": cached["draws"],
                "source": cached.get("source") or GAMES[game]["api"],
                "source_name": GAMES[game]["source_name"],
                "fetched_at": cached.get("fetched_at") or "",
                "cached": True,
                "stale": True,
                "warning": f"{exc}；已使用本地缓存（{cached.get('fetched_at', '时间未知')}）",
            }
        raise


def _zones(max_n: int, parts: int = 3) -> list[list[int]]:
    size = math.ceil(max_n / parts)
    return [list(range(start, min(start + size - 1, max_n) + 1))
            for start in range(1, max_n + 1, size)]


def compute_stats(draws: list[dict], game: str, window: int = 100) -> dict:
    spec = GAMES[game]
    window = _int(window, 100, 10, min(200, len(draws)))
    selected = draws[:window]
    main_max = spec["main_max"]
    extra_max = spec["extra_max"]
    main_freq = {n: 0 for n in range(1, main_max + 1)}
    extra_freq = {n: 0 for n in range(1, extra_max + 1)} if extra_max else {}
    main_last_seen: dict[int, int] = {}
    extra_last_seen: dict[int, int] = {}

    for index, draw in enumerate(selected):
        for n in draw["main"]:
            if n in main_freq:
                main_freq[n] += 1
                main_last_seen.setdefault(n, index)
        for n in draw["extra"]:
            if n in extra_freq:
                extra_freq[n] += 1
                extra_last_seen.setdefault(n, index)

    main_omission = {n: main_last_seen.get(n, window) for n in main_freq}
    extra_omission = {n: extra_last_seen.get(n, window) for n in extra_freq}

    sums = [sum(d["main"]) for d in selected]
    last = selected[0]
    prev = selected[1] if len(selected) > 1 else {"main": []}
    last_sorted = sorted(last["main"])
    odd = sum(1 for n in last_sorted if n % 2 == 1)
    zone_counts = []
    for zone in _zones(main_max):
        zone_counts.append(sum(1 for n in last_sorted if n in zone))
    consecutive = sum(1 for a, b in zip(last_sorted, last_sorted[1:]) if b == a + 1)
    repeat = len(set(last_sorted) & set(prev.get("main") or []))

    def hot_cold(freq, omission):
        hot = sorted(freq, key=lambda n: (-freq[n], omission[n], n))[:8]
        cold = sorted(freq, key=lambda n: (freq[n], -omission[n], n))[:8]
        return hot, cold

    main_hot, main_cold = hot_cold(main_freq, main_omission)
    extra_hot, extra_cold = hot_cold(extra_freq, extra_omission) if extra_freq else ([], [])

    return {
        "window": len(selected),
        "main": {
            "frequency": [{"n": n, "count": main_freq[n]} for n in range(1, main_max + 1)],
            "omission": [{"n": n, "count": main_omission[n]} for n in range(1, main_max + 1)],
            "hot": main_hot,
            "cold": main_cold,
            "avg_freq": round(sum(main_freq.values()) / max(1, len(selected)), 2),
        },
        "extra": {
            "frequency": [{"n": n, "count": extra_freq[n]} for n in range(1, extra_max + 1)],
            "omission": [{"n": n, "count": extra_omission[n]} for n in range(1, extra_max + 1)],
            "hot": extra_hot,
            "cold": extra_cold,
        } if extra_max else None,
        "sum": {
            "avg": round(sum(sums) / max(1, len(sums)), 1),
            "min": min(sums) if sums else 0,
            "max": max(sums) if sums else 0,
            "last": sums[0] if sums else 0,
        },
        "odd_even": {"odd": odd, "even": len(last_sorted) - odd},
        "zones": zone_counts,
        "consecutive": consecutive,
        "repeat": repeat,
        "last": {"issue": last["issue"], "date": last["date"], "main": last_sorted,
                 "extra": sorted(last["extra"])},
        "recent": [
            {"issue": d["issue"], "date": d["date"], "main": sorted(d["main"]),
             "extra": sorted(d["extra"])}
            for d in selected[:5]
        ],
    }


def _weights(numbers: list[int], freq: dict, omission: dict, strategy: str) -> list[float]:
    if strategy == "random":
        return [1.0 for _ in numbers]
    max_freq = max(freq.values()) if freq else 1
    max_omission = max(omission.values()) if omission else 1
    total_freq = sum(freq.values()) or 1
    avg = total_freq / max(1, len(freq))
    out = []
    for n in numbers:
        f = freq.get(n, 0)
        o = omission.get(n, 0)
        if strategy == "hot":
            w = 1.0 + 2.0 * f / max(1, max_freq)
        elif strategy == "cold":
            w = 1.0 + 2.0 * o / max(1, max_omission)
        elif strategy == "trend":
            w = 1.0 + 1.5 * f / max(1, max_freq) + 0.5 * o / max(1, max_omission)
        else:  # balanced：越接近平均出现次数越稳
            w = 1.2 - min(0.8, abs(f - avg) / max(1, avg))
        out.append(max(0.05, w))
    return out


def _sample(numbers: list[int], k: int, weights: list[float], rng: random.Random) -> list[int]:
    candidates = list(numbers)
    weights = list(weights)
    picked = []
    for _ in range(min(k, len(candidates))):
        total = sum(weights)
        if total <= 0:
            index = rng.randrange(len(candidates))
        else:
            point = rng.random() * total
            acc = 0.0
            index = len(candidates) - 1
            for i, w in enumerate(weights):
                acc += w
                if point <= acc:
                    index = i
                    break
        picked.append(candidates.pop(index))
        weights.pop(index)
    return picked


def _pick_main(game: str, count: int, stats: dict, strategy: str, rng: random.Random) -> list[int]:
    spec = GAMES[game]
    numbers = list(range(1, spec["main_max"] + 1))
    main_stats = stats["main"]
    freq = {item["n"]: item["count"] for item in main_stats["frequency"]}
    omission = {item["n"]: item["count"] for item in main_stats["omission"]}

    if strategy == "balanced" and game in ("dlt", "ssq") and count >= 3:
        zones = _zones(spec["main_max"])
        base = count // len(zones)
        extra = count - base * len(zones)
        quotas = [base + (1 if i < extra else 0) for i in range(len(zones))]
        picked = []
        for zone, quota in zip(zones, quotas):
            weights = _weights(zone, freq, omission, strategy)
            picked.extend(_sample(zone, quota, weights, rng))
        if len(picked) < count:
            remain = [n for n in numbers if n not in picked]
            picked.extend(_sample(remain, count - len(picked),
                                  _weights(remain, freq, omission, "random"), rng))
        return sorted(picked[:count])

    weights = _weights(numbers, freq, omission, strategy)
    return sorted(_sample(numbers, count, weights, rng))


def _pick_extra(game: str, count: int, stats: dict, strategy: str, rng: random.Random) -> list[int]:
    spec = GAMES[game]
    if not spec["extra_max"] or count <= 0:
        return []
    numbers = list(range(1, spec["extra_max"] + 1))
    extra_stats = stats.get("extra") or {}
    freq = {item["n"]: item["count"] for item in extra_stats.get("frequency", [])}
    omission = {item["n"]: item["count"] for item in extra_stats.get("omission", [])}
    return sorted(_sample(numbers, count, _weights(numbers, freq, omission, strategy), rng))


def _single_ticket(game: str, stats: dict, strategy: str, rng: random.Random,
                   pick_count: int | None = None) -> dict:
    spec = GAMES[game]
    pick = pick_count if pick_count is not None else spec["main_pick"]
    return {
        "main": _pick_main(game, pick, stats, strategy, rng),
        "extra": _pick_extra(game, spec["extra_pick"], stats, strategy, rng),
    }


def _validate_numbers(values, max_n: int, label: str, count: int) -> list[int]:
    if not isinstance(values, list):
        raise LotteryError(f"{label}需要是数组")
    nums = []
    for value in values:
        n = _int(value, 0, 1, max_n)
        if n < 1 or n > max_n:
            raise LotteryError(f"{label}号码 {value} 超出 1~{max_n}")
        nums.append(n)
    if len(set(nums)) != len(nums):
        raise LotteryError(f"{label}号码不能重复")
    if len(nums) != count:
        raise LotteryError(f"{label}需要 {count} 个号码")
    return sorted(nums)


def _parse_params(game: str, play: str, params: dict) -> dict:
    spec = GAMES[game]
    params = params or {}
    if play not in spec["plays"]:
        raise LotteryError(f"{spec['name']}不支持玩法：{play}")
    out = {"play": play}
    if play in ("single", "multiple", "package"):
        default = 1 if play == "single" else (10 if play == "package" else 1)
        hi = 50 if play == "package" else 20
        out["count"] = _int(params.get("count"), default, 1, hi)
        if game == "kl8":
            out["pick"] = _int(params.get("pick"), 5, spec["pick_min"], spec["pick_max"])
        return out

    if play == "compound":
        if game == "kl8":
            pick = _int(params.get("pick"), 5, spec["pick_min"], spec["pick_max"])
            out["pick"] = pick
            out["main_count"] = _int(params.get("main_count"), pick + 2,
                                     pick + 1, spec.get("compound_max", 15))
            if out["main_count"] <= pick:
                raise LotteryError("快乐8复式选号个数必须多于基础玩法个数")
            return out
        c = spec["compound"]
        if game == "dlt":
            out["main_count"] = _int(params.get("main_count"), 6, c["main_min"], c["main_max"])
            out["extra_count"] = _int(params.get("extra_count"), 2, c["extra_min"], c["extra_max"])
        else:
            out["main_count"] = _int(params.get("main_count"), 7, c["main_min"], c["main_max"])
            out["extra_count"] = _int(params.get("extra_count"), 1, c["extra_min"], c["extra_max"])
        return out

    if play == "dantuo":
        if game == "kl8":
            pick = _int(params.get("pick"), 5, 2, spec["pick_max"])
            dan = _int(params.get("main_dan"), 2, 1, pick - 1)
            need = pick - dan
            tuo_min = need + 1
            tuo = _int(params.get("main_tuo"), max(4, tuo_min), tuo_min, spec["main_max"] - dan)
            if dan + tuo <= pick:
                raise LotteryError("快乐8胆码 + 拖码必须多于基础玩法个数")
            out["pick"] = pick
            out["main_dan"] = dan
            out["main_tuo"] = tuo
            return out
        if game == "dlt":
            out["main_dan"] = _int(params.get("main_dan"), 2, 1, 4)
            out["main_tuo"] = _int(params.get("main_tuo"), 4, 2, 12)
            out["extra_count"] = 2
            if out["main_dan"] + out["main_tuo"] < 5:
                raise LotteryError("前区胆码 + 拖码至少要有 5 个号码")
        else:
            out["main_dan"] = _int(params.get("main_dan"), 2, 1, 5)
            out["main_tuo"] = _int(params.get("main_tuo"), 4, 1, 12)
            out["extra_count"] = _int(params.get("extra_count"), 1, 1, 6)
            if out["main_dan"] + out["main_tuo"] < 6:
                raise LotteryError("红球胆码 + 拖码至少要有 6 个号码")
        return out

    raise LotteryError(f"未知玩法：{play}")


def _bet_count(game: str, params: dict) -> int:
    spec = GAMES[game]
    play = params["play"]
    if play in ("single", "multiple", "package"):
        return params["count"]
    if play == "compound":
        if game == "kl8":
            return _comb(params["main_count"], params["pick"])
        if game == "dlt":
            return _comb(params["main_count"], 5) * _comb(params["extra_count"], 2)
        return _comb(params["main_count"], 6) * params["extra_count"]
    if play == "dantuo":
        if game == "kl8":
            return _comb(params["main_tuo"], params["pick"] - params["main_dan"])
        if game == "dlt":
            return _comb(params["main_tuo"], 5 - params["main_dan"])
        return _comb(params["main_tuo"], 6 - params["main_dan"]) * params["extra_count"]
    return 0


def _build_tickets(game: str, params: dict, stats: dict, strategy: str, rng: random.Random):
    spec = GAMES[game]
    play = params["play"]
    tickets = []
    selection = None

    if play in ("single", "multiple", "package"):
        for _ in range(params["count"]):
            pick = params.get("pick") if game == "kl8" else None
            ticket = _single_ticket(game, stats, strategy, rng, pick)
            if game == "kl8":
                ticket["extra"] = []
            tickets.append({"kind": "single", **ticket})
        return tickets, selection

    if play == "compound":
        main = _pick_main(game, params["main_count"], stats, strategy, rng)
        if game == "kl8":
            selection = {"main": main, "extra": [], "dan": [], "tuo": main}
            tickets.append({"kind": "compound", "main": main, "extra": []})
            return tickets, selection
        extra = _pick_extra(game, params["extra_count"], stats, strategy, rng)
        selection = {"main": main, "extra": extra, "dan": [], "tuo": main}
        tickets.append({"kind": "compound", "main": main, "extra": extra})
        return tickets, selection

    if play == "dantuo":
        dan = _pick_main(game, params["main_dan"], stats, strategy, rng)
        remain = [n for n in range(1, spec["main_max"] + 1) if n not in dan]
        freq = {item["n"]: item["count"] for item in stats["main"]["frequency"]}
        omission = {item["n"]: item["count"] for item in stats["main"]["omission"]}
        tuo = _sample(remain, params["main_tuo"], _weights(remain, freq, omission, strategy), rng)
        tuo = sorted(tuo)
        if game == "kl8":
            selection = {"main": sorted(dan + tuo), "extra": [], "dan": sorted(dan), "tuo": tuo}
            tickets.append({"kind": "dantuo", "main": sorted(dan + tuo), "extra": [],
                            "dan": sorted(dan), "tuo": tuo})
            return tickets, selection
        extra = _pick_extra(game, params["extra_count"], stats, strategy, rng)
        selection = {"main": sorted(dan + tuo), "extra": extra, "dan": sorted(dan), "tuo": tuo}
        tickets.append({"kind": "dantuo", "main": sorted(dan + tuo), "extra": extra,
                        "dan": sorted(dan), "tuo": tuo})
        return tickets, selection

    raise LotteryError("未知玩法")


def simulate(game: str, play: str, params: dict | None = None,
             strategy: str = "hot", window: int = 100, seed: str | int | None = None,
             append: bool = False, config: dict | None = None) -> dict:
    if game not in GAMES:
        raise LotteryError("未知彩种")
    spec = GAMES[game]
    if strategy not in STRATEGIES:
        strategy = "hot"
    parsed = _parse_params(game, play, params or {})
    history = fetch_history(game, config)
    draws = history["draws"]
    window = _int(window, 100, 10, min(200, len(draws)))
    stats = compute_stats(draws, game, window)

    seed_text = str(seed).strip() if seed not in (None, "") else str(secrets.randbits(32))
    rng = random.Random(seed_text)
    tickets, selection = _build_tickets(game, parsed, stats, strategy, rng)
    count = _bet_count(game, parsed)
    if count <= 0:
        raise LotteryError("按当前参数计算出的注数为 0，请检查胆码/拖码")
    if count > 20000:
        raise LotteryError(f"注数 {count} 太多，请减少复式/拖码数量")
    unit_price = spec.get("append_price", spec["price"]) if (game == "dlt" and append) else spec["price"]
    amount = count * unit_price

    latest = history["draws"][0]
    return {
        "ok": True,
        "game": game,
        "game_name": spec["name"],
        "play": parsed["play"],
        "play_name": PLAY_NAMES[parsed["play"]],
        "strategy": strategy,
        "strategy_name": STRATEGIES[strategy],
        "seed": seed_text,
        "append": bool(append and game == "dlt"),
        "bet_count": count,
        "unit_price": unit_price,
        "amount": amount,
        "params": parsed,
        "tickets": tickets,
        "selection": selection,
        "data": {
            "source": history["source"],
            "source_name": history["source_name"],
            "fetched_at": history["fetched_at"],
            "cached": history["cached"],
            "stale": history["stale"],
            "warning": history["warning"],
            "window": stats["window"],
            "latest_issue": latest["issue"],
            "latest_date": latest["date"],
        },
        "stats": stats,
        "notes": [
            "模拟结果由随机算法生成，不代表真实开奖，也不构成任何购彩建议。",
            "历史开奖相互独立，冷热/遗漏/和值等统计不能预测下一期。",
            "复式/胆拖上限是本工具为控制注数设置的保护值，具体以官方规则为准。",
        ],
    }


# ------------------------------------------------------------------ 保存与开奖核对

SAVED_VERSION = 1
SAVED_LIMIT = 200

DLT_LEVEL_NAMES = {
    1: "一等奖",
    2: "二等奖",
    3: "三等奖",
    4: "四等奖",
    5: "五等奖",
    6: "六等奖",
    7: "七等奖",
}
SSQ_LEVEL_NAMES = {1: "一等奖", 2: "二等奖", 3: "三等奖",
                   4: "四等奖", 5: "五等奖", 6: "六等奖"}
# 官方浮动奖金额按当期公告；这里只是接口没给金额时的兜底（低奖池口径）
DLT_FALLBACK = {3: 5000, 4: 300, 5: 150, 6: 15, 7: 5}
SSQ_FALLBACK = {3: 3000, 4: 200, 5: 10, 6: 5}


def _dlt_level(main_hit: int, extra_hit: int):
    """按 2026 年 1 月新规则（7 个奖级）判断大乐透奖级。"""
    if main_hit == 5 and extra_hit == 2:
        return 1
    if main_hit == 5 and extra_hit == 1:
        return 2
    if (main_hit == 5 and extra_hit == 0) or (main_hit == 4 and extra_hit == 2):
        return 3
    if main_hit == 4 and extra_hit == 1:
        return 4
    if (main_hit == 4 and extra_hit == 0) or (main_hit == 3 and extra_hit == 2):
        return 5
    if (main_hit == 3 and extra_hit == 1) or (main_hit == 2 and extra_hit == 2):
        return 6
    if (
        (main_hit == 3 and extra_hit == 0)
        or (main_hit == 2 and extra_hit == 1)
        or (main_hit == 1 and extra_hit == 2)
        or (main_hit == 0 and extra_hit == 2)
    ):
        return 7
    return None


def _ssq_level(red_hit: int, blue_hit: int):
    if red_hit == 6 and blue_hit == 1:
        return 1
    if red_hit == 6 and blue_hit == 0:
        return 2
    if red_hit == 5 and blue_hit == 1:
        return 3
    if (red_hit == 5 and blue_hit == 0) or (red_hit == 4 and blue_hit == 1):
        return 4
    if (red_hit == 4 and blue_hit == 0) or (red_hit == 3 and blue_hit == 1):
        return 5
    if blue_hit == 1 and red_hit in (2, 1, 0):
        return 6
    return None


def _score_ticket(game: str, ticket: dict, draw: dict, append: bool) -> dict:
    main = set(ticket.get("main") or [])
    extra = set(ticket.get("extra") or [])
    draw_main = set(draw.get("main") or [])
    draw_extra = set(draw.get("extra") or [])
    main_hit = len(main & draw_main)
    extra_hit = len(extra & draw_extra)
    prizes = draw.get("prizes") or {}

    if game == "dlt":
        level = _dlt_level(main_hit, extra_hit)
        if not level:
            return {"level": None, "level_name": "", "amount": 0,
                    "main_hit": main_hit, "extra_hit": extra_hit}
        base = _amount(prizes.get(DLT_LEVEL_NAMES[level])) or DLT_FALLBACK.get(level, 0)
        # 新规则：追加只参与一、二等奖（浮动奖）
        amount = int(round(base * 1.8)) if (append and level in (1, 2)) else base
        return {"level": level, "level_name": DLT_LEVEL_NAMES[level], "amount": amount,
                "main_hit": main_hit, "extra_hit": extra_hit}

    if game == "ssq":
        level = _ssq_level(main_hit, extra_hit)
        if not level:
            return {"level": None, "level_name": "", "amount": 0,
                    "main_hit": main_hit, "extra_hit": extra_hit}
        base = _amount(prizes.get(str(level))) or SSQ_FALLBACK.get(level, 0)
        return {"level": level, "level_name": SSQ_LEVEL_NAMES[level], "amount": base,
                "main_hit": main_hit, "extra_hit": extra_hit}

    # 快乐8：按“选 k 中 m”查当期官方奖金表
    k = len(main)
    hit = main_hit
    amount = _amount(prizes.get(f"x{k}z{hit}"))
    return {
        "level": f"选{k}中{hit}" if amount > 0 else None,
        "level_name": f"选{k}中{hit}" if amount > 0 else "",
        "amount": amount,
        "main_hit": hit,
        "extra_hit": 0,
    }


def _expanded_tickets(record: dict):
    """把复式/胆拖展开成实际参与核对的基本投注。"""
    game = record["game"]
    play = record["play"]
    tickets = record.get("tickets") or []
    selection = record.get("selection") or {}

    if play in ("single", "multiple", "package"):
        for ticket in tickets:
            yield ticket
        return

    if play == "compound":
        main = selection.get("main") or []
        extra = selection.get("extra") or []
        if game == "kl8":
            pick = int(record.get("params", {}).get("pick") or 5)
            for main_part in combinations(main, pick):
                yield {"main": list(main_part), "extra": []}
        elif game == "dlt":
            for main_part in combinations(main, 5):
                for extra_part in combinations(extra, 2):
                    yield {"main": list(main_part), "extra": list(extra_part)}
        else:
            for main_part in combinations(main, 6):
                for blue in extra:
                    yield {"main": list(main_part), "extra": [blue]}
        return

    if play == "dantuo":
        dan = selection.get("dan") or []
        tuo = selection.get("tuo") or []
        extra = selection.get("extra") or []
        if game == "kl8":
            pick = int(record.get("params", {}).get("pick") or 5)
            need = pick - len(dan)
            for picked in combinations(tuo, need):
                yield {"main": sorted(list(dan) + list(picked)), "extra": []}
        elif game == "dlt":
            need = 5 - len(dan)
            for picked in combinations(tuo, need):
                for extra_part in combinations(extra, 2):
                    yield {"main": sorted(list(dan) + list(picked)),
                           "extra": list(extra_part)}
        else:
            need = 6 - len(dan)
            for picked in combinations(tuo, need):
                for blue in extra:
                    yield {"main": sorted(list(dan) + list(picked)),
                           "extra": [blue]}
        return


def score_record(record: dict, draw: dict) -> dict:
    """核对一条保存记录，返回奖级分布和总奖金。"""
    game = record["game"]
    append = bool(record.get("append"))
    expanded = list(_expanded_tickets(record))
    if not expanded:
        raise LotteryError("这条记录没有可核对的号码")
    if len(expanded) != int(record.get("bet_count") or 0):
        raise LotteryError("记录注数与展开后的组合数不一致，可能已损坏")

    total = 0
    winning_tickets = 0
    levels: dict[str, dict] = {}
    for ticket in expanded:
        result = _score_ticket(game, ticket, draw, append)
        if not result["level"]:
            continue
        winning_tickets += 1
        total += result["amount"]
        key = str(result["level"])
        bucket = levels.setdefault(key, {
            "level": result["level"],
            "name": result["level_name"],
            "count": 0,
            "amount": 0,
        })
        bucket["count"] += 1
        bucket["amount"] += result["amount"]

    level_list = list(levels.values())
    numeric = [item["level"] for item in level_list if isinstance(item["level"], int)]
    best = min(numeric) if numeric else (level_list[0]["level"] if level_list else None)
    return {
        "issue": draw["issue"],
        "date": draw["date"],
        "draw_main": draw["main"],
        "draw_extra": draw["extra"],
        "winning": winning_tickets > 0,
        "amount": total,
        "winning_tickets": winning_tickets,
        "total_tickets": len(expanded),
        "best_level": best,
        "levels": level_list,
        "checked_at": _iso_now(),
        "note": "按官方开奖号码自动核对，仅供参考；实际兑奖以官方终端为准。",
    }


def _saved_path(config: dict) -> Path:
    return config_mod.workdir(config) / "lottery" / "saved.json"


def load_saved(config: dict | None = None) -> dict:
    config = config or config_mod.load()
    path = _saved_path(config)
    if not path.is_file():
        return {"version": SAVED_VERSION, "records": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": SAVED_VERSION, "records": []}
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return {"version": SAVED_VERSION, "records": []}
    return data


def _write_saved(data: dict, config: dict) -> None:
    path = _saved_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _valid_numbers(nums, max_n: int, label: str) -> None:
    if not isinstance(nums, list) or not nums:
        raise LotteryError(f"{label}不能为空")
    for value in nums:
        n = _int(value, 0)
        if n < 1 or n > max_n:
            raise LotteryError(f"{label}号码 {value} 超出 1~{max_n}")
    if len(set(nums)) != len(nums):
        raise LotteryError(f"{label}号码不能重复")


def save_result(payload: dict, config: dict | None = None) -> dict:
    """保存一次模拟结果，返回保存后的记录。"""
    config = config or config_mod.load()
    payload = payload or {}
    game = str(payload.get("game") or "")
    play = str(payload.get("play") or "")
    if game not in GAMES:
        raise LotteryError("未知彩种")
    spec = GAMES[game]
    parsed = _parse_params(game, play, payload.get("params") or {})
    count = _bet_count(game, parsed)
    if count <= 0:
        raise LotteryError("注数为 0，不能保存")
    if count > 20000:
        raise LotteryError("注数过多，不能保存")

    tickets = payload.get("tickets") or []
    selection = payload.get("selection")
    if play in ("single", "multiple", "package"):
        if not tickets:
            raise LotteryError("没有可保存的号码")
        for ticket in tickets:
            _valid_numbers(ticket.get("main"), spec["main_max"], "号码")
            if spec["extra_max"]:
                _valid_numbers(ticket.get("extra"), spec["extra_max"], spec["extra_label"])
    else:
        if not isinstance(selection, dict):
            raise LotteryError("复式/胆拖缺少选号集合")
        _valid_numbers(selection.get("main"), spec["main_max"], spec["main_label"])
        if spec["extra_max"]:
            _valid_numbers(selection.get("extra"), spec["extra_max"], spec["extra_label"])
        if play == "dantuo":
            _valid_numbers(selection.get("dan"), spec["main_max"], "胆码")
            _valid_numbers(selection.get("tuo"), spec["main_max"], "拖码")

    append = bool(payload.get("append") and game == "dlt")
    unit_price = spec.get("append_price", spec["price"]) if append else spec["price"]
    data = payload.get("data") or {}
    record = {
        "id": dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3),
        "created_at": _iso_now(),
        "game": game,
        "game_name": spec["name"],
        "play": parsed["play"],
        "play_name": PLAY_NAMES[parsed["play"]],
        "strategy": payload.get("strategy") if payload.get("strategy") in STRATEGIES else "hot",
        "strategy_name": STRATEGIES.get(payload.get("strategy"), STRATEGIES["hot"]),
        "seed": str(payload.get("seed") or ""),
        "append": append,
        "params": parsed,
        "tickets": tickets,
        "selection": selection,
        "bet_count": count,
        "unit_price": unit_price,
        "amount": count * unit_price,
        "reference_issue": str(data.get("latest_issue") or ""),
        "reference_date": str(data.get("latest_date") or ""),
        "check": None,
    }
    store = load_saved(config)
    store["version"] = SAVED_VERSION
    store["records"].insert(0, record)
    store["records"] = store["records"][:SAVED_LIMIT]
    _write_saved(store, config)
    return record


def delete_saved(record_id: str, config: dict | None = None) -> bool:
    config = config or config_mod.load()
    store = load_saved(config)
    before = len(store["records"])
    store["records"] = [r for r in store["records"] if r.get("id") != record_id]
    if len(store["records"]) == before:
        return False
    _write_saved(store, config)
    return True


def check_saved(record_id: str, config: dict | None = None, issue: str = "") -> dict:
    """用参考期号的下一期开奖核对；也可显式指定 issue。"""
    config = config or config_mod.load()
    store = load_saved(config)
    record = next((r for r in store["records"] if r.get("id") == record_id), None)
    if record is None:
        raise LotteryError("找不到这条历史记录")
    history = fetch_history(record["game"], config)
    draws = history["draws"]

    if issue:
        target = next((d for d in draws if str(d.get("issue")) == str(issue)), None)
        if target is None:
            raise LotteryError(f"近期数据里找不到第 {issue} 期")
    else:
        ref = str(record.get("reference_issue") or "")
        if not ref:
            target = draws[0]
        else:
            index = next((i for i, d in enumerate(draws) if str(d.get("issue")) == ref), None)
            if index is None:
                raise LotteryError(f"近期数据里找不到参考期号 {ref}，暂时无法核对")
            if index == 0:
                raise LotteryError(f"参考期号 {ref} 之后还没有新开奖，请开奖后再试")
            target = draws[index - 1]

    check = score_record(record, target)
    record["check"] = check
    store["version"] = SAVED_VERSION
    _write_saved(store, config)
    return record
