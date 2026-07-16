# -*- coding: utf-8 -*-
"""基线 v1.1 推送卡改版单测(情报组/系统组构卡纯函数): 博主发帖 / 问财中断恢复 /
系统健康汇总 / 信号EOD复核 / 任务失败文本 / 行情自检文本。不连库不联网。"""
from datetime import datetime

from backend.services.blogger_post_scanner import _post_gist, build_post_card
from backend.services.data_sanity import build_sanity_alert_text
from backend.services.signal_eod_audit import build_audit_card, note_category
from backend.services.system_health import build_digest_card
from backend.services.task_registry import ALERT_THRESHOLD, build_task_failure_text
from backend.services.wencai_scanner import build_fail_card, build_recover_card


# ── 博主发帖(情报蓝): 结论行=博主名+要点一句, 全文进折叠 ──

def test_post_gist_first_nonempty_line_truncated():
    assert _post_gist("第一行要点\n第二行") == "第一行要点"
    assert _post_gist("") == "（无文字内容）"
    g = _post_gist("长" * 50)
    assert g.endswith("…") and len(g) == 41


def test_build_post_card():
    post = {"blogger_name": "全能的野人", "posted_at": datetime(2026, 7, 16, 10, 30),
            "content": "指数缩量回踩，重点看机器人\n后排补涨没意义",
            "stock_codes": ["300124"], "url": "http://t.10jqka.com.cn/x",
            "like_num": 12, "comment_num": 3, "images": []}
    card = build_post_card(post)
    assert card.family == "intel" and card.template == "blue"   # 原 orange → 情报蓝
    assert card.title == "📣 博主发帖 · 全能的野人"
    head = card.elements[0]["content"]
    assert "全能的野人" in head and "机器人" in head
    assert "后排补涨" not in head                                 # 全文只进折叠
    fold = next(e for e in card.elements if e.get("tag") == "collapsible_panel")
    assert "后排补涨" in fold["elements"][0]["content"]
    assert any(e.get("tag") == "markdown" and "👉" in e.get("content", "")
               for e in card.elements)
    assert card.link_url.startswith("http")
    assert "300124" in card.fallback                              # 回退同源信息量
    assert "全能的野人" in card.summary


# ── 问财候选榜中断/恢复(系统灰, 原 red/green) ──

def test_wencai_fail_card():
    c = build_fail_card(4, "预置榜: pywencai import error")
    assert c.family == "system" and c.template == "grey"
    assert "问财候选榜中断" in c.title
    assert "连续 **4** 轮" in c.elements[0]["content"]
    assert any(e.get("tag") == "markdown" and "👉" in e.get("content", "")
               for e in c.elements)
    assert c.elements[-1]["tag"] == "collapsible_panel"           # 排查方法折叠
    assert "pywencai" in c.fallback


def test_wencai_recover_card():
    c = build_recover_card()
    assert c.family == "system" and c.template == "grey"          # 灰 = 中性收尾闭环
    assert "已恢复" in c.title and c.tags == [("已恢复", "grey")]


# ── 系统健康盘后汇总(系统灰, 原 orange): 灯串 + 异常清单 ──

def test_build_digest_card():
    items = [("博主发帖", "「x」连续3次拉取失败: err", "10:05"),
             ("数据源交叉校验", "偏差超阈值", "14:30")]
    card = build_digest_card(items)
    assert card.family == "system" and card.template == "grey"
    assert "2项" in card.title
    head = card.elements[0]["content"]
    assert "🟡" in head and "博主发帖" in head                    # 灯串 + 类别
    assert any(e.get("tag") == "markdown" and "👉" in e.get("content", "")
               for e in card.elements)
    assert "10:05" in card.fallback and "14:30" in card.fallback


def test_build_digest_card_over8_top5_plus_fold():
    items = [(f"来源{i}", f"故障{i}", "10:00") for i in range(10)]
    card = build_digest_card(items)
    assert any("等 **10** 项" in e.get("content", "") for e in card.elements
               if e.get("tag") == "markdown")
    folds = [e for e in card.elements if e.get("tag") == "collapsible_panel"]
    assert any("故障9" in f["elements"][0]["content"] for f in folds)   # 全量下沉


# ── 信号EOD复核(系统灰): 灯串 + 短表(股票|信号|结论) + 疑点明细折叠 ──

def _sig(name="京东方A", signal="回踩MA10", t="2026-07-16 10:31:00"):
    return {"name": name, "signal_name": signal, "triggered_at": t, "code": "000725"}


def test_note_category_short_words():
    assert note_category("K线序列错位: 触发时认的昨日=2026-06-06") == "序列错位"
    assert note_category("昨收不符: 触发时昨收=10.0") == "昨收不符"
    assert note_category("触发价418不在当日真实区间[95, 105]") == "价格越界"
    assert note_category("波幅容不下宣称急跌, 疑似分时冻结回放") == "冻结回放"
    assert note_category("上涨0家且下跌0家 = 竞价时段/降级源无数据假象") == "数据假象"
    assert note_category("跌停5523家超合理上限1500") == "数据脏值"
    assert note_category("") == "数据存疑"


def test_build_audit_card():
    suspects = [(_sig(), "触发价418不在当日真实区间[95, 105]")]
    card = build_audit_card(suspects, "2026-07-16", n_ok=5, n_unverified=1)
    assert card.family == "system" and card.template == "grey"
    assert "1条存疑" in card.title
    head = card.elements[0]["content"]
    assert "🟢" in head and "🔴" in head                          # 复核结果灯串
    assert "复核 **7** 条" in head and "存疑 **1**" in head
    table = card.elements[1]["content"]
    assert "| 股票 | 信号 | 结论 |" in table
    assert "京东方A" in table and "价格越界" in table
    assert "418" not in table                                     # 疑点长文案下沉折叠
    assert any(e.get("tag") == "markdown" and "👉" in e.get("content", "")
               for e in card.elements)
    fold = card.elements[-1]
    assert fold["tag"] == "collapsible_panel"
    assert "418" in fold["elements"][0]["content"]
    assert "418" in card.fallback                                 # 回退同源信息量


def test_build_audit_card_over8_top5():
    suspects = [(_sig(name=f"股{i}"), "K线序列错位") for i in range(10)]
    card = build_audit_card(suspects, "2026-07-16")
    table = card.elements[1]["content"]
    assert table.count("\n") == 6                                 # 表头2行 + Top5
    assert any("等 **10** 条" in e.get("content", "") for e in card.elements
               if e.get("tag") == "markdown")


# ── 任务失败/行情自检 纯文本轻处理: 结论前置 + 加粗 + 👉 ──

def test_task_failure_text_layout():
    t = build_task_failure_text("scan_stock_pool", "scan_stock_pool", 3, "TimeoutError: x")
    lines = t.splitlines()
    assert lines[0] == "⚠️ 调度任务连续失败"
    assert f"**scan_stock_pool** 连续失败 **3** 次（阈值 {ALERT_THRESHOLD}）" in t
    assert "TimeoutError" in t and "👉" in t


def test_sanity_alert_text_layout():
    t = build_sanity_alert_text(["行情陈旧 **87/153** 只超6分钟未更新"])
    assert t.startswith("⚠️ 行情数据自检告警")
    assert "**87/153**" in t and "👉" in t
