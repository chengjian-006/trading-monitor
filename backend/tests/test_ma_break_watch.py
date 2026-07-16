# -*- coding: utf-8 -*-
"""尾盘破位警戒 纯函数单测: 连续破位天数回算 / 卡片文案 / 静音kind。"""
from datetime import date, timedelta

from backend.services.ma_break_watch import (
    break_streaks, build_watch_card, deepest_broken, find_cost_line, MA_PERIODS, WATCH_MA,
)
from backend.services import push_pref as pp


def _tb(card):
    """基线 v1.1 改版后 build_watch_card 返回 Card; fallback 保留旧版行式全文(同源信息量)。"""
    return card.title, card.fallback


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
    title, body = _tb(build_watch_card(items))
    assert "跌破主力成本区" in body and "57.01" in body and "06-11" in body


def test_card_cost_break_alone_still_shown():
    # 只跌破成本线、未破任何均线, 也要入卡显示
    items = [{"name": "甲", "code": "000001", "price": 20.0, "pct": -1.0,
              "streaks": {5: 0, 10: 0, 20: 0},
              "cost_break": {"price": 21.0, "date": "2026-06-20"}, "actions_md": ""}]
    title, body = _tb(build_watch_card(items))
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
    title, body = _tb(build_watch_card(items))
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
    title, body = _tb(build_watch_card(items))
    assert "2只" in title
    assert "甲" in body and "乙" in body
    assert "[当日不提醒](http://x)" in body
    assert "破MA5·连续2日" in body                  # 甲只破MA5
    assert "破MA20·今日新破" in body                # 乙破5+10+20 → 只报MA20
    assert "破MA10" not in body                     # 乙的MA10被MA20吞掉


# ── 自选段(v1.7.606): 今日新跌破 MA20 ──

def _watch(name="丙", code="300123", price=8.55, pct=-3.2, dist=-1.8,
           model="回踩MA10", model_at="2026-06-18"):
    return {"name": name, "code": code, "price": price, "pct": pct,
            "dist_pct": dist, "model": model, "model_at": model_at}


def test_watch_ma_is_20_only():
    assert WATCH_MA == 20            # 观察票只判中期趋势线, 不判 MA5/MA10(噪声)


def test_card_watch_section_rendered():
    title, body = _tb(build_watch_card([], [_watch()]))
    assert "1只自选" in title
    assert "只报这一次" in body                      # 自选段语义: 只在转弱当天报
    assert "丙(300123)" in body and "8.55" in body
    assert "距MA20 -1.8%" in body
    assert "当初买点: 回踩MA10" in body and "2026-06-18" in body
    assert "非卖点" in body                          # 自选段是复盘提示不是卖点


def test_card_watch_without_buy_signal_falls_back():
    title, body = _tb(build_watch_card([], [_watch(model="", model_at="")]))
    assert "手工加入, 无买点信号记录" in body
    assert "当初买点" not in body


def test_card_both_sections_titled_and_separated():
    holds = [{"name": "甲", "code": "000001", "price": 10.0, "pct": -1.0,
              "streaks": {5: 2, 10: 0, 20: 0}, "actions_md": ""}]
    title, body = _tb(build_watch_card(holds, [_watch()]))
    assert "1只持仓" in title and "1只自选" in title
    assert "【持仓 · 每日尾盘复报直到收复】" in body
    assert "【自选 · 今日新跌破MA20 · 只报这一次】" in body
    assert "甲" in body and "丙" in body


def test_card_holdings_only_unchanged_when_no_watch():
    """回归: 没有自选破位时, 卡片不能冒出空的自选段。"""
    holds = [{"name": "甲", "code": "000001", "price": 10.0, "pct": -1.0,
              "streaks": {5: 2, 10: 0, 20: 0}, "actions_md": ""}]
    title, body = _tb(build_watch_card(holds))
    assert "1只持仓" in title and "自选" not in title
    assert "自选" not in body


def test_watch_section_caps_rows_and_discloses_omission():
    """大盘暴跌日可能几十只 —— 截断可以, 静默截断不行(飞书4000字符会无声吃掉尾部)。"""
    many = [_watch(name=f"股{i}", code=f"{i:06d}", dist=-float(i)) for i in range(1, 26)]
    title, body = _tb(build_watch_card([], many))
    assert "25只自选" in title                       # 标题报总数, 不报截断后的数
    assert "另有 10 只新破MA20未列出" in body        # 25 - 15 = 10
    assert "股1(000001)" in body                     # 破得最深的留下
    assert "股25(000025)" not in body                # 最浅的被截掉


def test_watch_section_no_omission_note_when_under_cap():
    title, body = _tb(build_watch_card([], [_watch()]))
    assert "未列出" not in body


def test_watch_filter_semantics_via_streaks():
    """自选段的入选口径 = streaks[20] == 1(今日新破)。0=没破, ≥2=早破了不该再喊。"""
    flat = [10.0] * 25                                  # 历史全平, MA20≈10
    assert break_streaks(flat, price=10.5)[20] == 0     # 站在线上 → 不入选
    assert break_streaks(flat, price=9.0)[20] == 1      # 昨天还在线上, 今天破 → 入选

    already = [10.0] * 23 + [9.0, 9.0]                  # 前两日已收在MA20下方
    assert break_streaks(already, price=9.0)[20] >= 2   # 老早破了 → 不再喊


# ── 基线 v1.1 Card 结构: 家族色 / 短表 / 折叠长值 / 动作行 ──

def _hold_item(**kw):
    base = {"name": "圣泉集团", "code": "605589", "price": 55.70, "pct": -2.9,
            "streaks": {5: 3, 10: 3, 20: 3},
            "cost_break": {"price": 57.01, "date": "2026-06-11"},
            "actions_md": "[当日不提醒](http://x)　·　[已卖出](http://y)"}
    base.update(kw)
    return base


def test_card_family_exit_green():
    card = build_watch_card([_hold_item()])
    assert card.family == "exit" and card.template == "green"


def test_card_elements_short_table_and_fold():
    card = build_watch_card([_hold_item()], [_watch()])
    joined = str(card.elements)
    # 持仓/自选各一张全短列表格(≤3列), 破位/当初买点短值入表
    tables = [e for e in card.elements if e.get("tag") == "markdown" and "| 股票 |" in e.get("content", "")]
    assert len(tables) == 2
    assert "| 股票 | 涨跌 | 破位 |" in tables[0]["content"]
    assert "MA20·3日+🔴成本线" in tables[0]["content"]
    assert "| 股票 | 涨跌 | 当初买点 |" in tables[1]["content"]
    # 长值(现价/距MA/成本线价格)下沉折叠, 表格里不出现
    assert "55.70" not in tables[0]["content"] and "57.01" not in tables[0]["content"]
    folds = [e for e in card.elements if e.get("tag") == "collapsible_panel"]
    fold_text = str(folds)
    assert "¥55.70" in fold_text and "¥57.01" in fold_text and "距MA20 -1.8%" in fold_text
    assert "👉" in joined                        # 行动建议区
    # 快捷动作行(逐票 snooze+已卖出)永远最后
    assert "当日不提醒" in card.elements[-1]["content"] and "已卖出" in card.elements[-1]["content"]


def test_card_summary_counts_sections():
    card = build_watch_card([_hold_item()], [_watch()])
    assert "1只持仓" in card.summary and "1只自选" in card.summary and "破位" in card.summary


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
