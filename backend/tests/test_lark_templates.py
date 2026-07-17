# -*- coding: utf-8 -*-
"""预览页(lark_templates)回归测试 — 预览卡直调真实构卡函数生成, 与真实推送 1:1.

覆盖: 模版清单完整性(id唯一/分类/结构字段) + 每张卡 JSON 结构合法
(schema2.0 有 body.elements; config.summary 存在的卡摘要非空; header 家族色合法)
+ 直调卡与对应 service 构卡函数产物一致(抽样) + 列表/详情路由。不连库不联网。
"""
import asyncio

import pytest

from backend.routers import lark_templates as lt

VALID_TEMPLATES = {"red", "orange", "yellow", "green", "blue", "grey", "purple", "turquoise"}
KNOWN_CATEGORIES = {"买入信号", "卖出信号", "风险预警", "盘面分析", "系统通知",
                    "黑天鹅预警", "持仓研判晚报", "盘后提醒"}


def _v2_cards():
    return [t for t in lt.TEMPLATES if t["card"].get("schema") == "2.0"]


# ── 清单完整性 ──

def test_templates_nonempty_and_ids_unique():
    assert len(lt.TEMPLATES) >= 40
    ids = [t["id"] for t in lt.TEMPLATES]
    assert len(ids) == len(set(ids))


def test_template_meta_fields():
    for t in lt.TEMPLATES:
        assert t["id"] == f"{t['category']}/{t['name']}"
        assert t["category"] in KNOWN_CATEGORIES
        assert isinstance(t["name"], str) and t["name"]
        assert isinstance(t["description"], str) and t["description"]
        assert isinstance(t["timing"], str)
        assert isinstance(t["card"], dict) and t["card"]


def test_all_categories_present():
    assert {t["category"] for t in lt.TEMPLATES} == KNOWN_CATEGORIES


# ── 每张卡 JSON 结构合法 ──

def test_card_json_structure_valid():
    for t in lt.TEMPLATES:
        card = t["card"]
        header = card.get("header") or {}
        assert header.get("title", {}).get("content"), t["id"]
        assert header.get("template") in VALID_TEMPLATES, t["id"]
        if card.get("schema") == "2.0":
            els = card.get("body", {}).get("elements")
        else:  # v1 卡
            els = card.get("elements")
        assert isinstance(els, list) and els, t["id"]
        for e in els:
            assert isinstance(e, dict) and e.get("tag"), t["id"]


def test_v2_cards_majority_and_summary_nonempty():
    """改造后绝大多数是 2.0 结构卡; config.summary 存在的卡其内容非空。"""
    v2 = _v2_cards()
    assert len(v2) >= len(lt.TEMPLATES) - 3   # 仅个别旧通道卡仍为 v1
    with_summary = 0
    for t in v2:
        summary = t["card"].get("config", {}).get("summary")
        if summary is not None:
            assert isinstance(summary.get("content"), str) and summary["content"].strip(), t["id"]
            with_summary += 1
    assert with_summary >= 30   # 基线 v1.1: 摘要是标配


def test_v2_title_carries_time_like_real_push():
    """真实 _build_card_v2 会在标题栏拼时间(预览走同一信封)。"""
    for t in _v2_cards():
        title = t["card"]["header"]["title"]["content"]
        assert any(ch.isdigit() for ch in title.rsplit(" ", 1)[-1]), t["id"]


def test_text_tags_at_most_3():
    for t in _v2_cards():
        tags = t["card"]["header"].get("text_tag_list") or []
        assert len(tags) <= 3, t["id"]


def test_collapsible_and_table_shapes():
    """折叠面板/图表元素若出现, 结构须与真实推送一致(header.title / chart硬规则)。"""
    for t in _v2_cards():
        for e in t["card"]["body"]["elements"]:
            if e.get("tag") == "collapsible_panel":
                assert e["header"]["title"]["content"], t["id"]
                assert e.get("elements"), t["id"]
            if e.get("tag") == "chart":
                assert e.get("aspect_ratio") == "2:1", t["id"]
                assert e["chart_spec"].get("media") == [], t["id"]


# ── 直调一致性(抽样): 预览卡 = 真实构卡函数产物 ──

def _by_id(tid: str) -> dict:
    return next(t for t in lt.TEMPLATES if t["id"] == tid)


