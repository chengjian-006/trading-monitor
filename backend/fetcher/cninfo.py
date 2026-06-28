# -*- coding: utf-8 -*-
"""巨潮资讯网(cninfo)公告抓取 — 风险公告监控数据源(v1.7.x).

为什么用巨潮: 证监会指定信披平台, 公告最权威最全, 且不在东财(生产IP被封)范围内。

  get_org_id_map        — code -> orgId 字典(巨潮查询必需), 进程内按日缓存
  query_announcements   — 查某票最近 N 天公告标题(翻页+去重), 返回结构化列表

注: 公开只读接口, 无需 cookie。orgId 形如 SZ=9900xxxxxx / SH=gsshxxxxxxx / BJ=gsbjxxxxxxx。
"""
import asyncio
import logging
from datetime import datetime, timedelta

from backend.fetcher.http_client import _get_client

logger = logging.getLogger(__name__)

_STOCK_JSON = "http://www.cninfo.com.cn/new/data/szse_stock.json"   # 含沪深京全A
_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 进程内缓存: {code: orgId} + 缓存日期(每日刷新一次, 新股/改名才变动)
_org_map: dict[str, str] = {}
_org_map_day: str = ""


def _plate(code: str) -> str:
    """巨潮 plate/column 参数: 6→沪市, 0/3→深市, 4/8→北交所。"""
    if code[:1] == "6":
        return "sh"
    if code[:1] in ("4", "8"):
        return "bj"
    return "sz"


async def get_org_id_map(force: bool = False) -> dict[str, str]:
    """拉巨潮股票字典, 返回 code->orgId。进程内按自然日缓存, 失败保留旧缓存。"""
    global _org_map, _org_map_day
    today = datetime.now().strftime("%Y-%m-%d")
    if not force and _org_map and _org_map_day == today:
        return _org_map

    client = _get_client()
    for attempt in range(1, 4):
        try:
            resp = await client.get(_STOCK_JSON, headers={"User-Agent": _UA})
            data = resp.json()
            rows = data.get("stockList", data) if isinstance(data, dict) else data
            new_map = {r.get("code"): r.get("orgId")
                       for r in rows if r.get("code") and r.get("orgId")}
            if new_map:
                _org_map = new_map
                _org_map_day = today
                logger.info(f"[cninfo] orgId 字典刷新 {len(new_map)} 只")
            return _org_map
        except Exception as e:
            logger.warning(f"[cninfo] 拉股票字典第{attempt}次失败: {e}")
            if attempt < 3:
                await asyncio.sleep(2)
    return _org_map   # 失败兜底返回旧缓存(可能为空)


async def query_announcements(code: str, org_id: str, days: int = 7,
                              max_pages: int = 6) -> list[dict]:
    """查某票最近 days 天公告。返回 [{ann_id,title,time_ms,date,url}], 按公告ID去重。"""
    plate = _plate(code)
    end = datetime.now()
    start = end - timedelta(days=days)
    se_date = f"{start:%Y-%m-%d}~{end:%Y-%m-%d}"
    client = _get_client()

    seen: dict = {}
    for page in range(1, max_pages + 1):
        payload = {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": "30",
            "pageNum": str(page),
            "column": plate,
            "plate": plate,
            "seDate": se_date,
            "isHLtitle": "false",
        }
        try:
            resp = await client.post(_QUERY_URL, data=payload, headers={
                "User-Agent": _UA,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
            })
            j = resp.json()
        except Exception as e:
            logger.warning(f"[cninfo] {code} 第{page}页公告拉取失败: {e}")
            break
        batch = j.get("announcements") or []
        if not batch:
            break
        for a in batch:
            aid = a.get("announcementId")
            if not aid or aid in seen:
                continue
            adjunct = a.get("adjunctUrl") or ""
            seen[aid] = {
                "ann_id": str(aid),
                "title": a.get("announcementTitle", ""),
                "time_ms": a.get("announcementTime", 0) or 0,
                "date": datetime.fromtimestamp((a.get("announcementTime", 0) or 0) / 1000).strftime("%Y-%m-%d"),
                "url": f"http://static.cninfo.com.cn/{adjunct}" if adjunct else "",
            }
        if not j.get("hasMore"):
            break
    return list(seen.values())


# ── 财务三表(二期: 财务红旗打分) ──────────────────────────────────────────
# data20 财务接口: 参数 scode + sign=1(合并报表标志), 一次返回最近5年"年报"口径数据。
# records[0].year = [{index:科目名, '2025':值, '2024':值, ...}], 值单位万元。
_FIN_BASE = "http://www.cninfo.com.cn/data20/financialData"
_FIN_METHODS = {
    "income": "getIncomeStatement",        # 利润表: 营业总收入/归属母公司净利润...
    "cashflow": "getCashFlowStatement",    # 现金流量表: 经营/投资/筹资活动现金流量净额
    "balance": "getBalanceSheets",         # 资产负债表(摘要): 货币资金/总资产/总负债/所有者权益/未分配利润
}


def _parse_year_group(records: list) -> dict[str, dict[str, float]]:
    """records[0].year → {科目名: {年份: 值}}。"""
    rows = records[0].get("year", []) if records else []
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        idx = row.get("index")
        if not idx:
            continue
        out[idx] = {k: v for k, v in row.items() if k != "index"}
    return out


async def get_financial_statements(code: str) -> dict[str, dict]:
    """拉某票三大报表(年报口径近5年)。返回 {'income':{科目:{年:值}}, 'cashflow':..., 'balance':...}。
    任一表拉取失败 → 该表为空 dict(调用方按缺数据处理, 不报红旗)。"""
    client = _get_client()
    result: dict[str, dict] = {"income": {}, "cashflow": {}, "balance": {}}
    for key, method in _FIN_METHODS.items():
        try:
            resp = await client.get(f"{_FIN_BASE}/{method}",
                                    params={"scode": code, "sign": "1"},
                                    headers={"User-Agent": _UA})
            recs = (resp.json().get("data") or {}).get("records") or []
            result[key] = _parse_year_group(recs)
        except Exception as e:
            logger.warning(f"[cninfo] {code} {method} 财务表拉取失败: {e}")
    return result
