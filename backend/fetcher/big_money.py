"""大单成交分析 - v1.7.x.

从东方财富分笔成交接口 (details/get) 过滤当日 ≥ 阈值的笔级大单, 区分主动买/卖.
给"大单异动"信号或主力资金分析使用. 60s 进程缓存.
"""
import logging
import time

from backend.fetcher.http_client import EM_HEADERS, _get_client

logger = logging.getLogger(__name__)

_big_orders_cache: dict[str, tuple[float, dict]] = {}
BIG_ORDERS_TTL = 60


async def get_big_orders_today(code: str, threshold_yuan: float = 15_000_000) -> dict | None:
    """获取个股当日 ≥ 阈值金额的笔级大单 (主动买/主动卖).

    Returns: {
      code, threshold, total_ticks,
      big_buys: [{time, price, hands, amount}, ...],
      big_sells: [...],
      big_buy_count, big_sell_count,
      big_buy_amount, big_sell_amount,
      net_big_amount,
    }
    失败返回 None.
    """
    if not code:
        return None

    cache_key = f"{code}_{threshold_yuan}"
    now = time.time()
    cached = _big_orders_cache.get(cache_key)
    if cached and now - cached[0] < BIG_ORDERS_TTL:
        return cached[1]

    prefix = "1" if code.startswith(("6", "9")) else "0"
    secid = f"{prefix}.{code}"
    url = (f"https://push2.eastmoney.com/api/qt/stock/details/get"
           f"?secid={secid}&pos=0&end=0"
           f"&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55")

    client = _get_client()
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
        details = data.get("data", {}).get("details", []) if data.get("data") else []
    except Exception as e:
        logger.warning(f"[big_orders] {code} 取数失败: {e}")
        return None

    if not details:
        return None

    big_buys, big_sells = [], []
    for line in details:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            t = parts[0]
            price = float(parts[1])
            hands = int(parts[2])
            bs = parts[4]  # f55: 1=主动买, 2=主动卖, 4=集合竞价
        except (ValueError, IndexError):
            continue
        amount = price * hands * 100
        if amount < threshold_yuan:
            continue
        item = {"time": t, "price": price, "hands": hands, "amount": amount}
        if bs == "1":
            big_buys.append(item)
        elif bs == "2":
            big_sells.append(item)

    buy_sum = sum(b["amount"] for b in big_buys)
    sell_sum = sum(s["amount"] for s in big_sells)
    result = {
        "code": code, "threshold": threshold_yuan, "total_ticks": len(details),
        "big_buys": big_buys, "big_sells": big_sells,
        "big_buy_count": len(big_buys), "big_sell_count": len(big_sells),
        "big_buy_amount": buy_sum, "big_sell_amount": sell_sum,
        "net_big_amount": buy_sum - sell_sum,
    }
    _big_orders_cache[cache_key] = (now, result)
    return result
