"""推送偏好/快捷设置 (v1.7.464+): 飞书推送卡片内置快捷链接, 点一下即改设置.

五种设置(kind):
  mute       今日免打扰(仅静音飞书), 次日0点自动解除            target=''                       until=今日
  snooze     个股 N 天不再提醒(全渠道)                          target=code                     until=今日+N-1(含今日共N天)
  model_off  某买点模型仅今日关推送(全渠道), 次日恢复            target=signal_id                until=今日
  ack        某条信号标记已处理(全渠道当日不再推该 code+模型)    target=f'{code}|{signal_id}'    until=今日
  mark_sold  标记已卖出(target=code), 压住该票所有卖出/持仓类提醒  target=code                 until=远期(手动撤销或新交割单导入)

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
               "surge_snooze",    # surge_snooze=二波过前高提醒专用静音(target=code), 仅二波扫描器消费, 同上不串台
               "snooze_until_retrigger",  # 条件型个股静音(target=code|signal_id): 压住连日重复, 该票该模型
               "mark_sold")       # mark_sold=标记已卖出(target=code): 压住该票所有卖出/持仓类提醒, 远期过期靠手动撤销或新交割单导入
                                          # 先安静≥1交易日、之后再触发(真新一轮突破)时由引擎撤销放行

# 条件型静音无固定到期日, 用远期 until_date 让 SQL 存活判定(until_date>=今日)不误杀, 靠引擎再触发时撤销
_RETRIGGER_FAR_DAYS = 3650


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
    """各 kind 的生效截止日(含当日). 今日域(mute/model_off/ack)=今日; snooze族=今日+N-1;
    条件型(snooze_until_retrigger)=远期(靠引擎撤销, 非日期过期)."""
    today = today or date.today()
    if kind in ("snooze_until_retrigger", "mark_sold"):
        return today + timedelta(days=_RETRIGGER_FAR_DAYS)
    if kind in ("snooze", "stop_snooze", "ma_watch_snooze", "surge_snooze"):
        n = max(int(days or 0), 1)
        return today + timedelta(days=n - 1)
    return today


def retrigger_verdict(prefs: list[dict], code: str, signal_id: str,
                      triggered_prev_trading_day: bool) -> dict:
    """条件型静音判定(纯函数): 该票该模型有活跃 snooze_until_retrigger 时 ——
      上一交易日也触发(连续) → 压住(suppress); 上一交易日没触发(≥1日安静, 真新一轮突破) → 放行+撤销。
    triggered_prev_trading_day 由调用方(notifier)查 cfzy_biz_signals 得到。
    返回 {has_snooze, suppress, revoke_id}. 无匹配静音 → has_snooze=False。"""
    tgt = f"{code}|{signal_id}"
    for p in prefs:
        if p.get("kind") == "snooze_until_retrigger" and (p.get("target") or "") == tgt:
            if triggered_prev_trading_day:
                return {"has_snooze": True, "suppress": True, "revoke_id": None}
            return {"has_snooze": True, "suppress": False, "revoke_id": p.get("id")}
    return {"has_snooze": False, "suppress": False, "revoke_id": None}


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


def mark_sold_active(prefs: list[dict], code: str) -> bool:
    """检查该票是否被标记为「已卖出」。生效偏好里有 mark_sold(target=code) → 压住所有卖出/持仓类提醒。
    独立于 decide(): 买入信号(该票在自选仍正常推), 仅压卖出类+持仓守护+异动+止损升级+破位警戒。"""
    for p in prefs:
        if p.get("kind") == "mark_sold" and code and (p.get("target") or "") == code:
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


# ── 快捷动作公共执行器 (v1.7.631): HTTP落地页(routers/quick.py)与飞书卡片回调(lark_app)共用 ──

KIND_LABEL = {
    "mute": "今日免打扰(仅飞书)",
    "snooze": "个股静音",
    "model_off": "今日关此模型",
    "ack": "标记已处理",
    "stop_snooze": "止损提醒静音",
    "ma_watch_snooze": "破位警戒静音",
    "surge_snooze": "二波提醒静音",
    "snooze_until_retrigger": "个股静音·直到再突破",
    "mark_sold": "标记已卖出",
}


async def execute_quick_action(u: int, k: str, t: str, d: int) -> tuple[bool, str, str]:
    """执行一条快捷设置(落库+联动), 返回 (ok, 动作名, 结果说明)。

    调用方需先做各自的鉴权(HTTP 路由验 HMAC 签名; 卡片回调由飞书长连接身份保证)。
    """
    from backend.models.repo import push_pref as pref_repo

    if k not in VALID_KINDS:
        return False, "无效操作", "未知的设置类型。"

    # 恢复今日免打扰: 撤销当日 mute, 不新增偏好
    if k == "unmute":
        n = await pref_repo.revoke_kind(u, "mute")
        if n:
            return True, "已恢复今日推送", "今日免打扰已取消，飞书推送恢复正常。"
        return True, "无需恢复", "当前没有生效的今日免打扰。"

    until = until_for(k, d)
    await pref_repo.add_pref(u, k, t, until)

    # 今日免打扰: 往未被静音的渠道发一条可随时点的恢复入口
    if k == "mute":
        await _send_mute_recovery(u)

    # 标记已卖出: 该票从持仓降级为观察(status hold→watch), 压掉后续卖出/持仓类提醒(买点照常)
    if k == "mark_sold" and t:
        try:
            from backend.models import repository
            await repository.update_stock(t, u, status="watch")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[quick] 标记已卖出翻转持仓状态失败({t}): {e}")

    label = KIND_LABEL.get(k, k)
    if k == "mute":
        detail = "今天剩余的飞书推送已静音，明日自动恢复。"
    elif k == "snooze":
        detail = (f"已对 {t} 静音至 {until.strftime('%m-%d')}，"
                  f"期间不再推送其全部信号（含卖点/止损；止损连续未执行的升级红卡、尾盘破位警戒仍会提醒）。")
    elif k == "model_off":
        detail = f"已关闭「{t}」今日推送，明日自动恢复。"
    elif k == "stop_snooze":
        detail = f"已静音 {t} 的止损升级提醒至 {until.strftime('%m-%d')}（其它买卖点/异动照常）。"
    elif k == "ma_watch_snooze":
        detail = f"已静音 {t} 的尾盘破位警戒至 {until.strftime('%m-%d')}（其它买卖点/异动照常）。"
    elif k == "surge_snooze":
        detail = f"已静音 {t} 的二波过前高提醒至 {until.strftime('%m-%d')}（其它买卖点/异动照常）。"
    elif k == "snooze_until_retrigger":
        _code = t.split("|", 1)[0]
        detail = f"已静音 {_code}，直到它安静≥1个交易日后再次触发该买点时才重新提醒。"
    elif k == "mark_sold":
        detail = f"已标记 {t} 为已卖出，已从持仓列表移出（转为自选观察），不再推送该票的卖出/减仓/持仓提醒。导入新交割单后自动归位。"
    else:  # ack
        detail = "该信号已标记处理，当日不再重复提醒。"
    return True, f"已设置：{label}", detail


async def _send_mute_recovery(user_id: int) -> None:
    """开启今日免打扰后, 往微信/PushPlus(未被静音的渠道)发一条带「恢复」链接的确认, 随时可点。"""
    import logging
    try:
        from backend.core.config import load_config
        from backend.services import notifier
        site = (load_config().get("site_url", "") or "").rstrip("/")
        if not site:
            return
        link = build_quick_link(site, user_id, "unmute")
        content = ("已开启**今日免打扰**(仅静音飞书, 微信照常), 明日 0 点自动恢复。\n\n"
                   f"想提前恢复 👉 [点此恢复今日推送]({link})")
        await notifier._fanout_pushplus("🔕 已开启今日免打扰", content)
    except Exception as e:
        logging.getLogger(__name__).warning(f"[quick] 免打扰恢复确认发送失败: {e}")


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
    # 个股静音: 指向落地页选到期语义(仅今日/本周/直到再突破), 只对有个股的买卖信号加(大盘预警无 code)
    if code and signal_id:
        links.append(f"[🔕 静音此股]({build_signal_snooze_link(site, user_id, code, signal_id)})")
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


def build_mark_sold_md(site: str, user_id, code: str, name: str = "") -> str:
    """个股卖出类推送底部「已卖出」按钮。走 mark_sold(target=code), 远期过期靠手动撤销或新交割单导入。"""
    site = (site or "").rstrip("/")
    if not site or not code:
        return ""
    link = build_quick_link(site, user_id, "mark_sold", target=code, days=365)
    return f"[✅ 已卖出]({link})"


# ── 应用机器人模式: 快捷动作变真回调按钮(点击不跳页, 原地toast; v1.7.631) ──

def build_quick_action_button_rows(user_id, code: str, signal_id: str, direction: str) -> list[dict]:
    """信号卡快捷动作按钮行(schema2.0 回调按钮, 仅应用机器人通道用)。

    行1: [✅ 已卖出](卖/减仓类) + [🔕 今日免打扰]
    行2(有个股): [🔕 静音今日] [🔕 静音本周] [🔕 静到再突破]
    与 build_quick_actions_md(webhook 链接版)动作对齐, 静音三档从落地页上提为直接按钮。
    """
    from backend.services import lark_app

    rows: list[dict] = []
    row1 = []
    if direction in ("sell", "reduce") and code:
        row1.append(lark_app.callback_button(
            "✅ 已卖出", lark_app.quick_action_value(user_id, "mark_sold", code, 365), style="primary"))
    row1.append(lark_app.callback_button(
        "🔕 今日免打扰", lark_app.quick_action_value(user_id, "mute")))
    rows.append(lark_app.button_row(row1))
    if code and signal_id:
        rows.append(lark_app.button_row([
            lark_app.callback_button(
                "🔕 静音今日", lark_app.quick_action_value(user_id, "snooze", code, 1)),
            lark_app.callback_button(
                "🔕 静音本周", lark_app.quick_action_value(user_id, "snooze", code, days_until_week_end())),
            lark_app.callback_button(
                "🔕 静到再突破",
                lark_app.quick_action_value(user_id, "snooze_until_retrigger", f"{code}|{signal_id}", 0)),
        ]))
    return rows


def build_surge_action_button_rows(user_id, code: str) -> list[dict]:
    """二波过前高卡逐票静音按钮行(应用机器人通道): 当日不提醒 / 本周不提醒。"""
    from backend.services import lark_app
    return [lark_app.button_row([
        lark_app.callback_button(
            "🔕 当日不提醒", lark_app.quick_action_value(user_id, "surge_snooze", code, 1)),
        lark_app.callback_button(
            "🔕 本周不提醒", lark_app.quick_action_value(user_id, "surge_snooze", code, days_until_week_end())),
    ])]


# ── 个股信号静音: 落地页选到期语义(仅今日/本周/直到再突破) ──

def build_signal_snooze_link(site: str, user_id, code: str, signal_id: str,
                             name: str = "", exp=None) -> str:
    """指向静音落地页的签名链接(kind=snooze 占位签名, 携 code|signal_id + 名称)。落地页再给三档。"""
    exp = int(exp) if exp is not None else int(time.time()) + QUICK_LINK_TTL_SECONDS
    target = f"{code}|{signal_id}"
    sig = sign_params(user_id, "snooze", target, 0, exp)
    q = urlencode({"u": user_id, "t": target, "n": name, "exp": exp, "sig": sig})
    return f"{site.rstrip('/')}/api/quick/snooze-options?{q}"


def render_snooze_options_page(site: str, user_id, code: str, name: str, signal_id: str) -> str:
    """静音落地页: 三个按钮各带独立签名链接 —— 仅今日 / 本周(到周日) / 直到再次突破。"""
    site = site.rstrip("/")
    today_link = build_quick_link(site, user_id, "snooze", target=code, days=1)
    week_link = build_quick_link(site, user_id, "snooze", target=code, days=days_until_week_end())
    retrig_link = build_quick_link(site, user_id, "snooze_until_retrigger",
                                   target=f"{code}|{signal_id}", days=0)
    disp = f"{name}({code})" if name else code
    btn = ("display:block;margin:12px 0;padding:14px;border-radius:12px;text-decoration:none;"
           "font-size:16px;font-weight:600;")
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>静音设置</title></head>
<body style="margin:0;font-family:-apple-system,'PingFang SC',sans-serif;background:#f5f6f8;">
<div style="max-width:420px;margin:12vh auto;padding:24px;background:#fff;border-radius:14px;
            box-shadow:0 2px 16px rgba(0,0,0,.06);">
  <div style="font-size:18px;font-weight:700;color:#333;">🔕 静音 {disp}</div>
  <div style="margin:8px 0 18px;font-size:13px;color:#888;line-height:1.6;">选择静音时长。「仅今日/本周」静音该票<b>全部信号（含卖点/止损）</b>；「直到再次突破」只静音该票该买点：</div>
  <a href="{today_link}" style="{btn}background:#eef2ff;color:#3730a3;">仅今日<span style="font-weight:400;font-size:12px;color:#888;"> · 明日自动恢复</span></a>
  <a href="{week_link}" style="{btn}background:#ecfdf5;color:#065f46;">本周<span style="font-weight:400;font-size:12px;color:#888;"> · 到本周日</span></a>
  <a href="{retrig_link}" style="{btn}background:#fff7ed;color:#9a3412;">直到再次突破<span style="font-weight:400;font-size:12px;color:#888;"> · 安静≥1日后重新触发才再提醒</span></a>
</div></body></html>"""
