"""出口IP探测缓存测试 — 防"一次网络抖动→推送哑火至重启"隐患整改.

原 get_outbound_ip 把失败结果("unknown")缓存到进程退出, 重启后首次探测若失败则
is_production 永远 False、全渠道推送静默跳过直到下次重启。改为: 成功才永久缓存,
失败只冷却 _OUTBOUND_FAIL_TTL 秒, 之后自动重探, 故障恢复无需重启。
"""
import pytest

from backend.core import config as cfg


def _reset():
    cfg._outbound_ip_cache = None
    cfg._outbound_ip_retry_at = 0.0


@pytest.mark.asyncio
async def test_success_caches_permanently(monkeypatch):
    _reset()
    calls = {"n": 0}

    async def fake_probe():
        calls["n"] += 1
        return "124.71.75.5"

    monkeypatch.setattr(cfg, "_probe_outbound_ip", fake_probe)
    assert await cfg.get_outbound_ip() == "124.71.75.5"
    assert await cfg.get_outbound_ip() == "124.71.75.5"
    assert calls["n"] == 1  # 第二次走缓存, 不重探


@pytest.mark.asyncio
async def test_failure_not_cached_permanently(monkeypatch):
    _reset()

    async def fail_probe():
        return None

    monkeypatch.setattr(cfg, "_probe_outbound_ip", fail_probe)
    assert await cfg.get_outbound_ip() == "unknown"
    # 关键: 失败没把 unknown 永久写进缓存
    assert cfg._outbound_ip_cache is None


@pytest.mark.asyncio
async def test_recovers_after_failure_without_restart(monkeypatch):
    _reset()

    async def fail_probe():
        return None

    monkeypatch.setattr(cfg, "_probe_outbound_ip", fail_probe)
    assert await cfg.get_outbound_ip() == "unknown"

    # 模拟冷却窗已过 + 网络恢复 → 自动重探拿到真实IP, 无需重启
    cfg._outbound_ip_retry_at = 0.0

    async def ok_probe():
        return "124.71.75.5"

    monkeypatch.setattr(cfg, "_probe_outbound_ip", ok_probe)
    assert await cfg.get_outbound_ip() == "124.71.75.5"
    assert await cfg.is_production() is True


@pytest.mark.asyncio
async def test_failure_cooldown_skips_reprobe(monkeypatch):
    _reset()
    calls = {"n": 0}

    async def fail_probe():
        calls["n"] += 1
        return None

    monkeypatch.setattr(cfg, "_probe_outbound_ip", fail_probe)
    assert await cfg.get_outbound_ip() == "unknown"
    # 冷却窗内再次调用不重探(防故障期每次推送都卡探测)
    assert await cfg.get_outbound_ip() == "unknown"
    assert calls["n"] == 1
