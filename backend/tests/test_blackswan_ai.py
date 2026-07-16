# backend/tests/test_blackswan_ai.py
"""黑天鹅 AI 逐股研判 纯函数单测: PDF解析/分组/prompt/解析。不联网。"""
import fitz  # PyMuPDF

import asyncio

from backend.services.blackswan_ai import (
    extract_pdf_text,
    group_hits_by_stock,
    build_risk_prompt,
    parse_risk_verdict,
    generate_risk_verdicts,
)


def _make_pdf(text: str) -> bytes:
    """造一个含给定文本的 PDF, 返回字节。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontname="china-s")  # 内置简体中文字体, 否则中文成乱码
    data = doc.tobytes()
    doc.close()
    return data


# ---------- extract_pdf_text: 解析PDF正文 ----------

def test_extract_pdf_text_returns_content():
    pdf = _make_pdf("关于公司及实际控制人收到证监会立案告知书的公告")
    out = extract_pdf_text(pdf)
    assert "立案告知书" in out


def test_extract_pdf_text_truncates():
    pdf = _make_pdf("X" * 9000)
    out = extract_pdf_text(pdf, max_chars=4000)
    assert len(out) <= 4000


def test_extract_pdf_text_bad_bytes_returns_empty():
    assert extract_pdf_text(b"not a real pdf") == ""
    assert extract_pdf_text(b"") == ""


# ---------- group_hits_by_stock: 命中按股分组 + 每股限 N 条 ----------

def _hit(code, name, title, tags, date="2026-06-01", url="u"):
    return {"code": code, "name": name, "title": title, "tags": tags, "date": date, "url": url}


def test_group_hits_by_stock_groups_and_keeps_name():
    hits = [
        _hit("002217", "合力泰", "立案告知书", "立案调查"),
        _hit("002217", "合力泰", "问询函回复", "交易所问询函"),
        _hit("600110", "诺德股份", "减持公告", "减持"),
    ]
    out = group_hits_by_stock(hits)
    assert set(out.keys()) == {"002217", "600110"}
    assert out["002217"]["name"] == "合力泰"
    assert len(out["002217"]["hits"]) == 2
    assert len(out["600110"]["hits"]) == 1


def test_group_hits_caps_per_stock():
    hits = [_hit("002217", "合力泰", f"公告{i}", "立案调查") for i in range(5)]
    out = group_hits_by_stock(hits, max_per_stock=2)
    assert len(out["002217"]["hits"]) == 2


# ---------- build_risk_prompt: 携带标题/标签/正文 + 要求严重度JSON ----------

def test_build_risk_prompt_carries_data():
    system, user = build_risk_prompt(
        "002217", "合力泰",
        [_hit("002217", "合力泰", "收到证监会立案告知书的公告", "立案调查")],
        "经查，公司涉嫌信息披露违法违规，证监会决定立案。")
    assert "高" in system and "中" in system and "低" in system and "JSON" in system
    assert "合力泰" in user and "立案告知书" in user and "立案调查" in user
    assert "信息披露违法违规" in user


# ---------- parse_risk_verdict: 解析严重度 + 研判句 ----------

def test_parse_risk_verdict_valid():
    out = parse_risk_verdict('{"severity":"中","verdict":"常规审核问询，影响有限"}')
    assert out["severity"] == "中" and out["emoji"] == "🟡"
    assert out["text"] == "常规审核问询，影响有限"


def test_parse_risk_verdict_fenced():
    out = parse_risk_verdict('```json\n{"severity":"高","verdict":"立案调查重大利空"}\n```')
    assert out["severity"] == "高" and out["emoji"] == "🔴"


def test_parse_risk_verdict_garbage_returns_none():
    assert parse_risk_verdict("模型超时") is None
    assert parse_risk_verdict("") is None


def test_parse_risk_verdict_bad_severity_returns_none():
    assert parse_risk_verdict('{"severity":"严重","verdict":"x"}') is None


# ---------- generate_risk_verdicts: 空命中兜底(不联网) ----------

def test_generate_risk_verdicts_empty_returns_empty():
    assert asyncio.run(generate_risk_verdicts([])) == {}


# ---------- 接入卡片: ann_section_text/ann_table 注入 AI 研判 ----------

def test_ann_section_text_injects_verdict():
    from backend.services.risk_announcement_scanner import ann_section_text
    hits = [_hit("002217", "合力泰", "立案告知书", "立案调查", url="")]
    verdicts = {"002217": {"severity": "高", "emoji": "🔴", "text": "立案调查重大利空"}}
    out = ann_section_text(hits, verdicts)
    assert "🤖" in out and "🔴高" in out and "立案调查重大利空" in out
    # 无 verdict 时不挂研判
    assert "🤖" not in ann_section_text(hits)


def test_ann_table_short_columns_with_verdict_brief():
    # 基线 v1.1: ann_table = 全短列表「股票|类型|要点」, 要点=AI严重度(有研判)或日期(无研判)。
    from backend.services.risk_announcement_scanner import ann_table
    hits = [_hit("002217", "合力泰", "立案告知书", "立案调查", date="2026-06-01")]
    verdicts = {"002217": {"severity": "高", "emoji": "🔴", "text": "重大利空"}}
    el = ann_table(hits, verdicts)
    assert el["tag"] == "markdown"
    content = el["content"]
    assert "| 股票 | 类型 | 要点 |" in content
    assert "合力泰 002217" in content and "立案调查" in content
    assert "🔴高" in content
    # 长值(研判句/公告标题)不进表
    assert "重大利空" not in content and "立案告知书" not in content
    # 无 verdict → 要点列回落到日期
    assert "06-01" in ann_table(hits)["content"]


def test_ann_fold_carries_title_and_verdict():
    # 长值下沉折叠: 公告标题+日期+AI研判句进 collapsible_panel。
    from backend.services.risk_announcement_scanner import ann_fold
    hits = [_hit("002217", "合力泰", "立案告知书", "立案调查", date="2026-06-01")]
    verdicts = {"002217": {"severity": "高", "emoji": "🔴", "text": "重大利空"}}
    el = ann_fold(hits, verdicts)
    assert el["tag"] == "collapsible_panel"
    detail = el["elements"][0]["content"]
    assert "立案告知书" in detail and "06-01" in detail
    assert "🤖🔴高" in detail and "重大利空" in detail
    # 无 verdict → 不挂研判
    assert "🤖" not in ann_fold(hits)["elements"][0]["content"]
