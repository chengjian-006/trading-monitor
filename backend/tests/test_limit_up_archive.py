"""每日涨停复盘 — 概念排名 + 复盘卡文案 纯逻辑测试 (v1.7.572)."""
from backend.services.limit_up_archive import build_concept_ranking, build_review_text

BOARDS = [
    {"code": "603580", "name": "艾艾精工", "height": 3, "streak_label": "4天3板", "reason": "摘帽+新能源电池配件", "pct": 10.0, "open_times": 0},
    {"code": "603730", "name": "岱美股份", "height": 1, "streak_label": "首板", "reason": "人形机器人+特斯拉+遮阳板龙头", "pct": 9.98, "open_times": 3},
    {"code": "002979", "name": "雷赛智能", "height": 2, "streak_label": "2天2板", "reason": "人形机器人+灵巧手", "pct": 10.0, "open_times": 0},
    {"code": "000506", "name": "招金黄金", "height": 2, "streak_label": "2天2板", "reason": "黄金+国企", "pct": 9.99, "open_times": 0},
    {"code": "600988", "name": "赤峰黄金", "height": 2, "streak_label": "2天2板", "reason": "黄金+紫金入主", "pct": 10.0, "open_times": 0},
    {"code": "301379", "name": "天山电子", "height": 2, "streak_label": "4天2板", "reason": "存储芯片+SSD", "pct": 20.0, "open_times": 12},
]
META = {"limit_up_count": 6, "limit_up_history": 9, "limit_down_count": 1,
        "broken_board_count": 2, "seal_rate": 0.6666}


class TestConceptRanking:
    def test_merge_synonyms_and_count(self):
        r = dict(build_concept_ranking(BOARDS, min_count=1))
        # 人形机器人/灵巧手 归一到"机器人"= 2 只
        assert r.get("机器人") == 2
        # 黄金 2 只
        assert r.get("黄金") == 2
        # 存储/芯片 归一到"半导体" = 1 只
        assert r.get("半导体") == 1

    def test_min_count_filters(self):
        r = dict(build_concept_ranking(BOARDS, min_count=2))
        assert "机器人" in r and "黄金" in r
        assert "半导体" not in r   # 只1只, 被 min_count=2 过滤

    def test_dedup_within_stock(self):
        # 一只票 reason 含机器人多个近义词, 只计1次
        one = [{"reason": "人形机器人+减速器+灵巧手+谐波", "code": "1", "name": "x", "height": 1}]
        assert dict(build_concept_ranking(one, min_count=1)).get("机器人") == 1


class TestReviewText:
    def test_has_overview_ladder_ranking(self):
        txt = build_review_text("20260703", META, BOARDS, link="http://x/limit-up")
        assert "涨停 **6** 家" in txt
        assert "封板率 67%" in txt
        assert "连板梯队" in txt
        # 连板梯队只列 height>=2, 首板岱美不进梯队
        assert "岱美股份" not in txt.split("连板梯队")[1].split("热点分布")[0]
        assert "热点分布" in txt
        assert "查看全部涨停复盘" in txt

    def test_no_link_when_empty(self):
        txt = build_review_text("20260703", META, BOARDS, link="")
        assert "查看全部" not in txt

    def test_date_formatting(self):
        txt = build_review_text("20260703", META, BOARDS)
        assert "2026-07-03" in txt
