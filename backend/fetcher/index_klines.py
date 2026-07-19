# -*- coding: utf-8 -*-
"""指数 5 分钟 K 线抓取 — 新浪源 (v1.7.692).

为什么是新浪(实测选型, 2026-07-19 从生产服务器逐一探测):
  东财 push2his  : 生产 IP 被封(连接直接被断); 本机可通但频率稍高即限流。且 lmt=10000
                   实测只返回 1488 根(≈31 交易日) —— 免费源普遍只留约一个月分钟数据。
  腾讯 ifzq      : 服务器与本机均 DNS 不通, 未能验证。
  通达信(tdxpy)  : 公开服务器列表已失效, 连上后日线/分钟均返回 0 根; 库停更约 2 年。
  baostock       : 支持个股 5 分钟(2020-01-02 起)但**不支持指数分钟线**(实测 0 根,
                   非报错; 而同代码 frequency='d' 指数日线正常) —— 故个股走 baostock、
                   指数走新浪, 两条链路。
  新浪           : 生产服务器直连可用, datalen 上限 1023 根(≈21 交易日, 滚动窗口)。
                   历史深度不够回填 2 年, 但每日增量(每天 48 根)绰绰有余 → 选它。

代码格式: 一律用**带市场前缀**的 symbol(sh000001/sz399001/sz399006)。
  裸码会撞个股 —— cfzy_sys_kline_5m 里的 "000001" 实为平安银行(后复权收盘 1346),
  而非上证指数; "000688"=国城矿业、"000905"=厦门港务。此坑已在 0719 排查中确认。
"""

import json
import logging

import httpx

logger = logging.getLogger(__name__)

_URL = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_k/"
        "CN_MarketDataService.getKLineData")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

# 要跟踪的指数(带市场前缀; name 仅用于日志/展示)
INDEXES: list[tuple[str, str]] = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
]

SINA_MAX_DATALEN = 1023      # 新浪硬上限, 再大也只返回这么多


class IndexKlineError(Exception):
    """指数K线抓取失败(网络/解析/空数据)。"""


def parse_sina_klines(text: str) -> list[dict]:
    """新浪 jsonp 文本 → [{dt, open, high, low, close, volume}] (按时间升序)。

    返回空列表代表"取到了但没数据"(非交易日等), 由调用方决定是否算失败。
    """
    s, e = text.find("("), text.rfind(")")
    body = text[s + 1:e] if (s >= 0 and e > s) else text
    try:
        rows = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise IndexKlineError("新浪返回无法解析为 JSON")
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for r in rows:
        try:
            out.append({
                "dt": str(r["day"])[:19],          # "2026-07-17 15:00:00"
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(float(r.get("volume") or 0)),
            })
        except (KeyError, TypeError, ValueError):
            continue          # 单根脏数据跳过, 不拖垮整批
    out.sort(key=lambda x: x["dt"])
    return out


async def fetch_index_5m(symbol: str, datalen: int = 64,
                         client: httpx.AsyncClient | None = None) -> list[dict]:
    """取单个指数最近 datalen 根 5 分钟 K 线(按时间升序)。

    datalen 上限 1023(新浪硬限); 日常增量取 64 根即可覆盖一个交易日(48 根)还有富余。
    """
    n = max(1, min(int(datalen), SINA_MAX_DATALEN))
    params = {"symbol": symbol, "scale": 5, "ma": "no", "datalen": n}
    own = client is None
    c = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), trust_env=False)
    try:
        r = await c.get(_URL, params=params, headers=_HEADERS)
        if r.status_code != 200:
            raise IndexKlineError(f"{symbol} HTTP {r.status_code}")
        return parse_sina_klines(r.text)
    except httpx.HTTPError as e:
        raise IndexKlineError(f"{symbol} 请求失败: {type(e).__name__}: {e}") from e
    finally:
        if own:
            await c.aclose()
