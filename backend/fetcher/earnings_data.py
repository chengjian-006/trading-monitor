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

# 净利变动"标准口径"指标(东财每家按财务指标返回多行, 预增榜取归母净利润这行)
PARENT_NP = "归属于上市公司股东的净利润"
DEDUCT_NP = "扣除非经常性损益后的净利润"


def _has_amp(r: dict) -> bool:
    return r.get("ADD_AMP_LOWER") is not None or r.get("ADD_AMP_UPPER") is not None


def pick_forecast_row(rows: list[dict]) -> dict | None:
    """东财每家按财务指标返回多行(每股收益/扣非/营收/归母净利润), 选"净利变动"标准口径行。

    优先级: 归母净利润(有幅度) > 扣非净利润(有幅度) > 任一有幅度 > 归母/扣非行(无幅度) > 首行。
    避免无脑取第一行——它可能是"每股收益"行, 东财对EPS不给同比幅度 → 净利变动列空成"—"(中金岭南bug)。
    """
    if not rows:
        return None
    for target in (PARENT_NP, DEDUCT_NP):
        hit = [r for r in rows if (r.get("PREDICT_FINANCE") or "").strip() == target and _has_amp(r)]
        if hit:
            return hit[0]
    with_amp = [r for r in rows if _has_amp(r)]
    if with_amp:
        return with_amp[0]
    for target in (PARENT_NP, DEDUCT_NP):     # 都无幅度: 优先归母行(content更完整)
        hit = [r for r in rows if (r.get("PREDICT_FINANCE") or "").strip() == target]
        if hit:
            return hit[0]
    return rows[0]


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


async def fetch_earnings_forecasts(report_date: str, notice_date: str | None = None,
                                   notice_since: str | None = None) -> list[dict]:
    """某报告期业绩预告(可按公告日过滤: notice_date 精确当日 / notice_since 回看至该日起)。

    notice_since 用于回看窗(修"周五盘后/周末发的预告掉进裂缝"bug): 扫描回看最近几天,
    已推过的靠 pushed_at 去重, 不会重复推。notice_date 与 notice_since 二选一(后者优先)。

    每家按财务指标多行 → pick_forecast_row 取归母净利润标准口径行(修"净利变动为空"bug)。
    返回 [{code, name, notice_date, report_date, predict_type, amp_lower, amp_upper, content, group}]。
    """
    if notice_since:
        extra = f"(NOTICE_DATE>='{notice_since}')"
    elif notice_date:
        extra = f"(NOTICE_DATE='{notice_date}')"
    else:
        extra = ""
    rows = await _fetch_all("RPT_PUBLIC_OP_NEWPREDICT", report_date,
                            "NOTICE_DATE,SECURITY_CODE", extra)
    # 按 code 分组(东财每家多行/每指标一行), 每家选"净利变动"标准口径行
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        if code:
            by_code.setdefault(code, []).append(r)
    out = []
    for code, group_rows in by_code.items():
        r = pick_forecast_row(group_rows)
        if not r:
            continue
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
