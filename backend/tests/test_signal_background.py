"""个股买卖卡"背景标签"纯逻辑测试 (v1.7.x).

融合黑天鹅(风险公告/财务红旗) + 业绩预增到买卖点卡片。只测可纯函数化的标签构建;
DB 取数(fetch_background)是集成层, 不在此测。
"""
from backend.services import signal_background as sb


class TestBuildTags:
    def test_positive_forecast_with_amp(self):
        tags = sb.build_background_tags(
            forecast={"predict_type": "预增", "amp_lower": 50, "amp_upper": 80},
            fin_risk=None, risk_anns=[], bold=False)
        assert any("业绩预增" in t and "预增" in t and "50" in t and "80" in t for t in tags)

    def test_forecast_no_amp_still_shows_type(self):
        tags = sb.build_background_tags(
            forecast={"predict_type": "扭亏", "amp_lower": None, "amp_upper": None},
            fin_risk=None, risk_anns=[], bold=False)
        assert any("扭亏" in t for t in tags)

    def test_fin_risk_high_shows(self):
        tags = sb.build_background_tags(
            forecast=None, fin_risk={"score": 85}, risk_anns=[], bold=False)
        assert any("黑天鹅" in t and "85" in t and "高危" in t for t in tags)

    def test_fin_risk_mid_shows(self):
        tags = sb.build_background_tags(
            forecast=None, fin_risk={"score": 55}, risk_anns=[], bold=False)
        assert any("黑天鹅" in t and "中危" in t for t in tags)

    def test_fin_risk_low_hidden(self):
        # 关注级(<50)不上标签, 防噪
        tags = sb.build_background_tags(
            forecast=None, fin_risk={"score": 30}, risk_anns=[], bold=False)
        assert not any("黑天鹅" in t for t in tags)

    def test_recent_risk_ann_shows(self):
        tags = sb.build_background_tags(
            forecast=None, fin_risk=None,
            risk_anns=[{"tags": "商誉减值", "title": "关于计提商誉减值的公告", "ann_date": "2026-07-02"}],
            bold=False)
        assert any("风险公告" in t and "商誉减值" in t and "07-02" in t for t in tags)

    def test_risk_ann_falls_back_to_title_when_no_tags(self):
        tags = sb.build_background_tags(
            forecast=None, fin_risk=None,
            risk_anns=[{"tags": "", "title": "收到交易所问询函", "ann_date": "2026-07-01"}],
            bold=False)
        assert any("问询" in t for t in tags)

    def test_empty_returns_nothing(self):
        assert sb.build_background_tags(forecast=None, fin_risk=None, risk_anns=[], bold=False) == []

    def test_risk_before_forecast(self):
        # 风险优先看: 黑天鹅在预增前
        tags = sb.build_background_tags(
            forecast={"predict_type": "预增", "amp_lower": 50, "amp_upper": 80},
            fin_risk={"score": 85}, risk_anns=[], bold=False)
        assert "黑天鹅" in tags[0]
        assert any("业绩预增" in t for t in tags)

    def test_bold_wraps_key_tokens(self):
        tags = sb.build_background_tags(
            forecast={"predict_type": "预增", "amp_lower": 50, "amp_upper": 80},
            fin_risk=None, risk_anns=[], bold=True)
        assert any("**" in t for t in tags)

    def test_render_joins_with_newline(self):
        tags = ["⚠️ a", "📈 b"]
        assert sb.render_tags_text(tags) == "⚠️ a\n📈 b"
        assert sb.render_tags_text([]) == ""
