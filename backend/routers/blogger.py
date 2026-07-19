"""博主 cookie 续签 (v1.7.x): 油猴脚本读 httpOnly cookie → POST 这里 → 校验后写 config.

油猴脚本在登录态 t.10jqka.com.cn 用 GM_cookie 读全部 cookie(含 httpOnly)+ hexin-v(=v cookie),
定时/手动 POST 到本接口。鉴权用 renew_token(脚本与服务器共享密钥)——脚本在同花顺域无法
携带本系统 JWT, 故不走 get_current_user。校验逻辑: 先用新凭证试拉一次帖子, 通过才落
config.json(写坏拒绝)。load_config 无缓存, 落库即生效, 下次博主扫描自动用新凭证。
"""
import hmac
import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from backend.core import config as cfgmod
from backend.fetcher.ths_blogger import fetch_posts_with_creds, BloggerFetchError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/blogger", tags=["blogger"])


class RenewRequest(BaseModel):
    token: str
    cookie: str
    hexin_v: str
    user_code: str = "<blogger_user_code>"


def _read_raw_config() -> dict:
    """读原始 config.json(不经 DEFAULT 合并), 避免续签时把默认占位值写回覆盖生产配置。"""
    path = os.path.normpath(cfgmod.CONFIG_PATH)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


@router.post("/renew")
async def renew_cookie(req: RenewRequest):
    """续签博主 cookie/hexin-v。token 不匹配或新凭证校验不过则拒绝, 绝不写坏配置。"""
    raw = _read_raw_config()
    bt = raw.get("blogger_tracking", {}) or {}
    expected = bt.get("renew_token", "")
    if not expected or not hmac.compare_digest(str(req.token), str(expected)):
        logger.warning("[blogger_renew] token 不匹配, 拒绝续签")
        return {"ok": False, "error": "token 无效"}
    if not req.cookie or not req.hexin_v:
        return {"ok": False, "error": "cookie/hexin_v 为空"}

    # 先用新凭证试拉一次, 通过才落库(写坏拒绝)
    try:
        posts = await fetch_posts_with_creds(
            req.cookie, req.hexin_v, req.user_code, bt.get("blogger_name", "全能的野人"))
    except BloggerFetchError as e:
        logger.warning(f"[blogger_renew] 新凭证校验失败, 不落库: {e}")
        return {"ok": False, "error": f"凭证无效或已过期: {e}"}
    except Exception as e:
        logger.warning(f"[blogger_renew] 校验异常: {e}")
        return {"ok": False, "error": f"校验异常: {e}"}

    # 落库
    bt["cookie"] = req.cookie
    bt["hexin_v"] = req.hexin_v
    bt["user_code"] = req.user_code
    bt["enabled"] = True
    bt["renewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    raw["blogger_tracking"] = bt
    cfgmod.save_config(raw)

    latest = None
    if posts and posts[0].get("posted_at"):
        latest = posts[0]["posted_at"].strftime("%Y-%m-%d %H:%M")
    logger.info(f"[blogger_renew] 续签成功, 拉到 {len(posts)} 帖, 最新 {latest}")
    return {"ok": True, "posts_count": len(posts),
            "latest_post_time": latest, "renewed_at": bt["renewed_at"]}
