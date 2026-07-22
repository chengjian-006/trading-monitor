"""推送补发纯逻辑测试: 时间窗裁剪 + 摘要卡构建(不连库)。"""
from datetime import datetime, timedelta

from backend.services import push_backfill as pb


# ── compute_window: 窗口=[max(disabled_at, now-24h), now] ──
def test_window_none_when_no_disabled_at():
    now = datetime(2026, 6, 18, 15, 0, 0)
    assert pb.compute_window(None, now) is None
    assert pb.compute_window("", now) is None


def test_window_clamped_to_24h():
    now = datetime(2026, 6, 18, 15, 0, 0)
    disabled = now - timedelta(hours=50)  # 关了50小时
    start, end = pb.compute_window(disabled, now)
    assert end == now
    assert start == now - timedelta(hours=pb.MAX_AGE_HOURS)  # 只回溯24h


def test_window_uses_disabled_at_when_recent():
    now = datetime(2026, 6, 18, 15, 0, 0)
    disabled = now - timedelta(hours=3)
    start, end = pb.compute_window(disabled, now)
    assert start == disabled and end == now


def test_window_none_when_disabled_at_in_future():
    now = datetime(2026, 6, 18, 15, 0, 0)
    assert pb.compute_window(now + timedelta(minutes=1), now) is None


# ── 事件裁剪: 最多 MAX_ITEMS 条, 取最新 ──
def test_cap_items_keeps_newest():
    evs = [{"triggered_at": datetime(2026, 6, 18, 9, i), "name": f"股{i}", "code": "0",
            "direction": "buy", "signal_name": "x"} for i in range(40)]
    kept, dropped = pb.cap_events(evs)
    assert len(kept) == pb.MAX_ITEMS
    assert dropped == 40 - pb.MAX_ITEMS
    # 输入按时间升序, 保留的应是最新的(末尾那批)
    assert kept[0]["triggered_at"] >= kept[-1]["triggered_at"]


def test_cap_items_no_drop_when_few():
    evs = [{"triggered_at": datetime(2026, 6, 18, 9, 0), "name": "股", "code": "0",
            "direction": "sell", "signal_name": "x"}]
    kept, dropped = pb.cap_events(evs)
    assert len(kept) == 1 and dropped == 0


# ── 卡片构建: 有事件 → 有信号文本行; 风险档非GREEN → 有横幅 ──
def test_build_card_has_table_and_risk_banner():
    evs = [{"triggered_at": datetime(2026, 6, 18, 10, 30), "name": "国际复材", "code": "301526",
            "direction": "buy", "signal_name": "缩量突破"}]
    text, elements = pb.build_backfill_card("lark", evs, 0, risk_state="RED")
    assert "错过" in text
    # 移动优化(v1.7.581): 表格改换行文本行(单元格塞多字段手机端字符级截断), 断言信号名/标的进 markdown 行、无表格竖线
    assert any(e.get("tag") == "markdown" and "缩量突破" in e.get("content", "")
               and "国际复材" in e.get("content", "") and "|" not in e.get("content", "") for e in elements)
    joined = "".join(str(e) for e in elements)
    assert "危险档" in joined and "空仓" in joined  # 风险横幅(v1.7.752: 档名危险, 空仓是建议语)


def test_build_card_green_no_banner():
    evs = [{"triggered_at": datetime(2026, 6, 18, 10, 30), "name": "国际复材", "code": "301526",
            "direction": "buy", "signal_name": "缩量突破"}]
    text, elements = pb.build_backfill_card("lark", evs, 0, risk_state="GREEN")
    joined = "".join(str(e) for e in elements)
    assert "市场风险" not in joined
