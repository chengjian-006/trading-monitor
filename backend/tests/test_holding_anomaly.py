"""持仓异动 纯函数单测: 涨跌停判定/封板封单/急涨急跌速率/封单松动分档/文案/计数节流。不连库不联网。"""
from backend.services.holding_anomaly import (
    limit_pct, is_limit_up, is_limit_down,
    at_limit_side, confirm_seal, seal_amount, seal_weaken_tier,
    surge_delta, fmt_amount, amount_rate, board_volume_ratio,
    build_limit_up_msg, build_limit_down_msg,
    build_surge_msg, build_plunge_msg,
    build_seal_weaken_msg, build_board_open_msg, build_board_anomaly_msg,
    PctHistory, SealPeakTracker, AmountHistory, SealBaseRate,
)
from backend.services.holding_guard import GuardThrottle


# ---------- limit_pct: 板别 + ST ----------

def test_limit_pct_main_board():
    assert limit_pct("600000", "浦发银行") == 0.10


def test_limit_pct_chinext_star():
    assert limit_pct("300750", "宁德时代") == 0.20
    assert limit_pct("688981", "中芯国际") == 0.20


def test_limit_pct_st_overrides_board():
    assert limit_pct("600519", "ST测试") == 0.05
    assert limit_pct("300001", "*ST测试") == 0.05


def test_limit_pct_bse():
    assert limit_pct("830799", "艾融软件") == 0.30


# ---------- is_limit_up / down 边界(含四舍五入容差) ----------

def test_is_limit_up_main_board():
    assert is_limit_up("600000", "浦发", 11.0, 10.0) is True
    assert is_limit_up("600000", "浦发", 10.9, 10.0) is False


def test_is_limit_up_chinext_20pct():
    assert is_limit_up("300001", "特锐德", 12.0, 10.0) is True
    assert is_limit_up("300001", "特锐德", 11.5, 10.0) is False


def test_is_limit_down_main_board():
    assert is_limit_down("600000", "浦发", 9.0, 10.0) is True
    assert is_limit_down("600000", "浦发", 9.1, 10.0) is False


# ---------- at_limit_side / confirm_seal / seal_amount ----------

def test_at_limit_side():
    assert at_limit_side("600000", "浦发", 11.0, 10.0) == "up"
    assert at_limit_side("600000", "浦发", 9.0, 10.0) == "down"
    assert at_limit_side("600000", "浦发", 10.5, 10.0) is None


def test_confirm_seal_up_needs_zero_ask():
    assert confirm_seal("up", ask1_vol=0, bid1_vol=2_000_000) is True   # 无人卖=封死
    assert confirm_seal("up", ask1_vol=5000, bid1_vol=2_000_000) is False  # 有卖单=未封死
    assert confirm_seal("up", ask1_vol=None, bid1_vol=None) is None        # 无五档=未知


def test_confirm_seal_down_needs_zero_bid():
    assert confirm_seal("down", ask1_vol=3000, bid1_vol=0) is True
    assert confirm_seal("down", ask1_vol=3000, bid1_vol=9000) is False


def test_seal_amount_up_uses_bid1():
    q = {"bid1_vol": 2_000_000, "bid1_price": 6.66, "ask1_vol": 0, "ask1_price": 0}
    assert abs(seal_amount(q, "up") - 13_320_000) < 1
    # 跌停取卖一
    q2 = {"ask1_vol": 5_000_000, "ask1_price": 5.40, "bid1_vol": 0, "bid1_price": 0}
    assert abs(seal_amount(q2, "down") - 27_000_000) < 1


def test_seal_amount_missing_returns_none():
    assert seal_amount({"bid1_vol": 0, "bid1_price": 6.66}, "up") is None
    assert seal_amount({}, "up") is None


# ---------- 封单松动分档 ----------

def test_seal_weaken_tier_50():
    assert seal_weaken_tier(1.2e8, 3.5e7) == 0.5   # 回落71% → 过50%档未过75%


def test_seal_weaken_tier_75():
    assert seal_weaken_tier(1e8, 2e7) == 0.75      # 回落80% → 过75%档


def test_seal_weaken_tier_none():
    assert seal_weaken_tier(1e8, 8e7) is None       # 仅回落20%


# ---------- 急涨/急跌速率 ----------

def test_surge_delta_enough_window():
    hist = [(0, 1.0), (60, 2.0), (120, 3.0), (180, 4.0)]
    assert surge_delta(hist, 180, 4.0, window=180) == 3.0   # 对比~180s前 1.0


def test_surge_delta_insufficient_history():
    hist = [(120, 3.0), (180, 4.0)]
    assert surge_delta(hist, 180, 4.0, window=180) is None   # 最早点才60s前, 不够窗


def test_surge_delta_negative():
    hist = [(0, 5.0), (180, 1.0)]
    assert surge_delta(hist, 180, 1.0, window=180) == -4.0


# ---------- 板上放量: amount_rate / board_volume_ratio ----------

def test_amount_rate_per_minute():
    # 60s 内累计成交额从 1000万 → 1300万 = 300万/60s → 每分钟 300万
    hist = [(0, 1e7), (30, 1.15e7), (60, 1.3e7)]
    assert abs(amount_rate(hist, 60, window=60) - 3e6) < 1


def test_amount_rate_window_filters_old():
    # now=120, window=60 → 只取 ts≥60 的点 (60→1.3e7, 120→1.45e7) = 150万/60s
    hist = [(0, 1e7), (60, 1.3e7), (120, 1.45e7)]
    assert abs(amount_rate(hist, 120, window=60) - 1.5e6) < 1


def test_amount_rate_insufficient():
    assert amount_rate([(60, 1e7)], 60, window=60) is None   # 窗内仅一点
    assert amount_rate([], 60) is None


