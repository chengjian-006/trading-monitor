"""推送补发: 全局通道(飞书/企微)关→开时, 把关闭期间错过的关键信号汇总成一张卡补发。

规约(0618定):
- 只补关键信号: 买/卖/减仓/急跌预警(cfzy_biz_signals 内 direction ∈ KEY_DIRECTIONS)。
- 外加一条「当前市场风险档」横幅: 读最新 cfzy_biz_market_risk 状态, YELLOW/RED 才显示
  (该表按天一行快照, 无逐条迁移日志, 故只给当前档位提示, 不逐条补风险事件)。
- 时效/数量: 只回溯最近 24 小时, 最多 30 条, 超出取最新并标「等N条」。
- 飞书、企微各自独立: 哪个通道关闭期间错过的, 那个通道重新打开时补; 互不串号。
- 即时门控即时丢弃, 本模块靠「已落库的信号」反查窗口重建, 不维护待发队列。
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

KEY_DIRECTIONS = ("buy", "sell", "reduce", "plunge")
MAX_AGE_HOURS = 24
MAX_ITEMS = 30
FMT = "%Y-%m-%d %H:%M:%S"

_DIR_LABEL = {"buy": "🟢买入", "sell": "🔴卖出", "reduce": "🟡减仓", "plunge": "⚠️急跌预警"}
# 配置里记「关闭时刻」的字段名(通道 → config key)
STAMP_KEY = {"lark": "lark_disabled_at"}  # 企微通道已移除; PushPlus 无开关无需补发


def parse_stamp(val) -> datetime | None:
    """把 config 里存的关闭时刻字符串解析成 datetime; 空/非法 → None。"""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.strptime(str(val), FMT)
    except (ValueError, TypeError):
        return None


def compute_window(disabled_at, now: datetime):
    """补发时间窗 = [max(disabled_at, now-24h), now]。
    disabled_at 缺失/未来 → None(无需补发)。"""
    dt = parse_stamp(disabled_at)
    if dt is None:
        return None
    floor = now - timedelta(hours=MAX_AGE_HOURS)
    start = max(dt, floor)
    if start >= now:
        return None
    return start, now


def cap_events(events: list[dict]) -> tuple[list[dict], int]:
    """事件按时间升序传入; 保留最新 MAX_ITEMS 条, 返回 (保留[降序], 丢弃条数)。"""
    ordered = sorted(events, key=lambda e: e.get("triggered_at") or datetime.min, reverse=True)
    kept = ordered[:MAX_ITEMS]
    return kept, max(0, len(ordered) - MAX_ITEMS)


def _fmt_time(t) -> str:
    if isinstance(t, datetime):
        return t.strftime("%m-%d %H:%M")
    s = str(t or "")
    return s[5:16] if len(s) >= 16 else s


def build_backfill_card(channel: str, kept_events: list[dict], dropped: int,
                        risk_state: str = "GREEN") -> tuple[str, list]:
    """构建「错过消息回顾」摘要: 返回 (企微纯文本, 飞书元素列表)。
    kept_events 已按时间降序、已封顶。"""
    from backend.services.lark_notifier import md_element

    n = len(kept_events)
    tail = f"(最新{MAX_ITEMS}条, 另有{dropped}条更早未列)" if dropped else ""
    head = f"📮 错过消息回顾 — 关闭期间共 {n + dropped} 条关键信号{tail}"

    elements = [md_element(f"**📮 错过消息回顾**　_推送开关关闭期间错过的关键信号, 现汇总补发_")]
    text_lines = [head, ""]

    risk = (risk_state or "GREEN").upper()
    if risk in ("YELLOW", "RED"):
        tag = "🔴 危险档 · 强烈建议空仓" if risk == "RED" else "🟡 谨慎档"
        banner = f"⚠️ 当前市场风险档: {tag}"
        elements.append(md_element(f"**{banner}**"))
        text_lines.append(banner)
        text_lines.append("")

    # 移动优化(v1.7.581): 逐条换行文本行, 类型加粗前置, 时间/标的/信号全名换行不截
    #   (原 2列表格把 时间+名称+代码+信号 挤 info 一格, 手机端字符级截断——md_table 单元格不换行只截断,
    #    "自动换行"是错误前提, 与竞价卡同源 bug)
    info_lines = []
    for e in kept_events:
        kind = _DIR_LABEL.get(str(e.get("direction") or "").lower(), e.get("direction") or "")
        nm = e.get("name") or ""
        code = e.get("code") or ""
        sig = e.get("signal_name") or ""
        info_lines.append(f"**{kind}**　{_fmt_time(e.get('triggered_at'))} **{nm}** {code} {sig}".rstrip())
        text_lines.append(f"  • {_fmt_time(e.get('triggered_at'))} {kind} {nm}({code}) {sig}")

    if info_lines:
        elements.append(md_element("\n".join(info_lines)))
    else:
        elements.append(md_element("_关闭期间无关键信号_"))
        text_lines.append("(关闭期间无关键信号)")

    return "\n".join(text_lines), elements


async def backfill_channel(channel: str, disabled_at, now: datetime | None = None) -> bool:
    """通道(lark/wecom)关→开时调用: 捞窗口内关键信号 + 当前风险档, 汇总成一卡, 只发该通道。
    返回是否实际发出(无事件或非生产环境 → False)。"""
    from backend.core.config import load_config
    from backend.models import repository
    from backend.services import notifier
    from backend.services.market_risk_controller import get_risk_state

    now = now or datetime.now()
    window = compute_window(disabled_at, now)
    if window is None:
        logger.info(f"[push_backfill] {channel} 无有效关闭窗口, 跳过补发")
        return False
    start, end = window

    try:
        events = await repository.get_key_signals_between(
            start.strftime(FMT), end.strftime(FMT), list(KEY_DIRECTIONS))
    except Exception as e:
        logger.warning(f"[push_backfill] 查关键信号失败: {e}")
        return False

    if not events:
        logger.info(f"[push_backfill] {channel} 窗口 [{start:%m-%d %H:%M}~{end:%m-%d %H:%M}] 无关键信号, 不补发")
        return False

    kept, dropped = cap_events(events)
    try:
        risk_state = await get_risk_state()
    except Exception:
        risk_state = "GREEN"

    text, elements = build_backfill_card(channel, kept, dropped, risk_state)

    cfg = load_config()
    title = "📮 错过消息回顾"
    # pushplus=False: 补发只补飞书(它被关过才有漏), PushPlus 无开关一直实时收着, 再fanout=微信端重复
    ok = await notifier.send_dual_card_to(
        text, lark_title=title, elements=elements,
        lark_webhook=cfg.get("lark_webhook", ""), lark_on=True, pushplus=False)
    logger.info(f"[push_backfill] {channel} 补发 {len(kept)}/{len(kept)+dropped} 条关键信号, 发送={ok}")
    return ok
