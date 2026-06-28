"""一次性回填全市场近1年 5 分钟 K 线到 cfzy_sys_kline_5m(后复权, 断点续跑).

数据源: baostock(免费, 支持历史分钟线回溯). 后复权 adjustflag=1 → 价格连续无跳空, 适合回测.
落库: cfzy_sys_kline_5m(code 6位, dt=K线结束时刻, 含 amount). 幂等 upsert.

本机直连远程 MySQL 跑(baostock 装在本机, DB 在火山云):
  cd D:/财务管理/交易系统/trading-monitor && py -3 -u backend/scripts/backfill_fullmarket_kline_5m.py
重复运行只补缺口(某票当前根数 ≥ 阈值则跳过). 5400+ 只 × 近1年, 视网络约 1.5~3 小时.

可选环境变量:
  K5M_START=2025-06-19  K5M_END=2026-06-19   回填区间(默认近1年滚动)
  K5M_LIMIT=50                                只测前 N 只(联调用)
  K5M_POOL_USER=1                             只回填某用户自选股池(不传则全市场)
"""
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import baostock as bs

from backend.models.database import init_db
from backend.models.repo._db import _execute, _executemany, _fetchall

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("k5m")

ADJUST = "1"                 # 后复权
FREQ = "5"                   # 5 分钟
MIN_BARS_SKIP_RATIO = 0.9    # 已有根数 ≥ 预期×此比例 → 视为已回填, 跳过(断点续跑)
FETCH_FIELDS = "date,time,open,high,low,close,volume,amount"

_UPSERT = (
    "INSERT INTO cfzy_sys_kline_5m (code,dt,open,high,low,close,volume,amount) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE open=VALUES(open),high=VALUES(high),low=VALUES(low),"
    "close=VALUES(close),volume=VALUES(volume),amount=VALUES(amount)"
)


def _default_range() -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=366)
    return (os.environ.get("K5M_START", start.isoformat()),
            os.environ.get("K5M_END", end.isoformat()))


def _is_a_stock(code: str) -> bool:
    """baostock code(sh.6xxxxx/sz.0xxxxx/sz.3xxxxx)=沪深A股个股; 剔指数/B股/北交所."""
    if code.startswith("sh.6") and len(code) == 9:
        return True
    if (code.startswith("sz.0") or code.startswith("sz.3")) and len(code) == 9:
        return True
    return False


def _to6(bs_code: str) -> str:
    """sh.600519 → 600519."""
    return bs_code.split(".", 1)[1]


def _parse_dt(d: str, t: str) -> str | None:
    """date='2025-06-16', time='20250616093500000' → '2025-06-16 09:35:00'."""
    try:
        return f"{d} {t[8:10]}:{t[10:12]}:{t[12:14]}"
    except Exception:
        return None


def _fnum(s):
    try:
        return float(s) if s not in ("", None) else None
    except (ValueError, TypeError):
        return None


def _login() -> None:
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login 失败: {lg.error_msg}")


def _fetch_universe(probe_day: str) -> list[str]:
    """baostock 全市场清单 → 沪深A股个股(sh.6/sz.0/sz.3).

    当日清单 T+1 才发布, 故从 probe_day 往前回退最多 10 个自然日找到非空的交易日.
    """
    d = datetime.fromisoformat(probe_day).date()
    for _ in range(10):
        rs = bs.query_all_stock(day=d.isoformat())
        out: list[str] = []
        while rs.error_code == "0" and rs.next():
            code = rs.get_row_data()[0]
            if _is_a_stock(code):
                out.append(code)
        if out:
            logger.info(f"[k5m] 清单基准交易日={d.isoformat()}")
            return out
        d -= timedelta(days=1)
    return []


def _to_bs(code6: str) -> str | None:
    """600519 → sh.600519; 0/3 开头 → sz.; 北交所(4/8)/其他 baostock 无 → None."""
    if code6.startswith("6"):
        return f"sh.{code6}"
    if code6.startswith(("0", "3")):
        return f"sz.{code6}"
    return None


