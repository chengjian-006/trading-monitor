# -*- coding: utf-8 -*-
"""次新股龙头闸 (v1.7.704) — 纯函数, 不连库.

背景: SECTOR_CAPITAL_INFLOW 要求"龙头涨停"作为板块资金回流的证据。但 N/C 开头的
次新股在标识期内**无涨跌幅限制**(首日可涨100%+), "涨停"这个条件对它天然恒真 ——
于是任何板块只要当天有新股上市, 就可能被误判成资金回流。而新股暴涨反映的是打新
情绪, 不是板块资金回流。0719 复测实测: 近一月 25 条信号里 3 条(12%)龙头是 N 开头。
"""
import pytest

from backend.utils.limit_calc import get_limit_pct, is_at_limit_up, is_new_listing


@pytest.mark.parametrize("name", ["N臻宝", "N惠科", "N托伦斯", "n小写也算"])
def test_first_day_new_listing(name):
    """N 开头 = 上市首日。"""
    assert is_new_listing(name) is True


@pytest.mark.parametrize("name", ["C华虹", "C思看"])
def test_second_to_fifth_day(name):
    """C 开头 = 上市第 2~5 日, 同样无涨跌幅限制。"""
    assert is_new_listing(name) is True


@pytest.mark.parametrize("name", [
    "东方锆业", "中材科技", "乐山电力", "福石控股",   # 0719 实测中的真实龙头
    "TCL中环",                                      # 含大写字母但非 N/C 开头
    "ST中安", "*ST海投",                             # ST 不是次新股
    "", None,
])
def test_normal_stocks_not_flagged(name):
    """正常个股不能被误判为次新股 —— 误伤会让资金回流预警整体失灵。"""
    assert is_new_listing(name) is False


def test_names_starting_with_c_or_n_chinese_not_flagged():
    """中文名首字是「长」「南」这类不该命中(只认 ASCII 的 N/C 前缀)。"""
    for name in ("长城汽车", "南大光电", "宁德时代", "成都银行"):
        assert is_new_listing(name) is False


def test_gate_is_independent_of_limit_pct():
    """次新股闸与板幅判定是两件事: 次新股即便按主板板幅算"涨停"了, 也要被挡掉。"""
    assert get_limit_pct("301999", "N臻宝") == 20.0
    assert is_at_limit_up("301999", 20.0, "N臻宝") is True   # 板幅判定照常成立
    assert is_new_listing("N臻宝") is True                    # 但次新股闸独立拦下


def test_real_case_from_0719_audit():
    """0719 复测的三个真实漏网案例, 修复后必须全部被拦。"""
    for name in ("N臻宝", "N惠科", "N托伦斯"):
        assert is_new_listing(name), f"{name} 应被次新股闸拦下"
