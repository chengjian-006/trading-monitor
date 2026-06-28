# -*- coding: utf-8 -*-
"""自选股财务红旗打分 — 黑天鹅预警二期(v1.7.x).

承接一期(公告硬信号 [[risk-announcement-monitor]]), 二期补"软财务指标": 每周从巨潮 data20
财务三表(年报口径)算6项红旗, 命中即软提醒。纯提示, 不碰任何买卖点。

数据源: 巨潮 cninfo data20(scode+sign=1, 一次返近5年年报), 生产IP实证可达。
回测: 合力泰(002217)利润现金流背离(强)+累计亏损(中)→推; 茅台无红旗→不推; 平安银行仅高杠杆(银行天然)→不推。

9项红旗(巨潮摘要6项 + 2.1新浪明细3项):
  强(任一即推): 资不抵债(所有者权益<0) / 连续亏损(归母净利连续2年<0) / 利润现金流背离(净利>0但经营现金流<0)
  中(需≥2项): 累计亏损(未分配利润<0) / 高杠杆(资产负债率>70%) / 营收断崖(营收同比降>50%)
                + 商誉占比过高(商誉/净资产>30%) / 其他应收款膨胀(/总资产>10%) / 存贷双高(货币高且有息负债高)
  明细3项走新浪 vDOWN 资产负债表(巨潮免费仅摘要无这些科目); 质押/减持困境信号扩在一期公告关键词(risk_announcement_scanner)。

入口 scan_financial_risk() 注册为每周 cron。去重靠 cfzy_biz_fin_risk.pushed_key(命中组合不变不重推)。
"""
import asyncio
import logging

from backend.core.config import load_config
from backend.fetcher.cninfo import get_financial_statements
from backend.fetcher.sina_finance import get_balance_sheet_latest
from backend.models import repository

logger = logging.getLogger(__name__)

_SEM = asyncio.Semaphore(4)   # 巨潮并发上限

# 红旗强度分值(用于展示/排序的风险分, 0~100)
_STRONG_PTS = 40
_MEDIUM_PTS = 15


def _val(table: dict, key: str, year: str | None):
    if year is None:
        return None
    return (table.get(key) or {}).get(year)


def compute_flags(income: dict, cashflow: dict, balance: dict) -> tuple[list[dict], str]:
    """返回 (flags, report_year)。flag = {label, strength('strong'/'medium'), detail}。"""
    years = sorted({y for d in income.values() for y, v in d.items() if v is not None}, reverse=True)
    if not years:
        return [], ""
    y0 = years[0]
    y1 = years[1] if len(years) > 1 else None

    np0 = _val(income, "归属母公司净利润", y0)
    np1 = _val(income, "归属母公司净利润", y1)
    ocf = _val(cashflow, "经营活动产生的现金流量净额", y0)
    equity = _val(balance, "所有者权益", y0)
    retained = _val(balance, "未分配利润", y0)
    tot_liab = _val(balance, "总负债", y0)
    tot_asset = _val(balance, "总资产", y0)
    rev0 = _val(income, "营业总收入", y0)
    rev1 = _val(income, "营业总收入", y1)

    flags: list[dict] = []
    # 强红旗
    if equity is not None and equity < 0:
        flags.append({"label": "资不抵债", "strength": "strong", "brief": f"{equity/1e4:.1f}亿",
                      "detail": f"{y0}年所有者权益{equity:.0f}万<0"})
    if np0 is not None and np1 is not None and np0 < 0 and np1 < 0:
        flags.append({"label": "连续亏损", "strength": "strong", "brief": "",
                      "detail": f"归母净利{y1}/{y0}连续2年亏损"})
    if np0 is not None and ocf is not None and np0 > 0 and ocf < 0:
        flags.append({"label": "利润现金流背离", "strength": "strong", "brief": "",
                      "detail": f"{y0}归母净利+{np0:.0f}万但经营现金流{ocf:.0f}万<0"})
    # 中红旗
    if retained is not None and retained < 0:
        flags.append({"label": "累计亏损", "strength": "medium", "brief": f"{retained/1e4:.1f}亿",
                      "detail": f"{y0}未分配利润{retained:.0f}万<0"})
    if tot_liab and tot_asset:
        dar = tot_liab / tot_asset
        if dar > 0.70:
            flags.append({"label": "高杠杆", "strength": "medium", "brief": f"{dar*100:.0f}%",
                          "detail": f"{y0}资产负债率{dar*100:.0f}%>70%"})
    if rev0 is not None and rev1 not in (None, 0):
        drop = (rev1 - rev0) / abs(rev1)
        if drop > 0.50:
            flags.append({"label": "营收断崖", "strength": "medium", "brief": f"降{drop*100:.0f}%",
                          "detail": f"营收{y1}→{y0}降{drop*100:.0f}%"})
    return flags, y0


