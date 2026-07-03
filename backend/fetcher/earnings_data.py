"""业绩预告 + 预约披露时间表 数据抓取 (东财 datacenter) — v1.7.573.

两个数据源(生产实测 datacenter.eastmoney.com 可达, 未随行情 push2 一起被封):
  业绩预告  RPT_PUBLIC_OP_NEWPREDICT — 预告类型/变动幅度/公告日, 供「预增榜」(进攻·快进快出)
  预约披露  RPT_PUBLIC_BS_APPOIN     — 定期报告预约/实际披露日, 供「财报披露日历」(防御·二元事件避险)

回测背书(bt: yjyg_backtest 全市场11340事件): 好业绩涨在公告那一下、之后D+2→D+5阴跌(利好兑现),
唯「预增」埋伏D-6→D+2 约+2.3%/胜率61%有小edge; 利空跌幅(-2%~-2.9%)远大于利好涨幅 → 防御价值更高。
"""
import logging

from backend.fetcher.http_client import EM_HEADERS, _get_client

logger = logging.getLogger(__name__)

_BASE = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# 预告类型分组(与回测口径一致)
GOOD_TYPES = {"预增", "略增", "扭亏", "减亏", "续盈", "预盈"}
BAD_TYPES = {"预减", "略减", "首亏", "增亏", "续亏", "预亏"}


def _d10(v) -> str:
    """'2025-06-19 00:00:00' → '2025-06-19'; None/空 → ''。"""
    return str(v)[:10] if v else ""


async def _fetch_all(report_name: str, report_date: str, sort: str,
                     extra_filter: str = "", page_size: int = 500) -> list[dict]:
    """分页拉某 reportName 在某 REPORT_DATE 的全部行。datacenter 无数据时 result 为 falsy。"""
    client = _get_client()
    out: list[dict] = []
    page = 1
    sort_types = ",".join(["-1"] * len(sort.split(",")))   # 东财要求 sortTypes 数量与 sortColumns 一致
    while True:
        filt = f"(REPORT_DATE='{report_date}'){extra_filter}"
        url = (f"{_BASE}?sortColumns={sort}&sortTypes={sort_types}&pageSize={page_size}&pageNumber={page}"
               f"&reportName={report_name}&columns=ALL&filter={filt}")
        try:
            resp = await client.get(url, headers=EM_HEADERS)
            js = resp.json()
        except Exception as e:
            logger.warning(f"[earnings] 抓取失败 {report_name} p{page}: {e}")
            break
        res = js.get("result") if isinstance(js, dict) else None
        data = (res or {}).get("data") or []
        out.extend(data)
        pages = int((res or {}).get("pages") or 1)
        if page >= pages or not data:
            break
        page += 1
        if page > 40:   # 安全上限(全市场约2-3k家/500页大小 → ≤6页, 40是兜底)
            break
    return out


async def fetch_earnings_forecasts(report_date: str, notice_date: str | None = None) -> list[dict]:
    """某报告期业绩预告(可按公告日过滤当日新增)。

    返回 [{code, name, notice_date, report_date, predict_type, amp_lower, amp_upper,
            content, group}], group ∈ 利好/利空/中性。
    """
    extra = f"(NOTICE_DATE='{notice_date}')" if notice_date else ""
    rows = await _fetch_all("RPT_PUBLIC_OP_NEWPREDICT", report_date,
                            "NOTICE_DATE,SECURITY_CODE", extra)
    out = []
    seen = set()
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code or code in seen:
            continue
        seen.add(code)   # 一司一期取首条(datacenter 已按最新在前)
        ptype = (r.get("PREDICT_TYPE") or "").strip()
        group = "利好" if ptype in GOOD_TYPES else ("利空" if ptype in BAD_TYPES else "中性")
        out.append({
            "code": code,
            "name": (r.get("SECURITY_NAME_ABBR") or "").strip(),
            "notice_date": _d10(r.get("NOTICE_DATE")),
            "report_date": _d10(r.get("REPORT_DATE")),
            "predict_type": ptype,
            "amp_lower": r.get("ADD_AMP_LOWER"),
            "amp_upper": r.get("ADD_AMP_UPPER"),
            "content": (r.get("PREDICT_CONTENT") or "").strip(),
            "group": group,
        })
    return out


async def fetch_disclosure_calendar(report_date: str) -> list[dict]:
    """某报告期定期报告预约/实际披露时间表。

    返回 [{code, name, report_year, report_type, appoint_date, actual_date}],
    appoint_date=预约(或最新变更)披露日, actual_date=已实际披露日(未披露则空)。
    """
    rows = await _fetch_all("RPT_PUBLIC_BS_APPOIN", report_date, "SECURITY_CODE")
    out = []
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if not code:
            continue
        # 预约日取最新变更(三次变更 > 二次 > 一次 > 首次)
        appoint = (_d10(r.get("THIRD_CHANGE_DATE")) or _d10(r.get("SECOND_CHANGE_DATE"))
                   or _d10(r.get("FIRST_CHANGE_DATE")) or _d10(r.get("FIRST_APPOINT_DATE")))
        out.append({
            "code": code,
            "name": (r.get("SECURITY_NAME_ABBR") or "").strip(),
            "report_year": str(r.get("REPORT_YEAR") or ""),
            "report_type": str(r.get("REPORT_TYPE") or ""),   # 东财: 1一季/2半年/3三季/4年报(以实际为准)
            "appoint_date": appoint,
            "actual_date": _d10(r.get("ACTUAL_PUBLISH_DATE")),
        })
    return out
