# -*- coding: utf-8 -*-
"""「分时二波过前高」纯检测函数 second_surge.detect_second_surge 单测。

形态(严格): 第一波放量冲高 H1 → 回落 ≥pullback_min 缩量 → 第二波(最近 leg_window 分钟)
  放量 ≥vol_mult×基准量 拉升 ≥leg_rise_min → 现价创当日新高(过 H1)。确认后报。
"""
from backend.services.second_surge import (
    baseline_vol, detect_second_surge, cum_amount, build_surge_card, ma20_rising,
)

# 默认参数(= 生产原设计: 深回落≥1.5% / 放量≥1.8× / 二波涨≥0.8% / 窗口4分钟)
P = {
    "leg_window": 4,
    "leg_rise_min": 0.008,
    "vol_mult": 1.8,
    "pullback_min": 0.015,
    "min_points": 15,
    "chase_limit_buffer_pct": 1.0,
}


def _mk(prices, vols):
    """价+量序列 → trends(逐分钟, 时间从 09:30 递增)。"""
    assert len(prices) == len(vols)
    out = []
    for i, (p, v) in enumerate(zip(prices, vols)):
        mn = 9 * 60 + 30 + i
        out.append({"time": f"2026-07-08 {mn // 60:02d}:{mn % 60:02d}", "price": float(p), "volume": float(v)})
    return out


# 标准正例: warmup×8@100 → 第一波 101..104(H1=104,放量) → 回落 103,102(缩量) → 二波 102,103,104,104.5,105(放量,过前高)
_POS_PRICES = [100] * 8 + [101, 102, 103, 104] + [103, 102] + [102, 103, 104, 104.5, 105]
_POS_VOLS = [1000] * 8 + [3000] * 4 + [500, 500] + [800, 2500, 2500, 2500, 2500]


class TestBaselineVol:
    def test_median_skips_open_spike(self):
        # 首根 99999 巨量(竞价)应被跳过, 中位数取其余
        vb = baseline_vol([99999, 1000, 1000, 1000, 2000])
        assert 1000 <= vb <= 2000


class TestSecondSurge:
    def test_positive_second_surge_over_prior_high(self):
        r = detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), pre_close=100.0, params=P, code="600000", name="测试")
        assert r is not None
        assert r["price_now"] == 105.0
        assert r["H1"] == 104.0
        assert r["vol_mult"] >= 1.8
        assert r["trough_pct"] >= 1.5

    def test_not_new_high_blocked(self):
        # 二波没过 H1(收在 103.5 < 104) → 不报
        prices = _POS_PRICES[:-1] + [103.5]
        assert detect_second_surge(_mk(prices, _POS_VOLS), 100.0, P, code="600000") is None

    def test_single_leg_no_pullback_blocked(self):
        # 一路单边上涨(无回落降温) → 不报
        prices = [100 + i * 0.3 for i in range(19)]      # 100 → 105.4 单调
        vols = [1000] * 14 + [2500] * 5
        assert detect_second_surge(_mk(prices, vols), 100.0, P, code="600000") is None

    def test_no_volume_surge_blocked(self):
        # 形态对但二波不放量(量与基准持平) → 不报
        vols = [1000] * 8 + [1000] * 4 + [1000, 1000] + [1000, 1000, 1000, 1000, 1000]
        assert detect_second_surge(_mk(_POS_PRICES, vols), 100.0, P, code="600000") is None

    def test_near_limit_up_blocked(self):
        # 现价逼近涨停(pre_close=96 → +9.4%, 主板板≈+10%, tol1%) → 挂不进不报
        assert detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), 96.0, P, code="600000") is None

    def test_no_code_backtest_ignores_chase_limit(self):
        # 回测无 code → 不判逼近涨停, 照常触发(即便 pre_close=96)
        assert detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), 96.0, P, code=None) is not None

    def test_too_few_points_blocked(self):
        assert detect_second_surge(_mk([100] * 10, [1000] * 10), 100.0, P, code="600000") is None


