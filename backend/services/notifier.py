import asyncio
import httpx
import logging
import re
from datetime import datetime

from backend.core.config import load_config, is_production, get_outbound_ip

logger = logging.getLogger(__name__)


def _lark_md_to_html(md: str) -> str:
    """飞书 lark_md → PushPlus(微信)HTML, 保证两端内容/格式一致:
    **加粗**→<b>, `代码`→<code>, 换行→<br>. 让微信复用飞书同一份正文。"""
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", md or "")
    html = re.sub(r"`([^`]+?)`", r"<code>\1</code>", html)
    return html.replace("\n", "<br>")


async def _fanout_pushplus(title: str, content: str) -> bool:
    """文本/播报类消息推 PushPlus(个人微信, 替代原企微通道). 开关关或 token 空则跳过返回 False.
    content 走与飞书同一份 lark_md, 转 HTML 后发, 保证两端格式一致(加粗/代码同步渲染)。"""
    cfg = load_config()
    if not cfg.get("pushplus_enabled", True):
        return False
    return await _post_pushplus(cfg.get("pushplus_token", ""), title, _lark_md_to_html(content), "pushplus")


async def _post_pushplus(token: str, title: str, content: str, tag: str) -> bool:
    """PushPlus 微信推送 — 免费, 扫码即用, 支持HTML。"""
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("http://www.pushplus.plus/send", json={
                "token": token, "title": title[:100], "content": content[:8000],
                "template": "html",
            })
            data = resp.json()
            if data.get("code") == 200:
                logger.info(f"[{tag}] PushPlus 推送成功")
                return True
            logger.error(f"[{tag}] PushPlus 响应错误: {data}")
            return False
    except Exception as e:
        logger.warning(f"[{tag}] PushPlus 失败: {e}")
        return False


async def _post_wxpusher(token: str, uids: list, content: str, tag: str) -> bool:
    """WxPusher 微信推送 — 免费, 扫码订阅即收。contentType=1=文本。"""
    if not token or not uids:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://wxpusher.zjiecode.com/api/send/message", json={
                "appToken": token, "content": content[:8000], "contentType": 1,
                "uids": uids,
            })
            data = resp.json()
            if data.get("code") == 1000:
                logger.info(f"[{tag}] WxPusher 推送成功")
                return True
            logger.error(f"[{tag}] WxPusher 响应错误: {data}")
            return False
    except Exception as e:
        logger.warning(f"[{tag}] WxPusher 失败: {e}")
        return False


async def _fanout_lark(webhook: str, enabled, body: str, *, title: str = "📊 盘面播报",
                       template: str = "blue") -> bool:
    """飞书并推交互卡片(独立通道). enabled 为真且 webhook 非空才发, 与企微开关互不影响.
    body 走 lark_md, **加粗** 等可渲染; title 是卡片彩色标题栏.
    """
    if not enabled or not webhook:
        return False
    from backend.services import lark_notifier
    return await lark_notifier.post_lark_card(webhook, title, body, template)


_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]

def _time_prefix() -> str:
    now = datetime.now()
    wd = _WEEKDAY_CN[now.weekday()]
    return f"🕐 {now:%H:%M}（周{wd}）\n\n"

DIRECTION_EMOJI = {"buy": "🟢", "sell": "🔴", "reduce": "🟡", "plunge": "⚠️"}
DIRECTION_LABEL = {"buy": "买入信号", "sell": "卖出信号", "reduce": "减仓提示", "plunge": "大盘预警"}
DIRECTION_SHORT = {"buy": "买入", "sell": "卖出", "reduce": "减仓", "plunge": "预警"}

# 模型规则摘要 — 推送里另起一行展示量化门槛
MODEL_RULES: dict[str, str] = {
    "缩量后放量突破（右侧）": "昨量<均量×0.8 → 今放量≥昨量×2且≥均量×1.5 → 突破昨高×1.02 · 站上MA10/20 · 成交额≥10亿",
    "回踩20MA缩量后突破昨高": "近30日主升≥15% · 回踩MA20±3% · 昨缩量<均量×0.8 → 突破昨高×1.025 · 成交额≥10亿",
    "回踩10MA缩量后突破昨高": "近30日主升≥15% · 回踩MA10±1% · 昨缩量<均量×0.8 → 突破昨高×1.025 · 成交额≥10亿",
    "强势起点": "前置弱势极限地量 → 放量≥近期×2 · 涨幅≥2% · 站上MA10/20 · 成交额≥10亿",
    "弱势极限": "近30日主升≥15% · 贴MA10/20±2% → 地量≤近10日最低×1.1且≤均量×0.70",
    "中继平台突破": "前12日横盘振幅≤15% · 缓升台阶 → 收盘≥上沿×1.005 · 放量≥均量×1.2 · 成交额≥10亿",
    "竞价弱转强": "昨缩量≤均×0.8 → 竞价高开3-9% · 竞价成交额≥5000万 · 大盘红盘≥3500或绿盘≥3500",
}
ACTION_HINT = {
    "buy":    "建议跟随入场",
    "sell":   "建议清仓离场",
    "reduce": "建议减仓50%锁利",
    "plunge": "盘面预警，注意控仓",
}


# 全市场回测战绩(近3月/近6月) — ms 形如 {model_name, win_rate_3m, net_3m, n_3m, win_rate_6m, net_6m, n_6m}
def _has_model_stats(ms: dict | None) -> bool:
    """有近6月样本才展示(近3月可能因近端2周触发未走完出场而偏少甚至空)。"""
    return bool(ms) and (ms.get("n_6m") or 0) > 0


def _ms_wr(ms: dict, suf: str) -> str:
    wr, n = ms.get(f"win_rate_{suf}"), ms.get(f"n_{suf}") or 0
    return f"{wr:.0f}%({n}笔)" if (wr is not None and n) else "—"


def _ms_net(ms: dict, suf: str) -> str:
    nt, n = ms.get(f"net_{suf}"), ms.get(f"n_{suf}") or 0
    if nt is None or not n:
        return "—"
    return f"+{nt:.1f}%" if nt >= 0 else f"{nt:.1f}%"


def _build_model_stats_block(ms: dict | None) -> list[str]:
    """买点全市场回测战绩(企微纯文本): 胜率 + 单笔平均收益, 近3月/近6月。无近6月样本则空。"""
    if not _has_model_stats(ms):
        return []
    sep = "━━━━━━━━━━━━━━━"
    out = [
        sep,
        f"📊 模型战绩（{ms.get('model_name', '')}）",
        f"   胜率  近3月 {_ms_wr(ms, '3m')} · 近6月 {_ms_wr(ms, '6m')}",
        f"   单笔均收益  近3月 {_ms_net(ms, '3m')} · 近6月 {_ms_net(ms, '6m')}",
    ]
    if ms.get("rank_3m"):
        out.append(f"   近3月胜率 全模型第 {ms['rank_3m']}/{ms.get('rank_n', 0)} 名")
    return out


