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


# kind → 中文动作名(管理页用; 执行逻辑已抽到 pref_svc.execute_quick_action, v1.7.631)
_KIND_LABEL = pref_svc.KIND_LABEL


def _confirm_page(title: str, detail: str, ok: bool = True) -> HTMLResponse:
    """确认页(v1.7.632 自动关闭版): 设置已在服务端完成, 本页只是回执 ——
    展示大✅后 0.6s 自动尝试关窗回到飞书; 关不掉的环境(部分浏览器限制)显示大字提示手动关。"""
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
  <div id="close-hint" style="margin-top:10px;font-size:14px;color:#666;line-height:1.6;">已生效，正在返回飞书…</div>
  <div id="detail" style="display:none;margin-top:10px;font-size:14px;color:#666;line-height:1.6;">{detail}</div>
  <div id="foot" style="display:none;margin-top:22px;font-size:12px;color:#aaa;">可在「设置 · 推送偏好」里查看或撤销</div>
</div>
<script>
// 设置在服务端已完成, 本页只是回执 → 自动关窗回飞书。
// window.close 对"脚本/链接打开的新窗口"多数浏览器放行; 被拦截(标签复用等)则退回完整详情页。
setTimeout(function () {{
  try {{ window.open('', '_self'); window.close(); }} catch (e) {{}}
  setTimeout(function () {{
    var h = document.getElementById('close-hint');
    if (h) h.innerHTML = '<span style="font-size:17px;font-weight:600;">已完成，可关闭此页</span>';
    var d = document.getElementById('detail'); if (d) d.style.display = 'block';
    var f = document.getElementById('foot'); if (f) f.style.display = 'block';
  }}, 400);
}}, 600);
</script>
</body></html>"""
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

    ok, label, detail = await pref_svc.execute_quick_action(u, k, t, d)
    return _confirm_page(label, detail, ok=ok)


@router.get("/snooze-options")
async def snooze_options(
    u: int = Query(...),
    t: str = Query(...),          # code|signal_id
    n: str = Query(""),           # 股票名(展示用)
    exp: int | None = Query(None),
    sig: str = Query(""),
):
    """个股信号静音落地页: 校验签名(kind=snooze 占位, 仅签名成分不落库) → 渲染单选项
    (直到再突破, 条件式单模型静音)按钮页。「仅今日/本周」按票全压两档已拆除(2026-07)。"""
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
        # 条件型/永久型无固定到期日(远期占位), 展示语义文案而非误导性的 2036 日期
        if r["kind"] == "snooze_until_retrigger":
            until_label = "直到再次突破"
        elif r["kind"] == "mark_sold":
            until_label = "手动恢复或导入新买入"
        else:
            until_label = until.isoformat() if hasattr(until, "isoformat") else str(until)
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
