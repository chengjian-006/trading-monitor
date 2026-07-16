"""风暴聚合窗口 storm_aggregator 行为测试 (机制一, 基线第五节聚合卡)。

覆盖: ≥3 条合并聚合卡 / <3 条到期原样逐发(参数不丢) / 窗口边界与结算幂等 /
发送失败重排不丢件 / 重试上限 / 开关关闭直通 / 全员免打扰不聚合 / 并发入队 /
notifier.send_wechat_signal 拦截点接线。

定时器(call_later 90s)在测试中不等真到期: 直接调 settle(family, seq) 或
把 due_at 拨到过去后走 flush_expired, 与生产结算路径同代码。
"""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from backend.services import storm_aggregator as sa


@pytest.fixture(autouse=True)
def reset_storm_state(monkeypatch):
    """每个测试前后清空窗口缓冲 + 固定配置(开, 90s), 防互相污染/防读真实 config.json。"""
    sa._windows.clear()
    monkeypatch.setattr(sa, "_load_settings", lambda: (True, 90.0))
    yield
    sa._windows.clear()


@pytest.fixture
def mock_send_card(monkeypatch):
    """替换 notifier.send_card(聚合卡出口), 记录收到的 Card。"""
    import backend.services.notifier as notifier
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifier, "send_card", mock)
    return mock


def _params(code="600001", name="测试股", signal="主升浪回踩MA10·止损",
            pct=-3.2, **extra) -> dict:
    p = dict(code=code, name=name, signal_name=signal, direction="sell",
             price=10.5, detail="d", user_id=1, strategy="", pct_change=pct,
             model_stats=None, signal_id="SELL_X", username="u", mute_lark=False)
    p.update(extra)
    return p


class TestAggregateWhenThresholdMet:
    async def test_three_items_merge_into_one_aggregate_card(self, mock_send_card, monkeypatch):
        # 归因固定, 断言卡内容确定性
        monkeypatch.setattr(sa, "_build_cause",
                            AsyncMock(return_value=("3 只持仓/自选同窗口触发", "")))
        sends = [AsyncMock(return_value=True) for _ in range(3)]
        for i, s in enumerate(sends):
            assert await sa.intercept("exit", _params(code=f"60000{i}", name=f"股{i}"), send=s)
        assert len(sa._windows["exit"].items) == 3

        await sa.settle("exit", sa._windows["exit"].seq)

        # 只发一张聚合卡, 单卡回调一个都不调, 缓冲清空
        mock_send_card.assert_awaited_once()
        for s in sends:
            s.assert_not_awaited()
        assert len(sa._windows["exit"].items) == 0
        card = mock_send_card.call_args.args[0]
        assert "自选3只" in card.title and "集中触发" in card.title
        assert card.template == "orange"          # 风暴聚合统一风险色
        for i in range(3):
            assert f"股{i}" in card.fallback
        # 逐发用的止损短名进了表格行
        table_md = card.elements[1]["content"]
        assert "止损" in table_md and "股0" in table_md

    async def test_aggregate_card_fold_keeps_long_values(self, mock_send_card, monkeypatch):
        monkeypatch.setattr(sa, "_build_cause", AsyncMock(return_value=("x", "")))
        for i in range(3):
            await sa.intercept("exit", _params(code=f"00000{i}", name=f"票{i}", pct=-2.0))
        # 逐发路径不应被走到 → 兜住 notifier 直发函数
        import backend.services.notifier as notifier
        direct = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "_send_wechat_signal_direct", direct)

        await sa.settle("exit", sa._windows["exit"].seq)
        direct.assert_not_awaited()
        card = mock_send_card.call_args.args[0]
        # 现价(长值)下沉折叠区
        fold = card.elements[-1]
        assert "¥10.50" in str(fold)


class TestFewerThanThresholdSendsIndividually:
    async def test_two_items_sent_one_by_one_with_full_params(self, mock_send_card, monkeypatch):
        import backend.services.notifier as notifier
        direct = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "_send_wechat_signal_direct", direct)

        p1 = _params(code="600001", name="甲", strategy="低吸", detail="明细A")
        p2 = _params(code="600002", name="乙", signal="缩量突破·清剩半", pct=-1.1)
        await sa.intercept("exit", p1)
        await sa.intercept("exit", p2)

        await sa.settle("exit", sa._windows["exit"].seq)

        mock_send_card.assert_not_awaited()       # 不合并
        assert direct.await_count == 2            # 逐张发出, 不丢件
        sent_kwargs = [c.kwargs for c in direct.call_args_list]
        assert p1 in sent_kwargs and p2 in sent_kwargs   # 全部参数原样留存
        assert len(sa._windows["exit"].items) == 0

    async def test_single_item_passes_through_untouched(self, mock_send_card):
        send = AsyncMock(return_value=True)
        await sa.intercept("exit", _params(), send=send)
        await sa.settle("exit", sa._windows["exit"].seq)
        send.assert_awaited_once()
        mock_send_card.assert_not_awaited()


