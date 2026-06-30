"""卖出信号三分组(主动止盈/被动止损/纪律清仓)归类测试 — signal_specs.sell_category。"""
from backend.services.signal_specs import sell_category, sell_group_category


def test_profit():
    for sid in ("SELL_TAKE_PROFIT", "SELL_RR_TARGET", "SELL_TRAIL_STOP",
                "SELL_RALLY_MA20_HALF", "SELL_RALLY_MA10_HALF"):
        assert sell_category(sid, "") == "profit", sid


def test_loss():
    for sid in ("SELL_LOSS_5", "SELL_LOSS_8", "SELL_LOSS_10", "SELL_WEAK_STOP",
                "SELL_BREAK_MA5", "SELL_BREAK_MA10", "SELL_BREAK_MA20"):
        assert sell_category(sid, "") == "loss", sid


def test_discipline():
    assert sell_category("SELL_WEAK_TIME", "弱势极限 持有满15日 清仓") == "discipline"
    # SELL_TIME_STOP 名含"止损"但属纪律(时间到), 必须 id 先判
    assert sell_category("SELL_TIME_STOP", "时间止损-5日") == "discipline"


def test_rally_derived_by_name():
    # 回踩MA派生卖点未登记 id, 按名称关键词
    assert sell_category("SELL_RALLY_MA20", "回踩MA20 止损") == "loss"
    assert sell_category("SELL_RALLY_MA20", "回踩MA20 +7%止盈减半") == "profit"
    assert sell_category("SELL_RALLY_MA10", "回踩MA10 时间止损") == "discipline"


def test_group_priority_loss_first():
    # 同股多信号: 被动止损 > 纪律清仓 > 主动止盈
    assert sell_group_category([("SELL_TAKE_PROFIT", "止盈减仓+7%"),
                                ("SELL_LOSS_10", "浮亏止损-10%")]) == "loss"
    assert sell_group_category([("SELL_TAKE_PROFIT", "止盈减仓+7%"),
                                ("SELL_WEAK_TIME", "持满清仓")]) == "discipline"
    assert sell_group_category([("SELL_TAKE_PROFIT", "止盈减仓+7%")]) == "profit"


def test_today_six_stocks():
    """今天 6 只实际归类对账。"""
    assert sell_category("SELL_TAKE_PROFIT", "止盈减仓 +7%") == "profit"      # 麦捷/康强/沪电/天华
    assert sell_category("SELL_LOSS_10", "浮亏止损 -10%") == "loss"            # 卫星化学
    assert sell_category("SELL_BREAK_MA20", "短线卖 跌破MA20") == "loss"       # 卫星化学
    assert sell_category("SELL_WEAK_TIME", "弱势极限 持有满15日 清仓") == "discipline"  # 阳光
