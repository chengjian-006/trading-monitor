"""预增榜 (进攻向, 但克制, v1.7.573)。

回测结论(全市场11340事件): 只有「预增」埋伏 D-6→D+2 有约 +2.3%/胜率61% 的小 edge; 且公告后
D+2→D+5 阴跌(利好兑现)。故此榜定位= 盘后把当日新出的正向预告捞出来给你**发现线索**, 不是埋伏神器:
卡片明确标注「涨在公告那一下、之后阴跌, 只做快进快出, 别追高」。自选/持仓命中置顶。

任务: run_earnings_forecast_scan  每日 18:30  拉当日业绩预告→落库→推正向预告(自选置顶+全市场大幅预增TopN)
"""
import logging
from datetime import date, timedelta

from backend.fetcher.earnings_data import fetch_earnings_forecasts
from backend.models import repository
from backend.models.repo import earnings as earnings_repo
from backend.services import notifier
from backend.services.disclosure_reminder import _current_report_date
from backend.services.lark_notifier import md_element

logger = logging.getLogger(__name__)

MARKET_TOP = 15         # 全市场正向预告最多展示 N 条(按变动幅度上限降序)
LOOKBACK_DAYS = 3       # 回看窗: 捞最近N天公告(周五盘后/周末发的次日补上, pushed_at去重不重推)
CAUTION = "⚠️ 回测: 好业绩涨在公告那一下、之后D+2→D+5阴跌(利好兑现), 只做快进快出别追高; 埋伏仅「预增」有小edge。"


def _amp_txt(lo, up) -> str:
    """变动幅度区间文案。"""
    def f(v):
        return f"{v:+.0f}%" if isinstance(v, (int, float)) else None
    a, b = f(lo), f(up)
    if a and b and a != b:
        return f"{a}~{b}"
    return b or a or "—"


async def run_earnings_forecast_scan() -> None:
    """盘后拉当日业绩预告→落库→推正向预告榜(自选/持仓命中置顶)。任意日可跑(周末也有公告)。"""
    today = date.today().isoformat()
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    rd = _current_report_date()
    try:
        rows = await fetch_earnings_forecasts(rd, notice_since=since)
    except Exception as e:
        logger.warning(f"[yjyg] 业绩预告抓取失败({rd}/{since}起): {e}")
        return
    if not rows:
        logger.info(f"[yjyg] {since}起 无业绩预告")
        return
    await earnings_repo.upsert_forecasts(rows)

    good = await earnings_repo.forecasts_to_push(since, groups=("利好",))
    if not good:
        logger.info(f"[yjyg] {since}起 无未推送的正向预告")
        return

    # 自选/持仓命中集合(置顶)
    user_codes = set()
    hold_codes = set()
    try:
        user_codes.update(await repository.list_quotable_codes())
        cost_map, _, _ = await repository.get_holdings_full_info(1)
        hold_codes = set(cost_map.keys())
        user_codes.update(hold_codes)
    except Exception:
        pass

    mine = [g for g in good if str(g["code"]) in user_codes]
    others = [g for g in good if str(g["code"]) not in user_codes][:MARKET_TOP]

    # markdown 表格版式(飞书 schema2.0 markdown 组件渲染, 手机端整齐不截断; 替代原生table组件——
    # 后者列宽百分比在手机窄屏必截成"...")。2列: 股票 | 净利变动(含类型), 精简去空标签列。
    def _amp_cell(g) -> str:
        return f"{g['predict_type']} {_amp_txt(g.get('amp_lower'), g.get('amp_upper'))}"

    def _mdtable(rows: list) -> str:
        out = ["| 股票 | 净利变动 |", "| --- | --- |"]
        out += [f"| {s} | {a} |" for s, a in rows]
        return "\n".join(out)

    elements = []
    title = "📈 预增榜·当日正向业绩预告"
    head = f"{title}\n\n新出正向业绩预告 **{len(good)}** 条。{CAUTION}"
    elements.append(md_element(head))
    if mine:
        rows = [(f"{'🔴' if str(g['code']) in hold_codes else '⭐'}{g['name']}({g['code']})", _amp_cell(g))
                for g in mine]
        elements.append(md_element(
            f"**🎯 你的自选/持仓命中 {len(mine)} 只**（🔴持仓 ⭐自选）\n\n" + _mdtable(rows)))
    if others:
        rows = [(f"{g['name']}({g['code']})", _amp_cell(g)) for g in others]
        elements.append(md_element(
            f"**全市场大幅预增 Top{len(others)}**（按净利变动幅度）\n\n" + _mdtable(rows)))

    try:
        await notifier.send_dual_card(head, lark_title=title, elements=elements)
        await earnings_repo.mark_forecasts_pushed([(g["code"], g["report_date"]) for g in good])
        logger.info(f"[yjyg] 预增榜已推: 命中{len(mine)}/全市场{len(others)} (当日正向{len(good)})")
    except Exception as e:
        logger.warning(f"[yjyg] 预增榜推送失败: {e}")