def _build_model_stats_lark(ms: dict | None) -> list[str]:
    """买点全市场回测战绩(飞书 lark_md)。"""
    if not _has_model_stats(ms):
        return []
    out = [
        f"\n**📊 模型战绩**（{ms.get('model_name', '')}）",
        f"胜率　近3月 **{_ms_wr(ms, '3m')}**　近6月 **{_ms_wr(ms, '6m')}**",
        f"单笔均收益　近3月 **{_ms_net(ms, '3m')}**　近6月 **{_ms_net(ms, '6m')}**",
    ]
    if ms.get("rank_3m"):
        out.append(f"近3月胜率 全模型第 **{ms['rank_3m']}/{ms.get('rank_n', 0)}** 名")
    return out


def model_stats_oneline(ms: dict | None) -> str:
    """单行版(给多买点合并推送用)。无数据返回空串。"""
    if not _has_model_stats(ms):
        return ""
    rank = f"，近3月胜率第{ms['rank_3m']}/{ms.get('rank_n', 0)}名" if ms.get("rank_3m") else ""
    return (f"📊 全市场回测 胜率近3月{_ms_wr(ms, '3m')}/近6月{_ms_wr(ms, '6m')}"
            f"，单笔均收益{_ms_net(ms, '3m')}/{_ms_net(ms, '6m')}{rank}")


import re as _re_nf


def _split_detail_sections(detail: str):
    """把信号 detail(| 分隔) 拆成 (触发条件列表, 交易计划|None, 共振|None, 成交额|None)。
    交易计划/多买点共振/成交额 从触发条件里分出来单独成块; 成交额取最后一条(实时)以去重。"""
    segs = [s.strip() for s in (detail or "").split("|") if s.strip()]
    conds, plan, reso, amount = [], None, None, None
    for s in segs:
        if s.startswith("交易计划") or "交易计划:" in s or "交易计划：" in s:
            plan = s.split("交易计划", 1)[1].lstrip(":：").strip()
        elif "共振" in s:
            reso = s
        elif "成交额" in s:
            amount = s          # 最后一条覆盖前面的, 实现去重
        else:
            conds.append(s)
    return conds, plan, reso, amount


def _bold_nums(text: str) -> str:
    """给带单位的关键数字加粗(飞书 lark_md): 百分比 / Nx倍数 / N亿。MA10/T+10/×0.98 不误伤。"""
    text = _re_nf.sub(r'([+-]?\d+(?:\.\d+)?%)', r'**\1**', text)
    text = _re_nf.sub(r'(?<![A-Za-z\d.])(\d+(?:\.\d+)?x)', r'**\1**', text)
    text = _re_nf.sub(r'(\d+(?:\.\d+)?倍)', r'**\1**', text)
    text = _re_nf.sub(r'(\d+(?:\.\d+)?亿)', r'**\1**', text)
    return text


def _kpi_line(price: float, pct_change: float, direction: str, ms: dict | None = None, *, bold: bool) -> str:
    """顶部一行: 现价/涨幅。(战绩另用表格展示)"""
    b = (lambda s: f"**{s}**") if bold else (lambda s: str(s))
    if direction == "plunge":
        return f"现价{b(f'{price:.2f}')}"
    arrow = "▲" if pct_change > 0 else ("▼" if pct_change < 0 else "")
    tail = f" {arrow}{b(f'{pct_change:+.2f}%')}" if pct_change else ""
    return f"现价{b(f'{price:.2f}')}{tail}"


def _first_sentence(text: str, maxlen: int = 36) -> str:
    """取策略第一句(供折叠 header 常显): 先按句末标点切, 否则按长度截断。"""
    t = " ".join(x.strip() for x in (text or "").strip().split("\n") if x.strip())
    for d in ("；", ";", "。"):
        i = t.find(d)
        if 0 <= i <= maxlen + 12:
            return t[:i + 1]
    return t[:maxlen] + ("…" if len(t) > maxlen else "")


def _headline(direction: str, amount: str | None, ms: dict | None,
              conds: list | None, *, bold: bool) -> str:
    """关键数字前置行: 买入=成交额+近3月胜率排名; 卖出/减仓=浮盈/成本。无则空串。"""
    b = (lambda s: f"**{s}**") if bold else (lambda s: str(s))
    if direction == "buy":
        parts = []
        if amount:
            parts.append(f"💰 {_bold_nums(amount) if bold else amount}")
        if ms and ms.get("rank_3m"):
            wr = _ms_wr(ms, "3m")
            parts.append(f"🎯 近3月胜率{b(wr)}·第{b(ms['rank_3m'])}名" if wr != "—"
                         else f"🎯 全模型第{b(ms['rank_3m'])}名")
        return "　".join(parts)
    cc = next((c for c in (conds or []) if ("成本" in c or "浮" in c)), "")
    return (f"📊 {_bold_nums(cc) if bold else cc}") if cc else ""


def _basics_line(basics: dict | None, *, bold: bool) -> str:
    """预警时点基础信息行: 市场人气排名 / 成交额排名(100名内)。无则空串。"""
    if not basics:
        return ""
    b = (lambda s: f"**{s}**") if bold else (lambda s: str(s))
    parts = []
    pr = basics.get("popularity_rank")
    if pr:
        parts.append("🔥 人气榜100名外" if pr > 100 else f"🔥 人气榜第{b(pr)}")
    ar = basics.get("amount_rank")
    if ar and ar <= 100:
        parts.append(f"💵 成交额第{b(ar)}")
    return " · ".join(parts)


def _sector_line(sector: dict | None, *, bold: bool) -> str:
    """所属行业 + 关联热点(概念标签中当前最热题材: 轮动状态 + 今日涨停家数 + 近3日趋势)。无则空串。

    v1.7.561: 原来只显示最热概念且叫"所属板块", 主业与热点无关的票会被误导(卫星化学
    主业化学原料却显示 AI算力·液冷) → 改两截: 行业是主业口径, 热点明示只是"关联"。
    """
    if not sector:
        return ""
    industry = (sector.get("industry") or "").strip()
    label = sector.get("label")
    if not label:
        return f"📊 所属行业：{industry}" if industry else ""
    b = (lambda s: f"**{s}**") if bold else (lambda s: str(s))
    seq = "→".join(str(x) for x in sector.get("seq", []))
    n = len(sector.get("seq", []))
    line2 = (f"{sector['status']}{sector['emoji']} · 今日涨停{b(sector['today'])}家"
             f" · 近{n}日 {seq} {sector['trend']}")
    head = f"所属行业：{industry} ｜ 关联热点：{label}" if industry else f"关联热点：{label}"
    return f"📊 {head}\n　　{line2}"


async def _fetch_signal_basics(code: str, user_id: int | None) -> dict:
    """预警时点基础信息: 成交额全市场名次(新浪top100) + 同花顺人气榜名次。失败项静默略过。"""
    out: dict = {}
    if not (code and _re_nf.match(r"^\d{6}$", code)):
        return out
    try:
        from backend.routers.market_report import _fetch_amount_rank_top100
        ar = (await _fetch_amount_rank_top100()).get(code)
        if ar:
            out["amount_rank"] = int(ar)
    except Exception:
        pass
    try:
        from backend.models import repository
        pr = await repository.get_stock_popularity_rank(code)
        if pr:
            out["popularity_rank"] = int(pr)
    except Exception:
        pass
    return out


