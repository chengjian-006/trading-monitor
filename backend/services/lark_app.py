# -*- coding: utf-8 -*-
"""飞书应用机器人通道 (v1.7.631) — 卡片回调按钮, 点击不跳页面。

背景: 群自定义机器人(webhook)只支持 URL 跳转按钮, 点了必开浏览器新页(用户0716反馈体验差)。
企业自建应用机器人发的卡片支持 callback 按钮: 点击 → 飞书服务端回调(长连接, 免公网地址)
→ 服务器执行快捷设置 → 原地弹 toast, 全程不离开飞书。

启用条件(config.json): lark_app_enabled=true + lark_app_id/lark_app_secret/lark_app_chat_id 齐。
启用后所有飞书卡片改由应用机器人发到指定群(lark_notifier._post 路由), 失败自动回退 webhook;
关闭(默认)则一切走原 webhook, 零影响。

组成:
  - tenant_access_token 缓存获取
  - send_card_payload(): 把 {"msg_type":"interactive","card":...} 发到群(im/v1/messages)
  - callback_button()/button_row(): schema2.0 回调按钮与按钮行(column_set 横排)
  - start_ws_listener(): lark-oapi 长连接监听 card.action.trigger → push_pref.execute_quick_action
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

import httpx

from backend.core.config import load_config

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
_CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

# (token, 过期时刻monotonic)
_token_cache: tuple[str, float] = ("", 0.0)
_ws_thread: threading.Thread | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


def app_config() -> dict:
    cfg = load_config()
    return {
        "enabled": bool(cfg.get("lark_app_enabled", False)),
        "app_id": (cfg.get("lark_app_id") or "").strip(),
        "app_secret": (cfg.get("lark_app_secret") or "").strip(),
        "chat_id": (cfg.get("lark_app_chat_id") or "").strip(),
    }


def enabled() -> bool:
    """应用通道就绪: 开关开 + 凭证/群 id 齐。"""
    c = app_config()
    return c["enabled"] and bool(c["app_id"] and c["app_secret"] and c["chat_id"])


async def _get_token() -> str:
    """tenant_access_token, 提前 120s 刷新。失败返回 ''(调用方回退 webhook)。"""
    global _token_cache
    now = time.monotonic()
    if _token_cache[0] and now < _token_cache[1]:
        return _token_cache[0]
    c = app_config()
    if not (c["app_id"] and c["app_secret"]):
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_TOKEN_URL, json={
                "app_id": c["app_id"], "app_secret": c["app_secret"]})
            data = resp.json()
            if data.get("code") == 0:
                token = data.get("tenant_access_token", "")
                expire = float(data.get("expire", 3600))
                _token_cache = (token, now + max(expire - 120, 60))
                return token
            logger.warning(f"[lark_app] 取token失败: {data.get('code')} {data.get('msg')}")
    except Exception as e:
        logger.warning(f"[lark_app] 取token异常: {e}")
    return ""


async def send_card_payload(payload: dict) -> bool:
    """把 webhook 同款载荷 {"msg_type":"interactive","card":{...}} 经应用机器人发到配置群。"""
    card = (payload or {}).get("card")
    if not card:
        return False
    token = await _get_token()
    c = app_config()
    if not token or not c["chat_id"]:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _MSG_URL, params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={"receive_id": c["chat_id"], "msg_type": "interactive",
                      "content": json.dumps(card, ensure_ascii=False)},
            )
            data = resp.json()
            if data.get("code") == 0:
                return True
            logger.warning(f"[lark_app] 发卡失败: {data.get('code')} {data.get('msg')}")
    except Exception as e:
        logger.warning(f"[lark_app] 发卡异常: {e}")
    return False


async def list_chats() -> list[dict]:
    """机器人所在的群列表 [{chat_id, name}], 给「取 chat_id」辅助端点用。"""
    token = await _get_token()
    if not token:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_CHATS_URL, params={"page_size": 50},
                                    headers={"Authorization": f"Bearer {token}"})
            data = resp.json()
            if data.get("code") == 0:
                items = (data.get("data") or {}).get("items") or []
                return [{"chat_id": i.get("chat_id"), "name": i.get("name")} for i in items]
            logger.warning(f"[lark_app] 取群列表失败: {data.get('code')} {data.get('msg')}")
    except Exception as e:
        logger.warning(f"[lark_app] 取群列表异常: {e}")
    return []


# ── 回调按钮(schema 2.0) ──

def callback_button(text: str, value: dict, style: str = "default") -> dict:
    """schema2.0 回调按钮: 点击触发 card.action.trigger(不跳页面), value 原样回传。"""
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": style,                # default | primary | danger
        "width": "default",
        "behaviors": [{"type": "callback", "value": value}],
    }


def button_row(buttons: list[dict]) -> dict:
    """一行横排按钮(column_set, 每钮一列 auto 宽)。"""
    return {
        "tag": "column_set",
        "horizontal_spacing": "8px",
        "columns": [{"tag": "column", "width": "auto", "elements": [b]} for b in buttons],
    }


def quick_action_value(user_id, kind: str, target: str = "", days: int = 0) -> dict:
    """回调按钮的 value 载荷(与 push_pref.execute_quick_action 参数一一对应)。"""
    return {"k": kind, "u": int(user_id), "t": target or "", "d": int(days or 0)}


# ── 长连接回调监听 ──

def _handle_card_action_sync(data) -> object:
    """card.action.trigger 处理(在 lark ws 线程里跑): 解 value → 主循环执行 → 回 toast。"""
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

    try:
        value = data.event.action.value or {}
        k = str(value.get("k") or "")
        u = int(value.get("u") or 0)
        t = str(value.get("t") or "")
        d = int(value.get("d") or 0)
    except Exception:
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "参数错误"}})

    loop = _main_loop
    if loop is None or not k or not u:
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "服务未就绪"}})

    from backend.services import push_pref as pref_svc
    try:
        fut = asyncio.run_coroutine_threadsafe(pref_svc.execute_quick_action(u, k, t, d), loop)
        ok, label, detail = fut.result(timeout=10)
    except Exception as e:
        logger.warning(f"[lark_app] 卡片回调执行失败 k={k} t={t}: {e}")
        return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "操作失败, 稍后再试"}})
    logger.info(f"[lark_app] 卡片回调 {k} t={t} → {label}")
    return P2CardActionTriggerResponse({
        "toast": {"type": "success" if ok else "error", "content": f"{label}"[:60]},
    })


def start_ws_listener() -> bool:
    """启动 lark-oapi 长连接(守护线程), 监听卡片回调。已启动/未启用/缺依赖时安全跳过。"""
    global _ws_thread, _main_loop
    c = app_config()
    if not (c["enabled"] and c["app_id"] and c["app_secret"]):
        return False
    if _ws_thread is not None and _ws_thread.is_alive():
        return True
    try:
        import lark_oapi as lark
    except ImportError:
        logger.warning("[lark_app] 未安装 lark-oapi, 卡片回调不可用(pip install lark-oapi)")
        return False

    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = None
    if _main_loop is None:
        logger.warning("[lark_app] 无运行中事件循环, 跳过长连接启动")
        return False

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_card_action_trigger(_handle_card_action_sync)
               .build())
    client = lark.ws.Client(c["app_id"], c["app_secret"],
                            event_handler=handler, log_level=lark.LogLevel.WARNING)

    def _run():
        try:
            client.start()          # 阻塞(内部自动重连)
        except Exception as e:
            logger.error(f"[lark_app] 长连接退出: {e}")

    _ws_thread = threading.Thread(target=_run, name="lark-ws", daemon=True)
    _ws_thread.start()
    logger.info("[lark_app] 卡片回调长连接已启动")
    return True
