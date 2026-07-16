"""推送偏好/快捷设置 (v1.7.464+): 飞书推送卡片内置快捷链接, 点一下即改设置.

设置种类(kind):
  model_off  某买点模型仅今日关推送(全渠道), 次日恢复            target=signal_id                until=今日
  ack        某条信号标记已处理(全渠道当日不再推该 code+模型)    target=f'{code}|{signal_id}'    until=今日
  mark_sold  标记已卖出(target=code), 压住该票所有卖出/持仓类提醒  target=code                 until=远期(手动撤销或新交割单导入)
  ma_alert_* 均线到线提醒·一次性订阅(10/20/60日线)                target=code                     until=今日+59(60天未触发作废)

已拆除(2026-07 用户拍板, 库里旧行直接不再生效):
  mute   今日免打扰(仅静音飞书) — 整个功能移除, 含 unmute 恢复入口
  snooze 个股 N 天全渠道静音(仅今日/本周档) — 整个功能移除; 条件式「直到再次突破」
         (snooze_until_retrigger, 只压该票该买点)保留

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

VALID_KINDS = ("model_off", "ack",
               "stop_snooze",     # stop_snooze=止损强制升级专用静音(target=code), 仅升级检查消费, 不压这只票的其它推送
               "ma_watch_snooze",  # ma_watch_snooze=尾盘破位警戒专用静音(target=code), 仅警戒卡消费, 同上不串台
               "surge_snooze",    # surge_snooze=二波过前高提醒专用静音(target=code), 仅二波扫描器消费, 同上不串台
               "snooze_until_retrigger",  # 条件型个股静音(target=code|signal_id): 压住连日重复, 该票该模型
               "mark_sold",       # mark_sold=标记已卖出(target=code): 压住该票所有卖出/持仓类提醒, 远期过期靠手动撤销或新交割单导入
                                          # 先安静≥1交易日、之后再触发(真新一轮突破)时由引擎撤销放行
               "ma_alert_10", "ma_alert_20", "ma_alert_60")  # 均线到线提醒·一次性订阅(target=code):
                                          # 现价进入 N 日均线±0.3%贴线带推一次即失效; 扫描在 services/ma_touch_alert.py

# 条件型静音无固定到期日, 用远期 until_date 让 SQL 存活判定(until_date>=今日)不误杀, 靠引擎再触发时撤销
_RETRIGGER_FAR_DAYS = 3650

# 均线到线提醒(一次性订阅): kind → 均线周期; 有效期60天(过期自动作废, 防僵尸订阅永久挂着)
MA_ALERT_KINDS = {"ma_alert_10": 10, "ma_alert_20": 20, "ma_alert_60": 60}
MA_ALERT_DAYS = 60


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
    """各 kind 的生效截止日(含当日). 今日域(model_off/ack)=今日; 专用snooze族=今日+N-1;
    条件型(snooze_until_retrigger)=远期(靠引擎撤销, 非日期过期)."""
    today = today or date.today()
    if kind in ("snooze_until_retrigger", "mark_sold"):
        return today + timedelta(days=_RETRIGGER_FAR_DAYS)
    if kind in MA_ALERT_KINDS:
        return today + timedelta(days=MA_ALERT_DAYS - 1)   # 含今日共60天, 到期自动作废
    if kind in ("stop_snooze", "ma_watch_snooze", "surge_snooze"):
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
      suppress_all=True  → 全渠道都不推(model_off/ack 命中)
      mute_lark          → 恒 False(「今日免打扰」功能已拆除, 保留键位兼容下游调用方);
                           库里旧 mute/snooze(按票全压)行在此直接不再生效
    """
    for p in prefs:
        kind = p.get("kind")
        target = p.get("target", "") or ""
        if kind == "model_off" and signal_id and target == signal_id:
            return {"suppress_all": True, "mute_lark": False, "reason": f"模型今日关({signal_id})"}
        if kind == "ack" and code and signal_id and target == f"{code}|{signal_id}":
            return {"suppress_all": True, "mute_lark": False, "reason": "已标记处理"}
    return {"suppress_all": False, "mute_lark": False, "reason": ""}


# ── 快捷动作公共执行器 (v1.7.631): HTTP落地页(routers/quick.py)与飞书卡片回调(lark_app)共用 ──

