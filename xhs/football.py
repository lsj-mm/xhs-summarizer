# -*- coding: utf-8 -*-
"""竞彩足球参考分析（ESPN 公开赛程/赔率 + 泊松模型）。

边界：
    - 数据来自 ESPN 公开 scoreboard 接口，不是中国竞彩官方赛程；
    - 只有胜平负和大小球赔率，比分、半全场、总进球由泊松模型推算；
    - 没有伤停、阵容、历史交锋数据，页面会明确标注；
    - 结果只做娱乐参考，不构成投注建议。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
from pathlib import Path

import requests

from . import config as config_mod

LOG = logging.getLogger(__name__)

SOURCE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"
CACHE_VERSION = 2
CACHE_TTL_SECONDS = 10 * 60
MAX_MATCHES = 24
MAX_GOALS = 8

# 中文名是人工维护的常用译名；没收录的球队保留英文名，避免乱翻译。
TEAM_ZH = {
    "Brisbane Roar FC": "布里斯班狮吼",
    "Perth Glory FC": "珀斯光荣",
    "Inter Kashi": "国际卡希",
    "East Bengal FC": "东孟加拉",
    "Al Faisaly": "费萨里",
    "Al Nassr": "利雅得胜利",
    "Al Ittihad": "吉达联合",
    "Al Diriyah": "迪里耶",
    "Petro de Luanda": "罗安达石油",
    "Scottland Mabvuku": "斯科特兰马布武库",
    "Arsenal": "阿森纳",
    "Aston Villa": "阿斯顿维拉",
    "Bournemouth": "伯恩茅斯",
    "Brentford": "布伦特福德",
    "Brighton & Hove Albion": "布莱顿",
    "Chelsea": "切尔西",
    "Crystal Palace": "水晶宫",
    "Everton": "埃弗顿",
    "Fulham": "富勒姆",
    "Ipswich Town": "伊普斯维奇",
    "Leicester City": "莱斯特城",
    "Liverpool": "利物浦",
    "Manchester City": "曼城",
    "Manchester United": "曼联",
    "Newcastle United": "纽卡斯尔联",
    "Nottingham Forest": "诺丁汉森林",
    "Southampton": "南安普顿",
    "Tottenham Hotspur": "托特纳姆热刺",
    "West Ham United": "西汉姆联",
    "Wolverhampton Wanderers": "狼队",
    "Real Madrid": "皇家马德里",
    "Barcelona": "巴塞罗那",
    "Atletico Madrid": "马德里竞技",
    "Sevilla": "塞维利亚",
    "Real Sociedad": "皇家社会",
    "Athletic Club": "毕尔巴鄂竞技",
    "Villarreal": "比利亚雷亚尔",
    "Valencia": "瓦伦西亚",
    "Real Betis": "皇家贝蒂斯",
    "Alaves": "阿拉维斯",
    "Malaga": "马拉加",
    "Girona": "赫罗纳",
    "Celta Vigo": "塞尔塔",
    "Getafe": "赫塔费",
    "Osasuna": "奥萨苏纳",
    "Mallorca": "马略卡",
    "Rayo Vallecano": "巴列卡诺",
    "Espanyol": "西班牙人",
    "Bayern Munich": "拜仁慕尼黑",
    "Borussia Dortmund": "多特蒙德",
    "RB Leipzig": "莱比锡",
    "Bayer Leverkusen": "勒沃库森",
    "Eintracht Frankfurt": "法兰克福",
    "VfB Stuttgart": "斯图加特",
    "Wolfsburg": "沃尔夫斯堡",
    "Union Berlin": "柏林联合",
    "Freiburg": "弗赖堡",
    "Mainz": "美因茨",
    "Werder Bremen": "云达不莱梅",
    "Hoffenheim": "霍芬海姆",
    "Augsburg": "奥格斯堡",
    "Borussia Monchengladbach": "门兴格拉德巴赫",
    "Juventus": "尤文图斯",
    "Inter Milan": "国际米兰",
    "AC Milan": "AC米兰",
    "Napoli": "那不勒斯",
    "Roma": "罗马",
    "Lazio": "拉齐奥",
    "Atalanta": "亚特兰大",
    "Fiorentina": "佛罗伦萨",
    "Bologna": "博洛尼亚",
    "Torino": "都灵",
    "Udinese": "乌迪内斯",
    "Genoa": "热那亚",
    "Sassuolo": "萨索洛",
    "Parma": "帕尔马",
    "Lecce": "莱切",
    "Cagliari": "卡利亚里",
    "Verona": "维罗纳",
    "Como": "科莫",
    "Paris Saint-Germain": "巴黎圣日耳曼",
    "Marseille": "马赛",
    "Lyon": "里昂",
    "Monaco": "摩纳哥",
    "Lille": "里尔",
    "Nice": "尼斯",
    "Lens": "朗斯",
    "Rennes": "雷恩",
    "Strasbourg": "斯特拉斯堡",
    "Nantes": "南特",
    "Brest": "布雷斯特",
    "Toulouse": "图卢兹",
    "Montpellier": "蒙彼利埃",
    "Benfica": "本菲卡",
    "Porto": "波尔图",
    "Sporting CP": "葡萄牙体育",
    "Ajax": "阿贾克斯",
    "PSV Eindhoven": "埃因霍温",
    "Feyenoord": "费耶诺德",
    "Celtic": "凯尔特人",
    "Rangers": "流浪者",
    "Galatasaray": "加拉塔萨雷",
    "Fenerbahce": "费内巴切",
    "Besiktas": "贝西克塔斯",
    "Olympiacos": "奥林匹亚科斯",
    "Panathinaikos": "帕纳辛奈科斯",
    "Shakhtar Donetsk": "顿涅茨克矿工",
    "Dynamo Kyiv": "基辅迪纳摩",
}

COUNTRY_ZH = {
    "Nigeria": "尼日利亚", "North Korea": "朝鲜", "Puerto Rico": "波多黎各",
    "Poland": "波兰", "England": "英格兰", "France": "法国", "Germany": "德国",
    "Spain": "西班牙", "Italy": "意大利", "Portugal": "葡萄牙", "Netherlands": "荷兰",
    "Brazil": "巴西", "Argentina": "阿根廷", "Japan": "日本", "South Korea": "韩国",
    "China": "中国", "Australia": "澳大利亚", "Mexico": "墨西哥", "USA": "美国",
}

LEAGUE_ZH = {
    "A-League Men": "澳超",
    "Indian Super League": "印度超",
    "Saudi Pro League": "沙特联",
    "CAF Champions League, Second Preliminary Round": "非冠杯资格赛第二轮",
    "FIFA U-17 Women's World Cup, Group B": "U17 女足世界杯 B 组",
    "English Premier League": "英超",
    "LALIGA": "西甲",
    "Serie A": "意甲",
    "Bundesliga": "德甲",
    "Ligue 1": "法甲",
    "UEFA Champions League": "欧冠",
    "UEFA Europa League": "欧联",
    "UEFA Conference League": "欧协联",
    "FIFA World Cup": "世界杯",
    "FIFA Club World Cup": "世俱杯",
    "Copa Libertadores": "解放者杯",
    "Copa Sudamericana": "南美杯",
    "Major League Soccer": "美职联",
    "Liga MX": "墨超",
    "J1 League": "日职联",
    "K League 1": "韩K联",
    "Chinese Super League": "中超",
}


def zh_team(name: str) -> str:
    text = str(name or "").strip()
    if text in TEAM_ZH:
        return TEAM_ZH[text]
    match = re.match(r"^(.*?)\s+U(\d+)$", text)
    if match and match.group(1) in COUNTRY_ZH:
        return COUNTRY_ZH[match.group(1)] + "U" + match.group(2)
    return ""


def zh_league(name: str) -> str:
    text = str(name or "").strip()
    if text in LEAGUE_ZH:
        return LEAGUE_ZH[text]
    # 常见格式「联赛, 阶段」：只翻译联赛部分
    if "," in text:
        base = text.split(",", 1)[0].strip()
        if base in LEAGUE_ZH:
            return LEAGUE_ZH[base] + " · " + text.split(",", 1)[1].strip()
    return ""


class FootballError(RuntimeError):
    """可直接展示给用户的错误。"""


def _cache_path(day: str, config: dict) -> Path:
    return config_mod.workdir(config) / "football" / f"{day}.json"


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


def _decimal_odds(value):
    """把美式赔率（+210 / -155）转成小数赔率；无法解析返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace("−", "-")
    try:
        num = float(text)
    except ValueError:
        return None
    if num == 0:
        return None
    return 1.0 + (num / 100.0 if num > 0 else 100.0 / abs(num))


