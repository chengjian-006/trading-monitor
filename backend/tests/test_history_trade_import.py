"""历史成交导入解析护栏: 平安证券「历史成交」格式(无成交日期列/按笔明细)。

与交割单差异: ①无「成交日期」列, 日期由外部(前端日期选择器)注入; ②按笔成交明细,
同一委托(同合同编号)拆多行。沿用金额自洽护栏(金额≈价×量, 防列错位脏数据)。
"""
from datetime import date

from backend.services.trade_analyzer import parse_history_text

HEADER = "成交时间\t证券代码\t证券名称\t操作\t成交数量\t成交均价\t成交金额\t合同编号\t成交编号\t委托时间"
D = date(2026, 6, 16)


def _text(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def test_normal_rows_parsed():
    """京东方三行: 数量/价/金额/方向 正确解析。"""
    rows = [
        "14:55:15\t000725\t京东方Ａ\t证券卖出\t756\t6.090\t4604.040\t0212877912\t0101000089154126\t14:55:15",
        "14:15:50\t000725\t京东方Ａ\t证券买入\t2500\t6.110\t15275.000\t0215408923\t0101000076612694\t14:15:50",
    ]
    trades = parse_history_text(_text(*rows), D)
    assert len(trades) == 2
    assert trades[0]["direction"] == "sell"
    assert trades[0]["quantity"] == 756
    assert abs(trades[0]["price"] - 6.09) < 1e-6
    assert trades[1]["direction"] == "buy"
    assert trades[1]["quantity"] == 2500


def test_trade_date_injected_from_param():
    """行内无日期列 → trade_date 取自传入参数。"""
    row = "14:55:15\t000725\t京东方Ａ\t证券卖出\t756\t6.090\t4604.040\t0212877912\t0101000089154126\t14:55:15"
    trades = parse_history_text(_text(row), D)
    assert trades[0]["trade_date"] == D
    assert trades[0]["trade_time"] == "14:55:15"
    assert trades[0]["code"] == "000725"


def test_misaligned_amount_rejected():
    """金额≠价×量的脏行 → 拒收(沿用交割单护栏)。"""
    dirty = "14:55:15\t000725\t京东方Ａ\t证券卖出\t756\t4604.040\t999999\t0212877912\t0101000089154126\t14:55:15"
    trades = parse_history_text(_text(dirty), D)
    assert trades == []


def test_same_order_multiple_fills_all_kept():
    """同一委托(同合同编号)拆成的多笔(756+1744 同14:55:15)都保留, 不被误去重。"""
    rows = [
        "14:55:15\t000725\t京东方Ａ\t证券卖出\t756\t6.090\t4604.040\t0212877912\t0101000089154126\t14:55:15",
        "14:55:15\t000725\t京东方Ａ\t证券卖出\t1744\t6.090\t10620.960\t0212877912\t0101000089154124\t14:55:15",
    ]
    trades = parse_history_text(_text(*rows), D)
    assert len(trades) == 2
    assert {t["quantity"] for t in trades} == {756, 1744}


def test_no_header_returns_empty():
    """无表头(缺必需列) → 返回空, 不崩。"""
    row = "14:55:15\t000725\t京东方Ａ\t证券卖出\t756\t6.090\t4604.040"
    assert parse_history_text(row, D) == []


def test_non_trade_op_skipped():
    """操作列非买卖(表头残留/异常行) → 跳过。"""
    rows = [
        "14:55:15\t000725\t京东方Ａ\t证券卖出\t756\t6.090\t4604.040\t0212877912\t0101000089154126\t14:55:15",
        "时间\t代码\t名称\t操作\t100\t1.0\t100\tx\ty\tz",
    ]
    trades = parse_history_text(_text(*rows), D)
    assert len(trades) == 1