def _background_line(background: dict | None, *, bold: bool) -> str:
    """背景标签行(黑天鹅·风险公告/财务红旗 + 业绩预增)。无则空串。"""
    if not background:
        return ""
    from backend.services import signal_background as sbg
    tags = sbg.build_background_tags(
        forecast=background.get("forecast"), fin_risk=background.get("fin_risk"),
        risk_anns=background.get("risk_anns") or [], bold=bold)
    return sbg.render_tags_text(tags)


def _build_signal_elements(code: str, name: str, signal_name: str, direction: str,
                           price: float, detail: str, strategy: str = "",
                           pct_change: float = 0.0, model_stats: dict | None = None,
                           basics: dict | None = None, sector: dict | None = None,
                           background: dict | None = None) -> list:
    """飞书 v2 卡 elements: 头/现价 + 背景标签 + 战绩表(近3月/近6月) + 我的策略 + 触发条件/计划/共振成交 + 行动。"""
    from backend.services import lark_notifier
    header = f"**{name}**　`{code}`\n{_kpi_line(price, pct_change, direction, bold=True)}"
    _bl = _basics_line(basics, bold=True)
    if _bl:
        header += f"\n{_bl}"
    els = [lark_notifier.md_element(header)]

    # ⚠️📈 背景标签(黑天鹅/业绩预增) — 紧跟头部, 风险优先看
    _bg = _background_line(background, bold=True)
    if _bg:
        els.append(lark_notifier.md_element(_bg))

    conds, plan, reso, amount = _split_detail_sections(detail)

    # 🔑 关键数字前置 — 买入:成交额+胜率排名 / 卖出:浮盈·成本, 紧跟现价让一眼看到
    _hl = _headline(direction, amount, model_stats, conds, bold=True)
    if _hl:
        els.append(lark_notifier.md_element(_hl))

    # 📊 所属板块最近情况 — 题材是否在风口/退潮
    _sl = _sector_line(sector, bold=True)
    if _sl:
        els.append(lark_notifier.md_element(_sl))

    # 📌 我的策略 — 长文默认折叠只显第一句, 点开看全(减少卡片堆叠)
    if strategy and strategy.strip():
        s_one = " · ".join(x.strip() for x in strategy.strip().split("\n") if x.strip())
        first = _first_sentence(s_one)
        if len(s_one) > len(first) + 2:
            els.append(lark_notifier.collapsible_element(
                f"📌 **我的策略**　{first}", s_one))
        else:
            els.append(lark_notifier.md_element(f"📌 **我的策略**　{s_one}"))

    # 🎯 触发条件 / 💰 成交额 / 📋 计划 / 🔗 共振 — 各占一行
    # 卖出已把"成本/浮盈"前置到 headline, body 里去掉该段防重复
    body_conds = conds if direction == "buy" else [c for c in conds if not ("成本" in c or "浮" in c)]
    body = []
    if body_conds:
        body.append("🎯 个股实情：" + " · ".join(_bold_nums(c) for c in body_conds))
    if amount and direction != "buy":     # 买入成交额已前置到 headline, body 不重复
        body.append(f"💰 {_bold_nums(amount)}")
    rule = MODEL_RULES.get(signal_name, "")
    if rule:
        body.append(f"📐 模型规则：{rule}")
    if plan:
        body.append("📋 **计划**　" + " · ".join(_bold_nums(x.strip()) for x in plan.split("/") if x.strip()))
    if reso:
        body.append(f"🔗 {reso}")
    if body:
        els.append(lark_notifier.md_element("\n".join(body)))

    # 📊 全市场回测战绩 — 放最后, 参考信息不抢主内容
    if direction == "buy" and _has_model_stats(model_stats):
        ms = model_stats
        els.append(lark_notifier.md_element(f"**📊 模型战绩**（{ms.get('model_name', '')}）"))
        cols = [
            {"name": "period", "display_name": "周期"},
            {"name": "wr", "display_name": "胜率"},
            {"name": "net", "display_name": "单笔"},
        ]
        rows = [
            {"period": "近3月", "wr": _ms_wr(ms, "3m"), "net": _ms_net(ms, "3m")},
            {"period": "近6月", "wr": _ms_wr(ms, "6m"), "net": _ms_net(ms, "6m")},
        ]
        els.append(lark_notifier.md_table(cols, rows))
        if ms.get("rank_3m"):
            els.append(lark_notifier.md_element(
                f"近3月胜率　全模型第 **{ms['rank_3m']}/{ms.get('rank_n', 0)}** 名"))
    return els


def _build_text(code: str, name: str, signal_name: str,
                direction: str, price: float, detail: str,
                username: str = "", strategy: str = "",
                pct_change: float = 0.0, model_stats: dict | None = None,
                basics: dict | None = None, sector: dict | None = None,
                background: dict | None = None) -> str:
    """企微卡片：决策优先 / 分层布局
    顺序: 信号头 → 股票+实时价 → 背景标签 → 我的策略 → 触发要点 → 行动提示 → @
    """
    emoji = DIRECTION_EMOJI.get(direction, "⚪")
    label = DIRECTION_LABEL.get(direction, "信号")
    action = ACTION_HINT.get(direction, "")
    sep = "━━━━━━━━━━━━━━━"

    # 头 + KPI 一行(现价/涨幅 + 买点近3月胜率/单笔/排名) — 标题已在卡片头,不重复
    lines = [
        sep,
        f"📌 {name}  {code}",
        f"   {_kpi_line(price, pct_change, direction, model_stats, bold=False)}",
    ]
    conds, plan, reso, amount = _split_detail_sections(detail)
    # 🔑 关键数字前置(同卡片): 买入成交额+胜率排名 / 卖出浮盈·成本
    _hl = _headline(direction, amount, model_stats, conds, bold=False)
    if _hl:
        lines.append(f"   {_hl}")
    _bl = _basics_line(basics, bold=False)
    if _bl:
        lines.append(f"   {_bl}")
    _sl = _sector_line(sector, bold=False)
    if _sl:
        lines.append(_sl)
    # ⚠️📈 背景标签(黑天鹅/业绩预增)
    _bg = _background_line(background, bold=False)
    if _bg:
        for _t in _bg.split("\n"):
            lines.append(f"   {_t}")
    # 战绩(企微无表格 → 近3月/近6月 对齐文本块)
    if direction == "buy":
        lines.extend(_build_model_stats_block(model_stats))

    # 我的策略(用户预设) — 文本渠道不可展开, 截首句保持简洁
    if strategy and strategy.strip():
        s_one = " · ".join(x.strip() for x in strategy.strip().split("\n") if x.strip())
        s_show = _first_sentence(s_one)
        lines.append(sep)
        lines.append(f"📌 我的策略  {s_show}")

    # 触发条件(合并一行) / 交易计划(一行) / 共振+成交(一行)
    body_conds = conds if direction == "buy" else [c for c in conds if not ("成本" in c or "浮" in c)]
    body = []
    if body_conds:
        body.append(f"🎯 个股实情：{' · '.join(body_conds)}")
    if amount and direction != "buy":
        body.append(f"💰 {amount}")
    rule = MODEL_RULES.get(signal_name, "")
    if rule:
        body.append(f"📐 模型规则：{rule}")
    if plan:
        body.append("📋 计划  " + " · ".join(x.strip() for x in plan.split("/") if x.strip()))
    if reso:
        body.append(f"🔗 {reso}")
    if body:
        lines.append(sep)
        lines.extend(body)

    if username:
        lines.append(f"\n@{username}")
    return "\n".join(lines)


