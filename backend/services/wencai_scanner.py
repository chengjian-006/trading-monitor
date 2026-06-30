# -*- coding: utf-8 -*-
"""问财候选榜扫描 — 同花顺问财(iwencai)自然语言选股 (v1.7.540).

频率(自节流, 入口 scan_wencai 注册为 interval/300s, 内部控制实际执行间隔):
  交易日 09:30-15:00 → 15分钟一轮(盘中候选随行情变, 定期刷新)
  交易日 15:00-15:30 → 收盘后补一轮(定格当日终值)
  其余时段 / 非交易日 → 跳过(候选保留上一轮结果)

对每条 config.wencai_screening.queries 里 enabled 的语句各跑一次 pywencai, 整行 UPSERT 进
cfzy_sys_wencai_pool(全局共享一份, 非按用户)。单条语句失败不影响其它语句, 只标 last_error。

IP 限流兜底: 同花顺对问财接口有反爬, 连续整轮失败按指数退避拉长下次尝试(期间不发请求),
连续失败达阈值飞书+企微告警(多为 Node/pywencai 环境问题或接口风控), 恢复发一条闭环。
默认 config.wencai_screening.enabled=False → 早返回不跑(部署机未装 Node/pywencai 时不刷告警)。
"""

import logging
import time
from datetime import datetime

from backend.core.config import load_config
from backend.core.trading_calendar import is_workday as _is_workday
from backend.fetcher.wencai_screener import fetch_wencai, WencaiFetchError
from backend.models import repository

logger = logging.getLogger(__name__)

_last_scan: float = 0.0

# 连续整轮失败兜底告警
_fail_count: int = 0
_fail_alerted: bool = False
_last_fail_alert_at: float = 0.0
FAIL_THRESHOLD = 3
FAIL_ALERT_COOLDOWN = 12 * 3600

# IP 限流退避: 整轮全失败时按指数拉长下次尝试间隔
_backoff_until: float = 0.0
BACKOFF_BASE = 300           # 首次失败退避 5min
BACKOFF_CAP = 7200           # 封顶 2h (5→10→20→40→80→120min)


def _get_interval_seconds() -> int:
    """据当前时段返回应间隔秒数; 返回 99999 表示当前不该跑。"""
    if not _is_workday():
        return 99999
    t = datetime.now().strftime("%H:%M")
    if "09:30" <= t < "15:00":
        return 900       # 盘中 15 分钟
    if "15:00" <= t <= "15:30":
        return 900       # 收盘后补一轮(配合 15min 间隔, 单独跑一次定格)
    return 99999         # 其余时段不跑


async def scan_wencai():
    cfg = load_config().get("wencai_screening", {})
    if not cfg.get("enabled", False):
        return

    queries = [q for q in (cfg.get("queries") or []) if q.get("enabled", True) and q.get("query")]
    if not queries:
        return
    limit = int(cfg.get("result_limit", 50))

    global _last_scan, _fail_count, _fail_alerted, _last_fail_alert_at, _backoff_until
    interval = _get_interval_seconds()
    if interval > 3600:
        return
    now = time.monotonic()
    if now - _last_scan < interval:
        return
    if now < _backoff_until:
        return  # 退避中: 跳过本轮、不发请求
    _last_scan = now

    from backend.services import notifier

    trade_date = datetime.now().strftime("%Y-%m-%d")
    ok_count = 0
    total_stocks = 0
    last_err = ""
    for q in queries:
        sid = q.get("id") or q.get("name") or ""
        sname = q.get("name") or sid
        qtext = q.get("query") or ""
        try:
            items = await fetch_wencai(qtext, limit=limit)
            await repository.upsert_wencai_strategy(sid, sname, qtext, trade_date, items)
            ok_count += 1
            total_stocks += len(items)
        except WencaiFetchError as e:
            last_err = f"{sname}: {e}"
            logger.warning(f"[wencai] 「{sname}」选股失败: {e}")
            try:
                await repository.set_wencai_error(sid, str(e))
            except Exception:
                pass

    if ok_count == 0:
        # 整轮全失败: 退避 + 兜底告警
        _fail_count += 1
        backoff = min(BACKOFF_BASE * (2 ** (_fail_count - 1)), BACKOFF_CAP)
        _backoff_until = time.monotonic() + backoff
        logger.warning(f"[wencai] 整轮全失败(连续 {_fail_count} 次) → 退避 {backoff // 60}min")
        if _fail_count >= FAIL_THRESHOLD and (time.time() - _last_fail_alert_at > FAIL_ALERT_COOLDOWN):
            _last_fail_alert_at = time.time()
            _fail_alerted = True
            await notifier.send_dual(
                f"⚠️ 问财候选榜刷新中断\n\n"
                f"连续 {_fail_count} 轮全部选股语句失败: {last_err}\n\n"
                f"常见原因: 部署机 Node/pywencai 异常, 或同花顺问财接口风控/token 失效。\n"
                f"排查: 在部署机跑 `python -c \"import pywencai;print(pywencai.get(question='非ST').shape)\"` 看报错。",
                lark_title="⚠️ 问财候选榜中断", template="red")
        return

    # 本轮至少一条成功: 重置失败计数; 若此前告过警补恢复通知
    if _fail_alerted:
        await notifier.send_dual(
            "✅ 问财候选榜刷新已恢复", lark_title="✅ 问财候选榜已恢复", template="green")
    _fail_count = 0
    _fail_alerted = False
    _backoff_until = 0.0

    logger.info(f"[wencai] 本轮 {ok_count}/{len(queries)} 条语句成功, 共 {total_stocks} 只候选")
