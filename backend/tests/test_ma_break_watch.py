# -*- coding: utf-8 -*-
"""尾盘破位警戒 纯函数单测: 连续破位天数回算 / 卡片文案 / 静音kind。"""
from datetime import date, timedelta

from backend.services.ma_break_watch import (
    break_streaks, build_watch_card, deepest_broken, MA_PERIODS,
)
from backend.services import push_pref as pp


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
