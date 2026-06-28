"""涨停/跌停数据获取 — 短线情绪盯盘 P1。

主源 同花顺 data.10jqka.com.cn limit_up_pool — 一个接口同时返回:
  - info: 涨停股列表 (连板梯队用 high_days)
  - limit_up_count.today / limit_down_count.today: {num(封板), history_num(曾涨停), rate(封板成功率), open_num(炸板)}
  与用户用的同花顺远航版口径一致, 且非东财(东财行情接口对生产 IP 封禁, 见 [[avoid-eastmoney-api]])。
备源 东财 push2ex getTopicZTPool/ZBPool/DTPool — 同花顺失败时兜底 (无曾涨停/官方封板率, 封板率由 refresher 自算)。

归一化输出:
  {
    "source": "ths" | "eastmoney",
    "limit_up_count":     int,        # 收盘封板涨停数
    "limit_up_history":   int|None,   # 当日曾涨停 (同花顺 history_num)
    "limit_down_count":   int|None,   # 收盘封板跌停数
    "limit_down_history": int|None,   # 当日曾跌停
    "broken_board_count": int|None,   # 炸板 (涨停开板数 open_num)
    "seal_rate":          float|None, # 封板成功率(0-1); 同花顺官方 rate, 东财源为 None
    "boards":  [{"code","name","height","streak_label","reason","pct","open_times"}],  # 涨停股 → 梯队/连板详单
    "codes":   [code, ...],           # 涨停股代码 (昨涨停今日溢价用)
  }

注: THS 的 height 此处为"板数 M"(N天M板 的 M), 非真连板数 —— 真连板(连续涨停)由 emotion_refresher
按日K线倒数得出并覆盖。streak_label 保留同花顺原始描述("N天M板"/"N连板"/"首板")做个股标签。
"""
import logging
import re
import time

from backend.fetcher.http_client import EM_HEADERS, THS_HEADERS, _get_client

logger = logging.getLogger(__name__)

_EM_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _num(x):
    """宽松转 float, 失败/空 → None (同花顺/东财涨幅字段可能是字符串或缺失)。"""
    if x is None or x == "":
        return None
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


def _int0(x):
    """宽松转 int, 失败/空 → 0 (炸板次数)。"""
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _parse_board_count(high_days, high_days_value=None) -> int:
    """同花顺连板字段 → 板数 M。'首板'→1 / 'N连板'→N / 'N天M板'→M / 数字→该数。

    优先 high_days_value(同花顺打包: 高16位=板数M, 低16位=天数N, 如 '4天3板'=0x00030004);
    否则取字符串里最后一个数字组('N连板'末位=N, 'N天M板'末位=M)。
    注: 这是"板数"非"连板数"; 断板股(N天M板, M<N)的真连板由调用方按K线另算。
    """
    if high_days_value:
        try:
            m = int(high_days_value) >> 16
            if m > 0:
                return m
        except (TypeError, ValueError):
            pass
    if high_days is None:
        return 1
    s = str(high_days).strip()
    if not s or s == "首板":
        return 1
    nums = re.findall(r"\d+", s)
    return int(nums[-1]) if nums else 1


async def _get_ths(date: str) -> dict | None:
    """同花顺主源: limit_up_pool 一次拿涨停列表 + 涨/跌停官方汇总。"""
    url = (f"https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
           f"?page=1&limit=200&field=199112,10,9001,330323,330324,330325,"
           f"9002,330329,133971,133970,1968584,3475914,9003,9004"
           f"&order_field=330324&order_type=0&date={date}")
    client = _get_client()
    try:
        resp = await client.get(url, headers=THS_HEADERS)
        if resp.status_code >= 400:
            logger.warning(f"[limit_pool] 同花顺 HTTP {resp.status_code}")
            return None
        data = (resp.json() or {}).get("data")
        if not isinstance(data, dict):
            return None
        luc = (data.get("limit_up_count") or {}).get("today") or {}
        ldc = (data.get("limit_down_count") or {}).get("today") or {}
        if not luc:
            return None  # 无汇总块 → 交备源
        pool = data.get("info") or []
        boards, codes = [], []
        for it in pool:
            code = str(it.get("code") or "")
            if not code:
                continue
            hd = it.get("high_days")
            boards.append({"code": code, "name": it.get("name") or "",
                           "height": _parse_board_count(hd, it.get("high_days_value")),  # 板数M(占位, emotion 用真连板覆盖)
                           "streak_label": (str(hd).strip() if hd not in (None, "") else "首板"),  # 同花顺原始连板描述, 做个股标签
                           "reason": it.get("reason_type") or "",   # 涨停题材, 供题材热度聚合 + 连板梯队详单
                           "pct": _num(it.get("change_rate")),       # 当日涨幅(%)
                           "open_times": _int0(it.get("open_num"))})  # 炸板次数(0=未开板, >0=反复)
            codes.append(code)
        return {
            "source": "ths",
            "limit_up_count": luc.get("num"),
            "limit_up_history": luc.get("history_num"),
            "limit_down_count": ldc.get("num"),
            "limit_down_history": ldc.get("history_num"),
            "broken_board_count": luc.get("open_num"),
            "seal_rate": luc.get("rate"),   # 同花顺官方封板成功率 (0-1)
            "boards": boards,
            "codes": codes,
        }
    except Exception as e:
        logger.warning(f"[limit_pool] 同花顺异常: {e!r}")
        return None


