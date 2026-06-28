"""一次性回填全市场近5年日线到 cfzy_sys_kline_cache(断点续跑).

服务器运行:
  cd /opt/trading-monitor && PYTHONPATH=. venv/bin/python backend/scripts/backfill_fullmarket_klines.py
重复运行只补缺口(已≥1000根的票跳过). 预计 5400 只, 视网络十几到几十分钟.
"""
import asyncio

from backend.models.database import init_db
from backend.services.fullmarket_klines import backfill_full_market


async def main():
    await init_db()
    res = await backfill_full_market()
    print("backfill done:", res)


if __name__ == "__main__":
    asyncio.run(main())