def _implied(value):
    dec = _decimal_odds(value)
    return (1.0 / dec) if dec else None


def _parse_moneyline(odds: dict):
    ml = odds.get("moneyline") or {}
    out = {}
    for side in ("home", "draw", "away"):
        item = ml.get(side) or {}
        close = (item.get("close") or {}).get("odds")
        open_ = (item.get("open") or {}).get("odds")
        out[side] = close if close is not None else open_
    return out


def _parse_total(odds: dict):
    total = odds.get("total") or {}
    over = ((total.get("over") or {}).get("close") or {})
    under = ((total.get("under") or {}).get("close") or {})
    line_text = str(over.get("line") or under.get("line") or "")
    try:
        line = float(line_text.lstrip("oOuU"))
    except ValueError:
        line = 2.5
    return {
        "line": line,
        "over_odds": over.get("odds"),
        "under_odds": under.get("odds"),
    }


def _parse_event(event: dict, local_day: dt.date):
    comp = (event.get("competitions") or [{}])[0]
    date_text = event.get("date") or comp.get("date")
    if not date_text:
        return None
    try:
        kickoff = dt.datetime.fromisoformat(str(date_text).replace("Z", "+00:00"))
    except ValueError:
        return None
    local = kickoff.astimezone()
    if local.date() != local_day:
        return None
    status = ((comp.get("status") or {}).get("type") or {})
    if status.get("state") not in (None, "pre"):
        return None

    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    odds_list = comp.get("odds") or []
    odds = next((o for o in odds_list if isinstance(o, dict)), None)
    parsed_odds = None
    if odds:
        ml = _parse_moneyline(odds)
        total = _parse_total(odds)
        if all(ml.get(k) is not None for k in ("home", "draw", "away")):
            parsed_odds = {
                "home": ml["home"], "draw": ml["draw"], "away": ml["away"],
                "total_line": total["line"],
                "over": total["over_odds"], "under": total["under_odds"],
                "provider": ((odds.get("provider") or {}).get("name") or "ESPN"),
            }

    def team_info(item):
        team = item.get("team") or {}
        record = next((r.get("summary") for r in (item.get("records") or []) if r.get("summary")), "")
        name = team.get("displayName") or team.get("name") or ""
        return {
            "name": name,
            "name_zh": zh_team(name),
            "short": team.get("abbreviation") or "",
            "logo": team.get("logo") or "",
            "form": str(item.get("form") or ""),
            "record": record,
        }

    league = comp.get("altGameNote") or (event.get("season") or {}).get("slug") or ""
    return {
        "id": str(event.get("id") or ""),
        "league": league,
        "league_zh": zh_league(league),
        "kickoff": local.isoformat(timespec="minutes"),
        "kickoff_utc": kickoff.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
        "home": team_info(home),
        "away": team_info(away),
        "odds": parsed_odds,
    }


