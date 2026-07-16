# backend/tests/test_holding_guard.py
"""holding_guard 纯函数单测: 前高/接近判定/峰值/盈利保护触发/模型上下文/节流/文案。不连库不联网。"""
import pandas as pd

from backend.services.holding_guard import (
    prior_high,
    is_near_high,
    compute_peak,
    profit_protect_triggered,
    model_advisory,
    anomaly_quota_verdict,
    GuardThrottle,
    build_near_high_msg,
    build_profit_protect_msg,
    ANOMALY_MAX_DAILY,
    ANOMALY_DOWN_MAX_DAILY,
    DOWN_ANOMALY_RULES,
)


def _df(highs, start="2026-01-01"):
    """构造升序日K(只 date/high 有意义, 其余补齐), highs 为 high 序列。"""
    dates = pd.date_range(start, periods=len(highs)).strftime("%Y-%m-%d").tolist()
    return pd.DataFrame({
        "date": dates,
        "open": highs, "high": highs, "low": highs, "close": highs,
        "volume": [1000] * len(highs),
    })


# ---------- prior_high: 跳最近5根, 取更早60根窗口内最大值; 阻力已破则失效 ----------

def test_prior_high_skips_recent_and_picks_window_max():
    highs = [10.0] * 70
    highs[30] = 20.0   # 窗口内(index 5..64)真前高
    highs[67] = 15.0   # 最近5根(index 65..69)应被跳过(且低于前高, 阻力未破)
    highs[2] = 50.0    # 窗口下界之前(index<5)应被排除
    ph, ph_date = prior_high(_df(highs))
    assert ph == 20.0
    assert ph_date == "2026-01-31"   # index 30 = 第31天


def test_prior_high_suppressed_when_recent_broke_resistance():
    # 复现 京东方A/沪电股份 误报: 更早峰 20, 但最近5根已冲到 25(>峰) → 阻力已破, 不再当前高报
    highs = [10.0] * 70
    highs[30] = 20.0
    highs[67] = 25.0   # 最近5根突破了更早峰
    ph, ph_date = prior_high(_df(highs))
    assert ph is None and ph_date is None


def test_prior_high_kept_when_recent_below_resistance():
    # 真正逼近一个未破旧高: 最近5根都在峰下方 → 正常返回, 照常提醒
    highs = [10.0] * 70
    highs[30] = 20.0
    highs[66] = 19.5   # 最近5根接近但未破
    ph, ph_date = prior_high(_df(highs))
    assert ph == 20.0


def test_prior_high_insufficient_bars_returns_none():
    ph, ph_date = prior_high(_df([10.0] * 64))   # <65 根
    assert ph is None and ph_date is None


def test_prior_high_exactly_65_bars_ok():
    highs = [10.0] * 65
    highs[10] = 15.0
    ph, ph_date = prior_high(_df(highs))
    assert ph == 15.0


# ---------- is_near_high: 前高下方 ≤2% 触发, 已突破不报 ----------

def test_is_near_high_within_band():
    assert is_near_high(price=11.85, ph=12.05) is True   # -1.66%


def test_is_near_high_above_ph_not_triggered():
    assert is_near_high(price=12.10, ph=12.05) is False   # 已站上


def test_is_near_high_too_far_below():
    assert is_near_high(price=11.50, ph=12.05) is False   # -4.6% 超阈


# ---------- compute_peak: 仅 entry_date 起的子段, 与盘中现价取大 ----------

def test_compute_peak_from_entry_date_and_intraday():
    df = _df([8.0, 9.0, 13.0, 11.0, 10.0], start="2026-03-01")
    # entry 在 03-02(index1); 之前的 03-01=8 应忽略; 子段最高=13
    assert compute_peak(df, entry_date="2026-03-02", price=10.5) == 13.0
    # 盘中现价高于日K峰值时取现价
    assert compute_peak(df, entry_date="2026-03-02", price=14.2) == 14.2


# ---------- profit_protect_triggered: 峰值达标 × 回吐达标 真值表 ----------

def test_profit_protect_triggers_when_peaked_then_gave_back():
    # 峰值 +18% 达标, 当前 +1.4% ≤ +2% 达标
    assert profit_protect_triggered(cost=10.0, peak=11.8, price=10.14) is True


def test_profit_protect_no_trigger_still_in_profit():
    # 峰值达标但当前仍 +5% > +2%
    assert profit_protect_triggered(cost=10.0, peak=11.8, price=10.5) is False


def test_profit_protect_no_trigger_never_peaked():
    # 从没赚到 +10%
    assert profit_protect_triggered(cost=10.0, peak=10.5, price=10.1) is False


def test_profit_protect_no_trigger_zero_cost():
    assert profit_protect_triggered(cost=0.0, peak=11.8, price=10.1) is False


# ---------- model_advisory: 动量突破附洗盘提醒, 其余/未知为空 ----------

def test_model_advisory_momentum_breakout():
    assert "洗盘" in model_advisory("BUY_VOL_BREAKOUT")


def test_model_advisory_other_model_empty():
    assert model_advisory("BUY_RALLY_MA20") == ""


def test_model_advisory_unknown_empty():
    assert model_advisory(None) == ""


# ---------- 节流: 同股同规则当日仅一次, 跨日重置 ----------

def test_throttle_same_code_rule_once_per_day():
    t = GuardThrottle()
    assert t.throttled("000001", "prior_high", "2026-06-16") is False
    t.mark("000001", "prior_high", "2026-06-16")
    assert t.throttled("000001", "prior_high", "2026-06-16") is True
    # 另一规则不受影响
    assert t.throttled("000001", "profit_protect", "2026-06-16") is False


