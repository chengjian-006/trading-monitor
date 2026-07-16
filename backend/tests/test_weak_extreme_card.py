"""弱势极限尾盘快照结构卡(基线 v1.1)单测: 机会家族红卡 + 五区骨架 + 全短列表格。"""


def _hit(**kw):
    d = {"code": "600000", "name": "测试", "close": 12.34, "pct": -1.23,
         "amount": 2.3e8, "detail": "地量0.65×均量 · 贴MA10"}
    d.update(kw)
    return d


def test_weak_card_single():
    from backend.services.weak_extreme_scanner import build_weak_extreme_card
    c = build_weak_extreme_card([_hit()], "尾盘快照")
    assert c.title == "📈 弱势极限 · 测试(600000)"
    # 买点候选 = 机会家族红(原 blue 升级)
    assert c.family == "opportunity" and c.template == "red"
    assert c.subtitle == "收盘入场 · 左侧"
    assert c.tags == [("买点候选", "red")]
    assert "测试" in c.summary and "弱势极限" in c.summary and "12.34" in c.summary
    # 结论行: 标的+事件+关键数(现价加粗/涨跌带色)
    concl = c.elements[0]["content"]
    assert "**测试(600000)**" in concl and "**12.34**" in concl and "-1.2%" in concl
    # 单只: 免表格, 结论行 → 👉建议 → 折叠技术参数
    assert c.elements[1]["content"] == "👉 **等收盘确认再入场，左侧耐心持有**"
    assert c.elements[2]["tag"] == "collapsible_panel"
    # fallback 同源信息量(含技术参数明细 + 建议)
    assert "地量0.65×均量" in c.fallback and "👉 等收盘确认再入场" in c.fallback


def test_weak_card_multi_short_table():
    from backend.services.weak_extreme_scanner import build_weak_extreme_card
    hits = [_hit(), _hit(code="600001", name="乙", close=8.88, pct=0.5)]
    c = build_weak_extreme_card(hits, "尾盘快照")
    assert c.title == "📈 弱势极限 · 2只"
    tbl = c.elements[1]["content"]
    assert tbl.splitlines()[0] == "| 股票 | 现价 | 涨跌 |"   # 全短列, 重要列前置
    assert "测试(600000)" in tbl and "乙(600001)" in tbl
    assert "成交" not in tbl                                  # 长值(成交额/参数)不进表格
    # 建议行在表格后, 折叠技术参数永远在其后
    assert c.elements[2]["content"].startswith("👉")
    assert c.elements[-1]["tag"] == "collapsible_panel"


def test_weak_section_text_unchanged():
    # build_weak_extreme_section 仍被 盘面播报/收盘汇总/尾盘决策 引用, 文本口径不动
    from backend.services.weak_extreme_scanner import build_weak_extreme_section
    s = build_weak_extreme_section([_hit()])
    assert "弱势极限·收盘候选 (1只)" in s and "测试(600000)" in s
    assert build_weak_extreme_section([]) == ""
