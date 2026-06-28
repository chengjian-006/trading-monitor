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
    today = now.strftime("%Y-%m-%d")
    # v1.7.398: 标题不再拼时间(卡片正文首行有统一的右对齐发送时间)
    title = "竞价高开弱转强·首日自检"
    lines = ["【竞价高开弱转强·首日自检】"]   # 纯文本回退用(企微文本自带 🕐 时间前缀)
    elements: list = []                                                  # 飞书卡片 schema 2.0

    # 1. 情绪时效(门控数据是否当日值)
    em = await repository.get_latest_emotion()
    if em:
        ed = str(em.get("trade_date"))
        up = em.get("up_count")
        dn = em.get("down_count")
        fresh = "当日✓" if ed == today else f"旧值✗(快照{ed})"
        if up is not None and up >= 3500:
            regime = f"热(红盘{up})→放行"
        elif dn is not None and dn >= 3500:
            regime = f"冰点(绿盘{dn})→放行"
        else:
            regime = f"中性(红{up}/绿{dn})→剔除"
        lines.append(f"情绪: {fresh} | {regime}")
    else:
        lines.append("情绪: 无快照(门控无法放行)")

    # 2. 竞价采集 + 竞价额≥5000万
    snaps = await repository.get_auction_snapshots(today)
    big = await repository.get_auction_snapshots(today, min_amount=5e7)
    lines.append(f"竞价采集: 今日{len(snaps)}只, 竞价额≥5000万={len(big)}只")
    if big:
        top = " / ".join(f"{r.get('name', '')}{(r.get('auction_amount') or 0) / 1e4:.0f}万" for r in big[:8])
        lines.append(f"  ≥5000万: {top}")

    # 卡片: 情绪 + 竞价采集摘要(文字) → 后接 ≥5000万 候选表(多行多列用原生表格)
    elements.append(lark_notifier.md_element(
        f"**情绪门控**　{lines[1].split(': ', 1)[-1]}\n"
        f"**竞价采集**　今日 {len(snaps)} 只 ／ 竞价额≥5000万 {len(big)} 只"))
    if big:
        cols = [
            {"name": "name", "display_name": "名称", "data_type": "text", "width": "62%"},
            {"name": "amt", "display_name": "竞价额", "data_type": "text",
             "width": "38%", "horizontal_align": "right"},
        ]
        big_rows = [{"name": r.get("name", "") or r.get("code", ""),
                     "amt": f"{(r.get('auction_amount') or 0) / 1e4:.0f}万"} for r in big[:10]]
        elements.append(lark_notifier.md_element(f"**竞价额≥5000万（{len(big)}只）**"))
        elements.append(lark_notifier.table_element(cols, big_rows, page_size=10))

    # 3. 买点是否触发
    rows = await repository._fetchall(
        "SELECT code, name, detail, triggered_at FROM cfzy_biz_signals "
        "WHERE signal_id='BUY_AUCTION_STRENGTH' AND trigger_date=%s ORDER BY triggered_at",
        (today,))
    if rows:
        lines.append(f"买点触发: {len(rows)}条")
        for r in rows[:6]:
            lines.append(f"  {r.get('name', '')}({r['code']}) {str(r['triggered_at'])[11:16]}")
        cols = [
            {"name": "name", "display_name": "名称", "data_type": "text", "width": "50%"},
            {"name": "code", "display_name": "代码", "data_type": "text", "width": "28%"},
            {"name": "time", "display_name": "时间", "data_type": "text",
             "width": "22%"},
        ]
        buy_rows = [{"name": r.get("name", "") or "", "code": r["code"],
                     "time": str(r["triggered_at"])[11:16]} for r in rows[:10]]
        elements.append(lark_notifier.md_element(f"**🟢 买点触发（{len(rows)}）**"))
        elements.append(lark_notifier.table_element(cols, buy_rows, page_size=10))
    else:
        lines.append("买点触发: 0条(四道门很严, 没票满足属正常, 不等于坏了)")
        elements.append(lark_notifier.md_element(
            "**买点触发**　0 条\n_四道门很严, 没票满足属正常, 不等于坏了_"))

    text = "\n".join(lines)
    cfg = load_config()
    wh = cfg.get("lark_webhook", "")
    if cfg.get("lark_enabled", False) and wh:
        # 优先发 schema 2.0 表格卡; 失败回退纯文本卡(保留原行式信息)
        ok = await lark_notifier.post_lark_card_v2(wh, title, elements, template="blue")
        if not ok:
            ok = await post_lark_text(wh, text)
        logger.info(f"[as_selfcheck] 推飞书 ok={ok}")
    else:
        logger.info(f"[as_selfcheck] 飞书未启用, 报告:\n{text}")
    return text
