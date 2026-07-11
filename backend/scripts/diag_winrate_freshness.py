"""诊断模型胜率表为何停在 06-12 — 只读, 不写库。

查三件事:
  1. cfzy_sys_kline_cache 的 max(trade_date) + 近几日各日覆盖股票数(看每日追加是否停)
  2. cfzy_biz_model_winrate 的 run_date / updated_at / 各模型样本数
  3. cfzy_sys_scheduled_tasks 里 refresh_model_winrate / refresh_market_breadth 的 enabled/last_run

跑法(项目根目录): py -3 -m backend.scripts.diag_winrate_freshness
"""
import asyncio
import sys

import aiomysql

from backend.models.database import load_config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def main():
    cfg = load_config().get("database", {})
    pool = await aiomysql.create_pool(
        host=cfg.get("host", "127.0.0.1"), port=cfg.get("port", 3306),
        user=cfg.get("user", "root"), password=cfg.get("password", ""),
        db=cfg.get("db", "trading"), charset="utf8mb4", autocommit=True,
        minsize=1, maxsize=2,
    )
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            print("=" * 70)
            print("1) cfzy_sys_kline_cache 近期每日覆盖 (看每日追加是否停)")
            await cur.execute(
                "SELECT trade_date, COUNT(*) AS n FROM cfzy_sys_kline_cache "
                "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 15"
            )
            for r in await cur.fetchall():
                print(f"  {str(r['trade_date'])[:10]}  {r['n']:>6} 只")

            print("=" * 70)
            print("2) cfzy_biz_model_winrate (run_date / updated_at / 样本)")
            await cur.execute(
                "SELECT signal_id, model_name, win_rate_3m, n_3m, win_rate_6m, n_6m, "
                "run_date, updated_at FROM cfzy_biz_model_winrate ORDER BY model_name"
            )
            for r in await cur.fetchall():
                print(f"  {r['model_name']:<22} 3月{r['win_rate_3m']}%/{r['n_3m']}笔 "
                      f"6月{r['win_rate_6m']}%/{r['n_6m']}笔  run={r['run_date']} "
                      f"upd={r['updated_at']}")

            print("=" * 70)
            print("3) 相关定时任务 enabled / last_run_at / status")
            await cur.execute(
                "SELECT job_id, schedule_config, enabled, last_run_at, last_status, "
                "consecutive_failures, last_error_msg FROM cfzy_sys_scheduled_tasks "
                "WHERE job_id IN ('model_winrate_refresh','market_breadth_1535')"
            )
            for r in await cur.fetchall():
                print(f"  {r['job_id']:<24} enabled={r['enabled']} "
                      f"cfg={r['schedule_config']} last_run_at={r['last_run_at']} "
                      f"status={r['last_status']} fails={r['consecutive_failures']}")
                if r['last_error_msg']:
                    print(f"      err: {r['last_error_msg'][:300]}")

            print("=" * 70)
            print("4) 验证 Bug A: ORDER BY code,trade_date 下 rows[-1] 取到哪只票/哪天")
            await cur.execute(
                "SELECT MAX(trade_date) AS gmax FROM cfzy_sys_kline_cache "
                "WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 300 DAY)"
            )
            print(f"  全市场 max(trade_date) = {(await cur.fetchone())['gmax']}")
            # rows[-1] 等价: ORDER BY code, trade_date 的最后一行 = code 最大那只的最后一天
            await cur.execute(
                "SELECT code, MAX(trade_date) AS last_dt FROM cfzy_sys_kline_cache "
                "WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 300 DAY) "
                "AND code = (SELECT MAX(code) FROM cfzy_sys_kline_cache "
                "            WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 300 DAY)) "
                "GROUP BY code"
            )
            r = await cur.fetchone()
            print(f"  排最后的 code={r['code']} 其 max(trade_date)={r['last_dt']}  ← refresher 误当成 today_str")

        if "--dryrun" in sys.argv:
            print("=" * 70)
            print("5) DRY-RUN 已随 v1.7.599 移除: 胜率重算已切5分钟诚实口径(逐票按需加载, 无整表_crunch),")
            print("   要真跑一轮请用 python -m backend.scripts.run_winrate_refresh_once (会写库)。")
    pool.close()
    await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
