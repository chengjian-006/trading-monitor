"""交割单导入去重护栏: 同一笔成交在交割单(真实成交日)与历史成交(手选注入日)两种格式里
trade_date 可能不一致, 唯一键含 trade_date 拦不住 → filter_new_records 按日期无关指纹
(code+方向+量+价+trade_time) + 日期窗口判重, 防重复导入虚增持仓(0628 麦捷科技幽灵持仓事故)。
"""
from datetime import date

from backend.models.repo.trades import filter_new_records


def _t(code, direction, qty, price, ttime, tdate):
    return {"code": code, "direction": direction, "quantity": qty,
            "price": price, "trade_time": ttime, "trade_date": tdate}


def test_same_fill_shifted_date_is_deduped():
    """同一笔成交, trade_date 差 7 天(6-18↔6-25, 实测漂移), 应判为重复跳过。"""
    existing = [_t("300319", "sell", 700, 27.590, "13:09:10", date(2026, 6, 18))]
    incoming = [_t("300319", "sell", 700, 27.590, "13:09:10", date(2026, 6, 25))]
    assert filter_new_records(incoming, existing) == []


def test_string_date_and_decimal_price_normalized():
    """existing 来自库(date / Decimal样式), incoming 字符串日期 → 归一后仍判重。"""
    existing = [_t("300568", "buy", 900, 19.090, "13:19:39", date(2026, 6, 23))]
    incoming = [_t("300568", "buy", 900, 19.09, "13:19:39", "2026-06-25")]
    assert filter_new_records(incoming, existing) == []


def test_distinct_fills_kept():
    """量/价/时间任一不同 = 不同成交, 全部保留。"""
    existing = [_t("300319", "buy", 700, 21.819, "09:52:51", date(2026, 6, 16))]
    incoming = [
        _t("300319", "buy", 300, 22.637, "14:07:05", date(2026, 6, 17)),   # 不同量价时
        _t("300319", "sell", 700, 21.819, "09:52:51", date(2026, 6, 16)),  # 方向不同
    ]
    assert len(filter_new_records(incoming, existing)) == 2


def test_same_fingerprint_outside_window_kept():
    """指纹相同但成交日相距超窗口(>30天) → 视作另一笔真实成交, 保留。"""
    existing = [_t("600000", "buy", 100, 10.000, "09:30:00", date(2026, 1, 1))]
    incoming = [_t("600000", "buy", 100, 10.000, "09:30:00", date(2026, 3, 1))]
    assert len(filter_new_records(incoming, existing)) == 1


def test_dedup_within_same_batch():
    """同一批 records 内部重复(无 existing)也去重, 只留一条。"""
    incoming = [
        _t("002648", "buy", 1000, 25.850, "10:02:51", date(2026, 6, 24)),
        _t("002648", "buy", 1000, 25.850, "10:02:51", date(2026, 6, 25)),  # 同一笔, 日期漂移
    ]
    assert len(filter_new_records(incoming, [])) == 1


def test_empty_inputs():
    assert filter_new_records([], []) == []
