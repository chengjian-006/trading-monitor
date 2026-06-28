# -*- coding: utf-8 -*-
"""全天成交额预测系数实测标定 — 只读诊断.

intraday_estimator._TIME_COEF_TABLE 是凭经验填的"某时点累计成交占全天比例",
若早盘系数偏低, 当前累计额 / 系数 会把全天量外推得过高(开盘越猛越离谱)。
本脚本用全市场 5 分钟真实成交额(cfzy_sys_kline_5m) 反算实测系数曲线, 与经验表对照。

跑法(项目根): py -3 -m backend.scripts.diag_turnover_coef
"""
import asyncio
import sys
from collections import defaultdict

import aiomysql

from backend.core.config import load_config
from backend.models import database
from backend.models.repo._db import _fetchall
from backend.services.intraday_estimator import _TIME_COEF_TABLE, _interp_coef

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


async def _ensure_pool():
    cfg = load_config().get("database", {})
    database._pool = await aiomysql.create_pool(
        host=cfg.get("host", "127.0.0.1"), port=cfg.get("port", 3306),
        user=cfg.get("user", "root"), password=cfg.get("password", ""),
        db=cfg.get("db", "trading"), charset="utf8mb4",
        autocommit=True, minsize=1, maxsize=4,
    )


# 对照检查点 = 经验表全部锚点(用于整表重标定)
CHECK = [k for k, _ in _TIME_COEF_TABLE]


def hhmm(m):
    return f"{m//60:02d}:{m%60:02d}"


async def main():
    await _ensure_pool()
    # 全市场每个 5min 时间戳的成交额合计
    rows = await _fetchall(
        "SELECT dt, SUM(amount) AS amt FROM cfzy_sys_kline_5m GROUP BY dt ORDER BY dt")
    # 按日聚合: day -> {minute_of_day: 该bucket市场总额}
    byday = defaultdict(dict)
    for r in rows:
        dt = r["dt"]
        amt = float(r["amt"] or 0)
        mod = dt.hour * 60 + dt.minute
        byday[dt.strftime("%Y-%m-%d")][mod] = byday[dt.strftime("%Y-%m-%d")].get(mod, 0.0) + amt

    days = sorted(byday.keys())
    print(f"5m表覆盖 {len(days)} 天: {days[0]} ~ {days[-1]}")
    # 确认 bucket 标注(首/次 minute-of-day): baostock 5min 通常按 bar 结束时刻标注(首根=09:35)
    sample = sorted(byday[days[-1]].keys())[:3]
    print(f"末日前3个bucket minute-of-day: {[hhmm(m) for m in sample]}")

    # 每天算累计占比曲线(按 bucket 的 minute-of-day 升序累计), 跨天平均
    # 只保留全天总额>0 的天; 用各天总额排除半天/停盘异常日
    valid = []
    for d in days:
        buckets = byday[d]
        total = sum(buckets.values())
        if total <= 0:
            continue
        valid.append((d, buckets, total))
    # 取最近 N 天更贴近当前市况
    N = 30
    recent = valid[-N:]
    print(f"用于标定: 最近 {len(recent)} 个有效交易日 ({recent[0][0]} ~ {recent[-1][0]})\n")

    # 对每个检查点, 算实测累计占比(跨天均值)
    print(f"{'时点':>6} | {'经验表系数':>9} | {'实测系数':>8} | {'外推放大倍数 经验/实测':>16}")
    print("-" * 60)
    for cp in CHECK:
        fr_list = []
        for d, buckets, total in recent:
            cum = sum(a for m, a in buckets.items() if m <= cp)
            fr_list.append(cum / total)
        real = sum(fr_list) / len(fr_list)
        emp = _interp_coef(cp)
        # 同一累计额, 用经验系数 vs 实测系数 外推全天的比值(>1 = 经验表把全天放大了多少)
        ratio = (real / emp) if emp > 0 else float("nan")
        print(f"{hhmm(cp):>6} | {emp:9.3f} | {real:8.3f} | {ratio:16.2f}x")

    # 额外: 早盘高额日 vs 平均日的差异(开盘猛的日子早盘占比是否更高 → 经验固定表会高估)
    print("\n[开盘越猛早盘占比越高? 验证经验固定系数对高量日的偏差]")
    cp = 10 * 60  # 10:00
    arr = []
    for d, buckets, total in recent:
        cum = sum(a for m, a in buckets.items() if m <= cp)
        arr.append((total, cum / total))
    arr.sort()  # 按全天总额升序
    lo = arr[:max(1, len(arr)//3)]
    hi = arr[-max(1, len(arr)//3):]
    flo = sum(x for _, x in lo) / len(lo)
    fhi = sum(x for _, x in hi) / len(hi)
    print(f"  10:00累计占比  低量日组={flo:.3f}  高量日组={fhi:.3f}  (经验表={_interp_coef(cp):.3f})")

    await database.close_db()


if __name__ == "__main__":
    asyncio.run(main())
