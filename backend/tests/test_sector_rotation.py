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
