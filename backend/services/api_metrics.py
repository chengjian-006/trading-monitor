"""真实业务调用的滚动窗口成功率统计 (v1.7.71)

取代 api_health 的模拟探活, 监控反映真实业务可用性:
  - 业务能用就显示绿, 业务真挂才显示红
  - 探活流量被东财风控的误报问题彻底消失

数据来源:
  - data_fetcher.TrackedAsyncClient 的每次 get/post 自动 record
  - ai_analyst 的 akshare 调用显式 record

按 (source, usage) 维度统计最近 WINDOW_SEC 秒内:
  total / ok / fail / 中位数耗时 / 最近错误 / 最近调用时间
"""
import time
from collections import deque
from threading import Lock

WINDOW_SEC = 300  # 5 分钟滚动窗口

# {(source, usage): deque([(ts, ok, latency_ms, error), ...])}
_records: dict[tuple[str, str], deque] = {}
_lock = Lock()


def record(source: str, usage: str, ok: bool, latency_ms: int = 0, error: str = ""):
    """业务调用成功/失败时打点。线程安全。"""
    if not source or not usage:
        return
    key = (source, usage)
    now = time.time()
    err = (error or "")[:200]
    with _lock:
        dq = _records.setdefault(key, deque())
        dq.append((now, bool(ok), int(latency_ms), err))
        cutoff = now - WINDOW_SEC
        while dq and dq[0][0] < cutoff:
            dq.popleft()


def get_stats() -> dict[tuple[str, str], dict]:
    """返回 {(source, usage): {total, ok, success_rate, p50_latency_ms, last_error, last_ts}}.

    last_ts 是该 bucket 最近一次调用的 epoch 秒, 用于判断"窗口内有调用"还是"长时间无调用"。
    """
    out: dict[tuple[str, str], dict] = {}
    now = time.time()
    cutoff = now - WINDOW_SEC
    with _lock:
        for key, dq in list(_records.items()):
            while dq and dq[0][0] < cutoff:
                dq.popleft()
            entries = list(dq)
            total = len(entries)
            if total == 0:
                continue
            ok_count = sum(1 for e in entries if e[1])
            ok_latencies = sorted(e[2] for e in entries if e[1])
            last_error = next((e[3] for e in reversed(entries) if not e[1] and e[3]), "")
            last_ts = entries[-1][0]
            p50 = ok_latencies[len(ok_latencies) // 2] if ok_latencies else 0
            out[key] = {
                "total": total,
                "ok": ok_count,
                "fail": total - ok_count,
                "success_rate": ok_count / total,
                "p50_latency_ms": p50,
                "last_error": last_error,
                "last_ts": last_ts,
            }
    return out


def reset():
    """清空所有计数 (用于测试)。"""
    with _lock:
        _records.clear()
