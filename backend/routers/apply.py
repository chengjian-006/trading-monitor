"""官网内测申请 API (v1.7.613)。

  POST /api/apply        提交内测申请 —— 公开端点, 免鉴权(官网表单直接调)
  GET  /api/apply/list   查看申请列表 —— 仅管理员

公开端点的三道防刷:
  1. 蜜罐字段 website: 真人看不见也 tab 不到, 脚本会填满 → 填了就静默丢弃(照样返回 200,
     让脚本以为成功, 不给它反馈去调整)。
  2. 内存最小间隔: 同 IP 20 秒内只收一次(单 worker, 进程内存够用)。
  3. DB 日上限: 同 IP 24 小时内超过 5 条 → 429。重启不清零, 兜住内存限流的漏。
"""
import logging
import time

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.core.auth import require_admin
from backend.core.config import load_config
from backend.models.repo import beta_apply as apply_repo
from backend.services import lark_notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apply", tags=["apply"])

MIN_INTERVAL_SECONDS = 20      # 同 IP 两次提交的最小间隔
MAX_PER_IP_PER_DAY = 5         # 同 IP 24 小时内提交上限
_last_submit: dict[str, float] = {}   # ip → 上次提交的 monotonic 时间
_CLEANUP_INTERVAL = 300
_last_cleanup = 0.0


def _client_ip(request: Request) -> str:
    """真实客户端 IP(申请按IP限流用)。优先 X-Real-IP(nginx设, 不可伪造), 退而取 XFF 最后一段;
    绝不取 XFF 首段(客户端可控, 换首段即可绕过按IP限流)。"""
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _cleanup(now: float) -> None:
    """惰性清理过期条目, 防字典无限膨胀。"""
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    cutoff = now - MIN_INTERVAL_SECONDS
    for ip in [k for k, t in _last_submit.items() if t <= cutoff]:
        _last_submit.pop(ip, None)


class ApplyRequest(BaseModel):
    contact: str = Field(min_length=2, max_length=60)   # 微信号
    remark: str = Field(default="", max_length=400)     # 「你现在怎么盯盘」, 选填
    website: str = Field(default="", max_length=200)    # 蜜罐, 真人恒为空


async def _notify(contact: str, remark: str, ip: str) -> None:
    """飞书通知我有新申请。推送挂了不能连累申请提交, 所以整块吞异常。

    走 lark_notifier 而非 notifier.send_dual: 后者有生产 IP 闸门(非 prod 出口静默不发),
    而内测申请不是行情消息, 本地调试时也该能收到。
    """
    try:
        cfg = load_config()
        webhook = cfg.get("lark_webhook", "")
        if not webhook:
            return
        body = f"**微信**: {contact}\n**怎么盯盘**: {remark or '（未填）'}\n**来源 IP**: {ip}"
        await lark_notifier.post_lark_card(webhook, "🎉 官网内测申请", body, template="green")
    except Exception as e:
        logger.warning(f"[apply] 飞书通知失败(不影响申请落库): {e}")


@router.post("")
async def submit_apply(req: ApplyRequest, request: Request):
    """提交内测申请。公开端点, 无鉴权。"""
    # 1. 蜜罐命中 → 假装成功, 不落库不通知
    if req.website.strip():
        logger.info("[apply] 蜜罐命中, 静默丢弃")
        return {"ok": True}

    ip = _client_ip(request)
    now = time.monotonic()
    _cleanup(now)

    # 2. 内存最小间隔
    if now - _last_submit.get(ip, 0.0) < MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提交太频繁了，过会儿再试。",
            headers={"Retry-After": str(MIN_INTERVAL_SECONDS)},
        )

    # 3. DB 日上限
    if await apply_repo.count_by_ip_recent(ip, hours=24) >= MAX_PER_IP_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="今天提交次数太多了，明天再试，或直接联系我们。",
        )

    contact = req.contact.strip()
    remark = req.remark.strip()
    ua = (request.headers.get("user-agent") or "")[:255]

    apply_id = await apply_repo.add_apply(contact, remark, ip, ua)
    _last_submit[ip] = now
    logger.info(f"[apply] 新内测申请 #{apply_id} contact={contact} ip={ip}")

    await _notify(contact, remark, ip)
    return {"ok": True, "id": apply_id}


@router.get("/list")
async def list_applies(_: Annotated[dict, Depends(require_admin)], limit: int = 200):
    """查看内测申请列表(管理员)。"""
    return await apply_repo.list_applies(min(max(limit, 1), 500))
