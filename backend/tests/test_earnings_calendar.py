# -*- coding: utf-8 -*-
"""财报披露日历 + 预增榜 纯函数 (v1.7.573; 基线 v1.1 结构卡). 不连库不联网。"""
from datetime import date

from backend.services.disclosure_reminder import (
    _current_report_date, REPORT_TYPE_CN, build_disclosure_card,
)
from backend.services.earnings_forecast_scan import _amp_txt, build_forecast_card
from backend.fetcher.earnings_data import GOOD_TYPES, BAD_TYPES


def test_current_report_date_picks_recent_quarter_end():
    assert _current_report_date(date(2026, 7, 3)) == "2026-06-30"    # 半年报窗口
    assert _current_report_date(date(2026, 2, 15)) == "2025-12-31"   # 年报窗口(去年Q4)
    assert _current_report_date(date(2026, 4, 20)) == "2026-03-31"   # 一季报窗口
    assert _current_report_date(date(2026, 10, 30)) == "2026-09-30"  # 三季报窗口


def test_current_report_date_january_falls_back_prev_year():
    assert _current_report_date(date(2026, 1, 10)) == "2025-12-31"


def test_amp_txt_range_and_single():
    assert _amp_txt(30.0, 50.0) == "+30%~+50%"
    assert _amp_txt(None, 40.0) == "+40%"
    assert _amp_txt(20.0, 20.0) == "+20%"
    assert _amp_txt(None, None) == "—"


def test_good_bad_types_disjoint_and_cover_key_labels():
    assert "预增" in GOOD_TYPES and "扭亏" in GOOD_TYPES
    assert "预减" in BAD_TYPES and "首亏" in BAD_TYPES
    assert GOOD_TYPES.isdisjoint(BAD_TYPES)


def test_report_type_cn_map():
    assert REPORT_TYPE_CN["2"] == "半年报"
    assert REPORT_TYPE_CN["4"] == "年报"


# ---------- 基线 v1.1 结构卡: 财报披露日历(情报蓝) ----------

def _disc_row(code, name, d, rtype="2", year="2026"):
    return {"code": code, "name": name, "appoint_date": d,
            "report_type": rtype, "report_year": year}


def test_build_disclosure_card_structure():
    rows = [_disc_row("600000", "浦发银行", "2026-07-18"),
            _disc_row("000001", "平安银行", "2026-07-20", rtype="4")]
    card = build_disclosure_card(rows, {"600000"}, today=date(2026, 7, 16))
    assert card.family == "intel" and card.template == "blue"
    assert "2只" in card.title
    table = card.elements[1]["content"]
    assert "| 股票 | 披露日 | 类型 |" in table
    assert "🔴浦发银行" in table and "07-18" in table and "半年报" in table and "年报" in table
    assert "600000" not in table                     # 代码是长值, 下沉折叠
    advice = next(e["content"] for e in card.elements
                  if e.get("tag") == "markdown" and "👉" in e.get("content", ""))
    assert "避险" in advice
    fold = card.elements[-1]
    assert fold["tag"] == "collapsible_panel"
    assert "600000" in fold["elements"][0]["content"]   # 全量+代码在折叠
    assert "600000" in card.fallback                    # 回退同源信息量
    assert "财报披露日历" in card.summary


def test_build_disclosure_card_over8_top5_plus_fold():
    rows = [_disc_row(f"60000{i}", f"股{i}", f"2026-07-{17 + i}") for i in range(10)]
    card = build_disclosure_card(rows, set(), today=date(2026, 7, 16))
    table = card.elements[1]["content"]
    assert table.count("\n") == 6                       # 表头2行 + Top5
    assert any("等 **10** 只" in e.get("content", "") for e in card.elements
               if e.get("tag") == "markdown")
    assert "股9" in card.elements[-1]["elements"][0]["content"]   # 全量在折叠


# ---------- 基线 v1.1 结构卡: 预增榜(机会红) ----------

def _fc(code, name, ptype="预增", lo=30.0, up=50.0, nd="2026-07-16 00:00:00"):
    return {"code": code, "name": name, "predict_type": ptype, "amp_lower": lo,
            "amp_upper": up, "notice_date": nd, "report_date": "2026-06-30"}


def test_build_forecast_card_structure():
    good = [_fc("600000", "浦发银行"), _fc("000001", "平安银行", ptype="扭亏", lo=None, up=120.0)]
    card = build_forecast_card(good, [good[0]], [good[1]], {"600000"})
    assert card.family == "opportunity" and card.template == "red"   # 基线家族表: 预增榜归机会族
    assert "2条" in card.title
    md_all = "\n".join(e.get("content", "") for e in card.elements if e.get("tag") == "markdown")
    assert "命中 **1** 只" in md_all
    assert "🔴浦发银行 预增" in md_all and "+30%~+50%" in md_all and "07-16" in md_all
    assert "平安银行 扭亏" in md_all and "+120%" in md_all
    assert "👉 **只做快进快出，别追高**" in md_all
    fold = card.elements[-1]
    assert fold["tag"] == "collapsible_panel"
    assert "600000" in fold["elements"][0]["content"]   # 代码等长值下沉折叠
    assert "快进快出" in card.fallback and "浦发银行" in card.fallback
    assert "预增榜" in card.summary and "命中1只" in card.summary


def test_build_forecast_card_others_over8_top5_plus_fold():
    others = [_fc(f"00000{i}", f"股{i}") for i in range(10)]
    card = build_forecast_card(others, [], others, set())
    tables = [e["content"] for e in card.elements
              if e.get("tag") == "markdown" and e["content"].startswith("| 股票")]
    assert len(tables) == 1 and tables[0].count("\n") == 6    # Top5
    assert any("等 **10** 只" in e.get("content", "") for e in card.elements
               if e.get("tag") == "markdown")
    assert "股9" in card.elements[-1]["elements"][0]["content"]
