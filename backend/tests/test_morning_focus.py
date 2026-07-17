"""盘前「今日关注」摘要卡单测: 纯构卡逻辑(五区骨架/Top5折叠/空段跳过/全空不发) + 编排去重。"""
import asyncio
from datetime import date

from backend.services import morning_focus as mf


def _buy(name, code, model, pct):
    return {"name": name, "code": code, "model": model, "pct": pct}


def _kwargs(**over):
    kw = dict(
        holding_n=3, total_signals=7,
        buy_rows=[_buy("拓普集团", "601689", "缩量后放量突破（右侧）", 2.7)],
        disclosure_rows=[{"code": "600519", "name": "贵州茅台",
                          "report_type": "2", "appoint_date": "2026-07-17"}],
        hold_codes={"600519"},
        risk_state="YELLOW", risk_since="07-15 16:40",
        stop_pressure_n=1, ma_alert_n=2,
    )
    kw.update(over)
    return kw


def _all_md(card) -> str:
    out = []
    for el in card.elements:
        out.append(str(el.get("content", "")))
        for col in el.get("columns", []):
            for sub in col.get("elements", []):
                out.append(str(sub.get("content", "")))
        # 折叠面板正文
        for sub in el.get("elements", []):
            if isinstance(sub, dict):
                out.append(str(sub.get("content", "")))
    return "\n".join(out)


# ── 纯小件 ──

def test_model_short_strips_bracket_suffix():
    assert mf.model_short("缩量后放量突破（右侧）") == "缩量后放量突破"
    assert mf.model_short("弱势极限（左侧）") == "弱势极限"
    assert mf.model_short("strong(右)") == "strong"
    assert len(mf.model_short("回踩10MA缩量后突破昨高")) <= 8
    assert mf.model_short("") == ""


def test_close_pct():
    assert abs(mf.close_pct([10.5, 10.0]) - 5.0) < 1e-9
    assert abs(mf.close_pct([9.0, 10.0]) - (-10.0)) < 1e-9
    assert mf.close_pct([10.0]) is None
    assert mf.close_pct([]) is None
    assert mf.close_pct([10.0, 0.0]) is None


# ── 构卡 ──

def test_build_card_basic_structure():
    card = mf.build_morning_focus_card(**_kwargs())
    assert card is not None
    assert card.family == "intel" and card.template == "blue"
    assert card.title.startswith("📊 今日关注")
    assert card.summary  # 锁屏摘要标配
    # KPI 三栏
    kpi = card.elements[0]
    assert kpi["tag"] == "column_set" and len(kpi["columns"]) == 3
    md = _all_md(card)
    assert "3只" in md and "7条" in md and "1家" in md
    # 昨日买点追踪: 表格 + 模型短名(全名括号后缀剥掉)
    assert "| 股票 | 模型 | 昨收 |" in md
    assert "缩量后放量突破" in md and "（右侧）" not in md
    assert "+2.7%" in md
    # 今日风险一行: 今日披露摘要, 持仓票标🔴
    assert "今日披露财报" in md and "🔴贵州茅台" in md
    # 当前状态: 风险档 + 止损压力 + 到线提醒
    assert "🟡" in md and "谨慎" in md and "07-15 16:40" in md
    assert "止损压力" in md and "到线提醒" in md
    # 👉 建议
    assert "👉" in md and "竞价播报" in md
    # fallback 同源
    assert "拓普集团" in card.fallback and "👉" in card.fallback


def test_build_card_top5_overflow_folds():
    rows = [_buy(f"股{i}", f"60000{i}", "弱势极限", i * 1.0) for i in range(7)]
    card = mf.build_morning_focus_card(**_kwargs(buy_rows=rows, total_signals=9))
    md = _all_md(card)
    # 表格只放 Top5, 带"等 7 只"
    table_md = next(e["content"] for e in card.elements
                    if e.get("tag") == "markdown" and "| 股票 |" in e.get("content", ""))
    assert table_md.count("\n") == 6  # 表头+分隔+5行
    assert "等 **7** 只" in md
    # 全量进折叠
    folds = [e for e in card.elements if e.get("tag") == "collapsible_panel"]
    assert folds, "超5只必须有折叠全量"
    fold_md = _all_md(card)
    assert "股6" in fold_md


