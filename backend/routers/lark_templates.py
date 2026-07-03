# -*- coding: utf-8 -*-
"""飞书推送模版预览 API — 返回各推送场景的示例卡片 JSON + 元数据.

前端用 LarkCardPreview 组件渲染, 模拟飞书真实效果.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.routers.signals import get_current_user

router = APIRouter(prefix="/api/admin/lark-templates", tags=["lark-templates"])


def _card_v2(title: str, template: str, elements: list[dict],
              link_url: str = "", link_text: str = "") -> dict:
    """构建飞书 2.0 卡片 (schema: '2.0'). 时间已在标题栏右侧, body不再重复."""
    body_elements = list(elements)
    if link_url:
        body_elements.append({
            "tag": "markdown",
            "content": f"[{link_text or link_url}]({link_url})",
        })
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "width_mode": "fill"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "body": {"elements": body_elements},
    }


def _card_v1(title: str, template: str, md_body: str,
              link_url: str = "", link_text: str = "") -> dict:
    """构建飞书 1.0 卡片. 时间已在标题栏右侧, body不再重复."""
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": md_body}},
    ]
    if link_url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": link_text or "查看详情"},
                "type": "primary",
                "url": link_url,
            }],
        })
    return {
        "config": {"wide_screen_mode": True, "width_mode": "fill"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": elements,
    }


def _time_element() -> dict:
    from datetime import datetime
    now = datetime.now()
    return {
        "tag": "markdown",
        "content": f"<font color='grey'>{now.month}-{now.day} {now.hour:02d}:{now.minute:02d}</font>",
        "text_align": "right",
    }


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _collapsible(summary: str, detail: str, expanded: bool = False) -> dict:
    """折叠面板预览(与 lark_notifier.collapsible_element 一致): header 常显, 点开看 detail。"""
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": {"tag": "markdown", "content": summary},
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                     "color": "grey", "size": "12px 12px"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "elements": [{"tag": "markdown", "content": detail}],
    }


def _table(columns: list[dict], rows: list[dict], page_size: int = 10) -> dict:
    return {
        "tag": "table",
        "page_size": page_size,
        "row_height": "low",
        "header_style": {"text_align": "center", "text_size": "normal"},
        "columns": columns,
        "rows": rows,
    }


# ── 模版定义 ──

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
# 1. 买入信号 (v2 卡片 + 表格)
# ═══════════════════════════════════════════
register("买入信号", "回踩10MA缩量后突破昨高", "检测到回踩MA10缩量后放量突破昨高, 触发买入",
         _card_v2("🟢 买入信号 · 多氟多", "red", [
             _md("**多氟多**　`002407`\n现价 **33.18** ▲**+2.09%**"),
             _md("💰 成交额 **3.5亿**　🎯 近3月胜率**61%**·第**2**名"),   # 关键数字前置
             _md("📊 所属行业：化学制品 ｜ 关联热点：PCB·覆铜板\n升温🔥 · 今日涨停 **6** 家 · 近3日 4→5→6 走强"),
             _collapsible("📌 **我的策略**　回踩10日线缩量企稳是经典买点…",
                          "回踩10日线缩量企稳是经典买点 · 突破昨高确认 · 大涨减仓降成本反复跟踪 · 跌破MA10离场"),
             _md("🎯 个股实情：缩量回踩MA10(昨量是均量的**0.62倍**) · 突破昨高**2.5%**(触发价31.73)"),
             _md("**📊 模型战绩**: 近3月胜率 61% · 单笔均+3.2% · 全模型第2名 · 盈利因子2.15"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "30%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "70%"}],
                 [{"item": "距MA10", "value": "+0.8%"},
                  {"item": "昨量/10日均量", "value": "0.62x"},
                  {"item": "突破幅度", "value": "+4.2%"},
                  {"item": "主升浪涨幅", "value": "+23.5%"},
                  {"item": "距峰日数", "value": "8天"}],
             ),
             _md("⚠️ **市场风险·空仓预警(RED)**: 回测期内信号胜率30%均值-3.6%, 强烈建议停开新仓"),
         ]), "盘中实时 · 满足条件立即推送")

# ═══════════════════════════════════════════
# 2. 买入信号 (普通, 无风险预警)
# ═══════════════════════════════════════════
register("买入信号", "缩量后放量突破(无预警)", "正常市场环境下的缩量突破买点, 带模型战绩排名",
         _card_v2("🟢 买入信号 · 中科曙光", "red", [
             _md("**缩量突破昨高** 昨缩量(近10日均量的0.55倍) → 今日放量(均量的2.1倍、昨量的3.4倍) "
                  "突破昨高2.0%(触发价48.50) ｜ 站上MA10 MA20"),
             _md("📊 所属行业：计算机设备 ｜ 关联热点：AI算力·液冷\n高潮🔥 · 今日涨停 **9** 家 · 近3日 6→8→9 走强"),
             _md("**战绩**: 近3月胜率 65% · 单笔均+3.8% · 全模型第1名 · 盈利因子2.24"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "40%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "60%"}],
                 [{"item": "昨量/10日均量", "value": "0.55x"},
                  {"item": "今日放量/均量", "value": "2.1x"},
                  {"item": "突破昨高幅度", "value": "+3.1%"},
                  {"item": "站上均线", "value": "MA10 + MA20"}],
             ),
         ]), "盘中实时 · 满足条件立即推送")

# ═══════════════════════════════════════════
# 3-7. 其余买入信号模版 (v1.7.407 补齐)
# ═══════════════════════════════════════════
register("买入信号", "回踩20MA缩量后突破昨高", "检测到回踩MA20缩量后放量突破昨高, 触发买入",
         _card_v2("🟢 买入信号 · 回踩MA20", "red", [
             _md("**回踩20MA缩量后突破昨高** 缩量后放量突破昨高\n"
                  "触发价 **--** · 现价 **--**\n"
                  "近30日主升≥15% · 回踩MA20±3% · 缩量<均量×0.8 · 突破昨高×1.025"),
             _md("**战绩**: 近3月胜率 54% · 单笔均+2.7% · 盈利因子1.98"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "40%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "60%"}],
                 [{"item": "距MA20", "value": "--"},
                  {"item": "昨量/10日均量", "value": "--"},
                  {"item": "突破昨高幅度", "value": "--"},
                  {"item": "主升浪涨幅", "value": "--"}],
             ),
         ]), "盘中实时 · 成交额破10亿即触发")

register("买入信号", "弱势极限", "缩量地量潜伏买点, 博回调结束反弹",
         _card_v2("🟢 买入信号 · 弱势极限", "red", [
             _md("**弱势极限(左侧)** 缩量地量潜伏\n"
                  "触发价 **--** · 现价 **--**\n"
                  "近30日主升≥15% · 贴MA10/20±2% · 地量≤最低×1.1且≤均量×0.7"),
             _md("**战绩**: 近3月胜率 14% · 单笔均-5.8% · 左侧策略 · 靠大赢家吃波段"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "40%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "60%"}],
                 [{"item": "今量/10日最低", "value": "--"},
                  {"item": "今量/10日均量", "value": "--"},
                  {"item": "距MA10/20", "value": "--"},
                  {"item": "主升浪涨幅", "value": "--"}],
             ),
             _md("⚠️ **左侧买点**: 买在下跌中, 持有到T+15, 需扛得住继续下探"),
         ]), "盘中实时 · 10:00起满足条件推送")

register("买入信号", "强势起点", "弱势极限缩量后放量启动确认, 触发买入",
         _card_v2("🟢 买入信号 · 强势起点", "red", [
             _md("**强势起点(右侧)** 缩量地量后放量启动\n"
                  "触发价 **--** · 现价 **--**\n"
                  "满足弱势极限前置 · 放量≥近期×2 · 涨幅≥2% · 站上MA10/20"),
             _md("**战绩**: 近3月胜率 67% · 单笔均+4.6% · 盈利因子3.10 · 全模型最强"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "40%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "60%"}],
                 [{"item": "今日涨幅", "value": "--"},
                  {"item": "放量倍数", "value": "--"},
                  {"item": "站上均线", "value": "--"},
                  {"item": "成交额", "value": "--"}],
             ),
         ]), "盘中实时 · 10:00起满足条件推送")

register("买入信号", "中继平台突破", "多日横盘后尾盘确认突破平台上沿, 触发买入",
         _card_v2("🟢 买入信号 · 中继平台突破", "red", [
             _md("**中继平台突破(右侧)** 尾盘收盘确认\n"
                  "触发价 **--** · 现价 **--**\n"
                  "前12日振幅≤15% · 缓升台阶 · 收盘≥上沿×1.005 · 放量≥均量×1.2"),
             _md("**战绩**: 近3月胜率 53% · 单笔均+1.0% · 盈利因子1.35 · 需退潮择时"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "40%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "60%"}],
                 [{"item": "平台振幅", "value": "--"},
                  {"item": "突破幅度", "value": "--"},
                  {"item": "放量/均量", "value": "--"},
                  {"item": "成交额", "value": "--"}],
             ),
             _md("⚠️ **尾盘确认**: 必须等14:40, 早盘\"突破\"不算数"),
         ]), "尾盘14:40起 · 收盘确认后推送")

register("买入信号", "竞价弱转强", "9:26竞价高开弱转强, 情绪+竞价额双门控",
         _card_v2("🟢 买入信号 · 竞价弱转强", "red", [
             _md("**竞价弱转强(右侧·研发)** 9:26竞价触发\n"
                  "触发价 **--** · 现价 **--**\n"
                  "昨缩量≤均×0.8 · 竞价高开3-9% · 竞价成交额≥1亿 · 大盘极端情绪"),
             _md("⚠️ **研发中**: 无历史回测, 信号很稀, 真实胜率待验"),
             _table(
                 [{"name": "item", "display_name": "指标", "data_type": "text", "width": "40%"},
                  {"name": "value", "display_name": "数值", "data_type": "text", "width": "60%"}],
                 [{"item": "竞价高开", "value": "--"},
                  {"item": "竞价成交额", "value": "--"},
                  {"item": "大盘情绪", "value": "--"},
                  {"item": "昨缩量/均量", "value": "--"}],
             ),
         ]), "9:26竞价撮合后立即推送")

# ═══════════════════════════════════════════
# 卖出信号
# ═══════════════════════════════════════════
register("卖出信号", "弱势极限止损卖出", "弱势极限持仓触发止损条件, 提醒卖出",
         _card_v2("🔴 卖出信号 · 三花智控", "green", [
             _md("**三花智控**　`002050`\n现价 **18.20** ▼**-3.20%**"),
             _md("📊 成本21.50 当前18.20 浮亏**-15.3%**"),   # 关键数字前置(浮盈/成本)
             _md("📊 所属行业：通用设备 ｜ 关联热点：固态电池·锂电\n退潮📉 · 今日涨停 **2** 家 · 近3日 7→4→2 走弱"),
             _collapsible("📌 **我的策略**　弱势极限建仓·跌破MA60剩半清仓…",
                          "弱势极限建仓(左侧出场) · 跌破MA60×0.97 剩半清仓 · 持仓12天 · 入场2026-05-20@22.80 最高@24.50(+7.5%)未触发止盈"),
             _md("🎯 个股实情：收盘价跌破MA60×0.97(剩半清仓) · 持仓12天"),
         ]), "盘中实时 · 止损触发立即推送")

# ═══════════════════════════════════════════
# 4. 减仓提示
# ═══════════════════════════════════════════
register("风险预警", "大盘退潮·减仓", "强势股赚钱效应消失, 提醒减仓",
         _card_v1("🌊 大盘退潮·减仓提示", "orange",
                  "涨停家数5日均骤降至 **32** 家, 昨日涨停股今日溢价均值 **-1.8%**\n\n"
                  "强势方向赚钱效应正在消失 — 这不是恐慌而是\"没得赚了\"。\n"
                  "建议: 减仓至半仓, 持仓加速止盈节奏, 不开新仓。"), "盘中实时 · 涨停骤降/溢价转负触发")

# ═══════════════════════════════════════════
# 5. 市场风险 RED
# ═══════════════════════════════════════════
register("风险预警", "市场风险·RED空仓", "两级预警最高级, 强烈建议空仓",
         _card_v1("[RED] 市场风险·空仓预警", "red",
                  "全市场5日均收益 **-1.82%**, 广度MA20 **18.5%**, 新低占比 **22.3%**.\n\n"
                  "回测: RED期内买入信号胜率 30.3%/均值 -3.56%, 强烈建议空仓或停开新仓.\n"
                  "解除条件: 广度>25% 且 涨跌比>40% 且 全市场5日均收益>0%."), "10:00-14:30每5分+14:40预升级+16:40复核")

# ═══════════════════════════════════════════
# 6. 市场风险 YELLOW
# ═══════════════════════════════════════════
register("风险预警", "市场风险·YELLOW谨慎", "轻度预警, 注意风控",
         _card_v1("[YELLOW] 市场风险·谨慎", "yellow",
                  "涨跌比 **28.5%** 广度MA20 **32.1%** 炸板率 **55.0%** — 触发轻度预警.\n\n"
                  "回测: YELLOW期信号质量未显著下降(胜率56%/均值+4.1%), 正常交易但注意风控."), "10:00-14:30每5分+14:40预升级+16:40复核")

# ═══════════════════════════════════════════
# 7. 竞价分析卡 (表格类)
# ═══════════════════════════════════════════
register("盘面分析", "竞价分析·板块强弱", "9:26集合竞价撮合后推板块强弱硬数据卡",
         _card_v2("📊 竞价分析 · 06-15", "blue", [
             _md("**行业最强**: 半导体(+2.3%) · 通信设备(+1.8%) · 军工(+1.5%)"),
             _md("**行业最弱**: 房地产(-0.8%) · 银行(-0.5%)"),
             _table(
                 [{"name": "name", "display_name": "概念板块", "data_type": "text", "width": "25%"},
                  {"name": "up_ratio", "display_name": "涨家比", "data_type": "text", "width": "15%"},
                  {"name": "top3", "display_name": "领涨前三", "data_type": "text", "width": "35%"},
                  {"name": "score", "display_name": "热度", "data_type": "text", "width": "25%"}],
                 [{"name": "AI算力", "up_ratio": "18/22", "top3": "浪潮信息 中科曙光 寒武纪", "score": "🔥🔥🔥"},
                  {"name": "机器人", "up_ratio": "15/20", "top3": "绿的谐波 埃斯顿 拓斯达", "score": "🔥🔥"},
                  {"name": "低空经济", "up_ratio": "12/18", "top3": "亿航智能 中信海直 万丰奥威", "score": "🔥"}],
             ),
             _table(
                 [{"name": "yest", "display_name": "昨日热点", "data_type": "text", "width": "25%"},
                  {"name": "today", "display_name": "今日承接", "data_type": "text", "width": "30%"},
                  {"name": "status", "display_name": "承接度", "data_type": "text", "width": "45%"}],
                 [{"yest": "半导体", "today": "+2.3% · 15/20涨", "status": "强承接 ✅"},
                  {"yest": "光伏", "today": "+0.5% · 8/15涨", "status": "弱承接 ⚠️"},
                  {"yest": "券商", "today": "-0.3% · 4/12涨", "status": "退潮 ❌"}],
             ),
         ]), "交易日9:26定时推送")

# ═══════════════════════════════════════════
# 8. 收盘复盘 (表格 + 信号汇总)
# ═══════════════════════════════════════════
register("盘面分析", "收盘复盘", "收盘后汇总当日信号命中 + 买点跟踪",
         _card_v2("📋 收盘复盘 · 06-13", "blue", [
             _md("**今日信号 5 条** (买3 · 卖1 · 风险1) ｜ 持仓跟踪 8 只"),
             _table(
                 [{"name": "time", "display_name": "时间", "data_type": "text", "width": "12%"},
                  {"name": "code", "display_name": "代码", "data_type": "text", "width": "12%"},
                  {"name": "name", "display_name": "名称", "data_type": "text", "width": "16%"},
                  {"name": "signal", "display_name": "信号", "data_type": "text", "width": "35%"},
                  {"name": "price", "display_name": "价格", "data_type": "text", "width": "12%"},
                  {"name": "pct", "display_name": "涨幅", "data_type": "text", "width": "13%"}],
                 [{"time": "09:45", "code": "002407", "name": "多氟多", "signal": "回踩10MA缩量后突破昨高", "price": "32.50", "pct": "+2.1%"},
                  {"time": "10:30", "code": "603019", "name": "中科曙光", "signal": "缩量后放量突破", "price": "48.20", "pct": "+1.8%"},
                  {"time": "14:10", "code": "300750", "name": "宁德时代", "signal": "弱势极限(地量)", "price": "185.00", "pct": "-0.5%"}],
             ),
             _table(
                 [{"name": "code", "display_name": "持仓", "data_type": "text", "width": "20%"},
                  {"name": "entry", "display_name": "入场日/价", "data_type": "text", "width": "25%"},
                  {"name": "now", "display_name": "现价/盈亏", "data_type": "text", "width": "25%"},
                  {"name": "plan", "display_name": "卖出计划", "data_type": "text", "width": "30%"}],
                 [{"code": "000977 浪潮", "entry": "06-10 @52.00", "now": "55.20 +6.2%", "plan": "止盈@57.20(+10%)"},
                  {"code": "002156 通富", "entry": "06-08 @28.50", "now": "27.30 -4.2%", "plan": "止损@25.65(-10%)"}],
             ),
             # 卖出信号按"主动止盈/被动止损/纪律清仓"三组展示(v1.7.538)
             _md("**🔴 卖出（3）**"),
             _md("🟢 主动止盈（1）"),
             _table([{"name": "name", "display_name": "名称", "data_type": "text", "width": "42%"},
                     {"name": "sig", "display_name": "信号", "data_type": "text", "width": "58%"}],
                    [{"name": "通富微电(002156)", "sig": "止盈减仓 +7%"}]),
             _md("🔴 被动止损（1）"),
             _table([{"name": "name", "display_name": "名称", "data_type": "text", "width": "42%"},
                     {"name": "sig", "display_name": "信号", "data_type": "text", "width": "58%"}],
                    [{"name": "宁德时代(300750)", "sig": "跌破MA20 / 浮亏止损 -10%"}]),
             _md("⏳ 纪律清仓（1）"),
             _table([{"name": "name", "display_name": "名称", "data_type": "text", "width": "42%"},
                     {"name": "sig", "display_name": "信号", "data_type": "text", "width": "58%"}],
                    [{"name": "浪潮信息(000977)", "sig": "弱势极限 持满T+15 清仓"}]),
         ]), "交易日15:05定时推送")

# ═══════════════════════════════════════════
# 9. 资金回流板块预警
# ═══════════════════════════════════════════
register("盘面分析", "资金回流·板块预警", "板块涨≥1%+龙头涨停+全市场前5板块均涨≥5%",
         _card_v2("📊 资金回流·板块预警", "blue", [
             _md("**板块涨幅 TOP 5** ｜ 全市场量能回升"),
             _table(
                 [{"name": "sector", "display_name": "板块", "data_type": "text", "width": "25%"},
                  {"name": "pct", "display_name": "涨幅", "data_type": "text", "width": "15%"},
                  {"name": "leader", "display_name": "龙头", "data_type": "text", "width": "25%"},
                  {"name": "self", "display_name": "自选个股", "data_type": "text", "width": "35%"}],
                 [{"sector": "半导体", "pct": "+3.2%", "leader": "北方华创(涨停)", "self": "通富微电+2.1%"},
                  {"sector": "AI算力", "pct": "+2.8%", "leader": "中科曙光(涨停)", "self": "浪潮信息+3.5%"},
                  {"sector": "机器人", "pct": "+2.1%", "leader": "绿的谐波(涨停)", "self": "—"}],
             ),
         ]), "盘中30秒扫描 · 同板块同日仅一次")

# ═══════════════════════════════════════════
# 10. 信号EOD存疑复核
# ═══════════════════════════════════════════
register("系统通知", "信号EOD复核·存疑", "收盘后自动复核当日全部信号, 标记存疑项",
         _card_v2("信号EOD复核: 3条存疑", "blue", [
             _md("以下信号在收盘复核中标记为**存疑**, 请人工确认:"),
             _table(
                 [{"name": "time", "display_name": "时间", "data_type": "text", "width": "15%"},
                  {"name": "code", "display_name": "代码/名称", "data_type": "text", "width": "20%"},
                  {"name": "signal", "display_name": "信号", "data_type": "text", "width": "30%"},
                  {"name": "reason", "display_name": "存疑原因", "data_type": "text", "width": "35%"}],
                 [{"time": "10:15", "code": "002407 多氟多", "signal": "回踩10MA缩量后突破昨高", "reason": "K线收盘价与新浪快照偏离>2%"},
                  {"time": "13:20", "code": "300750 宁德", "signal": "弱势极限(地量)", "reason": "触发时量比1.8>阈值1.5, 量未真缩"}],
             ),
         ]), "交易日17:00定时执行")

# ═══════════════════════════════════════════
# 11. 博主发帖
# ═══════════════════════════════════════════
register("系统通知", "博主发帖跟踪", "同花顺博主新帖推送",
         _card_v1("📣 博主发帖", "orange",
                  "**全能的野人** 刚刚发帖:\n\n"
                  "「今天情绪明显退潮, 涨停家数不到40家, 各位注意风控。"
                  "我自己的仓位已经从8成降到3成, 等情绪冰点过去再说。」\n\n"
                  "[查看原帖](https://t.10jqka.com.cn/uid_xxx)"), "交易日9-10点5分/10-15点20分/盘后60分/非交易日20:00")

# ═══════════════════════════════════════════
# 12. 空仓预警解除 (GREEN)
# ═══════════════════════════════════════════
register("风险预警", "市场风险·GREEN解除", "风险解除, 恢复正常操作",
         _card_v1("[GREEN] 市场风险解除", "green",
                  "广度MA20 **42.5%** 涨跌比 **48.2%** — 恢复至正常水平, 全仓操作."), "状态迁移时推送 · 14:40/16:40触发")

# ═══════════════════════════════════════════
# 13. 恐慌底机会提示
# ═══════════════════════════════════════════
register("风险预警", "恐慌底·机会提示", "昨停溢价5日均下穿0, 5年仅9次的机会窗",
         _card_v1("⚡ 恐慌底·机会提示", "orange",
                  "昨日涨停股溢价5日均下穿0(现 **-0.52%**) — 5年仅出现9次, "
                  "全部对应恐慌大底(2022-04-26 / 2024-01-31 / 2024-08-23 等).\n\n"
                  "该窗口内买点历史胜率 85.7%/单笔 +10.0%, 其中弱势极限(左侧)是主力收割模型.\n"
                  "提示: 这不是风险预警而是机会窗 — 恐慌加速末段, 弱势极限信号此时质量最高, 可按纪律执行."), "16:40 EOD评估触发 · 5年仅9次")

# ═══════════════════════════════════════════
# 14. 板块弱转强·启动 (盘中即时)
# ═══════════════════════════════════════════
register("盘面分析", "板块弱转强·启动", "题材早盘冷→涨停家数快速抬升, 状态跃迁即推",
         _card_v2("🟢 板块弱转强·启动", "blue", [
             _table(
                 [{"name": "theme", "display_name": "题材", "data_type": "text", "width": "30%"},
                  {"name": "lu", "display_name": "涨停", "data_type": "text", "width": "16%"},
                  {"name": "trend", "display_name": "近段", "data_type": "text", "width": "20%"},
                  {"name": "rep", "display_name": "代表股", "data_type": "text", "width": "34%"}],
                 [{"theme": "PCB", "lu": "4家", "trend": "+3", "rep": "胜宏科技、景旺电子"},
                  {"theme": "算力服务器", "lu": "4家", "trend": "+2", "rep": "工业富联、沪电股份"}],
             ),
         ]), "交易日每3分钟扫描 · 弱转强启动跃迁即推, 同题材当日仅一次")

# ═══════════════════════════════════════════
# 15. 板块强转弱·退潮 (盘中即时)
# ═══════════════════════════════════════════
register("盘面分析", "板块强转弱·退潮", "热门题材涨停回落/封板大面积松动(炸板), 状态跃迁即推",
         _card_v2("🔴 板块强转弱·退潮", "blue", [
             _table(
                 [{"name": "theme", "display_name": "题材", "data_type": "text", "width": "30%"},
                  {"name": "lu", "display_name": "涨停", "data_type": "text", "width": "16%"},
                  {"name": "trend", "display_name": "炸板", "data_type": "text", "width": "20%"},
                  {"name": "rep", "display_name": "代表股", "data_type": "text", "width": "34%"}],
                 [{"theme": "光通信", "lu": "2家", "trend": "炸4", "rep": "中际旭创、新易盛"},
                  {"theme": "固态电池", "lu": "1家", "trend": "炸3", "rep": "三祥新材、上海洗霸"}],
             ),
         ]), "交易日每3分钟扫描 · 强转弱退潮跃迁即推, 同题材当日仅一次")

# ═══════════════════════════════════════════
# 16. 次日板块预测 (14:30 收盘前)
# ═══════════════════════════════════════════
register("盘面分析", "次日板块预测", "收盘前用多日涨停序列+今日质地做次日强弱预判(启发式未回测)",
         _card_v2("📅 次日板块预测", "blue", [
             _md("**📅 次日板块预测**　_收盘前启发式预判, 未回测, 仅供布局参考_"),
             _md("🟢弱转强 1　·　🔴强转弱 1　·　⬆️强势延续 3　·　⚰️疑似终结 137"),
             _md("🟢 **弱转强候选**（1）"),
             _table(
                 [{"name": "theme", "display_name": "题材", "data_type": "text", "width": "26%"},
                  {"name": "traj", "display_name": "近期轨迹", "data_type": "text", "width": "30%"},
                  {"name": "reason", "display_name": "理由", "data_type": "text", "width": "44%"}],
                 [{"theme": "创新药", "traj": "0→1→1→3", "reason": "近3日低迷(均0.7), 今日回升至3家, 次日或反弹启动"}],
             ),
             _md("🔴 **强转弱候选**（1）"),
             _table(
                 [{"name": "theme", "display_name": "题材", "data_type": "text", "width": "26%"},
                  {"name": "traj", "display_name": "近期轨迹", "data_type": "text", "width": "30%"},
                  {"name": "reason", "display_name": "理由", "data_type": "text", "width": "44%"}],
                 [{"theme": "光通信", "traj": "3→5→6→3", "reason": "近3日均5家高位, 今日3家较昨(6)回落, 次日防退潮"}],
             ),
             _md("⬆️ **强势延续**（3）"),
             _table(
                 [{"name": "theme", "display_name": "题材", "data_type": "text", "width": "26%"},
                  {"name": "traj", "display_name": "近期轨迹", "data_type": "text", "width": "30%"},
                  {"name": "reason", "display_name": "理由", "data_type": "text", "width": "44%"}],
                 [{"theme": "半导体", "traj": "4→5→6→6", "reason": "高位企稳, 次日有望延续"}],
             ),
             _md("⚰️ 疑似终结 137 个（已沉寂, 不展开）：某退潮老题材、数据中心电源、液冷服务器、半导体石英、印尼锂盐、参股算力 等"),
         ]), "交易日14:30定时执行 · 启发式未回测 · 疑似终结折叠计数不堆名")

# ═══════════════════════════════════════════
# 17. 真假强势评分快照 (14:30 盘中)
# ═══════════════════════════════════════════
register("盘面分析", "真假强势评分快照", "盘中给股票池打真假强势分, 真强势=企稳首日率先放量上攻者",
         _card_v2("📊 真假强势评分快照", "blue", [
             _md("**📊 真假强势评分快照**　_14:30 盘中_"),
             _md("沪指 3210.78　·　5日 -4.77%　|　🟢真强势 34　·　🟡观望 8"),
             _md("🟢 **真强势**（34 只，展示前 15）"),
             _table(
                 [{"name": "name", "display_name": "名称", "data_type": "text", "width": "26%"},
                  {"name": "ind", "display_name": "行业", "data_type": "text", "width": "20%"},
                  {"name": "score", "display_name": "评分", "data_type": "text", "width": "12%"},
                  {"name": "cum", "display_name": "5日累计", "data_type": "options", "width": "16%"},
                  {"name": "plus", "display_name": "关键加分", "data_type": "text", "width": "26%"}],
                 [{"name": "国际复材 301526", "ind": "玻璃玻纤", "score": "105分",
                   "cum": [{"text": "+59.58%", "color": "red"}], "plus": "+25逆势创新高 +20多头排列"},
                  {"name": "中际旭创 300308", "ind": "通信设备", "score": "85分",
                   "cum": [{"text": "+11.26%", "color": "red"}], "plus": "+25逆势创新高 +20量缩健康+收阳"}],
             ),
             _md("🟡 **观望**（展示前 8 只）"),
             _table(
                 [{"name": "name", "display_name": "名称", "data_type": "text", "width": "40%"},
                  {"name": "score", "display_name": "评分", "data_type": "text", "width": "28%"},
                  {"name": "cum", "display_name": "5日累计", "data_type": "options", "width": "32%"}],
                 [{"name": "华正新材 603186", "score": "60分", "cum": [{"text": "+38.79%", "color": "red"}]},
                  {"name": "云南锗业 002428", "score": "55分", "cum": [{"text": "+23.73%", "color": "red"}]}],
             ),
             _md("💡 真强势=大盘企稳首日率先放量上攻者, 才是买点; 观望=强度够但未确认, 等放量再跟"),
         ]), "交易日14:30定时执行 · 盘中评分快照")

# ═══════════════════════════════════════════
# 18. 错过消息回顾 (推送开关关→开补发)
# ═══════════════════════════════════════════
register("系统通知", "错过消息回顾", "推送开关关闭期间错过的关键信号(买/卖/减仓/急跌预警), 重新打开时汇总补发一卡",
         _card_v2("📮 错过消息回顾", "blue", [
             _md("**📮 错过消息回顾**　_推送开关关闭期间错过的关键信号, 现汇总补发_"),
             _md("**⚠️ 当前市场风险档: ⚡ YELLOW 谨慎**"),
             _table(
                 [{"name": "time", "display_name": "时间", "data_type": "text", "width": "20%"},
                  {"name": "kind", "display_name": "类型", "data_type": "text", "width": "22%"},
                  {"name": "name", "display_name": "标的", "data_type": "text", "width": "32%"},
                  {"name": "sig", "display_name": "信号", "data_type": "text", "width": "26%"}],
                 [{"time": "06-18 10:30", "kind": "🟢买入", "name": "国际复材 301526", "sig": "缩量突破"},
                  {"time": "06-18 13:05", "kind": "🟡减仓", "name": "中际旭创 300308", "sig": "接近前高"},
                  {"time": "06-18 14:12", "kind": "🔴卖出", "name": "兆易创新 603986", "sig": "弱势止损"}],
             ),
         ]), "推送开关关→开时触发 · 只补关键信号 · 最近24h/最多30条 · 飞书企微各自独立")


# ═══════════════════════════════════════════
# 19. 自选股黑天鹅预警 (风险公告+财务红旗 合并单卡, v1.7.488/493/504/506)
# ═══════════════════════════════════════════
register("黑天鹅预警", "自选股黑天鹅预警", "风险公告(监管硬信号·每只挂AI逐股研判)+财务红旗(年报指标)合并一张两区域卡, 18:30一次",
         _card_v2("⚠️ 自选股黑天鹅预警", "blue", [
             _md("**🚨 风险公告（2）** 监管/财务硬信号 · 每只票 AI 抓公告正文逐股研判(严重度🔴高/🟡中/⚪低)"),
             _table(
                 [{"name": "stock", "display_name": "股票", "data_type": "text", "width": "16%"},
                  {"name": "tag", "display_name": "风险类型", "data_type": "text", "width": "16%"},
                  {"name": "date", "display_name": "日期", "data_type": "text", "width": "12%"},
                  {"name": "title", "display_name": "公告", "data_type": "text", "width": "30%"},
                  {"name": "ai", "display_name": "🤖AI研判", "data_type": "text", "width": "26%"}],
                 [{"stock": "合力泰\n002217", "tag": "立案调查", "date": "2025-04-29",
                   "title": "关于公司及实际控制人收到证监会立案告知书的公告",
                   "ai": "🔴高 · 实控人遭证监会立案，信披违法实锤，退市与索赔风险，持仓应规避"},
                  {"stock": "润泽科技\n300442", "tag": "交易所问询函", "date": "2026-05-10",
                   "title": "关于对深交所年报问询函的回复公告",
                   "ai": "🟡中 · 常规年报审核问询及中介回复，程序性环节非造假质疑，影响有限"}],
             ),
             _md("**📉 财务红旗（4）** 年报指标打分"),
             _table(
                 [{"name": "stock", "display_name": "股票", "data_type": "text", "width": "24%"},
                  {"name": "level", "display_name": "告警级别", "data_type": "text", "width": "16%"},
                  {"name": "flags", "display_name": "财务红旗", "data_type": "text", "width": "60%"}],
                 [{"stock": "诺德股份\n600110", "level": "🔴高危", "flags": "连续亏损·累计亏损-1.1亿·存贷双高"},
                  {"stock": "黄河旋风\n600172", "level": "🔴高危", "flags": "连续亏损·累计亏损-27.2亿·高杠杆91%"},
                  {"stock": "东方财富\n300059", "level": "🟠中危", "flags": "利润现金流背离·高杠杆77%"},
                  {"stock": "弘信电子\n300657", "level": "🟡关注", "flags": "累计亏损-3.8亿·高杠杆80%"}],
             ),
             _md("**纯提示, 不影响买卖点。** 监管/财务红旗多为 ST 黑天鹅前兆。"),
         ]), "交易日18:30定时执行 · 两区域常驻(某类无新增标「本次无新增」) · ≥1区域有新增才发")


# ═══════════════════════════════════════════
# 20. 持仓研判晚报 (交易日前夜20:00, 逐股数据体检+AI次日方向性建议, v1.7.x)
# ═══════════════════════════════════════════
register("持仓研判晚报", "持仓研判晚报", "交易日前夜20:00逐股数据体检+AI次日方向性建议(持/减/清/加+目标价/止损价), 客观概率源自全市场五年同类形态前向分布",
         _card_v2("📋 持仓研判晚报", "blue", [
             _md("**次日大盘环境**：风险偏低，可正常持仓"),
             _table(
                 [{"name": "stock", "display_name": "股票", "data_type": "text", "width": "26%"},
                  {"name": "state", "display_name": "状态/浮盈", "data_type": "text", "width": "24%"},
                  {"name": "advice", "display_name": "次日建议", "data_type": "text", "width": "22%"},
                  {"name": "reason", "display_name": "理由", "data_type": "text", "width": "28%"}],
                 [{"stock": "京东方A\n000725 7.18", "state": "多头站均线\n浮盈+12.0% 持5天",
                   "advice": "🟢持有\n目标7.60 止损6.70", "reason": "量能延续板块第二强，同类形态次日↑58%"},
                  {"stock": "沪电股份\n002463 151.0", "state": "高位放量滞涨\n浮盈+24.0% 持11天",
                   "advice": "🟡减仓\n目标150 止损140", "reason": "高位放量滞涨，同类形态次日↑47%偏弱"}],
             ),
             _md("**AI研判仅供参考，最终决策在你。** 客观概率源自全市场五年同类形态回测。"),
         ]), "交易日前夜20:00定时执行 · 内部判「明天是交易日」才发(周五晚/节假日前不发) · 空仓只发一行「今日空仓」")


# ── 快捷设置动作行: 个股买入/卖出信号卡底部统一追加(与真实推送 1:1, 实推由 push_pref.build_quick_actions_md 生成) ──
# 预览里链接做视觉展示(指向 # ), 真实推送是带 HMAC 签名的 /api/quick/set 链接
_QUICK_ACTIONS_DEMO = "[🔕 今日免打扰](#)"

for _tpl in TEMPLATES:
    if _tpl["category"] in ("买入信号", "卖出信号"):
        _els = _tpl["card"].get("body", {}).get("elements")
        if isinstance(_els, list):
            _els.append(_md(_QUICK_ACTIONS_DEMO))


# ── API ──

@router.get("")
async def list_templates(user: Annotated[dict, Depends(get_current_user)]):
    """返回所有飞书推送模版列表(含示例卡片JSON + 触发时机)."""
    import os
    from datetime import datetime
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
