"""藏龙岛观点 API (v1.7.x).

  GET  /api/lark-coach/posts                分页取藏龙岛消息(按发布时间倒序)
  GET  /api/lark-coach/media/{message_id}   图片消息取图(本地缓存, 缺则经 lark-cli 拉)
  GET  /api/lark-coach/relay-style          转发形式(card/text, 管理员)
  POST /api/lark-coach/relay-style          切换转发形式(管理员)
"""
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.core.auth import get_current_user, require_admin
from backend.models import repository

router = APIRouter(prefix="/api/lark-coach", tags=["lark-coach"])

# 图片缓存目录(项目根/data/coach_media): 首次请求经 lark-cli 下载, 之后直接读盘
_MEDIA_DIR = Path(__file__).resolve().parents[2] / "data" / "coach_media"


def _sniff_media_type(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    return "application/octet-stream"


@router.get("/posts")
async def list_posts(_user: Annotated[dict, Depends(get_current_user)],
                     limit: int = 100, offset: int = 0):
    limit = max(1, min(limit, 300))
    offset = max(0, offset)
    rows = await repository.list_coach_posts(limit=limit, offset=offset)
    posts = [{
        "id": r["id"],
        "message_id": r["message_id"],
        "coach_name": r["coach_name"],
        "posted_at": r["posted_at"].strftime("%Y-%m-%d %H:%M") if r.get("posted_at") else "",
        "content": r["content"],
        "msg_type": r["msg_type"],
    } for r in rows]
    return {"posts": posts}


@router.get("/relay-style")
async def get_relay_style(_admin: Annotated[dict, Depends(require_admin)]):
    """当前转发形式: card=卡片 / text=文本。取合并默认段后的生效值。"""
    from backend.services.lark_coach_scanner import _load_cfg
    return {"style": str(_load_cfg().get("relay_style", "card")).lower()}


@router.post("/relay-style")
async def set_relay_style(data: dict, admin: Annotated[dict, Depends(require_admin)]):
    """切换转发形式。只改 lark_coach_tracking.relay_style 一个键(复制现段再改,
    不整段覆盖, 防误清 relay_webhook 等生产专属配置)。"""
    style = str(data.get("style", "")).lower()
    if style not in ("card", "text"):
        raise HTTPException(status_code=400, detail="style 只能是 card / text")
    from backend.core.config import load_config, save_config
    cfg = load_config()
    section = dict(cfg.get("lark_coach_tracking") or {})
    old = str(section.get("relay_style", "card")).lower()
    section["relay_style"] = style
    cfg["lark_coach_tracking"] = section
    save_config(cfg)
    if old != style:
        await repository.add_log(admin["id"], admin["username"], "update_config", "lark_coach_relay_style",
                                 old_value={"relay_style": old}, new_value={"relay_style": style})
    return {"ok": True, "style": style}


@router.get("/media/{message_id}")
async def get_media(message_id: str, _user: Annotated[dict, Depends(get_current_user)]):
    """图片消息取图: 已缓存直接回, 没缓存经 lark-cli 下载后回。"""
    from backend.fetcher.lark_coach import extract_image_key, download_message_image, LarkCoachFetchError
    from backend.services.lark_coach_scanner import _load_cfg

    # message_id 进文件名, 先掐死路径穿越
    if not message_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="非法 message_id")

    row = await repository.get_coach_post_by_message_id(message_id)
    if not row or row.get("msg_type") != "image":
        raise HTTPException(status_code=404, detail="不是图片消息")
    key = extract_image_key(row.get("content", ""))
    if not key:
        raise HTTPException(status_code=404, detail="没有图片资源")

    path = _MEDIA_DIR / f"{message_id}.img"
    if not path.exists():
        try:
            await download_message_image(_load_cfg(), message_id, key,
                                         str(_MEDIA_DIR), f"{message_id}.img")
        except LarkCoachFetchError as e:
            raise HTTPException(status_code=502, detail=f"图片下载失败: {e}") from e
    if not path.exists():
        # lark-cli 可能按 Content-Disposition 起了别的名, 兜底找同前缀文件
        cand = [p for p in _MEDIA_DIR.glob(f"{message_id}.*")] if _MEDIA_DIR.exists() else []
        if not cand:
            raise HTTPException(status_code=502, detail="图片下载后未找到文件")
        path = cand[0]
    return FileResponse(str(path), media_type=_sniff_media_type(str(path)))
