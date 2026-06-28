# -*- coding: utf-8 -*-
"""分析自选股(user_id=1)里近期涨幅好的票, 提取技术/题材共性。只读, 不写库。"""
import asyncio
import warnings
import statistics as st

warnings.filterwarnings("ignore")

from backend.models.database import init_db  # noqa: E402
from backend.models.repo._db import _fetchall  # noqa: E402

UID = 1


async def main():
    await init_db()
    import numpy as np

    # 1) 取自选池(个股, 排除板块/指数码)
    prows = await _fetchall(
        "SELECT * FROM cfzy_biz_stock_pool WHERE user_id=%s", (UID,))
    if prows:
        print("池表字段:", list(prows[0].keys()))
    pool = []
    for r in prows:
        code = str(r.get("code", ""))
        if len(code) != 6 or not code.isdigit():
            continue
        if code[:3] in ("881", "884", "885", "886") or code[:2] in ("88", "98"):
            continue
        if r.get("deleted_at"):
            continue
        ind = r.get("industry") or r.get("board_name") or ""
        pool.append((code, r.get("name", ""), ind, r.get("concepts") or ""))
    print("个股池数量:", len(pool))

    out = []
    for code, nm, board, concepts in pool:
        rs = await _fetchall(
            "SELECT trade_date,open,high,low,close,volume FROM cfzy_sys_kline_cache "
            "WHERE code=%s ORDER BY trade_date DESC LIMIT 75", (code,))
        if len(rs) < 65:
            continue
        rs = rs[::-1]
        c = np.array([float(x["close"]) for x in rs])
        v = np.array([float(x["volume"]) for x in rs])
        h = np.array([float(x["high"]) for x in rs])
        last = str(rs[-1]["trade_date"])[:10]
        ma20 = c[-20:].mean(); ma60 = c[-60:].mean()
        p5 = (c[-1] / c[-6] - 1) * 100
        p20 = (c[-1] / c[-21] - 1) * 100
        volr = v[-1] / v[-11:-1].mean() if v[-11:-1].mean() > 0 else 0
        bias20 = (c[-1] / ma20 - 1) * 100
        a20 = c[-1] > ma20; a60 = c[-1] > ma60
        zt = sum(1 for i in range(-20, 0) if c[i] / c[i - 1] - 1 > 0.098)
        nh = c[-1] >= h[-60:-1].max()
        out.append(dict(code=code, nm=nm, board=board, concepts=concepts, last=last, p5=p5, p20=p20,
                        volr=volr, bias20=bias20, a20=a20, a60=a60, zt=zt, nh=nh))

    out.sort(key=lambda x: x["p20"], reverse=True)
    print("\n=== 自选池近20日涨幅 TOP25(截至", out[0]["last"] if out else "?", ") ===")
    print("代码    名称        20日%  量比 距MA20 近20涨停 概念")
    for x in out[:25]:
        print("%-7s %-9s %6.1f %5.2f %6.1f   %d   %s" % (
            x["code"], x["nm"][:9], x["p20"], x["volr"], x["bias20"], x["zt"],
            (x["concepts"] or "")[:46]))

    # 2) 强势子集 = 近20日涨幅 top(取>=15% 或前10)
    strong = [x for x in out if x["p20"] >= 15]
    if len(strong) < 8:
        strong = out[:10]
    print("\n=== 强势子集共性 (n=%d, 近20日涨幅阈值) ===" % len(strong))
    n = len(strong)
    print("站上MA20:", sum(x["a20"] for x in strong), "/", n)
    print("站上MA60:", sum(x["a60"] for x in strong), "/", n)
    print("破60日新高:", sum(x["nh"] for x in strong), "/", n)
    print("近20日有涨停:", sum(1 for x in strong if x["zt"] > 0), "/", n)
    print("量比中位数:", round(st.median([x["volr"] for x in strong]), 2))
    print("距MA20乖离中位数:", round(st.median([x["bias20"] for x in strong]), 1), "%")
    print("20日涨幅中位数:", round(st.median([x["p20"] for x in strong]), 1), "%")
    # 题材关键词聚类(对强势子集的 concepts 串做关键词计数)
    from collections import Counter
    KW = ["覆铜板", "铜箔", "玻纤", "玻璃布", "PCB", "MLCC", "电感", "被动元件", "陶瓷",
          "光模块", "CPO", "算力", "半导体", "封装", "存储", "光纤", "硅", "锂", "铜", "钨", "光刻"]
    kc = Counter()
    for x in strong:
        s = x["concepts"] or ""
        for k in KW:
            if k in s:
                kc[k] += 1
    print("题材关键词命中(强势子集):", dict(kc.most_common()))
    bc = Counter((x["board"] or "未知") for x in strong)
    print("行业分布:", dict(bc.most_common(12)))


asyncio.run(main())
