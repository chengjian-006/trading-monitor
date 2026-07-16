# backend/tests/test_holding_brief.py
"""持仓研判晚报 纯函数单测: 持仓态分类器。不连库不联网。"""
import pandas as pd

from backend.services.signal_engine_indicators import compute_indicators
from backend.services.holding_brief import (
    classify_holding_state,
    aggregate_state_fwd_dist,
    stock_fwd_records,
    fmt_fwd,
    build_brief_prompt,
    parse_ai_verdicts,
    render_wechat_text,
    build_lark_elements,
    build_brief_card,
)


def _d(closes, vols=None):
    """构造升序日K并算指标。closes 决定均线关系; vols 控量(默认平量)。
    open=high=low=close(无影线), 需要影线/缺口的测试单独构造。"""
    n = len(closes)
    vols = vols if vols is not None else [1000.0] * n
    dates = pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d").tolist()
    df = pd.DataFrame({
        "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": vols,
    })
    return compute_indicators(df)


# ---------- classify_holding_state: 跌破MA20 ----------

def test_classify_break_ma20():
    # 长期在 10 一线, 最后一根跌到 9.0 < MA20(≈9.95) → 跌破MA20
    closes = [10.0] * 40 + [9.0]
    assert classify_holding_state(_d(closes)) == "跌破MA20"


# ---------- classify_holding_state: 多头站均线 ----------

def test_classify_bull_aligned():
    # 稳定上行: close > ma5 > ma10 > ma20 多头排列, 现价站在 ma5 上方
    closes = [10.0 + 0.2 * i for i in range(60)]
    assert classify_holding_state(_d(closes)) == "多头站均线"


# ---------- classify_holding_state: 回踩支撑 ----------

def test_classify_pullback_to_support():
    # 上行趋势(ma10>ma20)中回踩: 现价跌回 ma5 下方、贴近 ma10、仍站 ma20 上
    closes = [10.0 + 0.05 * i for i in range(55)] + [12.4]
    assert classify_holding_state(_d(closes)) == "回踩支撑"


# ---------- classify_holding_state: 高位放量滞涨 ----------

def test_classify_high_stall():
    # 持续拉升到远离 ma20 的高位(+17%), 最后一根放量(3x)却收平(滞涨)
    closes = [7.0 + 0.35 * i for i in range(40)] + [20.65]
    vols = [1000.0] * 40 + [3000.0]
    assert classify_holding_state(_d(closes, vols)) == "高位放量滞涨"


# ---------- classify_holding_state: 缩量整理 ----------

def test_classify_low_vol_consolidation():
    # 横盘一线 + 末段地量(vol_ratio<0.7), 不破位不放量
    closes = [10.0] * 40
    vols = [1000.0] * 35 + [300.0] * 5
    assert classify_holding_state(_d(closes, vols)) == "缩量整理"


def test_classify_insufficient_bars_returns_other():
    # 不足 20 根 → MA20 为 NaN, 不报任何明确态, 优雅归"其他"
    closes = [10.0, 10.5, 11.0, 10.8, 11.2]
    assert classify_holding_state(_d(closes)) == "其他"


# ---------- aggregate_state_fwd_dist: 按态聚合 T+1/T+3 前向分布 ----------

def test_aggregate_fwd_dist_basic():
    records = [
        {"state": "多头站均线", "fwd1": 0.02, "fwd3": 0.05},
        {"state": "多头站均线", "fwd1": 0.01, "fwd3": 0.03},
        {"state": "多头站均线", "fwd1": -0.01, "fwd3": -0.02},
        {"state": "多头站均线", "fwd1": 0.03, "fwd3": 0.04},
        {"state": "跌破MA20", "fwd1": -0.02, "fwd3": -0.05},
        {"state": "跌破MA20", "fwd1": -0.01, "fwd3": 0.01},
    ]
    out = aggregate_state_fwd_dist(records, min_sample=1)
    assert out["多头站均线"]["n"] == 4
    assert out["多头站均线"]["up_rate_1"] == 75.0       # 3/4 上涨
    assert out["多头站均线"]["median_1"] == 1.5         # median([-1,1,2,3]%)
    assert out["跌破MA20"]["n"] == 2
    assert out["跌破MA20"]["up_rate_1"] == 0.0


def test_aggregate_fwd_dist_skips_below_min_sample():
    # 样本不足(默认<30)的态不出统计, 避免小样本噪声当客观概率
    records = [{"state": "其他", "fwd1": 0.01, "fwd3": 0.02}] * 5
    out = aggregate_state_fwd_dist(records, min_sample=30)
    assert "其他" not in out


# ---------- stock_fwd_records: 单票逐bar取 T+1/T+3 前向收益(索引正确性) ----------

def test_stock_fwd_records_forward_indexing():
    import pytest
    # 末4根 close = 10,11,12,13.2 → 最后一条记录在 i=n-4:
    #   fwd1 = 11/10-1 = +10%, fwd3 = 13.2/10-1 = +32%
    closes = [10.0] * 26 + [10.0, 11.0, 12.0, 13.2]
    recs = stock_fwd_records(_d(closes))
    assert recs, "应至少产出一条记录"
    last = recs[-1]
    assert set(last.keys()) == {"state", "fwd1", "fwd3"}
    assert last["fwd1"] == pytest.approx(0.10, abs=1e-9)
    assert last["fwd3"] == pytest.approx(0.32, abs=1e-9)


