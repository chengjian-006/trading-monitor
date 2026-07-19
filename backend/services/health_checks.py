# -*- coding: utf-8 -*-
"""系统体检 — 断言式定时校验 (v1.7.698).

为什么要有这个模块
──────────────────
现有八个"健康检查"任务的成功判定几乎都是「handler 没抛异常」, 于是这四类静默失效
全部盖不住(2026-07-19 审计逐条确认):
  · 接口 HTTP 200 但内容为空 —— 问财 4 个榜全 0 只, 却走"成功"分支只打一行 info
  · 任务注册了但从未触发 —— last_run_at 恒 NULL, 而失败列表只筛 consecutive_failures>0,
    从未跑过的任务计数是 0, 永远不会出现在里面
  · 表长期无新数据 —— cfzy_biz_holding_state_fwd 空了 3 周无人知(上游空则 return, 记 success)
  · 数据陈旧 —— 行业映射停在 7/11, refresher 有四条静默 return 路径, 一条都不告警

本模块把判定建在 **outcome** 上而非 exception 上: 每项检查声明"期望什么", 跑出"实际什么",
不一致就是失败。三条设计约束:
  1. **逐项异常隔离**: 某项检查自己炸了 → 记为该项失败(带异常信息), 不拖垮整轮。
  2. **先落库再推送**: 结果写 cfzy_sys_health_check, 推送只是展示层。推失败不丢数据,
     下轮能补报 —— 直接修正旧 system_health"finally 无条件清空导致推送失败即蒸发"。
  3. **报告自带元检查**: 执行项数少于注册项数 = 有检查项自己挂了; 再带推送心跳,
     把"没消息"和"消息发不出去"区分开。

阈值一律按**交易日**而非自然日: 周日看核心表都是"距今 2 天"(最新=周五), 那是健康的。
按自然日算会导致每周一早上全线误报, 三天就没人看了。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

CRITICAL, WARN, INFO = "critical", "warn", "info"
_SEV_ORDER = {CRITICAL: 0, WARN: 1, INFO: 2}
_SEV_ICON = {CRITICAL: "🔴", WARN: "🟡", INFO: "⚪"}


@dataclass
class CheckResult:
    key: str
    name: str
    category: str
    severity: str
    ok: bool
    actual: str = ""
    expected: str = ""
    detail: str = ""

    def as_row(self) -> dict:
        return {"key": self.key, "name": self.name, "category": self.category,
                "severity": self.severity, "ok": self.ok, "actual": self.actual,
                "expected": self.expected, "detail": self.detail}


@dataclass
class Check:
    key: str
    name: str
    category: str
    severity: str
    fn: object                      # async () -> (ok, actual, expected, detail?)
    only_trading_day: bool = False  # 仅交易日有意义(非交易日跳过, 不计入失败)
    tags: list = field(default_factory=list)


# ── 时间基准 ──

def expected_data_date(now: datetime | None = None):
    """数据"应该新到哪一天": 交易日收盘(15:05)后=今天, 否则=上一个交易日。

    这是所有新鲜度检查的基准。写死"今天"会在周末/盘前全线误报。
    """
    from backend.core.trading_calendar import is_workday, prev_trading_day
    now = now or datetime.now()
    if is_workday(now) and now.strftime("%H:%M") >= "15:05":
        return now.date()
    return prev_trading_day(now.date())


def _as_date(v):
    from datetime import date as _d
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, _d):
        return v
    s = str(v)[:10]
    if len(s) == 8 and s.isdigit():          # limit_up_daily 存的是 20260717
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return _d.fromisoformat(s)
    except ValueError:
        return None


# ── 通用断言构件 ──

async def _table_fresh(table: str, col: str, max_lag_days: int = 0):
    """表最新时点是否 >= 期望数据日 - max_lag_days。返回 (ok, actual, expected)。

    只查 MAX(col), **绝不用 COUNT(*)**: cfzy_sys_kline_5m 有 6500 万行、kline_cache 600 万行,
    InnoDB 的 COUNT(*) 要全表扫, 一项就能把整轮体检拖到十分钟以上(实测)。
    而 MAX 走索引近乎 O(1), 且空表时返回 NULL —— 空表判定本来就不需要 COUNT。
    """
    from datetime import timedelta

    from backend.models.repo._db import _fetchone
    row = await _fetchone(f"SELECT MAX(`{col}`) mx FROM `{table}`")
    mx = row.get("mx") if row else None
    want = expected_data_date() - timedelta(days=max_lag_days)
    if mx is None:
        return False, "空表", f"≥{want}"
    got = _as_date(mx)
    if got is None:
        return False, f"无法解析({mx})", str(want)
    return got >= want, str(got), f"≥{want}"


# ══════════════════════════════════════════════════════════════════
# 一、定时任务健康
# ══════════════════════════════════════════════════════════════════

async def _chk_task_never_ran():
    """从未跑过的任务。这类任务 consecutive_failures 恒为 0, 现有失败列表永远筛不到。"""
    from backend.models.repo._db import _fetchall
    rows = await _fetchall(
        "SELECT job_id FROM cfzy_sys_scheduled_tasks "
        "WHERE enabled=1 AND last_run_at IS NULL "
        "AND created_at < DATE_SUB(NOW(), INTERVAL 2 DAY) ORDER BY job_id")
    names = [r["job_id"] for r in rows]
    return (not names, f"{len(names)}个" + (f": {', '.join(names[:4])}" if names else ""), "0个")


async def _chk_task_orphan():
    """DB 里 enabled=1 但代码已无对应 handler 的任务 —— 永远注册不上、永远不会跑。

    单列一项(INFO)而不是混进"超期未跑": 孤儿是**已知的历史残留**, 混进超期项会让它
    每天永久报红, 而永久报红的检查项等于墙纸, 几天后整张报告就被无视了。
    """
    from backend.models.repo._db import _fetchall
    from backend.services.task_registry import TASK_HANDLERS
    rows = await _fetchall(
        "SELECT job_id, handler FROM cfzy_sys_scheduled_tasks WHERE enabled=1")
    bad = [r["job_id"] for r in rows if r["handler"] not in TASK_HANDLERS]
    return (True, f"{len(bad)}个" + (f": {', '.join(bad[:4])}" if bad else ""), "仅记录")


async def _chk_task_overdue():
    """启用中但久未运行的任务(按各自调度周期的 3 倍容忍)。孤儿任务由上一项单独统计, 此处排除。"""
    import json as _json

    from backend.models.repo._db import _fetchall
    from backend.services.task_registry import TASK_HANDLERS
    rows = await _fetchall(
        "SELECT job_id, handler, schedule_type, schedule_config, last_run_at "
        "FROM cfzy_sys_scheduled_tasks WHERE enabled=1 AND last_run_at IS NOT NULL")
    now = datetime.now()
    bad = []
    for r in rows:
        if r["handler"] not in TASK_HANDLERS:
            continue          # 孤儿, 见 _chk_task_orphan
        try:
            sc = _json.loads(r["schedule_config"] or "{}")
        except Exception:
            continue
        if r["schedule_type"] == "interval":
            tol = max(int(sc.get("seconds", 300)) * 3, 900)
        else:
            # cron: 周任务给 9 天, 日任务给 2 天
            tol = 9 * 86400 if sc.get("day_of_week") else 2 * 86400
        lag = (now - r["last_run_at"]).total_seconds()
        if lag > tol:
            bad.append(f"{r['job_id']}({int(lag / 3600)}h)")
    return (not bad, f"{len(bad)}个" + (f": {', '.join(bad[:4])}" if bad else ""), "0个")


async def _chk_task_failing():
    from backend.models.repo._db import _fetchall
    rows = await _fetchall(
        "SELECT job_id, consecutive_failures FROM cfzy_sys_scheduled_tasks "
        "WHERE enabled=1 AND consecutive_failures >= 2 ORDER BY consecutive_failures DESC")
    bad = [f"{r['job_id']}×{r['consecutive_failures']}" for r in rows]
    return (not bad, f"{len(bad)}个" + (f": {', '.join(bad[:4])}" if bad else ""), "0个")


async def _chk_task_disabled():
    """enabled=0 的存量哑任务: 代码还在、永不执行、无人知晓。只提示不报警。"""
    from backend.models.repo._db import _fetchall
    rows = await _fetchall(
        "SELECT job_id FROM cfzy_sys_scheduled_tasks WHERE enabled=0 ORDER BY job_id")
    names = [r["job_id"] for r in rows]
    return (True, f"{len(names)}个" + (f": {', '.join(names[:5])}" if names else ""), "仅记录")


# ══════════════════════════════════════════════════════════════════
# 二、数据新鲜度
# ══════════════════════════════════════════════════════════════════

async def _chk_kline_cache():
    return await _table_fresh("cfzy_sys_kline_cache", "trade_date")


async def _chk_kline_5m():
    return await _table_fresh("cfzy_sys_kline_5m", "dt")


async def _chk_index_kline_5m():
    """最严的一项: 新浪只有约21个交易日滚动窗, 断了超过这个数就**永久补不回来**。"""
    return await _table_fresh("cfzy_sys_index_kline_5m", "dt")


async def _chk_market_risk():
    return await _table_fresh("cfzy_biz_market_risk", "trade_date")


async def _chk_model_winrate():
    return await _table_fresh("cfzy_biz_model_winrate", "run_date", max_lag_days=1)


async def _chk_industry_map():
    """周任务, 给 9 天容忍。行业映射停更会让板块共振禁补仓无声地算偏。"""
    return await _table_fresh("cfzy_sys_industry_map", "updated_at", max_lag_days=9)


async def _chk_holding_state_fwd():
    return await _table_fresh("cfzy_biz_holding_state_fwd", "run_date", max_lag_days=9)


async def _chk_emotion_snapshot():
    return await _table_fresh("cfzy_sys_emotion_snapshot", "trade_date")


# ══════════════════════════════════════════════════════════════════
# 三、外部接口(真拉一次 + 校验内容, 不看 HTTP 码)
# ══════════════════════════════════════════════════════════════════

async def _chk_sina_quote():
    from backend.fetcher import quotes
    codes = ["600519", "000001", "300750", "601318", "000858"]
    q = await quotes.get_realtime_quotes(codes)
    got = sum(1 for c in codes if (q or {}).get(c, {}).get("price"))
    return got >= 4, f"{got}/5 只有价", "≥4/5"


async def _chk_sina_market_snapshot():
    """全市场快照。现有 cross_check 拿到 <3000 只是**静默 return** —— 恰是最该报的时候。"""
    from backend.services.market_risk_controller import _today_snapshot
    snap = await _today_snapshot()
    n = len(snap or {})
    return n >= 3000, f"{n} 只", "≥3000 只"


async def _chk_sina_index_5m():
    from backend.fetcher.index_klines import fetch_index_5m
    bars = await fetch_index_5m("sh000001", datalen=64)
    n = len(bars)
    last = bars[-1]["dt"][:10] if bars else "-"
    want = str(expected_data_date())
    return (n >= 48 and last == want), f"{n}根, 末根{last}", f"≥48根且末根={want}"


# 注: 曾写过一个直接探测 baostock 的检查项, 已删除。两个原因:
#   1. baostock 走自有 socket 协议且非线程安全, 实测 login 成功后 query 可挂死 >45s,
#      而它跑在 asyncio.to_thread 里 —— wait_for 超时也杀不掉线程, 整个进程退不出去。
#      一个体检项把体检本身卡死, 得不偿失。
#   2. 冗余: baostock 只服务 cfzy_sys_kline_5m 的每晚追加, 它挂了必然导致该表停更,
#      data_kline_5m 那项(0.0s, 走索引)就会报 —— 用便宜的结果断言替代昂贵的探活。


async def _chk_outbound_ip():
    """出口 IP 是所有推送的总闸: is_production() 白名单不匹配 → 全系统推送静默哑火,
    而这件事本身不会告警(报告发不出去正是因为它)。所以这项失败时报告多半也送不达 ——
    它的价值在于落库留痕, 事后一眼看出"那几天为什么什么都没收到"。"""
    from backend.core.config import get_outbound_ip, is_production
    ip = await get_outbound_ip()
    ok = await is_production()
    return ok, str(ip), "在生产白名单内"


async def _chk_trading_calendar():
    """chinese_calendar 缺失/超范围会静默回退成"仅按周几", 节假日被当交易日。"""
    import datetime as _dt
    try:
        import chinese_calendar
    except ImportError:
        return False, "库未安装", "已安装且覆盖今年"
    try:
        chinese_calendar.is_holiday(_dt.date(datetime.now().year, 12, 31))
        return True, f"覆盖到{datetime.now().year}年底", "已安装且覆盖今年"
    except NotImplementedError:
        return False, f"{datetime.now().year}年超出支持范围", "覆盖今年"


# ══════════════════════════════════════════════════════════════════
# 四、业务规则自洽
# ══════════════════════════════════════════════════════════════════

async def _chk_signal_volume():
    """当日信号数落在历史区间内(骤降为 0 或暴增都可疑)。"""
    from backend.models.repo._db import _fetchall, _fetchone
    d = expected_data_date()
    cur = await _fetchone(
        "SELECT COUNT(*) n FROM cfzy_biz_signals WHERE DATE(triggered_at)=%s", (d,))
    hist = await _fetchall(
        "SELECT COUNT(*) n FROM cfzy_biz_signals "
        "WHERE DATE(triggered_at) < %s AND DATE(triggered_at) >= DATE_SUB(%s, INTERVAL 30 DAY) "
        "GROUP BY DATE(triggered_at)", (d, d))
    n = int(cur["n"]) if cur else 0
    counts = sorted(int(r["n"]) for r in hist)
    if len(counts) < 5:
        return True, f"{n}条(历史样本不足)", "样本≥5日才判定"
    lo, hi = counts[len(counts) // 10], counts[-1] * 3
    return lo <= n <= hi, f"{n}条", f"{lo}~{hi}条"


async def _chk_eod_audit_effective():
    """EOD 复核若全部 unverified(日线源挂了), suspects 为空 → 一条推送都不会有, 看着风平浪静。"""
    from backend.models.repo._db import _fetchall
    d = expected_data_date()
    rows = await _fetchall(
        "SELECT eod_audit, COUNT(*) n FROM cfzy_biz_signals "
        "WHERE DATE(triggered_at)=%s GROUP BY eod_audit", (d,))
    tot = sum(int(r["n"]) for r in rows)
    if tot == 0:
        return True, "当日无信号", "不适用"
    unv = sum(int(r["n"]) for r in rows if str(r["eod_audit"] or "") == "unverified")
    pct = unv / tot * 100
    return pct < 30, f"unverified {unv}/{tot}({pct:.0f}%)", "<30%"


async def _chk_rounds_synced():
    """交易回合应跟得上成交流水。

    持仓本身是从 trades FIFO 推导的, 不存在"对不上"; 真实的失效模式是**导入了成交单
    但回合重建没跑**(重建是导入后的异步后台任务 + 15:20 定时, 两条都可能悄悄失败),
    此时回合表停在旧日期, 所有基于回合的分析(MFE/MAE、执行质量)就静默过时了。
    """
    from backend.models.repo._db import _fetchone
    t = await _fetchone("SELECT MAX(trade_date) d, COUNT(*) n FROM cfzy_biz_trades")
    r = await _fetchone("SELECT MAX(open_date) d, COUNT(*) n FROM cfzy_biz_trade_rounds")
    if not t or not t.get("n"):
        return True, "无成交单", "不适用"
    td, rd = _as_date(t["d"]), _as_date(r["d"]) if r else None
    if rd is None:
        return False, f"成交单到{td}, 回合表空", "回合表非空"
    return rd >= td, f"成交单{td} / 回合{rd}", "回合 ≥ 成交单"


# ══════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════

CHECKS: list[Check] = [
    # 定时任务健康
    Check("task_never_ran", "任务从未跑过", "任务", CRITICAL, _chk_task_never_ran),
    Check("task_overdue", "任务超期未跑", "任务", CRITICAL, _chk_task_overdue),
    Check("task_orphan", "孤儿任务(无handler)", "任务", INFO, _chk_task_orphan),
    Check("task_failing", "任务连续失败", "任务", WARN, _chk_task_failing),
    Check("task_disabled", "已停用任务清单", "任务", INFO, _chk_task_disabled),
    # 数据新鲜度
    Check("data_kline_cache", "全市场日线", "数据", CRITICAL, _chk_kline_cache),
    Check("data_kline_5m", "个股5分钟K线", "数据", CRITICAL, _chk_kline_5m),
    Check("data_index_5m", "指数5分钟K线(不可回补)", "数据", CRITICAL, _chk_index_kline_5m),
    Check("data_market_risk", "大盘风控状态", "数据", CRITICAL, _chk_market_risk),
    Check("data_model_winrate", "模型胜率表", "数据", WARN, _chk_model_winrate),
    Check("data_industry_map", "行业映射", "数据", WARN, _chk_industry_map),
    Check("data_holding_fwd", "持仓态前向分布", "数据", WARN, _chk_holding_state_fwd),
    Check("data_emotion", "情绪快照", "数据", WARN, _chk_emotion_snapshot),
    # 外部接口
    Check("api_sina_quote", "新浪实时行情", "接口", CRITICAL, _chk_sina_quote,
          only_trading_day=True),
    Check("api_sina_snapshot", "新浪全市场快照", "接口", CRITICAL, _chk_sina_market_snapshot),
    Check("api_sina_index5m", "新浪指数5分钟", "接口", CRITICAL, _chk_sina_index_5m),
    Check("api_outbound_ip", "出口IP(推送总闸)", "接口", CRITICAL, _chk_outbound_ip),
    Check("api_calendar", "交易日历库", "接口", CRITICAL, _chk_trading_calendar),
    # 业务规则
    Check("rule_signal_volume", "当日信号量", "规则", WARN, _chk_signal_volume,
          only_trading_day=True),
    Check("rule_eod_audit", "EOD复核有效性", "规则", WARN, _chk_eod_audit_effective),
    Check("rule_rounds_synced", "回合与成交单同步", "规则", WARN, _chk_rounds_synced),
]


# ══════════════════════════════════════════════════════════════════
# 执行与报告
# ══════════════════════════════════════════════════════════════════

async def run_checks() -> list[CheckResult]:
    """逐项执行。单项异常隔离为该项失败, 不拖垮整轮 —— 检查器自己坏了必须能被看见。"""
    from backend.core.trading_calendar import is_workday
    trading = is_workday()
    out: list[CheckResult] = []
    for c in CHECKS:
        if c.only_trading_day and not trading:
            out.append(CheckResult(c.key, c.name, c.category, INFO, True,
                                   "非交易日跳过", "仅交易日适用"))
            continue
        try:
            r = await asyncio.wait_for(c.fn(), timeout=60)
            ok, actual, expected = r[0], r[1], r[2]
            detail = r[3] if len(r) > 3 else ""
            out.append(CheckResult(c.key, c.name, c.category, c.severity,
                                   bool(ok), str(actual), str(expected), str(detail)))
        except Exception as e:
            out.append(CheckResult(c.key, c.name, c.category, c.severity, False,
                                   f"检查项自身异常: {type(e).__name__}", "正常执行",
                                   str(e)[:300]))
    return out


def _lamp_state(r: CheckResult) -> str:
    """检查项 → 灯串状态。通过=绿; 失败按严重度分 红/黄。"""
    if r.ok:
        return "ok"
    return "bad" if r.severity == CRITICAL else "warn"


def build_report_card(results: list[CheckResult], heartbeat: dict):
    """按《观潮推送设计基线 v1.1》构体检卡 → (Card, 是否有严重问题)。

    遵循基线的四条硬约束(首版都违反了, 已改正):
      · 家族=system(grey header) + 标题 emoji ⚙️ —— 体检属"系统"族(与EOD复核/数据源同族),
        不是风险族; 首版误用 risk/intel 的橙蓝色, 会和真正的行情风险卡抢注意力。
      · 五区骨架里**行动建议区必有**: 👉 ≤20字动词开头。首版整卡没有建议行。
      · 系统卡的家族图形 = **灯串 + 异常项列表**, 首版只有列表没有灯串。
      · 正文(0~3区)≤10 行: 异常项超 6 条时只列前 6, 其余进折叠区计数。
    """
    from backend.services import card_kit
    from backend.services.lark_notifier import collapsible_element, md_element

    failed = [r for r in results if not r.ok]
    crit = [r for r in failed if r.severity == CRITICAL]
    failed.sort(key=lambda r: _SEV_ORDER.get(r.severity, 9))

    if not failed:
        title = f"⚙️ 系统体检 · {len(results)}项全通过"
    elif crit:
        title = f"⚙️ 系统体检 · {len(crit)}项故障 / 共{len(failed)}项异常"
    else:
        title = f"⚙️ 系统体检 · {len(failed)}项异常"

    elements: list = []
    fb: list[str] = [title]

    # 1 结论区: 灯串(系统卡家族图形)
    lamp = card_kit.light_string([(_lamp_state(r), r.name) for r in results])
    elements.append(card_kit.heading_md(lamp))
    fb.append(lamp)

    # 2 数据区: 异常项(正文最多 6 条, 防超 10 行)
    _MAX = 6
    for r in failed[:_MAX]:
        line = f"{_SEV_ICON.get(r.severity, '')} **{r.name}** · 实际 {r.actual} · 期望 {r.expected}"
        elements.append(md_element(line))
        fb.append(line)
    if len(failed) > _MAX:
        more = f"<font color='grey'>…另有 {len(failed) - _MAX} 项异常, 见折叠详情</font>"
        elements.append(md_element(more))
        fb.append(f"…另有 {len(failed) - _MAX} 项异常")

    # 3 行动建议区(基线: 必有, ≤20字动词开头)
    if crit:
        adv = f"先查{crit[0].name}"
    elif failed:
        adv = "抽空核对异常项"
    else:
        adv = "无需处理"
    elements.append(card_kit.advice(adv))
    fb.append(f"👉 {adv}")

    # 4 折叠详情: 元检查 + 心跳 + 超出正文的异常项 + 通过项名单
    det: list[str] = [f"通过 {len(results) - len(failed)}/{len(results)} 项 · 注册 {len(CHECKS)} 项"]
    if len(results) < len(CHECKS):
        det.append("⚠️ 执行项数少于注册项数, 有检查项自身未运行")
    lp = heartbeat.get("last_push_at")
    if lp:
        det.append(f"距上次成功推送 {(datetime.now() - lp).total_seconds() / 3600:.0f} 小时")
    streak = int(heartbeat.get("fail_streak") or 0)
    if streak:
        det.append(f"⚠️ 报告推送已连续失败 {streak} 次")
    for r in failed[_MAX:]:
        det.append(f"{_SEV_ICON.get(r.severity, '')} {r.name} · 实际 {r.actual}")
    for r in failed:
        if r.detail:
            det.append(f"{r.name}: {r.detail[:120]}")
    detail_md = "\n".join(det)
    elements.append(collapsible_element("体检明细", detail_md))
    fb.extend(det)

    card = card_kit.Card(
        title=title, elements=elements, fallback="\n".join(fb), family="system",
        summary=card_kit.summary_text(
            "系统体检", f"{len(results) - len(failed)}/{len(results)}通过",
            f"{len(crit)}项故障" if crit else ""),
        tags=[("故障", "red")] if crit else ([("异常", "orange")] if failed else [("正常", "grey")]),
    )
    return card, bool(crit)


async def run_health_report():
    """定时入口: 跑体检 → 落库 → 推报告 → 记心跳。"""
    from backend.models import repository
    from backend.models.repo import health_check as hc_repo

    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = await run_checks()
    try:
        await hc_repo.save_results(run_at, [r.as_row() for r in results])
    except Exception as e:
        logger.warning(f"[health] 结果落库失败(继续推送): {e}")

    hb = await hc_repo.get_heartbeat()
    ok = False
    try:
        from backend.services import notifier
        card, _has_crit = build_report_card(results, hb)
        ok = bool(await notifier.send_card(card))
    except Exception as e:
        logger.warning(f"[health] 报告推送失败: {e}")
    await hc_repo.mark_push(ok)

    try:
        await hc_repo.prune(60)
    except Exception:
        pass
    failed = [r for r in results if not r.ok]
    logger.info(f"[health] 体检完成: {len(results) - len(failed)}/{len(results)} 通过, "
                f"推送={'成功' if ok else '失败'}")
    return {"total": len(results), "failed": len(failed), "pushed": ok}
