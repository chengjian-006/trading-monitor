"""同花顺投资圈博主发帖采集 - v1.7.x.

  fetch_blogger_posts   — 拉取某博主最近动态, 返回归一化 post 列表
  extract_stock_codes   — 从帖子正文解析 $股票名(代码)$ 标签里的 6 位代码

⚠️ 接口阻塞项: 动态列表接口的精确 URL/参数/签名(hexin-v)在同花顺前端运行时拼接, 静态扒不出,
必须先从浏览器抓一次真实请求(DevTools→Network→Copy as cURL)。抓到后把
  config.json["blogger_tracking"]["request"]  里的 url / headers / params 填实,
并按真实返回 JSON 校正 _normalize_item 的字段映射(field_map)。

config.json 结构(示例):
  "blogger_tracking": {
    "enabled": true,
    "bloggers": [{"fid": "<博主fid>", "name": "全能的野人"}],
    "request": {
      "url": "https://<host>/<path>",          # 抓到的真实接口
      "method": "GET",
      "params": {"fid": "{fid}", "page": "1"},  # {fid} 会被替换
      "headers": {"Cookie": "<登录态>", "hexin-v": "<签名>", "User-Agent": "..."}
    },
    "field_map": {
      "list":     "data.list",   # 帖子数组所在路径
      "post_id":  "seq",         # 帖子唯一 id 字段
      "time":     "ctime",       # 发帖时间字段(秒级时间戳或字符串)
      "content":  "text",        # 正文字段
      "url":      "url"          # 帖子详情链接字段(可空)
    }
  }
"""
import logging
import re
from datetime import datetime

from backend.core.config import load_config
from backend.fetcher.http_client import THS_HEADERS, _get_client

logger = logging.getLogger(__name__)

# $华能蒙电(600863)$ / $华能国际(600011)$ → 600863, 600011
_STOCK_TAG_RE = re.compile(r"\$[^$(]+\((\d{6})\)\$")


def extract_stock_codes(content: str) -> list[str]:
    """从帖子正文解析个股标签里的 6 位代码, 去重保序。"""
    if not content:
        return []
    seen: dict[str, None] = {}
    for code in _STOCK_TAG_RE.findall(content):
        seen.setdefault(code, None)
    return list(seen.keys())


def _dig(obj, path: str):
    """按 'a.b.c' 点路径取嵌套值, 取不到返回 None。"""
    cur = obj
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def _parse_time(val) -> datetime | None:
    if val is None or val == "":
        return None
    # 秒级 / 毫秒级时间戳
    if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
        ts = int(val)
        if ts > 1e12:  # 毫秒
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            return None
    # 字符串日期
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(str(val), fmt)
        except ValueError:
            continue
    return None


def _normalize_item(item: dict, field_map: dict, blogger: dict) -> dict | None:
    """把单条原始 JSON 帖子归一化为统一结构。字段名取自 field_map。"""
    post_id = _dig(item, field_map.get("post_id", "seq"))
    content = _dig(item, field_map.get("content", "text")) or ""
    if post_id is None:
        return None
    return {
        "blogger_fid": str(blogger.get("fid", "")),
        "blogger_name": blogger.get("name", ""),
        "post_id": str(post_id),
        "posted_at": _parse_time(_dig(item, field_map.get("time", "ctime"))),
        "content": str(content),
        "stock_codes": extract_stock_codes(str(content)),
        "url": _dig(item, field_map.get("url", "url")) or "",
        "raw": item,
    }


async def fetch_blogger_posts(blogger: dict) -> list[dict]:
    """拉取某博主最近动态, 返回归一化 post 列表(失败返回空列表)。

    blogger: {"fid": "...", "name": "..."}
    返回: [{blogger_fid, blogger_name, post_id, posted_at, content, stock_codes, url, raw}, ...]
    """
    cfg = load_config().get("blogger_tracking", {})
    req = cfg.get("request", {})
    field_map = cfg.get("field_map", {})
    url = req.get("url", "")
    if not url:
        logger.warning("[blogger_posts] 未配置 blogger_tracking.request.url, 跳过(待抓真实接口后填入)")
        return []

    fid = str(blogger.get("fid", ""))
    # {fid} 占位替换
    url = url.replace("{fid}", fid)
    params = {k: str(v).replace("{fid}", fid) for k, v in (req.get("params") or {}).items()}
    headers = {**THS_HEADERS, **(req.get("headers") or {})}
    method = (req.get("method") or "GET").upper()

    client = _get_client()
    try:
        if method == "POST":
            resp = await client.post(url, params=params, headers=headers,
                                     json=req.get("body") or None)
        else:
            resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            logger.warning(f"[blogger_posts] {blogger.get('name')} HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        logger.warning(f"[blogger_posts] {blogger.get('name')} 拉取失败: {e}")
        return []

    # 同花顺接口惯例: status_code != 0 多为 cookie 失效 / 参数错 / 签名过期
    if isinstance(data, dict) and data.get("status_code") not in (None, 0):
        logger.warning(
            f"[blogger_posts] {blogger.get('name')} 接口返回异常 "
            f"status_code={data.get('status_code')} msg={data.get('status_msg')} "
            f"(很可能 Cookie/签名已失效, 需重新抓取)"
        )
        return []

    raw_list = _dig(data, field_map.get("list", "data.list"))
    if not isinstance(raw_list, list):
        logger.warning(f"[blogger_posts] {blogger.get('name')} 未解析到帖子数组(检查 field_map.list)")
        return []

    posts = []
    for item in raw_list:
        if isinstance(item, dict):
            norm = _normalize_item(item, field_map, blogger)
            if norm:
                posts.append(norm)
    return posts
