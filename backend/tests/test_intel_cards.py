"""情报类推送卡(基线 v1.1 蓝卡)改版回归测试 — 第三批 7 张。

覆盖: 竞价播报/盘面播报、竞价分析、晚盘复盘总结(持仓表现+胜率+近期披露)、次日板块预测、
真假强势评分快照、资金进攻方向、合并竞价卡。验证五区骨架(结论KPI/heading → 数据区全短列
→ 👉定性 → 折叠)、信封字段(summary/family=intel)、fallback 同源信息量、
正文不写时间、chart 硬规则(media锁定)等。(尾盘决策合并卡14:40已下线, 相关测试移除)
"""
from unittest.mock import AsyncMock

from backend.services import card_kit


# ── 断言小工具 ──

def _kpi_rows(elements):
    return [e for e in elements if isinstance(e, dict) and e.get("tag") == "column_set"]


def _folds(elements):
    return [e for e in elements if isinstance(e, dict) and e.get("tag") == "collapsible_panel"]


def _charts(elements):
    return [e for e in elements if isinstance(e, dict) and e.get("tag") == "chart"]


def _md_join(elements):
    out = []
    for e in elements:
        if not isinstance(e, dict):
            continue
        if e.get("tag") in ("markdown",):
            out.append(str(e.get("content", "")))
        if e.get("tag") == "collapsible_panel":
            out.append(str(e["header"]["title"].get("content", "")))
            for sub in e.get("elements", []):
                out.append(str(sub.get("content", "")))
    return "\n".join(out)


def _advice_lines(elements):
    return [e for e in elements
            if isinstance(e, dict) and e.get("tag") == "markdown"
            and str(e.get("content", "")).startswith("👉 ")]


# ── 1. 盘面播报(AI 开盘共性) ──

class TestAuctionSummaryCard:
    def _d(self):
        return {"headline": "抢筹积极高开为主", "vibe": "高开多", "style": "小盘更强",
                "kill": "无明显杀跌", "action": "开盘顺势不追高",
                "mainlines": [{"direction": "算力", "reps": "中贝通信/鸿博股份"}]}

    def test_structure(self):
        from backend.services.auction_summary_analyst import _build_auction_card
        indices = [{"name": "上证指数", "price": 3500.0, "pct_change": 0.32, "amount": 100}]
        text, elements, meta = _build_auction_card(self._d(), 8, 21, indices, 3.2)
        # 结论区: heading 定调 + KPI 恰好 3 栏
        assert elements[0]["tag"] == "markdown" and elements[0]["text_size"] == "heading"
        assert "抢筹积极高开为主" in elements[0]["content"]
        kpis = _kpi_rows(elements)
        assert len(kpis) == 1 and len(kpis[0]["columns"]) == 3
        md = _md_join(elements)
        assert "+0.32%" in str(kpis[0])          # 上证竞价实测值进 KPI
        assert "8只" in str(kpis[0]) and "21只" in str(kpis[0])
        # 👉 定性 + 折叠方法论
        assert len(_advice_lines(elements)) == 1
        assert "开盘顺势不追高" in _advice_lines(elements)[0]["content"]
        assert len(_folds(elements)) == 1
        # meta 供信封摘要
        assert meta == {"headline": "抢筹积极高开为主", "near_lu": 8, "strong": 21}
        # fallback 同源信息量
        assert "抢筹积极高开为主" in text and "涨停预排8" in text and "算力" in text
        assert "👉 开盘顺势不追高" in text

    def test_kpi_pads_to_3_without_index(self):
        from backend.services.auction_summary_analyst import _build_auction_card
        _, elements, _ = _build_auction_card(self._d(), 8, 21, [], 1.0)
        assert len(_kpi_rows(elements)[0]["columns"]) == 3


# ── 2. 竞价分析(板块强弱)纯函数件 ──