def _build_lark_signal(code: str, name: str, signal_name: str, direction: str,
                       price: float, detail: str, strategy: str = "",
                       pct_change: float = 0.0, model_stats: dict | None = None,
                       basics: dict | None = None, sector: dict | None = None,
                       background: dict | None = None) -> str:
    """飞书卡片正文(lark_md): 股票名/现价加粗, 策略/要点分节. 信号头放在卡片彩色标题栏."""
    # 头 + KPI 一行
    lines: list[str] = [
        f"**{name}**　`{code}`",
        _kpi_line(price, pct_change, direction, model_stats, bold=True),
    ]
    _bl = _basics_line(basics, bold=True)
    if _bl:
        lines.append(_bl)
    _sl = _sector_line(sector, bold=True)
    if _sl:
        lines.append(_sl)
    # ⚠️📈 背景标签(黑天鹅/业绩预增)
    _bg = _background_line(background, bold=True)
    if _bg:
        lines.append(_bg)
    if direction == "buy":
        lines.extend(_build_model_stats_lark(model_stats))

    # 我的策略(用户预设) — 紧凑一行
    if strategy and strategy.strip():
        s_one = " · ".join(x.strip() for x in strategy.strip().split("\n") if x.strip())
        lines.append(f"\n📌 **我的策略**　{s_one}")

    # 触发条件(合并一行) / 计划(一行) / 共振+成交(一行), 关键数字加粗
    conds, plan, reso, amount = _split_detail_sections(detail)
    if conds:
        lines.append("\n🎯 个股实情：" + " · ".join(_bold_nums(c) for c in conds))
    if amount:
        lines.append(f"💰 {_bold_nums(amount)}")
    rule = MODEL_RULES.get(signal_name, "")
    if rule:
        lines.append(f"\n📐 模型规则：{rule}")
    if plan:
        lines.append("📋 **计划**　" + " · ".join(_bold_nums(x.strip()) for x in plan.split("/") if x.strip()))
    if reso:
        lines.append(f"🔗 {reso}")
    return "\n".join(lines)


def _build_pushplus_html(code: str, name: str, signal_name: str, direction: str,
                          price: float, detail: str, strategy: str = "",
                          pct_change: float = 0.0, model_stats: dict | None = None,
                          basics: dict | None = None, sector: dict | None = None,
                          background: dict | None = None) -> str:
    """微信(PushPlus)信号正文 — 复用飞书同一份 lark_md(_build_lark_signal)再转 HTML,
    保证内容/分节/加粗与飞书卡片完全一致(飞书 v2 卡的战绩原生表格此处呈现为同数据的文本块)。"""
    md = _build_lark_signal(code, name, signal_name, direction, price, detail,
                            strategy, pct_change, model_stats, basics, sector, background)
    return _lark_md_to_html(md)


