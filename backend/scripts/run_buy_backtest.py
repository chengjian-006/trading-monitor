"""一次性回测: 对比两个实盘买点的成功率 — 弱势极限(左侧) vs 启动初期(右侧)。

跑法 (项目根目录):
  python -m backend.scripts.run_buy_backtest

股票池取 user_id=1 的自选股, 回看窗口默认 60 交易日。
卖出规则沿用 backtester 的统一短线规则(止盈7%减仓 / 2日未站上MA10止损 / 大幅跌破MA5清仓),
两个买点用同一套卖出逻辑, 保证胜率口径可比。
"""
import asyncio

LOOKBACK_DAYS = 60
SIGNALS = [
    ("BUY_WEAK_EXTREME", "弱势极限（左侧）"),
    ("BUY_STRONG_START", "启动初期（右侧）"),
]


def _fmt_summary(name: str, s: dict) -> str:
    return (
        f"\n=== {name} ===\n"
        f"  触发信号数 : {s['total_trades']}  (已完结 {s['closed_trades']} / 仍持仓 {s['holding_trades']})\n"
        f"  胜率       : {s['win_rate']}%   (赢 {s['win_count']} / 亏 {s['loss_count']})\n"
        f"  平均收益   : {s['avg_return_pct']}%\n"
        f"  最大盈利   : {s['max_profit_pct']}%   最大亏损: {s['max_loss_pct']}%\n"
        f"  累计收益   : {s['total_return_pct']}%\n"
        f"  平均持仓   : {s['avg_hold_days']} 天\n"
    )


async def main():
    from backend.models import database
    from backend.models import repository
    from backend.services.backtester import run_backtest

    await database.init_db()
    try:
        stocks = await repository.list_stocks(1, include_deleted=True)
        codes = [s["code"] for s in stocks]
        print(f"自选股池: {len(codes)} 只  | 回看窗口: {LOOKBACK_DAYS} 交易日")
        if not codes:
            print("股票池为空, 无法回测。")
            return

        user_config = await repository.get_signal_config(1)

        results = {}
        for sig_id, sig_name in SIGNALS:
            print(f"\n>>> 正在回测 {sig_name} ({sig_id}) ...")
            res = await run_backtest(codes, sig_id, LOOKBACK_DAYS, user_config)
            results[sig_id] = res
            print(_fmt_summary(sig_name, res["summary"]))

            # 逐笔明细
            trades = res["trades"]
            if trades:
                print(f"  逐笔明细 ({len(trades)} 笔):")
                for t in trades:
                    closed = "持仓中" if any(a["type"] == "holding" for a in t["actions"]) else "已完结"
                    print(f"    {t['code']} {t['name']}  买{t['buy_date']}@{t['buy_price']}  "
                          f"收益{t['total_return_pct']}%  持{t['hold_days']}天  [{closed}]")

        # 对比表
        print("\n" + "=" * 60)
        print("对比汇总")
        print("=" * 60)
        print(f"{'买点':<14}{'信号数':>6}{'胜率':>8}{'平均收益':>10}{'平均持仓':>10}")
        for sig_id, sig_name in SIGNALS:
            s = results[sig_id]["summary"]
            print(f"{sig_name:<14}{s['total_trades']:>6}{str(s['win_rate'])+'%':>8}"
                  f"{str(s['avg_return_pct'])+'%':>10}{str(s['avg_hold_days'])+'天':>10}")
    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
