# -*- coding: utf-8 -*-
"""一次性: 回踩买点显示名统一为完整话术 — 更新存量DB行 (v1.7.379).

代码侧名称已全改"回踩10MA缩量后突破昨高/回踩20MA缩量后突破昨高", 但两处存量行不会自愈:
  1. cfzy_biz_model_winrate.model_name — 每日17:30才重算, 期间推送战绩/图鉴胜率榜显示旧名
  2. cfzy_sys_scheduled_tasks — 种子是 INSERT IGNORE, 已有行的 name/description 不更新

跑法(服务器): cd /opt/trading-monitor && python3 -m backend.scripts.rename_model_display
"""
import asyncio

import aiomysql

from backend.core.config import load_config
from backend.models import database

R10 = "回踩10MA缩量后突破昨高"
R20 = "回踩20MA缩量后突破昨高"


async def _ensure_pool():
    cfg = load_config().get("database", {})
    database._pool = await aiomysql.create_pool(
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 3306),
        user=cfg.get("user", "root"),
        password=cfg.get("password", ""),
        db=cfg.get("db", "trading"),
        charset="utf8mb4",
        autocommit=True,
    )


async def main():
    await _ensure_pool()
    pool = database.get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            n1 = await cur.execute(
                "UPDATE cfzy_biz_model_winrate SET model_name=%s WHERE signal_id='BUY_RALLY_MA10'", (R10,))
            n2 = await cur.execute(
                "UPDATE cfzy_biz_model_winrate SET model_name=%s WHERE signal_id='BUY_RALLY_MA20'", (R20,))
            n3 = await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET name='回踩买点提醒·盘中', description=%s "
                "WHERE job_id='rally_reminder_tick'",
                (f"盘中每60秒: {R10}/{R20}触发即建跟踪持仓并推买入提醒; 持仓(T+1起)盘中触及+7%推止盈减半",))
            n4 = await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET name='回踩买点提醒·尾盘14:40' "
                "WHERE job_id='rally_reminder_eod'")
            n5 = await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET description=%s WHERE job_id='near_buy_refresh'",
                (f"每3分钟扫全自选+持仓, 算各票距四买点(弱势极限/{R10}/{R20}/强势起点)的接近度(触发/接近两档), "
                 "写 cfzy_sys_near_buy_snapshot, 供监控看板临近买点榜",))
            print(f"winrate: MA10={n1}行 MA20={n2}行 | tasks: tick={n3} eod={n4} near_buy={n5}")
    database._pool.close()
    await database._pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
