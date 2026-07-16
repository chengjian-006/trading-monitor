# -*- coding: utf-8 -*-
"""博主发帖扫描 — 同花顺全能的野人.

频率(自节流):
  交易日 9:00-10:00 → 5分钟
  交易日 10:00-15:00 → 20分钟
  盘后 15:00-次日9:00 → 60分钟
  非交易日 → 20:00 一次

入口 scan_blogger_posts() 注册为 interval/300s(5min, 见 database.py 种子), 内部按上表节流控制实际执行间隔.
去重靠 cfzy_biz_blogger_posts 唯一索引.
"""

import logging
import time
from datetime import datetime

from backend.core.config import load_config
from backend.core.trading_calendar import is_trading_time as _is_trading_time, is_workday as _is_workday
from backend.fetcher.ths_blogger import fetch_blogger_posts, BloggerFetchError
from backend.models import repository

logger = logging.getLogger(__name__)

_last_scan: float = 0.0

# cookie/签名失效兜底: 连续失败达阈值即飞书告警提醒重抓, 同类告警冷却防刷屏; 恢复后发一条恢复通知
_fetch_fail_count: int = 0
_fail_alerted: bool = False
_last_fail_alert_at: float = 0.0
FAIL_THRESHOLD = 3            # 连续3次拉取失败才告警(避开偶发网络抖动)
FAIL_ALERT_COOLDOWN = 12 * 3600  # 已告警后12小时内不重复(重抓需人工, 别每轮唠叨)

# IP 限流退避: 同花顺对 get_by_uid 有 IP 反爬(连发多次→"Nginx forbidden"封出口IP一段时间)。
# 拉取失败时按指数拉长下次尝试间隔(期间完全不发请求, 让封禁自然解除), 避免高频重试火上浇油、
# 延长封禁。成功即清零。退避叠加在常规节流(5/20/60min)之上, 取二者更晚者。
_backoff_until: float = 0.0
BACKOFF_BASE = 300           # 首次失败退避 5min
BACKOFF_CAP = 7200           # 封顶 2h (连续失败时: 5→10→20→40→80→120min 封顶)


def _get_interval_seconds() -> int:
    """根据当前时段返回应间隔秒数."""
    now = datetime.now()
    t = now.strftime("%H:%M")
    if not _is_workday():
        # 非交易日: 只在20:00前后跑一次
        if "19:55" <= t <= "20:10":
            return 0
        return 99999  # 其他时间不跑
    if "09:00" <= t < "10:00":
        return 300   # 5分钟
    if "10:00" <= t < "15:00":
        return 1200  # 20分钟
    return 3600      # 盘后60分钟


def _format_post(post: dict) -> str:
    """单条新帖推送文案(纯文本回退)."""
    name = post.get("blogger_name") or "博主"
    when = post.get("posted_at")
    when_str = when.strftime("%m-%d %H:%M") if when else ""
    codes = post.get("stock_codes") or []
    codes_line = ("\n涉及个股: " + " ".join(codes)) if codes else ""
    content = (post.get("content") or "").strip()
    images = post.get("images") or []
    img_line = (f"\n📷 配图{len(images)}张: " + " ".join(images)) if images else ""
    url = post.get("url") or ""
    url_line = f"\n原帖: {url}" if url else ""
    like = post.get("like_num", 0)
    comment = post.get("comment_num", 0)
    stats = f" 👍{like} 💬{comment}" if like or comment else ""
    return f"📣 {name} 发新帖 {when_str}{stats}\n\n{content}{codes_line}{img_line}{url_line}"


def _post_gist(content: str, limit: int = 40) -> str:
    """帖子要点一句: 取第一行非空文本, 截断到 limit 字。"""
    for line in (content or "").strip().splitlines():
        line = line.strip()
        if line:
            return line[:limit] + ("…" if len(line) > limit else "")
    return "（无文字内容）"


