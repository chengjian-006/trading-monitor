"""card_kit 构造器单测(基线 v1.1): 图形词汇表硬规则 + 信封字段 + 标准卡型。"""
import asyncio

from backend.services import card_kit, lark_notifier, notifier
from backend.services.card_kit import (
    Card, aggregate_card, chart, checklist, dismiss_card, family_template,
    heat_strip, kpi_row, light_string, long_short_bar, pct_md, short_table,
    strength_bar, summary_text, thermometer,
)


# ── 家族色 ──

def test_family_template():
    assert family_template("opportunity") == "red"
    assert family_template("exit") == "green"
    assert family_template("risk") == "orange"
    assert family_template("risk_hot") == "red"
    assert family_template("intel") == "blue"
    assert family_template("system") == "grey"
    assert family_template("unknown") == "blue"


def test_card_template_property():
    c = Card(title="t", elements=[], fallback="f", family="exit")
    assert c.template == "green"


# ── 数字小件 ──

def test_pct_md():
    assert pct_md(2.7) == "<font color='red'>**+2.7%**</font>"
    assert pct_md(-1.8) == "<font color='green'>**-1.8%**</font>"
    assert "grey" in pct_md(0)


def test_summary_text():
    assert summary_text("拓普集团", "601689", None, "", "+2.7%") == "拓普集团 601689 +2.7%"


# ── 图形词汇表硬规则 ──

def test_strength_bar_caps_at_8():
    bar = strength_bar(1.0, "100%", slots=20)
    assert bar.count("▰") == 8 and "▱" not in bar
    half = strength_bar(0.5, "62%")
    assert half.count("▰") == 4 and half.count("▱") == 4
    assert "**62%**" in half


def test_checklist_format():
    md = checklist([("放量", "1.9×", "≥1.8×"), ("过前高", "¥98.50", "")])
    lines = md.split("\n")
    assert lines[0] == "✅ 放量 **1.9×**（要求 ≥1.8×）"
    assert lines[1] == "✅ 过前高 **¥98.50**"


def test_heat_strip():
    s = heat_strip([True, True, False, True, None])
    assert s == "🟩🟩🟥🟩⬜ 3胜1负"
    capped = heat_strip([True] * 15)
    assert capped.count("🟩") == 10 and "15胜0负" in capped


def test_thermometer_total_caps_at_10():
    t = thermometer([(4, "blue"), (9, "red")], "68°")
    assert t.count("▰") == 10 and "▱" not in t and "**68°**" in t
    short = thermometer([(2, "blue")], total=5)
    assert short.count("▰") == 2 and short.count("▱") == 3


def test_long_short_bar_each_side_max_3():
    bar = long_short_bar(33, 40)
    left, right = bar.split("｜")
    assert left.count("▰") <= 3 and right.count("▰") <= 3
    assert "跌33" in left and "涨40" in right


def test_light_string():
    s = light_string([("ok", "a"), ("ok", "b"), ("ok", "c"), ("warn", "d")])
    assert s.startswith("🟢🟢🟢🟡") and "3正常 1要看" in s
    many = light_string([("ok", str(i)) for i in range(9)] + [("warn", "x"), ("bad", "y")])
    assert "🟢9" in many and "🟡1" in many and "🔴1" in many
    assert many.count("🟢") == 1  # 超8盏改计数式, 不再排灯


def test_kpi_row_exactly_3_columns():
    el = kpi_row([("现价", "¥58.39"), ("涨幅", "+2.7%", "red"), ("排名", "第2名"), ("多余", "x")])
    assert el["tag"] == "column_set" and len(el["columns"]) == 3
    col0 = el["columns"][0]["elements"]
    assert col0[0]["text_size"] == "heading" and "**¥58.39**" in col0[0]["content"]
    assert "grey" in col0[1]["content"]
    assert "<font color='red'>" in el["columns"][1]["elements"][0]["content"]


def test_chart_hard_rules():
    line = chart("line", [("07-15", 61), ("07-16", 55)])
    assert line["aspect_ratio"] == "2:1"
    assert line["chart_spec"]["media"] == []          # 真机: 不锁会被平台拉回1:1
    assert line["chart_spec"]["axes"][0]["zero"] is False
    assert line["chart_spec"]["data"][0]["values"][0] == {"x": "07-15", "y": 61}
    bar = chart("bar", [("a", 1)])
    assert bar["chart_spec"]["type"] == "bar" and bar["chart_spec"]["media"] == []


