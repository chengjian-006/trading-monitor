"""卖点撤销 merger 测试.

覆盖:
  - 单股单子信号: 完整原预警引用块
  - 单股多子信号: 多个子信号联名 (短线卖一、卖二)
  - 多股汇总: 顶部摘要 + 每股分段
"""
from backend.services.scanner import _merge_sell_revoke


def _mk(code, name, signal_name, original_price=100.0, new_price=99.0,
        recovery_pct=-1.0, original_detail="", original_time=""):
    return {
        "code": code,
        "name": name,
        "signal_id": signal_name,
        "signal_name": signal_name,
        "original_price": original_price,
        "new_price": new_price,
        "recovery_pct": recovery_pct,
        "original_detail": original_detail,
        "original_time": original_time,
    }


class TestMergeSellRevoke:
    def test_empty_returns_empty_string(self):
        assert _merge_sell_revoke([]) == ""

    def test_single_stock_single_signal_keeps_quote_block(self):
        items = [_mk("300390", "天华新能", "短线卖二",
                     original_price=88.98, new_price=88.33, recovery_pct=-0.73,
                     original_detail="持仓股跌破MA10 ≥2% | close 88.98 ≤ MA10(91.33) × 0.98",
                     original_time="09:38")]
        text = _merge_sell_revoke(items)
        assert "【卖点撤销】▲ 短线卖二 已恢复" in text
        assert "天华新能(300390)" in text
        assert "88.98 → 现 88.33" in text
        # 单子信号保留完整原预警引用块
        assert "┌─ 原预警 · 09:38 ─" in text
        assert "│ 持仓股跌破MA10" in text
        assert "└─" in text

    def test_single_stock_multiple_signals_uses_per_signal_blocks(self):
        items = [
            _mk("300390", "天华新能", "短线卖一", original_time="09:38",
                original_detail="MA5 跌破"),
            _mk("300390", "天华新能", "短线卖二", original_time="09:38",
                original_detail="MA10 跌破"),
        ]
        text = _merge_sell_revoke(items)
        # 标题里所有信号名联名
        assert "短线卖一、短线卖二" in text
        # 每子信号一段引用块
        assert text.count("┌─") == 2
        assert "│ MA5 跌破" in text
        assert "│ MA10 跌破" in text
        # 注意: "原预警" 头是单子信号特有, 多子信号每段头是 "{signal_name} · {time}"
        assert "原预警" not in text

    def test_multiple_stocks_have_summary_header(self):
        items = [
            _mk("300390", "天华新能", "短线卖一"),
            _mk("002230", "科大讯飞", "短线卖二"),
        ]
        text = _merge_sell_revoke(items)
        # 多股: 顶部加汇总
        assert "【卖点撤销 · 汇总】 近15分钟 2 只个股 / 2 条已恢复" in text
        # 分隔符
        assert "──────────" in text
        # 两只票都出现
        assert "天华新能" in text
        assert "科大讯飞" in text

    def test_signal_name_dedup_preserves_order(self):
        # 同股同名两条 (理论上不应发生但保险) → 标题去重
        items = [
            _mk("300390", "天华新能", "短线卖一", original_time="09:38"),
            _mk("300390", "天华新能", "短线卖一", original_time="09:54"),
        ]
        text = _merge_sell_revoke(items)
        # 标题里 "短线卖一" 只出现一次 (dict.fromkeys 去重保序)
        lines = text.split("\n")
        title = lines[0]
        assert title.count("短线卖一") == 1