async def send_wechat_signal(code: str, name: str, signal_name: str,
                             direction: str, price: float, detail: str,
                             user_id: int | None = None,
                             strategy: str = "",
                             pct_change: float = 0.0,
                             model_stats: dict | None = None,
                             signal_id: str = "") -> bool:
    # IP 网关：只允许生产服务器 IP 推送，本地开发跳过避免重复
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[wechat_signal] 非生产环境 IP={ip}，跳过推送: {name}({code}) {signal_name}")
        return False

    from backend.models import repository

    # 飞书 webhook 已统一为单一全局配置(不再分个人/全局); user_id 仅用于 username 与推送偏好闸门
    username = ""
    if user_id:
        user = await repository.get_user_by_id(user_id)
        if not user:
            logger.info(f"用户{user_id}不存在，跳过: {name}({code}) {signal_name}")
            return False
        username = user.get("username", "")
    cfg = load_config()
    lark_webhook = cfg.get("lark_webhook", "")
    lark_on = bool(cfg.get("lark_enabled", False))

    # 推送偏好闸门(快捷设置): 个股snooze/模型今日关/已标记 → 全渠道不推; 今日免打扰 → 仅静音飞书
    mute_lark = False
    try:
        from backend.services import push_pref as _pref_svc
        from backend.models.repo import push_pref as _pref_repo
        _prefs = await _pref_repo.active_prefs(user_id or 1)
        _verdict = _pref_svc.decide(_prefs, code, signal_id)
        if _verdict["suppress_all"]:
            logger.info(f"[push_pref] 抑制推送({_verdict['reason']}): {name}({code}) {signal_name}")
            return False
        mute_lark = _verdict["mute_lark"]
        # 条件型静音「直到再次突破」: 上一交易日也触发=连续→压住; 昨没触发=新一轮突破→撤销静音放行
        _rt_probe = _pref_svc.retrigger_verdict(_prefs, code, signal_id, False)
        if _rt_probe["has_snooze"] and code and signal_id:
            from backend.core.trading_calendar import prev_trading_day
            from backend.models.repo import signals as _sig_repo
            _prev = prev_trading_day()
            _prev_hit = await _sig_repo.signal_triggered_on(code, signal_id, _prev.isoformat(), user_id or 1)
            _rt = _pref_svc.retrigger_verdict(_prefs, code, signal_id, _prev_hit)
            if _rt["suppress"]:
                logger.info(f"[push_pref] 抑制推送(直到再突破·连续触发中): {name}({code}) {signal_name}")
                return False
            if _rt["revoke_id"] is not None:
                await _pref_repo.revoke(user_id or 1, _rt["revoke_id"])
                logger.info(f"[push_pref] 新一轮突破, 撤销条件静音放行: {name}({code}) {signal_name}")
    except Exception as e:
        logger.warning(f"[push_pref] 闸门异常, 放行: {e}")

    # v1.7.x: 市场风险预警期间, 买点推送统一标记 (替代v1.7.406空仓预警)
    if direction == "buy":
        try:
            from backend.services.market_risk_controller import get_risk_state
            risk_state = await get_risk_state()
            if risk_state == "RED":
                detail = f"⚠️ 市场风险·空仓预警(RED): 回测期内信号胜率30%均值-3.6%, 强烈建议停开新仓\n{detail}"
            elif risk_state == "YELLOW":
                detail = f"⚡ 市场风险·谨慎(YELLOW): 轻度预警, 信号质量未显著下降, 注意风控\n{detail}"
        except Exception:
            pass

    basics = await _fetch_signal_basics(code, user_id)
    sector = None
    try:
        from backend.services import sector_context
        sector = await sector_context.get_sector_brief(code, user_id)
    except Exception as e:
        logger.warning(f"[sector_brief] 取板块情况失败, 略过: {e}")
    background = None
    try:
        from backend.services import signal_background as _sbg
        background = await _sbg.fetch_background(code)
    except Exception as e:
        logger.warning(f"[signal_background] 取黑天鹅/预增背景失败, 略过: {e}")
    content = _build_text(code, name, signal_name, direction, price, detail, username, strategy, pct_change, model_stats, basics, sector, background)

    # 飞书并推(独立通道, 不受企微开关影响) — 交互卡片: 信号头进彩色标题栏, 正文加粗
    lark_ok = False
    if lark_on and lark_webhook and not mute_lark:
        from backend.services import lark_notifier
        from backend.services import push_pref as _pref_svc
        lark_title, risk_banner = await _risk_deco(
            f"{DIRECTION_EMOJI.get(direction, '')} {DIRECTION_SHORT.get(direction, '')} · [{signal_name}]")
        lark_template = lark_notifier.DIRECTION_TEMPLATE.get(direction, "blue")
        # 个股信号卡片底部加"查看分时图"按钮, 跳系统分时页 (大盘预警 plunge 不挂个股图)
        link = ""
        site = (load_config().get("site_url", "") or "").rstrip("/")
        if site and direction != "plunge" and code:
            from urllib.parse import quote
            link = f"{site}/intraday?code={code}&name={quote(name)}"
        # 快捷设置动作行(今日免打扰/个股静音/关模型/标记已处理), 走带签名的 /api/quick 链接
        quick_md = _pref_svc.build_quick_actions_md(site, user_id or 1, code, signal_id, direction)
        # 卖出/减仓类卡加「已卖出」: 点了这只票从持仓消失 + 压后续卖出/减仓/持仓提醒(买点仍照常)
        if direction in ("sell", "reduce") and code:
            sold_md = _pref_svc.build_mark_sold_md(site, user_id or 1, code, name)
            if sold_md:
                quick_md = f"{quick_md}　·　{sold_md}" if quick_md else sold_md
        # v2 卡(战绩走原生表格); 失败回退旧 markdown 卡
        elements = _build_signal_elements(code, name, signal_name, direction, price, detail,
                                          strategy, pct_change, model_stats, basics, sector, background)
        # 快捷动作: 应用机器人通道 → 真回调按钮(点击不跳页, 原地toast, v1.7.631);
        #           webhook 通道 → 原 markdown 签名链接行
        app_buttons = False
        try:
            from backend.services import lark_app
            app_buttons = lark_app.enabled()
        except Exception:
            app_buttons = False
        if app_buttons:
            elements = list(elements) + _pref_svc.build_quick_action_button_rows(
                user_id or 1, code, signal_id, direction)
        elif quick_md:
            elements = list(elements) + [lark_notifier.md_element(quick_md)]
        if risk_banner:
            elements = [lark_notifier.md_element(risk_banner)] + list(elements)
        lark_ok = await lark_notifier.post_lark_card_v2(
            lark_webhook, lark_title, elements, lark_template, link_url=link, link_text="查看分时图")
        if not lark_ok:
            lark_body = _build_lark_signal(code, name, signal_name, direction, price, detail,
                                           strategy, pct_change, model_stats, basics, sector, background)
            if quick_md:
                lark_body = f"{lark_body}<br>{quick_md}"
            if risk_banner:
                lark_body = f"{risk_banner}<br>{lark_body}"
            lark_ok = await lark_notifier.post_lark_card(
                lark_webhook, lark_title, lark_body, lark_template, link)

    wx_token = load_config().get("wxpusher_token", "")
    wx_uids = load_config().get("wxpusher_uids", [])
    wx_ok = await _post_wxpusher(wx_token, wx_uids, content, "wxpusher")

    pp_cfg = load_config()
    pp_ok = False
    if pp_cfg.get("pushplus_enabled", True):
        pp_title, pp_banner = await _risk_deco(
            f"{DIRECTION_EMOJI.get(direction,'')} {DIRECTION_SHORT.get(direction,'')} · [{signal_name}] — {name}")
        pp_html = _build_pushplus_html(code, name, signal_name, direction, price, detail, strategy, pct_change, model_stats, basics, sector, background)
        if pp_banner:
            pp_html = f"<div style='font-size:15px;margin-bottom:6px'>{pp_banner}</div>{pp_html}"
        pp_ok = await _post_pushplus(pp_cfg.get("pushplus_token", ""), pp_title, pp_html, "pushplus")

    if lark_ok or wx_ok or pp_ok:
        logger.info(f"信号推送 lark={lark_ok} wx={wx_ok} pp={pp_ok}: {name}({code}) {signal_name}")
    return lark_ok or wx_ok or pp_ok


async def send_wechat_markdown(content: str) -> bool:
    """企业微信 markdown 格式推送。支持标题/加粗/引用块(>)/字体颜色等。

    企业微信 markdown 限制 ≤ 4096 字符,这里取 4000 留余量。
    """
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[wechat_md] 非生产环境 IP={ip}，跳过 markdown 推送")
        return False
    cfg = load_config()
    title, banner = await _risk_deco("📊 盘面分析")
    body = _time_prefix() + (banner + "\n\n" if banner else "") + content

    # 飞书走交互卡片, lark_md 渲染 **加粗** (企微 markdown 的加粗语法两边通用)
    lark_ok = await _fanout_lark(cfg.get("lark_webhook", ""), cfg.get("lark_enabled", False), body,
                                 title=title)
    pp_ok = await _fanout_pushplus(title, body)
    return lark_ok or pp_ok


async def send_wechat_text(content: str, *, mute_lark: bool = False) -> bool:
    """通用文本推送(企业微信)。用于汇总报告类消息(如 S0 快照、自定义播报)。

    mute_lark=True: 今日免打扰命中 → 仅静音飞书, 其他渠道照常(v1.7.569, 供合并推送透传偏好闸)。
    """
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[wechat_text] 非生产环境 IP={ip}，跳过文本推送")
        return False
    cfg = load_config()
    title, banner = await _risk_deco("📊 盘面播报")
    body = _time_prefix() + (banner + "\n\n" if banner else "") + content

    lark_ok = False
    if not mute_lark:
        lark_ok = await _fanout_lark(cfg.get("lark_webhook", ""), cfg.get("lark_enabled", False), body,
                                     title=title)
    pp_ok = await _fanout_pushplus(title, body)
    return lark_ok or pp_ok


