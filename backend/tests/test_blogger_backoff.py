"""博主扫描 IP 限流退避单测 (v1.7.x).

验证: 拉取失败→设指数退避; 退避窗内跳过不发请求; 到期后重试且退避翻倍; 封顶2h; 成功清零。
注入假时钟 + 桩 fetch/notifier/config, 不连库不打网。
"""
import asyncio

from backend.services import blogger_post_scanner as bps
from backend.fetcher.ths_blogger import BloggerFetchError


def _reset():
    bps._last_scan = 0.0
    bps._fetch_fail_count = 0
    bps._backoff_until = 0.0
    bps._fail_alerted = False
    bps._last_fail_alert_at = 0.0


def _setup(monkeypatch, fetch):
    clock = {"t": 10000.0}
    monkeypatch.setattr(bps.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(bps.time, "time", lambda: clock["t"])
    monkeypatch.setattr(bps, "_get_interval_seconds", lambda: 0)   # 去掉时段节流干扰
    monkeypatch.setattr(bps, "load_config",
                        lambda: {"blogger_tracking": {"enabled": True, "blogger_name": "x", "user_code": "u"}})
    monkeypatch.setattr(bps, "fetch_blogger_posts", fetch)
    import backend.services.notifier as notifier

    async def noop(*a, **k):
        return True
    monkeypatch.setattr(notifier, "send_dual", noop)
    return clock


def test_fail_sets_backoff_and_skips_within_window(monkeypatch):
    _reset()
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise BloggerFetchError("HTTP 403 Nginx forbidden")
    clock = _setup(monkeypatch, boom)

    # 第1次失败 → 退避 5min, fetch 被调 1 次
    asyncio.run(bps.scan_blogger_posts())
    assert calls["n"] == 1 and bps._fetch_fail_count == 1
    assert bps._backoff_until == 10000.0 + bps.BACKOFF_BASE

    # 退避窗内(+100s)再调 → 跳过, 不再发请求
    clock["t"] = 10000.0 + 100
    asyncio.run(bps.scan_blogger_posts())
    assert calls["n"] == 1

    # 退避到期(+301s)再调 → 第2次失败, 退避翻倍到 10min
    clock["t"] = 10000.0 + 301
    asyncio.run(bps.scan_blogger_posts())
    assert calls["n"] == 2 and bps._fetch_fail_count == 2
    assert bps._backoff_until == (10000.0 + 301) + bps.BACKOFF_BASE * 2


def test_backoff_caps_at_2h(monkeypatch):
    _reset()

    async def boom():
        raise BloggerFetchError("HTTP 403")
    clock = _setup(monkeypatch, boom)
    bps._fetch_fail_count = 10        # 已连续失败很多次
    asyncio.run(bps.scan_blogger_posts())
    # backoff = min(300*2^10, 7200) = 7200 封顶
    assert bps._backoff_until == 10000.0 + bps.BACKOFF_CAP


def test_success_clears_backoff(monkeypatch):
    _reset()

    async def ok():
        return []                      # 成功但无新帖
    clock = _setup(monkeypatch, ok)
    bps._fetch_fail_count = 3
    bps._backoff_until = 9000.0       # 已到期(< 当前时钟10000), 退避门放行 → 跑到成功路径
    asyncio.run(bps.scan_blogger_posts())
    assert bps._fetch_fail_count == 0 and bps._backoff_until == 0.0
