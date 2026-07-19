"""一次性: 手动触发 refresh_model_winrate 重算并写生产 cfzy_biz_model_winrate。
跑法(项目根): py -3 -m backend.scripts.run_winrate_refresh_once
"""
import asyncio
import sys

from backend.models.database import init_db, close_db
from backend.services.model_winrate_refresher import refresh_model_winrate

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def main():
    await init_db()
    try:
        # force=True: 手动补算绕过工作日闸; 断点续算, 反复跑直到定稿(全票齐才写正式表)。
        r = await refresh_model_winrate(force=True)
        while r and r.get("partial"):
            print(f"  部分完成 {r['staged']}/{r['total']}, 继续续算...")
            r = await refresh_model_winrate(force=True)
    finally:
        await close_db()
    if not r:
        print("空缓存/无可用股票, 未写入")
        return
    print(f"重算完成 run_date={r['as_of']}")
    for m in sorted(r["models"], key=lambda x: x["model_name"]):
        print(f"  {m['model_name']:<22} 3月{m['win_rate_3m']}%/{m['n_3m']}笔 "
              f"6月{m['win_rate_6m']}%/{m['n_6m']}笔 rank={m.get('rank_3m')}/{m.get('rank_n')}")


if __name__ == "__main__":
    asyncio.run(main())