# ── 大盘风险标记(v1.7.628, v1.7.629 加正文横幅) ──
# 大盘风险(谨慎/空仓)生效期间, 所有推送做双重醒目标记:
#   1) 标题前缀(手机通知栏第一眼) 2) 正文顶部红色加粗大横幅(点开第一行)。
# 市场风险状态卡/大盘风控卡本身不挂(它们就是在宣布这件事, 再挂就重复)。
_RISK_TITLE_SKIP = ("市场风险", "大盘风控")
_RISK_TITLE_PREFIX = {"RED": "🚨大盘空仓中🚨 ", "YELLOW": "⚠️大盘谨慎中 "}
# <font color> 飞书 lark_md 与 PushPlus HTML 两端都渲染, 一份横幅两端通用。
# {since} = 时间锚点(如「（13:11起）」, 对标状态页 since 模式), 无锚点时为空串。
_RISK_BANNER = {
    "RED": "<font color='red'>**🚨🚨🚨 大盘空仓中{since} —— 停开新仓、别抄底、先保命 🚨🚨🚨**</font>",
    "YELLOW": "<font color='orange'>**⚠️⚠️ 大盘谨慎中{since} —— 控制仓位、别追高 ⚠️⚠️**</font>",
}


async def _risk_deco(title: str) -> tuple[str, str]:
    """(带前缀的标题, 正文横幅md)。无风险/风险卡自身/取态失败 → (原标题, '')。"""
    try:
        if any(k in (title or "") for k in _RISK_TITLE_SKIP):
            return title, ""
        from backend.services.market_risk_controller import get_risk_state_info
        st, since = await get_risk_state_info()   # 2分钟缓存, 不加DB压力
    except Exception:
        return title, ""
    if st not in _RISK_TITLE_PREFIX:
        return title, ""
    banner = _RISK_BANNER[st].format(since=f"（{since}起）" if since else "")
    return _RISK_TITLE_PREFIX[st] + title, banner


async def _with_risk_flag(title: str) -> str:
    """只要标题的旧接口(兼容既有调用/测试)。"""
    return (await _risk_deco(title))[0]


async def send_dual(content: str, *, lark_title: str = "📊 盘面播报",
                    template: str = "blue", wecom_markdown: bool = False) -> bool:
    """通用双通道推送(企微+飞书卡片), 飞书卡片标题可自定义。给复盘摘要等自定义播报用。"""
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[send_dual] 非生产环境 IP={ip}，跳过推送: {lark_title}")
        return False
    cfg = load_config()
    lark_title, banner = await _risk_deco(lark_title)
    body = _time_prefix() + (banner + "\n\n" if banner else "") + content
    lark_ok = await _fanout_lark(cfg.get("lark_webhook", ""), cfg.get("lark_enabled", False),
                                 body, title=lark_title, template=template)
    pp_ok = await _fanout_pushplus(lark_title, body)
    return lark_ok or pp_ok


async def send_dual_card(content: str, *, lark_title: str, elements: list,
                         link_url: str = "", link_text: str = "",
                         template: str = "blue") -> bool:
    """企微纯文本 + 飞书原生表格卡(schema 2.0, post_lark_card_v2); 飞书表格卡失败回退纯文本卡。
    给「带表格的合并推送」用(如资金回流·板块预警的自选个股网格)。企微无原生表格, 始终发 content 文本兜底。"""
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[send_dual_card] 非生产环境 IP={ip}，跳过推送: {lark_title}")
        return False
    cfg = load_config()
    lark_title, banner = await _risk_deco(lark_title)
    body = _time_prefix() + (banner + "\n\n" if banner else "") + content

    lark_ok = False
    lark_webhook = cfg.get("lark_webhook", "")
    if cfg.get("lark_enabled", False) and lark_webhook:
        if elements:
            from backend.services import lark_notifier
            if banner:
                elements = [lark_notifier.md_element(banner)] + list(elements)
            lark_ok = await lark_notifier.post_lark_card_v2(
                lark_webhook, lark_title, elements, template=template,
                link_url=link_url, link_text=link_text)
            if not lark_ok:
                logger.warning(f"[send_dual_card] 飞书表格卡失败, 回退纯文本卡: {lark_title}")
        if not lark_ok:
            lark_ok = await _fanout_lark(lark_webhook, True, body, title=lark_title, template=template)

    pp_ok = await _fanout_pushplus(lark_title, body)
    return lark_ok or pp_ok


async def send_dual_card_to(content: str, *, lark_title: str, elements: list,
                            lark_webhook: str = "", lark_on: bool = False,
                            link_url: str = "", link_text: str = "") -> bool:
    """与 send_dual_card 同, 但飞书推送目标由调用方显式指定(多用户场景)。
    飞书原生表格卡(失败回退纯文本卡) + PushPlus(个人微信, 全局)。非生产环境(IP 网关)一律跳过。"""
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[send_dual_card_to] 非生产环境 IP={ip}，跳过推送: {lark_title}")
        return False
    lark_title, banner = await _risk_deco(lark_title)
    body = _time_prefix() + (banner + "\n\n" if banner else "") + content

    lark_ok = False
    if lark_on and lark_webhook:
        if elements:
            from backend.services import lark_notifier
            if banner:
                elements = [lark_notifier.md_element(banner)] + list(elements)
            lark_ok = await lark_notifier.post_lark_card_v2(
                lark_webhook, lark_title, elements, link_url=link_url, link_text=link_text)
            if not lark_ok:
                logger.warning(f"[send_dual_card_to] 飞书表格卡失败, 回退纯文本卡: {lark_title}")
        if not lark_ok:
            lark_ok = await _fanout_lark(lark_webhook, True, body, title=lark_title)

    pp_ok = await _fanout_pushplus(lark_title, body)
    return lark_ok or pp_ok