def test_throttle_resets_next_day():
    t = GuardThrottle()
    t.mark("000001", "prior_high", "2026-06-16")
    assert t.throttled("000001", "prior_high", "2026-06-17") is False


def test_throttle_load_restores_counts_from_db_snapshot():
    """v1.7.569: 重启后从 DB 快照恢复今日计数, 已推过的规则仍被挡(不重推)。"""
    t = GuardThrottle()
    rows = [
        {"code": "000001", "rule": "prior_high", "cnt": 1, "last_ts": 1000.0},
        {"code": "300750", "rule": "surge", "cnt": 2, "last_ts": 2000.0},
    ]
    t.load("2026-06-16", rows)
    # 接近前高(limit=1)已推1次 → 挡; 急拉(limit=2)已2次 → 挡; 冷却时间戳也恢复
    assert t.throttled("000001", "prior_high", "2026-06-16") is True
    assert t.throttled("300750", "surge", "2026-06-16", limit=2) is True
    assert t.cooling("300750", "surge", 2000.0 + 100, cooldown_sec=1800) is True
    # 未记录的规则不受影响
    assert t.throttled("000001", "profit_protect", "2026-06-16") is False


# ---------- 异动卡配额: 总3张 + 下跌类独立保底1张/自身封顶3张(2026-07 用户拍板"2改") ----------

def test_anomaly_quota_up_capped_by_total():
    # 上涨类只看总配额: 前3张放行, 第4张挡
    assert anomaly_quota_verdict(0, 0, is_down=False) is True
    assert anomaly_quota_verdict(2, 0, is_down=False) is True
    assert anomaly_quota_verdict(3, 0, is_down=False) is False


def test_anomaly_quota_down_guaranteed_when_total_full():
    # 核心场景: 当日前3张全是上涨类烧光总配额, 第4张是下跌类(急跌/跌停/开板) → 仍放行(保底1张)
    assert anomaly_quota_verdict(3, 0, is_down=True) is True


def test_anomaly_quota_down_no_second_exemption():
    # 保底只有1张: 总配额已满且当日已发过下跌类 → 再来下跌类挡住
    assert anomaly_quota_verdict(4, 1, is_down=True) is False


def test_anomaly_quota_down_passes_within_total():
    # 总配额未满时下跌类照常放行(不消耗"保底"语义)
    assert anomaly_quota_verdict(1, 1, is_down=True) is True
    assert anomaly_quota_verdict(2, 2, is_down=True) is True


def test_anomaly_quota_down_own_daily_cap():
    # 下跌类自身每日封顶3张防刷屏(即便构造出总计数偏低的状态也不放行)
    assert anomaly_quota_verdict(3, 3, is_down=True) is False
    assert anomaly_quota_verdict(0, ANOMALY_DOWN_MAX_DAILY, is_down=True) is False


def test_anomaly_quota_up_not_exempted_when_total_full_by_down():
    # 反向不豁免: 下跌类把总配额占满后, 上涨类没有保底
    assert anomaly_quota_verdict(3, 3, is_down=False) is False


def test_anomaly_quota_constants_and_rules():
    assert ANOMALY_MAX_DAILY == 3 and ANOMALY_DOWN_MAX_DAILY == 3
    # 下跌类口径固定: 急跌/跌停/开板(封板异动/涨停/急拉属上涨类)
    assert set(DOWN_ANOMALY_RULES) == {"plunge", "limit_down", "board_open"}


# ---------- 文案(基线 v1.1 Card): 关键事实出现 ----------

def test_near_high_msg_contains_facts():
    card = build_near_high_msg("平安银行", "000001", 11.85, 12.05, "2026-05-21")
    assert "接近前高 · 平安银行(000001)" in card.title
    assert card.family == "exit" and card.template == "green"
    assert "000001" in card.fallback
    assert "11.85" in card.fallback and "12.05" in card.fallback
    joined = str(card.elements)
    assert "距60日前高" in joined and "👉" in joined
    assert "▰" in joined or "▱" in joined            # card_kit.strength_bar 进度条
    # 价格长值下沉折叠
    folds = [e for e in card.elements if e.get("tag") == "collapsible_panel"]
    assert folds and "¥11.85" in str(folds[0]) and "¥12.05" in str(folds[0])
    assert "平安银行" in card.summary and "接近前高" in card.summary


def test_profit_protect_msg_contains_facts_and_advisory():
    card = build_profit_protect_msg(
        "测试股份", "000XXX", peak_gain=0.183, cur_gain=0.014,
        cost=10.0, advisory="动量突破回踩常是洗盘，留意而非急走", model_name="缩量突破")
    assert "盈利保护 · 测试股份(000XXX)" in card.title
    assert card.family == "exit" and card.template == "green"
    assert "18.3" in card.fallback and "1.4" in card.fallback
    # KPI 三栏 = 当前盈利/峰值盈利/已回吐, 价格长值(成本)下沉折叠
    kpi = card.elements[0]
    assert kpi["tag"] == "column_set" and len(kpi["columns"]) == 3
    kpi_text = str(kpi)
    assert "+1.4%" in kpi_text and "+18.3%" in kpi_text and "92%" in kpi_text
    assert "¥10.00" not in kpi_text
    folds = [e for e in card.elements if e.get("tag") == "collapsible_panel"]
    fold_text = str(folds)
    assert "¥10.00" in fold_text                       # 价格明细折叠
    assert "缩量突破" in fold_text and "洗盘" in fold_text   # 建仓信息折叠保留
