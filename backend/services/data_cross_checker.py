# -*- coding: utf-8 -*-
"""跨数据源交叉校验 — 定时检查指标偏差, 超阈值飞书告警.

检查项 (均 60 分钟一次):
  A. 涨跌幅对比: 抽20只票新浪 vs 东财, 偏差>1%告警
  B. 涨跌家数: 新浪快照 vs 腾讯板块聚合, 偏差>10%告警
  C. 数据覆盖率: 股票池行情新鲜度, 过期>20%告警
"""

import asyncio
import json
import logging
import random
import time
from datetime import datetime

import httpx

from backend.fetcher.http_client import HEADERS, EM_HEADERS, _get_client
from backend.models.repo._db import _fetchall
from backend.models.repo.stocks import list_all_stocks

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 3600  # 60 分钟
_last_run: float = 0.0
_alerted_today: dict[str, str] = {}  # check_name -> date_str, 同日不重复告警

# ── 告警阈值 ──
PCT_DEVIATION_THRESHOLD = 1.0       # 涨跌幅偏差 > 1%
BREADTH_DEVIATION_THRESHOLD = 10.0  # 涨跌比偏差 > 10%
COVERAGE_DROP_THRESHOLD = 20.0      # 覆盖率下降 > 20%
SAMPLE_SIZE = 20                    # 涨跌幅抽检样本数
QUOTE_STALE_SEC = 600               # 行情超过10分钟视为过期


async def _push(text: str, title: str, check_name: str) -> None:
    """推送告警, 同日同检查项仅一次."""
    today = datetime.now().strftime("%Y-%m-%d")
    if _alerted_today.get(check_name) == today:
        logger.info(f"[cross_check] {check_name} 今日已告警, 跳过")
        return
    _alerted_today[check_name] = today
    try:
        from backend.services import notifier
        await notifier.send_dual(text, lark_title=title, template="orange")
    except Exception as e:
        logger.warning(f"[cross_check] 推送失败: {e}")


# ═══════════════════════════════════════
# A. 涨跌幅对比: 新浪 vs 东财
# ═══════════════════════════════════════

def _code_to_sina(code: str) -> str:
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    return f"{prefix}{code}"


def _code_to_em(code: str) -> str:
    prefix = "1" if code.startswith(("6", "9")) else "0"
    return f"{prefix}.{code}"


async def _fetch_sina_pcts(codes: list[str]) -> dict[str, float]:
    """新浪实时行情 → {code: pct_change}."""
    result: dict[str, float] = {}
    sina_codes = [_code_to_sina(c) for c in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    client = _get_client()
    try:
        r = await client.get(url, headers=HEADERS)
        text = r.text
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            import re
            m = re.match(r'var hq_str_(\w+)="(.+)"', line)
            if not m:
                continue
            raw = m.group(2)
            parts = raw.split(",")
            if len(parts) < 4:
                continue
            code_6 = m.group(1)[2:]
            try:
                price = float(parts[3])        # 现价
                prev_close = float(parts[2])   # 昨收
                if prev_close > 0:
                    result[code_6] = (price / prev_close - 1) * 100
            except (ValueError, IndexError):
                continue
    except Exception as e:
        logger.warning(f"[cross_check] 新浪取数失败: {e}")
    return result


async def _fetch_em_pcts(codes: list[str]) -> dict[str, float]:
    """东财实时行情 → {code: pct_change}."""
    result: dict[str, float] = {}
    em_codes = [_code_to_em(c) for c in codes]
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get"
           f"?fltt=2&fields=f2,f3,f12&secids={','.join(em_codes)}")
    client = _get_client()
    try:
        r = await client.get(url, headers=EM_HEADERS)
        data = r.json()
        for item in data.get("data", {}).get("diff", []):
            code = item.get("f12", "")
            pct = item.get("f3")
            if code and pct is not None:
                result[code] = float(pct)
    except Exception as e:
        logger.warning(f"[cross_check] 东财取数失败: {e}")
    return result


async def _check_pct_deviation():
    """抽检涨跌幅偏差."""
    pool = await list_all_stocks()
    if not pool:
        return
    codes = [s["code"] for s in pool if s.get("code") and len(s["code"]) == 6]
    if len(codes) < 10:
        return
    sample = random.sample(codes, min(SAMPLE_SIZE, len(codes)))

    sina_pcts = await _fetch_sina_pcts(sample)
    em_pcts = await _fetch_em_pcts(sample)

    if not sina_pcts or not em_pcts:
        logger.warning(f"[cross_check] 涨跌幅取数不足: sina={len(sina_pcts)} em={len(em_pcts)}")
        return

    deviations = []
    for code in sample:
        sp = sina_pcts.get(code)
        ep = em_pcts.get(code)
        if sp is not None and ep is not None:
            deviations.append((code, abs(sp - ep), sp, ep))

    if not deviations:
        return

    avg_dev = sum(d[1] for d in deviations) / len(deviations)
    big = [d for d in deviations if d[1] > PCT_DEVIATION_THRESHOLD]

    logger.info(f"[cross_check] 涨跌幅抽检 {len(deviations)}只: 均偏差{avg_dev:.2f}%, "
                f">阈值{PCT_DEVIATION_THRESHOLD}%有{len(big)}只")

    if big:
        detail = "\n".join(f"  {d[0]}: 新浪{d[2]:+.2f}% vs 东财{d[3]:+.2f}% (差{d[1]:.2f}%)"
                          for d in sorted(big, key=lambda x: -x[1])[:8])
        await _push(
            f"⚠️ **涨跌幅数据源偏差**\n\n"
            f"抽检 {len(deviations)} 只, 平均偏差 {avg_dev:.2f}%\n"
            f"超过阈值({PCT_DEVIATION_THRESHOLD}%)的有 **{len(big)} 只**:\n\n{detail}\n\n"
            f"可能原因: 数据源时延 / 新浪已收盘东财仍在刷新 / 停牌股数据不一致",
            "⚠️ 涨跌幅数据源偏差", "pct")