class TestAuctionSectorPieces:
    def test_board_row_hit_watchlist_red(self):
        from backend.services.auction_sector_strength import _board_row
        b = {"name": "光伏设备", "pct": 2.1, "leader_name": "阳光电源", "leader_code": "300274"}
        row = _board_row(b, {"300274"})
        assert row[0] == "**光伏设备**"
        assert "+2.1%" in row[1]
        assert "<font color='red'>阳光电源</font>" == row[2]
        row2 = _board_row({**b, "leader_code": "000001"}, {"300274"})
        assert row2[2] == "阳光电源"    # 未命中不标红

    def test_relay_advice_levels(self):
        from backend.services.auction_sector_strength import _relay_advice
        assert "顺主线" in _relay_advice(3, 0)
        assert "退潮" in _relay_advice(1, 2)
        assert "分化" in _relay_advice(1, 1)


# ── 3. 真假强势评分快照 ──

class TestStrengthCard:
    def _rows(self, n):
        return [{"name": f"股{i}", "code": f"60000{i}", "industry": "算力",
                 "close": 10.0, "stock_5d_cum": 6.5 - i, "score": 30 - i,
                 "grade": "A", "is_real_strong": True,
                 "criteria": [{"delta": 8, "name": "率先放量"}, {"delta": 4, "name": "主流题材"}]}
                for i in range(n)]

    def test_full_card(self):
        from backend.services.strength_quality_scanner import _build_strength_card
        real = self._rows(7)
        obs = self._rows(6)
        text, elements, meta = _build_strength_card(real, obs, 40, 3512.34, 1.8, 12)
        kpi = _kpi_rows(elements)[0]
        assert len(kpi["columns"]) == 3
        assert "7只" in str(kpi) and "6只" in str(kpi) and "+1.8%" in str(kpi)
        # 表格只进 Top5(>8 行规则: Top+等N, 全量下沉折叠)
        tables = [e for e in elements if isinstance(e, dict)
                  and e.get("tag") == "markdown" and "| 个股 |" in str(e.get("content"))]
        assert len(tables) == 1
        assert tables[0]["content"].count("\n") == 6   # 表头+分隔+5行
        # 折叠含全名单(7 只都在) + 方法论
        fold_md = _md_join(_folds(elements))
        assert all(f"股{i}" in fold_md for i in range(7))
        assert "真强势=大盘企稳首日率先放量上攻" in fold_md
        # 👉 定性 + meta + fallback 同源
        assert len(_advice_lines(elements)) == 1
        assert meta["real"] == 7 and meta["observe"] == 6 and meta["top"] == "股0"
        assert "股6" in text and "👉" in text
        assert "14:30" not in text and "14:30" not in _md_join(elements)  # 正文不写时间

    def test_empty_card(self):
        from backend.services.strength_quality_scanner import _build_strength_card
        text, elements, meta = _build_strength_card([], [], 33, 3500.0, -0.6, 12)
        assert len(_kpi_rows(elements)) == 1
        assert len(_advice_lines(elements)) == 1
        assert meta == {"real": 0, "observe": 0, "top": ""}
        assert "33" in text and "无真强势" in text


# ── 4. 收盘复盘 ──

