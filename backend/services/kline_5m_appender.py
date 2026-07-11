"""5分钟K线每日追加(baostock 后复权) — 胜率5分钟诚实口径的数据前提 (v1.7.598)。

cfzy_sys_kline_5m 此前是一次性回填(backend/scripts/backfill_fullmarket_kline_5m.py, 停在回填日),
本任务每晚 20:00 增量续上:
  - 逐票取库内最后交易日, start=次日, end=今天; 新上市/新入池票默认回补近一年
  - baostock 分钟线通常当晚可得; 若当晚未出, 次日追加自动补齐(幂等 upsert)
  - baostock 是同步单会话客户端: 全程 to_thread 串行拉取, 不碰实时行情 HTTP 池
后复权口径与存量一致(adjustflag=1, 历史bar不重写, 增量追加安全); 消费端(backtester_5m)
按日重定标到日线前复权刻度, 这里不做任何价格换算。
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from backend.models.repo._db import _executemany, _fetchall

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 366     # 库内无此票时的默认回补窗口
FETCH_FIELDS = "date,time,open,high,low,close,volume,amount"
ADJUST = "1"                    # 后复权(与存量口径一致)
FREQ = "5"

_UPSERT = (
    "INSERT INTO cfzy_sys_kline_5m (code,dt,open,high,low,close,volume,amount) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE open=VALUES(open),high=VALUES(high),low=VALUES(low),"
    "close=VALUES(close),volume=VALUES(volume),amount=VALUES(amount)"
)


def _to_bs(code6: str) -> str | None:
    """600519 → sh.600519; 0/3 开头 → sz.; 北交所(4/8/92)等 baostock 无 → None。"""
    c = str(code6)
    if c.startswith("6"):
        return f"sh.{c}"
    if c.startswith(("0", "3")):
        return f"sz.{c}"
    return None


def _plan_windows(have_max: dict, codes: list, end: str,
                  default_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """逐票增量窗口: {bs_code: (start, end)}。

    have_max: {code6: 'YYYY-MM-DD' 库内最后交易日}; 无此票 → 回补 end-default_days。
    已追到 end 的票不进计划。"""
    plans: dict[str, tuple[str, str]] = {}
    for code in codes:
        bs_code = _to_bs(code)
        if not bs_code:
            continue
        last = have_max.get(str(code))
        if last:
            start = (date.fromisoformat(str(last)[:10]) + timedelta(days=1)).isoformat()
        else:
            start = (date.fromisoformat(end) - timedelta(days=default_days)).isoformat()
        if start > end:
            continue
        plans[bs_code] = (start, end)
    return plans


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


def _fetch_5m_sync(bs, bs_code: str, start: str, end: str) -> list[tuple]:
    """拉单只5分钟线(同步, 在线程里跑) → upsert 行。会话掉线重登一次。"""
    code6 = bs_code.split(".", 1)[1]
    rs = bs.query_history_k_data_plus(
        bs_code, FETCH_FIELDS, start_date=start, end_date=end,
        frequency=FREQ, adjustflag=ADJUST)
    if rs.error_code != "0":
        bs.login()
        rs = bs.query_history_k_data_plus(
            bs_code, FETCH_FIELDS, start_date=start, end_date=end,
            frequency=FREQ, adjustflag=ADJUST)
        if rs.error_code != "0":
            return []
    rows: list[tuple] = []
    while rs.next():
        r = rs.get_row_data()
        dt = _parse_dt(r[0], r[1])
        if not dt:
            continue
        vol = _fnum(r[6])
        rows.append((code6, dt, _fnum(r[2]), _fnum(r[3]), _fnum(r[4]),
                     _fnum(r[5]), int(vol) if vol is not None else None, _fnum(r[7])))
    return rows


async def _universe_codes() -> list[str]:
    """追加对象 = 5m表已有票 ∪ 各用户自选股池(新入池票自动回补近一年)。

    不走 baostock query_all_stock 拉全市场清单: 新IPO不足65根本就进不了胜率样本,
    季度跑一次一次性回填脚本即可补新票, 每晚任务保持轻依赖。"""
    five = await _fetchall("SELECT DISTINCT code FROM cfzy_sys_kline_5m")
    pool = await _fetchall("SELECT DISTINCT code FROM cfzy_biz_stock_pool")
    codes = {str(r["code"]) for r in five} | {str(r["code"]) for r in pool}
    return sorted(codes)


async def append_kline_5m():
    """每晚20:00: 逐票增量追加5分钟K线。baostock 当晚数据未出时次日自动补齐(幂等)。"""
    try:
        import baostock as bs
    except ImportError:
        logger.error("[k5m_append] baostock 未安装(pip install baostock), 任务跳过")
        raise
    end = date.today().isoformat()
    rows = await _fetchall(
        "SELECT code, DATE(MAX(dt)) AS d FROM cfzy_sys_kline_5m GROUP BY code")
    have_max = {str(r["code"]): str(r["d"])[:10] for r in rows if r["d"]}
    codes = await _universe_codes()
    plans = _plan_windows(have_max, codes, end)
    if not plans:
        logger.info("[k5m_append] 已全部最新, 无需追加")
        return {"appended": 0, "codes": 0}

    def _login():
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login 失败: {lg.error_msg}")

    await asyncio.to_thread(_login)
    logger.info(f"[k5m_append] {len(plans)} 只待追加, end={end}")
    ok = empty = fail = total_rows = 0
    try:
        for i, (bs_code, (start, wend)) in enumerate(sorted(plans.items()), 1):
            try:
                data = await asyncio.to_thread(_fetch_5m_sync, bs, bs_code, start, wend)
                if data:
                    await _executemany(_UPSERT, data)
                    ok += 1
                    total_rows += len(data)
                else:
                    empty += 1
            except Exception as e:
                fail += 1
                if fail <= 5:
                    logger.warning(f"[k5m_append] {bs_code} 失败: {e}")
            if i % 500 == 0:
                logger.info(f"[k5m_append] {i}/{len(plans)} ok={ok} empty={empty} fail={fail}")
    finally:
        try:
            await asyncio.to_thread(bs.logout)
        except Exception:
            pass
    logger.info(f"[k5m_append] DONE 追加{total_rows}根/{ok}只 empty={empty} fail={fail}")
    if fail and fail >= max(20, len(plans) // 4):
        raise RuntimeError(f"[k5m_append] 失败过多 fail={fail}/{len(plans)}, 触发任务失败告警")
    return {"appended": total_rows, "codes": ok}
