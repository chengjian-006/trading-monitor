# -*- coding: utf-8 -*-
"""藏龙岛观点采集器 — CLI 路径解析回退 + payload 解析过滤(纯函数, 不连库不跑子进程)."""
from backend.fetcher import lark_coach as lc


def test_resolve_cli_falls_back_to_usr_local_bin(monkeypatch):
    """v1.7.742: systemd 单元 PATH 不含 /usr/local/bin 时, which 首查失败要补搜常见位置。

    实测 v1.7.741 上线后服务进程报「lark-cli 未安装或不在 PATH」, 而 ssh 手跑正常 ——
    差异就是 systemd 的 Environment=PATH 没带 /usr/local/bin。
    """
    calls = []

    def fake_which(exe, path=None):
        calls.append(path)
        if path is None:
            return None                       # 进程 PATH 里找不到(systemd 场景)
        return f"/usr/local/bin/{exe}"        # 回退搜索命中

    monkeypatch.setattr(lc.shutil, "which", fake_which)
    assert lc._resolve_cli("lark-cli") == "/usr/local/bin/lark-cli"
    assert calls == [None, lc._FALLBACK_PATH]


def test_resolve_cli_prefers_process_path(monkeypatch):
    monkeypatch.setattr(lc.shutil, "which", lambda exe, path=None: "/opt/bin/lark-cli" if path is None else None)
    assert lc._resolve_cli("lark-cli") == "/opt/bin/lark-cli"


def test_resolve_cli_returns_raw_when_nowhere(monkeypatch):
    """哪都找不到 → 原样返回, 让子进程报 FileNotFoundError 走清晰报错链路。"""
    monkeypatch.setattr(lc.shutil, "which", lambda exe, path=None: None)
    assert lc._resolve_cli("lark-cli") == "lark-cli"


def test_build_env_injects_home_when_missing(monkeypatch):
    """v1.7.743: systemd 单元不设 HOME, lark-cli 靠 $HOME 找 ~/.lark-cli 授权配置,
    缺了报 not_configured(实测 v1.7.742 上线后退出码 3)。缺 HOME 要按 uid 补。"""
    import sys
    import types

    fake_pwd = types.SimpleNamespace(getpwuid=lambda uid: types.SimpleNamespace(pw_dir="/root"))
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)
    import os
    monkeypatch.setattr(os, "getuid", lambda: 0, raising=False)

    env = lc._build_env({"PATH": "/usr/bin"})
    assert env["HOME"] == "/root"
    assert env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] == "1"


def test_build_env_keeps_existing_home():
    env = lc._build_env({"PATH": "/usr/bin", "HOME": "/home/me"})
    assert env["HOME"] == "/home/me"


CFG = {"sender_open_id": "ou_coach", "coach_name": "藏龙岛", "chat_id": "oc_x"}


def _msg(sender_id, mid="om_1", content="藏龙岛：观点正文", t="2026-07-21 10:50"):
    return {"message_id": mid, "chat_id": "oc_x", "msg_type": "text",
            "create_time": t, "content": content,
            "sender": {"id": sender_id, "id_type": "open_id"}}


def test_parse_payload_keeps_only_coach_and_strips_prefix():
    payload = {"ok": True, "data": {"messages": [
        _msg("ou_coach"), _msg("ou_student", mid="om_2", content="学员:提问"),
    ]}}
    out = lc.parse_payload(payload, CFG)
    assert len(out) == 1
    assert out[0]["message_id"] == "om_1"
    assert out[0]["content"] == "观点正文"          # 去掉「藏龙岛：」前缀
    assert out[0]["posted_at"].strftime("%H:%M") == "10:50"


def test_parse_payload_raises_on_not_ok():
    import pytest
    with pytest.raises(lc.LarkCoachFetchError):
        lc.parse_payload({"ok": False, "error": "token expired"}, CFG)


def test_extract_image_key():
    """图片消息 content 形如 "[Image: img_v3_xxx]", 供页面取图与群转发复用。"""
    assert lc.extract_image_key("[Image: img_v3_0213q_a5645a82-69e8-49e9-a2ce-a08eeab14f0g]") \
        == "img_v3_0213q_a5645a82-69e8-49e9-a2ce-a08eeab14f0g"
    assert lc.extract_image_key("纯文本没有图") is None
    assert lc.extract_image_key("") is None
    assert lc.extract_image_key(None) is None


# ── 卡片转发(relay_style=card, v1.7.751; 正文整体加粗=0722方案B) ──

def test_bold_lines_md():
    from backend.services.lark_coach_scanner import _bold_lines_md
    assert _bold_lines_md("稳稳的赚钱") == "**稳稳的赚钱**"
    # 多行逐行包粗(md 加粗不跨行), 空行保留
    assert _bold_lines_md("第一行\n\n第二行") == "**第一行**\n\n**第二行**"


def test_build_relay_card_text_with_quote():
    # v1.7.755: 去蓝色 header 栏, 标题改小号蓝字首行 + 分隔线; 正文 heading, 学员引用灰色常规字号
    from backend.services.lark_coach_scanner import _build_relay_card
    card = _build_relay_card("藏龙岛", "07-22 09:41", text="核心票：坚定持有\n----- 学员：老师怎么看")
    assert "header" not in card                            # 不再用固定大字号的蓝色 header 栏
    tags = [e["tag"] for e in card["elements"]]
    assert tags == ["markdown", "hr", "markdown", "markdown"]   # 标题行 + 分隔线 + 正文 + 学员引用
    # 标题行: 小号(normal)蓝色加粗
    assert card["elements"][0]["content"] == "<font color='blue'>**藏龙岛观点 · 07-22 09:41**</font>"
    assert card["elements"][0]["text_size"] == "normal"
    # 正文: 整体加粗 + 大一号(heading)
    assert card["elements"][2]["content"] == "**核心票：坚定持有**"
    assert card["elements"][2]["text_size"] == "heading"
    # 学员引用: 灰色常规字号
    assert "老师怎么看" in card["elements"][3]["content"]
    assert "<font color='grey'>" in card["elements"][3]["content"]
    assert "text_size" not in card["elements"][3]


def test_build_relay_card_image_and_blank():
    from backend.services.lark_coach_scanner import _build_relay_card
    card = _build_relay_card("藏龙岛", "07-22 10:00", img_key="img_v3_abc")
    # 图片卡也带标题行 + 分隔线, 再接图片
    tags = [e["tag"] for e in card["elements"]]
    assert tags == ["markdown", "hr", "img"]
    assert card["elements"][0]["text_size"] == "normal"
    assert card["elements"][2] == {"tag": "img", "img_key": "img_v3_abc",
                                   "alt": {"tag": "plain_text", "content": "观点图片"}}
    # 全空白正文兜底: 标题行 + 分隔线 + 兜底正文, 不发只剩标题的空卡
    blank = _build_relay_card("藏龙岛", "", text="   ")
    assert [e["tag"] for e in blank["elements"]] == ["markdown", "hr", "markdown"]
    assert blank["elements"][0]["content"] == "<font color='blue'>**藏龙岛观点**</font>"