class TestReviewCard:
    def _cmp(self):
        return {"buy": {"evaluated": 20, "success": 11, "success_rate": 55.0,
                        "pending": 3, "avg_p5": 1.23},
                "sell": {"evaluated": 10, "success": 6, "success_rate": 60.0,
                         "pending": 1, "avg_p5": -0.5}}

    def test_structure(self):
        from backend.services.review_summary import _build_review_card
        top = [{"signal_name": "缩量突破", "success_rate": 71.0, "success": 5, "evaluated": 7}]
        weak = [{"signal_name": "弱势极限", "success_rate": 40.0, "success": 2, "evaluated": 5}]
        text, elements, meta = _build_review_card(3, 2, 1, 0, self._cmp(), top, weak)
        kpi = _kpi_rows(elements)[0]
        assert len(kpi["columns"]) == 3 and "55.0%" in str(kpi)
        md = _md_join(elements)
        # 胜率强度条(▰▱) + 全短列表格
        assert "▰" in md and "▱" in md
        assert "| 信号 | 胜率 | 战绩 |" in md
        assert "**71.0%**" in md and "5/7" in md
        # 👉 定性 + 折叠口径(另含板块预警下沉)
        assert "优先跟缩量突破" in _advice_lines(elements)[0]["content"]
        fold_md = _md_join(_folds(elements))
        assert "板块预警 1" in fold_md and "实际收盘视角" in fold_md
        assert meta["buy_rate_str"] == "买点胜率55.0%"
        # fallback 同源
        assert "买 3 / 卖 2" in text and "缩量突破" in text and "弱势极限" in text

    def test_no_rank_advice(self):
        from backend.services.review_summary import _build_review_card
        text, elements, meta = _build_review_card(0, 0, 0, 0, self._cmp(), [], [])
        assert "按交易计划执行" in _advice_lines(elements)[0]["content"]
        assert meta["top"] == ""

    def test_holdings_and_disclosure_sections(self):
        """晚盘复盘总结新增两段: 💼持仓今日表现(跌幅大在前) + 📅近期披露。"""
        from backend.services.review_summary import _build_review_card
        hold_perf = [
            {"code": "002463", "name": "沪电股份", "price": 128.68, "pct": -5.88, "floating": -3.20},
            {"code": "600519", "name": "贵州茅台", "price": 1245.0, "pct": 1.11, "floating": 2.30},
        ]
        disc_rows = [{"code": "002463", "name": "沪电股份", "appoint_date": "2026-07-20",
                      "report_type": "2", "report_year": "2026"}]
        text, elements, meta = _build_review_card(
            1, 0, 0, 0, self._cmp(), [], [],
            hold_perf=hold_perf, disc_rows=disc_rows, disc_hold={"002463"})
        md = _md_join(elements)
        assert "持仓今日表现" in md and "2只（1涨 1跌）" in md
        assert "| 股票 | 今日 | 浮盈 |" in md
        assert "近期财报披露" in md and "| 股票 | 披露日 | 类型 |" in md
        assert "🔴沪电股份" in md          # 持仓票标🔴
        # 披露 → 建议追加避险话术
        assert "披露前拿不准的持仓可减仓避险" in _advice_lines(elements)[0]["content"]
        # fallback 同源含两段
        assert "持仓今日表现" in text and "近期披露" in text


# ── 5. 次日板块预测 ──

class TestPredictionCard:
    def _groups(self, n_wts=7):
        def item(i):
            return {"theme": f"题材{i}", "reason": "昨冷今抬升", "traj": "0→1→3→7",
                    "today": 7, "samples": "甲/乙",
                    "points": [("07-11", 0), ("07-14", 1), ("07-15", 3), ("07-16", 7)]}
        return {"弱转强候选": [item(i) for i in range(n_wts)],
                "强转弱候选": [item(90)],
                "强势延续": [item(91)],
                "疑似终结": [item(92), item(93)]}

    async def test_return_only_structure(self):
        from backend.services.sector_rotation_scanner import _push_prediction
        text, elements, meta = await _push_prediction(self._groups(), return_only=True)
        kpi = _kpi_rows(elements)[0]
        assert len(kpi["columns"]) == 3 and "7个" in str(kpi)
        # 头号候选柱状图: chart 硬规则(2:1 + media 锁定)
        charts = _charts(elements)
        assert len(charts) == 1
        assert charts[0]["aspect_ratio"] == "2:1"
        assert charts[0]["chart_spec"]["media"] == []
        assert charts[0]["chart_spec"]["type"] == "bar"
        # 表格 Top5 封顶, 全量理由进折叠
        md = _md_join(elements)
        assert "| 题材 | 近6日涨停 |" in md
        assert "等7个" in md
        fold_md = _md_join(_folds(elements))
        assert "题材6" in fold_md and "疑似终结 2 个" in fold_md and "未回测" in fold_md
        # 👉 定性 + meta + fallback
        assert "题材0" in _advice_lines(elements)[0]["content"]
        assert meta == {"wts": 7, "stw": 1, "cont": 1, "top": "题材0"}
        assert "弱转强候选" in text and "👉" in text

    async def test_send_path_uses_send_card(self, monkeypatch):
        from backend.services import sector_rotation_scanner as srs
        captured = {}

        async def fake_send_card(card):
            captured["card"] = card
            return True

        import backend.services.notifier as notifier
        monkeypatch.setattr(notifier, "send_card", fake_send_card)
        await srs._push_prediction(self._groups(3))
        card = captured["card"]
        assert card.title == "📊 次日板块预测"
        assert card.family == "intel" and card.template == "blue"
        assert "弱转强3" in card.summary
        assert card.subtitle


