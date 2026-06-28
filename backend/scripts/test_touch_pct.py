"""
参数扫描: ma10_touch_pct 2%/3%/4%/5% 对 S3_BUY 信号数量和胜率的影响。

跑法 (项目根目录):
  python -m backend.scripts.test_touch_pct
"""
import asyncio

LOOKBACK_DAYS = 40
TEST_PARAMS = [2.0, 3.0, 4.0, 5.0]


async def main():
    from backend.models import database, repository
    from backend.services.backtester import run_backtest

    await database.init_db()
    try:
        stocks = await repository.list_stocks(1, include_deleted=True)
        codes = [s["code"] for s in stocks]
        print(f"自选股池: {len(codes)} 只  | 回看窗口: {LOOKBACK_DAYS} 交易日")
        if not codes:
            print("股票池为空。")
            return

        base_config = await repository.get_signal_config(1)

        print(f"\n{'ma10_touch_pct':>15}  {'信号数':>6}  {'已完结':>6}  {'胜率':>7}  {'平均收益':>9}  {'平均持仓':>9}")
        print("-" * 65)

        for pct in TEST_PARAMS:
            # 注入参数覆盖
            user_cfg = dict(base_config or {})
            s3_cfg = dict(user_cfg.get("S3_BUY", {}))
            s3_cfg["ma10_touch_pct"] = pct
            user_cfg["S3_BUY"] = s3_cfg

            res = await run_backtest(codes, "S3_BUY", LOOKBACK_DAYS, user_cfg)
            s = res["summary"]
            trades = res["trades"]

            # 列出信号触发的股票
            triggered = [f"{t['code']}{t['name']}" for t in trades]

            print(
                f"{pct:>14.0f}%  {s['total_trades']:>6}  {s['closed_trades']:>6}  "
                f"{s['win_rate']:>6}%  {s['avg_return_pct']:>8}%  {s['avg_hold_days']:>7}天"
            )
            if triggered:
                print(f"  → 触发: {', '.join(triggered)}")

        # 逐笔明细（最宽松参数 5%）
        print("\n\n=== 5% 参数下逐笔明细 ===")
        user_cfg = dict(base_config or {})
        s3_cfg = dict(user_cfg.get("S3_BUY", {}))
        s3_cfg["ma10_touch_pct"] = 5.0
        user_cfg["S3_BUY"] = s3_cfg
        res5 = await run_backtest(codes, "S3_BUY", LOOKBACK_DAYS, user_cfg)
        for t in res5["trades"]:
            closed = "持仓中" if any(a["type"] == "holding" for a in t["actions"]) else "已完结"
            print(
                f"  {t['code']} {t['name']:6s}  买{t['buy_date']}@{t['buy_price']:.2f}"
                f"  收益{t['total_return_pct']:+.2f}%  持{t['hold_days']}天  [{closed}]"
            )

    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
