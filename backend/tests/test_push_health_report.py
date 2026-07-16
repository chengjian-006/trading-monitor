"""推送健康度周报单测: 动作汇总纯函数 + 建议分支 + 构卡口径 + 编排去重。"""
import asyncio
from datetime import date

from backend.services import push_health_report as ph


def _row(kind, target="", created="2026-07-14 10:00:00"):
    return {"kind": kind, "target": target, "created_at": created}


SAMPLE = [
    _row("mute"),
    _row("snooze", "601689"),
    _row("model_off", "BUY_VOL_BREAKOUT"),
    _row("model_off", "BUY_VOL_BREAKOUT"),
    _row("model_off", "BUY_RALLY_MA10"),
    _row("ack", "601689|BUY_VOL_BREAKOUT"),
    _row("mark_sold", "600519"),
    _row("ma_alert_10", "600519"),
]


def _all_md(card) -> str:
    out = []
    for el in card.elements:
        out.append(str(el.get("content", "")))
        for col in el.get("columns", []):
            for sub in col.get("elements", []):
                out.append(str(sub.get("content", "")))
        for sub in el.get("elements", []):
            if isinstance(sub, dict):
                out.append(str(sub.get("content", "")))
    return "\n".join(out)


# ── 汇总纯函数 ──

def test_summarize_actions():
    s = ph.summarize_actions(SAMPLE)
    assert s["total"] == 8
    assert dict(s["by_kind"])["model_off"] == 3
    assert s["model_off"][0] == ("BUY_VOL_BREAKOUT", 2)
    assert s["top_model"] == ("BUY_VOL_BREAKOUT", 2)
    assert s["ma_alert_new"] == 1


def test_summarize_empty():
    s = ph.summarize_actions([])
    assert s["total"] == 0 and s["by_kind"] == [] and s["top_model"] is None


# ── 建议分支 ──

def test_advice_concentrated_model():
    s = ph.summarize_actions(SAMPLE)
    adv = ph.pick_advice(s, {"BUY_VOL_BREAKOUT": "缩量后放量突破（右侧）"})
    assert "缩量后放量突破" in adv and "模型图鉴" in adv


def test_advice_healthy_when_quiet():
    s = ph.summarize_actions([_row("mute")])
    assert "健康" in ph.pick_advice(s, {})


def test_advice_many_actions_no_focus():
    rows = [_row("mute", created=f"2026-07-1{i % 5} 09:00:00") for i in range(12)]
    adv = ph.pick_advice(ph.summarize_actions(rows), {})
    assert "健康" not in adv  # 动作偏多不能说节奏健康


# ── 构卡 ──

def test_build_health_card_structure():
    stats = ph.summarize_actions(SAMPLE)
    card = ph.build_health_card(
        stats=stats, name_map={"BUY_VOL_BREAKOUT": "缩量后放量突破（右侧）",
                               "BUY_RALLY_MA10": "回踩10MA缩量后突破昨高"},
        active_ma_alerts=3, start_date="2026-07-13", trading_days_n=4)
    assert card.family == "system" and card.template == "grey"
    assert card.title.startswith("⚙️ 推送健康度周报")
    assert card.summary
    kpi = card.elements[0]
    assert kpi["tag"] == "column_set" and len(kpi["columns"]) == 3
    md = _all_md(card)
    assert "8次" in md and "3单" in md
    # KPI 最常关模型用中文短名, 不出现 signal_id 代号
    assert "缩量后放量突破" in md and "BUY_VOL_BREAKOUT" not in md
    # 动作分布清单
    assert "| 动作 | 次数 |" in md and "今日关此模型" in md
    # 口径注明起始日 + 无推送量日志说明
    assert "2026-07-13" in md and "推送" in md
    assert "👉" in md
    assert "2026-07-13" in card.subtitle
    assert "8" in card.fallback and "👉" in card.fallback


def test_build_health_card_no_actions_still_builds():
    stats = ph.summarize_actions([])
    card = ph.build_health_card(stats=stats, name_map={}, active_ma_alerts=0,
                                start_date="2026-07-13", trading_days_n=4)
    assert card is not None
    md = _all_md(card)
    assert "0次" in md and "无" in md and "健康" in md


# ── 编排: 去重 + 先标记再推 ──

def test_run_skips_non_workday(monkeypatch):
    monkeypatch.setattr(ph, "is_workday", lambda *a, **k: False)

    async def boom(*a, **k):
        raise AssertionError("非交易日不应查询")

    monkeypatch.setattr(ph.gt, "last_date", boom)
    asyncio.run(ph.run_push_health_report())


def test_run_dedup_already_sent_today(monkeypatch):
    monkeypatch.setattr(ph, "is_workday", lambda *a, **k: True)

    async def fake_last(code, rule):
        assert (code, rule) == (ph._DEDUP_CODE, ph._DEDUP_RULE)
        return date.today().isoformat()

    async def boom(*a, **k):
        raise AssertionError("当日已发不应再取数")

    monkeypatch.setattr(ph.gt, "last_date", fake_last)
    monkeypatch.setattr(ph, "_collect", boom)
    asyncio.run(ph.run_push_health_report())


def test_run_sends_even_with_zero_actions(monkeypatch):
    """数据不足/无动作也发(注明口径), 与盘前卡"全空不发"不同。"""
    monkeypatch.setattr(ph, "is_workday", lambda *a, **k: True)
    calls = {"bump": 0, "send": 0}

    async def fake_last(code, rule):
        return None

    async def fake_collect():
        return dict(stats=ph.summarize_actions([]), name_map={},
                    active_ma_alerts=0, start_date="2026-07-13", trading_days_n=4)

    async def fake_bump(today, code, rule, ts):
        calls["bump"] += 1

    async def fake_send(card):
        calls["send"] += 1
        assert calls["bump"] == 1, "先标记再推"
        assert card.family == "system"
        return True

    monkeypatch.setattr(ph.gt, "last_date", fake_last)
    monkeypatch.setattr(ph.gt, "bump", fake_bump)
    monkeypatch.setattr(ph, "_collect", fake_collect)
    monkeypatch.setattr(ph.notifier, "send_card", fake_send)
    asyncio.run(ph.run_push_health_report())
    assert calls == {"bump": 1, "send": 1}