KIND_LABEL = {
    "model_off": "今日关此模型",
    "ack": "标记已处理",
    "stop_snooze": "止损提醒静音",
    "ma_watch_snooze": "破位警戒静音",
    "surge_snooze": "二波提醒静音",
    "snooze_until_retrigger": "个股静音·直到再突破",
    "mark_sold": "标记已卖出",
    "ma_alert_10": "到线提醒·10日线",
    "ma_alert_20": "到线提醒·20日线",
    "ma_alert_60": "到线提醒·60日线",
}


async def execute_quick_action(u: int, k: str, t: str, d: int) -> tuple[bool, str, str]:
    """执行一条快捷设置(落库+联动), 返回 (ok, 动作名, 结果说明)。

    调用方需先做各自的鉴权(HTTP 路由验 HMAC 签名; 卡片回调由飞书长连接身份保证)。
    """
    from backend.models.repo import push_pref as pref_repo

    if k not in VALID_KINDS:
        return False, "无效操作", "未知的设置类型。"

    # 均线到线提醒(一次性订阅): 同票同线重复点击=幂等, 已有生效订阅不再新增(也不刷新60天窗口)
    if k in MA_ALERT_KINDS and t:
        try:
            _actives = await pref_repo.active_prefs(u)
        except Exception:
            _actives = []
        for _p in _actives:
            if _p.get("kind") == k and (_p.get("target") or "") == t:
                return True, "已在监控中", (
                    f"{t} 的{MA_ALERT_KINDS[k]}日均线到线提醒已在监控中"
                    f"（一次性，触发后自动失效），无需重复订阅。")

    until = until_for(k, d)
    await pref_repo.add_pref(u, k, t, until)

    # 标记已卖出: 该票从持仓降级为观察(status hold→watch), 压掉后续卖出/持仓类提醒(买点照常)
    if k == "mark_sold" and t:
        try:
            from backend.models import repository
            await repository.update_stock(t, u, status="watch")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[quick] 标记已卖出翻转持仓状态失败({t}): {e}")

    label = KIND_LABEL.get(k, k)
    if k == "model_off":
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
    elif k in MA_ALERT_KINDS:
        _n = MA_ALERT_KINDS[k]
        detail = (f"已订：{t} 触及{_n}日均线时提醒你（一次性，提醒后自动失效）。"
                  f"贴线带±0.3%；订阅时若已贴线，需先离线再回触才算；"
                  f"{until.strftime('%m-%d')} 前有效，未触发自动作废。")
    elif k == "mark_sold":
        detail = f"已标记 {t} 为已卖出，已从持仓列表移出（转为自选观察），不再推送该票的卖出/减仓/持仓提醒。导入新交割单后自动归位。"
    else:  # ack
        detail = "该信号已标记处理，当日不再重复提醒。"
    return True, f"已设置：{label}", detail


# ── 推送卡片里的快捷动作行 ──

def build_quick_actions_md(site: str, user_id, code: str, signal_id: str, direction: str) -> str:
    """拼飞书卡片底部的快捷动作 markdown 链接行. 只对有个股的买卖信号给「静到再突破」入口
    (指向单选项落地页, kind=snooze_until_retrigger, 只压该票该买点); 大盘预警(无 code)无动作行.

    「今日免打扰」「静音此股(仅今日/本周按票全压)」已拆除(2026-07 用户拍板);
    「今日关此模型」更早移除(2026-06-27, model_off 后端能力保留)。
    用 markdown 链接而非原生按钮: v2 卡(schema2.0)不支持 action 按钮容器, 链接两版卡通用.
    """
    site = (site or "").rstrip("/")
    if not site or not code or not signal_id:
        return ""
    return f"[🔕 静到再突破]({build_signal_snooze_link(site, user_id, code, signal_id)})"


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


# ── 均线到线提醒(一次性订阅, v1.7.x): 个股买卖类信号卡挂 10/20/60日线订阅链接 ──

def ma_alert_eligible(direction: str, code: str) -> bool:
    """到线提醒订阅行门槛: 只挂个股买卖类信号卡(buy/sell/reduce 且有 code); plunge 大盘卡不挂。"""
    return direction in ("buy", "sell", "reduce") and bool(code)