def test_signal_card_uses_real_builder_output():
    """买入信号卡含真实 _build_signal_elements 的特征件: KPI三栏 + 触发模型行 + 战绩表。"""
    card = _by_id("买入信号/缩量后放量突破(无预警)")["card"]
    els = card["body"]["elements"]
    assert any(e.get("tag") == "column_set" for e in els)          # KPI 三栏
    md_all = "\n".join(str(e.get("content", "")) for e in els if e.get("tag") == "markdown")
    assert "⚡ 触发模型　**缩量后放量突破（右侧）**" in md_all       # 触发模型全名加粗行
    assert "| 周期 | 胜率 | 单笔 |" in md_all                       # 模型战绩表
    assert "▰" in md_all                                            # 胜率强度条
    assert "🔕 静到再突破" in md_all                                # 快捷动作行 demo
    assert "今日免打扰" not in md_all and "静音此股" not in md_all   # 已拆除入口不得回归
    assert card["header"]["template"] == "red"
    # 价格槽(批注②): 触发价49.47 → 名册族止损-6%/目标+7%, 相对触发价固定幅度
    assert "🎯 参考买入 **¥49.47**" in md_all
    assert "🛑 止损 **¥46.50**（-6%）" in md_all
    assert "🎯 目标 **¥52.93**（+7%）" in md_all


def test_price_slot_pcts_by_model():
    """价格槽止损/目标幅度按模型分流: 名册族-6%/+7%, 弱势极限-12%无目标, 强势起点/竞价只显买入价。"""
    from backend.services.notifier import _price_slot_pcts, _price_slot_md
    # 名册族(5个)全部 -6%/+7%
    for nm in ("回踩10MA缩量后突破昨高", "回踩20MA缩量后突破昨高", "回踩60MA缩量后突破昨高",
               "缩量后放量突破（右侧）", "中继平台突破"):
        assert _price_slot_pcts(nm) == (-0.06, 0.07), nm
    # 弱势极限: -12% 止损, 无目标
    assert _price_slot_pcts("弱势极限（左侧）") == (-0.12, None)
    # 方案A: 强势起点/竞价弱转强 只显买入价(无止损/目标)
    assert _price_slot_pcts("强势起点") == (None, None)
    assert _price_slot_pcts("竞价弱转强") == (None, None)
    # 手工/非模型: 无价格槽
    assert _price_slot_pcts("手工加入") is None
    # 弱势极限 md: 只有 参考买入 + 止损, 没有目标行
    md = _price_slot_md("弱势极限（左侧）", 42.25, bold=False)
    assert "🎯 参考买入 ¥42.25" in md and "🛑 止损 ¥37.18（-12%）" in md and "目标" not in md
    # 方案A md: 只有 参考买入 一行
    md_a = _price_slot_md("强势起点", 52.30, bold=False)
    assert md_a == "🎯 参考买入 ¥52.30"
    # 非模型: 空串
    assert _price_slot_md("手工加入", 10.0) == ""


def test_sell_card_has_sold_action():
    card = _by_id("卖出信号/弱势极限止损卖出")["card"]
    md_all = "\n".join(str(e.get("content", "")) for e in card["body"]["elements"]
                       if e.get("tag") == "markdown")
    assert "✅ 已卖出" in md_all
    assert card["header"]["template"] == "green"


def test_surge_card_matches_service():
    """二波卡与 second_surge.build_surge_card_v2 同源(✅触发清单 + 机会红卡)。"""
    card = _by_id("买入信号/二波过前高")["card"]
    assert card["header"]["template"] == "red"
    md_all = "\n".join(str(e.get("content", "")) for e in card["body"]["elements"]
                       if e.get("tag") == "markdown")
    assert "✅ 第一波冲高" in md_all and "✅ 二波过前高" in md_all
    assert "卫星化学" in card["config"]["summary"]["content"]


def test_escalation_card_is_risk_hot_red():
    card = _by_id("卖出信号/止损未执行·纪律升级")["card"]
    assert card["header"]["template"] == "red"      # 唯一允许绿越级红
    tags = card["header"].get("text_tag_list") or []
    assert any("第3天" in tag["text"]["content"] for tag in tags)


def test_dismiss_and_aggregate_standard_cards():
    dismiss = _by_id("风险预警/预警解除(标准解除卡)")["card"]
    assert dismiss["header"]["template"] == "grey"  # 解除卡=灰 header 中性收尾
    assert "✅ 预警解除" in dismiss["header"]["title"]["content"]
    agg = _by_id("风险预警/集中触发·聚合卡(标准聚合卡)")["card"]
    assert agg["header"]["template"] == "orange"
    assert "🚨 集中触发 · 自选3只" in agg["header"]["title"]["content"]
    md_all = "\n".join(str(e.get("content", "")) for e in agg["body"]["elements"]
                       if e.get("tag") == "markdown")
    assert "**归因**" in md_all and "| 股票 | 跌幅 | 信号 |" in md_all


