"""锁定 model_winrate_refresher 两个修复点 (v1.7.x):
  Bug A: today_str 必须取全市场最大交易日, 不能取 ORDER BY code,trade_date 后 rows[-1]
         (排最后的指数码 980030 停在 06-12 会毒化锚点 → run_date/窗口全错)。
  Bug B(口径): 回测 universe 只留真股票(00/30/60/68), 剔除 98x/88x 指数码 + 北交所。
"""
from backend.services.model_winrate_refresher import _anchor_date, _is_stock


def test_anchor_date_takes_global_max_not_last_code():
    # rows 按 (code, trade_date) 升序: 真股票排前(到06-18), 指数码 980030 排最后(停06-12)
    rows = [
        {"code": "600000", "trade_date": "2026-06-17"},
        {"code": "600000", "trade_date": "2026-06-18"},
        {"code": "688981", "trade_date": "2026-06-18"},
        {"code": "980030", "trade_date": "2026-06-11"},
        {"code": "980030", "trade_date": "2026-06-12"},   # rows[-1] = 旧逻辑误取的 06-12
    ]
    assert _anchor_date(rows) == "2026-06-18"


def test_anchor_date_handles_datetime_and_date_objects():
    import datetime as dt
    rows = [
        {"code": "000001", "trade_date": dt.date(2026, 6, 18)},
        {"code": "980030", "trade_date": dt.datetime(2026, 6, 12, 0, 0)},
    ]
    assert _anchor_date(rows) == "2026-06-18"


def test_is_stock_keeps_real_a_shares():
    for code in ("600000", "000001", "300750", "688981", "601318", "002594"):
        assert _is_stock(code) is True


def test_is_stock_excludes_index_board_and_bj():
    for code in ("980030",   # 中证消费电子指数
                 "881124",   # 板块/行业指数
                 "886033",   # 概念指数
                 "lc9999",   # 碳酸锂主连
                 "830799",   # 北交所
                 "430139",   # 北交所
                 "920099"):  # 北交所新段
        assert _is_stock(code) is False
