"""微信(PushPlus)推送内容与飞书保持一致 — 验证 PushPlus 正文由飞书同一份 lark_md 转 HTML 而来."""
from backend.services import notifier as n


class TestLarkMdToHtml:
    def test_bold_code_newline(self):
        assert n._lark_md_to_html("**多氟多** `002407`\n现价 **32.50**") == \
            "<b>多氟多</b> <code>002407</code><br>现价 <b>32.50</b>"

    def test_plain_text_unchanged_except_newline(self):
        assert n._lark_md_to_html("纯文本一行\n第二行") == "纯文本一行<br>第二行"


class TestSignalConsistency:
    def test_pushplus_signal_is_lark_md_converted(self):
        # 微信信号正文 == 飞书 lark_md 正文转 HTML, 二者同源 → 内容/分节/加粗一致
        args = ("002407", "多氟多", "回踩10MA缩量后突破昨高", "buy", 32.50,
                "缩量回踩MA10 | 触发价32.50 | 计划: 站稳MA10持有/破MA10×0.98卖半")
        lark_md = n._build_lark_signal(*args)
        pushplus_html = n._build_pushplus_html(*args)
        assert pushplus_html == n._lark_md_to_html(lark_md)
        # 飞书加粗的数字在微信侧也是 <b> 加粗(不再是字面星号)
        assert "**" not in pushplus_html