def test_blackswan_card_two_sections():
    card = _by_id("黑天鹅预警/自选股黑天鹅预警")["card"]
    md_all = "\n".join(str(e.get("content", "")) for e in card["body"]["elements"]
                       if e.get("tag") == "markdown")
    assert "🚨 风险公告（2）" in md_all and "📉 财务红旗（4）" in md_all
    assert card["header"]["template"] == "orange"   # 风险家族


def test_system_cards_grey():
    for tid in ("系统通知/信号EOD复核·存疑", "系统通知/数据源健康预警", "系统通知/系统健康·盘后汇总"):
        assert _by_id(tid)["card"]["header"]["template"] == "grey", tid


def test_intel_cards_blue():
    for tid in ("盘面分析/晚盘复盘总结", "持仓研判晚报/持仓研判晚报"):
        assert _by_id(tid)["card"]["header"]["template"] == "blue", tid


def test_evening_review_card_has_holdings_and_disclosure():
    """晚盘复盘总结 = 持仓今日表现 + 信号胜率 + 近期披露 三段合一。"""
    card = _by_id("盘面分析/晚盘复盘总结")["card"]
    assert card["header"]["template"] == "blue"
    assert "晚盘复盘总结" in card["header"]["title"]["content"]
    md_all = _md_all(card)
    assert "持仓今日表现" in md_all              # 💼 持仓段
    assert "近期财报披露" in md_all              # 📅 披露段(从早盘挪来)
    assert "| 股票 | 今日 | 浮盈 |" in md_all    # 持仓全短表
    assert "| 股票 | 披露日 | 类型 |" in md_all  # 披露全短表
    assert card["config"]["summary"]["content"].strip()


def _md_all(card: dict) -> str:
    return "\n".join(str(e.get("content", "")) for e in card["body"]["elements"]
                     if e.get("tag") == "markdown")


def test_ma_touch_card_matches_service():
    """均线到线提醒 = ma_touch_alert.build_touch_card 直调产物(情报蓝卡+KPI三栏)。"""
    card = _by_id("盘后提醒/均线到线提醒")["card"]
    assert card["header"]["template"] == "blue"
    assert "🔔 到线提醒" in card["header"]["title"]["content"]
    assert "触及20日线" in card["header"]["title"]["content"]
    els = card["body"]["elements"]
    assert any(e.get("tag") == "column_set" for e in els)        # KPI 三栏
    assert any(e.get("tag") == "collapsible_panel" for e in els)  # 口径折叠
    assert card["config"]["summary"]["content"].strip()
    assert "三花智控" in card["config"]["summary"]["content"]


def test_morning_focus_card_matches_service():
    """盘前今日关注 = morning_focus.build_morning_focus_card 直调产物。"""
    card = _by_id("盘面分析/盘前今日关注")["card"]
    assert card["header"]["template"] == "blue"
    md_all = _md_all(card)
    assert "昨日买点追踪" in md_all
    assert "今日披露财报" in md_all              # 今日披露一行(盘前速览)
    assert "大盘风险" in md_all                  # 当前生效状态段
    # 样例 6 只 > TOP_N=5 → 全量折叠必须出现
    assert any(e.get("tag") == "collapsible_panel"
               for e in card["body"]["elements"])
    assert card["config"]["summary"]["content"].strip()
    assert "今日关注" in card["config"]["summary"]["content"]


def test_push_health_card_matches_service():
    """推送健康度周报 = push_health_report.build_health_card 直调产物(系统灰卡)。"""
    card = _by_id("系统通知/推送健康度周报")["card"]
    assert card["header"]["template"] == "grey"
    md_all = _md_all(card)
    assert "被关的模型" in md_all
    assert "弱势极限" in md_all                  # name_map 展示中文名, 禁 signal_id 代号
    assert "BUY_WEAK_EXTREME" not in md_all
    assert "模型图鉴" in md_all                  # 集中被关 → 点名建议分支
    assert card["config"]["summary"]["content"].strip()


# ── 路由 ──

def test_list_templates_route():
    out = asyncio.run(lt.list_templates(user={"id": 1}))
    assert out["templates"] is lt.TEMPLATES
    assert out["updated_at"]


def test_get_template_route():
    tid = lt.TEMPLATES[0]["id"]
    got = asyncio.run(lt.get_template(tid, user={"id": 1}))
    assert got["id"] == tid
    missing = asyncio.run(lt.get_template("不存在/xx", user={"id": 1}))
    assert missing == {"error": "not found"}
