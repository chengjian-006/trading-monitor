"""胜率重算「断点续算」行为锁定 (v1.7.x).

事故背景: 服务高频重启杀掉 21:00 的 6h 长任务, 胜率表静默停写 9 天(推送战绩冻在旧值)。
修复: 每票算完落暂存, 全票齐才聚合写正式表; 被杀重启从断点续算。本测试用内存替身锁定:
  1. 部分完成 → 不写正式表(save_model_winrate 不调用), 返回 partial
  2. 全部完成 → 写正式表 + 清暂存
  3. 续算跳过已落暂存的票(不重算)
全程不碰真实 DB。
"""
import pytest

from backend.services import model_winrate_refresher as mwr
from backend.models import repository


class _FakeStage:
    """内存暂存替身: 模拟 cfzy_sys_model_winrate_stage 的落/查/清。"""

    def __init__(self):
        self.rows: dict[tuple[str, str], list] = {}   # (anchor, code) -> trades
        self.fail_codes: set[str] = set()             # 这些票 stage 时抛错(模拟被打断/失败)

    async def stage(self, anchor, code, trades):
        if code in self.fail_codes:
            raise RuntimeError("模拟落暂存失败")
        self.rows[(anchor, code)] = trades

    async def codes(self, anchor):
        return {c for (a, c) in self.rows if a == anchor}

    async def count(self, anchor):
        return sum(1 for (a, _) in self.rows if a == anchor)

    async def load(self, anchor):
        return [{"code": c, "trades_json": _json(t)} for (a, c), t in self.rows.items() if a == anchor]

    async def clear(self, anchor=None, exclude_anchor=None):
        if anchor is not None:
            self.rows = {k: v for k, v in self.rows.items() if k[0] != anchor}
        elif exclude_anchor is not None:
            self.rows = {k: v for k, v in self.rows.items() if k[0] == exclude_anchor}


def _json(t):
    import json
    return json.dumps(t, ensure_ascii=False)


@pytest.fixture
def wired(monkeypatch):
    """把 refresher 的 DB/加载依赖全换成内存替身; load_daily_one=None → 每票 0 交易(快)。"""
    stage = _FakeStage()
    saved = {}

    async def fake_fetchall(sql, args=None):
        return [{"d": "2026-07-17"}]            # 锚点

    async def fake_universe(_):
        return ["000001", "000002", "600000"]   # 3 只真股票

    async def fake_load_daily_one(code):
        return None                             # → trades=[]

    monkeypatch.setattr(mwr, "_fetchall", fake_fetchall)
    monkeypatch.setattr(mwr, "universe_codes", fake_universe)
    monkeypatch.setattr(mwr, "load_daily_one", fake_load_daily_one)
    monkeypatch.setattr(repository, "clear_model_winrate_stage", stage.clear)
    monkeypatch.setattr(repository, "staged_model_winrate_codes", stage.codes)
    monkeypatch.setattr(repository, "stage_model_winrate_code", stage.stage)
    monkeypatch.setattr(repository, "staged_model_winrate_count", stage.count)
    monkeypatch.setattr(repository, "load_model_winrate_stage", stage.load)

    async def fake_save(anchor, rows):
        saved["anchor"] = anchor
        saved["rows"] = rows
        return len(rows)

    monkeypatch.setattr(repository, "save_model_winrate", fake_save)
    return stage, saved


async def test_full_run_writes_and_clears(wired):
    """全票齐 → 写正式表(锚点正确) + 清空暂存。"""
    stage, saved = wired
    r = await mwr.refresh_model_winrate(force=True)
    assert r and not r.get("partial")
    assert r["as_of"] == "2026-07-17"
    assert saved["anchor"] == "2026-07-17"
    assert await stage.count("2026-07-17") == 0        # 定稿后暂存已清


async def test_partial_run_does_not_write(wired):
    """一只票落暂存失败 → 覆盖不全 → 不写正式表, 返回 partial。"""
    stage, saved = wired
    stage.fail_codes = {"600000"}
    r = await mwr.refresh_model_winrate(force=True)
    assert r and r.get("partial") is True
    assert r["staged"] == 2 and r["total"] == 3
    assert "anchor" not in saved                        # save_model_winrate 未被调用


async def test_resume_skips_already_staged(wired, monkeypatch):
    """已落暂存的票不再重算(load_daily_one 不该被这些票调用)。"""
    stage, saved = wired
    stage.rows[("2026-07-17", "000001")] = []           # 预置: 000001 已算
    stage.rows[("2026-07-17", "000002")] = []
    called = []

    async def spy_load(code):
        called.append(code)
        return None

    monkeypatch.setattr(mwr, "load_daily_one", spy_load)
    r = await mwr.refresh_model_winrate(force=True)
    assert r and not r.get("partial")
    assert called == ["600000"]                         # 只算了没落暂存的那只