async def _fetch_em_pool(endpoint: str, date: str, sort: str = "fbt:asc") -> list[dict] | None:
    """备源: 拉东财某个池 (空池返回 [], 失败返回 None)。
    跌停池股票无 fbt 字段, 必须用 fund 排序, 否则恒为 0。"""
    sort_q = sort.replace(":", "%3A")
    url = (f"https://push2ex.eastmoney.com/{endpoint}"
           f"?ut={_EM_UT}&dpt=wz.ztzt&Pageindex=0&pagesize=400"
           f"&sort={sort_q}&date={date}&_=0")
    client = _get_client()
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        if resp.status_code >= 400:
            logger.warning(f"[limit_pool] 东财 {endpoint} HTTP {resp.status_code}")
            return None
        data = resp.json()
        if data.get("rc") != 0:
            return None
        return (data.get("data") or {}).get("pool") or []
    except Exception as e:
        logger.warning(f"[limit_pool] 东财 {endpoint} 异常: {e!r}")
        return None


async def _get_eastmoney(date: str) -> dict | None:
    """备源: 东财涨停专题 (无曾涨停/官方封板率, refresher 自算封板率)。"""
    zt = await _fetch_em_pool("getTopicZTPool", date)
    if zt is None:
        return None
    zb = await _fetch_em_pool("getTopicZBPool", date)
    dt = await _fetch_em_pool("getTopicDTPool", date, sort="fund:asc")
    boards, codes = [], []
    for it in zt:
        code = str(it.get("c") or "")
        if not code:
            continue
        height = int(it.get("lbc") or (it.get("zttj") or {}).get("days") or 1)  # 东财 lbc=连板次(已是连续)
        boards.append({"code": code, "name": it.get("n") or "", "height": height,
                       "streak_label": (f"{height}连板" if height >= 2 else "首板"),
                       "reason": "",                       # 东财源无涨停题材
                       "pct": _num(it.get("zdp")),           # 东财涨跌幅
                       "open_times": 0})                     # 东财涨停池无个股级炸板次数
        codes.append(code)
    return {
        "source": "eastmoney",
        "limit_up_count": len(zt),
        "limit_up_history": None,
        "limit_down_count": len(dt) if dt is not None else None,
        "limit_down_history": None,
        "broken_board_count": len(zb) if zb is not None else None,
        "seal_rate": None,
        "boards": boards,
        "codes": codes,
    }


async def get_limit_pool(date: str) -> dict | None:
    """涨停/跌停归一化获取: 同花顺主 → 东财备 → None。date: 'YYYYMMDD'。"""
    result = await _get_ths(date)
    if result is not None:
        return result
    logger.info("[limit_pool] 同花顺主源失败, 切东财备源")
    return await _get_eastmoney(date)


# ── 进程内 TTL 缓存 (盘中高频任务共享同一份当日涨停池, 省重复外部请求) ──
# 结构: {date_key: (timestamp, result)}。仅缓存成功且非空结果, 失败/空不长缓存,
# 避免一次网络抖动导致 TTL 内全挂。按 date_key 区分防跨日返回旧数据。
_cache: dict[str, tuple[float, dict]] = {}


async def get_limit_pool_cached(date: str, ttl: int = 60) -> dict | None:
    """get_limit_pool 的进程内 TTL 缓存包装。

    命中且未过期(time.time()-ts < ttl)直接返回缓存; 否则真调 get_limit_pool 刷新。
    返回 None / 空(无 boards 且无 codes)时不写缓存。产物与 get_limit_pool 完全一致。
    """
    now = time.time()
    cached = _cache.get(date)
    if cached is not None and (now - cached[0]) < ttl:
        return cached[1]

    result = await get_limit_pool(date)
    # 失败/空结果不长缓存: 避免一次抖动污染 TTL 窗口
    if result and (result.get("boards") or result.get("codes")):
        _cache[date] = (now, result)
    return result
