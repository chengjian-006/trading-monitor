# -*- coding: utf-8 -*-
"""市场风险状态机 + 历史指标口径修复 (v1.7.570) + 基线 v1.1 构卡(退潮提示卡/解除卡)。
纯函数与 mock 推送, 不连库不联网。"""
from unittest.mock import AsyncMock

from backend.services import market_risk_controller as mrc
from backend.services.market_risk_controller import _run_state_machine, _hist_indicators


# ── RED→YELLOW 降级(补推送分支的前提: 该迁移确实会发生) ──

def test_state_machine_red_to_yellow_on_recovery():
    """RED 态下广度>25%、涨跌比>40%、5日均收益>0 → 降级 YELLOW(触发新的降级卡)。"""
    today = {"advance_ratio": 60.0, "avg_ret_ma5": 0.5, "low52_ratio": 2.0,
             "zha_rate": 10.0, "breadth_ma20": 50.0}
    assert _run_state_machine("RED", today, breadth=50.0) == "YELLOW"


def test_state_machine_red_stays_red_when_still_weak():
    today = {"advance_ratio": 30.0, "avg_ret_ma5": -1.5, "low52_ratio": 20.0,
             "zha_rate": 40.0, "breadth_ma20": 18.0}
    assert _run_state_machine("RED", today, breadth=18.0) == "RED"


def test_state_machine_red_needs_all_three_to_exit():
    """只满足部分回暖条件(涨跌比够但广度不够)仍留 RED。"""
    today = {"advance_ratio": 60.0, "avg_ret_ma5": 0.5, "low52_ratio": 2.0,
             "zha_rate": 10.0, "breadth_ma20": 20.0}
    assert _run_state_machine("RED", today, breadth=20.0) == "RED"   # 广度20<25


# ── 0-100 风险分(v1.7.740, Deploy 2A): 展示用, 档位仍由状态机定, 分数只在带内定位 ──

def test_risk_score_stays_within_tier_band():
    """分数永远落在所属档的分数带内, 绝不与三档戳矛盾(正常 0-33 / 谨慎 34-66 / 危险 67-100)。"""
    calm = {"breadth_ma20": 60.0, "advance_ratio": 60.0, "avg_ret_ma5": 1.0,
            "low52_ratio": 1.0, "zha_rate": 10.0}
    panic = {"breadth_ma20": 10.0, "advance_ratio": 10.0, "avg_ret_ma5": -3.0,
             "low52_ratio": 25.0, "zha_rate": 80.0}
    assert 0 <= mrc.risk_score_of("GREEN", calm) <= 33
    assert 34 <= mrc.risk_score_of("YELLOW", calm) <= 66
    assert 67 <= mrc.risk_score_of("RED", panic) <= 100


def test_risk_score_monotonic_with_pressure():
    """同一档内, 指标越差风险分越高。"""
    weak = {"breadth_ma20": 20.0, "advance_ratio": 25.0, "avg_ret_ma5": -0.8,
            "low52_ratio": 12.0, "zha_rate": 55.0}
    weaker = {"breadth_ma20": 16.0, "advance_ratio": 18.0, "avg_ret_ma5": -0.95,
              "low52_ratio": 14.0, "zha_rate": 65.0}
    assert mrc.risk_score_of("YELLOW", weaker) >= mrc.risk_score_of("YELLOW", weak)


def test_risk_score_handles_missing_indicators():
    """realtime 行只有涨跌比/均收益, 其余为 None → 用现有维度算, 不抛。"""
    partial = {"advance_ratio": 20.0, "avg_ret_ma5": -2.0,
               "breadth_ma20": None, "low52_ratio": None, "zha_rate": None}
    s = mrc.risk_score_of("RED", partial)
    assert 67 <= s <= 100


def test_tier_label_of():
    # v1.7.752 (Deploy 2B retier): RED 档名「空仓」→「危险」
    assert mrc.tier_label_of("GREEN") == "正常"
    assert mrc.tier_label_of("YELLOW") == "谨慎"
    assert mrc.tier_label_of("RED") == "危险"


# ── 状态段锚点(v1.7.678): 横幅「几点起」必须指向连续段第一天, 不是最新行 updated_at ──

def test_streak_from_rows_anchors_at_segment_start():
    """连续 RED 段: 锚点=段内最早那天, 天数=段长。EOD 每日 upsert 刷新 updated_at 不应影响。"""
    rows = [
        {"state": "RED", "updated_at": "2026-07-17 16:40:31"},
        {"state": "RED", "updated_at": "2026-07-16 16:40:25"},
        {"state": "RED", "updated_at": "2026-07-15 16:40:26"},
        {"state": "GREEN", "updated_at": "2026-07-14 16:40:27"},
        {"state": "RED", "updated_at": "2026-07-13 16:40:27"},
    ]
    st, anchor, days = mrc.streak_from_rows(rows)
    assert st == "RED"
    assert anchor == "2026-07-15 16:40:26"   # 不是最新的 7/17
    assert days == 3                         # GREEN 之前的那段 RED 不计入


def test_streak_from_rows_breaks_on_state_change():
    """RED→YELLOW→RED 按 state 值断段: 当前 RED 只算 1 天, 与横幅文案(空仓中)一致。"""
    rows = [
        {"state": "RED", "updated_at": "2026-07-17 16:40:31"},
        {"state": "YELLOW", "updated_at": "2026-07-16 16:40:25"},
        {"state": "RED", "updated_at": "2026-07-15 16:40:26"},
    ]
    st, anchor, days = mrc.streak_from_rows(rows)
    assert (st, anchor, days) == ("RED", "2026-07-17 16:40:31", 1)