def test_build_card_pct_missing_shows_dash():
    card = mf.build_morning_focus_card(**_kwargs(
        buy_rows=[_buy("无K线股", "600001", "弱势极限", None)]))
    assert "—" in _all_md(card)


def test_build_card_skips_empty_sections():
    card = mf.build_morning_focus_card(**_kwargs(
        buy_rows=[], disclosure_rows=[], stop_pressure_n=0, ma_alert_n=0,
        risk_state="GREEN", risk_since=""))
    md = _all_md(card)
    assert "昨日买点追踪" not in md
    assert "披露日历" not in md
    assert "止损压力" not in md and "到线提醒" not in md
    assert "🟢" in md  # 风险档常显


def test_build_card_all_empty_returns_none():
    assert mf.build_morning_focus_card(**_kwargs(
        holding_n=0, total_signals=0, buy_rows=[], disclosure_rows=[])) is None


# ── 编排: 工作日闸 / 每日一次去重 / 先标记再推 ──

def test_run_skips_non_workday(monkeypatch):
    monkeypatch.setattr(mf, "is_workday", lambda *a, **k: False)

    async def boom(*a, **k):
        raise AssertionError("非交易日不应查询")

    monkeypatch.setattr(mf.gt, "last_date", boom)
    asyncio.run(mf.run_morning_focus())


def test_run_dedup_already_sent_today(monkeypatch):
    monkeypatch.setattr(mf, "is_workday", lambda *a, **k: True)

    async def fake_last(code, rule):
        assert (code, rule) == (mf._DEDUP_CODE, mf._DEDUP_RULE)
        return date.today().isoformat()

    async def boom():
        raise AssertionError("当日已发不应再取数")

    monkeypatch.setattr(mf.gt, "last_date", fake_last)
    monkeypatch.setattr(mf, "_collect", boom)
    asyncio.run(mf.run_morning_focus())


def test_run_sends_and_marks(monkeypatch):
    monkeypatch.setattr(mf, "is_workday", lambda *a, **k: True)
    calls = {"bump": 0, "send": 0}

    async def fake_last(code, rule):
        return None

    async def fake_collect():
        return _kwargs()

    async def fake_bump(today, code, rule, ts):
        calls["bump"] += 1
        assert (code, rule) == (mf._DEDUP_CODE, mf._DEDUP_RULE)

    async def fake_send(card):
        calls["send"] += 1
        assert calls["bump"] == 1, "先标记再推(防发送后标记失败重复发)"
        assert card.family == "intel"
        return True

    monkeypatch.setattr(mf.gt, "last_date", fake_last)
    monkeypatch.setattr(mf.gt, "bump", fake_bump)
    monkeypatch.setattr(mf, "_collect", fake_collect)
    monkeypatch.setattr(mf.notifier, "send_card", fake_send)
    asyncio.run(mf.run_morning_focus())
    assert calls == {"bump": 1, "send": 1}


def test_run_all_empty_no_send_no_mark(monkeypatch):
    monkeypatch.setattr(mf, "is_workday", lambda *a, **k: True)

    async def fake_last(code, rule):
        return None

    async def fake_collect():
        return _kwargs(holding_n=0, total_signals=0, buy_rows=[], disclosure_rows=[])

    async def boom(*a, **k):
        raise AssertionError("全空不发也不标记")

    monkeypatch.setattr(mf.gt, "last_date", fake_last)
    monkeypatch.setattr(mf.gt, "bump", boom)
    monkeypatch.setattr(mf, "_collect", fake_collect)
    monkeypatch.setattr(mf.notifier, "send_card", boom)
    asyncio.run(mf.run_morning_focus())
