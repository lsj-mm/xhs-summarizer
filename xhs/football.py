# -*- coding: utf-8 -*-
"""竞彩足球参考分析（500.com 竞彩赛程/赔率）。

边界：
    - 数据来自 500.com 竞彩足球页面，中文队名和竞彩编号与体彩一致；
    - 比分、半全场、总进球都直接使用对应玩法的真实赔率，不用另外编造；
    - 页面没有伤停/阵容/历史交锋，页面会明确标注；
    - 结果只做娱乐参考，不构成投注建议。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

import requests

from . import config as config_mod

LOG = logging.getLogger(__name__)

BASE_URL = "https://trade.500.com/jczq/"
PAGE_URL = BASE_URL + "?playid={playid}&g=2&date={date}"
PAGE_IDS = (269, 270, 271, 272)  # 胜平负/让球、总进球、比分、半全场
CACHE_VERSION = 4
CACHE_TTL_SECONDS = 10 * 60
MAX_MATCHES = 60

ROW_SPLIT = '<tr class="bet-tb-tr"'
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
ODDS_RE = re.compile(r'data-type="([^"]+)"\s+data-value="([^"]*)"\s+data-sp="([^"]+)"')
HOME_RE = re.compile(r'class="team-l" title="([^"]*)"')
AWAY_RE = re.compile(r'class="team-r" title="([^"]*)"')


class FootballError(RuntimeError):
    """可直接展示给用户的错误。"""


def _cache_path(day: str, config: dict) -> Path:
    return config_mod.workdir(config) / "football500" / f"{day}.json"


def _read_cache(day: str, config: dict):
    path = _cache_path(day, config)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("matches"), list):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    return data


def _write_cache(day: str, config: dict, payload: dict) -> None:
    path = _cache_path(day, config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        LOG.warning("写足球缓存失败：%s", exc)


def _age_seconds(payload: dict) -> float:
    try:
        when = dt.datetime.fromisoformat(payload.get("fetched_at"))
        return max(0.0, (dt.datetime.now().astimezone() - when).total_seconds())
    except (TypeError, ValueError):
        return CACHE_TTL_SECONDS + 1


def _fetch_page(playid: int, day: str) -> str:
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        PAGE_URL.format(playid=playid, date=day),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": BASE_URL,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=20,
    )
    response.raise_for_status()
    # 500.com 页面声明 gbk；个别广告字节会解码失败，用 replace 保留正文
    return response.content.decode("gbk", errors="replace")


def _parse_rows(html: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    chunks = html.split(ROW_SPLIT)[1:]
    for chunk in chunks:
        tag_end = chunk.find(">")
        if tag_end < 0:
            continue
        attrs = dict(ATTR_RE.findall(chunk[:tag_end]))
        fid = attrs.get("data-fixtureid")
        if not fid:
            continue
        if not attrs.get("data-matchdate"):
            continue
        home_match = HOME_RE.search(chunk)
        away_match = AWAY_RE.search(chunk)
        home = home_match.group(1).strip() if home_match else attrs.get("data-homesxname", "")
        away = away_match.group(1).strip() if away_match else attrs.get("data-awaysxname", "")
        if not home or not away:
            continue
        row = rows.setdefault(fid, {
            "id": fid,
            "matchnum": attrs.get("data-matchnum", ""),
            "league": attrs.get("data-simpleleague", ""),
            "date": attrs.get("data-matchdate", ""),
            "processdate": attrs.get("data-processdate", ""),
            "time": attrs.get("data-matchtime", ""),
            "home": home,
            "away": away,
            "handicap": attrs.get("data-rangqiu", ""),
            "markets": {},
        })
        for kind, value, sp in ODDS_RE.findall(chunk):
            try:
                price = float(sp)
            except ValueError:
                continue
            if price <= 0:
                continue
            markets = row["markets"]
            if kind == "nspf":
                markets.setdefault("one_x_two", {})[value] = price
            elif kind == "spf":
                markets.setdefault("handicap", {})[value] = price
            elif kind == "jqs":
                markets.setdefault("total", {})[value] = price
            elif kind == "bqc":
                markets.setdefault("htft", {})[value] = price
            elif kind == "bf":
                markets.setdefault("score", {})[value] = price
    return rows


def _row_belongs(row: dict, day: dt.date) -> bool:
    """500.com 按「周五/周六」销售日分组，凌晨比赛会落在次日。"""
    weekday = "一二三四五六日"[day.weekday()]
    prefix = "周" + weekday
    return (
        str(row.get("matchnum") or "").startswith(prefix)
        or row.get("processdate") == day.isoformat()
        or row.get("date") == day.isoformat()
    )


def _fetch_all(day: str):
    day_obj = dt.date.fromisoformat(day)
    merged: dict[str, dict] = {}
    warnings = []
    for playid in PAGE_IDS:
        try:
            html = _fetch_page(playid, day)
        except Exception as exc:
            warnings.append(f"玩法 {playid} 获取失败：{exc}")
            continue
        for fid, row in _parse_rows(html).items():
            if not _row_belongs(row, day_obj):
                continue
            if fid not in merged:
                merged[fid] = row
            else:
                merged[fid]["markets"].update(row.get("markets") or {})
    return list(merged.values()), warnings


def _normalize(market: dict[str, float]):
    if not market:
        return {}
    implied = {key: 1.0 / price for key, price in market.items() if price > 0}
    total = sum(implied.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in implied.items()}


def _pct(value: float) -> float:
    return round(value * 100, 1)


def _score_label(value: str):
    label_map = {"胜其它": "主胜其他", "平其它": "平局其他", "负其它": "客胜其他"}
    if value in label_map:
        return label_map[value], ""
    if ":" in value:
        try:
            home, away = (int(x) for x in value.split(":", 1))
        except ValueError:
            return "", ""
        outcome = "主胜" if home > away else ("平局" if home == away else "客胜")
        return f"{home}-{away}", outcome
    return "", ""


def _htft_label(value: str):
    names = {"3": "胜", "1": "平", "0": "负"}
    parts = str(value).split("-")
    if len(parts) != 2 or parts[0] not in names or parts[1] not in names:
        return ""
    return f"{names[parts[0]]}/{names[parts[1]]}"


def _analyze(match: dict):
    markets = match.get("markets") or {}
    one = _normalize(markets.get("one_x_two") or {})
    if not one:
        return None
    # 500.com 的 nspf 用 3/1/0 表示主胜/平/客胜
    p_home = one.get("3", 0.0)
    p_draw = one.get("1", 0.0)
    p_away = one.get("0", 0.0)
    total = p_home + p_draw + p_away
    if total <= 0:
        return None
    p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

    scores = []
    for value, prob in _normalize(markets.get("score") or {}).items():
        label, outcome = _score_label(value)
        if label:
            scores.append({"score": label, "prob": _pct(prob), "outcome": outcome})
    scores.sort(key=lambda x: x["prob"], reverse=True)

    htft = []
    for value, prob in _normalize(markets.get("htft") or {}).items():
        label = _htft_label(value)
        if label:
            htft.append({"combo": label, "prob": _pct(prob)})
    htft.sort(key=lambda x: x["prob"], reverse=True)

    total_market = _normalize(markets.get("total") or {})
    total_rows = []
    for value, prob in total_market.items():
        label = "7+" if value == "7" else value
        total_rows.append({"goals": int(value) if value.isdigit() else 7,
                           "label": label, "prob": prob})
    total_rows.sort(key=lambda x: x["goals"])
    ranges = [
        {"range": "0-1 球", "prob": _pct(sum(r["prob"] for r in total_rows if r["goals"] <= 1))},
        {"range": "2-3 球", "prob": _pct(sum(r["prob"] for r in total_rows if r["goals"] in (2, 3)))},
        {"range": "4+ 球", "prob": _pct(sum(r["prob"] for r in total_rows if r["goals"] >= 4))},
    ]
    ranges.sort(key=lambda x: x["prob"], reverse=True)
    expected = sum(r["goals"] * r["prob"] for r in total_rows)

    basis = (
        f"500.com 竞彩赔率隐含：主胜 {_pct(p_home)}%、平 {_pct(p_draw)}%、客胜 {_pct(p_away)}%。"
        + (f"总进球期望 {expected:.2f} 球。" if total_rows else "")
        + "比分、半全场、总进球都直接用对应玩法的真实赔率去抽水计算，不是另外编的模型数。"
    )
    return {
        "probabilities": {"home": _pct(p_home), "draw": _pct(p_draw), "away": _pct(p_away)},
        "expected_goals": round(expected, 2) if total_rows else None,
        "scores": scores[:5],
        "htft": htft[:4],
        "totals": ranges[:2],
        "basis": basis,
        "data_note": "数据来自 500.com 竞彩页面；未接入伤停、阵容和历史交锋。赔率会变化，实际以体彩官方为准。",
    }


def daily(day_text: str, config: dict | None = None) -> dict:
    config = config or config_mod.load()
    try:
        day = dt.date.fromisoformat(day_text)
    except (TypeError, ValueError) as exc:
        raise FootballError("日期格式应为 YYYY-MM-DD") from exc
    today = dt.date.today()
    if day < today - dt.timedelta(days=7) or day > today + dt.timedelta(days=14):
        raise FootballError("只能查询今天前后 7~14 天的比赛")

    cached = _read_cache(day_text, config)
    if cached and _age_seconds(cached) < CACHE_TTL_SECONDS:
        return {**cached, "cached": True, "stale": False}

    try:
        matches, warnings = _fetch_all(day_text)
    except Exception as exc:
        if cached:
            return {**cached, "cached": True, "stale": True,
                    "warning": f"{exc}；已使用本地缓存"}
        raise FootballError(f"获取 500.com 赛程失败：{exc}") from exc

    if not matches:
        raise FootballError(f"500.com 没有返回 {day_text} 的竞彩比赛")

    for match in matches:
        match["analysis"] = _analyze(match)
    matches.sort(key=lambda m: (m.get("date") or "", m.get("time") or "", m.get("matchnum") or ""))
    truncated = len(matches) > MAX_MATCHES
    matches = matches[:MAX_MATCHES]
    payload = {
        "ok": True,
        "version": CACHE_VERSION,
        "date": day_text,
        "source": BASE_URL,
        "source_name": "500.com 竞彩足球",
        "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "matches": matches,
        "truncated": truncated,
        "warning": "；".join(warnings),
    }
    _write_cache(day_text, config, payload)
    return {**payload, "cached": False, "stale": False}
