"""股票人气排行 - 同花顺热榜接口 (dq.10jqka.com.cn).

v1.7.x: 全量从东方财富 stockrank 切到同花顺热榜, 东财人气接口已停用.
同花顺热榜只返回市场 TOP100(无单票全市场名次接口), 故自选池中落榜(不在 TOP100)的票
统一记 RANK_OUT_OF_TOP100, 展示层渲染为 "100名外"(不是空白, 也不当成进榜).

  _parse_ths_hot_list(payload, top_n)  — 纯解析: 热榜 JSON → 名次列表
  _fetch_popularity_rank_ths(top_n)    — 拉同花顺热榜 top_n (≤100)
  get_popularity_rank(top_n)           — 带重试的全市场榜
  get_popularity_rank_for_codes(codes) — 指定票批量, 命中给名次, 落榜给 RANK_OUT_OF_TOP100
"""
import asyncio
import logging

from backend.fetcher.http_client import _get_client

logger = logging.getLogger(__name__)

# 同花顺热榜只覆盖市场 TOP100; 自选池中落榜的票统一用此哨兵, 展示层渲染为 "100名外".
# 取大值(>100)使其在「火苗共振/人气≤N 筛选/竞价弱转强人气闸」等所有 ≤100 门前天然落到"非热门",
# 与东财时代落榜票真实名次>100 的语义一致.
RANK_OUT_OF_TOP100 = 999

_THS_HOT_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
_THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://eq.10jqka.com.cn/",
}
_THS_PARAMS = {"stock_type": "a", "type": "hour", "list_type": "normal"}


def _to_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def fmt_pop_rank(rank) -> str:
    """人气名次展示: 1..100 → '第N'; >100(含哨兵) → '100名外'; None/非法 → ''."""
    if rank is None:
        return ""
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return ""
    return f"第{r}" if r <= 100 else "100名外"


def _parse_ths_hot_list(payload: dict, top_n: int) -> list[dict]:
    """同花顺热榜 JSON → [{code, rank, rank_change, heat, name, pct_change}].

    去重 + 只取 A 股(6位纯数字) + 限 top_n. rank=order(1..100), rank_change=hot_rank_chg(正=排名上升).
    """
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(payload, dict):
        return out
    data = payload.get("data") or {}
    stock_list = data.get("stock_list") or []
    for item in stock_list:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if len(code) != 6 or not code.isdigit() or code in seen:
            continue
        seen.add(code)
        try:
            heat = float(item.get("rate") or 0)
        except (TypeError, ValueError):
            heat = 0.0
        out.append({
            "code": code,
            "rank": _to_int(item.get("order")),
            "rank_change": _to_int(item.get("hot_rank_chg")),
            "heat": heat,
            "name": item.get("name", ""),
            "pct_change": item.get("rise_and_fall", 0),
        })
        if len(out) >= top_n:
            break
    return out


async def _fetch_popularity_rank_ths(top_n: int) -> list[dict]:
    client = _get_client()
    resp = await client.get(_THS_HOT_URL, params=_THS_PARAMS, headers=_THS_HEADERS, timeout=10.0)
    return _parse_ths_hot_list(resp.json(), top_n)


async def get_popularity_rank(top_n: int = 20) -> list[dict]:
    """全市场人气榜(同花顺热榜), 3 次重试. 同花顺最多给 TOP100."""
    top_n = min(top_n, 100)
    for attempt in range(1, 4):
        try:
            result = await _fetch_popularity_rank_ths(top_n)
            if result:
                return result
        except Exception as e:
            logger.warning(f"[popularity] 第{attempt}次失败: {e}")
        if attempt < 3:
            await asyncio.sleep(2)
    return []


async def get_popularity_rank_for_codes(codes: list[str]) -> dict[str, int]:
    """指定股票批量取人气名次(同花顺热榜 TOP100).

    命中 TOP100 → 实际名次; 落榜 → RANK_OUT_OF_TOP100(展示"100名外").
    同花顺无单票全市场名次接口, 落榜票取不到精确名次, 故统一标"100名外".
    整榜拉取失败(返回空)时回 {} —— 不写哨兵, 避免把"取数失败"误标成"100名外".
    """
    top100 = await get_popularity_rank(100)
    if not top100:
        return {}
    rank_map = {item["code"]: item["rank"] for item in top100}
    return {c: rank_map.get(c, RANK_OUT_OF_TOP100) for c in codes}
