# -*- coding: utf-8 -*-
"""尾盘破位警戒 纯函数单测: 连续破位天数回算 / 卡片文案 / 静音kind。"""
from datetime import date, timedelta

from backend.services.ma_break_watch import (
    break_streaks, build_watch_card, deepest_broken, find_cost_line, MA_PERIODS,
)
from backend.services import push_pref as pp


# ── find_cost_line: 识别最近放量起涨点成本线(用户0705逻辑) ──

def _flat_then_start():
    # 20日横盘(收10/量100) + 第21日放量2倍突破新高上涨(起涨点, low=10.5) + 后续温和
    closes = [10.0] * 20 + [11.0, 11.5, 11.2]
    highs = [10.2] * 20 + [11.3, 11.8, 11.5]
    lows = [9.8] * 20 + [10.5, 11.0, 10.9]
    vols = [100.0] * 20 + [300.0, 150.0, 120.0]
    return closes, highs, lows, vols


def test_find_cost_line_identifies_start():
    c, h, l, v = _flat_then_start()
    r = find_cost_line(c, h, l, v)
    assert r is not None
    assert abs(r["low"] - 10.5) < 1e-6      # 起涨点K线最低价=成本线
    assert r["idx"] == 20                     # 第21根(idx20)是放量起涨点


def test_find_cost_line_none_when_no_volume_surge():
    # 全程平量, 无放量起涨点
    c = [10.0] * 40; h = [10.2] * 40; l = [9.8] * 40; v = [100.0] * 40
    assert find_cost_line(c, h, l, v) is None


def test_find_cost_line_picks_most_recent():
    # 两个起涨点, 取最近的
    c = [10.0]*20 + [11.0] + [11.0]*10 + [13.0] + [13.0]*3
    h = [10.2]*20 + [11.3] + [11.2]*10 + [13.3] + [13.2]*3
    l = [9.8]*20 + [10.5] + [10.8]*10 + [12.5] + [12.8]*3
    v = [100.0]*20 + [300.0] + [100.0]*10 + [300.0] + [100.0]*3
    r = find_cost_line(c, h, l, v)
    assert r["idx"] == 31                      # 最近那个起涨点(13.0), 非早先的11.0
    assert abs(r["low"] - 12.5) < 1e-6


# ── build_watch_card: 主力成本线维度 ──

def test_card_shows_cost_break():
    items = [{"name": "圣泉集团", "code": "605589", "price": 55.70, "pct": -2.9,
              "streaks": {5: 3, 10: 3, 20: 3},
              "cost_break": {"price": 57.01, "date": "2026-06-11"}, "actions_md": ""}]
    title, body = build_watch_card(items)
    assert "跌破主力成本区" in body and "57.01" in body and "06-11" in body


def test_card_cost_break_alone_still_shown():
    # 只跌破成本线、未破任何均线, 也要入卡显示
    items = [{"name": "甲", "code": "000001", "price": 20.0, "pct": -1.0,
              "streaks": {5: 0, 10: 0, 20: 0},
              "cost_break": {"price": 21.0, "date": "2026-06-20"}, "actions_md": ""}]
    title, body = build_watch_card(items)
    assert "甲" in body and "跌破主力成本区" in body


# ── break_streaks: 连续破位天数(含今日, 今日用尾盘现价) ──

def test_no_break_returns_zero():
    closes = [10.0] * 30
    s = break_streaks(closes, price=10.5)      # 今日在所有均线上方
    assert s == {5: 0, 10: 0, 20: 0}


def test_first_day_break_ma5_only():
    closes = [10.0] * 30                        # 历史全平, 均线=10
    s = break_streaks(closes, price=9.0)        # 今日9.0: ma5_today=9.8, ma10=9.9, ma20=9.95 全破
    assert s[5] == 1 and s[10] == 1 and s[20] == 1


def test_streak_three_days():
    closes = [10.0] * 28 + [9.0, 9.0]           # 昨/前日已收9.0(破), 今日9.0
    s = break_streaks(closes, price=9.0)
    assert s[5] == 3
    assert s[10] == 3


