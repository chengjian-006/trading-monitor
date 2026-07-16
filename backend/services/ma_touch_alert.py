# -*- coding: utf-8 -*-
"""均线到线提醒·一次性订阅 (v1.7.x) — 推送卡「🔔 到线提醒」链接订阅, 到线推一次即失效。

个股买卖类信号卡底部挂 10/20/60日线三条签名订阅链接(push_pref.build_ma_alert_md,
kind=ma_alert_10/20/60, target=code, 落 cfzy_biz_push_pref, 有效期60天过期自动作废)。
本模块交易时段每60秒扫全部生效订阅:

  触发判定: 现价进入对应均线 ±0.3% 贴线带(TOUCH_BAND)。
  均线口径: 今日均线 = (最近 N-1 根历史收盘 + 今日现价)/N — 与 ma_break_watch 同口径,
            历史收盘走 kline_cache 批量查询(before=今日, 剔当日半根bar), 不读股票池列。
  防误触:   订阅后首次检查若已在带内不触发; 须先观察到"离带"(带外出现过)后再回触才算。
            状态存内存 dict(_armed, pref行id→已离带); 重启丢状态可接受 —— 重启后视同
            刚订阅重新走离带逻辑。
  触发动作: card_kit 情报蓝卡(标题/KPI三栏/👉/摘要/分时图链接) → notifier.send_card;
            不落信号库(不污染胜率); 发完撤销该订阅行(一次性), 同票其他均线订阅不受影响;
            发送失败不失效, 下轮重试。
"""
from __future__ import annotations

import logging
from datetime import time as _time

from backend.services.push_pref import MA_ALERT_KINDS

logger = logging.getLogger(__name__)

TOUCH_BAND = 0.003   # 贴线带: 现价距均线 ±0.3% 视为"到线"(与 near_buy 贴线带常量同档)

# 防误触状态(内存): 订阅行 id → 已观察到"离带"(在带外出现过)。触发/撤销/过期后清理。
_armed: dict[int, bool] = {}


def _in_window(now_t: _time) -> bool:
    """交易时段闸(学 sector_cocrash_guard 写法): 上午 09:30~11:30 + 下午 13:00~15:00。"""
    return (_time(9, 30) <= now_t <= _time(11, 30)) or (_time(13, 0) <= now_t <= _time(15, 0))


# ══════════════ 纯函数(可单测) ══════════════

def in_band(price: float, ma: float, band: float = TOUCH_BAND) -> bool:
    """现价是否进入均线 ±band 贴线带。非法输入(价/线≤0)一律 False。"""
    if price <= 0 or ma <= 0:
        return False
    return abs(price / ma - 1.0) <= band


def touch_verdict(pref_id: int, price: float, ma: float,
                  armed_map: dict[int, bool] | None = None,
                  band: float = TOUCH_BAND) -> bool:
    """一条订阅本轮的触发判定, 内置防误触状态机(副作用维护 armed_map):

      带外 → 记"已离带"(武装), 不触发;
      带内且已武装 → 触发(调用方负责发卡+失效);
      带内且未武装(订阅后首见即贴线/重启丢状态) → 不触发, 等它先离带。
    """
    m = _armed if armed_map is None else armed_map
    if not in_band(price, ma, band):
        m[pref_id] = True
        return False
    return bool(m.get(pref_id))


def build_touch_card(name: str, code: str, period: int, price: float, ma: float,
                     site: str = ""):
    """到线提醒卡(基线五区: KPI三栏结论 → 👉建议 → 折叠口径 → 分时图链接), 情报蓝卡。"""
    from urllib.parse import quote

    from backend.services import card_kit

    dist_pct = (price / ma - 1.0) * 100
    title = f"🔔 到线提醒 · {name}({code}) 触及{period}日线"
    elements = [
        card_kit.kpi_row([("现价", f"¥{price:.2f}"),
                          (f"{period}日线", f"¥{ma:.2f}"),
                          ("距离", f"{dist_pct:+.2f}%")]),
        card_kit.advice("你订的到线提醒到了"),
        card_kit.fold("口径说明", (
            f"均线口径：({period - 1}根历史收盘+今日现价)/{period}；贴线带 ±0.3%；"
            f"订阅时已贴线则等离线后再回触才算。本提醒为一次性订阅，发送后已自动失效"
            f"（同票其他均线订阅不受影响），需要可从新推送卡再订。")),
    ]
    site = (site or "").rstrip("/")
    link = f"{site}/intraday?code={code}&name={quote(name)}" if site else ""
    fallback = (f"🔔 到线提醒 · {name}({code}) 触及{period}日线\n"
                f"现价 ¥{price:.2f}　{period}日线 ¥{ma:.2f}（距离 {dist_pct:+.2f}%）\n"
                f"👉 你订的到线提醒到了（一次性，已自动失效）")
    return card_kit.Card(
        title=title, elements=elements, fallback=fallback, family="intel",
        summary=card_kit.summary_text(name, code, f"触及{period}日线", f"¥{price:.2f}"),
        subtitle="一次性提醒 · 发送后自动失效",
        link_url=link, link_text="查看分时图",
    )


