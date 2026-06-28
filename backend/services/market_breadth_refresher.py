"""全市场广度刷新 - v1.7.x.

盘后(15:35)抓全市场(剔北交所/ST/退市)日线, 算"站上MA20/MA10/MA60"个股比例, 写入
cfzy_sys_market_breadth。供前端大盘"环境温度计"参考。

注意:
- prod 库只存自选池, 没有全市场清单 → 这里从新浪 Market_Center 现拉全A列表。
- 东财在 prod 被风控, 全程走新浪。用独立 httpx client(trust_env=False)隔离, 不污染
  实时行情的 api_metrics, 也不与 3s 扫描循环抢连接。
- 并发 15, datalen=65(够算MA60)。约 4800 只, 3~6 分钟。
"""
import asyncio
import json
import logging

import httpx

from backend.models import repository
from backend.fetcher.codes import _normalize_code
from backend.services.fullmarket_klines import _parse_sina_klines

logger = logging.getLogger(__name__)

_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
_CONCURRENCY = 15
_DATALEN = 65


async def _fetch_all_symbols(client: httpx.AsyncClient) -> list[str]:
    """全A列表(新浪 hs_a 节点), 剔北交所/ST/退市. 返回新浪 symbol(如 sh600519)."""
    out: list[str] = []
    page = 1
    while page <= 90:
        params = {"page": page, "num": 80, "sort": "symbol", "asc": 1,
                  "node": "hs_a", "symbol": "", "_s_r_a": "page"}
        try:
            r = await client.get(_LIST_URL, params=params, headers=_HEADERS)
            txt = (r.text or "").strip()
            if not txt or txt == "null":
                break
            rows = json.loads(txt)
        except Exception as e:
            logger.warning(f"[breadth] 列表第{page}页失败: {e}")
            break
        if not rows:
            break
        for it in rows:
            sym = it.get("symbol", "")
            name = it.get("name", "")
            if sym.startswith("bj") or not (sym.startswith("sh") or sym.startswith("sz")):
                continue
            if "ST" in name or "退" in name or name.startswith("*"):
                continue
            out.append(sym)
        page += 1
    return out


async def _fetch_one(client, sym, sem):
    """抓单只全 OHLCV(datalen=_DATALEN). 返回 (sym, rows); 失败/过短返回 (sym, [])."""
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={_DATALEN}")
    async with sem:
        try:
            r = await client.get(url, headers=_HEADERS)
            rows = _parse_sina_klines(r.text)
            return sym, (rows if len(rows) >= 20 else [])
        except Exception:
            return sym, []


def _breadth_from_closes(closes_list: list[list]) -> dict:
    """一组个股收盘序列 → 站上 MA20/MA10/MA60 比例(%) 与有效样本数.

    口径与历史一致: 有效样本=长度≥20 的序列; 分子按"够长且收盘≥该均线"计, 分母统一用有效样本数.
    """
    a20 = a10 = a60 = tot = 0
    for closes in closes_list:
        if not closes or len(closes) < 20:
            continue
        tot += 1
        c = closes[-1]
        if c >= sum(closes[-20:]) / 20:
            a20 += 1
        if len(closes) >= 10 and c >= sum(closes[-10:]) / 10:
            a10 += 1
        if len(closes) >= 60 and c >= sum(closes[-60:]) / 60:
            a60 += 1
    if tot == 0:
        return {"ma20_ratio": 0.0, "ma10_ratio": 0.0, "ma60_ratio": 0.0, "total": 0}
    return {
        "ma20_ratio": round(a20 / tot * 100, 2),
        "ma10_ratio": round(a10 / tot * 100, 2),
        "ma60_ratio": round(a60 / tot * 100, 2),
        "total": tot,
    }


async def refresh_market_breadth():
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        trust_env=False,
    )
    try:
        symbols = await _fetch_all_symbols(client)
        if not symbols:
            logger.warning("[breadth] 全市场列表为空, 跳过")
            return
        sem = asyncio.Semaphore(_CONCURRENCY)
        fetched = await asyncio.gather(*[_fetch_one(client, s, sem) for s in symbols])

        # 广度: 口径不变, 用纯函数
        closes_list = [[row[4] for row in rows] for _, rows in fetched if rows]
        last_date = None
        for _, rows in fetched:
            if rows:
                last_date = rows[-1][0] or last_date
        b = _breadth_from_closes(closes_list)
        if b["total"] == 0:
            logger.warning("[breadth] 有效样本0, 跳过写库")
            return
        await repository.save_market_breadth(
            last_date, b["ma20_ratio"], b["ma10_ratio"], b["ma60_ratio"], b["total"])
        logger.info(f"[breadth] {last_date}: 站上MA20 {b['ma20_ratio']}% "
                    f"(MA10 {b['ma10_ratio']}% / MA60 {b['ma60_ratio']}%), 样本{b['total']}")

        # 顺带把全市场日线落库(替代原独立17:00任务). 写库受限并发8 < 连接池10, 不抢实盘查询.
        wsem = asyncio.Semaphore(8)
        kok = 0

        async def _persist(sym, rows):
            nonlocal kok
            if not rows:
                return
            async with wsem:
                try:
                    await repository.cache_klines(_normalize_code(sym), rows)
                    kok += 1
                except Exception as e:
                    logger.debug(f"[breadth] K线落库失败 {sym}: {e}")

        await asyncio.gather(*[_persist(sym, rows) for sym, rows in fetched])
        logger.info(f"[breadth] 顺带落库全市场日线 {kok} 只")
    finally:
        await client.aclose()


def breadth_band(pct):
    """站上MA20比例 → (区间标签, level, 操作含义). 阈值由全市场半年回测标定。"""
    if pct is None:
        return ("未知", "unknown", "暂无数据")
    if pct > 70:
        return ("过热", "hot", "普涨亢奋、接近高位，防回落，别追高")
    if pct >= 50:
        return ("健康强势", "strong", "进攻区，各买点可正常做")
    if pct >= 45:
        return ("中性偏强", "neutral", "可开仓（回测最优过滤线=45%）")
    if pct >= 30:
        return ("转弱调整", "weak", "谨慎，降低仓位和开仓频率")
    return ("极弱", "extreme_weak", "空仓为主，等强势股占比回升再做")