def _fetch_day(day: dt.date, config: dict):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }
    session = requests.Session()
    session.trust_env = False
    results = []
    for offset in (-1, 0):
        query_day = day + dt.timedelta(days=offset)
        url = f"{SOURCE_URL}?dates={query_day.strftime('%Y%m%d')}"
        try:
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            LOG.warning("ESPN 赛程请求失败 %s：%s", query_day, exc)
            continue
        for event in payload.get("events") or []:
            parsed = _parse_event(event, day)
            if parsed:
                results.append(parsed)
    # 同一场比赛可能同时出现在两个 UTC 日期的响应里
    unique = {}
    for match in results:
        unique[match["id"]] = match
    return list(unique.values())


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _score_matrix(lh: float, la: float):
    home = [_poisson_pmf(i, lh) for i in range(MAX_GOALS + 1)]
    away = [_poisson_pmf(i, la) for i in range(MAX_GOALS + 1)]
    matrix = [[home[i] * away[j] for j in range(MAX_GOALS + 1)]
              for i in range(MAX_GOALS + 1)]
    # 把 8 球以上的概率归到 8 球，避免尾部丢失
    total = sum(sum(row) for row in matrix)
    if total > 0:
        matrix[MAX_GOALS][MAX_GOALS] += max(0.0, 1.0 - total)
    return matrix


