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
import time
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from backend.core.auth import SECRET_KEY

_SIGN_KEY = ("pushpref:" + SECRET_KEY).encode()

# 快捷链接有效期: 签发后 48 小时(覆盖「当天推送+晚间复盘」), 过期链接不可再操作(防重放)。
QUICK_LINK_TTL_SECONDS = 48 * 3600

VALID_KINDS = ("mute", "snooze", "model_off", "ack", "unmute",  # unmute=恢复今日免打扰(撤销, 非新增偏好)
               "stop_snooze",     # stop_snooze=止损强制升级专用静音(target=code), 仅升级检查消费, 不压这只票的其它推送
               "ma_watch_snooze",  # ma_watch_snooze=尾盘破位警戒专用静音(target=code), 仅警戒卡消费, 同上不串台
               "surge_snooze")     # surge_snooze=二波过前高提醒专用静音(target=code), 仅二波扫描器消费, 同上不串台


def _canonical(user_id, kind: str, target: str, days, exp) -> str:
    """签名原文: 六要素拼成定长串(含过期时间戳 exp), 任一被篡改签名即不符."""
    return f"{user_id}|{kind}|{target}|{days}|{exp}"


def sign(payload: str) -> str:
    return hmac.new(_SIGN_KEY, payload.encode(), hashlib.sha256).hexdigest()[:32]


def verify(payload: str, sig: str | None) -> bool:
    return hmac.compare_digest(sign(payload), sig or "")


def sign_params(user_id, kind: str, target: str, days, exp) -> str:
    return sign(_canonical(user_id, kind, target, days, exp))


def verify_params(user_id, kind: str, target: str, days, exp, sig: str | None) -> bool:
    """exp 纳入 HMAC 原文: 无 exp 的旧链接一律拒绝(旧卡片自然淘汰); exp 被篡改签名即不符.
    注意本函数只验真伪, exp 是否已过期由调用方(routers/quick.py)另行判定并给友好提示."""
    if exp is None or str(exp) == "":
        return False
    return verify(_canonical(user_id, kind, target, days, exp), sig)


def build_quick_link(site: str, user_id, kind: str, target: str = "", days=0, exp=None) -> str:
    """拼一条带签名的快捷链接, 指向 /api/quick/set. 默认签发后 48 小时过期(防永久重放)."""
    exp = int(exp) if exp is not None else int(time.time()) + QUICK_LINK_TTL_SECONDS
    sig = sign_params(user_id, kind, target, days, exp)
    q = urlencode({"u": user_id, "k": kind, "t": target, "d": days, "exp": exp, "sig": sig})
    return f"{site.rstrip('/')}/api/quick/set?{q}"


def until_for(kind: str, days, today: date | None = None) -> date:
    """各 kind 的生效截止日(含当日). 今日域(mute/model_off/ack)=今日; snooze族=今日+N-1."""
    today = today or date.today()
    if kind in ("snooze", "stop_snooze", "ma_watch_snooze", "surge_snooze"):
        n = max(int(days or 0), 1)
        return today + timedelta(days=n - 1)
    return today


def days_until_week_end(today: date | None = None) -> int:
    """含今日到本周日的自然日数(周一=7 … 周日=1). 供「本周不提醒」算 snooze 天数。"""
    today = today or date.today()
    return 7 - today.weekday()


def stop_snooze_active(prefs: list[dict], code: str) -> bool:
    """止损升级检查专用: 生效偏好里是否有这只票的 stop_snooze(已在 SQL 层过滤未撤销+未过期)。
    独立于 decide(): 故点了止损升级静音, 这只票的买卖点/异动照常推。"""
    for p in prefs:
        if p.get("kind") == "stop_snooze" and code and (p.get("target") or "") == code:
            return True
    return False


def ma_watch_snooze_active(prefs: list[dict], code: str) -> bool:
    """尾盘破位警戒专用: 生效偏好里是否有这只票的 ma_watch_snooze。
    独立于 decide(): 点了警戒静音, 这只票的买卖点/异动照常推。"""
    for p in prefs:
        if p.get("kind") == "ma_watch_snooze" and code and (p.get("target") or "") == code:
            return True
    return False


def surge_snooze_active(prefs: list[dict], code: str) -> bool:
    """二波过前高提醒专用: 生效偏好里是否有这只票的 surge_snooze。
    独立于 decide(): 点了二波静音, 这只票的买卖点/异动照常推。"""
    for p in prefs:
        if p.get("kind") == "surge_snooze" and code and (p.get("target") or "") == code:
            return True
    return False


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


def build_stop_escalation_actions_md(site: str, user_id, code: str, today: date | None = None) -> str:
    """止损升级红卡底部两开关: 当日不提醒 / 本周不提醒。均走 stop_snooze(target=code),
    只静音这只票的止损升级, 不影响其它推送。site 为空则不给链接(本地/非生产)。"""
    site = (site or "").rstrip("/")
    if not site:
        return ""
    week_days = days_until_week_end(today)
    today_link = build_quick_link(site, user_id, "stop_snooze", target=code, days=1)
    week_link = build_quick_link(site, user_id, "stop_snooze", target=code, days=week_days)
    return f"[🔕 当日不提醒]({today_link})　·　[🔕 本周不提醒]({week_link})"


def build_ma_watch_actions_md(site: str, user_id, code: str, today: date | None = None) -> str:
    """尾盘破位警戒卡逐票两开关: 当日不提醒 / 本周不提醒。走 ma_watch_snooze(target=code),
    只静音这只票的破位警戒, 不影响其它推送。site 为空则不给链接(本地/非生产)。"""
    site = (site or "").rstrip("/")
    if not site:
        return ""
    week_days = days_until_week_end(today)
    today_link = build_quick_link(site, user_id, "ma_watch_snooze", target=code, days=1)
    week_link = build_quick_link(site, user_id, "ma_watch_snooze", target=code, days=week_days)
    return f"[🔕 当日不提醒]({today_link})　·　[🔕 本周不提醒]({week_link})"


def build_surge_actions_md(site: str, user_id, code: str, today: date | None = None) -> str:
    """二波过前高提醒卡逐票两开关: 当日不提醒 / 本周不提醒。走 surge_snooze(target=code),
    只静音这只票的二波提醒, 不影响其它推送。site 为空则不给链接(本地/非生产)。"""
    site = (site or "").rstrip("/")
    if not site:
        return ""
    week_days = days_until_week_end(today)
    today_link = build_quick_link(site, user_id, "surge_snooze", target=code, days=1)
    week_link = build_quick_link(site, user_id, "surge_snooze", target=code, days=week_days)
    return f"[🔕 当日不提醒]({today_link})　·　[🔕 本周不提醒]({week_link})"