# ═══════════════════════════════════════
# B. 涨跌家数对比
# ═══════════════════════════════════════

async def _fetch_sina_breadth() -> dict:
    """新浪全市场快照 → 涨跌家数."""
    from backend.services.market_risk_controller import _today_snapshot
    snap = await _today_snapshot()
    if len(snap) < 3000:
        return {}
    adv = sum(1 for v in snap.values() if v > 0)
    dec = sum(1 for v in snap.values() if v < 0)
    return {"advance_ratio": adv / max(adv + dec, 1) * 100, "total": len(snap)}


async def _fetch_tencent_breadth() -> dict | None:
    """腾讯行业榜聚合 → 近似涨跌比."""
    from backend.fetcher.sectors import _sector_ranking_tencent
    try:
        sectors = await _sector_ranking_tencent()
    except Exception as e:
        logger.warning(f"[cross_check] 腾讯板块取数失败: {e}")
        return None
    if not sectors:
        return None
    # 从板块 zgb (涨家比) 推算全市场近似涨跌比
    total_zg = 0; total_zb = 0
    for s in sectors:
        zgb = s.get("zgb", "")
        if "/" in zgb:
            try:
                up, total = zgb.split("/")
                total_zg += int(up)
                total_zb += int(total)
            except (ValueError, TypeError):
                continue
    if total_zb == 0:
        return None
    return {"advance_ratio": total_zg / total_zb * 100, "sectors": len(sectors)}


async def _check_breadth():
    """对比新浪 vs 腾讯涨跌比."""
    sina = await _fetch_sina_breadth()
    tencent = await _fetch_tencent_breadth()

    if not sina:
        logger.warning("[cross_check] 新浪涨跌家数取数失败")
        return
    if not tencent:
        logger.warning("[cross_check] 腾讯涨跌家数取数失败, 跳过对比")
        return

    diff = abs(sina["advance_ratio"] - tencent["advance_ratio"])
    logger.info(f"[cross_check] 涨跌家数: 新浪{sina['advance_ratio']:.1f}% vs "
                f"腾讯{tencent['advance_ratio']:.1f}% (差{diff:.1f}%)")

    if diff > BREADTH_DEVIATION_THRESHOLD:
        await _push(
            f"⚠️ **涨跌家数数据源偏差**\n\n"
            f"新浪: 涨跌比 **{sina['advance_ratio']:.1f}%** (全市场 {sina['total']} 只)\n"
            f"腾讯: 涨跌比 **{tencent['advance_ratio']:.1f}%** ({tencent['sectors']} 个行业板块聚合)\n"
            f"偏差: **{diff:.1f}%** (>{BREADTH_DEVIATION_THRESHOLD}%)\n\n"
            f"可能原因: 新浪快照不全 / 腾讯板块延迟 / 板块聚合口径差异",
            "⚠️ 涨跌家数数据源偏差", "breadth")


# ═══════════════════════════════════════
# C. 数据覆盖率
# ═══════════════════════════════════════

async def _check_coverage():
    """检查股票池行情新鲜度."""
    # 非交易时段 quote_refresher 本就只刷一次后早退(off-hours), 收盘后/周末整池必然全部
    # >600s "过期" —— 这是健康系统的预期状态, 不是限流/网络故障/卡死。只在交易时段才有意义。
    from backend.core.trading_calendar import is_trading_time
    if not is_trading_time():
        return
    rows = await _fetchall(
        "SELECT COUNT(*) as total, "
        "SUM(quote_updated_at > NOW() - INTERVAL %s SECOND) as fresh "
        "FROM cfzy_biz_stock_pool WHERE deleted_at IS NULL",
        (QUOTE_STALE_SEC,))
    if not rows:
        return
    r = rows[0]
    total = int(r["total"])
    fresh = int(r["fresh"])
    stale = total - fresh
    stale_pct = stale / max(total, 1) * 100

    logger.info(f"[cross_check] 覆盖率: {fresh}/{total} 新鲜 ({100-stale_pct:.1f}%), "
                f"{stale}只超{QUOTE_STALE_SEC}s未更新")

    if stale_pct > COVERAGE_DROP_THRESHOLD:
        await _push(
            f"⚠️ **行情数据覆盖率下降**\n\n"
            f"股票池 {total} 只, **{stale} 只 ({stale_pct:.0f}%)** 超过 {QUOTE_STALE_SEC}s 未更新\n"
            f"新鲜: {fresh} 只\n\n"
            f"可能原因: 新浪接口限流 / 网络故障 / quote_refresher 卡死",
            "⚠️ 行情覆盖率下降", "coverage")


# ═══════════════════════════════════════
# 定时入口
# ═══════════════════════════════════════

async def run_cross_check():
    """60 分钟一次: 三项检查并行."""
    global _last_run
    now = time.monotonic()
    if now - _last_run < _CHECK_INTERVAL - 60:
        return
    _last_run = now

    logger.info("[cross_check] 开始交叉校验...")
    await asyncio.gather(
        _check_pct_deviation(),
        _check_breadth(),
        _check_coverage(),
    )
    logger.info("[cross_check] 交叉校验完成")
