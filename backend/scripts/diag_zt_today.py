"""今日涨停板盘点 — 一次性诊断脚本(只读, 不写库)。

同花顺 limit_up_pool 一次拿: 涨停时间(首封/末封) + 连板高度 + 涨停题材(概念)。
跑法(项目根目录): py -3 -m backend.scripts.diag_zt_today [YYYYMMDD]
"""
import asyncio
import sys

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.10jqka.com.cn/",
}


from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))


def _fmt_time(v):
    """同花顺封板时间为 Unix 秒级时间戳 → 北京时间 HH:MM:SS。"""
    if v in (None, "", 0, "0"):
        return "--"
    try:
        return datetime.fromtimestamp(int(v), _CST).strftime("%H:%M:%S")
    except Exception:
        return str(v)


async def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "20260617"
    url = (f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
           f"?page=1&limit=200&field=199112,10,9001,330323,330324,330325,"
           f"9002,330329,133971,133970,1968584,3475914,9003,9004"
           f"&order_field=330323&order_type=0&date={date}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0),
                                 follow_redirects=True, trust_env=False) as client:
        resp = await client.get(url, headers=THS_HEADERS)
        print(f"HTTP {resp.status_code}  date={date}")
        data = (resp.json() or {}).get("data") or {}
        luc = (data.get("limit_up_count") or {}).get("today") or {}
        ldc = (data.get("limit_down_count") or {}).get("today") or {}
        pool = data.get("info") or []
        print(f"封板涨停={luc.get('num')}  曾涨停={luc.get('history_num')}  "
              f"炸板={luc.get('open_num')}  封板率={luc.get('rate')}  "
              f"跌停={ldc.get('num')}")
        if pool:
            print(f"\n首条原始字段: {sorted(pool[0].keys())}")
            print(f"样本: {pool[0]}\n")
        # 排序: 连板高度降序, 同高度按首封时间升序
        def board_n(it):
            hv = it.get("high_days_value")
            if hv:
                try:
                    return int(hv) >> 16
                except Exception:
                    pass
            s = str(it.get("high_days") or "")
            import re
            nums = re.findall(r"\d+", s)
            return int(nums[-1]) if nums else 1
        rows = sorted(pool, key=lambda it: (-board_n(it), str(it.get("first_limit_up_time") or it.get("330323") or "")))
        print(f"{'代码':<8}{'名称':<9}{'高度':<10}{'首封':<10}{'末封':<10}{'炸板':<5}涨停原因/概念")
        print("-" * 90)
        for it in rows:
            code = it.get("code") or ""
            name = (it.get("name") or "").ljust(6)
            label = it.get("high_days") or "首板"
            first_t = _fmt_time(it.get("first_limit_up_time") or it.get("330323"))
            last_t = _fmt_time(it.get("last_limit_up_time") or it.get("330324"))
            opn = it.get("open_num") or 0
            reason = it.get("reason_type") or ""
            print(f"{code:<8}{name}{str(label):<10}{first_t:<10}{last_t:<10}{str(opn):<5}{reason}")


if __name__ == "__main__":
    asyncio.run(main())
