# -*- coding: utf-8 -*-
"""实盘 vs 模型对比 — 本地诊断脚本 (只读交割单, 真连库真跑).

自建轻量 aiomysql 池绕过 init_db 的建表/迁移, 直接喂给 repo 层,
调用 compare_trades_to_model(user_id) 跑出真实买点/卖点/错过信号/盈亏对照,
打印汇总 + 明细前若干行。

跑法:
    set PYTHONPATH=<项目根> && set PYTHONIOENCODING=utf-8
    python -m backend.scripts.diag_trade_compare [user_id=2] [signal_window=5]
"""
import asyncio
import sys

import aiomysql

from backend.core.config import load_config
from backend.models import database
from backend.services.trade_model_compare import compare_trades_to_model


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
        minsize=1,
        maxsize=4,
    )
    return cfg


def _hr(title=""):
    print("=" * 78)
    if title:
        print(title)
        print("=" * 78)


def _grp(label, g):
    print(f"  {label}: 笔数 {g['count']}  胜率 {g['win_rate']}%  平均收益 {g['avg_return']:+}%")


async def main():
    user_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    signal_window = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    cfg = await _ensure_pool()
    print(f"[diag] 连库 {cfg.get('host')}/{cfg.get('db')}  user_id={user_id}  signal_window={signal_window}")

    try:
        res = await compare_trades_to_model(user_id, signal_window=signal_window)
    finally:
        await database.close_db()

    if not res.get("ok"):
        print(f"[diag] 失败: {res.get('msg')}")
        return

    meta = res["meta"]
    _hr(f"实盘 vs 模型对比  user_id={user_id}  信号窗口={signal_window}交易日")
    print(f"涉及个股 {meta['stocks_total']} 只 | 可评估(拉到K线) {meta['stocks_evaluated']} 只 "
          f"| 配对交易 {meta['paired_trades']} 笔")
    if meta["stocks_no_kline"]:
        print(f"无K线(跳过): {', '.join(meta['stocks_no_kline'])}")

    bc = res["buy_compare"]
    _hr("【买点对比】 实盘买入点 是否落在模型买点附近")
    print(f"总买入 {bc['total']} 笔 | 符合模型 {bc['aligned']} | 偏离模型 {bc['deviated']} "
          f"| 无法评估 {bc['not_evaluable']}")
    print("-- 明细 --")
    for d in bc["details"]:
        tag = d["verdict"]
        extra = ""
        if tag == "符合模型":
            extra = f" ← {d['matched_signal_name']} (提前{d['signal_gap']}日) {d['detail']}"
        print(f"  {d['buy_date']} {d['code']} {d['name']:<6} @{d['buy_price']:<7} [{tag}]{extra}")

    sc = res["sell_compare"]
    _hr("【卖点对比】 实盘卖出 vs 模型(从你买入日模拟卖出规则)")
    print(f"总卖出 {sc['total']} 笔 | 符合 {sc['aligned']} | 卖太晚 {sc['too_late']} "
          f"| 卖太早 {sc['too_early']} | 无法评估 {sc['not_evaluable']}")
    print("-- 明细 --")
    for d in sc["details"]:
        mret = d["model_return"]
        mret_s = f"{mret:+}%" if mret is not None else "—"
        print(f"  {d['code']} {d['name']:<6} 买{d['buy_date']} 卖{d['sell_date']} "
              f"实盘{d['actual_return']:+}%/{d['hold_days']}日  "
              f"[{d['verdict']}] 模型卖{d['model_exit_date'] or '—'}({d['model_reason'] or '—'}) "
              f"模型收益{mret_s} 差{d['day_diff']}日")

    ms = res["missed_signals"]
    _hr(f"【错过的信号】 模型给了买点但你 {signal_window} 日内没买 (共 {len(ms)} 条, 列最近20)")
    for d in ms[:20]:
        fwd = d["forward_ret_5d"]
        fwd_s = f"{fwd:+}%" if fwd is not None else "—"
        print(f"  {d['signal_date']} {d['code']} {d['name']:<6} {d['signal_name']:<14} "
              f"后5日{fwd_s}  {d['detail']}")

    pc = res["pnl_contrast"]
    _hr("【盈亏对照】 听模型(符合) vs 凭感觉(偏离)")
    _grp("符合模型", pc["aligned"])
    _grp("偏离模型", pc["deviated"])
    _hr()
    print("[diag] 完成 — 以上为真实数据")


if __name__ == "__main__":
    asyncio.run(main())