def test_recovered_middle_resets_streak():
    # 前天破(9.0), 昨天收复(10.5 高于当日ma5), 今天又破 → 连续只算今日1天
    closes = [10.0] * 28 + [9.0, 10.5]
    s = break_streaks(closes, price=9.0)
    assert s[5] == 1


def test_today_not_below_gives_zero_even_if_history_below():
    closes = [10.0] * 28 + [9.0, 9.0]           # 历史连续破
    s = break_streaks(closes, price=10.8)       # 今日站回
    assert s[5] == 0 and s[10] == 0


def test_insufficient_history_skips_period():
    closes = [10.0] * 8                          # 不够 MA10/MA20
    s = break_streaks(closes, price=9.0)
    assert s[5] >= 1
    assert s[10] == 0 and s[20] == 0


def test_periods_constant():
    assert MA_PERIODS == (5, 10, 20)


# ── deepest_broken: 同时破多档只报最深 ──

def test_deepest_broken_picks_longest_period():
    assert deepest_broken({5: 3, 10: 1, 20: 0}) == 10   # 破5+10 只报10
    assert deepest_broken({5: 4, 10: 4, 20: 2}) == 20   # 破到20 只报20
    assert deepest_broken({5: 2, 10: 0, 20: 0}) == 5    # 只破5
    assert deepest_broken({5: 0, 10: 0, 20: 0}) is None


# ── build_watch_card: 文案(只报最深破位那档) ──

def test_card_reports_only_deepest_ma():
    # 破MA5(连3日)+MA10(今日新破) → 只报最深MA10, MA5那行不出现
    items = [
        {"name": "圣泉集团", "code": "605589", "price": 28.30, "pct": -2.1,
         "streaks": {5: 3, 10: 1, 20: 0}, "actions_md": ""},
    ]
    title, body = build_watch_card(items)
    assert "尾盘破位警戒" in title and "1只" in title
    assert "圣泉集团" in body and "605589" in body
    assert "破MA10·今日新破" in body               # 最深档=MA10, 用MA10自己的连续天数
    assert "破MA5" not in body                     # 破MA5被最深档吞掉, 不单列
    assert "破MA20" not in body                    # MA20未破


def test_card_multiple_stocks_and_actions():
    items = [
        {"name": "甲", "code": "000001", "price": 10.0, "pct": -1.0,
         "streaks": {5: 2, 10: 0, 20: 0}, "actions_md": "[当日不提醒](http://x)"},
        {"name": "乙", "code": "000002", "price": 20.0, "pct": -3.0,
         "streaks": {5: 1, 10: 1, 20: 1}, "actions_md": ""},
    ]
    title, body = build_watch_card(items)
    assert "2只" in title
    assert "甲" in body and "乙" in body
    assert "[当日不提醒](http://x)" in body
    assert "破MA5·连续2日" in body                  # 甲只破MA5
    assert "破MA20·今日新破" in body                # 乙破5+10+20 → 只报MA20
    assert "破MA10" not in body                     # 乙的MA10被MA20吞掉


# ── push_pref: ma_watch_snooze kind ──

def test_ma_watch_snooze_kind_registered():
    assert "ma_watch_snooze" in pp.VALID_KINDS


def test_ma_watch_snooze_until_for():
    today = date(2026, 7, 6)
    assert pp.until_for("ma_watch_snooze", 1, today) == today
    assert pp.until_for("ma_watch_snooze", 3, today) == today + timedelta(days=2)


def test_ma_watch_snooze_active():
    prefs = [{"kind": "ma_watch_snooze", "target": "605589"},
             {"kind": "stop_snooze", "target": "000001"}]
    assert pp.ma_watch_snooze_active(prefs, "605589") is True
    assert pp.ma_watch_snooze_active(prefs, "000001") is False   # stop_snooze 不串台
    assert pp.ma_watch_snooze_active(prefs, "600000") is False


def test_ma_watch_actions_md_signed_links():
    md = pp.build_ma_watch_actions_md("http://site", 1, "605589")
    assert "ma_watch_snooze" in md and "605589" in md
    assert "当日不提醒" in md and "本周不提醒" in md
