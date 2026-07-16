"""问财观点上报接口单测 (最小链路).

直调 async 端点 + monkeypatch, 不起 app、不打网、不连库。
覆盖: ingest token 守门 / 从投顾话术里撞出个股(6位代码 + 全名命中) / 主推排序 / 落库参数。
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

    async def fake_insert(user_id, question, answer_text, stocks, agent_mode, trace_id):
        cap.update(user_id=user_id, question=question, answer_text=answer_text,
                   stocks=stocks, agent_mode=agent_mode, trace_id=trace_id)
        return 42
    monkeypatch.setattr(wc.repository, "insert_wencai_opinion", fake_insert)
    return cap


def _req(**kw):
    base = dict(token="SECRET", question="给我推荐一只股票",
                answer_text="", trace_id="tid1", agent_mode="normal")
    base.update(kw)
    return wc.OpinionIngestRequest(**base)


def test_opinion_token_guard(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(wc.ingest_opinion(_req(token="WRONG")))
    assert ei.value.status_code == 401


def test_opinion_empty_question(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(wc.ingest_opinion(_req(question="   ")))
    assert ei.value.status_code == 400


def test_extract_by_name_and_primary(monkeypatch):
    """话术里反复提「浪潮信息」→ 命中且标 primary; 顺带提到的茅台也识别但非主推。"""
    cap = _setup(monkeypatch)
    answer = ("综合看，**浪潮信息**当前处于买入区间。浪潮信息受益于算力需求，"
              "相比贵州茅台这类防御票更适合短线。建议浪潮信息回踩买入。")
    r = asyncio.run(wc.ingest_opinion(_req(answer_text=answer)))
    assert r["ok"] is True and r["id"] == 42
    codes = {s["code"]: s for s in cap["stocks"]}
    assert "000977" in codes and "600519" in codes
    assert codes["000977"]["primary"] is True     # 提及3次, 主推
    assert codes["600519"]["primary"] is False
    assert cap["user_id"] == 0                     # 观点默认全局


def test_extract_by_code(monkeypatch):
    """话术里带 6 位代码也能撞出。"""
    cap = _setup(monkeypatch)
    r = asyncio.run(wc.ingest_opinion(_req(answer_text="关注长川科技(300604)的低吸机会。")))
    codes = {s["code"] for s in cap["stocks"]}
    assert "300604" in codes
    assert r["stock_count"] >= 1


def test_extract_none_when_no_match(monkeypatch):
    """纯观点没提具体票 → 空列表, 仍落库。"""
    cap = _setup(monkeypatch)
    r = asyncio.run(wc.ingest_opinion(_req(answer_text="当前市场情绪偏弱，建议轻仓观望。")))
    assert r["stock_count"] == 0
    assert cap["stocks"] == []
