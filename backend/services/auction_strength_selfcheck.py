"""竞价高开弱转强 首日自检 → 飞书 (v1.7.276 临时验证任务, 确认后可删).

每交易日 9:31 跑: 核查 情绪时效 / 竞价采集 / 买点触发 / 服务健康, 推一条飞书报告。
不依赖会话, 服务器端定时执行。验证完成后删 cfzy_sys_scheduled_tasks 中 job_id=auction_strength_selfcheck 的行即可下线。
"""
import logging
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend.services import lark_notifier
from backend.services.lark_notifier import post_lark_text

logger = logging.getLogger(__name__)


async def run_auction_strength_selfcheck():
    now = datetime.now()
    if now.weekday() >= 5:
        logger.info("[as_selfcheck] 非交易日, 跳过")
        return
    from backend.services import card_kit

    today = now.strftime("%Y-%m-%d")
    # v1.7.398: 标题不再拼时间(卡片正文首行有统一的右对齐发送时间)
    title = "⚙️ 竞价高开弱转强·首日自检"
    lines = ["【竞价高开弱转强·首日自检】"]   # 纯文本回退用(企微文本自带 🕐 时间前缀)

    # 1. 情绪时效(门控数据是否当日值)
    em = await repository.get_latest_emotion()
    emotion_ok = "bad"
    if em:
        ed = str(em.get("trade_date"))
        up = em.get("up_count")
        dn = em.get("down_count")
        fresh = "当日✓" if ed == today else f"旧值✗(快照{ed})"
        emotion_ok = "ok" if ed == today else "warn"
        if up is not None and up >= 3500:
            regime = f"热(红盘{up})→放行"
        elif dn is not None and dn >= 3500:
            regime = f"冰点(绿盘{dn})→放行"
        else:
            regime = f"中性(红{up}/绿{dn})→剔除"
        lines.append(f"情绪: {fresh} | {regime}")
        emotion_txt = f"{fresh} | {regime}"
    else:
        lines.append("情绪: 无快照(门控无法放行)")
        emotion_txt = "无快照(门控无法放行)"

    # 2. 竞价采集 + 竞价额≥5000万
    #    读侧兜底: 剔历史残留的板块/指数码(如 399366 能源金属), 本提醒只面向个股。
    #    与 auction_pool_refresher._is_stock 同源(采集侧已从源头拦, 这里再兜一道防旧数据)。
    _is_stock = lambda c: str(c or "")[:2] in ("00", "30", "60", "68")
    snaps = [r for r in await repository.get_auction_snapshots(today) if _is_stock(r.get("code"))]
    big = [r for r in await repository.get_auction_snapshots(today, min_amount=5e7) if _is_stock(r.get("code"))]
    collect_ok = "ok" if snaps else "bad"
    lines.append(f"竞价采集: 今日{len(snaps)}只, 竞价额≥0.5亿={len(big)}只")
    if big:
        top = " / ".join(f"{r.get('name', '')}{(r.get('auction_amount') or 0) / 1e8:.2f}亿" for r in big[:8])
        lines.append(f"  ≥0.5亿: {top}")

    # 3. 买点是否触发
    rows = await repository._fetchall(
        "SELECT code, name, detail, triggered_at FROM cfzy_biz_signals "
        "WHERE signal_id='BUY_AUCTION_STRENGTH' AND trigger_date=%s ORDER BY triggered_at",
        (today,))
    if rows:
        lines.append(f"买点触发: {len(rows)}条")
        for r in rows[:6]:
            lines.append(f"  {r.get('name', '')}({r['code']}) {str(r['triggered_at'])[11:16]}")
    else:
        lines.append("买点触发: 0条(四道门很严, 没票满足属正常, 不等于坏了)")

    # 飞书卡片 schema 2.0 — 五区骨架: 结论(灯串) → 数据(短表) → 👉建议 → 口径折叠
    checks = [(emotion_ok, "情绪"), (collect_ok, "采集")]
    elements: list = [lark_notifier.md_element(
        f"{card_kit.light_string(checks)}\n"
        f"竞价额≥0.5亿 **{len(big)}** 只 · 买点触发 **{len(rows)}** 条")]
    elements.append(lark_notifier.md_element(
        f"**情绪门控**　{emotion_txt}\n"
        f"**竞价采集**　今日 {len(snaps)} 只 ／ 竞价额≥0.5亿 {len(big)} 只"))
    if big:
        big_rows = [(r.get("name", "") or r.get("code", ""),
                     f"{(r.get('auction_amount') or 0) / 1e8:.2f}亿") for r in big[:10]]
        elements.append(lark_notifier.md_element(f"**竞价额≥0.5亿（{len(big)}只）**"))
        if len(big_rows) > 8:
            elements.append(card_kit.short_table(["股票", "竞价额"], big_rows[:5]))
            elements.append(lark_notifier.md_element(f"…等 **{len(big)}** 只"))
        else:
            elements.append(card_kit.short_table(["股票", "竞价额"], big_rows))
    if rows:
        buy_rows = [(str(r["triggered_at"])[11:16],
                     f"{r.get('name', '') or ''}　{r['code']}") for r in rows[:10]]
        elements.append(lark_notifier.md_element(f"**🟢 买点触发（{len(rows)}）**"))
        elements.append(card_kit.short_table(["时间", "名称·代码"], buy_rows))
    else:
        elements.append(lark_notifier.md_element("**买点触发**　0 条"))
    healthy = emotion_ok == "ok" and collect_ok == "ok"
    elements.append(card_kit.advice("自检通过，无需处理" if healthy else "有异常项，查后端日志"))
    elements.append(card_kit.fold(
        "口径说明",
        "买点 0 条属正常: 四道门很严, 没票满足不等于坏了。\n"
        "本卡为竞价弱转强首日自检临时任务, 验证完成后删调度行即可下线。"))

    text = "\n".join(lines)
    cfg = load_config()
    wh = cfg.get("lark_webhook", "")
    if cfg.get("lark_enabled", False) and wh:
        # 保持直发通道: 优先发 schema 2.0 结构卡(系统族 grey); 失败回退纯文本卡(保留原行式信息)
        ok = await lark_notifier.post_lark_card_v2(wh, title, elements, template="grey")
        if not ok:
            ok = await post_lark_text(wh, text)
        logger.info(f"[as_selfcheck] 推飞书 ok={ok}")
    else:
        logger.info(f"[as_selfcheck] 飞书未启用, 报告:\n{text}")
    return text