def _signals_tables(context: dict | None) -> list:
    """今日信号(板块预警/个股信号/卖出) → 飞书原生表格元素。
    仅当有结构化分组(buy_sectors/buy_stocks/sell_groups)时生效; 旧扁平数据返回 [] 留给文本。"""
    sig = (context or {}).get("signals_summary") or {}
    if not sig or sig.get("total", 0) <= 0:
        return []
    sectors = sig.get("buy_sectors") or []
    stocks = sig.get("buy_stocks") or []
    sell_groups = sig.get("sell_groups") or []
    if not (sectors or stocks or sell_groups):
        return []
    from backend.services import lark_notifier

    def _win(s: dict) -> str:
        w = s.get("win")
        return f"{w['rate']:.0f}%({w['success']}/{w['evaluated']})" if w else "—"

    # v1.7.x: 列宽改百分比(合计100%) — 飞书原生表格手机端不横向滚动, auto/px 列总宽超屏会把右列裁掉(末列"胜率/差额"看不全)。
    #   百分比按卡片画布宽度分配, 保证三列永远适配手机宽度。
    name_col = {"name": "name", "display_name": "名称", "data_type": "text", "width": "38%"}
    win_col = {"name": "win", "display_name": "胜率", "data_type": "text",
               "width": "28%"}
    elements = [lark_notifier.md_element(
        f"📊 **今日信号** 共{sig.get('total', 0)}个（🟢买{sig.get('buy', 0)} 🔴卖{sig.get('sell', 0)}）")]

    if sectors:
        # 板块预警: 不要胜率列(预警非买卖点, 无胜率意义); 预警类型统一时折进标题
        #   (飞书表格不支持真合并单元格 → 用"折标题"做等效, 不在每行重复同一类型), 多类型才保留预警列。
        sec_types = [s["signal_name"].replace("·板块预警", "").replace("板块预警", "").strip() or s["signal_name"]
                     for s in sectors]
        uniq = list(dict.fromkeys(sec_types))
        if len(uniq) == 1:
            elements.append(lark_notifier.md_element(f"**🟢 板块预警 · {uniq[0]}（{len(sectors)}）**"))
            cols = [{"name": "name", "display_name": "名称"}]
            rows = [{"name": s['name']} for s in sectors]
        else:
            elements.append(lark_notifier.md_element(f"**🟢 板块预警（{len(sectors)}）**"))
            cols = [{"name": "name", "display_name": "名称"}, {"name": "sig", "display_name": "预警"}]
            rows = [{"name": s['name'], "sig": t} for s, t in zip(sectors, sec_types)]
        elements.append(lark_notifier.md_table(cols, rows))
    if stocks:
        # 移动优化: 2列(名称|信号), 名称去代码省宽; 胜率下沉(个股卡自带战绩), 长信号名独占宽列
        cols = [{"name": "name", "display_name": "名称"}, {"name": "sig", "display_name": "信号"}]
        rows = []
        for s in stocks:
            sg = s["signal_name"].replace("（左侧）", "").replace("(左侧)", "").strip() or s["signal_name"]
            rows.append({"name": s['name'], "sig": sg})
        elements.append(lark_notifier.md_element(f"**🟢 个股信号（{len(stocks)}）**"))
        elements.append(lark_notifier.md_table(cols, rows))
    if sell_groups:
        from backend.services.signal_specs import SELL_CATEGORY_LABEL, SELL_CATEGORY_EMOJI
        cols = [{"name": "name", "display_name": "名称"}, {"name": "sig", "display_name": "信号"}]
        elements.append(lark_notifier.md_element(f"**🔴 卖出（{len(sell_groups)}）**"))
        # 三组: 主动止盈 / 被动止损 / 纪律清仓, 各起一张小表(空组跳过)
        for cat in ("profit", "loss", "discipline"):
            cat_groups = [g for g in sell_groups if g.get("category", "loss") == cat]
            if not cat_groups:
                continue
            rows = [{"name": g['name'], "sig": " / ".join(g["signals"])} for g in cat_groups]
            elements.append(lark_notifier.md_element(
                f"{SELL_CATEGORY_EMOJI[cat]} {SELL_CATEGORY_LABEL[cat]}（{len(cat_groups)}）"))
            elements.append(lark_notifier.md_table(cols, rows))
    return elements


def _buy_tracking_tables(context: dict | None) -> list:
    """买点盈利跟踪 → 飞书原生表格元素(今日/昨日各一表; 差额红涨绿跌)。无数据返回 []。"""
    bt = (context or {}).get("buy_tracking") or {}
    if not (bt.get("today") or bt.get("yest")):
        return []
    from backend.services import lark_notifier
    # 移动优化(v1.7.581): 逐条换行文本行, 差额(关键可扫值)红绿前置, 名称+买点全名换行不截
    #   (原 2列表格把 名称+买点 挤名称格, 手机端买点模型名被字符级截断)

    def _lines(items: list) -> list:
        out = []
        for it in items:
            pct = float(it.get("pct", 0) or 0)
            nm = str(it.get("name", ""))
            sig = str(it.get("signal", "")).strip()
            color = "red" if pct >= 0 else "green"
            tail = f"　{sig}" if sig else ""
            out.append(f"<font color='{color}'>{pct:+.2f}%</font>　**{nm}**{tail}")
        return out

    elements = [lark_notifier.md_element(f"💹 **买点盈利跟踪**（截至 {bt.get('as_of', '')}）")]
    for key, sum_key, label in (("today", "today_sum", "📍 今日买点"),
                                ("yest", "yest_sum", "📅 昨日买点")):
        items = bt.get(key) or []
        if not items:
            continue
        sm = bt.get(sum_key) or {}
        head = (f"{label} {sm.get('n', len(items))}只 · 均{sm.get('avg', 0):+.1f}%"
                f"（{sm.get('red', 0)}红{sm.get('green', 0)}绿）")
        elements.append(lark_notifier.md_element(f"**{head}**"))
        elements.append(lark_notifier.md_element("\n".join(_lines(items))))
    return elements


async def send_market_report(content: str, slot_name: str, context: dict | None = None,
                             *, extra_sections: str = "") -> bool:
    """盘面日报推送。extra_sections 非空时(收盘统一汇总)接在报告正文与"查看完整报告"链接之间。
    飞书侧: 买点跟踪这类多行多列信息用原生表格展示(schema 2.0); 表格卡失败自动回退纯文本卡。"""
    if not await is_production():
        ip = await get_outbound_ip()
        logger.info(f"[market_report] 非生产环境 IP={ip}，跳过推送 {slot_name}")
        return False
    cfg = load_config()
    site_url = cfg.get("site_url", "")
    title, banner = await _risk_deco(f"📊 盘面日报 · {slot_name}")

    # 企微/兜底纯文本(含买点跟踪文本段)
    text = _build_report_text(slot_name, context)
    if banner:
        text = f"{banner}\n\n{text}"
    if extra_sections:
        text += f"\n\n{extra_sections}"
    if site_url:
        text += f"\n\n👉 查看完整报告: {site_url}"
    text = text[:4000]

    # 飞书: 有买点跟踪 → 用原生表格卡(正文 markdown + 表格); 失败回退纯文本卡
    lark_ok = False
    lark_webhook = cfg.get("lark_webhook", "")
    if cfg.get("lark_enabled", False) and lark_webhook:
        from backend.services import lark_notifier
        sig_tables = _signals_tables(context)
        bt_tables = _buy_tracking_tables(context)
        if sig_tables or bt_tables:
            narrative = _build_report_text(slot_name, context,
                                           include_signals=not bool(sig_tables),
                                           include_buy_tracking=not bool(bt_tables))
            if extra_sections:
                narrative += f"\n\n{extra_sections}"
            elements = [lark_notifier.md_element(narrative[:3500])] + sig_tables + bt_tables
            if banner:
                elements = [lark_notifier.md_element(banner)] + elements
            lark_ok = await lark_notifier.post_lark_card_v2(
                lark_webhook, title, elements, link_url=site_url, link_text="查看完整报告")
            if not lark_ok:
                logger.warning("[market_report] 表格卡推送失败, 回退纯文本卡")
        if not lark_ok:
            lark_ok = await _fanout_lark(lark_webhook, True, text, title=title)

    pp_ok = await _fanout_pushplus(title, text)
    if lark_ok or pp_ok:
        logger.info(f"Market report pushed lark={lark_ok} pp={pp_ok}: {slot_name}")
    return lark_ok or pp_ok