class TestWindowBoundaryIdempotency:
    async def test_stale_seq_settle_is_noop(self, mock_send_card):
        send1 = AsyncMock(return_value=True)
        await sa.intercept("exit", _params(code="1"), send=send1)
        seq1 = sa._windows["exit"].seq
        await sa.settle("exit", seq1)
        assert send1.await_count == 1

        # 新窗口开张后, 旧 seq 的迟到定时器再触发 = 幂等无操作, 不动新窗口的件
        send2 = AsyncMock(return_value=True)
        await sa.intercept("exit", _params(code="2"), send=send2)
        seq2 = sa._windows["exit"].seq
        assert seq2 != seq1
        await sa.settle("exit", seq1)             # 迟到的旧定时器
        send2.assert_not_awaited()
        assert len(sa._windows["exit"].items) == 1

        await sa.settle("exit", seq2)
        send2.assert_awaited_once()

    async def test_double_settle_same_seq_sends_once(self, mock_send_card):
        send = AsyncMock(return_value=True)
        await sa.intercept("exit", _params(), send=send)
        seq = sa._windows["exit"].seq
        await sa.settle("exit", seq)
        await sa.settle("exit", seq)              # 定时器+周期flush 双触发
        assert send.await_count == 1

    async def test_families_do_not_cross(self, mock_send_card):
        s_exit = AsyncMock(return_value=True)
        s_risk = AsyncMock(return_value=True)
        await sa.intercept("exit", _params(code="1"), send=s_exit)
        await sa.intercept("risk", _params(code="2"), send=s_risk)
        await sa.settle("exit", sa._windows["exit"].seq)
        s_exit.assert_awaited_once()
        s_risk.assert_not_awaited()               # 风险族窗口未到期不受影响
        assert len(sa._windows["risk"].items) == 1


class TestFlushExpired:
    async def test_flush_expired_settles_due_window_only(self, mock_send_card):
        due_send = AsyncMock(return_value=True)
        fresh_send = AsyncMock(return_value=True)
        await sa.intercept("exit", _params(code="1"), send=due_send)
        await sa.intercept("risk", _params(code="2"), send=fresh_send)
        sa._windows["exit"].due_at = time.monotonic() - 1   # exit 到期, risk 还没到

        await sa.flush_expired()

        due_send.assert_awaited_once()
        fresh_send.assert_not_awaited()
        assert len(sa._windows["risk"].items) == 1


class TestRequeueOnFailure:
    async def test_aggregate_failure_requeues_all_items(self, monkeypatch):
        import backend.services.notifier as notifier
        monkeypatch.setattr(sa, "_build_cause", AsyncMock(return_value=("x", "")))
        fail_card = AsyncMock(return_value=False)
        monkeypatch.setattr(notifier, "send_card", fail_card)
        for i in range(3):
            await sa.intercept("exit", _params(code=str(i)))
        seq = sa._windows["exit"].seq

        await sa.settle("exit", seq)

        # 全渠道失败 → 3 条放回缓冲开新窗口, 不静默丢件
        assert fail_card.await_count == 1
        assert len(sa._windows["exit"].items) == 3
        assert sa._windows["exit"].seq != seq

        # 渠道恢复 → 到期重试成功, 缓冲清空
        ok_card = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "send_card", ok_card)
        sa._windows["exit"].due_at = time.monotonic() - 1
        await sa.flush_expired()
        ok_card.assert_awaited_once()
        assert len(sa._windows["exit"].items) == 0

    async def test_individual_failure_requeues_only_failed(self, mock_send_card):
        ok = AsyncMock(return_value=True)
        bad = AsyncMock(return_value=False)
        await sa.intercept("exit", _params(code="ok"), send=ok)
        await sa.intercept("exit", _params(code="bad"), send=bad)
        await sa.settle("exit", sa._windows["exit"].seq)
        ok.assert_awaited_once()
        # 失败的那条留在缓冲等下轮, 成功的不重复
        assert len(sa._windows["exit"].items) == 1
        assert sa._windows["exit"].items[0]["params"]["code"] == "bad"

    async def test_retry_cap_drops_item(self, mock_send_card):
        bad = AsyncMock(return_value=False)
        await sa.intercept("exit", _params(), send=bad)
        sa._windows["exit"].items[0]["retries"] = sa.MAX_RETRIES   # 已到上限
        await sa.settle("exit", sa._windows["exit"].seq)
        assert len(sa._windows["exit"].items) == 0                 # 超限丢弃, 不再无限空转

    async def test_send_exception_treated_as_failure(self, mock_send_card):
        boom = AsyncMock(side_effect=RuntimeError("boom"))
        await sa.intercept("exit", _params(), send=boom)
        await sa.settle("exit", sa._windows["exit"].seq)
        assert len(sa._windows["exit"].items) == 1                 # 异常≠丢件