# ── 6. 资金进攻方向 ──

class TestAttackCard:
    def _hot(self):
        return [{"theme": "商业航天", "state": "启动", "limit_up": 5, "max_height": 3,
                 "broken": 0, "samples": ["甲", "乙", "丙", "丁"]}]

    def _lead(self):
        # 行业名包含题材名 → 软匹配双确认(_cross_confirm)
        return [{"industry": "商业航天与卫星", "pct_today": 2.4}]

    def test_mainline_card(self):
        from backend.services.attack_direction_analyst import _build_card
        hits = [{"name": f"股{i}", "code": f"00000{i}", "hold": i == 0,
                 "where": "商业航天", "strong": True} for i in range(7)]
        elements = _build_card(self._hot(), self._lead(), hits, "封板率80% · 涨停45家", False)
        # 结论区 heading 定性
        assert elements[0]["text_size"] == "heading" and "商业航天" in elements[0]["content"]
        md = _md_join(elements)
        assert "| 题材 | 涨停 | 状态 |" in md and "**5家**" in md
        assert "| 行业 | 涨幅 |" in md and "+2.4%" in md
        assert "🔥双确认" in md          # 题材/行业名共振
        # 自选命中前 5 + 全量下沉折叠
        assert "等7条" in md
        fold_md = _md_join(_folds(elements))
        assert "股6" in fold_md and "代表股" in fold_md and "口径" in fold_md
        assert "主攻商业航天" in _advice_lines(elements)[0]["content"]

    def test_no_main_card(self):
        from backend.services.attack_direction_analyst import _build_card, _attack_advice
        elements = _build_card([], [], [], "冰点 · 涨停18家", True)
        assert "无明显主线" in elements[0]["content"]
        assert "观望" in _advice_lines(elements)[0]["content"]
        assert _attack_advice([], [], True).startswith("资金分散")

    def test_text_has_no_time(self):
        from backend.services.attack_direction_analyst import _build_text
        text = _build_text(self._hot(), self._lead(), [], "封板率80%", False)
        assert "09:45" not in text and "👉" in text


# ── 7. (尾盘决策合并卡已下线 — 14:40整卡取消, 内容不再推送; 相关测试随之移除) ──


# ── 8. 合并竞价卡(信封 + 双部分组装) ──

class TestAuction0926Card:
    async def test_merged_envelope(self, monkeypatch):
        from backend.services import auction_summary_analyst as asa
        import backend.services.auction_sector_strength as ass
        import backend.services.notifier as notifier

        monkeypatch.setattr(asa, "_is_trading_day", lambda now=None: True)
        s_part = (["共性文本"], [card_kit.heading_md("共性")],
                  {"headline": "高开抢筹", "near_lu": 6, "strong": 18})
        b_part = (["板块文本"], [card_kit.heading_md("板块")],
                  {"top_board": "算力", "top_pct": 2.3, "relay": "承接2/转弱1", "advice": "x"})
        monkeypatch.setattr(asa, "build_auction_summary_part", AsyncMock(return_value=s_part))
        monkeypatch.setattr(ass, "build_auction_sector_part", AsyncMock(return_value=b_part))
        captured = {}

        async def fake_send_card(card):
            captured["card"] = card
            return True

        monkeypatch.setattr(notifier, "send_card", fake_send_card)
        await asa.run_auction_0926()
        card = captured["card"]
        assert card.title == "📊 竞价播报"
        assert card.family == "intel" and card.template == "blue"
        assert "高开抢筹" in card.summary and "涨停预排6只" in card.summary
        assert "最强算力+2.3%" in card.summary
        assert "共性文本" in card.fallback and "板块文本" in card.fallback