def build_post_card(post: dict):
    """博主发帖 → 基线 v1.1 结构卡(情报族 blue):
    结论行=博主名+要点一句 → 互动/个股数据行 → 👉建议 → 帖子全文进折叠。"""
    from backend.services import card_kit
    from backend.services.lark_notifier import md_element

    name = post.get("blogger_name") or "博主"
    content = (post.get("content") or "").strip()
    gist = _post_gist(content)
    codes = post.get("stock_codes") or []
    images = post.get("images") or []
    like = post.get("like_num", 0)
    comment = post.get("comment_num", 0)
    when = post.get("posted_at")
    when_str = when.strftime("%m-%d %H:%M") if when else ""

    elements: list = [md_element(f"**{name}**：{gist}")]
    meta_bits = []
    if like or comment:
        meta_bits.append(f"👍{like} 💬{comment}")
    if codes:
        meta_bits.append("涉及个股 " + " ".join(f"**{c}**" for c in codes))
    if meta_bits:
        elements.append(md_element(" · ".join(meta_bits)))
    elements.append(card_kit.advice("观点仅供参考，别直接跟单"))
    detail = content or "（无文字内容）"
    if images:
        detail += f"\n\n📷 配图{len(images)}张:\n" + "\n".join(images)
    elements.append(card_kit.fold("帖子全文", detail))

    return card_kit.Card(
        title=f"📣 博主发帖 · {name}", elements=elements, fallback=_format_post(post),
        family="intel",
        summary=card_kit.summary_text(name, "发新帖", gist),
        subtitle=f"发帖 {when_str}" if when_str else "",
        link_url=post.get("url") or "", link_text="看原帖")


async def scan_blogger_posts():
    cfg = load_config().get("blogger_tracking", {})
    if not cfg.get("enabled", False):
        return

    global _last_scan, _fetch_fail_count, _fail_alerted, _last_fail_alert_at, _backoff_until
    interval = _get_interval_seconds()
    if interval > 3600:
        return  # 非交易日非20:00, 跳过
    now = time.monotonic()
    if now - _last_scan < interval:
        return
    if now < _backoff_until:
        return  # IP 限流退避中: 跳过本轮、不发请求, 等封禁解除
    _last_scan = now

    from backend.services import notifier

    try:
        posts = await fetch_blogger_posts()
    except BloggerFetchError as e:
        _fetch_fail_count += 1
        backoff = min(BACKOFF_BASE * (2 ** (_fetch_fail_count - 1)), BACKOFF_CAP)
        _backoff_until = time.monotonic() + backoff
        logger.warning(f"[blogger_posts] 拉取失败(连续 {_fetch_fail_count} 次): {e} → 退避 {backoff // 60}min 不发请求")
        if _fetch_fail_count >= FAIL_THRESHOLD and (time.time() - _last_fail_alert_at > FAIL_ALERT_COOLDOWN):
            _last_fail_alert_at = time.time()
            _fail_alerted = True
            name = cfg.get("blogger_name", "博主")
            # v1.7.557 批次E: 不再实时独推, 登记进「系统健康·盘后汇总」当日合并
            from backend.services.system_health import report_issue
            report_issue("博主发帖", f"「{name}」连续{_fetch_fail_count}次拉取失败: {e}"
                                     f"(多为同花顺 cookie/hexin-v 过期, 需重抓 get_by_uid)")
        return

    # 拉取成功: 重置失败计数; 若此前告过警, 补一条恢复登记(并入盘后汇总闭环)
    if _fail_alerted:
        from backend.services.system_health import report_issue
        report_issue("博主发帖", f"「{cfg.get('blogger_name', '博主')}」拉取已恢复")
    _fetch_fail_count = 0
    _fail_alerted = False
    _backoff_until = 0.0   # 成功即清退避

    if not posts:
        return

    total_new = 0
    posts_sorted = sorted(
        posts, key=lambda p: (p.get("posted_at") is None, p.get("posted_at"))
    )
    pushed_ids = []
    for post in posts_sorted:
        is_new = await repository.save_post(
            blogger_fid=cfg.get("user_code", ""),
            blogger_name=post["blogger_name"],
            post_id=post["post_id"],
            posted_at=post["posted_at"],
            content=post["content"],
            stock_codes=post["stock_codes"],
            url=post["url"],
        )
        if not is_new:
            continue
        total_new += 1
        ok = await notifier.send_card(build_post_card(post))
        if ok:
            recent = await repository.get_recent_posts(cfg.get("user_code", ""), limit=50)
            for r in recent:
                if r.get("post_id") == post["post_id"]:
                    pushed_ids.append(r["id"])
                    break
    if pushed_ids:
        await repository.mark_pushed(pushed_ids)

    if total_new:
        logger.info(f"[blogger_posts] 本轮新帖 {total_new} 条")
