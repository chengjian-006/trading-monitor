"""板块弱转强/强转弱预判 纯逻辑单测 (v1.7.x).

覆盖: 题材聚合 / 斜率 / 盘中状态分类 / 转折判定 / 终结判定 / 次日预测。
纯函数, 不连库不打网。阈值是启发式草案, 测试锁的是"规则方向", 非已验证胜率。
"""
from backend.services import sector_rotation as sr


# ── aggregate_themes ──
def test_aggregate_themes():
    boards = [
        {"reason": "电力+业绩", "name": "大唐发电", "height": 2, "open_times": 0},
        {"reason": "电力", "name": "华银电力", "height": 1, "open_times": 1},
        {"reason": "电力+风电", "name": "金山股份", "height": 1, "open_times": 0},
        {"reason": "光通信", "name": "亨通光电", "height": 3, "open_times": 0},
        {"reason": "", "name": "无题材", "height": 1, "open_times": 0},   # 无 reason 跳过
    ]
    agg = sr.aggregate_themes(boards)
    assert set(agg.keys()) == {"电力", "光通信"}
    power = agg["电力"]
    assert power["limit_up"] == 3
    assert power["max_height"] == 2
    assert power["first_board"] == 2     # 华银+金山
    assert power["broken"] == 1          # 华银 open_times>0
    assert "大唐发电" in power["samples"]
    assert agg["光通信"]["max_height"] == 3


def test_aggregate_empty():
    assert sr.aggregate_themes([]) == {}
    assert sr.aggregate_themes(None) == {}


# ── compute_slope ──
def test_compute_slope():
    assert sr.compute_slope([1, 2, 3, 5]) == 4        # 5 - 1 (window=3, ref=idx0)
    assert sr.compute_slope([0, 0, 5]) == 5
    assert sr.compute_slope([5, 3, 2]) == -3          # 降温
    assert sr.compute_slope([]) == 0
    assert sr.compute_slope([7]) == 0


# ── classify_intraday ──
def test_classify_start_weak_to_strong():
    # 早盘冷(0,1) → 快速抬升到4 = 启动
    assert sr.classify_intraday([0, 1, 2, 4]) == "启动"


def test_classify_ebb_strong_to_weak():
    # 当日峰值6, 现回落到2 (≤6*0.5) = 退潮
    assert sr.classify_intraday([3, 6, 5, 2]) == "退潮"
    # 高位但封板大面积松动也算退潮
    assert sr.classify_intraday([5, 6, 5], {"broken": 4}) == "退潮"


def test_classify_climax_and_cold():
    assert sr.classify_intraday([4, 5, 6]) == "高潮"     # 高位仍上行
    assert sr.classify_intraday([0, 0, 1]) == "冷"
    assert sr.classify_intraday([]) == "冷"


# ── detect_transition ──
def test_detect_transition():
    assert sr.detect_transition("冷", "启动") == "weak_to_strong"
    assert sr.detect_transition("高潮", "退潮") == "strong_to_weak"
    assert sr.detect_transition("启动", "启动") is None       # 同状态不重复推
    assert sr.detect_transition(None, "高潮") is None         # 进高潮不推(非转折)
    assert sr.detect_transition("退潮", "冷") is None


# ── is_theme_ended ──
def test_is_theme_ended():
    assert sr.is_theme_ended([0, 0, 0, 0, 0]) is True
    assert sr.is_theme_ended([2, 0, 0, 0, 0, 0]) is True      # 只看最近5日(末5全0)
    assert sr.is_theme_ended([0, 0, 0, 1, 0]) is False        # 中间有涨停
    assert sr.is_theme_ended([0, 0, 0, 0], days=5) is False   # 样本不足5
    assert sr.is_theme_ended([0, 0, 0, 0, 0], today_strong=True) is False  # 今日有强势不判终结


# ── predict_next_day ──
def test_predict_ended():
    p = sr.predict_next_day([0, 0, 0, 0, 0], {"max_height": 0, "broken": 0})
    assert p["direction"] == "疑似终结"


def test_predict_strong_to_weak():
    # 近3日均高(5,6,5=5.3), 今日3较昨5回落 → 强转弱候选
    p = sr.predict_next_day([5, 6, 5, 3])
    assert p["direction"] == "强转弱候选"


def test_predict_weak_to_strong():
    # 近3日低迷(0,1,1), 今日4回升 → 弱转强候选
    p = sr.predict_next_day([0, 1, 1, 4])
    assert p["direction"] == "弱转强候选"


