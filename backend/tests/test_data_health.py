"""数据源健康度主动预警测试 — 0612误报普查第3项整改.

源挂掉当天就推预警, 而不是靠误报/几天后普查暴露。核心约束:
1. 每类事件每天最多推一次(防刷屏: 冻结回放每30s检测一轮会反复上报)。
2. 达到该类阈值才推(盘中日K缺今日bar偶发1-2次是个股源抖动, 不算源挂)。
3. 跨天自动重置(昨天推过不影响今天再推)。
"""
from backend.services import data_health


class TestReportAndDrain:
    def setup_method(self):
        data_health.reset_for_test()

    def test_below_threshold_no_alert(self):
        # kline_network_down 阈值5: 偶发几次是个股网络抖动不算源挂
        for _ in range(3):
            data_health.report("kline_network_down", today="2026-06-12", now_hhmm="10:05")
        assert data_health.drain_alerts(today="2026-06-12") == []

    def test_threshold_reached_alerts_once(self):
        data_health.report("index_trends_frozen", detail="科创指数行情停在10:00",
                           today="2026-06-12", now_hhmm="10:31")
        lines = data_health.drain_alerts(today="2026-06-12")
        assert len(lines) == 1 and "大盘分时行情" in lines[0] and "科创指数行情停在10:00" in lines[0]

    def test_recovered_status_in_message(self):
        # 末次异常 10:31, flush 时已 10:35(超3分钟没再犯) → 文案带"已恢复正常"
        data_health.report("index_trends_frozen", today="2026-06-12", now_hhmm="10:31")
        lines = data_health.drain_alerts(today="2026-06-12", now_hhmm="10:35")
        assert len(lines) == 1 and "已恢复正常" in lines[0]

    def test_not_recovered_status_in_message(self):
        # 末次异常就在1分钟前 → 还不能说恢复
        data_health.report("index_trends_frozen", today="2026-06-12", now_hhmm="10:34")
        lines = data_health.drain_alerts(today="2026-06-12", now_hhmm="10:35")
        assert len(lines) == 1 and "还没恢复" in lines[0]

    def test_same_day_no_repeat(self):
        data_health.report("market_stats_empty", today="2026-06-12", now_hhmm="09:35")
        assert len(data_health.drain_alerts(today="2026-06-12")) == 1
        # 同日继续上报, 不再重复推
        data_health.report("market_stats_empty", today="2026-06-12", now_hhmm="10:35")
        assert data_health.drain_alerts(today="2026-06-12") == []

    def test_next_day_resets(self):
        data_health.report("market_stats_empty", today="2026-06-12", now_hhmm="09:35")
        assert len(data_health.drain_alerts(today="2026-06-12")) == 1
        data_health.report("market_stats_empty", today="2026-06-13", now_hhmm="09:35")
        assert len(data_health.drain_alerts(today="2026-06-13")) == 1

    def test_count_and_time_window_in_message(self):
        for hhmm in ("10:05", "10:06", "10:07", "10:08", "10:09",
                     "10:10", "10:11", "10:12", "10:13", "10:14"):
            data_health.report("kline_network_down", today="2026-06-12", now_hhmm=hhmm)
        lines = data_health.drain_alerts(today="2026-06-12")
        assert len(lines) == 1
        assert "10次" in lines[0] and "10:05" in lines[0] and "10:14" in lines[0]

    def test_multiple_kinds_one_drain(self):
        data_health.report("index_trends_frozen", today="2026-06-12", now_hhmm="10:31")
        data_health.report("market_stats_empty", today="2026-06-12", now_hhmm="10:32")
        assert len(data_health.drain_alerts(today="2026-06-12")) == 2

    def test_unknown_kind_ignored(self):
        data_health.report("no_such_kind", today="2026-06-12", now_hhmm="10:00")
        assert data_health.drain_alerts(today="2026-06-12") == []

    def test_stale_event_from_yesterday_not_alerted_today(self):
        data_health.report("index_trends_frozen", today="2026-06-11", now_hhmm="14:00")
        assert data_health.drain_alerts(today="2026-06-12") == []

    def test_open_rollover_frozen_suppressed(self):
        # 冻结全部落在 09:32 之前(开盘头一分多钟源 rollover 残留昨日末点), 过了开盘就新鲜 → 静默不推
        for hhmm in ("09:30", "09:30", "09:31"):
            data_health.report("index_trends_frozen", detail="深证成指行情停在15:00",
                               today="2026-06-23", now_hhmm=hhmm)
        assert data_health.drain_alerts(today="2026-06-23") == []

    def test_frozen_past_open_grace_alerts(self):
        # 过了 09:32 仍在冻 = 真降级, 照常预警(末次冻结越过宽限)
        for hhmm in ("09:30", "09:31", "09:35"):
            data_health.report("index_trends_frozen", detail="深证成指行情停在15:00",
                               today="2026-06-23", now_hhmm=hhmm)
        lines = data_health.drain_alerts(today="2026-06-23")
        assert len(lines) == 1 and "大盘分时行情" in lines[0]

    def test_open_grace_only_applies_to_index_trends(self):
        # 全市场快照无数据不吃开盘宽限: 09:31 出现即推(阈值1, 非 rollover 类)
        data_health.report("market_stats_empty", today="2026-06-23", now_hhmm="09:31")
        assert len(data_health.drain_alerts(today="2026-06-23")) == 1

    def test_open_grace_boundary_0932_alerts(self):
        # 边界: 末次冻结正好 09:32 算"过了开盘", 照常预警(09:32之前=严格早于)
        data_health.report("index_trends_frozen", today="2026-06-23", now_hhmm="09:32")
        assert len(data_health.drain_alerts(today="2026-06-23")) == 1


class TestStockCodeFilterForKlineHealth:
    """日K源健康埋点只统计个股 — 板块指数(BK*)/指数三源拉不到是预期, 不该计数(防0617那类44次假预警)。"""

    def test_individual_stock_codes_counted(self):
        from backend.fetcher.klines import _is_individual_stock
        for code in ("600519", "000001", "300750", "688981", "830799"):
            assert _is_individual_stock(code), code

    def test_board_index_codes_excluded(self):
        from backend.fetcher.klines import _is_individual_stock
        for code in ("BK1201", "BK0427", "bk1339", "sh000001", "sz399006", "", "60051"):
            assert not _is_individual_stock(code), code


class TestRedAlertTemplate:
    """真·源故障走红色告警模版, 与默认蓝色"盘面播报"区分, 显著标识。"""

    def setup_method(self):
        data_health.reset_for_test()

    async def test_flush_uses_red_template_and_warning_title(self, monkeypatch):
        from unittest.mock import AsyncMock
        from backend.services import notifier
        sent = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "send_dual", sent)
        for _ in range(5):   # 达到 kline_network_down 阈值
            data_health.report("kline_network_down", detail="最近: 600519")
        await data_health.flush_data_health()
        assert sent.await_count == 1
        _, kwargs = sent.await_args
        assert kwargs.get("template") == "red"
        assert "数据源健康预警" in kwargs.get("lark_title", "")

    async def test_flush_noop_when_no_alert(self, monkeypatch):
        from unittest.mock import AsyncMock
        from backend.services import notifier
        sent = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "send_dual", sent)
        await data_health.flush_data_health()
        assert sent.await_count == 0
