# -*- coding: utf-8 -*-
"""推送卡「所属行业 ｜ 关联热点」行渲染 (v1.7.561) — 行业与蹭热概念分离, 防误导。"""
from backend.services.notifier import _sector_line


def test_industry_and_hot_concept():
    sector = {"industry": "化学原料", "label": "AI算力·液冷", "status": "高潮", "emoji": "🔥",
              "today": 8, "seq": [8, 5, 8], "trend": "持平"}
    line = _sector_line(sector, bold=False)
    assert "所属行业：化学原料 ｜ 关联热点：AI算力·液冷" in line
    assert "今日涨停8家" in line
    assert "所属板块" not in line


def test_industry_only_when_no_hot_concept():
    # 概念标签映射不到热点题材时退化为只显示行业
    assert _sector_line({"industry": "化学原料", "label": None}, bold=False) == "📊 所属行业：化学原料"


def test_hot_concept_without_industry():
    sector = {"industry": "", "label": "机器人", "status": "启动", "emoji": "🚀",
              "today": 7, "seq": [3, 3, 7], "trend": "走强"}
    line = _sector_line(sector, bold=False)
    assert line.startswith("📊 关联热点：机器人")


def test_none_sector_empty():
    assert _sector_line(None, bold=True) == ""
