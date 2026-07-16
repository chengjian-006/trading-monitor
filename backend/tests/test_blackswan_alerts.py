# -*- coding: utf-8 -*-
"""黑天鹅合并卡(基线 v1.1)构卡单测: 风险家族橙卡 🦢 + 两区域全短列表+折叠 + 锁屏摘要。不联网。"""
from backend.services.blackswan_alerts import _build_card


def _ann(code="002217", name="合力泰"):
    return {"code": code, "name": name, "title": "关于收到证监会立案告知书的公告",
            "tags": "立案调查", "date": "2026-06-01", "url": "u"}


def _fin(code="300001", name="特锐德", score=75):
    return {"code": code, "name": name, "year": "2025", "score": score,
            "flags": [{"label": "连续亏损", "strength": "strong", "brief": ""},
                      {"label": "高杠杆", "strength": "medium", "brief": "85%"}]}


def test_build_card_both_regions():
    verdicts = {"002217": {"severity": "高", "emoji": "🔴", "text": "立案调查重大利空"}}
    card = _build_card([_ann()], [_fin()], verdicts)
    # 信封: 🦢 标题公式 + 风险家族橙 + 彩签 + 锁屏摘要(命中N只)
    assert card.title == "🦢 黑天鹅预警 · 自选2只"
    assert card.family == "risk" and card.template == "orange"   # 改掉旧默认蓝
    assert card.tags == [("黑天鹅", "orange")]
    assert "自选2只" in card.summary and "公告1条" in card.summary and "财务红旗1只" in card.summary

    md = "\n".join(el.get("content", "") for el in card.elements if el.get("tag") == "markdown")
    # 两区域常驻 + 全短列表
    assert "🚨 风险公告（1）" in md and "📉 财务红旗（1）" in md
    assert "| 股票 | 类型 | 要点 |" in md
    assert "| 股票 | 级别 | 要点 |" in md
    assert "🔴高" in md                     # AI 严重度进表(短值)
    assert "立案告知书" not in md           # 长公告标题不进表(下沉折叠)
    assert "👉" in md                       # 行动建议区
    # 长值折叠区: 公告摘要+AI研判句 / 红旗全量明细
    folds = [el for el in card.elements if el.get("tag") == "collapsible_panel"]
    assert len(folds) == 2
    fold_md = "\n".join(f["elements"][0]["content"] for f in folds)
    assert "立案告知书" in fold_md and "立案调查重大利空" in fold_md
    assert "连续亏损" in fold_md and "高杠杆85%" in fold_md
    # fallback 同源信息量
    assert "合力泰" in card.fallback and "特锐德" in card.fallback and "纯提示" in card.fallback


def test_build_card_empty_region_stays_resident():
    card = _build_card([], [_fin()])
    md = "\n".join(el.get("content", "") for el in card.elements if el.get("tag") == "markdown")
    assert "本次无新增" in md               # 空区域保持结构
    assert "🚨 风险公告（0）" in md
    assert card.title == "🦢 黑天鹅预警 · 自选1只"