class TestSwitchAndScope:
    async def test_disabled_switch_passes_through(self, monkeypatch):
        monkeypatch.setattr(sa, "_load_settings", lambda: (False, 90.0))
        assert not await sa.intercept("exit", _params())
        assert len(sa._windows["exit"].items) == 0

    async def test_non_aggregate_family_passes_through(self):
        assert not await sa.intercept("opportunity", _params(direction="buy"))
        assert "opportunity" not in sa._windows or not sa._windows["opportunity"].items

    async def test_all_muted_items_sent_individually_not_aggregated(self, mock_send_card):
        """今日免打扰(只静音飞书)命中全员时不聚合: 聚合卡通道没有 mute_lark 语义,
        逐发路径能正确尊重静音偏好。"""
        sends = [AsyncMock(return_value=True) for _ in range(3)]
        for i, s in enumerate(sends):
            await sa.intercept("exit", _params(code=str(i), mute_lark=True), send=s)
        await sa.settle("exit", sa._windows["exit"].seq)
        mock_send_card.assert_not_awaited()
        for s in sends:
            s.assert_awaited_once()


class TestConcurrentEnqueue:
    async def test_parallel_intercepts_land_in_one_window(self, mock_send_card, monkeypatch):
        monkeypatch.setattr(sa, "_build_cause", AsyncMock(return_value=("x", "")))
        await asyncio.gather(*[
            sa.intercept("exit", _params(code=f"c{i}")) for i in range(5)
        ])
        win = sa._windows["exit"]
        assert len(win.items) == 5                # 并发入队无丢件
        seq = win.seq
        await sa.settle("exit", seq)
        mock_send_card.assert_awaited_once()      # 同窗 5 条只出一张聚合卡
        assert "自选5只" in mock_send_card.call_args.args[0].title


class TestNotifierInterceptionWiring:
    """send_wechat_signal 拦截点: 闸门全过后 sell/reduce 进缓冲; buy/plunge 直通。"""

    @pytest.fixture
    def wired(self, monkeypatch):
        import backend.services.notifier as notifier
        monkeypatch.setattr(notifier, "is_production", AsyncMock(return_value=True))
        direct = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "_send_wechat_signal_direct", direct)
        intercept = AsyncMock(return_value=True)
        monkeypatch.setattr(sa, "intercept", intercept)
        return notifier, direct, intercept

    async def test_sell_signal_goes_to_buffer(self, wired):
        notifier, direct, intercept = wired
        ok = await notifier.send_wechat_signal("600001", "甲", "模型·止损", "sell", 9.9, "d")
        assert ok is True
        intercept.assert_awaited_once()
        family, params = intercept.call_args.args[0], intercept.call_args.args[1]
        assert family == "exit"
        assert params["code"] == "600001" and params["signal_name"] == "模型·止损"
        assert params["direction"] == "sell" and params["price"] == 9.9
        direct.assert_not_awaited()               # 聚合器接管, 不直发

    async def test_reduce_signal_goes_to_buffer(self, wired):
        notifier, direct, intercept = wired
        await notifier.send_wechat_signal("600002", "乙", "模型·止盈减半", "reduce", 8.8, "d")
        intercept.assert_awaited_once()
        direct.assert_not_awaited()

    async def test_buy_and_plunge_bypass_buffer(self, wired):
        notifier, direct, intercept = wired
        await notifier.send_wechat_signal("600003", "丙", "缩量突破", "buy", 7.7, "d")
        await notifier.send_wechat_signal("000001", "上证指数", "大盘急跌", "plunge", 3400.0, "d")
        intercept.assert_not_awaited()            # 机会族/大盘急跌不进缓冲
        assert direct.await_count == 2

    async def test_intercept_declined_falls_back_to_direct(self, wired):
        """开关关闭(intercept 返回 False)→ 直通原路径, 一条不丢。"""
        notifier, direct, intercept = wired
        intercept.return_value = False
        ok = await notifier.send_wechat_signal("600004", "丁", "模型·止损", "sell", 6.6, "d")
        assert ok is True
        direct.assert_awaited_once()

    async def test_intercept_exception_falls_back_to_direct(self, wired):
        """拦截层抛错也不能弄丢推送 → 改直发。"""
        notifier, direct, intercept = wired
        intercept.side_effect = RuntimeError("boom")
        ok = await notifier.send_wechat_signal("600005", "戊", "模型·止损", "sell", 5.5, "d")
        assert ok is True
        direct.assert_awaited_once()


class TestShortSignalName:
    def test_model_dot_action_takes_action_segment(self):
        assert sa._short_signal("主升浪回踩MA10·止损") == "止损"
        assert sa._short_signal("缩量突破·止盈减半") == "止盈减半"

    def test_plain_name_truncated(self):
        assert sa._short_signal("回踩10MA缩量后突破昨高") == "回踩10MA缩量"
        assert sa._short_signal("") == ""
