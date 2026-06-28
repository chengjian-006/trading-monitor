"""alert_throttle 节流 + 合并行为测试.

测试时间通过 monkeypatch datetime.now 或直接操控 buffer.last_push_at 来模拟。
notifier.send_wechat_text 用 AsyncMock 替换避免真发推送。
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from backend.services import alert_throttle as at


@pytest.fixture(autouse=True)
def reset_throttle_state():
    """每个测试前后清空 alert_throttle 全局状态, 防止互相污染。"""
    at._buffers.clear()
    at._mergers.clear()
    at._throttles.clear()
    yield
    at._buffers.clear()
    at._mergers.clear()
    at._throttles.clear()


@pytest.fixture
def mock_notifier(monkeypatch):
    """替换 notifier 的 send 函数, 让 alert_throttle 内部 import 用 mock 版."""
    import backend.services.notifier as notifier
    text_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(notifier, "send_wechat_text", text_mock)
    return text_mock


class TestEnqueueFirstTimeFlushesImmediately:
    async def test_single_item_first_enqueue_flushes(self, mock_notifier):
        text_mock = mock_notifier

        def merger(items):
            return f"got {len(items)} items"

        at.register("T1", merger, throttle_seconds=60)
        await at.enqueue("T1", {"id": 1})

        text_mock.assert_awaited_once()
        assert text_mock.call_args.args[0] == "got 1 items"

    async def test_second_within_throttle_does_not_flush(self, mock_notifier):
        text_mock = mock_notifier

        def merger(items):
            return f"got {len(items)}"

        at.register("T1", merger, throttle_seconds=900)
        await at.enqueue("T1", {"id": 1})
        await at.enqueue("T1", {"id": 2})
        # 第二条进缓冲, 没到节流时间, 不应再 flush
        assert text_mock.await_count == 1
        # 缓冲里应该还有 id=2 等待
        assert len(at._buffers["T1"].items) == 1


class TestFlushAllAfterThrottleExpired:
    async def test_flush_all_drains_due_buffers(self, mock_notifier):
        text_mock = mock_notifier

        def merger(items):
            return f"got {len(items)}"

        at.register("T1", merger, throttle_seconds=900)
        # 第一次入队 → flush (text_mock 调用 1 次)
        await at.enqueue("T1", {"id": 1})
        # 立即追加 2 条, 在节流期内 → 不 flush
        await at.enqueue("T1", {"id": 2})
        await at.enqueue("T1", {"id": 3})
        assert text_mock.await_count == 1

        # 把 last_push_at 调到 1 小时前, 模拟节流期过
        at._buffers["T1"].last_push_at = datetime.now() - timedelta(hours=1)
        await at.flush_all()

        assert text_mock.await_count == 2
        assert text_mock.call_args.args[0] == "got 2"


class TestMultipleAlertTypesIndependent:
    async def test_two_types_have_independent_buffers(self, mock_notifier):
        text_mock = mock_notifier

        def m1(items):
            return f"T1:{len(items)}"

        def m2(items):
            return f"T2:{len(items)}"

        at.register("T1", m1, throttle_seconds=900)
        at.register("T2", m2, throttle_seconds=900)
        await at.enqueue("T1", {"a": 1})
        await at.enqueue("T2", {"b": 1})

        # 两次首推都立即发出
        assert text_mock.await_count == 2
        texts = [c.args[0] for c in text_mock.call_args_list]
        assert "T1:1" in texts
        assert "T2:1" in texts


class TestEmptyMergerSkipsPush:
    async def test_merger_returns_empty_string_skips_send(self, mock_notifier):
        text_mock = mock_notifier

        def merger(items):
            return ""

        at.register("T1", merger, throttle_seconds=60)
        await at.enqueue("T1", {"x": 1})

        text_mock.assert_not_awaited()


class TestBufferStats:
    async def test_get_buffer_stats_returns_pending_and_throttle(self):
        def m(items):
            return ""

        at.register("T1", m, throttle_seconds=300)
        at._buffers["T1"].items.append({"x": 1})
        stats = at.get_buffer_stats()
        assert "T1" in stats
        assert stats["T1"]["pending"] == 1
        assert stats["T1"]["throttle_seconds"] == 300
