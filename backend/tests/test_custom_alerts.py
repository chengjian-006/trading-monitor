"""自定义预警条件求值单测 (纯逻辑, 不连库)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.custom_alert_scanner import (  # noqa: E402
    build_ctx, eval_conditions, _eval_one, describe_conditions,
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