class TestAmountAndCard:
    def test_cum_amount_prefers_real_amount(self):
        # 分时自带真实成交额(THS 第3字段) → 直接求和, 不再由量价估算
        trends = [{"price": 10.0, "volume": 100_000, "amount": 1_000_000},
                  {"price": 10.0, "volume": 200_000, "amount": 2_000_000}]
        assert abs(cum_amount(trends) - 3_000_000) < 1

    def test_cum_amount_fallback_no_x100(self):
        # 老缓存无 amount 时退回 量×价: volume 单位是「股」, 不得再 ×100(曾放大百倍,
        # 致 min_amount_now=5000万 的流动性闸门实际只卡到 50 万)
        trends = [{"price": 10.0, "volume": 100_000}, {"price": 10.0, "volume": 200_000}]
        assert abs(cum_amount(trends) - 3_000_000) < 1

    def test_build_card_single_and_multi(self):
        r = detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), 100.0, P, code="600000", name="测试")
        t1, b1 = build_surge_card([{"name": "测试", "code": "600000", "r": r, "action_md": ""}])
        assert "二波过前高 · 测试(600000)" in t1
        assert "创当日新高" in b1 and "10jqka.com.cn/600000" in b1
        t2, _ = build_surge_card([
            {"name": "甲", "code": "600000", "r": r, "action_md": ""},
            {"name": "乙", "code": "600001", "r": r, "action_md": ""},
        ])
        assert "2只" in t2

    def test_card_plain_language_checklist(self):
        # v1.7.625: 所有触发规则大白话逐条列出, 含20线/流动性/涨停暗闸
        r = detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), 100.0, P, code="600000", name="测试")
        r["ma20_now"] = 98.50
        r["ma20_prev"] = 97.20
        r["amount_yi"] = 2.3
        _, body = build_surge_card([{"name": "测试", "code": "600000", "r": r, "action_md": ""}])
        assert "触发条件（全中才提醒）" in body
        assert "第一波冲高" in body and "回落降温" in body and "二波放量" in body
        assert "20日线向上：MA20 **¥98.50** ≥ 3天前的 ¥97.20" in body
        assert "已成交 **2.3 亿**" in body
        assert "没贴涨停板" in body and "买得进" in body
        assert "**¥105.00**" in body                     # 现价加粗(0716: 重要数字加粗)

    def test_card_without_optional_fields(self):
        # 无 ma20/amount 值(如回测/老调用): 20线一行退化成定性文案, 成交额行省略
        r = detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), 100.0, P, code="600000", name="测试")
        _, body = build_surge_card([{"name": "测试", "code": "600000", "r": r, "action_md": ""}])
        assert "20日线向上：最近3天 MA20 没掉头" in body
        assert "已成交" not in body


class TestMa20Rising:
    def test_rising_passes(self):
        # 逐日抬升的收盘(最新在前): 昨收MA20 明显 > 3日前MA20 → 上翘
        closes = [30 - i * 0.1 for i in range(30)]     # closes[0]=30(最新最高), 越老越低
        assert ma20_rising(closes, lookback=3) is True

    def test_flat_counts_as_rising(self):
        # 完全走平: 昨收MA20 == 3日前MA20 → 仍算过(只滤明确掉头)
        closes = [20.0] * 30
        assert ma20_rising(closes, lookback=3) is True

    def test_falling_blocked(self):
        # 逐日下行(最新在前=越新越低): 昨收MA20 < 3日前MA20 → 掉头, 拦下
        closes = [20 + i * 0.1 for i in range(30)]     # closes[0]=20(最新最低)
        assert ma20_rising(closes, lookback=3) is False

    def test_insufficient_history_blocked(self):
        # 不足 20+lookback 根(次新股) → 判不满足(宁缺毋滥)
        assert ma20_rising([10.0] * 22, lookback=3) is False
        assert ma20_rising([10.0] * 23, lookback=3) is True


