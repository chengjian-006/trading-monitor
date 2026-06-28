# -*- coding: utf-8 -*-
"""实时拉一次博主最新帖 (验证 cookie 是否过期, 只读不入库).

跑法:
    set PYTHONIOENCODING=utf-8
    py -3 -m backend.scripts.diag_blogger_fetch
"""
import asyncio

from backend.fetcher.ths_blogger import fetch_blogger_posts, BloggerFetchError
from backend.core.config import load_config


async def main():
    cfg = load_config().get("blogger_tracking", {})
    print(f"[diag] enabled={cfg.get('enabled')} user_code={cfg.get('user_code')} "
          f"has_cookie={bool(cfg.get('cookie'))} has_hexin_v={bool(cfg.get('hexin_v'))}")
    try:
        posts = await fetch_blogger_posts()
        print(f"[diag] 拉到 {len(posts)} 条\n")
        for p in posts:
            print(f"--- {p['posted_at']} post_id={p['post_id']} 票={p['stock_codes']} "
                  f"赞={p['like_num']} 评={p['comment_num']}")
            print(p["content"])
            print()
    except BloggerFetchError as e:
        print(f"[FAIL] 拉取失败(很可能 cookie/hexin-v 过期): {e}")


if __name__ == "__main__":
    asyncio.run(main())
