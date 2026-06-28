# -*- coding: utf-8 -*-
"""导出已入库的博主帖子原文 (只读).

跑法:
    set PYTHONIOENCODING=utf-8
    py -3 -m backend.scripts.diag_blogger_dump
"""
import asyncio

import aiomysql

from backend.core.config import load_config
from backend.models import database
from backend.models.repo._db import _fetchall


async def _ensure_pool():
    cfg = load_config().get("database", {})
    database._pool = await aiomysql.create_pool(
        host=cfg.get("host", "127.0.0.1"), port=cfg.get("port", 3306),
        user=cfg.get("user", "root"), password=cfg.get("password", ""),
        db=cfg.get("db", "trading"), charset="utf8mb4",
        autocommit=True, minsize=1, maxsize=4,
    )
    return cfg


async def main():
    cfg = await _ensure_pool()
    print(f"[diag] 连库 {cfg.get('host')}/{cfg.get('db')}")
    try:
        rows = await _fetchall(
            "SELECT id, blogger_name, post_id, posted_at, content, stock_codes, url "
            "FROM cfzy_biz_blogger_posts ORDER BY posted_at ASC, id ASC", ()
        )
        print(f"[diag] 共 {len(rows)} 条帖子\n")
        for r in rows:
            print("=" * 90)
            print(f"#{r['id']}  {r['posted_at']}  post_id={r['post_id']}  个股标签={r['stock_codes']}")
            print("-" * 90)
            print(r["content"])
            print()
    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
