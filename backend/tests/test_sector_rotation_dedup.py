"""板块弱转强推送去重(重启安全) + 弱转强失败提醒 — 0703整改回归测试.

背景: _pushed 去重集合原本只在进程内存, 部署重启即丢 → 同一「弱转强·启动」
当日推了3次(机器人 13:41/13:58/14:04, 对应三次部署重启)。
修复: 每3分钟落库的快照本就带当日 transitions 流水, 重启后首轮回读补种。
新增: 已广播启动的题材回落到 冷/退潮 → 补推一条「弱转强·失败」(每题材当日一次)。
"""
from unittest.mock import AsyncMock

from backend.services import sector_rotation_scanner as srs


def _reset_state():
    srs._intraday.clear()
    srs._state.clear()
    srs._pushed.clear()
    srs._transitions.clear()
    srs._yest_baseline.clear()
    srs._seeded.clear()


def _patch_env(monkeypatch, *, boards, yest_baseline, stored_transitions=None):
    """替换掉全部外部依赖, 模拟一轮 scan。返回 enqueue mock。"""
    _reset_state()
    monkeypatch.setattr(srs, "is_workday", lambda now=None: True)
    monkeypatch.setattr(srs, "get_limit_pool_cached",
                        AsyncMock(return_value={"boards": boards}))
    monkeypatch.setattr(srs, "_load_yest_baseline", AsyncMock(return_value=yest_baseline))
    enqueue = AsyncMock()
    monkeypatch.setattr(srs.alert_throttle, "enqueue", enqueue)
    row = {"rotation_data": {"transitions": stored_transitions or []}}
    monkeypatch.setattr(srs.repository, "get_sector_rotation",
                        AsyncMock(return_value=row if stored_transitions else None))
    monkeypatch.setattr(srs.repository, "upsert_sector_rotation", AsyncMock())
    monkeypatch.setattr(srs.repository, "list_all_stocks", AsyncMock(return_value=[]))
    return enqueue


def _boards(theme: str, n: int):
    return [{"reason": theme, "height": 2, "open_times": 0, "name": f"股{i}", "code": f"00000{i}"}
            for i in range(n)]


def _fake_time(monkeypatch, hhmm: str = "13:41"):
    from datetime import datetime as real_dt

    class _FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 7, 3, int(hhmm[:2]), int(hhmm[3:]))

    monkeypatch.setattr(srs, "datetime", _FakeDT)


class TestRestartSafeDedup:
    async def test_first_trigger_pushes_once(self, monkeypatch):
        _fake_time(monkeypatch)
        enqueue = _patch_env(monkeypatch, boards=_boards("机器人", 7),
                             yest_baseline={"机器人": 3})
        await srs.scan_sector_rotation()
        calls = [c for c in enqueue.await_args_list if c[0][0] == "SECTOR_WEAK_TO_STRONG"]
        assert len(calls) == 1

    async def test_same_process_second_scan_no_repush(self, monkeypatch):
        _fake_time(monkeypatch)
        enqueue = _patch_env(monkeypatch, boards=_boards("机器人", 7),
                             yest_baseline={"机器人": 3})
        await srs.scan_sector_rotation()
        await srs.scan_sector_rotation()
        calls = [c for c in enqueue.await_args_list if c[0][0] == "SECTOR_WEAK_TO_STRONG"]
        assert len(calls) == 1

    async def test_restart_seeds_from_db_no_repush(self, monkeypatch):
        # 模拟重启: 内存清空, 但DB快照里已有当日"机器人 weak_to_strong"流水 → 不再重推
        _fake_time(monkeypatch, "13:58")
        stored = [{"at": "13:41", "direction": "weak_to_strong", "theme": "机器人",
                   "limit_up": 7, "yest": 3, "slope": 2, "max_height": 2,
                   "broken": 0, "samples": ["天安新材"]}]
        enqueue = _patch_env(monkeypatch, boards=_boards("机器人", 7),
                             yest_baseline={"机器人": 3}, stored_transitions=stored)
        await srs.scan_sector_rotation()
        calls = [c for c in enqueue.await_args_list if c[0][0] == "SECTOR_WEAK_TO_STRONG"]
        assert len(calls) == 0


class TestWtsFailedFollowup:
    async def test_fallback_after_start_pushes_failed_once(self, monkeypatch):
        # 早先已推启动(7家), 现回落到3家(≤昨) → 状态"冷" → 推一条失败提醒; 再扫不重复
        _fake_time(monkeypatch, "14:30")
        stored = [{"at": "13:41", "direction": "weak_to_strong", "theme": "机器人",
                   "limit_up": 7, "yest": 3, "slope": 2, "max_height": 2,
                   "broken": 0, "samples": ["天安新材"]}]
        enqueue = _patch_env(monkeypatch, boards=_boards("机器人", 3),
                             yest_baseline={"机器人": 3}, stored_transitions=stored)
        await srs.scan_sector_rotation()
        await srs.scan_sector_rotation()
        failed = [c for c in enqueue.await_args_list if c[0][0] == "SECTOR_WTS_FAILED"]
        assert len(failed) == 1
        assert failed[0][0][1]["theme"] == "机器人"

    async def test_no_failed_push_without_prior_start(self, monkeypatch):
        # 没推过启动的题材回落, 不发失败提醒
        _fake_time(monkeypatch, "14:30")
        enqueue = _patch_env(monkeypatch, boards=_boards("机器人", 1),
                             yest_baseline={"机器人": 3})
        await srs.scan_sector_rotation()
        failed = [c for c in enqueue.await_args_list if c[0][0] == "SECTOR_WTS_FAILED"]
        assert len(failed) == 0

    async def test_still_strong_no_failed_push(self, monkeypatch):
        # 已推启动且仍然7家(启动状态维持) → 不发失败提醒
        _fake_time(monkeypatch, "14:30")
        stored = [{"at": "13:41", "direction": "weak_to_strong", "theme": "机器人",
                   "limit_up": 7, "yest": 3, "slope": 2, "max_height": 2,
                   "broken": 0, "samples": ["天安新材"]}]
        enqueue = _patch_env(monkeypatch, boards=_boards("机器人", 7),
                             yest_baseline={"机器人": 3}, stored_transitions=stored)
        await srs.scan_sector_rotation()
        failed = [c for c in enqueue.await_args_list if c[0][0] == "SECTOR_WTS_FAILED"]
        assert len(failed) == 0
