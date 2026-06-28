# -*- coding: utf-8 -*-
"""同花顺博主帖子采集 — 全能的野人 (v1.7.x).

API: POST user_center/open/api/content/v2/get_by_uid
请求体: {"user_code": "<blogger_user_code>"}
返回: contents[] 含 info/abstract/stat, end_time 分页游标.

认证: hexin-v 签名 + Cookie(sess_tk/utk/ticket), 需定期从浏览器重新抓取.
"""

import logging
import re
from datetime import datetime

from backend.core.config import load_config
from backend.fetcher.http_client import THS_HEADERS, _get_client

logger = logging.getLogger(__name__)

THS_POST_API = "https://t.10jqka.com.cn/user_center/open/api/content/v2/get_by_uid"

_STOCK_TAG_RE = re.compile(r"\$[^$(]+\((\d{6})\)\$")


def extract_stock_codes(content: str) -> list[str]:
    """$华能蒙电(600863)$ → 600863, 去重保序."""
    if not content:
        return []
    seen: dict[str, None] = {}
    for code in _STOCK_TAG_RE.findall(content):
        seen.setdefault(code, None)
    return list(seen.keys())


# 同花顺正文里的内嵌标记: <hx_stock>stockName:弘信电子,stockCode:300657,market:33</hx_stock>
_HX_STOCK_RE = re.compile(
    r"<hx_stock>\s*stockName:\s*([^,，]+?)\s*,\s*stockCode:\s*(\d{4,6})\s*,\s*market:[^<]*</hx_stock>")
# 表情图标: <img class="emojiface" title="[吐血]" src="..." />
_EMOJI_IMG_RE = re.compile(r'<img[^>]*\btitle="(\[[^\]]*\])"[^>]*?>')
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_content(raw: str) -> str:
    """帖子正文清洗: <hx_stock>股票标记→$名(代码)$(供 extract_stock_codes 识别),
    emojiface 图标→[表情]文字, 去残留 HTML 标签, 折叠多余空白。"""
    if not raw:
        return ""
    s = _HX_STOCK_RE.sub(lambda m: f"${m.group(1).strip()}({m.group(2)})$", raw)
    s = _EMOJI_IMG_RE.sub(lambda m: m.group(1), s)
    s = _HTML_TAG_RE.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _norm_img(u: str) -> str:
    """协议相对地址 //i.thsi.cn/... 补 https:。"""
    if u and u.startswith("//"):
        return "https:" + u
    return u


def _parse_ctime(ms: int) -> datetime | None:
    """毫秒时间戳 → datetime."""
    try:
        return datetime.fromtimestamp(ms / 1000)
    except (ValueError, OSError):
        return None


class BloggerFetchError(Exception):
    """拉取博主帖子失败(多为 cookie/hexin-v 过期或接口异常)。

    供上层 scan_blogger_posts 据此做"连续失败→飞书告警提醒重抓 cookie"的兜底,
    区别于"拉取成功但暂无新帖"(返回 [])。"""


async def fetch_blogger_posts() -> list[dict]:
    """拉取全能的野人最新帖子, 返回归一化列表.

    成功返回 [{post_id, posted_at, content, stock_codes, url, like_num, comment_num}, ...]
    (功能未启用时返回 []); 配置缺失/网络/HTTP/签名失效等"失败"抛 BloggerFetchError。
    """
    cfg = load_config().get("blogger_tracking", {})
    if not cfg.get("enabled"):
        return []

    user_code = cfg.get("user_code", "<blogger_user_code>")
    blogger_name = cfg.get("blogger_name", "全能的野人")
    cookie = cfg.get("cookie", "")
    hexin_v = cfg.get("hexin_v", "")

    if not cookie or not hexin_v:
        raise BloggerFetchError("未配置 cookie/hexin_v")
    return await fetch_posts_with_creds(cookie, hexin_v, user_code, blogger_name)


async def fetch_posts_with_creds(cookie: str, hexin_v: str,
                                 user_code: str = "<blogger_user_code>",
                                 blogger_name: str = "全能的野人") -> list[dict]:
    """用显式凭证拉一次帖子(供 /api/blogger/renew 续签时先校验后落库, 与定时拉取共用解析)。
    凭证无效(HTTP非200/status_code非0/签名过期)抛 BloggerFetchError。"""
    headers = {
        **THS_HEADERS,
        "Cookie": cookie,
        "hexin-v": hexin_v,
        "Content-Type": "application/json",
        "Referer": f"https://t.10jqka.com.cn/lgt/user_page/?user_code={user_code}",
    }

    client = _get_client()
    try:
        resp = await client.post(
            THS_POST_API,
            json={"user_code": user_code},
            headers=headers,
            timeout=15,
        )
    except Exception as e:
        raise BloggerFetchError(f"请求异常: {e}")
    if resp.status_code != 200:
        raise BloggerFetchError(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except Exception as e:
        raise BloggerFetchError(f"响应非JSON: {e}")

    if data.get("status_code") != 0:
        raise BloggerFetchError(
            f"status_code={data.get('status_code')} msg={data.get('status_msg')} (Cookie/签名可能过期)")

    contents = data.get("data", {}).get("contents", [])
    if not isinstance(contents, list):
        return []

    posts = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        info = item.get("info", {})
        post_id = str(info.get("id", ""))
        ctime = info.get("ctime")
        url = info.get("jump_url", "") or info.get("client_url", "")

        # 内容: abstract 是预览, 但通常已包含全文关键信息; 清洗内嵌股票标记/表情/HTML
        abstract = item.get("abstract", {})
        raw_content = abstract.get("content", "") if isinstance(abstract, dict) else ""
        content = _clean_content(raw_content)

        # 配图: image.urls 为图片地址数组(纯文本帖为空)
        img_field = item.get("image", {})
        raw_imgs = img_field.get("urls", []) if isinstance(img_field, dict) else []
        images = [_norm_img(u) for u in raw_imgs if u]

        stat = item.get("stat", {})
        like = stat.get("like_num", 0) if isinstance(stat, dict) else 0
        comment = stat.get("comment_num", 0) if isinstance(stat, dict) else 0

        if not post_id:
            continue

        posts.append({
            "blogger_name": blogger_name,
            "post_id": post_id,
            "posted_at": _parse_ctime(ctime) if ctime else None,
            "content": content,
            "stock_codes": extract_stock_codes(content),
            "images": images,
            "url": url or f"https://t.10jqka.com.cn/m/post/discussDetail/?contentId={post_id}",
            "like_num": like,
            "comment_num": comment,
        })

    return posts
