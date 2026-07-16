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


def _date_cell(g) -> str:
    """公告发布日 MM-DD(回看窗内可能是今日/隔日, 逐条标各自发布日); 缺失回退 —。"""
    nd = g.get("notice_date")
    return str(nd)[5:10] if nd and len(str(nd)) >= 10 else "—"


def build_forecast_card(good: list, mine: list, others: list, hold_codes: set):
    """预增榜 → 基线 v1.1 结构卡(机会族 red——基线家族表把预增榜归机会族):
    结论行 → 命中表/全市场表(全短列 股票|净利变动|发布, >8行 Top5+全量折叠) → 👉建议 → 回测口径折叠。
    表格三列各格都短(名称+类型/净利变动/发布MM-DD), 手机端不触发"单格塞多字段"截断; 代码下沉折叠。"""
    from backend.services import card_kit

    def _row(g, mark: str = "") -> tuple:
        return (f"{mark}{g['name']} {g['predict_type']}",
                _amp_txt(g.get("amp_lower"), g.get("amp_upper")), _date_cell(g))

    def _long_line(g, mark: str = "") -> str:
        return (f"{mark}**{g['name']}**({g['code']}) {g['predict_type']} "
                f"净利变动 {_amp_txt(g.get('amp_lower'), g.get('amp_upper'))} · 发布 {_date_cell(g)}")

    headers = ["股票", "净利变动", "发布"]
    elements: list = [md_element(
        f"新出正向业绩预告 **{len(good)}** 条，自选/持仓命中 **{len(mine)}** 只（🔴持仓 ⭐自选）")]
    fold_sections: list[str] = []
    if mine:
        marks = ["🔴" if str(g["code"]) in hold_codes else "⭐" for g in mine]
        elements.append(md_element(f"**🎯 自选/持仓命中 {len(mine)} 只**"))
        if len(mine) > 8:
            elements.append(card_kit.short_table(headers, [_row(g, m) for g, m in zip(mine[:5], marks)]))
            elements.append(md_element(f"…等 **{len(mine)}** 只，全量见折叠"))
        else:
            elements.append(card_kit.short_table(headers, [_row(g, m) for g, m in zip(mine, marks)]))
        fold_sections.append("**自选/持仓命中**\n" + "\n".join(
            _long_line(g, m) for g, m in zip(mine, marks)))
    if others:
        elements.append(md_element(f"**全市场大幅预增 Top{len(others)}**（按净利变动幅度）"))
        if len(others) > 8:
            elements.append(card_kit.short_table(headers, [_row(g) for g in others[:5]]))
            elements.append(md_element(f"…等 **{len(others)}** 只，全量见折叠"))
        else:
            elements.append(card_kit.short_table(headers, [_row(g) for g in others]))
        fold_sections.append(f"**全市场大幅预增 Top{len(others)}**\n" + "\n".join(
            _long_line(g) for g in others))
    elements.append(card_kit.advice("只做快进快出，别追高"))
    elements.append(card_kit.fold(
        "回测口径与全量名单", CAUTION + "\n\n" + "\n\n".join(fold_sections)))

    fb_lines = [f"📈 预增榜 · 当日正向业绩预告 **{len(good)}** 条。{CAUTION}"]
    if mine:
        fb_lines.append(f"\n🎯 自选/持仓命中 {len(mine)} 只（🔴持仓 ⭐自选）")
        fb_lines += [_long_line(g, "🔴" if str(g["code"]) in hold_codes else "⭐") for g in mine]
    if others:
        fb_lines.append(f"\n全市场大幅预增 Top{len(others)}（按净利变动幅度）")
        fb_lines += [_long_line(g) for g in others]
    fb_lines.append("\n👉 只做快进快出，别追高")
    return card_kit.Card(
        title=f"📈 预增榜 · {len(good)}条", elements=elements, fallback="\n".join(fb_lines),
        family="opportunity",
        summary=card_kit.summary_text("预增榜", f"{len(good)}条",
                                      f"命中{len(mine)}只" if mine else "自选无命中"),
        subtitle="盘后正向业绩预告",
        tags=[("快进快出", "orange")])


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

    try:
        await notifier.send_card(build_forecast_card(good, mine, others, hold_codes))
        await earnings_repo.mark_forecasts_pushed([(g["code"], g["report_date"]) for g in good])
        logger.info(f"[yjyg] 预增榜已推: 命中{len(mine)}/全市场{len(others)} (当日正向{len(good)})")
    except Exception as e:
        logger.warning(f"[yjyg] 预增榜推送失败: {e}")