def _outcomes(matrix):
    home = draw = away = 0.0
    for i, row in enumerate(matrix):
        for j, prob in enumerate(row):
            if i > j:
                home += prob
            elif i == j:
                draw += prob
            else:
                away += prob
    total = home + draw + away
    if total <= 0:
        return 0.0, 0.0, 0.0
    return home / total, draw / total, away / total


def _over_prob(matrix, line: float):
    return sum(prob for i, row in enumerate(matrix)
               for j, prob in enumerate(row) if i + j > line)


def _fit_lambdas(p_home: float, p_draw: float, p_away: float,
                 p_over: float | None, line: float):
    best = (float("inf"), 1.35, 1.15)
    for lh in [x / 20 for x in range(4, 69)]:  # 0.20 ~ 3.40
        for la in [x / 20 for x in range(4, 69)]:
            matrix = _score_matrix(lh, la)
            h, d, a = _outcomes(matrix)
            error = (h - p_home) ** 2 + (d - p_draw) ** 2 + (a - p_away) ** 2
            if p_over is not None:
                error += (_over_prob(matrix, line) - p_over) ** 2
            else:
                # 没有大小球时，让总进球靠近联赛常见值，避免无穷解
                error += 0.01 * (lh + la - 2.6) ** 2
            if error < best[0]:
                best = (error, lh, la)
    _, lh0, la0 = best
    for lh in [lh0 + x / 100 for x in range(-8, 9)]:
        if lh <= 0.05:
            continue
        for la in [la0 + x / 100 for x in range(-8, 9)]:
            if la <= 0.05:
                continue
            matrix = _score_matrix(lh, la)
            h, d, a = _outcomes(matrix)
            error = (h - p_home) ** 2 + (d - p_draw) ** 2 + (a - p_away) ** 2
            if p_over is not None:
                error += (_over_prob(matrix, line) - p_over) ** 2
            if error < best[0]:
                best = (error, lh, la)
    return best[1], best[2]


def _pct(value: float) -> float:
    return round(value * 100, 1)


