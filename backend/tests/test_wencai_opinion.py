"""问财观点上报接口单测 (最小链路).

直调 async 端点 + monkeypatch, 不起 app、不打网、不连库。
覆盖: 无 token 也放行(已去鉴权) / 从投顾话术里撞出个股(6位代码 + 全名命中) / 主推排序 / 落库参数。
"""
import asyncio
import io
import re
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.routers import wencai as wc

_EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension" / "wencai-opinion"

# 模拟全市场名称字典(含会被话术提及的 + 干扰项)
_NAMES = [
    {"code": "000977", "name": "浪潮信息"},
    {"code": "603001", "name": "奥康国际"},
    {"code": "300604", "name": "长川科技"},
    {"code": "600519", "name": "贵州茅台"},
]


def _setup(monkeypatch, token="SECRET"):
    monkeypatch.setattr(wc, "load_config", lambda: {"wencai_opinion": {"ingest_token": token}})

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


def test_opinion_requires_a_valid_ingest_token(monkeypatch):
    """Public extension uploads must reject missing or incorrect credentials."""
    cap = _setup(monkeypatch)
    with pytest.raises(HTTPException) as empty:
        _ingest(_req(token=""))
    with pytest.raises(HTTPException) as wrong:
        _ingest(_req(token="WRONG"))
    assert empty.value.status_code == 401
    assert wrong.value.status_code == 401
    assert cap == {}


def test_opinion_accepts_the_configured_ingest_token(monkeypatch):
    """A valid, configured extension credential keeps the intended ingest flow working."""
    cap = _setup(monkeypatch)
    r = _ingest(_req(token="SECRET"))
    assert r["ok"] is True
    assert cap["user_id"] == 0


def test_opinion_rejects_when_server_token_is_unconfigured(monkeypatch):
    cap = _setup(monkeypatch, token="")
    with pytest.raises(HTTPException) as error:
        _ingest(_req(token="SECRET"))
    assert error.value.status_code == 401
    assert cap == {}


def test_extension_defaults_to_https_application_host_and_preserves_overrides():
    """Every entry point uses the deployed app origin; settings saves retain overrides."""
    sources = {
        name: (_EXTENSION_DIR / name).read_text(encoding="utf-8")
        for name in ("background.js", "content.js", "options.js", "popup.js")
    }
    for source in sources.values():
        assert "serverUrl: 'https://app.guxiaocha.com'" in source
        assert "serverUrl: 'https://124.71.75.5'" not in source
    for name in ("options.js", "popup.js"):
        assert "serverUrl: activeServerUrl" in sources[name]
        assert "activeServerUrl = s.serverUrl || DEFAULTS.serverUrl" in sources[name]


def test_opinion_docstring_describes_dedicated_token_protection():
    doc = wc.ingest_opinion.__doc__ or ""
    assert "wencai_opinion.ingest_token" in doc
    assert "不做 token 鉴权" not in doc


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


# ── 扩展/油猴 分发与自更新接口 (v1.7.681) ──

def test_ext_version_reads_real_manifest():
    """/ext/version 应读到已部署扩展 manifest 的版本(形如 x.y.z)与油猴 @version。"""
    r = asyncio.run(wc.ext_version())
    assert re.match(r"^\d+\.\d+", r["ext_version"] or "")       # 扩展版本非空且像版本号
    assert re.match(r"^\d+\.\d+", r["userscript_version"] or "")  # 油猴 @version 非空


def test_userscript_served_with_update_headers():
    """/userscript.user.js 返回油猴原文, 且带自动更新头(@downloadURL)。"""
    r = asyncio.run(wc.userscript())
    body = bytes(r.body).decode("utf-8")
    assert "==UserScript==" in body
    assert "@downloadURL" in body and "@updateURL" in body
    assert r.media_type.startswith("text/javascript")


def test_ext_download_is_loadable_zip():
    """/ext/download 返回 zip, 顶层含 wencai-opinion/ 且有 manifest.json。"""
    r = asyncio.run(wc.ext_download())
    assert r.media_type == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(bytes(r.body))).namelist()
    assert any(n.endswith("wencai-opinion/manifest.json") for n in names)
    assert all(n.startswith("wencai-opinion/") for n in names)


def test_ext_trading_day_matches_calendar():
    """/ext/trading-day 给扩展「定时自动问」判断今天要不要跑。

    存在的理由: 扩展自己只会 getDay()===0||6 跳周末, **不认法定节假日**, 于是国庆/春节
    整周照跑。后端有 chinese-calendar 权威日历(v1.7.464 起), 这里把它暴露给扩展。
    断言口径与 trading_calendar.is_workday 一致 —— 不写死某天真假, 否则用例本身会随日期翻车。
    """
    from backend.core.trading_calendar import is_workday

    r = asyncio.run(wc.ext_trading_day())
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", r["date"])
    assert isinstance(r["is_trading_day"], bool)
    assert r["is_trading_day"] is bool(is_workday())