class TestSurgeCardV2:
    """基线 v1.1 结构卡 build_surge_card_v2: 机会家族红卡 + 五区骨架 + 信封字段。"""

    def _item(self, code="600000", name="测试", action_md=""):
        r = detect_second_surge(_mk(_POS_PRICES, _POS_VOLS), 100.0, P, code=code, name=name)
        r["ma20_now"] = 98.50
        r["ma20_prev"] = 97.20
        r["amount_yi"] = 2.3
        return {"name": name, "code": code, "r": r, "action_md": action_md}

    def test_structure_single(self):
        from backend.services.second_surge import build_surge_card_v2
        it = self._item(action_md="[🔕 当日不提醒](http://x/a)　·　[🔕 本周不提醒](http://x/b)")
        c = build_surge_card_v2([it], P)
        assert c.title == "🔥 二波过前高 · 测试(600000)"
        assert c.family == "opportunity" and c.template == "red"
        assert c.tags == [("二波", "red")]
        assert c.subtitle == "形态提示 · 非买卖建议"
        # 锁屏摘要: 名/代码/事件/现价/涨幅
        assert "测试" in c.summary and "600000" in c.summary
        assert "二波过前高" in c.summary and "¥105.00" in c.summary
        # 结论行 + ✅ 触发清单(card_kit.checklist 口径: 实测值加粗 + 要求门槛)
        body0 = c.elements[0]["content"]
        assert body0.startswith("**测试(600000)**")
        assert "**¥105.00**" in body0 and "+5.0%" in body0
        for cond in ("第一波冲高", "回落降温", "二波放量", "二波过前高",
                     "20日线向上", "不是死票", "没贴涨停板"):
            assert f"✅ {cond}" in body0, cond
        assert "（要求 ≥1.5%）" in body0
        # 👉 建议 → 折叠口径 → 快捷动作行(永远最后)
        assert c.elements[1]["content"] == "👉 **抬头看一眼，形态提示非买卖建议**"
        assert c.elements[2]["tag"] == "collapsible_panel"
        last = c.elements[-1]["content"]
        assert "看分时图" in last and "当日不提醒" in last and "本周不提醒" in last
        # fallback = 原纯文本卡(PushPlus 同源信息量)
        assert "触发条件（全中才提醒）" in c.fallback
        assert "10jqka.com.cn/600000" in c.fallback

    def test_structure_multi(self):
        from backend.services.second_surge import build_surge_card_v2
        c = build_surge_card_v2([self._item(), self._item(code="600001", name="乙")], P)
        assert c.title == "🔥 二波过前高 · 2只"
        assert "2只" in c.summary
        # 多只合并: 逐股结论+清单 → 建议 → 折叠 → 动作行(带股票名前缀)
        assert "**乙(600001)**" in c.elements[1]["content"]
        last = c.elements[-1]["content"]
        assert "测试：" in last and "乙：" in last


class TestScannerDedup:
    def test_daily_dedup_and_crossday_reset(self):
        from backend.services import second_surge_scanner as sc
        sc._fired_today.clear()
        sc._mark_fired("2026-07-08", "600000")
        assert sc._already_fired("2026-07-08", "600000") is True
        assert sc._already_fired("2026-07-08", "600001") is False
        # 跨日: 新日期首次登记应清掉昨日
        sc._mark_fired("2026-07-09", "600001")
        assert sc._already_fired("2026-07-08", "600000") is False
        assert sc._already_fired("2026-07-09", "600001") is True
        sc._fired_today.clear()

    def test_ma20_blocked_dedup_and_crossday_reset(self):
        from backend.services import second_surge_scanner as sc
        sc._ma20_blocked.clear()
        sc._mark_ma20_blocked("2026-07-08", "600000")
        assert sc._is_ma20_blocked("2026-07-08", "600000") is True
        assert sc._is_ma20_blocked("2026-07-08", "600001") is False
        # 跨日: 新日期首次登记应清掉昨日
        sc._mark_ma20_blocked("2026-07-09", "600001")
        assert sc._is_ma20_blocked("2026-07-08", "600000") is False
        assert sc._is_ma20_blocked("2026-07-09", "600001") is True
        sc._ma20_blocked.clear()