def test_streak_from_rows_empty():
    assert mrc.streak_from_rows([]) == ("GREEN", None, 0)


# ── 历史指标: 脏 low=0 行不再炸整轮 EOD(v1.7.570 crash guard) ──

def test_hist_indicators_no_crash_on_zero_low_rows():
    """某票 low 全为 0(脏数据)时, 原 min([]) 抛 ValueError 会炸掉整轮评估; 修复后安全跳过。"""
    rows = [
        ("000001", "2026-06-10", 10.0, 10.5, 0.0),
        ("000001", "2026-06-11", 10.2, 10.6, 0.0),
        ("000001", "2026-06-12", 10.1, 10.3, 0.0),
    ]
    out = _hist_indicators(rows, need_days=6)   # 不应抛异常
    assert isinstance(out, list)                # 小样本(n<1000)输出为空, 但不炸


# ── 盘中实时监测(Deploy 2B, v1.7.752: 全市场口径, 纯函数部分) ──

def test_watch_enter_state_thresholds():
    """进入口径: 涨跌比+三大指数平均 → 应然状态(危险要两条同时满足, 谨慎满足其一)。"""
    assert mrc._watch_enter_state(18.0, -1.8) == "RED"     # 双条件满足 → 危险
    assert mrc._watch_enter_state(18.0, -0.5) == "YELLOW"  # 只有涨跌比差 → 谨慎(不到危险)
    assert mrc._watch_enter_state(50.0, -1.2) == "YELLOW"  # 只有指数差 → 谨慎
    assert mrc._watch_enter_state(55.0, 0.3) == "GREEN"


def test_watch_exit_needs_buffer():
    """退出机制: 必须明显转好过缓冲带才降档, 缓冲带内维持原档(防贴线来回打脸)。"""
    # RED: 进入线是 22%, 但回到 30%(未过 35% 退出线)仍是 RED
    assert mrc._watch_exit_target("RED", 30.0, -0.5) == "RED"
    assert mrc._watch_exit_target("RED", 36.0, -0.5) == "YELLOW"   # 过 RED 退出线 → 降谨慎
    assert mrc._watch_exit_target("RED", 50.0, 0.1) == "GREEN"     # 一步转好到位 → 直接解除
    # YELLOW: 40%(未过 45% 退出线)仍是 YELLOW
    assert mrc._watch_exit_target("YELLOW", 40.0, 0.0) == "YELLOW"
    assert mrc._watch_exit_target("YELLOW", 46.0, 0.0) == "GREEN"
    assert mrc._watch_exit_target("GREEN", 10.0, -3.0) == "GREEN"  # 退出函数不管升级


# ── 大白话解读(Deploy 2B: 从 regime_filter 迁入, 纯函数) ──

def test_plain_language_panic():
    s, a = mrc.plain_market_language(
        {"up_count": 300, "down_count": 4500, "limit_up": 5, "limit_down": 60}, 9000, "RED")
    assert "恐慌" in s and "空仓" in a


def test_plain_language_broad_rally_green():
    s, a = mrc.plain_market_language(
        {"up_count": 4000, "down_count": 800, "limit_up": 80, "limit_down": 3}, 13000, "GREEN")
    assert "普涨" in s and "放量" in s
    assert "风控" not in s      # 正常档不加尾注


def test_plain_language_rally_but_risky_gets_tier_tail():
    s, a = mrc.plain_market_language(
        {"up_count": 4000, "down_count": 800, "limit_up": 80, "limit_down": 3}, 9000, "YELLOW")
    assert "谨慎档" in s        # 非正常档带风控尾注


def test_plain_language_empty_stats():
    assert mrc.plain_market_language({}, 0, "GREEN") == ("", "")


# ── EOD 恢复正常 → 解除卡(基线 v1.1 标准型: 灰header + 副标题时间线 + 解除条件 + 👉建议) ──

async def test_eod_green_recovery_sends_dismiss_card(monkeypatch):
    cur = {"date": "2026-07-16", "advance_ratio": 55.0, "avg_ret_ma5": 0.4,
           "low52_ratio": 2.0, "zha_rate": 10.0, "adv": 3000, "dec": 1500}
    monkeypatch.setattr("backend.core.trading_calendar.is_workday", lambda: True)
    monkeypatch.setattr(mrc, "_gather_metrics", AsyncMock(return_value=([], cur, 45.0)))
    monkeypatch.setattr(mrc, "_get_prev_state", AsyncMock(return_value=mrc.YELLOW))
    monkeypatch.setattr(mrc, "_get_row", AsyncMock(return_value=None))
    monkeypatch.setattr(mrc, "_upsert_risk", AsyncMock())
    monkeypatch.setattr(mrc, "_nongreen_streak", AsyncMock(return_value=("7月8日", 6)))
    state_card = AsyncMock()
    monkeypatch.setattr(mrc, "_push_state_card", state_card)
    dismiss = AsyncMock()
    monkeypatch.setattr(mrc, "_push_dismiss", dismiss)

    await mrc.market_risk_eod()   # YELLOW + 55%/45%/+0.4 → GREEN

    assert state_card.await_count == 0 and dismiss.await_count == 1
    card = dismiss.await_args[0][0]
    assert card.title == "✅ 预警解除 · 市场风险预警"
    assert card.template == "grey"                               # 灰 = 中性收尾
    assert card.subtitle == "7月8日 发布 → 今日解除，生效 6 个交易日"   # 时间线
    cond = card.elements[0]["content"]
    assert "解除条件" in cond and "**55%**" in cond and "≥ 42%" in cond   # 写明解除条件+实测值加粗
    assert "👉" in card.elements[-1]["content"]
