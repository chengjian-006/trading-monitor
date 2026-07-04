"""预增榜抓取 选行逻辑 测试 (v1.7.x, 修"净利变动为空"bug)。

东财按财务指标每家返回多行(每股收益/扣非/营收/归母净利润), 须选"归母净利润"标准口径行,
不能无脑取第一行(可能是每股收益行, 东财对EPS不给同比幅度 → 净利变动空)。
"""
from backend.fetcher.earnings_data import pick_forecast_row


def _row(finance, lo=None, up=None, ptype="预增"):
    return {"PREDICT_FINANCE": finance, "ADD_AMP_LOWER": lo, "ADD_AMP_UPPER": up,
            "PREDICT_TYPE": ptype, "PREDICT_CONTENT": f"{finance}内容"}


class TestPickForecastRow:
    def test_picks_parent_netprofit_over_eps(self):
        # 中金岭南真实场景: 每股收益行(amp空)排前, 归母净利润行(有amp)排后 → 取归母
        rows = [_row("每股收益", None, None),
                _row("扣除非经常性损益后的净利润", 88.91, 118.4),
                _row("归属于上市公司股东的净利润", 87.89, 114.73)]
        r = pick_forecast_row(rows)
        assert r["PREDICT_FINANCE"] == "归属于上市公司股东的净利润"
        assert r["ADD_AMP_LOWER"] == 87.89

    def test_falls_to_deduct_when_no_parent(self):
        rows = [_row("每股收益", None, None),
                _row("扣除非经常性损益后的净利润", 88.91, 118.4)]
        r = pick_forecast_row(rows)
        assert r["PREDICT_FINANCE"] == "扣除非经常性损益后的净利润"

    def test_prefers_any_amp_over_null(self):
        # 无归母/扣非, 但营收有amp → 取有amp的(总比空好)
        rows = [_row("每股收益", None, None), _row("营业收入", 115, 145)]
        r = pick_forecast_row(rows)
        assert r["ADD_AMP_LOWER"] == 115

    def test_all_null_amp_prefers_parent_row(self):
        # 都无amp: 优先归母行(content更完整), 否则首行
        rows = [_row("每股收益", None, None), _row("归属于上市公司股东的净利润", None, None)]
        r = pick_forecast_row(rows)
        assert r["PREDICT_FINANCE"] == "归属于上市公司股东的净利润"

    def test_single_eps_row_fallback(self):
        rows = [_row("每股收益", None, None)]
        r = pick_forecast_row(rows)
        assert r["PREDICT_FINANCE"] == "每股收益"

    def test_empty_returns_none(self):
        assert pick_forecast_row([]) is None
