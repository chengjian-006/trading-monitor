"""问财本地油猴代跑上报接口单测 (最小链路).

直调 async 端点 + monkeypatch, 不起 FastAPI app(避开 lifespan 连库)、不打网。
覆盖: 清单下发 token 守门 / ingest token 守门 / 防御性清洗(6位代码·白名单extra·数值强转) /
      user_id 从 strategy_id 反解 / 落库参数正确。
"""
import asyncio

import pytest
from fastapi import HTTPException

from backend.routers import wencai as wc


def _setup(monkeypatch, token="SECRET"):
    """注入: config(含 ingest_token) + 捕获 upsert 参数 + 桩掉自定义语句查询。返回 captured 容器。"""
    monkeypatch.setattr(wc, "load_config", lambda: {"wencai_screening": {
        "ingest_token": token,
        "queries": [
            {"id": "breakout", "name": "量价突破型", "query": "换手率大于5% 且 非ST", "enabled": True},
            {"id": "off", "name": "停用的", "query": "x", "enabled": False},
        ],
    }})
    cap = {}

    async def fake_upsert(strategy_id, user_id, name, query, trade_date, items, last_error=""):
        cap.update(sid=strategy_id, uid=user_id, name=name, query=query,
                   trade_date=trade_date, items=items)
    monkeypatch.setattr(wc.repository, "upsert_wencai_strategy", fake_upsert)

    async def fake_user_queries():
        return [{"user_id": 7, "id": 3, "name": "我的低吸", "query_text": "回踩10日线", "enabled": 1}]
    monkeypatch.setattr(wc.repository, "list_all_enabled_queries", fake_user_queries)
    monkeypatch.setattr(wc.repository, "pool_strategy_id", lambda uid, qid: f"u{uid}_q{qid}")
    return cap


def _req(**kw):
    base = dict(token="SECRET", strategy_id="breakout", strategy_name="量价突破型",
                query_text="换手率大于5%", trade_date="2026-07-15", items=[])
    base.update(kw)
    return wc.IngestRequest(**base)


# ── 清单下发 ──

def test_queries_bad_token_rejected(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(wc.ingest_queries(wc.IngestQueriesRequest(token="WRONG")))
    assert ei.value.status_code == 401


def test_queries_lists_preset_and_custom_enabled_only(monkeypatch):
    _setup(monkeypatch)
    res = asyncio.run(wc.ingest_queries(wc.IngestQueriesRequest(token="SECRET")))
    sids = [q["strategy_id"] for q in res["queries"]]
    assert "breakout" in sids            # 预置启用
    assert "off" not in sids             # 预置停用被过滤
    assert "u7_q3" in sids               # 用户自定义启用
    custom = next(q for q in res["queries"] if q["strategy_id"] == "u7_q3")
    assert custom["query"] == "回踩10日线"


# ── ingest 上报 ──

def test_ingest_bad_token_rejected(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(wc.ingest_wencai(_req(token="WRONG")))
    assert ei.value.status_code == 401


def test_ingest_empty_configured_token_rejects_all(monkeypatch):
    _setup(monkeypatch, token="")        # 未配置密钥 = 拒收一切
    with pytest.raises(HTTPException) as ei:
        asyncio.run(wc.ingest_wencai(_req(token="")))
    assert ei.value.status_code == 401


def test_ingest_empty_strategy_id_rejected(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(wc.ingest_wencai(_req(strategy_id="   ")))
    assert ei.value.status_code == 400


def test_ingest_valid_saved_and_cleaned(monkeypatch):
    cap = _setup(monkeypatch)
    items = [wc.IngestItem(code="301520", name="万邦医药", price=58.79, pct_change=20.0,
                           extra={"tech_pattern": "价升量涨", "turnover": 54.058, "junk": "x"})]
    res = asyncio.run(wc.ingest_wencai(_req(items=items)))
    assert res["ok"] is True and res["stock_count"] == 1
    assert cap["sid"] == "breakout" and cap["uid"] == 0    # 预置榜 → user_id 0
    assert cap["name"] == "量价突破型" and cap["trade_date"] == "2026-07-15"
    row = cap["items"][0]
    assert row["code"] == "301520" and row["name"] == "万邦医药"
    assert row["price"] == 58.79 and row["pct_change"] == 20.0
    assert row["extra"]["tech_pattern"] == "价升量涨" and row["extra"]["turnover"] == 54.058
    assert "junk" not in row["extra"]                      # 白名单外的键被剔除


def test_ingest_uid_from_custom_strategy_id(monkeypatch):
    cap = _setup(monkeypatch)
    asyncio.run(wc.ingest_wencai(_req(strategy_id="u7_q3", strategy_name="我的低吸")))
    assert cap["uid"] == 7                                 # 自定义榜 u7_q3 → user_id 7


def test_ingest_bad_code_filtered(monkeypatch):
    cap = _setup(monkeypatch)
    items = [wc.IngestItem(code="ABC", name="坏"), wc.IngestItem(code="600000", name="浦发")]
    res = asyncio.run(wc.ingest_wencai(_req(items=items)))
    assert res["stock_count"] == 1
    assert cap["items"][0]["code"] == "600000"


def test_ingest_trade_date_defaults_today(monkeypatch):
    cap = _setup(monkeypatch)
    asyncio.run(wc.ingest_wencai(_req(trade_date="")))
    assert len(cap["trade_date"]) == 10                    # YYYY-MM-DD
