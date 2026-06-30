"""摊薄成本(券商参考成本价口径)纯函数测试 — compute_diluted_holdings.

v1.7.535 把持仓成本由 FIFO(剩余物理批次) 改为摊薄成本: 卖出金额抵减剩余成本, 清仓重置。
锁住: 真实序列对齐券商参考成本价、重置、摊薄压低成本、负成本、清仓不计。
"""
from backend.models.repo.holdings import compute_diluted_holdings


def _t(code, direction, qty, price, date, time="10:00:00"):
    return {"code": code, "direction": direction, "quantity": qty,
            "price": price, "trade_date": date, "trade_time": time}


def test_300390_matches_broker_reference_cost():
    """天华新能真实交割序列 → 摊薄成本 75.03(券商参考成本价75.38, 差额=手续费),
    建仓日=当前持仓段第一笔买入 2026-06-17(券商持股9天), 净持100。"""
    trades = [
        _t("300390", "buy", 100, 77.05, "2026-04-15"),
        _t("300390", "sell", 100, 83.90, "2026-04-16"),   # 第一轮清仓 → 重置
        _t("300390", "buy", 200, 95.02, "2026-05-27"),
        _t("300390", "buy", 200, 86.55, "2026-05-28", "13:30:48"),
        _t("300390", "sell", 200, 86.46, "2026-05-28", "14:55:22"),
        _t("300390", "sell", 200, 80.61, "2026-06-03"),   # 第二轮清仓 → 重置
        _t("300390", "buy", 100, 91.78, "2026-06-17"),    # 当前段建仓起点
        _t("300390", "buy", 100, 84.87, "2026-06-25", "10:37:57"),
        _t("300390", "buy", 100, 89.45, "2026-06-25", "13:24:42"),
        _t("300390", "sell", 100, 92.33, "2026-06-25", "14:37:59"),
        _t("300390", "sell", 100, 98.74, "2026-06-25", "14:56:27"),
    ]
    res = compute_diluted_holdings(trades)
    assert "300390" in res
    # (9178+8487+8945 - 9233 - 9874) / 100 = 75.03
    assert res["300390"]["avg_cost"] == 75.03
    assert res["300390"]["earliest_buy_date"] == "2026-06-17"


def test_simple_average_no_sell():
    trades = [_t("A", "buy", 100, 10.0, "2026-01-01"),
              _t("A", "buy", 100, 20.0, "2026-01-02")]
    res = compute_diluted_holdings(trades)
    assert res["A"]["avg_cost"] == 15.0
    assert res["A"]["earliest_buy_date"] == "2026-01-01"


def test_reset_on_flat_then_rebuy():
    """清仓后重新建仓 → 只算新一段, 建仓日=新买入日, 旧段盈亏不带入。"""
    trades = [_t("A", "buy", 100, 10.0, "2026-01-01"),
              _t("A", "sell", 100, 12.0, "2026-01-02"),    # flat → reset
              _t("A", "buy", 100, 20.0, "2026-01-05")]
    res = compute_diluted_holdings(trades)
    assert res["A"]["avg_cost"] == 20.0
    assert res["A"]["earliest_buy_date"] == "2026-01-05"


def test_profit_sell_dilutes_cost_below_buy_price():
    """买200@10后卖100@15 → 剩余100股摊薄成本 (2000-1500)/100 = 5.0(低于买入价)。"""
    trades = [_t("A", "buy", 200, 10.0, "2026-01-01"),
              _t("A", "sell", 100, 15.0, "2026-01-02")]
    res = compute_diluted_holdings(trades)
    assert res["A"]["avg_cost"] == 5.0


def test_cost_can_go_negative():
    """买100@10后卖50@30(落袋1500>投入1000) → 剩余50股成本 (1000-1500)/50 = -10.0。"""
    trades = [_t("A", "buy", 100, 10.0, "2026-01-01"),
              _t("A", "sell", 50, 30.0, "2026-01-02")]
    res = compute_diluted_holdings(trades)
    assert res["A"]["avg_cost"] == -10.0


def test_fully_closed_not_in_result():
    trades = [_t("A", "buy", 100, 10.0, "2026-01-01"),
              _t("A", "sell", 100, 12.0, "2026-01-02")]
    assert compute_diluted_holdings(trades) == {}