def test_predict_strong_continue():
    # 今日6高位且≥昨6 → 强势延续 (注: 近3日均也高但今日未回落)
    p = sr.predict_next_day([4, 5, 6, 6])
    assert p["direction"] == "强势延续"


def test_predict_weak_continue():
    p = sr.predict_next_day([1, 0, 1, 1])
    assert p["direction"] == "弱势延续"


def test_predict_empty():
    assert sr.predict_next_day([])["direction"] == "中性"


# ── 日基准 classify_daily (带昨日基准的按日口径) ──

def test_daily_weak_to_strong_user_example():
    # 用户例: 昨3家→今盘中10家 → 启动(弱转强)
    assert sr.classify_daily(yest=3, cur=10) == "启动"


def test_daily_weak_to_strong_boundary():
    # 昨3→今6(=昨+3, ≥4家) → 启动
    assert sr.classify_daily(yest=3, cur=6) == "启动"
    # 昨3→今5(仅+2, 未达+3) → 非启动; 今5≥4归高潮, 不触发弱转强推送
    assert sr.classify_daily(yest=3, cur=5) == "高潮"
    # 昨4(已热)→今10: 昨非冷, 不算弱转强, 归高潮(延续)
    assert sr.classify_daily(yest=4, cur=10) == "高潮"


def test_daily_strong_to_weak_afternoon_only():
    # 昨8家→今3家(≤8×0.5): 下午判 退潮; 早盘不判(防未封满)
    assert sr.classify_daily(yest=8, cur=3, is_afternoon=True) == "退潮"
    assert sr.classify_daily(yest=8, cur=3, is_afternoon=False) != "退潮"


def test_daily_strong_to_weak_needs_halve():
    # 昨8→今5(>8×0.5) 下午 → 未腰斩, 不算退潮(今5仍≥4归高潮)
    assert sr.classify_daily(yest=8, cur=5, is_afternoon=True) == "高潮"


def test_daily_cold():
    assert sr.classify_daily(yest=1, cur=1) == "冷"
    assert sr.classify_daily(yest=0, cur=0) == "冷"


def test_daily_flat_is_not_cold():
    """回归: 有涨停但未超昨日 = 持平, 不是"冷"。

    旧版兜底把 2~3 家涨停也归"冷", 09:45 推送出现「涨停扎堆题材(上榜门槛≥2家) · 商业航天·冷」
    的自相矛盾。"冷"只保留给涨停 ≤1 家。
    """
    assert sr.classify_daily(yest=3, cur=3) == "持平"   # 商业航天实例: 3家涨停, 与昨持平
    assert sr.classify_daily(yest=5, cur=3) == "持平"   # 早盘(未到下午)不判退潮, 也不该叫冷
    assert sr.classify_daily(yest=3, cur=2) == "持平"
    assert sr.classify_daily(yest=3, cur=1) == "冷"     # 掉到1家才是真冷


def test_intraday_flat_is_not_cold():
    # 日内口径同步: 3家涨停横住(斜率≤0)不该叫"冷"
    assert sr.classify_intraday([3, 3, 3]) == "持平"
    assert sr.classify_intraday([1, 1, 1]) == "冷"


# ── 回归: 昨日基准的日期格式归一(theme_heat 存紧凑 YYYYMMDD, today 传带连字符) ──
def test_yest_baseline_date_format_normalize():
    """曾因 '20260630' < '2026-07-01' 恒 False → 昨日基准全空 → 全题材误显示昨0。

    theme_heat.trade_date 是紧凑格式, today 是带连字符; _load_yest_baseline 必须去连字符后比。
    """
    import asyncio
    from backend.services import sector_rotation_scanner as scn

    fake_rows = [
        {"trade_date": "20260630", "theme": "机器人", "limit_up_count": 8},
        {"trade_date": "20260630", "theme": "人形机器人", "limit_up_count": 11},
        {"trade_date": "20260629", "theme": "机器人", "limit_up_count": 5},
        {"trade_date": "20260701", "theme": "机器人", "limit_up_count": 6},  # 今日, 不应入基准
    ]

    async def _fake_get_theme_heat(days=8):
        return fake_rows

    orig = scn.repository.get_theme_heat
    scn.repository.get_theme_heat = _fake_get_theme_heat
    scn._yest_baseline.clear()
    try:
        base = asyncio.run(scn._load_yest_baseline("2026-07-01"))
    finally:
        scn.repository.get_theme_heat = orig
        scn._yest_baseline.clear()

    # 昨日=20260630, 机器人应为 8(非 0)、人形机器人 11; 今日 20260701 的 6 不能混进来
    assert base.get("机器人") == 8
    assert base.get("人形机器人") == 11
