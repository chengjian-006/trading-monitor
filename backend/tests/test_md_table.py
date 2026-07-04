"""markdown 表格渲染 helper 测试 (v1.7.x, 修飞书手机端原生表格截断)。"""
from backend.services.lark_notifier import md_table_str, md_table


def test_basic_two_column():
    cols = [{"name": "stock", "display_name": "股票"}, {"name": "amp", "display_name": "净利变动"}]
    rows = [{"stock": "*ST尼雅 预增", "amp": "+1017%~1523%"}]
    s = md_table_str(cols, rows)
    assert s == ("| 股票 | 净利变动 |\n| --- | --- |\n| *ST尼雅 预增 | +1017%~1523% |")


def test_options_colored_label_to_text():
    cols = [{"name": "name", "display_name": "名称"}, {"name": "pct", "display_name": "涨幅"}]
    rows = [{"name": "某某", "pct": [{"text": "+9.8%", "color": "red"}]}]
    s = md_table_str(cols, rows)
    assert "+9.8%" in s and "color" not in s


def test_none_and_pipe_sanitized():
    cols = [{"name": "a", "display_name": "A"}, {"name": "b", "display_name": "B"}]
    rows = [{"a": None, "b": "x|y"}]
    s = md_table_str(cols, rows)
    # None → 空; 竖线转全角避免破坏表格
    assert "x／y" in s and "x|y" not in s.split("\n")[-1]


def test_empty_header_placeholder():
    cols = [{"name": "a", "display_name": "股票"}, {"name": "b", "display_name": ""}]
    rows = [{"a": "中金岭南", "b": "自选"}]
    s = md_table_str(cols, rows)
    assert s.split("\n")[0].count("|") == 3   # 两列 → 3个竖线


def test_md_table_returns_markdown_element():
    el = md_table([{"name": "a", "display_name": "A"}], [{"a": "1"}])
    assert el["tag"] == "markdown" and "| A |" in el["content"]