# ══════════════ 编排(定时 interval 60s, 时段闸在此自判) ══════════════

async def run_ma_touch_alert():
    """交易时段每60秒: 拉全部生效的 ma_alert_* 订阅 → 现价+均线贴线判定(防误触) →
    触发发卡并撤销订阅行(一次性)。不落信号库。"""
    from datetime import date as _date
    from datetime import datetime

    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    if not _in_window(datetime.now().time()):
        return

    from backend.models.repo import push_pref as pp_repo
    try:
        subs = await pp_repo.active_prefs_of_kinds(list(MA_ALERT_KINDS))
    except Exception as e:
        logger.warning(f"[ma_touch_alert] 拉订阅失败: {e}")
        return
    if not subs:
        if _armed:
            _armed.clear()
        return
    # 清理已消失订阅(触发失效/手动撤销/60天过期)的防误触残留状态
    live_ids = {s["id"] for s in subs}
    for pid in [p for p in _armed if p not in live_ids]:
        _armed.pop(pid, None)

    codes = sorted({str(s.get("target") or "") for s in subs if s.get("target")})
    if not codes:
        return

    from backend import data_fetcher
    from backend.models import repository
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception as e:
        logger.warning(f"[ma_touch_alert] 取现价失败: {e}")
        return
    try:
        # 一次批量取最长周期(60)的历史收盘, 全部订阅共用; before=今日 剔当日半根bar
        closes_map = await repository.fetch_kline_close_batch(
            codes, max(MA_ALERT_KINDS.values()), before=_date.today().isoformat())
    except Exception as e:
        logger.warning(f"[ma_touch_alert] 取日K失败: {e}")
        return

    from backend.core.config import load_config
    from backend.services import notifier
    site = (load_config().get("site_url", "") or "").rstrip("/")

    for s in subs:
        code = str(s.get("target") or "")
        period = MA_ALERT_KINDS.get(s.get("kind") or "")
        if not code or not period:
            continue
        q = quotes.get(code) or {}
        price = float(q.get("price") or 0)
        closes = [float(x) for x in (closes_map.get(code) or []) if x and float(x) > 0]
        if price <= 0 or len(closes) < period - 1:
            continue     # 无行情/历史K线不足 → 本轮跳过, 不动状态
        # 今日均线 = (最近 period-1 根历史收盘 + 今日现价)/period (closes 最新在前)
        ma = (sum(closes[:period - 1]) + price) / period
        if not touch_verdict(s["id"], price, ma):
            continue
        name = q.get("name") or code
        card = build_touch_card(name, code, period, price, ma, site)
        try:
            sent = await notifier.send_card(card)
        except Exception as e:
            logger.warning(f"[ma_touch_alert] 推送异常({name} MA{period}): {e}")
            sent = False
        if not sent:
            continue     # 发送失败不失效(保持武装), 下轮重试
        # 一次性: 发完即撤销该订阅行; 同票其他均线订阅各有各的行, 不受影响
        try:
            await pp_repo.revoke(int(s.get("user_id") or 1), s["id"])
        except Exception as e:
            logger.warning(f"[ma_touch_alert] 订阅失效落库失败(pref={s['id']}): {e}")
        _armed.pop(s["id"], None)
        logger.info(f"[ma_touch_alert] 已提醒并失效: {name}({code}) 触及MA{period} "
                    f"现价{price:.2f} 线{ma:.2f}")
