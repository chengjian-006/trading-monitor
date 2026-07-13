"""东财兜底熔断 (0713 行情停更 47 分钟的护栏).

背景: 同花顺分时主源大面积 502 → 231 只票逐只回落东财兜底 → 东财封了 prod 出口 IP,
必失败且是慢失败 → 48 分钟空耗 558 次, 塞满连接池, 3s 实时行情刷新每轮超预算被中止(461次),
全表行情冻在 10:12。熔断保证: 连续失败到阈值就停手, 不再逐只空耗。
"""
from backend.fetcher import intraday


def _reset():
    intraday._em_fail_streak = 0
    intraday._em_open_until = 0.0


def test_breaker_opens_after_threshold():
    _reset()
    for _ in range(intraday.EM_FAIL_STREAK_MAX - 1):
        intraday._em_record(False, "000001", "boom")
        assert not intraday._em_blocked()      # 未达阈值前不熔断
    intraday._em_record(False, "000001", "boom")
    assert intraday._em_blocked()              # 达阈值 → 熔断, 后续直接跳过东财


def test_success_resets_breaker():
    _reset()
    for _ in range(intraday.EM_FAIL_STREAK_MAX - 1):
        intraday._em_record(False, "000001", "boom")
    intraday._em_record(True, "000001")        # 中途成功 → 连败清零
    assert intraday._em_fail_streak == 0
    for _ in range(intraday.EM_FAIL_STREAK_MAX - 1):
        intraday._em_record(False, "000001", "boom")
    assert not intraday._em_blocked()          # 计数从头起, 没到阈值


def test_breaker_recloses_when_probe_fails_after_cooldown(monkeypatch):
    """冷却到期放行探路, 探路又挂 → 必须重新熔断。

    回归: 若用 `streak == MAX` 判定熔断, 探路失败时 streak 已越过阈值, 条件永不再成立,
    熔断就再也合不上 → 退化回逐只空耗的老毛病。
    """
    _reset()
    t = [1000.0]
    monkeypatch.setattr(intraday._time, "monotonic", lambda: t[0])
    for _ in range(intraday.EM_FAIL_STREAK_MAX):
        intraday._em_record(False, "000001", "boom")
    assert intraday._em_blocked()

    t[0] += intraday.EM_COOLDOWN_SEC + 1       # 冷却到期 → 放行一只探路
    assert not intraday._em_blocked()

    intraday._em_record(False, "000001", "boom")   # 探路又挂
    assert intraday._em_blocked()                  # → 重新熔断, 不再逐只空耗


def test_breaker_reopens_then_recovers(monkeypatch):
    """冷却到期探路成功 → 熔断解除, 恢复正常兜底。"""
    _reset()
    t = [1000.0]
    monkeypatch.setattr(intraday._time, "monotonic", lambda: t[0])
    for _ in range(intraday.EM_FAIL_STREAK_MAX):
        intraday._em_record(False, "000001", "boom")
    assert intraday._em_blocked()

    t[0] += intraday.EM_COOLDOWN_SEC + 1
    intraday._em_record(True, "000001")        # 探路成功(东财恢复/换IP)
    assert not intraday._em_blocked()
    assert intraday._em_fail_streak == 0


def test_intraday_client_is_isolated_from_main_pool():
    """分时用独立 client, 不复用主池 — 分时再堵也堵不到 3s 实时行情。"""
    from backend.fetcher.http_client import _get_client
    assert intraday._get_intraday_client() is not _get_client()
    assert intraday._get_intraday_client() is intraday._get_intraday_client()   # 同进程复用一个
