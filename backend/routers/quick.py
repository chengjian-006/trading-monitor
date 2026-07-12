"""推送快捷设置 (v1.7.464+): 飞书卡片里的快捷链接点击入口 + 前端管理.

/api/quick/set   公开(无需登录, 从飞书点进来), 靠 HMAC 签名防乱戳, 回一个极简确认页
/api/quick/prefs 需登录, 给前端管理页列当前生效设置 / 撤销
"""
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from backend.core.auth import get_current_user
from backend.core.config import load_config
from backend.models.repo import push_pref as pref_repo
from backend.services import push_pref as pref_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quick", tags=["quick"])


async def _send_mute_recovery(user_id: int) -> None:
    """开启今日免打扰后, 往微信/PushPlus(未被静音的渠道)发一条带「恢复」链接的确认, 随时可点。"""
    try:
        from backend.services import notifier
        site = (load_config().get("site_url", "") or "").rstrip("/")
        if not site:
            return
        link = pref_svc.build_quick_link(site, user_id, "unmute")
        content = ("已开启**今日免打扰**(仅静音飞书, 微信照常), 明日 0 点自动恢复。\n\n"
                   f"想提前恢复 👉 [点此恢复今日推送]({link})")
        await notifier._fanout_pushplus("🔕 已开启今日免打扰", content)
    except Exception as e:
        logger.warning(f"[quick] 免打扰恢复确认发送失败: {e}")

# kind → 中文动作名(确认页与管理页共用)
_KIND_LABEL = {
    "mute": "今日免打扰(仅飞书)",
    "snooze": "个股静音",
    "model_off": "今日关此模型",
    "ack": "标记已处理",
    "stop_snooze": "止损提醒静音",
    "ma_watch_snooze": "破位警戒静音",
    "surge_snooze": "二波提醒静音",
    "snooze_until_retrigger": "个股静音·直到再突破",
}


def _confirm_page(title: str, detail: str, ok: bool = True) -> HTMLResponse:
    color = "#18a058" if ok else "#d03050"
    icon = "✅" if ok else "⚠️"
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>快捷设置</title></head>
<body style="margin:0;font-family:-apple-system,'PingFang SC',sans-serif;background:#f5f6f8;">
<div style="max-width:420px;margin:18vh auto;padding:28px 24px;background:#fff;border-radius:14px;
            box-shadow:0 2px 16px rgba(0,0,0,.06);text-align:center;">
  <div style="font-size:44px;line-height:1;">{icon}</div>
  <div style="margin-top:14px;font-size:19px;font-weight:600;color:{color};">{title}</div>
  <div style="margin-top:10px;font-size:14px;color:#666;line-height:1.6;">{detail}</div>
  <div style="margin-top:22px;font-size:12px;color:#aaa;">可在「设置 · 推送偏好」里查看或撤销</div>
</div></body></html>"""
    return HTMLResponse(html)


@router.get("/set")
async def quick_set(
    u: int = Query(...),
    k: str = Query(...),
    t: str = Query(""),
    d: int = Query(0),
    exp: int | None = Query(None),
    sig: str = Query(""),
):
    """快捷设置入口: 校验签名(含 exp 时效) → 落库 → 回确认页. 公开端点, 仅认签名不认登录态.

    exp=签发时的过期时间戳(unix秒), 纳入 HMAC 原文防篡改续命; 无 exp 的旧链接一律拒绝.
    """
    if k not in pref_svc.VALID_KINDS:
        return _confirm_page("无效操作", "未知的设置类型。", ok=False)
    if not pref_svc.verify_params(u, k, t, d, exp, sig):
        return _confirm_page("链接已失效", "签名校验未通过(可能链接被改动或版本过旧)。", ok=False)
    if exp < time.time():
        return _confirm_page("链接已过期", "链接已过期，请从最新推送卡片操作。", ok=False)

    # 恢复今日免打扰: 撤销当日 mute, 不新增偏好
    if k == "unmute":
        n = await pref_repo.revoke_kind(u, "mute")
        if n:
            return _confirm_page("已恢复今日推送", "今日免打扰已取消，飞书推送恢复正常。")
        return _confirm_page("无需恢复", "当前没有生效的今日免打扰。")

    until = pref_svc.until_for(k, d)
    await pref_repo.add_pref(u, k, t, until)

    # 今日免打扰: 往未被静音的渠道发一条可随时点的恢复入口
    if k == "mute":
        await _send_mute_recovery(u)

    label = _KIND_LABEL.get(k, k)
    if k == "mute":
        detail = "今天剩余的飞书推送已静音，明日自动恢复。"
    elif k == "snooze":
        detail = f"已对 {t} 静音至 {until.strftime('%m-%d')}，期间不再推送其信号。"
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
    else:  # ack
        detail = "该信号已标记处理，当日不再重复提醒。"
    return _confirm_page(f"已设置：{label}", detail)


@router.get("/snooze-options")
async def snooze_options(
    u: int = Query(...),
    t: str = Query(...),          # code|signal_id
    n: str = Query(""),           # 股票名(展示用)
    exp: int | None = Query(None),
    sig: str = Query(""),
):
    """个股信号静音落地页: 校验签名(kind=snooze 占位) → 渲染三档(仅今日/本周/直到再突破)按钮页。"""
    if not pref_svc.verify_params(u, "snooze", t, 0, exp, sig):
        return _confirm_page("链接已失效", "签名校验未通过。", ok=False)
    if exp is None or exp < time.time():
        return _confirm_page("链接已过期", "链接已过期，请从最新推送卡片操作。", ok=False)
    code, _, signal_id = t.partition("|")
    site = (load_config().get("site_url", "") or "").rstrip("/")
    return HTMLResponse(pref_svc.render_snooze_options_page(site, u, code, n, signal_id))


@router.get("/prefs")
async def list_prefs(user: Annotated[dict, Depends(get_current_user)]):
    """前端管理页: 列出当前生效的推送偏好(未撤销+未过期)。"""
    rows = await pref_repo.active_prefs(user["id"])
    out = []
    for r in rows:
        until = r["until_date"]
        # 条件型静音无固定到期日(远期占位), 展示"直到再次突破"而非误导性的 2036 日期
        until_label = ("直到再次突破" if r["kind"] == "snooze_until_retrigger"
                       else (until.isoformat() if hasattr(until, "isoformat") else str(until)))
        out.append({
            "id": r["id"],
            "kind": r["kind"],
            "kind_label": _KIND_LABEL.get(r["kind"], r["kind"]),
            "target": r["target"],
            "until_date": until.isoformat() if hasattr(until, "isoformat") else str(until),
            "until_label": until_label,
        })
    return {"prefs": out}


@router.post("/prefs/{pref_id}/revoke")
async def revoke_pref(pref_id: int, user: Annotated[dict, Depends(get_current_user)]):
    await pref_repo.revoke(user["id"], pref_id)
    return {"ok": True}
