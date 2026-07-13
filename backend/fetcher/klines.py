"""日 K 线获取 - 双源 fallback + DB 缓存 - v1.7.x.

新浪为主, 同花顺兜底, DB 缓存最终降级.
失败链: sina(1次) → ths(1次) → cached.
东财已移除(v1.7.610): prod IP 被封, 请求必败且空耗连接池。
"""
import asyncio
import json
import logging
import time

import pandas as pd

from backend.fetcher.codes import _code_to_sina, _code_to_ths, _normalize_code
from backend.fetcher.http_client import HEADERS, THS_HEADERS, _get_client

logger = logging.getLogger(__name__)


def _parse_jsonp(text: str) -> dict:
    start = text.index("(") + 1
    end = text.rindex(")")
    return json.loads(text[start:end])


async def _kline_sina(code: str, days: int) -> pd.DataFrame:
    sina_sym = _code_to_sina(code)
    url = (
        f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
        f"CN_MarketDataService.getKLineData"
        f"?symbol={sina_sym}&scale=240&ma=no&datalen={days}"
    )
    client = _get_client()
    try:
        resp = await client.get(url, headers=HEADERS)
        text = resp.text
    except Exception as e:
        logger.error(f"Sina kline fetch failed for {code}: {e}")
        return pd.DataFrame()

    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return pd.DataFrame()
    try:
        data = json.loads(text[start + 1:end])
    except json.JSONDecodeError:
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df = df.rename(columns={"day": "date"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    needed = ["date", "open", "high", "low", "close", "volume"]
    for col in needed:
        if col not in df.columns:
            return pd.DataFrame()
    return df[needed].dropna().reset_index(drop=True)


async def _kline_ths(code: str, days: int) -> pd.DataFrame:
    ths_code = _code_to_ths(code)
    url = f"http://d.10jqka.com.cn/v6/line/{ths_code}/01/last.js"
    client = _get_client()
    try:
        resp = await client.get(url, headers=THS_HEADERS)
        data = _parse_jsonp(resp.text)
        raw = data.get("data", "")
        if not raw:
            return pd.DataFrame()
        records = raw.split(";")
        rows = []
        for r in records:
            parts = r.split(",")
            if len(parts) >= 6:
                rows.append({
                    "date": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}",
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": float(parts[5]),
                })
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows[-days:])
    except Exception as e:
        logger.error(f"THS kline fetch failed for {code}: {e}")
        return pd.DataFrame()


