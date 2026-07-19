# -*- coding: utf-8 -*-
"""回测结论登记表 (v1.7.711) — 系统对外宣称的战绩数字的唯一出处.

要解决的问题
────────────
0719 全库扫描发现约 50 处硬编码战绩数字。它们的共同毛病是: 来自某次一次性分析,
写进代码后**与来源彻底断链** —— 源头脚本改了、样本过期了、甚至结论被证伪了, 代码里
的数字纹丝不动, 而且没有任何机制能发现。三个已确认的实例:
  · 推送里「回测期内信号胜率30%均值-3.6%」用了很久 —— 实为带前视偏差的旧回测产物,
    独立样本复核是 36.1%/-2.27%; 而它每天推给用户看。
  · docstring 写「GREEN 52%/+4.6%/PF2.20」—— 实测 40.6%/-0.49%/PF0.89。
  · 模型图鉴文案写「实测胜率74%」而同页实时表是 54% —— 同屏差 20 多个百分点。

体检系统能抓"数据陈不陈旧""接口通不通", 但抓不了"这句话还对不对"。本模块补这个洞。

用法
────
    # 写入(由重算任务或人工复核后调用)
    await upsert("risk_red_avg", value={"avg": -2.27, "pf": 0.62},
                 text="独立样本实测此档买点单笔均 -2.3%(正常档 -0.5%)",
                 src="bt_risk_baseline_redo.py", window="2021-01~2025-05",
                 kind="manual", ttl_days=180)

    # 读取(代码里只引用 key, 不写字面量)
    note = await text_of("risk_red_avg", fallback="...")

kind 的两类:
  auto   有数据源可定时重算(如模型胜率走 cfzy_biz_model_winrate), 永不过期
  manual 一次性研究结论, 无法自动重算 → 到期由体检项 claim_stale 提醒人工复验

为什么大盘风控三档战绩登记为 manual 而不是建每周重算任务(2026-07-19 决策留档)
──────────────────────────────────────────────────────────────────────
这几条数字出自 backend/scripts/bt_risk_baseline_redo.py。做成生产定时任务会撞上三个
实打实的障碍, 权衡后判断不值得:
  1. **代码不在仓库内**: .gitignore 有 `backend/scripts/bt_*.py`, 生产环境根本没有这些
     脚本。要建任务就得把约 400 行回测代码搬进 backend/services/ 并长期维护。
  2. **任务很重**: 需把 609 万行日线载入内存(约210MB)再对 5000 只逐票跑指标与检测器,
     本机实测数十分钟。放进生产等于每周一次的重负载, 而它与实盘链路无关。
  3. **收益有限**: 这是 5 年 OOS 的慢变统计量, 不是天天会变的东西; 180 天到期由体检
     自动提醒复验已足够。
真正需要"每天更新"的是模型胜率 —— 它本来就有 cfzy_biz_model_winrate 每晚 21:00 重算,
v1.7.708 已把模型图鉴文案接上去了。两者性质不同, 不该用同一套频率。
若将来确实要自动化, 正确做法是先把计算逻辑产品化进 services 并瘦身样本区间, 而不是
把一次性研究脚本直接搬上生产。
"""

import json
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_CACHE_TTL = 300.0
_cache: dict = {"at": 0.0, "rows": {}}


async def _load() -> dict:
    """全表载入 + 5 分钟进程缓存(表很小, 几十行; 读多写少)。"""
    now = time.monotonic()
    if now - _cache["at"] < _CACHE_TTL and _cache["rows"]:
        return _cache["rows"]
    from backend.models.repo._db import _fetchall
    try:
        rows = await _fetchall("SELECT * FROM cfzy_sys_backtest_claims")
    except Exception as e:
        logger.warning(f"[claims] 读取失败, 用上次缓存: {e}")
        return _cache["rows"]
    out = {str(r["claim_key"]): r for r in rows}
    _cache["at"], _cache["rows"] = now, out
    return out


def invalidate() -> None:
    _cache["at"] = 0.0


async def get(key: str) -> dict | None:
    return (await _load()).get(key)


async def text_of(key: str, fallback: str = "") -> str:
    """取渲染好的文案片段; 查不到回退 —— **绝不能因为登记表缺条目就让推送变哑**。"""
    r = await get(key)
    return str(r["text"]) if r and r.get("text") else fallback


async def value_of(key: str, default=None):
    r = await get(key)
    if not r or not r.get("value_json"):
        return default
    try:
        return json.loads(r["value_json"])
    except (json.JSONDecodeError, TypeError):
        return default


async def upsert(key: str, *, text: str, src: str, window: str,
                 value=None, kind: str = "manual", ttl_days: int = 180,
                 computed_at: datetime | None = None) -> None:
    from backend.models.repo._db import _execute
    await _execute(
        "INSERT INTO cfzy_sys_backtest_claims "
        "(claim_key, value_json, text, src, window_desc, kind, ttl_days, computed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE value_json=VALUES(value_json), text=VALUES(text), "
        "src=VALUES(src), window_desc=VALUES(window_desc), kind=VALUES(kind), "
        "ttl_days=VALUES(ttl_days), computed_at=VALUES(computed_at)",
        (key, json.dumps(value, ensure_ascii=False) if value is not None else None,
         text[:500], src[:120], window[:64], kind, int(ttl_days),
         computed_at or datetime.now()))
    invalidate()


async def stale_claims() -> list[dict]:
    """超过各自 ttl_days 未重算/未复核的结论。auto 型也纳入 —— 它超期说明重算任务挂了。"""
    from backend.models.repo._db import _fetchall
    return await _fetchall(
        "SELECT claim_key, kind, src, window_desc, computed_at, ttl_days, "
        "       DATEDIFF(NOW(), computed_at) AS age_days "
        "FROM cfzy_sys_backtest_claims "
        "WHERE DATEDIFF(NOW(), computed_at) > ttl_days "
        "ORDER BY age_days DESC")


async def all_claims() -> list[dict]:
    from backend.models.repo._db import _fetchall
    return await _fetchall(
        "SELECT claim_key, kind, src, window_desc, computed_at, ttl_days, text "
        "FROM cfzy_sys_backtest_claims ORDER BY kind, claim_key")