def test_short_table_max_3_cols_and_escape():
    el = short_table(["股票", "跌幅", "信号", "多余列"], [("阳光电源", "-4.2%", "破|MA20", "x")])
    md = el["content"]
    assert md.splitlines()[0] == "| 股票 | 跌幅 | 信号 |"
    assert "破／MA20" in md and "多余列" not in md and "x" not in md


# ── 标准卡型 ──

def test_aggregate_card():
    c = aggregate_card(
        "跌破MA20",
        [("阳光电源", "-4.2%", "破MA20"), ("通威股份", "-3.8%", "破MA20"), ("隆基绿能", "-3.1%", "破MA10")],
        cause_md="大盘急跌 -1.8%", advice_text="板块共振跌，禁抄底",
        window="10:42~10:44", tag="板块共振",
        fold_detail="阳光电源 ¥68.20、通威股份 ¥21.35")
    assert c.title == "🚨 集中触发 · 自选3只 跌破MA20"
    assert c.template == "orange"
    assert c.subtitle == "10:42~10:44 合并推送"
    assert c.tags == [("板块共振", "orange")]
    assert "跌破MA20" in c.summary and "3只" in c.summary
    assert "**归因**" in c.elements[0]["content"]
    assert "| 股票 | 涨跌 | 信号 |" in c.elements[1]["content"]
    assert "👉 **板块共振跌，禁抄底**" in c.elements[2]["content"]
    assert c.elements[3]["tag"] == "collapsible_panel"
    assert "阳光电源 -4.2% 破MA20" in c.fallback and "👉 板块共振跌" in c.fallback


def test_dismiss_card():
    c = dismiss_card("情绪冰点(空仓预警)", issued_str="07-08 09:30", days_active=6,
                     condition_md="涨停家数5日均 **48** ≥ 45（连续 2 日）",
                     period_md="上证 -2.1%", advice_text="冰点解除，可逐步恢复试仓")
    assert c.title == "✅ 预警解除 · 情绪冰点(空仓预警)"
    assert c.template == "grey"
    assert c.subtitle == "07-08 09:30 发布 → 今日解除，生效 6 个交易日"
    assert c.tags == [("已解除", "grey")]
    assert "解除条件" in c.elements[0]["content"]
    assert "生效期间" in c.elements[1]["content"]
    assert "生效期间：上证 -2.1%" in c.fallback


# ── 信封字段(lark_notifier 扩展) ──

def test_build_card_v2_envelope_fields():
    card = lark_notifier._build_card_v2(
        "标题", [lark_notifier.md_element("x")], "red",
        summary="拓普集团 601689 缩量突破", subtitle="副标题",
        text_tags=[("缩量突破", "red"), ("第2名", "orange"), ("a", "grey"), ("超出", "red")])
    assert card["config"]["summary"]["content"] == "拓普集团 601689 缩量突破"
    assert card["header"]["subtitle"]["content"] == "副标题"
    tags = card["header"]["text_tag_list"]
    assert len(tags) == 3 and tags[0]["text"]["content"] == "缩量突破" and tags[0]["color"] == "red"


def test_build_card_v2_defaults_unchanged():
    card = lark_notifier._build_card_v2("标题", [lark_notifier.md_element("x")])
    assert "summary" not in card["config"]
    assert "subtitle" not in card["header"] and "text_tag_list" not in card["header"]
    assert card["schema"] == "2.0"


def test_send_card_passthrough(monkeypatch):
    captured = {}

    async def fake_send_dual_card(content, **kw):
        captured["content"] = content
        captured.update(kw)
        return True

    monkeypatch.setattr(notifier, "send_dual_card", fake_send_dual_card)
    c = card_kit.Card(title="T", elements=[{"tag": "markdown", "content": "x"}],
                      fallback="FB", family="risk", summary="S", subtitle="ST",
                      tags=[("t", "red")], link_url="http://a", link_text="看")
    ok = asyncio.run(notifier.send_card(c))
    assert ok is True
    assert captured["content"] == "FB"
    assert captured["lark_title"] == "T"
    assert captured["template"] == "orange"
    assert captured["summary"] == "S" and captured["subtitle"] == "ST"
    assert captured["text_tags"] == [("t", "red")]
    assert captured["link_url"] == "http://a"
