# -*- coding: utf-8 -*-
"""SERVER-SIDE: fetch blogger posts with server cookie, save to DB, NO Feishu push.
ASCII-only stdout to survive ssh transport. Run on server:
    cd /opt/trading-monitor && python3 -m backend.scripts.diag_blogger_pull_save
"""
import asyncio

import aiomysql

from backend.core.config import load_config
from backend.models import database
from backend.fetcher.ths_blogger import fetch_blogger_posts, BloggerFetchError
from backend.models import repository


async def main():
    cfg = load_config()
    dbc = cfg.get("database", {})
    database._pool = await aiomysql.create_pool(
        host=dbc.get("host", "127.0.0.1"), port=dbc.get("port", 3306),
        user=dbc.get("user", "root"), password=dbc.get("password", ""),
        db=dbc.get("db", "trading"), charset="utf8mb4",
        autocommit=True, minsize=1, maxsize=4,
    )
    bt = cfg.get("blogger_tracking", {})
    print("enabled=%s has_cookie=%s has_hexin=%s user_code=%s" % (
        bt.get("enabled"), bool(bt.get("cookie")), bool(bt.get("hexin_v")), bt.get("user_code")))
    try:
        posts = await fetch_blogger_posts()
    except BloggerFetchError as e:
        print("FETCH_FAIL: %r" % (e,))
        await database.close_db()
        return
    print("fetched=%d" % len(posts))
    new = 0
    for p in sorted(posts, key=lambda x: (x.get("posted_at") is None, x.get("posted_at"))):
        is_new = await repository.save_post(
            blogger_fid=bt.get("user_code", ""), blogger_name=p["blogger_name"],
            post_id=p["post_id"], posted_at=p["posted_at"], content=p["content"],
            stock_codes=p["stock_codes"], url=p["url"],
        )
        if is_new:
            new += 1
        print("post_id=%s ts=%s new=%s codes=%s" % (
            p["post_id"], p.get("posted_at"), is_new, ",".join(p["stock_codes"])))
    print("NEW_SAVED=%d" % new)
    await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
