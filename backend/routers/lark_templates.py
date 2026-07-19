# -*- coding: utf-8 -*-
"""飞书推送模版预览 API — 直接调用真实构卡函数生成预览 JSON, 与真实推送 1:1.

改造(基线 v1.1 落地): 原手工镜像卡片 JSON(与 service 双份维护、必然漂移) → 逐张 import
对应 service 的真实构卡纯函数, 用逼真样例数据调用, 经 lark_notifier._build_card_v2
(真实推送同一信封: summary/subtitle/text_tags/家族色)组装完整卡片 JSON 注册。
构卡逻辑改版时预览页自动跟进, 不再手工同步。

仍为手工镜像的卡(各处注释标明"手工镜像"):
  - market_risk_controller 状态卡 4 张: 该文件正在被同事修复(import SyntaxError),
    修复后应改直调 _push_state_card / emit_risk_dimension 版式
  - 竞价分析·板块强弱: 构卡内联在 async 网络编排函数里, 用其真实纯件
    (_board_row/_relay_advice) + card_kit 按 service 版式逐行对齐拼装
  - 资金回流·板块预警 / 错过消息回顾 / 恐慌底机会提示: 构卡内联在编排里, 手工对齐

前端 LarkCardPreview 渲染; 路由契约(GET 列表/详情)与分类不变。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from backend.routers.signals import get_current_user
from backend.services import card_kit
from backend.services.lark_notifier import (
    DIRECTION_TEMPLATE,
    _build_card_v2,
    md_element as _md,
)

router = APIRouter(prefix="/api/admin/lark-templates", tags=["lark-templates"])


# ══════════════════════════════════════════════════════════════
# 小助手: 真实信封(与 notifier.send_card → post_lark_card_v2 同路径)
# ══════════════════════════════════════════════════════════════

def _v2(title: str, elements: list, template: str, *, summary: str = "",
        subtitle: str = "", tags: list | None = None,
        link_url: str = "", link_text: str = "") -> dict:
    """完整飞书 2.0 卡 JSON — 走真实 lark_notifier._build_card_v2(标题栏自动拼时间)。"""
    return _build_card_v2(title, list(elements), template,
                          link_url=link_url, link_text=link_text or "查看详情",
                          summary=summary, subtitle=subtitle,
                          text_tags=list(tags or []))


def _card_json(card, extra_elements: list | None = None) -> dict:
    """card_kit.Card → 完整卡 JSON(信封字段 1:1 取自 Card, 同 notifier.send_card)。"""
    els = list(card.elements) + list(extra_elements or [])
    return _v2(card.title, els, card.template, summary=card.summary,
               subtitle=card.subtitle, tags=card.tags,
               link_url=card.link_url, link_text=card.link_text)


def _card_v1(title: str, template: str, md_body: str,
             link_url: str = "", link_text: str = "") -> dict:
    """飞书 1.0 卡(个别旧通道卡仍用)。"""
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": md_body}}]
    if link_url:
        elements.append({
            "tag": "action",
            "actions": [{"tag": "button",
                         "text": {"tag": "plain_text", "content": link_text or "查看详情"},
                         "type": "primary", "url": link_url}],
        })
    return {
        "config": {"wide_screen_mode": True, "width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
        "elements": elements,
    }


# 快捷设置动作行 demo(真实推送由 push_pref.build_quick_actions_md 生成带 HMAC 签名链接,
# 预览用 # 链接做视觉展示, 版式 1:1)。「今日免打扰/静音此股」已拆除(2026-07), 只剩条件式静音入口。
_QA_BUY = "[🔕 静到再突破](#)"
_QA_SELL = _QA_BUY + "　·　[✅ 已卖出](#)"
_QA_SURGE = "[🔕 当日不提醒](#)　·　[🔕 本周不提醒](#)"


def _signal_card(*, code: str, name: str, signal_name: str, direction: str,
                 price: float, pct_change: float, detail: str, strategy: str = "",
                 model_stats: dict | None = None, basics: dict | None = None,
                 sector: dict | None = None, background: dict | None = None) -> dict:
    """个股买卖点卡 — 直调 notifier._build_signal_elements(真实构卡), 信封/标题/彩签
    与 notifier.send_wechat_signal 的飞书路径逐项对齐(标题公式/摘要/模型彩签/动作行)。"""
    from backend.services import notifier
    elements = notifier._build_signal_elements(
        code, name, signal_name, direction, price, detail, strategy,
        pct_change, model_stats, basics, sector, background)
    qa = _QA_SELL if direction in ("sell", "reduce") else _QA_BUY
    elements = list(elements) + [_md(qa)]
    title = (f"{notifier.DIRECTION_EMOJI.get(direction, '')} "
             f"{notifier.DIRECTION_SHORT.get(direction, '')} · {name}({code})")
    summary = card_kit.summary_text(
        name, code, signal_name, f"¥{price:.2f}" if price else "",
        f"{pct_change:+.1f}%" if pct_change else "")
    tag_color = {"buy": "red", "sell": "green"}.get(direction, "orange")
    tags: list = [(signal_name, tag_color)] if signal_name else []
    if direction == "buy" and model_stats and model_stats.get("rank_3m"):
        tags.append((f"第{model_stats['rank_3m']}名", "orange"))
    return _v2(title, elements, DIRECTION_TEMPLATE.get(direction, "blue"),
               summary=summary, tags=tags, link_url="#", link_text="查看分时图")


def _ms(model_name: str, wr3, net3, n3, wr6, net6, n6,
        rank: int | None = None, rank_n: int = 9) -> dict:
    """模型战绩样例(与 buy_model_stats 行结构一致)。"""
    ms = {"model_name": model_name, "win_rate_3m": wr3, "net_3m": net3, "n_3m": n3,
          "win_rate_6m": wr6, "net_6m": net6, "n_6m": n6}
    if rank:
        ms.update(rank_3m=rank, rank_n=rank_n)
    return ms


# ── 模版注册 ──

TEMPLATES = []


def register(category: str, name: str, desc: str, card_json: dict, timing: str = ""):
    TEMPLATES.append({
        "id": f"{category}/{name}",
        "category": category,
        "name": name,
        "description": desc,
        "timing": timing,
        "card": card_json,
    })


# ═══════════════════════════════════════════
# 一、买入信号(直调 notifier._build_signal_elements, KPI三栏/触发模型行/胜率条)
# ═══════════════════════════════════════════

register("买入信号", "回踩10MA缩量后突破昨高",
         "检测到回踩MA10缩量后放量突破昨高, 触发买入(本例带市场风险RED横幅+黑天鹅/预增背景标签)",
         _signal_card(
             code="002407", name="多氟多", signal_name="回踩10MA缩量后突破昨高",
             direction="buy", price=33.18, pct_change=2.09,
             detail=("🔴 大盘空仓档 · 大盘整体走弱，但该模型在此档历史上接近打平"
                     "(PF≈1.0)，若做务必轻仓\n"
                     "缩量回踩MA10(昨量是均量的0.62倍) | 突破昨高2.5%(触发价31.73) "
                     "| 成交额3.5亿 | 交易计划: 目标35.80 / 止损30.50"),
             strategy=("回踩10日线缩量企稳是经典买点\n突破昨高确认\n"
                       "大涨减仓降成本反复跟踪\n跌破MA10离场"),
             model_stats=_ms("回踩10MA缩量后突破昨高", 61.0, 3.2, 18, 58.0, 2.9, 41, rank=2),
             basics={"amount_rank": 37, "popularity_rank": 52},
             sector={"industry": "化学制品", "label": "PCB·覆铜板", "status": "升温",
                     "emoji": "🔥", "today": 6, "seq": [4, 5, 6], "trend": "走强"},
             background={"forecast": {"predict_type": "预增", "amp_lower": 50, "amp_upper": 80},
                         "fin_risk": None,
                         "risk_anns": [{"tags": "大股东减持",
                                        "title": "关于控股股东减持股份的预披露公告",
                                        "ann_date": "2026-06-23"}]},
         ), "盘中实时 · 满足条件立即推送")

register("买入信号", "缩量后放量突破(无预警)",
         "正常市场环境下的缩量突破买点, 带模型战绩排名",
         _signal_card(
             code="603019", name="中科曙光", signal_name="缩量后放量突破（右侧）",
             direction="buy", price=49.47, pct_change=4.12,
             detail=("昨缩量(近10日均量的0.55倍) | 今日放量(均量的2.1倍、昨量的3.4倍) "
                     "| 突破昨高2.0%(触发价48.50) | 站上MA10 MA20 "
                     "| 成交额12.3亿 | 交易计划: 目标53.00 / 止损46.00"),
             strategy="缩量蓄势后放量突破跟进\n跌破MA10离场",
             model_stats=_ms("缩量后放量突破（右侧）", 65.0, 3.8, 21, 62.0, 3.4, 44, rank=1),
             basics={"amount_rank": 12, "popularity_rank": 18},
             sector={"industry": "计算机设备", "label": "AI算力·液冷", "status": "高潮",
                     "emoji": "🔥", "today": 9, "seq": [6, 8, 9], "trend": "走强"},
         ), "盘中实时 · 满足条件立即推送")

register("买入信号", "回踩20MA缩量后突破昨高",
         "检测到回踩MA20缩量后放量突破昨高, 触发买入",
         _signal_card(
             code="002484", name="江海股份", signal_name="回踩20MA缩量后突破昨高",
             direction="buy", price=21.86, pct_change=3.15,
             detail=("缩量回踩MA20(昨量是均量的0.71倍) | 突破昨高2.6%(触发价21.30) "
                     "| 成交额10.8亿 | 交易计划: 目标23.50 / 止损20.10"),
             model_stats=_ms("回踩20MA缩量后突破昨高", 54.0, 2.7, 13, 51.0, 2.3, 32, rank=5),
             sector={"industry": "被动元件", "label": "固态电池", "status": "启动",
                     "emoji": "⚡", "today": 4, "seq": [1, 2, 4], "trend": "走强"},
         ), "盘中实时 · 成交额破10亿即触发")

register("买入信号", "弱势极限",
         "缩量地量潜伏买点(左侧), 博回调结束反弹",
         _signal_card(
             code="600276", name="恒瑞医药", signal_name="弱势极限",
             direction="buy", price=27.42, pct_change=-1.15,
             detail=("贴MA20(距-1.2%) | 地量(今量是近10日最低的1.05倍、均量的0.62倍) "
                     "| 成交额6.2亿 | 交易计划: 左侧持有T+15 / 止损-12%"),
             strategy="左侧买点, 买在下跌中\n持有到T+15, 需扛得住继续下探",
             model_stats=_ms("弱势极限", 14.0, -5.8, 7, 16.0, -4.2, 25),
             sector={"industry": "化学制药", "label": "创新药", "status": "退潮",
                     "emoji": "📉", "today": 2, "seq": [5, 3, 2], "trend": "走弱"},
         ), "盘中实时 · 10:00起满足条件推送")

register("买入信号", "强势起点",
         "弱势极限缩量后放量启动确认(右侧), 触发买入",
         _signal_card(
             code="002600", name="领益智造", signal_name="强势起点",
             direction="buy", price=9.87, pct_change=4.55,
             detail=("昨日弱势极限地量 | 今放量(近期的2.4倍) | 涨幅+3.1%确认 "
                     "| 站上MA10/20 | 成交额11.5亿 | 交易计划: 目标10.60 / 止损9.20"),
             model_stats=_ms("强势起点", 67.0, 4.6, 9, 63.0, 4.1, 24, rank=1),
             sector={"industry": "消费电子", "label": "机器人", "status": "升温",
                     "emoji": "🔥", "today": 6, "seq": [3, 4, 6], "trend": "走强"},
         ), "盘中实时 · 10:00起满足条件推送")

register("买入信号", "中继平台突破",
         "多日横盘后尾盘确认突破平台上沿, 触发买入",
         _signal_card(
             code="300476", name="胜宏科技", signal_name="中继平台突破",
             direction="buy", price=112.30, pct_change=3.86,
             detail=("前12日横盘振幅9.8% | 收盘≥上沿×1.005 | 放量(均量的1.6倍) "
                     "| 成交额15.2亿 | 交易计划: 目标122.00 / 止损105.00"),
             strategy="尾盘确认: 必须等14:40, 早盘\"突破\"不算数",
             model_stats=_ms("中继平台突破", 53.0, 1.0, 15, 50.0, 0.7, 36, rank=6),
             sector={"industry": "印制电路板", "label": "PCB", "status": "高潮",
                     "emoji": "🔥", "today": 8, "seq": [4, 6, 8], "trend": "走强"},
         ), "尾盘14:40起 · 收盘确认后推送")

register("买入信号", "竞价弱转强",
         "9:26竞价高开弱转强(研发中·无回测), 情绪+竞价额双门控",
         _signal_card(
             code="002229", name="鸿博股份", signal_name="竞价弱转强",
             direction="buy", price=18.62, pct_change=5.20,
             detail=("昨缩量(均量的0.72倍) | 竞价高开5.2% | 竞价成交额1.8亿 "
                     "| 大盘极端情绪(昨涨停81家)"),
             strategy="研发中: 无历史回测, 信号很稀, 真实胜率待验",
         ), "9:26竞价撮合后立即推送")


# ── 二波过前高: 直调 second_surge.build_surge_card_v2(✅触发清单卡) ──

def _surge_preview() -> dict:
    from backend.services.second_surge import build_surge_card_v2
    items = [{
        "name": "卫星化学", "code": "002648", "action_md": _QA_SURGE,
        "r": {"price_now": 23.69, "day_pct": 4.1, "h1_time": "10:12", "H1": 23.40,
              "trough_pct": 1.8, "vol_mult": 2.1, "leg_rise_pct": 1.5,
              "ma20_now": 22.15, "ma20_prev": 21.90, "amount_yi": 4.6},
    }]
    return _card_json(build_surge_card_v2(items))


register("买入信号", "二波过前高",
         "盘中分时二波过前高实时提醒(✅触发清单卡): 第一波放量冲高→回落降温→第二波放量拉升"
         "创当日新高, 全自选每股每天一次, 只提醒不落信号库",
         _surge_preview(),
         "盘中09:45~15:00每30秒 · 每股每天一次 · surge_snooze逐票静音 · 20日线上翘闸")


# ── 弱势极限·候选快照: 直调 weak_extreme_scanner.build_weak_extreme_card ──

def _weak_extreme_preview() -> dict:
    from backend.services.weak_extreme_scanner import build_weak_extreme_card
    we_hits = [
        {"code": "000725", "name": "京东方A", "close": 3.45, "pct": -2.52, "amount": 8.9e8,
         "detail": "MA20距-1.8% | 地量(近10日最低×1.04、均量×0.58)"},
        {"code": "601318", "name": "中国平安", "close": 42.10, "pct": -1.23, "amount": 21.5e8,
         "detail": "MA10距-1.1% | 地量(近10日最低×1.08、均量×0.63)"},
        {"code": "600276", "name": "恒瑞医药", "close": 27.42, "pct": -1.15, "amount": 6.2e8,
         "detail": "MA20距-1.2% | 地量(近10日最低×1.05、均量×0.62)"},
    ]
    return _card_json(build_weak_extreme_card(we_hits, "尾盘快照"))


register("买入信号", "弱势极限·候选快照",
         "14:45尾盘单独推: 股票池中当前符合弱势极限条件的候选(结论行+全短列表+技术参数折叠)",
         _weak_extreme_preview(),
         "交易日14:45 · 股票池扫描弱势极限条件 · 无命中不推")


# ═══════════════════════════════════════════
# 二、卖出信号 / 离场家族
# ═══════════════════════════════════════════

register("卖出信号", "弱势极限止损卖出",
         "弱势极限持仓触发止损条件, 提醒卖出",
         _signal_card(
             code="002050", name="三花智控", signal_name="弱势极限·止损离场",
             direction="sell", price=18.20, pct_change=-3.20,
             detail=("成本21.50 当前18.20 浮亏-15.3% | 收盘价跌破止损线-12%(触发价19.10) "
                     "| 持仓12天(左侧出场·封顶T+15)"),
             strategy=("弱势极限建仓(左侧出场)\n止损-12% 封顶T+15\n"
                       "入场2026-07-01@22.80 最高@24.50(+7.5%)未触发止盈"),
             sector={"industry": "通用设备", "label": "机器人", "status": "退潮",
                     "emoji": "📉", "today": 2, "seq": [7, 4, 2], "trend": "走弱"},
         ), "盘中实时 · 止损触发立即推送")


# ── 尾盘破位警戒: 直调 ma_break_watch.build_watch_card ──

def _watch_preview() -> dict:
    from backend.services.ma_break_watch import build_watch_card
    items = [
        {"name": "圣泉集团", "code": "605589", "price": 55.70, "pct": -2.9,
         "streaks": {5: 4, 10: 4, 20: 3},
         "cost_break": {"price": 57.01, "date": "2026-06-11"},
         "actions_md": _QA_SURGE + "　·　[✅ 已卖出](#)"},
        {"name": "兆易创新", "code": "603986", "price": 118.50, "pct": -1.4,
         "streaks": {5: 2, 10: 1, 20: 0}, "cost_break": None,
         "actions_md": _QA_SURGE + "　·　[✅ 已卖出](#)"},
    ]
    watch_items = [
        {"name": "通富微电", "code": "002156", "price": 25.10, "pct": -3.2,
         "dist_pct": -1.6, "model": "缩量后放量突破（右侧）", "model_at": "2026-07-02"},
    ]
    return _card_json(build_watch_card(items, watch_items))


register("卖出信号", "尾盘破位警戒",
         "持仓14:40贴线判跌破MA5/MA10/MA20(同破多档只报最深)+主力成本线; 自选段今日新跌破MA20"
         "只报转弱当天; 每日尾盘复报直到收复",
         _watch_preview(),
         "交易日尾盘14:40 · 均线贴线判+主力成本线 · 连续N日从日线回算 · 收复即消失")


# ── 止损升级红卡: 直调 stop_escalation.build_escalation_card(唯一允许绿越级红) ──

def _escalation_preview() -> dict:
    from backend.services.stop_escalation import build_escalation_card
    return _card_json(build_escalation_card(
        name="天华新能", code="300390", day_n=3,
        first_stop_date="2026-07-13", first_stop_price=28.40, first_stop_pct=-8.0,
        current_price=26.10, current_pct=-15.2, extra_loss_yuan=2300,
        actions_md="[🔕 三天别提醒](#)　·　[✅ 已卖出](#)",
        history_md=("07-13 首次触发止损 @¥28.40（-8%）\n07-14 复喊未执行（-11%）\n"
                    "07-15 反弹未站回止损位（-12%）\n07-16 第3天, 现价 -15.2%"),
    ))


register("卖出信号", "止损未执行·纪律升级",
         "硬止损连续N天未执行 → 纪律升级红卡(基线: 唯一允许离场绿越级红), 带累计多亏金额",
         _escalation_preview(),
         "交易日09:30/11:20扫真实持仓 · 连续≥3天未执行触发 · 现价站回首次止损位自动熄火")


# ── 持仓守护: 直调 holding_guard.build_near_high_msg / build_profit_protect_msg ──

def _near_high_preview() -> dict:
    from backend.services.holding_guard import build_near_high_msg
    return _card_json(build_near_high_msg("沪电股份", "002463", 151.00, 153.80, "2026-06-30"),
                      extra_elements=[_md(_QA_SELL)])


register("卖出信号", "接近前高",
         "持仓逼近波段前高(阻力位)提醒: 距前高强度条+价格明细折叠, 放量站上是突破缩量到这是压力",
         _near_high_preview(),
         "盘中实时 · 每股每日一次 · 持仓守护族")


def _profit_protect_preview() -> dict:
    from backend.services.holding_guard import build_profit_protect_msg
    return _card_json(build_profit_protect_msg(
        "通富微电", "002156", peak_gain=0.12, cur_gain=0.02, cost=22.40,
        advisory="动量突破回踩常是洗盘，留意而非急走",
        model_name="缩量后放量突破（右侧）",
    ), extra_elements=[_md(_QA_SELL)])


register("卖出信号", "盈利保护",
         "赚过的票不做成亏损: 峰值浮盈≥10%回吐到贴成本线时提醒锁利(KPI三栏+回吐强度条)",
         _profit_protect_preview(),
         "盘中实时 · 每股每日一次 · model-dependent(动量回踩=洗盘提示)")


# ═══════════════════════════════════════════
# 三、持仓异动(直调 holding_anomaly.build_*_card + merge_anomaly_cards)
# ═══════════════════════════════════════════

def _anomaly_single_preview() -> dict:
    from backend.services.holding_anomaly import build_surge_card
    return _card_json(build_surge_card("三花智控", "002050", 4.2, 5, 21.80, 6.8))


register("风险预警", "持仓异动·急速拉升",
         "持仓分时急拉(N分钟涨幅跳升): 结论行heading+建议+明细折叠, 机会家族红卡",
         _anomaly_single_preview(),
         "盘中3秒行情扫描 · 每股每日限2次+冷却 · 同tick多项异动自动合并")


def _anomaly_merged_preview() -> dict:
    from backend.services.holding_anomaly import (
        build_board_anomaly_card, build_limit_up_card, merge_anomaly_cards,
    )
    c1 = build_limit_up_card("三花智控", "002050", 22.94, 10.0, 1.6e8, 2.4, 18.7e8)
    c2 = build_board_anomaly_card("三花智控", "002050", "up",
                                  peak_amt=2.3e8, cur_amt=0.9e8, surge_ratio=3.1)
    return _card_json(merge_anomaly_cards("三花智控", "002050", [c1, c2]))


register("风险预警", "持仓异动·多项合并",
         "同一时点多项异动合并一张卡(涨停+封板异动): 各项结论行→最重家族建议→逐项明细折叠",
         _anomaly_merged_preview(),
         "同tick触发≥2项自动合并 · 家族取最重(离场>风险>机会)")


# ═══════════════════════════════════════════
# 四、风险预警家族
# ═══════════════════════════════════════════

# 手工镜像 · market_risk_controller 修复中(0716 import SyntaxError, 同事在修) —
# 大盘退潮/空仓/谨慎/降级 4 张状态卡暂保留手工 JSON, 该文件修复后改直调
# emit_risk_dimension / _push_state_card 版式重新对齐。
register("风险预警", "大盘退潮·减仓", "强势股赚钱效应消失, 提醒减仓(手工镜像·待market_risk修复后直调)",
         _v2("📛 大盘风控·退潮提示", [
             card_kit.heading_md("退潮维度 1 项触发 · 当前大盘风控 🟡 谨慎"),
             _md("✅ 涨停家数5日均骤降至 **32** 家（昨 48）\n"
                 "✅ 昨日涨停股今日溢价均值 **-1.8%**（转负）"),
             card_kit.advice("整板在抽血，谨慎追高"),
         ], "orange", summary="大盘风控 退潮提示 当前谨慎", tags=[("谨慎", "orange")]),
         "盘中实时 · 涨停骤降/溢价转负触发 · 当日按维度集合去重")

register("风险预警", "市场风险·空仓(升级)",
         "跌破空仓线, 状态升到最高危档(手工镜像·待market_risk修复后直调)",
         _v2("🔴 市场风险 · 升到「空仓」档", [
             _md("**🟡 谨慎　→　🔴 空仓（最高危）**"),
             _md("盘面大跌：自选池158只，只 **18%** 在涨、平均 **-2.18%**"),
             _md("<font color='grey'>触发线：<22%在涨 且 平均跌超2% = 空仓</font>"),
             card_kit.advice("立即停开新仓、别抄底，今天先保命"),
         ], "red", summary="市场风险 谨慎→空仓 只18%在涨", tags=[("空仓", "red")]),
         "10:00-14:30每5分(缓冲带+冷静期防打脸)+14:40预升级+16:40复核")

register("风险预警", "市场风险·谨慎(升级)",
         "盘面转弱, 升到谨慎档(手工镜像·待market_risk修复后直调)",
         _v2("🟡 市场风险 · 升到「谨慎」档", [
             _md("**🟢 正常　→　🟡 谨慎（谨慎档）**"),
             _md("盘面转弱：自选池158只，只 **26%** 在涨、平均 **-1.20%**"),
             _md("<font color='grey'>触发线：<28%在涨 或 平均跌超1% = 谨慎</font>"),
             card_kit.advice("注意控制仓位、别追高"),
         ], "yellow", summary="市场风险 正常→谨慎 只26%在涨", tags=[("谨慎", "orange")]),
         "10:00-14:30每5分+16:40复核")

register("风险预警", "市场风险·降到谨慎(降级)",
         "明显转好过退出缓冲线才降档, 措辞诚实不叫回暖(手工镜像·待market_risk修复后直调)",
         _v2("🟡 市场风险 · 降到「谨慎」档", [
             _md("**🔴 空仓　→　🟡 谨慎（谨慎档）**"),
             _md("跌势明显缓和：自选池158只，**42%** 在涨、平均 **-0.6%**\n——是没那么急了，不是转多。"),
             card_kit.advice("空仓警报解除，可小仓试错、别重仓"),
         ], "yellow", summary="市场风险 空仓→谨慎 跌势缓和", tags=[("谨慎", "orange")]),
         "10:00-14:30每5分 · 涨跌比≥35%且均值≥-1.2% 且距上次变档≥30分钟才降")


# ── 解除卡: 直调 card_kit.dismiss_card(状态型预警闭环标准卡型) ──

register("风险预警", "预警解除(标准解除卡)",
         "状态型预警结束时的灰header解除卡(card_kit.dismiss_card): 写明解除条件+生效期间小结",
         _card_json(card_kit.dismiss_card(
             "市场风险·空仓预警", issued_str="07-10 09:35", days_active=4,
             condition_md="自选池涨跌比 **46%** ≥ 45% 且平均 **-0.2%** ≥ -0.3%（连续 2 个检查点，防贴线反复）",
             period_md="生效期间 上证 **-2.1%**，自选池平均 **-3.4%**，期间买点信号 0 开仓",
             advice_text="警报解除，可逐步恢复试仓",
         )),
         "状态型预警(空仓/情绪冰点/退潮)结束时收尾 · 防抖=超缓冲带或持续N周期 · 一次性事件卡不发解除")


# ── 聚合卡: 直调 card_kit.aggregate_card(风暴合并标准卡型) ──

register("风险预警", "集中触发·聚合卡(标准聚合卡)",
         "同族信号90秒内凑够≥3条合并一张(card_kit.aggregate_card): 归因行+全短列表+建议+折叠, 普跌日防轰炸",
         _card_json(card_kit.aggregate_card(
             "跌破MA10", rows=[
                 ("天华新能", card_kit.pct_md(-6.8), "跌破MA10"),
                 ("融捷股份", card_kit.pct_md(-9.2), "跌破MA10"),
                 ("海科新源", card_kit.pct_md(-5.4), "跌破MA10"),
             ],
             cause_md="大盘急跌 **-1.8%**，锂电板块共振下杀（板块内 5/7 只自选同跌）",
             advice_text="板块共振跌，禁抄底，等企稳再看",
             window="10:42~10:44", tag="板块共振",
             table_headers=["股票", "跌幅", "信号"],
             fold_summary="现价明细与合并口径",
             fold_detail=("天华新能 现价 ¥26.10\n融捷股份 现价 ¥33.85\n海科新源 现价 ¥18.42\n\n"
                          "合并口径: 同族信号 90 秒窗口内 ≥3 条合并一张, 各票原卡不再单发。"),
         )),
         "同族信号90秒窗口≥3条自动合并 · 归因共因进彩签 · 长值下沉折叠")


# ── 板块共振·禁补仓: 直调 sector_cocrash_guard.build_cocrash_card ──

def _cocrash_preview() -> dict:
    from backend.services.sector_cocrash_guard import build_cocrash_card
    sectors = {
        "有色金属-能源金属-锂": {"ratio": 1.0, "down": 6, "total": 6},
        "电力设备-电池化学品": {"ratio": 0.71, "down": 27, "total": 38},
    }
    hits = [
        {"code": "300390", "name": "天华新能", "pct": -15.8,
         "industry": "电力设备-电池化学品", "held": True},
        {"code": "002192", "name": "融捷股份", "pct": -10.0,
         "industry": "有色金属-能源金属-锂", "held": True},
        {"code": "301292", "name": "海科新源", "pct": -7.0,
         "industry": "电力设备-电池化学品", "held": False},
    ]
    return _card_json(build_cocrash_card(0.052, sectors, hits))


register("风险预警", "板块共振·禁补仓",
         "大盘正常但某行业集体大跌(超出大盘≥20pp)时, 自选池命中行业合并提示禁抄底/禁补仓; 回测背书",
         _cocrash_preview(),
         "交易日尾盘14:30 · 全市场+行业大跌占比判定 · 命中票再过个股破位闸 · 不落信号库")

# 手工镜像 · 恐慌底机会提示: 构卡内联在 cash_alert EOD 编排里, 按现行文案对齐。
register("风险预警", "恐慌底·机会提示",
         "昨停溢价5日均下穿0, 5年仅9次的机会窗(手工镜像·对齐现行EOD文案)",
         _card_v1("⚡ 恐慌底·机会提示", "orange",
                  "昨日涨停股溢价5日均下穿0(现 **-0.52%**) — 5年仅出现9次, "
                  "全部对应恐慌大底(2022-04-26 / 2024-01-31 / 2024-08-23 等).\n\n"
                  "该窗口内买点历史胜率 85.7%/单笔 +10.0%, 其中弱势极限(左侧)是主力收割模型.\n"
                  "提示: 这不是风险预警而是机会窗 — 恐慌加速末段, 弱势极限信号此时质量最高, 可按纪律执行."),
         "16:40 EOD评估触发 · 5年仅9次")


# ═══════════════════════════════════════════
# 五、黑天鹅预警(直调 blackswan_alerts._build_card, 两区域合并卡)
# ═══════════════════════════════════════════

def _blackswan_preview() -> dict:
    from backend.services.blackswan_alerts import _build_card
    ann_hits = [
        {"code": "002217", "name": "合力泰", "tags": "立案调查", "date": "2026-04-29",
         "title": "关于公司及实际控制人收到中国证监会立案告知书的公告",
         "url": "https://www.cninfo.com.cn/"},
        {"code": "300442", "name": "润泽科技", "tags": "交易所问询函", "date": "2026-05-10",
         "title": "关于对深圳证券交易所年报问询函的回复公告", "url": ""},
    ]
    ann_verdicts = {
        "002217": {"emoji": "🔴", "severity": "高",
                   "text": "实控人遭证监会立案，信披违法实锤，退市与索赔风险，持仓应规避"},
        "300442": {"emoji": "🟡", "severity": "中",
                   "text": "常规年报审核问询及中介回复，程序性环节非造假质疑，影响有限"},
    }
    fin_hits = [
        {"code": "600110", "name": "诺德股份", "score": 85, "year": 2025,
         "flags": [{"label": "连续亏损", "brief": ""}, {"label": "累计亏损", "brief": "-1.1亿"},
                   {"label": "存贷双高", "brief": ""}]},
        {"code": "600172", "name": "黄河旋风", "score": 78, "year": 2025,
         "flags": [{"label": "连续亏损", "brief": ""}, {"label": "累计亏损", "brief": "-27.2亿"},
                   {"label": "高杠杆", "brief": "91%"}]},
        {"code": "300059", "name": "东方财富", "score": 62, "year": 2025,
         "flags": [{"label": "利润现金流背离", "brief": ""}, {"label": "高杠杆", "brief": "77%"}]},
        {"code": "300657", "name": "弘信电子", "score": 45, "year": 2025,
         "flags": [{"label": "累计亏损", "brief": "-3.8亿"}, {"label": "高杠杆", "brief": "80%"}]},
    ]
    return _card_json(_build_card(ann_hits, fin_hits, ann_verdicts))


register("黑天鹅预警", "自选股黑天鹅预警",
         "风险公告(监管硬信号·每只挂AI逐股研判)+财务红旗(年报指标)合并一张两区域卡, 18:30一次",
         _blackswan_preview(),
         "交易日18:30定时执行 · 两区域常驻(某类无新增标「本次无新增」) · ≥1区域有新增才发")


# ═══════════════════════════════════════════
# 六、盘面分析 / 情报家族(蓝卡)
# ═══════════════════════════════════════════

# ── 竞价播报·开盘共性: 直调 auction_summary_analyst._build_auction_card ──

def _auction_summary_preview() -> dict:
    from backend.services.auction_summary_analyst import _build_auction_card
    d = {"headline": "抢筹积极高开为主", "vibe": "高开家数明显多于低开，抢筹氛围",
         "style": "小盘题材更强，权重平稳", "kill": "无明显杀跌方向",
         "action": "开盘顺势别追高，等回踩确认",
         "mainlines": [{"direction": "AI算力", "reps": "中贝通信、鸿博股份"},
                       {"direction": "机器人", "reps": "三花智控、绿的谐波"}]}
    indices = [{"name": "上证指数", "pct_change": 0.35},
               {"name": "深证成指", "pct_change": 0.52},
               {"name": "创业板指", "pct_change": 0.71},
               {"name": "科创50", "pct_change": 0.44}]
    text, elements, meta = _build_auction_card(d, 8, 21, indices, 1.2)
    card = card_kit.Card(
        title="📊 盘面播报", elements=elements, fallback=text, family="intel",
        summary=card_kit.summary_text("盘面播报", meta.get("headline"),
                                      f"涨停预排{meta.get('near_lu', 0)}只"))
    return _card_json(card)


register("盘面分析", "竞价播报·开盘共性",
         "9:26集合竞价AI开盘共性: heading定调+KPI三栏(上证竞价/涨停预排/高开≥5%)+题材主线+方法论折叠",
         _auction_summary_preview(),
         "交易日9:26 · 与竞价板块强弱合并成「📊 竞价播报」一张卡推送")


# ── 竞价分析·板块强弱: 构卡内联在 async 网络编排里 —— 手工镜像,
#    用其真实纯件(_board_row/_relay_advice) + card_kit 按 build_auction_sector_part 版式逐行对齐 ──

def _auction_sector_preview() -> dict:
    from backend.services.auction_sector_strength import _board_row, _relay_advice
    my_codes = {"002371", "002156"}
    top5 = [
        {"name": "半导体", "pct": 2.3, "leader_name": "北方华创", "leader_code": "002371"},
        {"name": "AI算力", "pct": 1.8, "leader_name": "浪潮信息", "leader_code": "000977"},
        {"name": "机器人", "pct": 1.5, "leader_name": "绿的谐波", "leader_code": "688017"},
        {"name": "低空经济", "pct": 1.2, "leader_name": "宗申动力", "leader_code": "001696"},
        {"name": "军工", "pct": 0.9, "leader_name": "中航沈飞", "leader_code": "600760"},
    ]
    n_hy, n_gn, up_n, down_n = 31, 86, 19, 11
    n_ok, n_weak = 1, 1
    # 结论区: KPI 三栏(上证竞价 / 最强板块 / 昨日热点承接) — 与 service 同序
    elements: list = [card_kit.kpi_row([
        ("上证竞价", "+0.35%", "red"),
        ("最强 半导体", "+2.3%", "red"),
        ("昨日热点承接", f"{n_ok}承/{n_weak}弱", None),
    ])]
    elements.append(_md(f"{n_hy}个一级行业竞价　" + card_kit.long_short_bar(down_n, up_n)))
    elements.append(_md(f"🔥 **竞价最强 TOP5**（{n_hy}行业 + {n_gn}概念）"))
    elements.append(card_kit.short_table(
        ["板块", "涨幅", "领涨"], [_board_row(b, my_codes) for b in top5]))
    elements.append(_md("💼 **持仓关联板块**"))
    elements.append(card_kit.short_table(
        ["持仓", "涨幅", "板块位"],
        [("**通富微电**", card_kit.pct_md(2.1), "行业3/31"),
         ("**江海股份**", card_kit.pct_md(1.4), "被动元件")]))
    elements.append(_md("🔁 **昨日热点 · 今晨承接**"))
    elements.append(card_kit.short_table(
        ["题材", "承接", "溢价"],
        [("⭐半导体", "<font color='red'>✅强承接</font>", card_kit.pct_md(2.3, bold=False)),
         ("光伏", "<font color='grey'>○一般</font>", card_kit.pct_md(0.5, bold=False)),
         ("券商", "<font color='green'>⚠️转弱</font>", card_kit.pct_md(-0.3, bold=False))]))
    elements.append(card_kit.advice(_relay_advice(n_ok, n_weak)))
    elements.append(card_kit.fold(
        "行业最弱与口径说明",
        "❄️ **行业最弱**\n-1.2%　煤炭\n-0.9%　银行\n-0.7%　石油石化\n\n"
        "🕤 四指数竞价　上证指数 +0.35%　深证成指 +0.52%　创业板指 +0.71%　科创50 +0.44%\n\n"
        "📐 口径：✅强承接 ○一般 ⚠️转弱；溢价=昨日该题材涨停股今晨竞价涨幅均值；"
        "⭐=自选池有票；领涨标红=命中自选；数据源 腾讯板块榜 · 用时 1.2s"))
    card = card_kit.Card(
        title="📊 竞价分析", elements=elements,
        fallback="【竞价分析】竞价最强 半导体+2.3%…(预览样例)", family="intel",
        subtitle="竞价板块强弱 · 腾讯板块榜",
        summary=card_kit.summary_text("竞价分析", "最强半导体+2.3%", f"承接{n_ok}/转弱{n_weak}"))
    return _card_json(card)


register("盘面分析", "竞价分析·板块强弱",
         "9:26竞价板块强弱硬数据卡(手工镜像·用真实纯件_board_row/_relay_advice按service版式对齐): "
         "KPI三栏+多空条+最强TOP5+持仓关联+昨日承接",
         _auction_sector_preview(),
         "交易日9:26定时推送 · 与开盘共性合并一张卡")


# ── 资金进攻方向: 直调 attack_direction_analyst._build_card ──

def _attack_preview() -> dict:
    from backend.services.attack_direction_analyst import _build_card
    hot = [
        {"theme": "机器人", "state": "升温", "limit_up": 6, "max_height": 3,
         "samples": ["三花智控", "绿的谐波", "兆威机电", "鸣志电器"]},
        {"theme": "固态电池", "state": "启动", "limit_up": 4, "max_height": 2,
         "samples": ["三祥新材", "上海洗霸"]},
        {"theme": "半导体", "state": "启动", "limit_up": 3, "max_height": 2,
         "samples": ["长电科技"]},
    ]
    lead = [{"industry": "半导体", "pct_today": 3.2},
            {"industry": "汽车零部件", "pct_today": 2.1},
            {"industry": "电力设备", "pct_today": 1.4}]
    watch_hits = [
        {"name": "三花智控", "code": "002050", "hold": True, "where": "机器人", "strong": True},
        {"name": "通富微电", "code": "002156", "hold": False, "where": "半导体", "strong": False},
    ]
    elements = _build_card(hot, lead, watch_hits, "启动 · 封板率72% · 涨停45家 · 最高4板", False)
    card = card_kit.Card(
        title="📊 资金进攻方向", elements=elements,
        fallback="【今日资金进攻方向】主线清晰: 机器人…(预览样例)", family="intel",
        subtitle="开盘一刻钟 · 涨停扎堆 + 领涨行业双口径",
        summary=card_kit.summary_text("进攻方向", "机器人", "涨停6家"))
    return _card_json(card)


register("盘面分析", "资金进攻方向·09:45",
         "开盘15分钟, 涨停扎堆题材(涨停池)+领涨行业(板块榜)双口径, 共振标🔥双确认, 叠自选/持仓命中",
         _attack_preview(),
         "交易日09:45定时执行 · 无明显主线时顶部改「资金分散·观望为主」照推")


# ── 晚盘复盘总结: 直调 review_summary._build_review_card(持仓+信号胜率+近期披露三段) ──

def _review_preview() -> dict:
    from backend.services.review_summary import _build_review_card
    cmp = {"buy": {"evaluated": 20, "success": 11, "success_rate": 55.0,
                   "pending": 3, "avg_p5": 1.23},
           "sell": {"evaluated": 10, "success": 6, "success_rate": 60.0,
                    "pending": 1, "avg_p5": -0.5}}
    top = [{"signal_name": "缩量后放量突破（右侧）", "success_rate": 71.0, "success": 5, "evaluated": 7},
           {"signal_name": "强势起点", "success_rate": 67.0, "success": 6, "evaluated": 9}]
    weak = [{"signal_name": "弱势极限", "success_rate": 40.0, "success": 2, "evaluated": 5}]
    hold_perf = [
        {"code": "002463", "name": "沪电股份", "price": 128.68, "pct": -5.88, "floating": -3.20},
        {"code": "300476", "name": "胜宏科技", "price": 96.50, "pct": -2.10, "floating": 8.40},
        {"code": "600519", "name": "贵州茅台", "price": 1245.0, "pct": 1.11, "floating": 2.30},
    ]
    disc_rows = [
        {"code": "002463", "name": "沪电股份", "appoint_date": "2026-07-20",
         "report_type": "2", "report_year": "2026"},
        {"code": "300750", "name": "宁德时代", "appoint_date": "2026-07-22",
         "report_type": "2", "report_year": "2026"},
    ]
    disc_hold = {"002463"}
    text, elements, meta = _build_review_card(
        3, 2, 1, 0, cmp, top, weak,
        hold_perf=hold_perf, disc_rows=disc_rows, disc_hold=disc_hold)
    card = card_kit.Card(
        title="📊 晚盘复盘总结", elements=elements, fallback=text, family="intel",
        subtitle="持仓表现 · 信号胜率 · 近期披露",
        summary=card_kit.summary_text("晚盘复盘", "持仓3只", "买3卖2", "披露2只"))
    return _card_json(card)


register("盘面分析", "晚盘复盘总结",
         "晚7点一张收盘总结: KPI三栏+💼持仓今日表现(逐票涨跌/浮盈)+买卖点胜率强度条+最好/警惕榜+📅近期财报披露+口径折叠",
         _review_preview(),
         "交易日 19:00 定时推送 · 排在 outcome 回填之后 · 披露内容并入(原08:40独立卡下线)")


# ── 涨停复盘: 直调 limit_up_archive.build_review_card ──

def _limit_up_preview() -> dict:
    from backend.services.limit_up_archive import build_review_card
    meta = {"limit_up_count": 45, "limit_up_history": 58, "limit_down_count": 3,
            "broken_board_count": 13, "seal_rate": 0.78}
    boards = [
        {"name": "天安新材", "code": "603725", "height": 4, "streak_label": "4连板",
         "reason": "机器人+人形机器人"},
        {"name": "胜宏科技", "code": "300476", "height": 3, "streak_label": "3连板",
         "reason": "PCB+算力"},
        {"name": "三祥新材", "code": "603663", "height": 2, "streak_label": "2连板",
         "reason": "固态电池"},
        {"name": "绿的谐波", "code": "688017", "height": 2, "streak_label": "2连板",
         "reason": "机器人+减速器"},
        {"name": "长电科技", "code": "600584", "height": 1, "streak_label": "首板",
         "reason": "半导体+封测"},
        {"name": "上海洗霸", "code": "603200", "height": 1, "streak_label": "首板",
         "reason": "固态电池"},
    ]
    return _card_json(build_review_card("20260716", meta, boards, link="#"))


register("盘面分析", "涨停复盘",
         "收盘后涨停池复盘: KPI三栏(涨停/封板率/跌停)+连板梯队短表+热点分布+一句话定性",
         _limit_up_preview(),
         "交易日15:35 · 存档当日涨停池 + 推复盘卡")


# ── 板块轮动三张: 直调 sector_rotation_scanner._build_*_card(alert_throttle 卡槽) ──

def _rotation_wts_preview() -> dict:
    from backend.services.sector_rotation_scanner import _build_weak_to_strong_card
    items = [
        {"theme": "PCB", "yest": 1, "limit_up": 4, "max_height": 4, "broken": 0,
         "samples": ["胜宏科技", "景旺电子", "生益科技"]},
        {"theme": "算力服务器", "yest": 2, "limit_up": 4, "max_height": 4, "broken": 0,
         "samples": ["工业富联", "沪电股份"]},
    ]
    title, elements = _build_weak_to_strong_card(items)
    return _v2(title, elements, "blue")


register("盘面分析", "板块弱转强·启动",
         "题材早盘冷→涨停家数快速抬升, 状态跃迁即推",
         _rotation_wts_preview(),
         "交易日每3分钟扫描 · 弱转强启动跃迁即推, 同题材当日仅一次")


def _rotation_wts_failed_preview() -> dict:
    from backend.services.sector_rotation_scanner import _build_wts_failed_card
    items = [{"theme": "机器人", "yest": 3, "peak": 7, "limit_up": 3,
              "samples": ["天安新材", "长盛轴承"]}]
    title, elements = _build_wts_failed_card(items)
    return _v2(title, elements, "blue")


register("盘面分析", "板块弱转强·失败",
         "早先推过启动的题材涨停回落, 补一条失败提醒(每题材当日一次)",
         _rotation_wts_failed_preview(),
         "早先弱转强启动后回落到冷/退潮才推 · 同题材当日仅一次")


def _rotation_stw_preview() -> dict:
    from backend.services.sector_rotation_scanner import _build_strong_to_weak_card
    items = [
        {"theme": "光通信", "yest": 5, "limit_up": 2, "broken": 4,
         "samples": ["中际旭创", "新易盛"], "holds": "中际旭创"},
        {"theme": "固态电池", "yest": 4, "limit_up": 1, "broken": 3,
         "samples": ["三祥新材", "上海洗霸"]},
    ]
    title, elements = _build_strong_to_weak_card(items)
    return _v2(title, elements, "blue")


register("盘面分析", "板块强转弱·退潮",
         "热门题材涨停回落/封板大面积松动(炸板), 状态跃迁即推, 持仓踩线单独列出",
         _rotation_stw_preview(),
         "交易日每3分钟扫描 · 强转弱退潮跃迁即推, 同题材当日仅一次")


# 注: 「次日板块预测 / 真假强势评分 / 尾盘决策合并卡」预览已下线 —
#   14:40 尾盘决策整卡下线(用户拍板精简盘后推送), 三者均不再作为独立推送, 故移除预览。

# 手工镜像 · 资金回流·板块预警: 构卡内联在 capital_inflow_scanner 编排里, 按现行版式对齐。
register("盘面分析", "资金回流·板块预警",
         "板块涨≥1%+龙头涨停+全市场前5板块均涨≥5%(手工镜像·对齐capital_inflow现行版式)",
         _v2("📊 资金回流·板块预警", [
             _md("**半导体**　板块 +3.2%\n龙头 **北方华创** +9.98%(涨停)　前5股均涨 +4.5%"),
             _md("📈 板块前5股"),
             _md("\n".join([
                 "<font color='red'>+9.98%</font>　**北方华创** 002371 · 成交12.3亿·额第1",
                 "<font color='red'>+5.20%</font>　**中微公司** 688012 · 成交6.8亿·额第5",
                 "<font color='red'>+2.10%</font>　⭐**通富微电** 002156 · 成交3.1亿·额第12"])),
             _md("⭐ 你自选的该板块个股（1只）"),
             _md("<font color='red'>+2.10%</font>　⭐**通富微电** 002156 · 成交3.1亿·额第12"),
         ], "blue", summary="资金回流 半导体+3.2% 龙头北方华创涨停"),
         "盘中30秒扫描 · 同板块同日仅一次")


# ── 盘前今日关注: 直调 morning_focus.build_morning_focus_card ──

def _morning_focus_preview() -> dict:
    from backend.services.morning_focus import build_morning_focus_card
    buy_rows = [
        {"name": "多氟多", "code": "002407", "model": "回踩10MA缩量后突破昨高", "pct": 2.09},
        {"name": "中科曙光", "code": "603019", "model": "缩量后放量突破（右侧）", "pct": 4.12},
        {"name": "江海股份", "code": "002484", "model": "回踩20MA缩量后突破昨高", "pct": -0.85},
        {"name": "领益智造", "code": "002600", "model": "强势起点", "pct": 1.66},
        {"name": "胜宏科技", "code": "300476", "model": "中继平台突破", "pct": 3.86},
        {"name": "恒瑞医药", "code": "600276", "model": "弱势极限", "pct": None},
    ]
    disclosure_rows = [
        {"code": "300750", "name": "宁德时代", "report_type": "2",
         "appoint_date": "2026-07-17", "report_year": 2026},
        {"code": "002594", "name": "比亚迪", "report_type": "2",
         "appoint_date": "2026-07-17", "report_year": 2026},
    ]
    return _card_json(build_morning_focus_card(
        holding_n=5, total_signals=9, buy_rows=buy_rows,
        disclosure_rows=disclosure_rows, hold_codes={"300750", "002407"},
        risk_state="YELLOW", risk_since="07-14", stop_pressure_n=1, ma_alert_n=3))


register("盘面分析", "盘前今日关注",
         "08:50盘前速览蓝卡: KPI三栏(持仓/昨日信号/今日披露)+昨日买点追踪表(>5只Top5+全量折叠)"
         "+今日披露一行+当前状态(风险档/止损压力/到线订阅)+👉一句话",
         _morning_focus_preview(),
         "交易日08:50 · 只取系统内现成数据 · 全空(无持仓无信号无披露)不发 · 每日一次DB去重")

# 手工镜像 · 错过消息回顾: 构卡内联在 push_backfill 编排里, 按现行版式对齐。
register("系统通知", "错过消息回顾",
         "推送开关关闭期间错过的关键信号(手工镜像·对齐push_backfill现行版式), 重新打开时汇总补发一卡",
         _v2("📮 错过消息回顾", [
             _md("**📮 错过消息回顾**　_推送开关关闭期间错过的关键信号, 现汇总补发_"),
             _md("**⚠️ 当前市场风险档: ⚡ YELLOW 谨慎**"),
             _md("\n".join([
                 "**🟢买入**　07-16 10:30 **国际复材** 301526 缩量突破",
                 "**🟡减仓**　07-16 13:05 **中际旭创** 300308 接近前高",
                 "**🔴卖出**　07-16 14:12 **兆易创新** 603986 弱势止损"])),
         ], "blue", summary="错过消息回顾 3条关键信号补发"),
         "推送开关关→开时触发 · 只补关键信号 · 最近24h/最多30条")


# ═══════════════════════════════════════════
# 七、系统通知 / 系统家族(灰卡)
# ═══════════════════════════════════════════

def _audit_preview() -> dict:
    from backend.services.signal_eod_audit import build_audit_card
    suspects = [
        ({"name": "多氟多", "code": "002407", "signal_name": "回踩10MA缩量后突破昨高",
          "triggered_at": "2026-07-16 10:15:00"}, "触发价与新浪快照昨收不符: 触发时昨收=30.9"),
        ({"name": "宁德时代", "code": "300750", "signal_name": "弱势极限",
          "triggered_at": "2026-07-16 13:20:00"}, "触发时量比1.8超合理上限, 量未真缩"),
    ]
    return _card_json(build_audit_card(suspects, "2026-07-16", n_ok=9, n_unverified=1))


register("系统通知", "信号EOD复核·存疑",
         "收盘后自动复核当日全部信号: 灯串+存疑短表(股票|信号|结论)+疑点明细折叠, 只标记未删",
         _audit_preview(),
         "交易日17:00定时执行")


def _blogger_preview() -> dict:
    from backend.services.blogger_post_scanner import build_post_card
    post = {
        "blogger_name": "全能的野人", "posted_at": datetime.now() - timedelta(minutes=6),
        "content": ("今天情绪明显退潮, 涨停家数不到40家, 各位注意风控。\n"
                    "我自己的仓位已经从8成降到3成, 等情绪冰点过去再说。"),
        "stock_codes": [], "url": "https://t.10jqka.com.cn/",
        "like_num": 156, "comment_num": 43, "images": [],
    }
    return _card_json(build_post_card(post))


register("系统通知", "博主发帖跟踪",
         "同花顺博主新帖: 结论行=博主名+要点一句, 互动/个股数据行, 帖子全文进折叠",
         _blogger_preview(),
         "交易日9-10点5分/10-15点20分/盘后60分/非交易日20:00")


def _custom_alert_preview() -> dict:
    from backend.services.custom_alert_scanner import build_alert_card
    items = [
        {"code": "002929", "name": "润建股份", "price": 58.10, "pct_change": 2.2,
         "note": "", "preset": "ma20", "ma_value": 58.05, "repeat_daily": True,
         "conditions": []},
        {"code": "002407", "name": "多氟多", "price": 31.50, "pct_change": -1.8,
         "note": "回踩接回", "preset": "", "ma_value": None, "repeat_daily": False,
         "conditions": [{"dim": "price", "op": "lte", "value": 32},
                        {"dim": "ma_near", "ma": 10, "band": 2}]},
    ]
    title, fallback, elements = build_alert_card(items)
    # 发送走 send_dual_card_to(多用户 webhook), 模板默认 blue, 无信封字段 — 与真实一致
    return _v2(title, elements, "blue")


register("系统通知", "自定义预警·均线提醒",
         "个股自定义预警触发合并卡: 均线快捷提醒(碰线±0.5%·每天一次)大白话+普通自定义条件摘要, 明细折叠",
         _custom_alert_preview(),
         "交易时段随池扫描节奏 · 均线快捷提醒=弹窗一键开关 · 普通自定义一次性触发")


def _data_health_preview() -> dict:
    from backend.services.data_health import build_health_card
    events = [
        {"kind": "index_trends_frozen",
         "seg": "10:31~10:39 大盘分时一度冻结 8 分钟, 现在已恢复正常", "recovered": True},
        {"kind": "market_stats_empty",
         "seg": "13:02 涨跌家数返回空, 目前可能还没恢复(系统会继续自动跳过)", "recovered": False},
    ]
    return _card_json(build_health_card(events))


register("系统通知", "数据源健康预警",
         "行情数据源波动即时预警: 灯串(大盘分时/涨跌家数/个股日K)+异常项+影响说明+要不要处理折叠",
         _data_health_preview(),
         "行情自检每轮检查 · 波动期间系统自动跳过异常数据不误报")


def _system_digest_preview() -> dict:
    from backend.services.system_health import build_digest_card
    items = [
        ("博主发帖", "「全能的野人」连续3次拉取失败: cookie过期(需重抓 get_by_uid)", "10:05"),
        ("数据源交叉校验", "上证收盘价与备用源偏差 0.6% 超阈值", "15:12"),
        ("博主发帖", "「全能的野人」拉取已恢复", "15:40"),
    ]
    return _card_json(build_digest_card(items))


register("系统通知", "系统健康·盘后汇总",
         "各类系统故障当日合并、盘后一次汇总(不实时刷屏): 灯串+异常清单+汇总口径折叠",
         _system_digest_preview(),
         "交易日盘后 · 当日无故障不推 · 紧急的行情源健康预警仍即时推")


# ── 系统体检报告(v1.7.698): 直调真实构造器 build_report_card, 保证预览与推送 1:1 ──

def _health_report_preview() -> dict:
    from datetime import timedelta

    from backend.services.health_checks import CRITICAL, WARN, CheckResult, build_report_card
    rs = [
        CheckResult("task_never_ran", "任务从未跑过", "任务", CRITICAL, False,
                    "1个: holding_state_fwd_refresh", "0个"),
        CheckResult("data_industry_map", "行业映射", "数据", WARN, False,
                    "2026-07-11", "≥2026-07-15"),
        CheckResult("data_kline_cache", "全市场日线", "数据", CRITICAL, True,
                    "2026-07-17", "≥2026-07-17"),
        CheckResult("data_index_5m", "指数5分钟K线(不可回补)", "数据", CRITICAL, True,
                    "2026-07-17", "≥2026-07-17"),
        CheckResult("api_sina_snapshot", "新浪全市场快照", "接口", CRITICAL, True,
                    "4990 只", "≥3000 只"),
        CheckResult("api_outbound_ip", "出口IP(推送总闸)", "接口", CRITICAL, True,
                    "124.71.75.5", "在生产白名单内"),
        CheckResult("rule_eod_audit", "EOD复核有效性", "规则", WARN, True,
                    "unverified 0/13(0%)", "<30%"),
    ]
    hb = {"last_push_at": datetime.now() - timedelta(hours=24), "fail_streak": 0}
    card, _ = build_report_card(rs, hb)
    return _card_json(card)


register("系统通知", "系统体检·每日报告",
         "每日盘前跑21项断言式检查(任务健康/数据新鲜度/外部接口/业务规则), 有无异常都推: "
         "灯串+异常项(实际vs期望)+建议+明细折叠(含执行项数与推送心跳自检)",
         _health_report_preview(),
         "每日 08:10 · 无异常也推(便于区分「一切正常」与「告警系统自己哑了」)")


# ── 推送健康度周报: 直调 push_health_report.build_health_card(stats 走真实 summarize_actions) ──

def _push_health_preview() -> dict:
    from backend.services.push_health_report import build_health_card, summarize_actions
    rows = [
        {"kind": "model_off", "target": "BUY_WEAK_EXTREME"},
        {"kind": "model_off", "target": "BUY_WEAK_EXTREME"},
        {"kind": "model_off", "target": "BUY_PLATFORM_BREAKOUT"},
        {"kind": "snooze_until_retrigger", "target": "002407|BUY_MA10_PULLBACK"},
        {"kind": "surge_snooze", "target": "002648"},
        {"kind": "mark_sold", "target": "603986"},
        {"kind": "ack", "target": ""},
        {"kind": "ma_alert_20", "target": "002050"},
        {"kind": "ma_alert_10", "target": "300476"},
    ]
    name_map = {"BUY_WEAK_EXTREME": "弱势极限", "BUY_PLATFORM_BREAKOUT": "中继平台突破"}
    return _card_json(build_health_card(
        stats=summarize_actions(rows), name_map=name_map, active_ma_alerts=3,
        start_date="2026-07-13", trading_days_n=5))


register("系统通知", "推送健康度周报",
         "周五盘后灰卡: 本周(近5交易日)推送偏好动作统计, KPI三栏(动作次数/最常关模型/到线订阅)"
         "+动作分布短表+被关模型一行+👉建议(集中被关点名去模型图鉴)+口径折叠",
         _push_health_preview(),
         "周五17:10 · 只统计用户降噪动作(无推送量日志表) · 无动作也发注明口径 · 当日一次去重")


# ═══════════════════════════════════════════
# 八、持仓研判晚报(直调 holding_brief.build_brief_card)
# ═══════════════════════════════════════════

def _brief_preview() -> dict:
    from backend.services.holding_brief import build_brief_card
    payloads = [
        {"name": "京东方A", "code": "000725", "price": 7.18, "state": "多头站均线",
         "profit_pct": 12.0, "hold_days": 5},
        {"name": "沪电股份", "code": "002463", "price": 151.00, "state": "高位放量滞涨",
         "profit_pct": 24.0, "hold_days": 11},
        {"name": "三花智控", "code": "002050", "price": 21.80, "state": "缩量回踩",
         "profit_pct": -2.1, "hold_days": 3},
    ]
    verdicts = {
        "000725": {"action": "持有", "target": 7.60, "stop": 6.70,
                   "reason": "量能延续板块第二强，同类形态次日↑58%"},
        "002463": {"action": "减仓", "target": 150, "stop": 140,
                   "reason": "高位放量滞涨，同类形态次日↑47%偏弱"},
        "002050": {"action": "持有", "target": 23.50, "stop": 20.80,
                   "reason": "缩量回踩MA10企稳，机器人主线仍在"},
    }
    return _card_json(build_brief_card(payloads, verdicts, "风险偏低，可正常持仓"))


register("持仓研判晚报", "持仓研判晚报",
         "交易日前夜20:00逐股数据体检+AI次日方向性建议(持/减/清/加+目标价/止损价), "
         "客观概率源自全市场五年同类形态前向分布",
         _brief_preview(),
         "交易日前夜20:00 · 内部判「明天是交易日」才发 · 空仓只发一行「今日空仓」")


# ═══════════════════════════════════════════
# 九、盘后提醒(预增榜)
# ═══════════════════════════════════════════
# 注: 财报披露日历原 08:40 独立卡已下线, 披露内容并入「盘面分析/晚盘复盘总结」的近期披露段。


def _forecast_preview() -> dict:
    from backend.services.earnings_forecast_scan import build_forecast_card
    mine = [{"code": "600744", "name": "华银电力", "predict_type": "预增",
             "amp_lower": 3601, "amp_upper": 4423, "notice_date": "2026-07-05"}]
    others = [
        {"code": "603889", "name": "南方精工", "predict_type": "扭亏",
         "amp_lower": 28647, "amp_upper": 35784, "notice_date": "2026-07-05"},
        {"code": "003037", "name": "三和管桩", "predict_type": "预增",
         "amp_lower": 3091, "amp_upper": 3889, "notice_date": "2026-07-04"},
        {"code": "001210", "name": "金房能源", "predict_type": "预增",
         "amp_lower": 500, "amp_upper": 800, "notice_date": "2026-07-05"},
    ]
    good = mine + others
    return _card_json(build_forecast_card(good, mine, others, hold_codes={"600744"}))


register("盘后提醒", "预增榜·当日正向业绩预告",
         "盘后把当日新出的正向业绩预告捞出来(自选置顶+全市场大幅预增), 机会族红卡, 克制使用",
         _forecast_preview(),
         "每日 18:30 · 当日有正向预告才发 · 克制使用(非埋伏神器)")


# ── 均线到线提醒: 直调 ma_touch_alert.build_touch_card(一次性订阅触发卡) ──

def _ma_touch_preview() -> dict:
    from backend.services.ma_touch_alert import build_touch_card
    # site 传 "#" 生成占位分时图链接(真实推送取 config site_url), 版式 1:1
    return _card_json(build_touch_card("三花智控", "002050", 20, 21.86, 21.83, site="#"))


register("盘后提醒", "均线到线提醒",
         "推送卡底部订阅的一次性到线提醒(情报蓝卡): 现价进入均线±0.3%贴线带触发, "
         "KPI三栏(现价/均线值/距离)+👉+口径折叠, 发送后订阅自动失效",
         _ma_touch_preview(),
         "交易时段每60秒扫生效订阅 · 防误触(先离带再回触才算) · 一次性发完即失效 · 60天有效期")


# ── API ──

@router.get("")
async def list_templates(user: Annotated[dict, Depends(get_current_user)]):
    """返回所有飞书推送模版列表(含示例卡片JSON + 触发时机)."""
    import os
    mtime = os.path.getmtime(__file__)
    updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return {"templates": TEMPLATES, "updated_at": updated}


@router.get("/{template_id:path}")
async def get_template(template_id: str, user: Annotated[dict, Depends(get_current_user)]):
    """返回单个模版详情."""
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    return {"error": "not found"}