async def _pool_universe(user_id: int) -> list[str]:
    """某用户自选股池 → baostock 个股代码(剔北交所等 baostock 无的)."""
    rows = await _fetchall(
        "SELECT DISTINCT code FROM cfzy_biz_stock_pool WHERE user_id=%s", (user_id,))
    out: list[str] = []
    for r in rows:
        bs_code = _to_bs(str(r["code"]))
        if bs_code:
            out.append(bs_code)
        else:
            logger.info(f"[k5m] 跳过(baostock无) {r['code']}")
    return out


def _fetch_5m(bs_code: str, start: str, end: str) -> list[tuple]:
    """拉单只 5 分钟线 → upsert 行 [(code6, dt, o,h,l,c,vol,amount), ...]."""
    code6 = _to6(bs_code)
    rs = bs.query_history_k_data_plus(
        bs_code, FETCH_FIELDS, start_date=start, end_date=end,
        frequency=FREQ, adjustflag=ADJUST)
    if rs.error_code != "0":
        # 会话可能掉线, 重登一次再试
        _login()
        rs = bs.query_history_k_data_plus(
            bs_code, FETCH_FIELDS, start_date=start, end_date=end,
            frequency=FREQ, adjustflag=ADJUST)
        if rs.error_code != "0":
            return []
    rows: list[tuple] = []
    while rs.next():
        r = rs.get_row_data()  # [date,time,open,high,low,close,volume,amount]
        dt = _parse_dt(r[0], r[1])
        if not dt:
            continue
        vol = _fnum(r[6])
        rows.append((code6, dt, _fnum(r[2]), _fnum(r[3]), _fnum(r[4]),
                     _fnum(r[5]), int(vol) if vol is not None else None, _fnum(r[7])))
    return rows


async def main() -> None:
    start, end = _default_range()
    limit = int(os.environ.get("K5M_LIMIT", "0"))
    bars_per_day = 48
    # 预期根数(粗估交易日 ≈ 跨度天数×5/7×0.96), 仅用于跳过判定
    span_days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    expected = int(span_days * 5 / 7 * 0.96) * bars_per_day
    skip_thresh = int(expected * MIN_BARS_SKIP_RATIO)

    await init_db()
    _login()
    logger.info(f"[k5m] 区间 {start}~{end}  预期≈{expected}根/票  跳过阈值={skip_thresh}")

    pool_user = os.environ.get("K5M_POOL_USER")
    if pool_user:
        universe = await _pool_universe(int(pool_user))
        logger.info(f"[k5m] 自选股池 user={pool_user}: {len(universe)} 只")
    else:
        universe = _fetch_universe(end)
        logger.info(f"[k5m] 全市场A股个股 {len(universe)} 只")
    if limit:
        universe = universe[:limit]
    total = len(universe)

    # 已有根数(断点续跑)
    rows = await _fetchall("SELECT code, COUNT(*) AS n FROM cfzy_sys_kline_5m GROUP BY code")
    have = {r["code"]: r["n"] for r in rows}

    ok = skipped = empty = fail = 0
    for i, bs_code in enumerate(universe, 1):
        code6 = _to6(bs_code)
        if have.get(code6, 0) >= skip_thresh:
            skipped += 1
        else:
            try:
                data = await asyncio.to_thread(_fetch_5m, bs_code, start, end)
                if data:
                    await _executemany(_UPSERT, data)
                    ok += 1
                else:
                    empty += 1
            except Exception as e:
                fail += 1
                logger.warning(f"[k5m] {bs_code} 失败: {e}")
        if i % 200 == 0:
            logger.info(f"[k5m] {i}/{total} ok={ok} skip={skipped} empty={empty} fail={fail}")

    bs.logout()
    cnt = await _fetchall("SELECT COUNT(*) AS n FROM cfzy_sys_kline_5m")
    logger.info(f"[k5m] DONE total={total} ok={ok} skip={skipped} empty={empty} "
                f"fail={fail}  库内总行数={cnt[0]['n']}")


if __name__ == "__main__":
    asyncio.run(main())