def _bs_pick(bs: dict, *names):
    for n in names:
        if n in bs and bs[n] is not None:
            return bs[n]
    return None


def sina_detail_flags(bs: dict) -> list[dict]:
    """新浪资产负债表明细的3项红旗(均中红旗): 商誉占比/其他应收款膨胀/存贷双高。
    巨潮免费摘要表没有这些科目, 故走新浪明细补。bs 为空(拉取失败)→ 返回 []。"""
    if not bs:
        return []
    equity = _bs_pick(bs, "所有者权益(或股东权益)合计", "归属于母公司股东权益合计")
    assets = _bs_pick(bs, "资产总计")
    goodwill = _bs_pick(bs, "商誉") or 0
    other_recv = _bs_pick(bs, "其他应收款(合计)", "其他应收款") or 0
    cash = _bs_pick(bs, "货币资金") or 0
    debt = (_bs_pick(bs, "短期借款") or 0) + (_bs_pick(bs, "长期借款") or 0) \
        + (_bs_pick(bs, "应付债券") or 0)

    flags: list[dict] = []
    if goodwill and equity and equity > 0 and goodwill / equity > 0.30:
        flags.append({"label": "商誉占比过高", "strength": "medium", "brief": f"{goodwill/equity*100:.0f}%",
                      "detail": f"商誉/净资产{goodwill/equity*100:.0f}%>30%"})
    if other_recv and assets and other_recv / assets > 0.10:
        flags.append({"label": "其他应收款膨胀", "strength": "medium", "brief": f"{other_recv/assets*100:.0f}%",
                      "detail": f"其他应收款占总资产{other_recv/assets*100:.0f}%>10%"})
    if cash and assets and debt and cash / assets > 0.15 and debt / assets > 0.30:
        flags.append({"label": "存贷双高", "strength": "medium", "brief": "",
                      "detail": f"货币{cash/assets*100:.0f}%+有息负债{debt/assets*100:.0f}%同高"})
    return flags


def risk_score(flags: list[dict]) -> int:
    s = sum(_STRONG_PTS if f["strength"] == "strong" else _MEDIUM_PTS for f in flags)
    return min(100, s)


def push_key(flags: list[dict]) -> str:
    """达到推送门槛(任一强 或 ≥2中)时返回触发组合键, 否则空串。"""
    strong = [f for f in flags if f["strength"] == "strong"]
    medium = [f for f in flags if f["strength"] == "medium"]
    if strong or len(medium) >= 2:
        return "+".join(sorted(f["label"] for f in flags))
    return ""


async def _scan_one(code: str, name: str) -> dict | None:
    """扫一只: 拉财务→算红旗→落库→返回需新推送的命中(否则None)。"""
    async with _SEM:
        fin = await get_financial_statements(code)
        bs_detail, _ = await get_balance_sheet_latest(code)   # 新浪明细补3项红旗
    flags, year = compute_flags(fin["income"], fin["cashflow"], fin["balance"])
    if not year:
        return None   # 无财务数据(新股/退市/拉取失败)
    flags += sina_detail_flags(bs_detail)   # 巨潮6项 + 新浪明细3项
    score = risk_score(flags)
    key = push_key(flags)

    old = await repository.get_fin_risk(code)
    old_key = (old or {}).get("pushed_key") or ""
    import json
    await repository.upsert_fin_risk(
        code=code, name=name, report_year=year, score=score,
        flags_json=json.dumps(flags, ensure_ascii=False), pushed_key=key)

    if key and key != old_key:   # 达门槛且命中组合较上次有变化 → 新推送
        return {"code": code, "name": name, "year": year, "score": score, "flags": flags}
    return None


