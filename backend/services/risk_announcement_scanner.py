# -*- coding: utf-8 -*-
"""自选股风险公告监控 — 黑天鹅(灰犀牛)预警(v1.7.x).

只对自选股, 收盘后一天一次。命中财务/监管类硬信号即软提醒(纯提示, 不碰任何买卖点逻辑)。

数据源: 巨潮资讯网 cninfo(证监会指定信披平台, 最权威, 不在东财封禁范围)。
回测: 合力泰(002217) ST 案——本规则最早在 2025-04-29 立案告知书当天即命中, 早于 2026-06 ST 约 14 个月。

命中规则(5 类硬信号 + 3 道方向/子串护栏):
  1. 证监会立案 / 行政处罚·事先告知书 / 行政监管措施
  2. 交易所问询函 / 关注函
  3. 非标审计意见(保留意见/无法表示意见/否定意见/非标意见) —— 护栏: 排除"无保留意见"
  4. 变更·解聘·改聘会计师事务所 —— 护栏: 排除续聘/选聘/履职/评估等例行
  5. 被实施风险警示 / 退市风险 —— 护栏: 排除"撤销/申请撤销"(摘帽利好)

入口 scan_risk_announcements() 注册为 cron 18:00。去重靠 cfzy_biz_risk_ann_seen 唯一索引。
"""
import asyncio
import logging

from backend.core.config import load_config
from backend.fetcher.cninfo import get_org_id_map, query_announcements
from backend.models import repository

logger = logging.getLogger(__name__)

_SEM = asyncio.Semaphore(4)   # 巨潮并发上限, 别把出口IP打到限流


def match_risk(title: str) -> list[str]:
    """命中的风险标签列表; 空=非风险。带方向/子串护栏。"""
    t = title
    tags: list[str] = []

    # 方向护栏: "撤销...风险警示/退市风险" = 摘帽利好, 直接放过
    is_revoke = "撤销" in t and ("风险警示" in t or "退市风险" in t)

    # 1. 监管立案 / 行政处罚 —— 最强信号
    if "立案" in t:
        tags.append("立案调查")
    if "行政处罚" in t or "事先告知书" in t or "行政监管措施" in t:
        tags.append("行政处罚")
    # 2. 交易所函件
    if "问询函" in t or "关注函" in t:
        tags.append("交易所问询")
    # 3. 非标审计意见 —— 子串护栏: "无保留意见"是干净意见, 不算非标
    if ("保留意见" in t and "无保留意见" not in t) or "无法表示意见" in t \
       or "否定意见" in t or "非标意见" in t or "非标准审计意见" in t:
        tags.append("非标审计意见")
    # 4. 换所 —— 只认变更/解聘/改聘, 排除续聘/选聘/履职/评估等例行公事
    if "会计师事务所" in t and any(k in t for k in ("变更", "解聘", "改聘")) \
       and not any(k in t for k in ("续聘", "选聘", "履职", "评估", "意见书")):
        tags.append("变更会计师事务所")
    # 5. 风险警示/退市风险 —— "实施"且非摘帽
    if not is_revoke and "撤销" not in t and "实施" in t \
       and ("风险警示" in t or "退市风险" in t):
        tags.append("被实施风险警示")
    # 6. 股权质押/冻结困境(2.1) —— 平仓(线)/质押违约/补充质押/股份冻结, 排除常规质押公告
    if "平仓" in t or ("质押" in t and any(k in t for k in ("违约", "补充质押"))) \
       or ("冻结" in t and ("股份" in t or "股权" in t or "质押" in t)):
        tags.append("质押冻结风险")
    # 7. 大股东减持(2.1) —— 只收控股股东/实控人/清仓式, 排除常规小额减持
    if "清仓式减持" in t or \
       (("控股股东" in t or "实际控制人" in t or "实控人" in t) and "减持" in t):
        tags.append("大股东减持")

    return tags


async def _scan_one(code: str, org_map: dict, name_map: dict) -> list[dict]:
    """扫一只票, 返回本次新命中的风险公告(已落库去重)。"""
    org_id = org_map.get(code)
    if not org_id:
        return []
    async with _SEM:
        try:
            anns = await query_announcements(code, org_id, days=7)
        except Exception as e:
            logger.warning(f"[risk_ann] {code} 公告查询失败: {e}")
            return []

    name = name_map.get(code, code)
    fresh: list[dict] = []
    for a in anns:
        tags = match_risk(a["title"])
        if not tags:
            continue
        tag_str = "/".join(tags)
        is_new = await repository.save_risk_ann(
            code=code, name=name, ann_id=a["ann_id"], title=a["title"],
            tags=tag_str, ann_date=a["date"], url=a["url"])
        if is_new:
            fresh.append({"code": code, "name": name, "title": a["title"],
                          "tags": tag_str, "date": a["date"], "url": a["url"]})
    return fresh