def _analyze(match: dict):
    odds = match.get("odds")
    if not odds:
        return None
    home = _implied(odds.get("home"))
    draw = _implied(odds.get("draw"))
    away = _implied(odds.get("away"))
    if home is None or draw is None or away is None:
        return None
    # 去掉博彩公司抽水，得到归一化市场概率
    total = home + draw + away
    p_home, p_draw, p_away = home / total, draw / total, away / total

    over = _implied(odds.get("over"))
    under = _implied(odds.get("under"))
    p_over = None
    line = float(odds.get("total_line") or 2.5)
    if over is not None and under is not None and over + under > 0:
        p_over = over / (over + under)

    lh, la = _fit_lambdas(p_home, p_draw, p_away, p_over, line)
    matrix = _score_matrix(lh, la)
    h, d, a = _outcomes(matrix)

    score_rows = []
    for i in range(0, 6):
        for j in range(0, 6):
            prob = matrix[i][j]
            if prob <= 0:
                continue
            outcome = "主胜" if i > j else ("平局" if i == j else "客胜")
            score_rows.append({"score": f"{i}-{j}", "prob": _pct(prob), "outcome": outcome})
    score_rows.sort(key=lambda x: x["prob"], reverse=True)

    # 半场按 45% 进球、下半场 55% 拆分，分别独立算泊松
    ht_matrix = _score_matrix(lh * 0.45, la * 0.45)
    sh_matrix = _score_matrix(lh * 0.55, la * 0.55)
    labels = ["主胜", "平局", "客胜"]
    htft = []
    for hi in range(3):
        for fi in range(3):
            prob = 0.0
            for i in range(MAX_GOALS + 1):
                for j in range(MAX_GOALS + 1):
                    ht_dir = 0 if i > j else (1 if i == j else 2)
                    if ht_dir != hi:
                        continue
                    ht_p = ht_matrix[i][j]
                    if ht_p <= 0:
                        continue
                    for k in range(MAX_GOALS + 1):
                        for l in range(MAX_GOALS + 1):
                            final_i, final_j = i + k, j + l
                            final_dir = 0 if final_i > final_j else (1 if final_i == final_j else 2)
                            if final_dir != fi:
                                continue
                            prob += ht_p * sh_matrix[k][l]
            if prob > 0:
                htft.append({
                    "combo": f"{labels[hi]}/{labels[fi]}",
                    "prob": _pct(prob),
                })
    htft.sort(key=lambda x: x["prob"], reverse=True)

    total_rows = []
    for goals in range(0, MAX_GOALS + 1):
        prob = sum(matrix[i][goals - i] for i in range(0, goals + 1)
                   if 0 <= goals - i <= MAX_GOALS)
        total_rows.append({"goals": goals, "prob": _pct(prob)})
    ranges = [
        {"range": "0-1 球", "prob": _pct(sum(r["prob"] for r in total_rows if r["goals"] <= 1) / 100)},
        {"range": "2-3 球", "prob": _pct(sum(r["prob"] for r in total_rows if r["goals"] in (2, 3)) / 100)},
        {"range": "4+ 球", "prob": _pct(sum(r["prob"] for r in total_rows if r["goals"] >= 4) / 100)},
    ]
    ranges.sort(key=lambda x: x["prob"], reverse=True)

    home_name = match["home"].get("name_zh") or match["home"]["name"]
    away_name = match["away"].get("name_zh") or match["away"]["name"]
    basis = (
        f"胜平负赔率隐含：主胜 {_pct(p_home)}%、平 {_pct(p_draw)}%、客胜 {_pct(p_away)}%；"
        f"大小球 {line:g} 大球 {_pct(p_over) if p_over is not None else '—'}%。"
        f"模型拟合期望进球 {lh:.2f}-{la:.2f}。"
        f"近期战绩：{home_name} {match['home']['form'] or '—'}，"
        f"{away_name} {match['away']['form'] or '—'}。"
    )
    return {
        "probabilities": {"home": _pct(h), "draw": _pct(d), "away": _pct(a)},
        "expected_goals": {"home": round(lh, 2), "away": round(la, 2)},
        "scores": score_rows[:5],
        "htft": htft[:4],
        "totals": ranges[:2],
        "basis": basis,
        "data_note": "仅使用赛前胜平负/大小球赔率与近期战绩；未接入伤停、阵容、历史交锋。比分、半全场、总进球为泊松模型推算，不是官方赔率。",
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
        return {**cached, "cached": True, "stale": False, "warning": ""}

    try:
        matches = _fetch_day(day, config)
    except Exception as exc:
        if cached:
            return {**cached, "cached": True, "stale": True,
                    "warning": f"{exc}；已使用本地缓存"}
        raise FootballError(f"获取赛程失败：{exc}") from exc

    analyzed = []
    for match in matches:
        analysis = _analyze(match)
        match["analysis"] = analysis
        analyzed.append(match)
    analyzed.sort(key=lambda m: (m.get("odds") is None, m.get("kickoff") or ""))
    truncated = len(analyzed) > MAX_MATCHES
    analyzed = analyzed[:MAX_MATCHES]
    payload = {
        "ok": True,
        "version": CACHE_VERSION,
        "date": day_text,
        "source": SOURCE_URL,
        "source_name": "ESPN 公开赛程/赔率",
        "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "matches": analyzed,
        "truncated": truncated,
        "warning": "",
    }
    _write_cache(day_text, config, payload)
    return {**payload, "cached": False, "stale": False}