def test_board_volume_ratio():
    assert board_volume_ratio(6e6, 3e6) == 2.0     # 当前是基线2倍
    assert board_volume_ratio(3e6, 0) is None      # 基线非正
    assert board_volume_ratio(None, 3e6) is None   # 当前缺失


# ---------- AmountHistory ----------

def test_amount_history_rate():
    h = AmountHistory()
    for ts, amt in [(0, 1e7), (30, 1.15e7), (60, 1.3e7)]:
        h.push("300319", ts, amt)
    assert abs(h.rate("300319", 60, window=60) - 3e6) < 1


def test_amount_history_prunes_old():
    h = AmountHistory(keep_sec=200)
    h.push("300319", 0, 1e7)
    h.push("300319", 1000, 5e7)   # 久远点应被剪
    assert all(ts >= 800 for ts, _ in h._hist["300319"])


# ---------- SealBaseRate ----------

def test_seal_base_rate_fixes_first_and_clears():
    t = SealBaseRate()
    assert t.ensure("300319", "up", 3e6) == 3e6     # 首次捕获
    assert t.ensure("300319", "up", 9e6) == 3e6     # 已固定, 不更新
    assert t.ensure("300319", "up", None) == 3e6    # None 不覆盖
    t.clear("300319", "up")
    assert t.ensure("300319", "up", 5e6) == 5e6     # 清后重捕获


def test_seal_base_rate_ignores_nonpositive():
    t = SealBaseRate()
    assert t.ensure("300319", "up", 0) is None      # 0 不捕获
    assert t.ensure("300319", "up", 4e6) == 4e6


# ---------- 封板异动文案: 三种原因 ----------

def test_board_anomaly_msg_weaken_only():
    msg = build_board_anomaly_msg("麦捷科技", "300319", "up", peak_amt=1.307e9, cur_amt=6.46e8)
    assert "封单大幅减少" in msg and "仍封涨停" in msg
    assert "放量" not in msg


def test_board_anomaly_msg_surge_only():
    msg = build_board_anomaly_msg("麦捷科技", "300319", "up", surge_ratio=2.3)
    assert "封板放量" in msg and "约2.3倍" in msg
    assert "封单 ¥" not in msg


def test_board_anomaly_msg_both():
    msg = build_board_anomaly_msg("麦捷科技", "300319", "down", peak_amt=1e8, cur_amt=4e7, surge_ratio=3.1)
    assert "封单减少 + 板上放量" in msg
    assert "仍封跌停" in msg and "约3.1倍" in msg


# ---------- fmt_amount ----------

def test_fmt_amount_yi():
    assert fmt_amount(1.2e8) == "1.2亿"


def test_fmt_amount_wan():
    assert fmt_amount(3.5e7) == "3,500万"


# ---------- PctHistory 跟踪器 ----------

def test_pct_history_push_and_delta():
    h = PctHistory()
    for ts, pct in [(0, 1.0), (60, 2.0), (120, 3.0), (180, 4.0)]:
        h.push("000001", ts, pct)
    assert h.delta("000001", 180, 4.0, window=180) == 3.0


def test_pct_history_prunes_old():
    h = PctHistory(keep_sec=200)
    h.push("000001", 0, 1.0)
    h.push("000001", 1000, 9.0)   # 久远点应被剪
    assert all(ts >= 800 for ts, _ in h._hist["000001"])


# ---------- SealPeakTracker ----------

def test_seal_peak_tracks_max_and_clears():
    t = SealPeakTracker()
    assert t.update("000001", "up", 5e7) == 5e7
    assert t.update("000001", "up", 1.2e8) == 1.2e8   # 取大
    assert t.update("000001", "up", 8e7) == 1.2e8      # 回落不降峰
    t.clear("000001", "up")
    assert t.update("000001", "up", 3e7) == 3e7        # 清后重置


# ---------- 计数节流(GuardThrottle 改计数版, 向后兼容) ----------

def test_throttle_count_limit_2():
    t = GuardThrottle()
    assert t.throttled("000001", "surge", "2026-06-16", limit=2) is False
    t.mark("000001", "surge", "2026-06-16")
    assert t.throttled("000001", "surge", "2026-06-16", limit=2) is False  # 1<2
    t.mark("000001", "surge", "2026-06-16")
    assert t.throttled("000001", "surge", "2026-06-16", limit=2) is True   # 2>=2


# ---------- 文案含关键字段 ----------

def test_limit_up_msg_fields():
    msg = build_limit_up_msg("京东方A", "000725", 6.66, 10.0, 1.2e8, 2.3, 1.86e9)
    assert "京东方A" in msg and "000725" in msg
    assert "涨停" in msg and "1.2亿" in msg


def test_limit_down_msg_fields():
    msg = build_limit_down_msg("京东方A", "000725", 5.40, -10.0, 2.7e7, 9.4e8)
    assert "跌停" in msg and "3,500万" not in msg  # 封单是2700万
    assert "2,700万" in msg


def test_surge_msg_fields():
    msg = build_surge_msg("京东方A", "000725", 5.2, 3, 6.40, 8.3)
    assert "拉升" in msg and "+5.2" in msg


def test_plunge_msg_fields():
    msg = build_plunge_msg("京东方A", "000725", -4.8, 3, 5.80, -6.2)
    assert "跳水" in msg and "-4.8" in msg


def test_seal_weaken_msg_fields():
    msg = build_seal_weaken_msg("京东方A", "000725", 1.2e8, 3.5e7, "up")
    assert "封单" in msg and "1.2亿" in msg and "3,500万" in msg


def test_board_open_msg_fields():
    msg = build_board_open_msg("京东方A", "000725", 6.50, 8.0, "up")
    assert "开板" in msg