def _flag_disp(f: dict) -> str:
    """红旗展示 = 标签 + 极简数字(brief), 如「累计亏损-1.1亿」「高杠杆91%」; 无数字则只标签。"""
    return f"{f['label']}{f.get('brief', '')}"


def fin_section_text(hits: list[dict]) -> str:
    """财务红旗区域文本(微信版): 按风险分分档(高危≥50/中危30-49), 一只一行, 标签接极简数字。
    不含顶层标题, 供合并推送拼区域用。"""
    lines: list[str] = []
    for bucket_name, lo, hi in (("高危 ≥50分", 50, 999), ("中危 30-49分", 0, 50)):
        grp = [h for h in hits if lo <= h["score"] < hi]
        if not grp:
            continue
        lines.append(f"【{bucket_name}】")
        for h in grp:
            fl = "·".join(_flag_disp(f) for f in h["flags"])
            lines.append(f"{h['score']} {h['name']}({h['code']})　{fl}")
    return "\n".join(lines)


def fin_table(hits: list[dict]) -> dict:
    """财务红旗飞书表格元素: 股票/风险分/财务红旗(标签+数字)。"""
    from backend.services import lark_notifier
    columns = [
        {"name": "stock", "display_name": "股票", "data_type": "text",
         "width": "24%", "horizontal_align": "left"},
        {"name": "score", "display_name": "风险分", "data_type": "text",
         "width": "14%", "horizontal_align": "left"},
        {"name": "flags", "display_name": "财务红旗", "data_type": "text",
         "width": "62%", "horizontal_align": "left"},
    ]
    rows = [{"stock": f"{h['name']}\n{h['code']}", "score": str(h["score"]),
             "flags": "·".join(_flag_disp(f) for f in h["flags"])} for h in hits]
    return lark_notifier.table_element(columns, rows, page_size=10)


def _build_push(hits: list[dict]) -> tuple[str, list]:
    text = (f"{len(hits)}只自选股年报命中风险红旗（纯提示，不影响买卖点）\n\n"
            + fin_section_text(hits))
    elements = [
        {"tag": "markdown",
         "content": "**纯提示, 不影响买卖点。** 数据=巨潮年报; 红旗多为ST/退市前兆。"},
        fin_table(hits),
    ]
    return text, elements


async def collect_financial_risk_hits() -> list[dict]:
    """扫全自选股财务三表算红旗+落库(去重), 返回需新推送的命中(按风险分降序)。不推送。
    供黑天鹅合并推送 (blackswan_alerts) 调用。"""
    cfg = load_config().get("fin_risk_monitor", {})
    if not cfg.get("enabled", True):   # 默认开启, 配置可显式关闭
        return []

    codes = await repository.list_quotable_codes()
    if not codes:
        logger.info("[fin_risk] 自选池为空, 跳过")
        return []

    rows = await repository.list_all_stocks()
    name_map = {r["code"]: (r.get("name") or r["code"]) for r in rows}

    results = await asyncio.gather(*[_scan_one(c, name_map.get(c, c)) for c in codes])
    hits = [r for r in results if r]
    hits.sort(key=lambda h: -h["score"])
    return hits


async def scan_financial_risk():
    """[独立任务·已并入黑天鹅合并推送 blackswan_alerts] 保留供直接调用/兜底, 自带单独成卡推送。"""
    hits = await collect_financial_risk_hits()
    if not hits:
        logger.info("[fin_risk] 无新增财务红旗")
        return

    from backend.services import notifier
    text, elements = _build_push(hits)
    ok = await notifier.send_dual_card(text, lark_title="⚠️ 自选股财务红旗", elements=elements)
    logger.warning(f"[fin_risk] 新增财务红旗 {len(hits)} 只, 推送={'成功' if ok else '失败/跳过'}")
