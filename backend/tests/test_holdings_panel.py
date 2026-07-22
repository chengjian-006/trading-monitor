"""持仓明细面板纯函数测试 — trade_analysis._assemble_holdings + trading_calendar.trading_days_between.

锁住: 浮盈/市值/浮盈% 计算, 成本存疑与负成本的置空口径, 缺现价/缺回合的兜底,
按市值降序, 汇总聚合; 以及"持仓天数(交易日)"的计数。
"""
from datetime import date

from backend.routers.trade_analysis import _assemble_holdings
from backend.core.trading_calendar import trading_days_between


def _info(avg_cost, qty, buy_date="2026-03-02", unreliable=False):
    return {"avg_cost": avg_cost, "qty": qty, "earliest_buy_date": buy_date,
            "cost_unreliable": unreliable}


def test_normal_holding_pnl_and_market_value():
    info = {"600000": _info(10.0, 100)}
    pool = {"600000": {"code": "600000", "name": "浦发银行", "price": 12.0, "pct_change": 3.5,
                       "board_name": "银行", "board_rank": 2, "board_total": 40}}
    rnd = {"600000": {"code": "600000", "name": "浦发银行", "holding_days": 5,
                      "entry_model_name": "回踩MA10"}}
    out = _assemble_holdings(info, pool, rnd, date(2026, 3, 9))
    h = out["holdings"][0]
    assert h["avg_cost"] == 10.0
    assert h["market_value"] == 1200.0
    assert h["float_pnl"] == 200.0
    assert h["float_pnl_pct"] == 20.0
    assert h["holding_days"] == 5          # 用回合已算好的
    assert h["entry_model"] == "回踩MA10"
    assert h["board_name"] == "银行" and h["board_rank"] == 2 and h["board_total"] == 40
    assert out["summary"]["count"] == 1
    assert out["summary"]["total_market_value"] == 1200.0
    assert out["summary"]["total_float_pnl"] == 200.0
    assert out["summary"]["total_float_pnl_pct"] == 20.0


def test_cost_unreliable_mutes_cost_and_pnl_but_keeps_market_value():
    info = {"000001": _info(3.0, 200, unreliable=True)}
    pool = {"000001": {"name": "平安银行", "price": 5.0}}
    out = _assemble_holdings(info, pool, {}, date(2026, 3, 9))
    h = out["holdings"][0]
    assert h["cost_unreliable"] is True
    assert h["avg_cost"] is None          # 成本偏低不显
    assert h["float_pnl"] is None and h["float_pnl_pct"] is None
    assert h["market_value"] == 1000.0    # 市值仍按现价×股数
    assert out["summary"]["total_float_pnl"] == 0.0
    assert out["summary"]["total_float_pnl_pct"] is None   # 无可信成本 → 无整体浮盈%


def test_negative_cost_pnl_amount_but_pct_none():
    """摊薄成本≤0(已超额落袋): 浮盈额照算(price-负成本)*qty, 浮盈%无意义置空。"""
    info = {"300390": _info(-2.0, 100)}
    pool = {"300390": {"name": "天华新能", "price": 90.0}}
    out = _assemble_holdings(info, pool, {}, date(2026, 3, 9))
    h = out["holdings"][0]
    assert h["float_pnl"] == round((90.0 - (-2.0)) * 100, 2)   # 9200.0
    assert h["float_pnl_pct"] is None


def test_missing_price_yields_none_market_value():
    info = {"600519": _info(1600.0, 100)}
    out = _assemble_holdings(info, {}, {}, date(2026, 3, 9))   # 池里没这只 → 无现价
    h = out["holdings"][0]
    assert h["price"] is None
    assert h["market_value"] is None
    assert h["float_pnl"] is None
    assert h["name"] == "600519"          # 无名字兜底成代码


def test_holding_days_fallback_to_trading_calendar_when_no_round():
    info = {"600000": _info(10.0, 100, buy_date="2026-03-02")}
    pool = {"600000": {"name": "X", "price": 11.0}}
    out = _assemble_holdings(info, pool, {}, date(2026, 3, 9))   # 无回合 → 交易日历兜底
    # 2026-03-02(周一) 之后到 2026-03-09(周一): 周二三四五(4) + 下周一(1) = 5 个交易日
    assert out["holdings"][0]["holding_days"] == 5


def test_sorted_by_market_value_desc():
    info = {"A": _info(10.0, 100), "B": _info(10.0, 300)}
    pool = {"A": {"name": "A", "price": 10.0}, "B": {"name": "B", "price": 10.0}}
    out = _assemble_holdings(info, pool, {}, date(2026, 3, 9))
    assert [h["code"] for h in out["holdings"]] == ["B", "A"]   # B 市值 3000 > A 1000


def test_empty_board_name_becomes_none():
    info = {"A": _info(10.0, 100)}
    pool = {"A": {"name": "A", "price": 10.0, "board_name": ""}}   # 非持仓票默认空串
    out = _assemble_holdings(info, pool, {}, date(2026, 3, 9))
    assert out["holdings"][0]["board_name"] is None


def test_trading_days_between():
    assert trading_days_between("2026-03-02", "2026-03-02") == 0     # 同日
    assert trading_days_between("2026-03-09", "2026-03-02") == 0     # 逆序
    assert trading_days_between("2026-03-02", "2026-03-06") == 4     # 周一→周五
    assert trading_days_between("2026-03-06", "2026-03-09") == 1     # 周五→周一(跳周末)
    assert trading_days_between(date(2026, 3, 2), date(2026, 3, 9)) == 5