def test_stock_fwd_records_drops_tail_without_3_forward_bars():
    # 最后3根没有完整 T+3 前向窗 → 不产出记录(避免用未来不全的数据)
    closes = [10.0 + 0.01 * i for i in range(25)]
    recs = stock_fwd_records(_d(closes))
    n = len(closes)
    # 末记录对应的 i 必须满足 i+3 <= n-1
    # 用 fwd3 反推: 最后一条不应触达数组尾部之外(此处仅断言不抛+条数受限)
    assert all("fwd3" in r for r in recs)
    assert len(recs) <= n - 3


# ---------- 报告半边: 前向分布格式化 ----------

def test_fmt_fwd_with_data():
    fwd = {"n": 420, "up_rate_1": 58.0, "median_1": 1.4, "up_rate_3": 61.0, "median_3": 2.2}
    s = fmt_fwd("回踩支撑", fwd)
    assert "回踩支撑" in s and "58.0%" in s and "+1.4%" in s and "n420" in s


def test_fmt_fwd_empty_when_no_sample():
    assert fmt_fwd("其他", None) == ""
    assert fmt_fwd("其他", {"n": 0}) == ""


# ---------- 报告半边: AI建议解析 ----------

def test_parse_ai_verdicts_fenced_json():
    text = """好的，研判如下：
```json
[{"code":"000725","action":"持有","target":7.6,"stop":6.7,"reason":"量能延续板块第二强"},
 {"code":"002463","action":"减仓","target":150,"stop":140,"reason":"高位放量滞涨"}]
```
"""
    out = parse_ai_verdicts(text)
    assert set(out.keys()) == {"000725", "002463"}
    assert out["000725"]["action"] == "持有"
    assert out["000725"]["target"] == 7.6
    assert out["002463"]["action"] == "减仓"


def test_parse_ai_verdicts_garbage_returns_empty():
    assert parse_ai_verdicts("模型超时了，无法生成") == {}
    assert parse_ai_verdicts("") == {}


def test_parse_ai_verdicts_skips_bad_action():
    # action 不在 持有/加仓/减仓/清仓 → 略过该股(不污染)
    out = parse_ai_verdicts('[{"code":"600000","action":"观望","reason":"x"}]')
    assert out == {}


# ---------- 报告半边: 渲染 ----------

def _payload(**kw):
    base = {"code": "000725", "name": "京东方A", "price": 7.18, "pct_change": 2.3,
            "hold_days": 5, "entry_model_name": "回踩MA10", "profit_pct": 12.0, "state": "多头站均线"}
    base.update(kw)
    return base


def test_render_wechat_empty_is_idle():
    assert "空仓" in render_wechat_text([], {}, "震荡")


def test_render_wechat_contains_facts_and_advice():
    payloads = [_payload()]
    verdicts = {"000725": {"action": "持有", "target": 7.6, "stop": 6.7, "reason": "量能延续"}}
    txt = render_wechat_text(payloads, verdicts, "震荡偏强")
    assert "京东方A" in txt and "000725" in txt
    assert "🟢持有" in txt and "7.60" in txt and "6.70" in txt
    assert "量能延续" in txt
    assert "仅供参考" in txt


def test_render_wechat_handles_missing_verdict():
    txt = render_wechat_text([_payload()], {}, "震荡")
    assert "AI研判未生成" in txt


def test_build_lark_elements_empty_and_filled():
    assert len(build_lark_elements([], {}, "震荡")) == 1   # 空仓一条
    els = build_lark_elements([_payload()], {"000725": {"action": "减仓", "target": 7.0, "stop": 6.5, "reason": "滞涨"}}, "震荡")
    # 移动优化(v1.7.581): 表格改逐股换行文本块(单元格塞多字段手机端字符级截断), 断言建议/理由进 markdown 行、无表格竖线
    assert any(e.get("tag") == "markdown" and "减仓" in e.get("content", "")
               and "滞涨" in e.get("content", "") and "|" not in e.get("content", "") for e in els)


def test_build_brief_prompt_carries_data():
    system, user = build_brief_prompt([_payload()], "震荡偏强")
    assert "JSON" in system and "持有/加仓/减仓/清仓" in system
    assert "000725" in user and "震荡偏强" in user


# ---------- 基线 v1.1 结构卡 ----------

def test_build_brief_card_structure():
    payloads = [_payload()]
    verdicts = {"000725": {"action": "减仓", "target": 7.0, "stop": 6.5, "reason": "滞涨"}}
    card = build_brief_card(payloads, verdicts, "震荡偏强")
    assert card.family == "intel" and card.template == "blue"
    # 结论区 = KPI 三栏(持仓/建议减清/次日环境)
    kpi = card.elements[0]
    assert kpi["tag"] == "column_set" and len(kpi["columns"]) == 3
    assert "1只" in kpi["columns"][1]["elements"][0]["content"]   # 减/清 1 只
    # 逐股数据区仍是换行文本块(移动优化), 无表格竖线
    assert any(e.get("tag") == "markdown" and "减仓" in e.get("content", "")
               and "滞涨" in e.get("content", "") and "|" not in e.get("content", "")
               for e in card.elements)
    # 行动建议 + 免责折叠(长值/口径下沉)
    assert any(e.get("tag") == "markdown" and "👉" in e.get("content", "") for e in card.elements)
    assert card.elements[-1]["tag"] == "collapsible_panel"
    # 锁屏摘要标配
    assert "持仓研判晚报" in card.summary and "减/清1只" in card.summary
    assert "京东方A" in card.fallback   # 回退同源信息


def test_build_brief_card_empty_holdings():
    card = build_brief_card([], {}, "震荡")
    assert card.family == "intel"
    assert "空仓" in card.elements[0]["content"]
    assert "今日空仓" in card.summary
