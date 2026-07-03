"""数据源健康度主动预警 (v1.7.389) — 0612误报普查第3项整改.

背景: 分时冻结回放/竞价0/0/日K缺今日bar这类源降级此前是沉默的, 靠误报或几天后人工普查
才暴露(科创分时回放了三天才发现)。本模块让检测点在发现降级时同步上报, 当天即推预警。

分工: 校验下沉(v1.7.387)负责"垃圾数据不进下游", 本模块负责"源挂了让人知道"。
上报点(均为已有校验逻辑顺手埋点, 不新增检测):
  - ai_analyst._sanitize_stale_index_trends: A股指数分时冻结回放被清洗时
  - ai_analyst.get_market_stats: 连续竞价时段全市场快照无涨跌幅(垃圾被拒)时
  - signal_engine._ensure_today_bar: 10:00后日K源仍缺今日bar被追加时(早盘是正常盲区不报)

推送策略: 每类每天最多一次(冻结回放每30s检测一轮会反复上报, 必须去重),
达到该类阈值才算"源挂"(日K缺bar偶发几次是个股抖动)。
flush 挂在 check_data_sanity 任务(300s一轮)末尾, 不新增调度任务。
"""
import logging
import threading
from datetime import date, datetime

logger = logging.getLogger(__name__)

# kind -> (预警标签(推送文案用大白话, 别用"冻结回放"这类术语), 当日触发次数阈值)
# 注: 曾有 kline_missing_today(盘中日K缺今日bar), 0612上线当天即证伪 —
# 新浪日K盘中固有不含今日bar, "缺今日bar"是每轮每股常态非降级信号, 已改为下面的三源全败口径。
KINDS = {
    "index_trends_frozen": ("大盘分时行情卡了一阵没更新", 1),
    "market_stats_empty": ("全市场涨跌家数一度拿不到数据", 1),
    "kline_network_down": ("个股日K行情源不稳(已用缓存兜底)", 5),
}

# 恢复判定: 异常存续期间检测每轮(约30s)都会上报, 若最近一次上报距 flush 时刻
# 已超过这个分钟数 = 后续几轮都没再犯 → 推送里直接写"现在已恢复正常"。
_RECOVERED_GAP_MIN = 3

# 开盘宽限: 东财/同花顺指数分时接口在刚开盘头一分多钟常残留昨日末点(15:00),
# 几十秒后即切到今日盘中数据 — 这是每日固有 rollover 抖动, 非源故障。
# 仅当冻结全部发生在 09:32 之前(即过了开盘就恢复新鲜、未再冻结)才静默;
# 若过了 09:32 仍在冻(ev["last"] >= 09:32), 视为真降级照常预警。
# 注: 持续冻结会每轮上报、把 ev["last"] 不断推后, 故"末次冻结 < 09:32"
# 这一条同时蕴含了"开盘后已恢复", 无需再单独判断当前时刻。
_OPEN_GRACE_HHMM = "09:32"
_OPEN_GRACE_KINDS = {"index_trends_frozen"}

_lock = threading.Lock()
_events: dict[str, dict] = {}    # kind -> {date, count, first, last, detail}
_alerted: dict[str, str] = {}    # kind -> 已推送日期


def reset_for_test():
    with _lock:
        _events.clear()
        _alerted.clear()


def report(kind: str, detail: str = "", today: str | None = None,
           now_hhmm: str | None = None):
    """检测点上报一次源降级事件。同步函数, 线程安全, 可在 to_thread 里调。"""
    if kind not in KINDS:
        return
    today = today or date.today().isoformat()
    now_hhmm = now_hhmm or datetime.now().strftime("%H:%M")
    with _lock:
        ev = _events.get(kind)
        if not ev or ev["date"] != today:
            ev = {"date": today, "count": 0, "first": now_hhmm, "last": now_hhmm, "detail": ""}
            _events[kind] = ev
        ev["count"] += 1
        ev["last"] = now_hhmm
        if detail:
            ev["detail"] = detail


def _hhmm_to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def drain_alerts(today: str | None = None, now_hhmm: str | None = None) -> list[str]:
    """取出今日达到阈值且尚未推送过的预警行(大白话文案), 并标记已推。"""
    today = today or date.today().isoformat()
    now_hhmm = now_hhmm or datetime.now().strftime("%H:%M")
    lines = []
    with _lock:
        for kind, (label, threshold) in KINDS.items():
            ev = _events.get(kind)
            if not ev or ev["date"] != today:
                continue
            if _alerted.get(kind) == today or ev["count"] < threshold:
                continue
            # 开盘 rollover 抖动静默(见 _OPEN_GRACE_HHMM): 不标记已推, 留待
            # 真持续冻结(末次时刻越过宽限)时仍能正常告警。
            if kind in _OPEN_GRACE_KINDS and ev["last"] < _OPEN_GRACE_HHMM:
                continue
            _alerted[kind] = today
            seg = f"{label}: {ev['first']}~{ev['last']} 共{ev['count']}次"
            if ev["detail"]:
                seg += f" ({ev['detail']})"
            try:
                recovered = _hhmm_to_min(now_hhmm) - _hhmm_to_min(ev["last"]) >= _RECOVERED_GAP_MIN
            except Exception:
                recovered = False
            seg += ", 现在已恢复正常" if recovered else ", 目前可能还没恢复(系统会继续自动跳过)"
            lines.append(seg)
    return lines


async def flush_data_health():
    """有待推预警则推一条文本(企微+飞书走 notifier 既有通道)。由 check_data_sanity 每轮调用。"""
    lines = drain_alerts()
    if not lines:
        return
    text = ("行情数据源刚才有点波动:\n\n" + "\n".join(f"- {x}" for x in lines) +
            "\n\n波动期间系统已自动跳过异常数据, 不会因此误报, 一般无需处理; "
            "若一整天反复出现再检查数据源。")
    try:
        from backend.services import notifier
        # 走红色告警模版(非默认蓝色"盘面播报"), 让真·源故障一眼可辨、不与日常播报混淆。
        await notifier.send_dual(text, lark_title="⚠️ 数据源健康预警", template="red")
        logger.warning(f"[data_health] 源健康预警已推送: {lines}")
    except Exception as e:
        logger.warning(f"[data_health] 预警推送失败: {e}")
