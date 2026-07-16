"""自定义预警条件求值单测 (纯逻辑, 不连库)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.custom_alert_scanner import (  # noqa: E402
    build_ctx, eval_conditions, _eval_one, describe_conditions, describe_hit,
)


def ctx(price=10.0, pct=2.0, closes=None):
    return build_ctx(price, pct, closes if closes is not None else [9.0] * 60)


def test_price_gte_lte():
    assert _eval_one({"dim": "price", "op": "gte", "value": 9.5}, ctx(price=10))
    assert not _eval_one({"dim": "price", "op": "gte", "value": 11}, ctx(price=10))
    assert _eval_one({"dim": "price", "op": "lte", "value": 11}, ctx(price=10))
    assert not _eval_one({"dim": "price", "op": "lte", "value": 9}, ctx(price=10))


def test_pct():
    assert _eval_one({"dim": "pct", "op": "gte", "value": 7}, ctx(pct=7.5))
    assert not _eval_one({"dim": "pct", "op": "gte", "value": 7}, ctx(pct=6.9))
    assert _eval_one({"dim": "pct", "op": "lte", "value": -5}, ctx(pct=-6))


def test_ma_near():
    # 60 根收盘=10 → MA10=10; 现价10.1 距 1% → ±2% 命中, ±0.5% 不命中
    c = build_ctx(10.1, 1.0, [10.0] * 60)
    assert _eval_one({"dim": "ma_near", "ma": 10, "band": 2}, c)
    assert not _eval_one({"dim": "ma_near", "ma": 10, "band": 0.5}, c)


def test_ma_cross_up_down():
    # 昨收 9.0, MA10=10.0(60根=10), 现价 10.5 → 上穿命中, 跌破不命中
    c = build_ctx(10.5, 1.0, [10.0] * 60)
    c["prev_close"] = 9.0
    assert _eval_one({"dim": "ma_cross", "ma": 10, "dir": "up"}, c)
    assert not _eval_one({"dim": "ma_cross", "ma": 10, "dir": "down"}, c)
    # 昨收 11 现价 9.5 → 跌破命中
    c2 = build_ctx(9.5, -1.0, [10.0] * 60)
    c2["prev_close"] = 11.0
    assert _eval_one({"dim": "ma_cross", "ma": 10, "dir": "down"}, c2)
    assert not _eval_one({"dim": "ma_cross", "ma": 10, "dir": "up"}, c2)


def test_missing_data_never_triggers():
    # 无现价
    assert not _eval_one({"dim": "price", "op": "gte", "value": 1}, build_ctx(None, 1, [10] * 60))
    # MA 数据不足(只有 3 根, < MA10 最低 7 根)
    c = build_ctx(10, 1, [10, 10, 10])
    assert not _eval_one({"dim": "ma_near", "ma": 10, "band": 5}, c)
    assert not _eval_one({"dim": "ma_cross", "ma": 10, "dir": "up"}, c)


def test_and_combination():
    c = ctx(price=10, pct=8, closes=[9.0] * 60)
    conds = [{"dim": "price", "op": "gte", "value": 9.5}, {"dim": "pct", "op": "gte", "value": 7}]
    assert eval_conditions(conds, c)
    # 一个不满足 → 整体不触发
    conds2 = [{"dim": "price", "op": "gte", "value": 9.5}, {"dim": "pct", "op": "gte", "value": 9}]
    assert not eval_conditions(conds2, c)
    # 空条件不触发
    assert not eval_conditions([], c)


def test_describe():
    s = describe_conditions([
        {"dim": "price", "op": "gte", "value": 15.2},
        {"dim": "ma_near", "ma": 10, "band": 2},
        {"dim": "ma_cross", "ma": 20, "dir": "up"},
        {"dim": "pct", "op": "lte", "value": -5},
    ])
    assert "价格≥15.2" in s and "接近MA10(±2%)" in s and "上穿MA20" in s and "涨跌幅≤-5%" in s


def test_describe_hit_preset_plain_language():
    # 均线快捷预设: 大白话"股价碰到X日线" + 现价/均线值加粗
    it = {"preset": "ma20", "price": 58.39, "ma_value": 58.10,
          "conditions": [{"dim": "ma_near", "ma": 20, "band": 0.5}]}
    s = describe_hit(it)
    assert "股价碰到20日线" in s
    assert "**58.39**" in s and "**58.10**" in s
    assert "今天不再重复报" in s


def test_describe_hit_custom_fallback():
    # 普通自定义(无 preset): 退回条件摘要
    it = {"preset": "", "price": 10.0, "ma_value": None,
          "conditions": [{"dim": "price", "op": "gte", "value": 9.5}]}
    assert "满足: 价格≥9.5" in describe_hit(it)


def test_build_alert_card_single():
    # 基线 v1.1 五区骨架: 结论 → 全短列表(股票|现价|涨跌) → 👉建议 → 折叠明细
    from backend.services.custom_alert_scanner import build_alert_card
    items = [{"code": "600519", "name": "贵州茅台", "price": 1580.0, "pct_change": 2.34,
              "conditions": [{"dim": "price", "op": "gte", "value": 1500}],
              "note": "", "preset": "", "repeat_daily": False, "ma_value": None}]
    title, content, elements = build_alert_card(items)
    assert title == "🔔 自定义预警 · 贵州茅台(600519)"
    assert "满足: 价格≥1500" in elements[0]["content"]
    tbl = elements[1]["content"]
    assert tbl.splitlines()[0] == "| 股票 | 现价 | 涨跌 |"
    assert "1580.00" in tbl and "+2.3%" in tbl
    assert elements[2]["content"].startswith("👉 **")
    assert elements[3]["tag"] == "collapsible_panel"
    # fallback 同源信息量: 明细 + 建议 + 一次性说明
    assert "现价 **1580.00**" in content
    assert "👉 对照预警条件核实，按计划操作" in content
    assert "一次性预警已自动停用" in content


def test_build_alert_card_multi_and_none_pct():
    from backend.services.custom_alert_scanner import build_alert_card
    items = [
        {"code": "600519", "name": "贵州茅台", "price": 1580.0, "pct_change": None,
         "conditions": [{"dim": "price", "op": "gte", "value": 1500}],
         "note": "", "preset": "", "repeat_daily": False, "ma_value": None},
        {"code": "601689", "name": "拓普集团", "price": 58.39, "pct_change": -1.2,
         "conditions": [{"dim": "ma_near", "ma": 20, "band": 0.5}],
         "note": "", "preset": "ma20", "repeat_daily": True, "ma_value": 58.10},
    ]
    title, content, elements = build_alert_card(items)
    assert title == "🔔 自定义预警 · 2只"
    assert "同时触发 **2** 条" in elements[0]["content"]
    tbl = elements[1]["content"]
    assert "| 贵州茅台(600519) | 1580.00 | - |" in tbl  # 无涨跌数据显示 -
    assert "-1.2%" in tbl
    # 折叠明细含大白话触发描述; foot 两种说明都带上
    assert "股价碰到20日线" in content
    assert "一次性预警已自动停用" in content and "均线提醒每天最多报一次" in content


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
