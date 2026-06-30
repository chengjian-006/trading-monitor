"""飞书(Lark)自定义机器人推送 - v1.7.x.

与企微平行的第二推送通道, 走「群自定义机器人 webhook」:
  POST {webhook}  body={"msg_type":"text","content":{"text": ...}}
成功响应: 新版 {"code":0,...} / 旧版 {"StatusCode":0,...}。

自定义机器人不支持直接发图片(需自建应用上传 image_key), 故 K 线图仍只走企微,
飞书侧只发文本(信号/汇总/日报内容直接作纯文本发出, 已带 emoji/分隔线, 可读性 OK)。
"""
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# 飞书自定义机器人 webhook 域名(国内 feishu / 海外 larksuite)
LARK_HOOK_HINTS = ("open.feishu.cn", "open.larksuite.com")


def is_lark_webhook(url: str) -> bool:
    return bool(url) and any(h in url for h in LARK_HOOK_HINTS)


def _is_ok(data: dict) -> bool:
    # 新版 hook 成功返回 code=0; 旧版返回 StatusCode=0
    if not isinstance(data, dict):
        return False
    return data.get("code") == 0 or data.get("StatusCode") == 0


# 卡片标题栏配色 (飞书 header.template): buy 红 / sell 绿 / reduce·plunge 橙
DIRECTION_TEMPLATE = {"buy": "red", "sell": "green", "reduce": "orange", "plunge": "orange"}


async def _post(webhook: str, payload: dict, label: str) -> bool:
    """飞书自定义机器人 POST, 3 次重试. 不做生产环境闸门(由调用方决定是否发)."""
    if not webhook:
        return False
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook, json=payload)
                data = resp.json()
                if _is_ok(data):
                    logger.info(f"[lark] {label} 推送成功")
                    return True
                logger.error(f"[lark] {label} 响应错误: {data}")
                return False
        except Exception as e:
            logger.warning(f"[lark] {label} 第{attempt}次失败: {e}")
            if attempt < 3:
                await asyncio.sleep(2)
    logger.error(f"[lark] {label} 推送 3 次均失败")
    return False


async def post_lark_text(webhook: str, content: str) -> bool:
    """飞书发纯文本(兜底/旧用法)."""
    if not content:
        return False
    return await _post(webhook, {"msg_type": "text", "content": {"text": content}}, "text")


def _time_str() -> str:
    """返回时间 HH:MM (周X), 拼入标题栏右侧."""
    from datetime import datetime
    wd = ["一","二","三","四","五","六","日"][datetime.now().weekday()]
    return datetime.now().strftime(f"%H:%M（周{wd}）")


def _build_card(title: str, md_body: str, template: str = "blue",
                link_url: str = "", link_text: str = "查看分时图") -> dict:
    """交互卡片: 彩色标题栏(含时间) + lark_md 正文. link_url 非空时底部加跳转按钮."""
    full_title = f"{title}          {_time_str()}"
    elements: list = [
        {"tag": "div", "text": {"tag": "lark_md", "content": (md_body or "")[:4000]}},
    ]
    if link_url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": link_text},
                "type": "primary",
                "url": link_url,
            }],
        })
    return {
        # width_mode=fill: 卡片撑满聊天窗宽(v1.7.399, 手机端表格列宽再松一点; API 实测 v1/v2 都接受)
        "config": {"wide_screen_mode": True, "width_mode": "fill"},
        "header": {
            "title": {"tag": "plain_text", "content": (full_title or "盘面播报")[:120]},
            "template": template,
        },
        "elements": elements,
    }


async def post_lark_card(webhook: str, title: str, md_body: str, template: str = "blue",
                         link_url: str = "", link_text: str = "查看分时图") -> bool:
    """飞书发交互卡片. 正文走 lark_md; link_url 非空时底部带一个跳转按钮."""
    if not md_body:
        return False
    payload = {"msg_type": "interactive", "card": _build_card(title, md_body, template, link_url, link_text)}
    return await _post(webhook, payload, "card")


# ── 卡片 schema 2.0: 支持原生 table 组件(多行多列结构化信息用表格展示) ──

def md_element(content: str) -> dict:
    """2.0 markdown 元素(支持 **加粗**/链接/换行)。"""
    return {"tag": "markdown", "content": (content or "")[:4000]}


def collapsible_element(summary_md: str, detail_md: str, expanded: bool = False) -> dict:
    """折叠面板(schema2.0 collapsible_panel): header(summary) 常显, 点开展开 detail。
    用于长文(如"我的策略")默认收起只显第一句, 减少卡片堆叠。飞书不支持时整卡回退纯文本。"""
    return {
        "tag": "collapsible_panel",
        "expanded": bool(expanded),
        "header": {
            "title": {"tag": "markdown", "content": (summary_md or "")[:2000]},
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                     "color": "grey", "size": "12px 12px"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "elements": [{"tag": "markdown", "content": (detail_md or "")[:4000]}],
    }


def table_element(columns: list, rows: list, page_size: int = 10) -> dict:
    """原生表格元素。columns: [{name,display_name,data_type,width,horizontal_align}]; rows: [{col_name: value}]。
    data_type='options' 的单元格取 [{"text":..,"color":..}] 做彩色标签(红涨绿跌)。"""
    return {
        "tag": "table",
        "page_size": max(1, min(page_size, 10)),
        "row_height": "low",
        "header_style": {"background_style": "grey", "bold": True, "text_size": "normal"},
        "columns": columns,
        "rows": rows,
    }


def _build_card_v2(title: str, elements: list, template: str = "blue",
                   link_url: str = "", link_text: str = "查看完整报告") -> dict:
    full_title = f"{title}          {_time_str()}"
    body_elements = list(elements)
    if link_url:
        # schema 2.0 不支持 action 按钮容器 → 用 markdown 链接代替
        body_elements.append({"tag": "markdown", "content": f"[👉 {link_text}]({link_url})"})
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "width_mode": "fill"},
        "header": {
            "title": {"tag": "plain_text", "content": (full_title or "盘面播报")[:120]},
            "template": template,
        },
        "body": {"elements": body_elements},
    }


async def post_lark_card_v2(webhook: str, title: str, elements: list, template: str = "blue",
                            link_url: str = "", link_text: str = "查看完整报告") -> bool:
    """飞书发 schema 2.0 卡片(可含 table 组件)。失败返回 False, 由调用方回退到纯文本卡。"""
    if not webhook or not elements:
        return False
    payload = {"msg_type": "interactive",
               "card": _build_card_v2(title, elements, template, link_url, link_text)}
    return await _post(webhook, payload, "card2")


async def send_lark_test(webhook: str) -> tuple[bool, str]:
    """测试飞书 webhook 连通性。"""
    if not is_lark_webhook(webhook):
        return False, "Webhook 地址无效，应为 open.feishu.cn 的群机器人地址"
    body = (
        "**观潮已成功连接飞书！** ✅\n"
        "盘中触发买卖信号时，将自动推送到此群。\n\n"
        "信号类型：🟢买入　🔴卖出　🟡减仓"
    )
    payload = {"msg_type": "interactive", "card": _build_card("🟢 交易监控系统 · 飞书连接测试", body, "green")}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook, json=payload)
            data = resp.json()
            if _is_ok(data):
                return True, "推送成功！请检查飞书群是否收到卡片消息。"
            return False, f"推送失败: {data.get('msg') or data.get('StatusMessage') or data}"
    except Exception as e:
        return False, f"请求失败: {str(e)}"
