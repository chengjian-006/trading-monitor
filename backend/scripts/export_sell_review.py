# -*- coding: utf-8 -*-
"""导出 卖太早/卖太晚 逐笔复盘清单 -> CSV(utf-8-sig, Excel可开) + 控制台TopN。

复用 compare_trades_to_model(真连库真跑), 取 sell_compare.details,
按「钱差」排序: 卖太早=模型多赚最多在前; 卖太晚=实盘亏最狠在前。
跑法: python -m backend.scripts.export_sell_review [user_id=1]
"""
import asyncio
import csv
import sys

import aiomysql

from backend.core.config import load_config
from backend.models import database
from backend.services.trade_model_compare import compare_trades_to_model

OUT_DIR = r"D:\财务管理\交易系统\Trading-monitor"
COLS = ["代码", "名称", "买入日", "卖出日", "实盘收益%", "持有天", "判定",
        "模型卖出日", "模型卖出理由", "模型收益%", "差_模型减实盘%", "时点差_日"]


async def _pool():
    cfg = load_config().get("database", {})
    database._pool = await aiomysql.create_pool(
        host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"],
        db=cfg["db"], charset="utf8mb4", autocommit=True, minsize=1, maxsize=4)


def _row(d):
    mret = d["model_return"]
    gap = (mret - d["actual_return"]) if mret is not None else None
    return {
        "代码": d["code"], "名称": d["name"], "买入日": d["buy_date"], "卖出日": d["sell_date"],
        "实盘收益%": d["actual_return"], "持有天": d["hold_days"], "判定": d["verdict"],
        "模型卖出日": d["model_exit_date"], "模型卖出理由": d["model_reason"],
        "模型收益%": mret, "差_模型减实盘%": round(gap, 2) if gap is not None else None,
        "时点差_日": d["day_diff"],
    }


def _dump(name, rows):
    path = f"{OUT_DIR}\\{name}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  -> {path}  ({len(rows)} 笔)")
    return path


async def main():
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    await _pool()
    try:
        res = await compare_trades_to_model(uid, signal_window=5)
    finally:
        await database.close_db()
    if not res.get("ok"):
        print("失败:", res.get("msg")); return

    details = res["sell_compare"]["details"]
    early = [_row(d) for d in details if d["verdict"] == "卖太早"]
    late = [_row(d) for d in details if d["verdict"] == "卖太晚"]
    # 卖太早: 模型多赚最多在前; 卖太晚: 实盘亏最狠在前
    early.sort(key=lambda r: -(r["差_模型减实盘%"] or 0))
    late.sort(key=lambda r: (r["实盘收益%"]))

    print("== 导出文件 ==")
    _dump("卖太早_赢家跑太快_清单", early)
    _dump("卖太晚_输家扛太久_清单", late)

    def show(title, rows, n=15):
        print(f"\n== {title} (Top{min(n,len(rows))}/{len(rows)}) ==")
        for r in rows[:n]:
            print(f"  {r['代码']} {r['名称']:<6} 买{r['买入日']} 卖{r['卖出日']} "
                  f"实盘{r['实盘收益%']:+}%/{r['持有天']}日 → 模型卖{r['模型卖出日']}"
                  f"({r['模型卖出理由']}) {r['模型收益%']:+}% | 差{r['差_模型减实盘%']:+}%")

    show("卖太早 · 模型多赚最多", early)
    show("卖太晚 · 实盘亏最狠", late)

    # ── ② 错过信号清单 ──
    missed = res["missed_signals"]            # 已按 signal_date 倒序
    mcols = ["代码", "名称", "信号日", "信号名", "后5日收益%", "信号明细"]
    mrows = [{"代码": d["code"], "名称": d["name"], "信号日": d["signal_date"],
              "信号名": d["signal_name"], "后5日收益%": d["forward_ret_5d"],
              "信号明细": d["detail"]} for d in missed]
    mpath = f"{OUT_DIR}\\错过信号_清单.csv"
    with open(mpath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=mcols); w.writeheader()
        for r in mrows:
            w.writerow(r)
    print(f"\n== 错过信号 ==\n  -> {mpath}  ({len(mrows)} 笔)")
    # 错过里后5日涨最猛的(最可惜) Top10
    hot = sorted([d for d in missed if d["forward_ret_5d"] is not None],
                 key=lambda d: -d["forward_ret_5d"])[:10]
    print("  最可惜(后5日涨最多) Top10:")
    for d in hot:
        print(f"    {d['signal_date']} {d['code']} {d['name']:<6} {d['signal_name']:<14} 后5日{d['forward_ret_5d']:+}%")

    # ── ③ 按个股归并 ──
    name_of = {}
    for d in details:
        name_of[d["code"]] = d["name"]
    for d in res["buy_compare"]["details"]:
        name_of.setdefault(d["code"], d["name"])
    for d in missed:
        name_of.setdefault(d["code"], d["name"])

    agg = {}
    def slot(code):
        return agg.setdefault(code, {"配对": 0, "卖太早": 0, "卖太晚": 0, "符合卖": 0,
                                     "买符合": 0, "买偏离": 0, "实盘合计%": 0.0,
                                     "可挽回钱差%": 0.0, "错过信号": 0})
    for d in details:
        s = slot(d["code"]); s["配对"] += 1
        s["实盘合计%"] += d["actual_return"]
        if d["verdict"] == "卖太早":
            s["卖太早"] += 1
        elif d["verdict"] == "卖太晚":
            s["卖太晚"] += 1
        elif d["verdict"] == "符合模型":
            s["符合卖"] += 1
        if d["verdict"] in ("卖太早", "卖太晚") and d["model_return"] is not None:
            s["可挽回钱差%"] += d["model_return"] - d["actual_return"]
    for d in res["buy_compare"]["details"]:
        s = slot(d["code"])
        if d["verdict"] == "符合模型":
            s["买符合"] += 1
        elif d["verdict"] == "偏离模型":
            s["买偏离"] += 1
    for d in missed:
        slot(d["code"])["错过信号"] += 1

    acols = ["代码", "名称", "配对笔数", "卖太早", "卖太晚", "符合卖", "买符合", "买偏离",
             "实盘合计%", "可挽回钱差%", "错过信号"]
    arows = []
    for code, s in agg.items():
        arows.append({"代码": code, "名称": name_of.get(code, code),
                      "配对笔数": s["配对"], "卖太早": s["卖太早"], "卖太晚": s["卖太晚"],
                      "符合卖": s["符合卖"], "买符合": s["买符合"], "买偏离": s["买偏离"],
                      "实盘合计%": round(s["实盘合计%"], 2),
                      "可挽回钱差%": round(s["可挽回钱差%"], 2), "错过信号": s["错过信号"]})
    arows.sort(key=lambda r: -r["可挽回钱差%"])
    apath = f"{OUT_DIR}\\按个股归并_清单.csv"
    with open(apath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=acols); w.writeheader()
        for r in arows:
            w.writerow(r)
    print(f"\n== 按个股归并 ==\n  -> {apath}  ({len(arows)} 只)")
    print("  最该重点盯的(可挽回钱差最多) Top15:")
    for r in arows[:15]:
        print(f"    {r['代码']} {r['名称']:<6} 配对{r['配对笔数']} 早{r['卖太早']}/晚{r['卖太晚']}/符{r['符合卖']} "
              f"买符{r['买符合']}/偏{r['买偏离']} 实盘合计{r['实盘合计%']:+}% | 可挽回{r['可挽回钱差%']:+}% 错过{r['错过信号']}")


if __name__ == "__main__":
    asyncio.run(main())
