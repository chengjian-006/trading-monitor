"""A 股交易日历 / 时段判断的单一来源 (v1.7.x).

替代此前散在 6 个 service 的 _is_trading_time / _is_workday 副本.

语义说明:
  is_workday(): 工作日 (周一~周五), 不考虑法定节假日 (后续可接 chinese-calendar 库)
  is_trading_time(): 工作日 + 当前时刻落在 config.json 的 trading_hours 区间内
                     (默认 09:25-11:30 / 13:00-15:00, 含集合竞价撮合)
  is_continuous_auction(): 工作日 + 09:30-11:30 或 13:00-15:00 (不含集合竞价)
                          用于"连续竞价才做的判断"(如 BUY_STRONG_START 的 10:00 门槛)

任何 service 需要这类判断都从这里取, 不要本地拷贝.
"""
from datetime import datetime, time as dtime

from backend.core.config import load_config


def _is_legal_holiday(d) -> bool:
    """该日期是否A股法定节假日(走 chinese_calendar). 库未装/年份超出支持范围 → False(回退到仅按周几判断, 不误拦)."""
    try:
        import chinese_calendar
        return chinese_calendar.is_holiday(d)
    except (ImportError, NotImplementedError):
        return False


def is_workday(now: datetime | None = None) -> bool:
    """A股交易日: 周一~周五 且 非法定节假日.

    节假日落在工作日(如端午周五)会被剔除; 调休补班的周末股市不交易, 因 weekday>=5 本就排除.
    chinese_calendar 仅覆盖到近1~2年, 超范围年份回退为仅按周几判断(详见 _is_legal_holiday)."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return not _is_legal_holiday(now.date())


def is_trading_time(now: datetime | None = None) -> bool:
    """配置 trading_hours 内 (默认 09:25-11:30 / 13:00-15:00). 含集合竞价撮合.

    用于触发盘中扫描、分时预热、行情刷新等任务的开闸条件.
    """
    if not is_workday(now):
        return False
    cfg = load_config()
    t = (now or datetime.now()).strftime("%H:%M")
    for p in cfg.get("trading_hours", []):
        if p["start"] <= t <= p["end"]:
            return True
    return False


def is_continuous_auction(now: datetime | None = None) -> bool:
    """连续竞价时段 (不含集合竞价): 09:30-11:30 或 13:00-15:00.

    用于"必须有连续盘中走势才有意义"的判断, 如 BUY_STRONG_START 量能预估、
    短线卖一/二/三 跌破均线判断 — 9:25 的集合竞价数据噪音过大不应触发.
    """
    now = now or datetime.now()
    if not is_workday(now):
        return False
    t = now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))


def trading_minute(hhmm: str) -> int:
    """HH:MM → 交易分钟序号(9:30=0, 11:30=120, 13:00=120, 15:00=240); 午休/盘外钳到边界,
    使 11:30 与 13:02 只差 2 分钟, 跨午休不误伤陈旧度判断. 仅适用A股时段(港股勿用)."""
    abs_min = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    if abs_min <= 9 * 60 + 30:
        return 0
    if abs_min <= 11 * 60 + 30:
        return abs_min - (9 * 60 + 30)
    if abs_min <= 13 * 60:
        return 120
    if abs_min <= 15 * 60:
        return 120 + abs_min - 13 * 60
    return 240


# 分时陈旧度上限(交易分钟): 分时1分钟一点, 健康滞后≤2分钟; 超过5分钟=源冻结回放/隔日残留
TREND_STALE_MIN = 5


def trends_stale(trends: list, now_hhmm: str, max_lag_min: int = TREND_STALE_MIN) -> bool:
    """分时末点时间离 now 太远(双向) → 源冻结回放(挂掉后反复吐同一份序列)/隔日残留.
    空序列是"没数据"不是"陈旧"; 无 time 字段(旧格式)无法判断 — 两者都不拦。"""
    t = trends[-1].get("time") if trends else None
    if not t:
        return False
    try:
        lag = abs(trading_minute(now_hhmm) - trading_minute(t))
    except (ValueError, TypeError):
        return False
    return lag > max_lag_min


def minutes_in_day(now: datetime | None = None) -> int:
    """当前时间换算为"自零点起分钟数", 供时点系数表/早盘门槛比较用."""
    now = now or datetime.now()
    return now.hour * 60 + now.minute


def effective_trade_date(now: datetime | None = None) -> str:
    """当前"有效交易日"(YYYY-MM-DD): 实时分时/买卖点应归属的那一天。

    - 工作日且已开盘(>=09:30): 今天(当日盘中/盘后, 数据就是今天的)。
    - 工作日开盘前(<09:30): 上一交易日(此时实时分时返回的是上一交易日那段)。
    - 周末/非工作日(含法定节假日): 最近一个工作日(如周末/节假日复盘看到的是上一交易日那段)。
    用于让买卖点查询的日期与分时图实际显示的那一段对齐(修过夜/周末复盘买点不显示)。
    is_workday 已剔法定节假日, 节假日会往前落到上一交易日。
    """
    from datetime import time as _dtime, timedelta
    now = now or datetime.now()
    if is_workday(now) and now.time() >= _dtime(9, 30):
        return now.date().isoformat()
    d = now.date()
    if is_workday(now):          # 工作日但开盘前 → 从昨天往前找
        d = d - timedelta(days=1)
    for _ in range(15):          # 往前找最近交易日(跳过周末+法定节假日, 长假留余量)
        if is_workday(datetime(d.year, d.month, d.day)):
            return d.isoformat()
        d = d - timedelta(days=1)
    return now.date().isoformat()