def _cache_today() -> str:
    """今日日期字符串; 独立成函数供测试注入。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


async def _save_kline_cache(code: str, df: pd.DataFrame):
    try:
        from backend.models import repository
        from backend.services.intraday_estimator import is_intraday
        # v1.7.384: 盘中拉到的日K常带"今日未收盘bar"(量价都是半截数据), 不得作为正式日线落库
        # — 否则降级读缓存的一侧会把半截bar当成完整日线。今日bar等收盘后的拉取再落。
        if is_intraday():
            today = _cache_today()
            df = df[df["date"].astype(str).str.slice(0, 10) < today]
            if df.empty:
                return
        rows = [
            (row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"])
            for _, row in df.iterrows()
        ]
        await repository.cache_klines(code, rows)
    except Exception as e:
        logger.debug(f"[kline_cache] 写入缓存失败({code}): {e}")


async def _load_kline_cache(code: str, days: int = 150) -> pd.DataFrame:
    try:
        from backend.models import repository
        rows = await repository.get_cached_klines(code, days)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={"trade_date": "date"})
        needed = ["date", "open", "high", "low", "close", "volume"]
        for col in needed:
            if col not in df.columns:
                return pd.DataFrame()
        df = df[needed]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna().reset_index(drop=True)
    except Exception as e:
        logger.debug(f"[kline_cache] 读取缓存失败({code}): {e}")
        return pd.DataFrame()


# 进程内短 TTL 缓存: 日K一天只变一根, 盘中最后一根的实时价由行情快照(get_realtime_quotes)单独提供并由
# scanner 回填, 故 90s 内复用缓存不影响信号灵敏度。此前 scanner 每轮(6s)对整池重拉日K, 占满 httpx/事件循环,
# 把 3s 的实时行情刷新挤到超时被丢 → 全池行情冻结。加这层缓存后日K请求量锐降, 行情刷新不再被饿死。
_MEM_TTL_SECONDS = 90
_mem_cache: dict[str, tuple[float, pd.DataFrame]] = {}


async def get_daily_kline(code: str, days: int = 150, prefer_cache: bool = False) -> pd.DataFrame:
    """v1.7.74 主备倒置: 新浪→东财→同花顺→DB 缓存. prefer_cache 模式下缓存够长直接用.

    外层加 90s 进程内缓存(按 code+days), 避免高频扫描重复网络拉取拖垮实时行情刷新。
    """
    code = _normalize_code(code)
    key = f"{code}:{days}"
    now = time.monotonic()
    hit = _mem_cache.get(key)
    if hit and (now - hit[0]) < _MEM_TTL_SECONDS and not hit[1].empty:
        return hit[1].copy()

    df = await _fetch_daily_kline(code, days, prefer_cache)
    if df is not None and not df.empty:
        _mem_cache[key] = (now, df.copy())
    return df


def _is_individual_stock(code: str) -> bool:
    """6位纯数字 = A股个股(沪深主板/创业板/科创/北交所); 板块指数(BK*)、带市场前缀的
    指数代码(sh000001 等)均非个股 → False。日K源健康埋点只统计个股源故障, 见调用点说明。"""
    return bool(code) and code.isdigit() and len(code) == 6


async def _fetch_daily_kline(code: str, days: int, prefer_cache: bool) -> pd.DataFrame:
    """实际三源 fallback 拉取(无内存缓存); 由 get_daily_kline 包一层 TTL 缓存调用。"""
    if prefer_cache:
        cached = await _load_kline_cache(code, days)
        if not cached.empty and len(cached) >= min(days, 60):
            return cached

    try:
        df = await _kline_sina(code, days)
        if not df.empty:
            await _save_kline_cache(code, df)
            return df
    except Exception as e:
        logger.warning(f"[kline] 新浪失败({code}): {e}")
    logger.warning(f"[kline] 新浪返回空({code}), 切换同花顺")

    df = await _kline_ths(code, days)
    if not df.empty:
        logger.info(f"[kline] 同花顺兜底成功({code})")
        await _save_kline_cache(code, df)
        return df
    df = await _load_kline_cache(code, days)
    if not df.empty:
        logger.info(f"[kline] 使用数据库缓存({code}), 共{len(df)}根K线")
    # 源健康埋点: 走到这里=新浪/东财/同花顺三个网络源全失败, 才是真正的"日K源挂".
    # 仅对个股(6位纯数字)计数 — 板块指数(BK*)/带前缀指数等非个股, 个股日K接口本就不提供它们
    # (新浪返空/东财断/同花顺404 是预期), 计入会误报(盘后回填板块信号曾刷出44次假"源挂"预警)。
    if _is_individual_stock(code):
        try:
            from backend.services import data_health
            data_health.report("kline_network_down", detail=f"最近: {code}")
        except Exception:
            pass
    return df


# ── 指数日K (v1.7.386, 自选股"对标指数"K线叠加用) ────────────────────────────
# 指数代码必须带市场前缀(sh000001/sz399006), 不能走 _normalize_code(会剥前缀后按个股猜市场)。
# 新浪 CN_MarketDataService 直接吃带前缀 symbol。
# 复用进程内 90s TTL 缓存(key 带 idx: 前缀隔离, 不与个股冲突), 不写个股 kline_cache 表。


async def get_index_kline(symbol: str, days: int = 250) -> pd.DataFrame:
    symbol = (symbol or "").strip().lower()
    if not symbol or len(symbol) < 8:
        return pd.DataFrame()
    key = f"idx:{symbol}:{days}"
    now = time.monotonic()
    hit = _mem_cache.get(key)
    if hit and (now - hit[0]) < _MEM_TTL_SECONDS and not hit[1].empty:
        return hit[1].copy()

    df = await _index_kline_sina_raw(symbol, days)
    if not df.empty:
        _mem_cache[key] = (now, df.copy())
    return df


async def _index_kline_sina_raw(symbol: str, days: int) -> pd.DataFrame:
    url = (
        f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
        f"CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen={days}"
    )
    client = _get_client()
    try:
        resp = await client.get(url, headers=HEADERS)
        text = resp.text
    except Exception as e:
        logger.warning(f"[index_kline] 新浪失败({symbol}): {e}")
        return pd.DataFrame()
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        return pd.DataFrame()
    try:
        data = json.loads(text[start + 1:end])
    except json.JSONDecodeError:
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).rename(columns={"day": "date"})
    needed = ["date", "open", "high", "low", "close", "volume"]
    for col in needed:
        if col not in df.columns:
            return pd.DataFrame()
    for col in needed[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[needed].dropna().reset_index(drop=True)
