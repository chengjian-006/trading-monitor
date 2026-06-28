"""推送偏好/快捷设置 (v1.7.464+): 飞书推送卡片内置快捷链接, 点一下即改设置.

四种设置(kind):
  mute       今日免打扰(仅静音飞书), 次日0点自动解除            target=''                       until=今日
  snooze     个股 N 天不再提醒(全渠道)                          target=code                     until=今日+N-1(含今日共N天)
  model_off  某买点模型仅今日关推送(全渠道), 次日恢复            target=signal_id                until=今日
  ack        某条信号标记已处理(全渠道当日不再推该 code+模型)    target=f'{code}|{signal_id}'    until=今日

快捷链接走 HMAC 签名(复用 auth.SECRET_KEY 派生, 不另存密钥), 防公网乱戳.
存活判定(在 SQL 层): revoked_at IS NULL 且 until_date >= 今日 — 故无需定时清理, 过期自动失效.

本模块只放纯逻辑(签名/判定/链接拼装); DB 读写在 models/repo/push_pref.py, HTTP 端点在 routers/quick.py.
"""
import hashlib
import hmac
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from backend.core.auth import SECRET_KEY

_SIGN_KEY = ("pushpref:" + SECRET_KEY).encode()

VALID_KINDS = ("mute", "snooze", "model_off", "ack", "unmute")  # unmute=恢复今日免打扰(撤销, 非新增偏好)


def _canonical(user_id, kind: str, target: str, days) -> str:
    """签名原文: 五要素拼成定长串, 任一被篡改签名即不符."""
    return f"{user_id}|{kind}|{target}|{days}"


def sign(payload: str) -> str:
    return hmac.new(_SIGN_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]


def verify(payload: str, sig: str | None) -> bool:
    return hmac.compare_digest(sign(payload), sig or "")


def sign_params(user_id, kind: str, target: str, days) -> str:
    return sign(_canonical(user_id, kind, target, days))


def verify_params(user_id, kind: str, target: str, days, sig: str | None) -> bool:
    return verify(_canonical(user_id, kind, target, days), sig)


def build_quick_link(site: str, user_id, kind: str, target: str = "", days=0) -> str:
    """拼一条带签名的快捷链接, 指向 /api/quick/set."""
    sig = sign_params(user_id, kind, target, days)
    q = urlencode({"u": user_id, "k": kind, "t": target, "d": days, "sig": sig})
    return f"{site.rstrip('/')}/api/quick/set?{q}"


def until_for(kind: str, days, today: date | None = None) -> date:
    """各 kind 的生效截止日(含当日). 今日域(mute/model_off/ack)=今日; snooze=今日+N-1."""
    today = today or date.today()
    if kind == "snooze":
        n = max(int(days or 0), 1)
        return today + timedelta(days=n - 1)
    return today


def decide(prefs: list[dict], code: str, signal_id: str, today: date | None = None) -> dict:
    """纯函数: 给定生效中的偏好列表(已在 SQL 层过滤未撤销+未过期), 判定一条信号该不该推.

    返回 {'suppress_all': bool, 'mute_lark': bool, 'reason': str}:
      suppress_all=True  → 全渠道都不推(snooze/model_off/ack 命中)
      mute_lark=True     → 仅飞书不推, 其他渠道照常(今日免打扰)
    """
    mute_lark = False
    for p in prefs:
        kind = p.get("kind")
        target = p.get("target", "") or ""
        if kind == "mute":
            mute_lark = True
        elif kind == "snooze" and code and target == code:
            return {"suppress_all": True, "mute_lark": False, "reason": f"个股静音中({code})"}
        elif kind == "model_off" and signal_id and target == signal_id:
            return {"suppress_all": True, "mute_lark": False, "reason": f"模型今日关({signal_id})"}
        elif kind == "ack" and code and signal_id and target == f"{code}|{signal_id}":
            return {"suppress_all": True, "mute_lark": False, "reason": "已标记处理"}
    return {"suppress_all": False, "mute_lark": mute_lark, "reason": ""}


# ── 推送卡片里的快捷动作行 ──

def build_quick_actions_md(site: str, user_id, code: str, signal_id: str, direction: str) -> str:
    """拼飞书卡片底部的快捷动作 markdown 链接行. site 为空或大盘预警(无个股)则只给「今日免打扰」.

    用 markdown 链接而非原生按钮: v2 卡(schema2.0)不支持 action 按钮容器, 链接两版卡通用.
    """
    site = (site or "").rstrip("/")
    if not site:
        return ""
    # 「今日关此模型」按需移除(2026-06-27): 只保留今日免打扰; model_off 后端能力保留(管理面板/历史链接仍可用)
    links = [f"[🔕 今日免打扰]({build_quick_link(site, user_id, 'mute')})"]
    return "　·　".join(links)
