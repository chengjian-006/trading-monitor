# -*- coding: utf-8 -*-
"""「分时二波过前高」实时提醒扫描器 (v1.7.597) — run_second_surge_scan。

盘中每 ~30s 一轮: 纯读分时预热缓存(sparkline_prefetcher 每25s焐热的 _batch_intraday_cache,
零新增上游请求), 对全自选池逐票跑 second_surge.detect_second_surge, 命中即实时提醒。

范围: 全池·在池即扫(concept指数含; ST 剔除, 与其它右侧信号一致)。每股每天首次触发报一次
(内存去重, 跨日重置; 重启清空可接受, 同持仓异动/尾盘警戒)。只提醒不落信号库(不污染胜率)。
静音: surge_snooze(target=code, 逐票"当日/本周不提醒"), 独立于买卖点/异动推送。

设计与回测背书见记忆 second-surge-backtest。参数默认 second_surge.DEFAULT_PARAMS,
可被 config.json 的 "second_surge" 段覆盖(免改代码调参)。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 进程内每日去重: {date_str: set(code)}。跨日自动切换(只留今日一份)。
_fired_today: dict[str, set] = {}


def _mark_fired(day: str, code: str):
    if day not in _fired_today:
        _fired_today.clear()          # 跨日: 丢弃昨日, 只留今日
        _fired_today[day] = set()
    _fired_today[day].add(code)


def _already_fired(day: str, code: str) -> bool:
    return code in _fired_today.get(day, set())


def _params() -> dict:
    """默认参数 + config.json "second_surge" 段覆盖。"""
    from backend.core.config import load_config
    from backend.services.second_surge import DEFAULT_PARAMS
    p = dict(DEFAULT_PARAMS)
    try:
        p.update(load_config().get("second_surge") or {})
    except Exception:
        pass
    return p


async def run_second_surge_scan():
    """盘中一轮二波过前高扫描。窗口外/未开盘/未到 start_minute 直接返回。"""
    from backend.core.trading_calendar import is_workday
    now = datetime.now()
    if not is_workday(now):
        return
    p = _params()
    if not p.get("enabled", True):
        return
    cur_min = now.hour * 60 + now.minute
    if cur_min < int(p.get("start_minute", 585)) or cur_min >= 15 * 60:   # 09:45~15:00
        return

    from backend.models import repository
    from backend.models.repo import push_pref as pp_repo
    from backend.services import push_pref as pp
    from backend.services import second_surge as ss
    from backend.services import notifier
    from backend.core.config import load_config
    from backend.fetcher.intraday import get_batch_intraday_sparkline

    try:
        stocks = await repository.list_all_stocks()
    except Exception as e:
        logger.warning(f"[second_surge] 取股票池失败: {e}")
        return
    # 全池·在池即扫: 剔ST; 概念指数按配置(默认含)
    include_index = bool(p.get("include_index", True))
    name_map: dict[str, str] = {}
    codes: list[str] = []
    for s in stocks:
        code = s.get("code")
        if not code:
            continue
        name = s.get("name") or code
        if "ST" in name.upper():
            continue
        if not include_index and s.get("trade_type") == "index":
            continue
        name_map[code] = name
        codes.append(code)
    codes = sorted(set(codes))
    if not codes:
        return

    day = now.strftime("%Y-%m-%d")
    codes = [c for c in codes if not _already_fired(day, c)]     # 今日已报的跳过(省算)
    if not codes:
        return

    try:
        cache = await get_batch_intraday_sparkline(codes)       # 纯读缓存(预热焐热), 极少miss才拉
    except Exception as e:
        logger.warning(f"[second_surge] 取分时缓存失败: {e}")
        return
    try:
        prefs = await pp_repo.active_prefs(1)
    except Exception:
        prefs = []

    site = (load_config().get("site_url", "") or "").rstrip("/")
    min_amt = float(p.get("min_amount_now", 50_000_000))
    hits: list[dict] = []
    for code in codes:
        if pp.surge_snooze_active(prefs, code):
            continue
        entry = cache.get(code)
        if not entry:
            continue
        trends = entry.get("trends") or []
        pre_close = float(entry.get("pre_close") or 0)
        r = ss.detect_second_surge(trends, pre_close, p, code=code, name=name_map.get(code, ""))
        if not r:
            continue
        if ss.cum_amount(trends) < min_amt:                     # 流动性底线(当日累计成交额)
            continue
        _mark_fired(day, code)                                  # 每股每天一次(命中即登记)
        hits.append({
            "name": name_map.get(code, code), "code": code, "r": r,
            "action_md": pp.build_surge_actions_md(site, 1, code) if site else "",
        })

    if not hits:
        return
    # 同tick多只按二波放量倍数降序(更猛的在前)
    hits.sort(key=lambda h: h["r"].get("vol_mult", 0), reverse=True)
    title, body = ss.build_surge_card(hits)
    try:
        await notifier.send_dual(body, lark_title=title, template="red")
    except Exception as e:
        logger.warning(f"[second_surge] 推送失败: {e}")
    logger.info(f"[second_surge] 本轮命中{len(hits)}只: {[h['code'] for h in hits]}")
