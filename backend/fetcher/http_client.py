"""共享 httpx async client 与 API 调用打点 - v1.7.x.

TrackedAsyncClient: 自动把每次外部 API 请求按 (source, usage) 打到 api_metrics,
让 ApiHealthIndicator 等监控看到的是真实业务调用而不是模拟探活.

调用方:
  from backend.fetcher.http_client import _get_client, HEADERS, EM_HEADERS, THS_HEADERS
  client = _get_client()
  resp = await client.get(url, headers=HEADERS)
"""
import time

import httpx


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com",
}

THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://stockpage.10jqka.com.cn/",
}


def _classify_source(url: str) -> str:
    if "eastmoney.com" in url:
        return "eastmoney"
    if "sinajs.cn" in url or "sina.cn" in url or "sina.com.cn" in url:
        return "sina"
    if "10jqka.com.cn" in url:
        return "ths"
    if "qq.com" in url or "gtimg.cn" in url:
        return "tencent"
    return ""


def _classify_usage(url: str) -> str:
    # 东财: ulist.np / clist / kline / trends2 / slist / details
    if "ulist.np" in url:
        # v1.7.80: ulist.np 同时用于实时报价和弹性数据, 按 fields 参数区分
        if "f100" in url or "f21" in url or "f11," in url:
            return "stock_extra"
        return "realtime_quote"
    if "/clist/get" in url:
        # 行业板块榜 fs=m:90 ; 主板/创业/科创等股票列表
        if "fs=m:90" in url or "fs=b:" in url:
            return "sector_ranking"
        return "realtime_quote"
    if "kline/get" in url:
        return "kline"
    if "trends2/get" in url:
        if any(idx in url for idx in ("000001", "399001", "899050", "000300", "000016")):
            return "market_indices"
        return "realtime_quote"
    if "slist/get" in url:
        return "sector_ranking"
    if "details/get" in url:
        return "realtime_quote"
    # 新浪
    if "hq.sinajs.cn" in url:
        return "realtime_quote"
    if "CN_MarketDataService" in url:
        return "kline"
    if "d.10jqka.com.cn" in url and "realhead" in url:
        return "stock_extra"
    # 腾讯: 行业板块榜备源 (proxy.finance.qq.com 的 getRank)
    if "getRank" in url:
        return "sector_ranking"
    # 腾讯: 实时行情备源 (qt.gtimg.cn, sina 失败/空时切换, v1.7.647)
    if "qt.gtimg.cn" in url:
        return "realtime_quote"
    return "misc"


class TrackedAsyncClient(httpx.AsyncClient):
    """httpx.AsyncClient 子类, 自动打点真实调用结果到 api_metrics."""

    async def request(self, method, url, **kwargs):
        # 延迟 import 避免循环
        from backend.services import api_metrics
        url_str = str(url)
        source = _classify_source(url_str)
        usage = _classify_usage(url_str)
        t0 = time.time()
        try:
            resp = await super().request(method, url, **kwargs)
            if source and usage != "misc":
                ok = resp.status_code < 400
                err = "" if ok else f"HTTP {resp.status_code}"
                api_metrics.record(source, usage, ok, int((time.time() - t0) * 1000), err)
            return resp
        except Exception as e:
            if source and usage != "misc":
                api_metrics.record(source, usage, False, int((time.time() - t0) * 1000), repr(e))
            raise


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # trust_env=False: 不读取系统 HTTP_PROXY / Windows 注册表代理
        # 所有股票数据源都在国内, 走代理反而会失败
        _client = TrackedAsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
            trust_env=False,
        )
    return _client