def _build_report_text(slot_name: str, context: dict | None, *,
                       include_buy_tracking: bool = True, include_signals: bool = True) -> str:
    """将市场数据构建为纯文本格式摘要。
    include_buy_tracking / include_signals = False 时省略对应段(飞书侧改用原生表格单独渲染)。"""
    now = datetime.now()
    lines = [
        f"🕐 {now.strftime('%H:%M')}",
        f"📊 盘面日报 — {slot_name}",
        now.strftime('%Y-%m-%d %H:%M'),
    ]

    if not context:
        return "\n".join(lines)

    indices = context.get("indices", [])
    if indices:
        lines.append("")
        for i in indices:
            arrow = "📈" if i["pct_change"] > 0 else "📉"
            lines.append(f"{arrow} {i['name']} {i['price']:.2f} ({i['pct_change']:+.2f}%)")

    amount_cmp = context.get("amount_compare", {})
    main_indices = {"上证指数", "深证成指"}
    total_amount = sum(i.get("amount", 0) for i in indices if i.get("name") in main_indices) if indices else 0
    if total_amount > 0:
        from backend.services.ai_analyst import _estimate_full_day_amount
        now_time = datetime.now().strftime("%H:%M")
        estimated = _estimate_full_day_amount(total_amount, now_time)
        def _fmt_amount(v):
            if v >= 10000:
                return f"{v/10000:.2f}万亿"
            return f"{v:.0f}亿"
        amt_parts = [f"两市合计{_fmt_amount(total_amount)}"]
        if estimated > 0:
            amt_parts.append(f"预计全天{_fmt_amount(estimated)}")
        latest_cp = None
        for cp in ["15:00", "14:00", "11:30", "11:00", "10:00"]:
            if cp in amount_cmp:
                latest_cp = cp
                break
        if latest_cp:
            v = amount_cmp[latest_cp]
            amt_parts.append(f"较昨日同期{v['pct']:+.1f}%")
        lines.append(f"💰 {'｜'.join(amt_parts)}")

    stats = context.get("market_stats", {})
    if stats:
        lines.append("")
        lines.append(
            f"涨停 {stats.get('limit_up', 0)} 家 | "
            f"跌停 {stats.get('limit_down', 0)} 家"
        )
        lines.append(
            f"上涨 {stats.get('up_count', 0)} 家 | "
            f"下跌 {stats.get('down_count', 0)} 家"
        )

    hot_concepts = context.get("hot_concepts", [])
    if hot_concepts:
        lines.append("")
        lines.append(f"🔥 热点：{', '.join(c['name'] for c in hot_concepts[:5])}")

    signals = context.get("signals_summary")
    if include_signals and signals and signals["total"] > 0:
        lines.append("")
        lines.append(f"今日信号 共{signals['total']}个（🟢买{signals['buy']} 🔴卖{signals['sell']}）")

        # 买入: 板块预警 / 个股信号 两层分组
        if signals["buy"] > 0:
            lines.append(f"  🟢 买入({signals['buy']})")
            sectors = signals.get("buy_sectors") or []
            stocks = signals.get("buy_stocks") or []
            def _winsuffix(s):
                w = s.get("win")
                return f"  胜率{w['rate']:.0f}%({w['success']}/{w['evaluated']})" if w else ""
            if sectors:
                # 板块预警: 不要胜率; 类型统一折进标题(对齐飞书表格), 多类型才逐行带类型
                sec_types = [s["signal_name"].replace("·板块预警", "").replace("板块预警", "").strip() or s["signal_name"]
                             for s in sectors]
                uniq = list(dict.fromkeys(sec_types))
                if len(uniq) == 1:
                    lines.append(f"    📊 板块预警·{uniq[0]}({len(sectors)})")
                    for s in sectors:
                        lines.append(f"      {s['name']}({s['code']})")
                else:
                    lines.append(f"    📊 板块预警({len(sectors)})")
                    for s, t in zip(sectors, sec_types):
                        lines.append(f"      {s['name']}({s['code']}) · {t}")
            if stocks:
                lines.append(f"    📈 个股信号({len(stocks)})")
                for s in stocks:
                    sig = s["signal_name"].replace("（左侧）", "").replace("(左侧)", "").strip() or s["signal_name"]
                    lines.append(f"      {s['name']}({s['code']}) · {sig}{_winsuffix(s)}")
            # 旧数据兜底: 没有结构化分组时回退到扁平 buy_details
            if not sectors and not stocks and signals.get("buy_details"):
                for d in signals["buy_details"]:
                    lines.append(f"    {d}")

        # 卖出: 按个股聚合, 同股多信号合并, 三组(主动止盈/被动止损/纪律清仓)
        if signals["sell"] > 0:
            lines.append(f"  🔴 卖出({signals['sell']})")
            groups = signals.get("sell_groups") or []
            if groups:
                from backend.services.signal_specs import SELL_CATEGORY_LABEL, SELL_CATEGORY_EMOJI
                for cat in ("profit", "loss", "discipline"):
                    cat_groups = [g for g in groups if g.get("category", "loss") == cat]
                    if not cat_groups:
                        continue
                    lines.append(f"    {SELL_CATEGORY_EMOJI[cat]} {SELL_CATEGORY_LABEL[cat]}({len(cat_groups)})")
                    for g in cat_groups:
                        lines.append(f"      {g['name']}({g['code']}) {' / '.join(g['signals'])}")
            elif signals.get("sell_details"):
                for d in signals["sell_details"]:
                    lines.append(f"    {d}")

    # 买点盈利跟踪(午盘/收盘): 今日/昨日买点 触发价→现价 差额%
    bt = context.get("buy_tracking")
    if include_buy_tracking and bt and (bt.get("today") or bt.get("yest")):
        def _bt_group(title: str, items: list, sm: dict) -> list[str]:
            if not items:
                return [f"{title} 暂无"]
            head = f"{title} {sm['n']}只 · 均{sm['avg']:+.1f}%（{sm['red']}红{sm['green']}绿）"
            rows = [f"   {it['name']}  {it['signal']}  当前差额：{it['pct']:+.2f}%" for it in items]
            return [head] + rows
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"💹 买点盈利跟踪（截至 {bt.get('as_of', '')}）")
        lines.append("")
        lines.extend(_bt_group("📍 今日买点", bt.get("today") or [], bt.get("today_sum") or {}))
        lines.append("")
        lines.extend(_bt_group("📅 昨日买点", bt.get("yest") or [], bt.get("yest_sum") or {}))

    return "\n".join(lines)


async def send_pushplus_test() -> tuple[bool, str]:
    """PushPlus(个人微信)连接测试. token 取自全局配置。"""
    token = load_config().get("pushplus_token", "")
    if not token:
        return False, "PushPlus token 未配置, 请先填写并保存"
    content = ("🟢 <b>交易监控系统 — PushPlus 连接测试</b><br>观潮已成功连接！<br>"
               "盘中触发买卖信号时将推送到此(个人微信)。<br>信号类型：🟢买入 🔴卖出 🟡减仓")
    ok = await _post_pushplus(token, "观潮 · 连接测试", content, "pushplus_test")
    return (ok, "推送成功！请检查微信是否收到。") if ok else (False, "推送失败: 检查 PushPlus token 是否正确")
