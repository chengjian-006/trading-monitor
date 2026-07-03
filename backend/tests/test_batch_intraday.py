"""批量分时 sparkline 拉取 — 0703「页面加载很慢」整改回归测试.

实测根因: 冷缓存时一次请求对50只逐只拉上游(同花顺慢+东财被封两次重试),
单请求140s+; 且收盘后分时已冻结仍按30s TTL整晚重拉。
修复: 请求预算8s部分返回+后台续拉写缓存 / 飞行中去重 / 盘后TTL放长1h / 东财兜底砍成1次。
"""
import asyncio
import time

from backend.fetcher import intraday as it


def _reset():
    it._batch_intraday_cache.clear()
    it._inflight.clear()
    it._fetch_sem = None


class TestBudgetPartialReturn:
    async def test_slow_code_excluded_then_backfilled(self, monkeypatch):
        _reset()
        monkeypatch.setattr(it, "_BATCH_FETCH_BUDGET", 0.1)

        async def fake_ths(code):
            if code == "000002":
                await asyncio.sleep(0.3)   # 超预算
            return [{"time": "09:30", "price": 1.0, "volume": 10}], 1.0

        monkeypatch.setattr(it, "_intraday_ths", fake_ths)
        r1 = await it.get_batch_intraday_sparkline(["000001", "000002"])
        assert "000001" in r1 and r1["000001"]["trends"]
        assert "000002" not in r1          # 预算内没完成 → 本轮不带

        await asyncio.sleep(0.4)           # 后台任务完成并写缓存
        r2 = await it.get_batch_intraday_sparkline(["000002"])
        assert "000002" in r2 and r2["000002"]["trends"]   # 下轮轮询命中缓存补全


class TestInflightDedup:
    async def test_concurrent_requests_fetch_once_per_code(self, monkeypatch):
        _reset()
        calls: list[str] = []

        async def fake_ths(code):
            calls.append(code)
            await asyncio.sleep(0.1)
            return [{"time": "09:30", "price": 1.0, "volume": 10}], 1.0

        monkeypatch.setattr(it, "_intraday_ths", fake_ths)
        await asyncio.gather(
            it.get_batch_intraday_sparkline(["600000"]),
            it.get_batch_intraday_sparkline(["600000"]),
        )
        assert calls.count("600000") == 1   # 同code飞行中只拉一份


class TestResponseSlimming:
    def test_downsample_and_drop_volume(self):
        from backend.routers.kline import _slim_sparkline
        trends = [{"time": f"09:{i:02d}", "price": float(i), "volume": 100.0} for i in range(240)]
        out = _slim_sparkline({"pre_close": 10.0, "trends": trends})
        assert out["pre_close"] == 10.0
        assert 78 <= len(out["trends"]) <= 82          # 240点 → ~80点
        assert "volume" not in out["trends"][0]         # 前端不用, 砍掉
        assert out["trends"][-1]["price"] == 239.0      # 末点(最新价)必保留

    def test_short_series_kept_full(self):
        from backend.routers.kline import _slim_sparkline
        trends = [{"time": f"09:{i:02d}", "price": float(i)} for i in range(30)]
        out = _slim_sparkline({"pre_close": 1.0, "trends": trends})
        assert len(out["trends"]) == 30                 # 早盘点少, 不降采样


class TestClosedMarketTTL:
    async def test_stale_entry_served_when_market_closed(self, monkeypatch):
        _reset()
        monkeypatch.setattr("backend.core.trading_calendar.is_trading_time", lambda: False)
        it._batch_intraday_cache["300001"] = {
            "pre_close": 9.9,
            "trends": [{"time": "09:30", "price": 10.0}],
            "_fetched_at": time.time() - 300,   # 5分钟前, 盘中口径已过期
        }

        async def boom(code):
            raise AssertionError("盘后不应重拉上游")

        monkeypatch.setattr(it, "_intraday_ths", boom)
        r = await it.get_batch_intraday_sparkline(["300001"])
        assert r["300001"]["pre_close"] == 9.9

    async def test_stale_entry_refetched_when_trading(self, monkeypatch):
        _reset()
        monkeypatch.setattr("backend.core.trading_calendar.is_trading_time", lambda: True)
        it._batch_intraday_cache["300001"] = {
            "pre_close": 9.9,
            "trends": [{"time": "09:30", "price": 10.0}],
            "_fetched_at": time.time() - 300,
        }

        async def fresh(code):
            return [{"time": "09:31", "price": 10.5, "volume": 1}], 9.9

        monkeypatch.setattr(it, "_intraday_ths", fresh)
        r = await it.get_batch_intraday_sparkline(["300001"])
        assert r["300001"]["trends"][0]["time"] == "09:31"   # 盘中30s口径, 已重拉