def build_ma_alert_md(site: str, user_id, code: str, name: str = "") -> str:
    """个股买卖信号卡「🔔 到线提醒」一次性订阅行: 10/20/60日线三条签名链接(kind=ma_alert_N, target=code)。

    点击即订阅: 该股现价进入对应均线±0.3%贴线带时推一次提醒后自动失效(一次性);
    60天未触发自动过期。扫描与触发在 services/ma_touch_alert.py。name 仅为调用方语义占位
    (订阅只按 code 存, 提醒卡触发时用实时行情里的最新名称)。"""
    site = (site or "").rstrip("/")
    if not site or not code:
        return ""
    links = " · ".join(
        f"[{n}日线]({build_quick_link(site, user_id, f'ma_alert_{n}', target=code)})"
        for n in sorted(MA_ALERT_KINDS.values()))
    return f"🔔 到线提醒：{links}"


# ── 应用机器人模式: 快捷动作变真回调按钮(点击不跳页, 原地toast; v1.7.631) ──

def build_quick_action_button_rows(user_id, code: str, signal_id: str, direction: str) -> list[dict]:
    """信号卡快捷动作按钮行(schema2.0 回调按钮, 仅应用机器人通道用)。

    单行: [✅ 已卖出](卖/减仓类) + [🔕 静到再突破](有个股, 只压该票该买点)。
    与 build_quick_actions_md(webhook 链接版)动作对齐。
    「今日免打扰」「静音今日/静音本周(按票全压)」已拆除(2026-07 用户拍板)。
    """
    from backend.services import lark_app

    row1 = []
    if direction in ("sell", "reduce") and code:
        row1.append(lark_app.callback_button(
            "✅ 已卖出", lark_app.quick_action_value(user_id, "mark_sold", code, 365), style="primary"))
    if code and signal_id:
        row1.append(lark_app.callback_button(
            "🔕 静到再突破",
            lark_app.quick_action_value(user_id, "snooze_until_retrigger", f"{code}|{signal_id}", 0)))
    return [lark_app.button_row(row1)] if row1 else []


def build_surge_action_button_rows(user_id, code: str) -> list[dict]:
    """二波过前高卡逐票静音按钮行(应用机器人通道): 当日不提醒 / 本周不提醒。"""
    from backend.services import lark_app
    return [lark_app.button_row([
        lark_app.callback_button(
            "🔕 当日不提醒", lark_app.quick_action_value(user_id, "surge_snooze", code, 1)),
        lark_app.callback_button(
            "🔕 本周不提醒", lark_app.quick_action_value(user_id, "surge_snooze", code, days_until_week_end())),
    ])]


# ── 个股信号静音: 落地页(单选项: 直到再次突破; 「仅今日/本周」按票全压两档已拆除 2026-07) ──

def build_signal_snooze_link(site: str, user_id, code: str, signal_id: str,
                             name: str = "", exp=None) -> str:
    """指向静音落地页的签名链接(kind=snooze 占位签名, 携 code|signal_id + 名称)。
    落地页只剩「直到再次突破」一档(条件式单模型静音)。占位 kind 沿用 'snooze' 字面量:
    仅作 HMAC 原文成分, 不落库不进 VALID_KINDS 校验, 且旧卡片已签发链接继续可用。"""
    exp = int(exp) if exp is not None else int(time.time()) + QUICK_LINK_TTL_SECONDS
    target = f"{code}|{signal_id}"
    sig = sign_params(user_id, "snooze", target, 0, exp)
    q = urlencode({"u": user_id, "t": target, "n": name, "exp": exp, "sig": sig})
    return f"{site.rstrip('/')}/api/quick/snooze-options?{q}"


def render_snooze_options_page(site: str, user_id, code: str, name: str, signal_id: str) -> str:
    """静音落地页: 单选项「直到再次突破」(只静音该票该买点, 安静≥1交易日后再触发自动恢复)。"""
    site = site.rstrip("/")
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
  <div style="margin:8px 0 18px;font-size:13px;color:#888;line-height:1.6;">只静音该票的<b>这一个买点</b>，其余信号（卖点/止损/异动等）照常推送。该票安静≥1个交易日后再次触发此买点时，自动恢复提醒：</div>
  <a href="{retrig_link}" style="{btn}background:#fff7ed;color:#9a3412;">直到再次突破<span style="font-weight:400;font-size:12px;color:#888;"> · 安静≥1日后重新触发才再提醒</span></a>
</div></body></html>"""