def ann_section_text(hits: list[dict], verdicts: dict | None = None) -> str:
    """风险公告区域文本(微信版): 一只一行(含原文链接)。verdicts={code:{emoji,severity,text}}时
    每只票行下跟一句 AI 研判。不含顶层标题, 供合并推送拼区域用。"""
    verdicts = verdicts or {}
    lines: list[str] = []
    for h in hits:
        url_line = f"\n  原文: {h['url']}" if h["url"] else ""
        line = f"• {h['name']}({h['code']}) [{h['tags']}] {h['date']}\n  {h['title']}{url_line}"
        v = verdicts.get(h["code"])
        if v:
            line += f"\n  🤖 {v['emoji']}{v['severity']} · {v['text']}"
        lines.append(line)
    return "\n".join(lines)


def ann_table(hits: list[dict], verdicts: dict | None = None) -> dict:
    """风险公告全短列表(基线 v1.1): 股票 | 类型 | 要点。

    要点列 = AI 严重度(有研判时, 如 🔴高)或公告日期(MM-DD), 全为可牺牲短值;
    长值(公告标题、AI 研判句)不进表, 由 ann_fold 下沉折叠区。"""
    from backend.services import card_kit
    verdicts = verdicts or {}
    rows = []
    for h in hits:
        v = verdicts.get(h["code"])
        brief = f"{v['emoji']}{v['severity']}" if v else ((h["date"] or "")[5:] or h["date"])
        rows.append((f"{h['name']} {h['code']}", h["tags"], brief))
    return card_kit.short_table(["股票", "类型", "要点"], rows)


def ann_fold(hits: list[dict], verdicts: dict | None = None) -> dict:
    """公告摘要+AI研判折叠区(长文本下沉): 每股一行 标题（日期）+ 🤖研判句。"""
    from backend.services import card_kit
    verdicts = verdicts or {}
    lines = []
    for h in hits:
        date_short = (h["date"] or "")[5:] or h["date"]
        title = (h["title"] or "").replace("\n", " ")[:40]
        line = f"• {h['name']} {title}（{date_short}）"
        v = verdicts.get(h["code"])
        if v:
            line += f"\n　🤖{v['emoji']}{v['severity']}·{v['text']}"
        lines.append(line)
    return card_kit.fold("公告摘要与AI研判", "\n".join(lines))


def ann_elements(hits: list[dict], verdicts: dict | None = None) -> list:
    """风险公告区域元素组: 全短列表 + 折叠明细(基线 v1.1 表格铁律)。"""
    return [ann_table(hits, verdicts), ann_fold(hits, verdicts)]


def _build_card(hits: list[dict]):
    """独立兜底卡(基线 v1.1): 风险家族橙卡 + 🦢 + 全短列表 + 折叠明细 + 👉建议。"""
    from backend.services import card_kit
    n = len({h["code"] for h in hits})
    text = (f"今日新命中 {len(hits)} 条监管/财务风险公告, 请留意持仓与选股风险:\n\n"
            + ann_section_text(hits)
            + "\n\n纯提示, 不影响买卖点。监管/财务红旗多为 ST 黑天鹅前兆。")
    elements = [
        *ann_elements(hits),
        card_kit.advice("纯提示不动买卖点，命中票自查风险"),
    ]
    return card_kit.Card(
        title=f"🦢 风险公告预警 · 自选{n}只",
        elements=elements, fallback=text, family="risk",
        summary=card_kit.summary_text(f"自选{n}只命中风险公告", f"{len(hits)}条", "监管/财务硬信号"),
        tags=[("风险公告", "orange")])


async def collect_risk_ann_hits() -> list[dict]:
    """扫全自选股最近公告, 命中风险规则+落库(去重), 返回新命中(按日期倒序)。不推送。
    供黑天鹅合并推送 (blackswan_alerts) 调用。"""
    cfg = load_config().get("risk_ann_monitor", {})
    if not cfg.get("enabled", True):   # 默认开启, 配置可显式关闭
        return []

    codes = await repository.list_quotable_codes()
    if not codes:
        logger.info("[risk_ann] 自选池为空, 跳过")
        return []

    org_map = await get_org_id_map()
    if not org_map:
        logger.warning("[risk_ann] 巨潮 orgId 字典为空(数据源异常), 跳过本轮")
        return []

    # 自选股名称(用已落库速览字段, 缺则用代码兜底)
    rows = await repository.list_all_stocks()
    name_map = {r["code"]: (r.get("name") or r["code"]) for r in rows}

    results = await asyncio.gather(*[_scan_one(c, org_map, name_map) for c in codes])
    hits = [h for sub in results for h in sub]
    hits.sort(key=lambda h: h["date"], reverse=True)   # 最新在前
    return hits


async def scan_risk_announcements():
    """[独立任务·已并入黑天鹅合并推送 blackswan_alerts] 保留供直接调用/兜底, 自带单独成卡推送。"""
    hits = await collect_risk_ann_hits()
    if not hits:
        logger.info("[risk_ann] 无新风险公告")
        return

    from backend.services import notifier
    card = _build_card(hits)
    ok = await notifier.send_card(card)
    logger.warning(f"[risk_ann] 新命中风险公告 {len(hits)} 条, 推送={'成功' if ok else '失败/跳过'}")
