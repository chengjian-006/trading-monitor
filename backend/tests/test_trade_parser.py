"""交割单解析护栏: 成交金额必须 ≈ 成交价 × 成交数量, 否则拒收(防列错位/单位错乱脏数据)。

背景: 2026-06-07 一次导入整行列错位, "成交金额"列的值落进"成交价"列, 写入 40 行
price=金额 的脏数据, 污染 FIFO 成本导致假浮亏止损。此护栏在解析层拦截这类行。
"""
from backend.services.trade_analyzer import parse_trades_text

HEADER = "成交日期\t成交时间\t证券代码\t证券名称\t操作\t成交数量\t成交均价\t成交金额"


def _text(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def test_consistent_row_is_parsed():
    """正常行: 金额 = 价 × 量 → 正常解析。"""
    row = "20260518\t09:30:00\t300604\t长川科技\t证券买入\t100\t211.46\t21146"
    trades = parse_trades_text(_text(row))
    assert len(trades) == 1
    assert abs(trades[0]["price"] - 211.46) < 1e-6
    assert trades[0]["quantity"] == 100


def test_misaligned_amount_in_price_col_is_rejected():
    """列错位脏行: 金额(21146)落进价格列、金额列是乱码 → 金额≠价×量 → 拒收。"""
    dirty = "20260518\t09:30:00\t300604\t长川科技\t证券买入\t100\t21146\t211849639"
    trades = parse_trades_text(_text(dirty))
    assert trades == []


def test_clean_and_dirty_mixed_only_clean_kept():
    """混合: 只保留金额自洽的行。"""
    clean = "20260518\t09:30:00\t300604\t长川科技\t证券买入\t100\t211.46\t21146"
    dirty = "20260519\t09:30:00\t300604\t长川科技\t证券卖出\t100\t22402\t215381881"
    trades = parse_trades_text(_text(clean, dirty))
    assert len(trades) == 1
    assert abs(trades[0]["price"] - 211.46) < 1e-6
