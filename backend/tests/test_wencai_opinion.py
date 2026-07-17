"""问财观点上报接口单测 (最小链路).

直调 async 端点 + monkeypatch, 不起 app、不打网、不连库。
覆盖: 无 token 也放行(已去鉴权) / 从投顾话术里撞出个股(6位代码 + 全名命中) / 主推排序 / 落库参数。
"""
import asyncio

import pytest
from fastapi import HTTPException

from backend.routers import wencai as wc

# 模拟全市场名称字典(含会被话术提及的 + 干扰项)
_NAMES = [
    {"code": "000977", "name": "浪潮信息"},
    {"code": "603001", "name": "奥康国际"},
    {"code": "300604", "name": "长川科技"},
    {"code": "600519", "name": "贵州茅台"},
]


def _setup(monkeypatch, token="SECRET"):
    monkeypatch.setattr(wc, "load_config", lambda: {"wencai_screening": {"ingest_token": token}})

    async def fake_all_names():
        return _NAMES
    monkeypatch.setattr(wc.repository, "all_stock_names", fake_all_names)

    cap = {}

    async def fake_insert(user_id, question, answer_text, stocks, agent_mode, trace_id,
                          uploader="", reasoning="", conclusion=None):
        cap.update(user_id=user_id, question=question, answer_text=answer_text,
                   stocks=stocks, agent_mode=agent_mode, trace_id=trace_id, uploader=uploader,
                   reasoning=reasoning, conclusion=conclusion)
        return 42
    monkeypatch.setattr(wc.repository, "insert_wencai_opinion", fake_insert)
    return cap


def _req(**kw):
    base = dict(token="SECRET", question="给我推荐一只股票",
                answer_text="", trace_id="tid1", agent_mode="normal")
    base.update(kw)
    return wc.OpinionIngestRequest(**base)


class _FakeReq:
    """最小 Request 桩: 供 IP 限流取 client.host / headers(v1.7.653 H4)。"""
    class _Client:
        host = "127.0.0.1"
    client = _Client()
    headers: dict = {}


def _ingest(req):
    """统一直调: 补 request 桩参数(限流阈值远高于单测调用量, 不会触发 429)。"""
    return asyncio.run(wc.ingest_opinion(req, _FakeReq()))


def test_opinion_no_token_required(monkeypatch):
    """观点上报不做 token 鉴权(2026-07-16 拍板): 空/错 token 都放行。"""
    cap = _setup(monkeypatch)
    r = _ingest(_req(token=""))
    assert r["ok"] is True
    r2 = _ingest(_req(token="WRONG"))
    assert r2["ok"] is True
    assert cap["user_id"] == 0


def test_opinion_empty_question(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _ingest(_req(question="   "))
    assert ei.value.status_code == 400


def test_extract_by_name_and_primary(monkeypatch):
    """话术里反复提「浪潮信息」→ 命中且标 primary; 顺带提到的茅台也识别但非主推。"""
    cap = _setup(monkeypatch)
    answer = ("综合看，**浪潮信息**当前处于买入区间。浪潮信息受益于算力需求，"
              "相比贵州茅台这类防御票更适合短线。建议浪潮信息回踩买入。")
    r = _ingest(_req(answer_text=answer))
    assert r["ok"] is True and r["id"] == 42
    codes = {s["code"]: s for s in cap["stocks"]}
    assert "000977" in codes and "600519" in codes
    assert codes["000977"]["primary"] is True     # 提及3次, 主推
    assert codes["600519"]["primary"] is False
    assert cap["user_id"] == 0                     # 观点默认全局


def test_extract_by_code(monkeypatch):
    """话术里带 6 位代码也能撞出。"""
    cap = _setup(monkeypatch)
    r = _ingest(_req(answer_text="关注长川科技(300604)的低吸机会。"))
    codes = {s["code"] for s in cap["stocks"]}
    assert "300604" in codes
    assert r["stock_count"] >= 1


def test_extract_none_when_no_match(monkeypatch):
    """纯观点没提具体票 → 空列表, 仍落库。"""
    cap = _setup(monkeypatch)
    r = _ingest(_req(answer_text="当前市场情绪偏弱，建议轻仓观望。"))
    assert r["stock_count"] == 0
    assert cap["stocks"] == []


def test_only_with_stock_skips_when_no_match(monkeypatch):
    """only_with_stock=True 且没抽出个股 → 跳过入库(不调 insert)。"""
    cap = _setup(monkeypatch)
    cap["stocks"] = "SENTINEL"   # 若被写入会被覆盖, 用哨兵确认 insert 未被调用
    r = _ingest(_req(answer_text="轻仓观望，无具体标的。", only_with_stock=True))
    assert r.get("skipped") is True and r["stock_count"] == 0
    assert cap["stocks"] == "SENTINEL"   # insert 没被调用


def test_only_with_stock_inserts_when_matched(monkeypatch):
    """only_with_stock=True 但抽出了个股 → 正常入库。"""
    cap = _setup(monkeypatch)
    r = _ingest(_req(answer_text="**浪潮信息** 值得关注。", only_with_stock=True))
    assert r["ok"] is True and r.get("skipped") is None
    assert any(s["code"] == "000977" for s in cap["stocks"])
