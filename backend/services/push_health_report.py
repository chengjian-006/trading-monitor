"""推送健康度周报 (机制类, cron 周五 17:10) — 本周(近5个交易日)推送偏好动作统计灰卡。

数据源 = cfzy_biz_push_pref 按 kind/created_at 统计本周用户动作(静音族/关模型/已处理/
已卖出/到线提醒订阅)。系统当前没有"推送量"日志表(推送发出不落库), 故本报只统计"用户对
推送做了什么降噪动作", 不含推送总条数 —— 口径在卡内折叠区写明, 不为此新建推送日志表
(那是大工程留以后)。

内容: KPI 三栏(本周动作N次/最常关的模型/到线提醒生效订阅数) + 动作分布清单 +
👉 建议("XX被关最多, 去模型图鉴看战绩"式; 无明显集中则"本周降噪动作少, 推送节奏健康")。
数据不足一周也发(卡内注明口径起始日)。

防重复划界: 系统健康盘后汇总(system_health 21:00 灰卡)看"系统故障"、收盘复盘(19:00
蓝卡)看"信号胜率"、模型胜率重算(21:00)是数据任务 —— 本卡只看"推送↔用户交互健康度",
周频不日推, 不统计信号也不统计故障。

去重: guard_throttle 哨兵(code=SYS, rule=push_health_week), 当日已发不重发(重启安全)。
"""
import logging
from datetime import date

from backend.core.trading_calendar import is_workday
from backend.models.repo import guard_throttle as gt
from backend.services import card_kit, notifier
from backend.services import push_pref as pref_svc
from backend.services.lark_notifier import md_element
from backend.services.morning_focus import model_short
from backend.services.stop_escalation import recent_trading_days_desc

logger = logging.getLogger(__name__)

REPORT_DAYS = 5           # 统计窗口 = 近 5 个交易日(周中上线数据不足一周照发, 注明起始日)
MANY_ACTIONS = 10         # 动作总数 ≥ 此值且无集中点 → 提示整体降噪(不能说"节奏健康")

_DEDUP_CODE = "SYS"
_DEDUP_RULE = "push_health_week"


# ══════════════ 纯函数(可单测, 不连库) ══════════════

def summarize_actions(rows: list) -> dict:
    """push_pref 行(kind/target/created_at) → 周动作汇总。
    返回 {total, by_kind(降序), model_off(降序), top_model, ma_alert_new}。"""
    total = len(rows)
    by_kind: dict[str, int] = {}
    model_off: dict[str, int] = {}
    ma_new = 0
    for r in rows:
        k = str(r.get("kind") or "")
        by_kind[k] = by_kind.get(k, 0) + 1
        if k == "model_off":
            t = str(r.get("target") or "")
            if t:
                model_off[t] = model_off.get(t, 0) + 1
        if k in pref_svc.MA_ALERT_KINDS:
            ma_new += 1
    by_kind_sorted = sorted(by_kind.items(), key=lambda x: (-x[1], x[0]))
    model_off_sorted = sorted(model_off.items(), key=lambda x: (-x[1], x[0]))
    return {"total": total, "by_kind": by_kind_sorted, "model_off": model_off_sorted,
            "top_model": model_off_sorted[0] if model_off_sorted else None,
            "ma_alert_new": ma_new}


def pick_advice(stats: dict, name_map: dict) -> str:
    """👉 建议分支: 有集中被关的模型 → 点名去模型图鉴; 动作偏多无集中 → 提示整体降噪;
    否则 → 节奏健康。name_map: signal_id → 中文全名(展示禁代号)。"""
    top = stats.get("top_model")
    if top and top[1] >= 2:
        short = model_short(name_map.get(top[0], top[0]))
        return f"{short}被关最多，去模型图鉴看战绩"
    if stats.get("total", 0) >= MANY_ACTIONS:
        return "降噪动作偏多，考虑整体下调推送频率"
    return "本周降噪动作少，推送节奏健康"


def build_health_card(*, stats: dict, name_map: dict, active_ma_alerts: int,
                      start_date: str, trading_days_n: int):
    """周报 → 基线 v1.1 系统卡(grey): KPI三栏 → 动作分布全短列表 → 被关模型一行 →
    👉建议 → 折叠口径(起始日+无推送量日志说明)。无动作也构卡(注明口径)。"""
    total = stats["total"]
    top = stats.get("top_model")
    top_disp = model_short(name_map.get(top[0], top[0])) if top else "无"

    elements: list = [card_kit.kpi_row([
        ("本周动作", f"{total}次"),
        ("最常关模型", top_disp, "orange" if top else None),
        ("到线提醒生效", f"{active_ma_alerts}单"),
    ])]
    fb = ["⚙️ 推送健康度周报", "",
          f"本周动作 {total} 次 / 最常关模型 {top_disp} / 到线提醒生效 {active_ma_alerts} 单"]

    # 动作分布清单(全短列: 动作中文名 | 次数)
    if stats["by_kind"]:
        trows = [(pref_svc.KIND_LABEL.get(k, k), f"{n}次") for k, n in stats["by_kind"]]
        elements.append(card_kit.short_table(["动作", "次数"], trows))
        fb.append("")
        fb += [f"· {label} {n}" for label, n in trows]
    else:
        elements.append(md_element("本周没有任何降噪/订阅动作"))
        fb += ["", "本周没有任何降噪/订阅动作"]

    # 被关模型一行(中文名, 禁 signal_id 代号)
    if stats["model_off"]:
        offs = "、".join(f"{model_short(name_map.get(t, t))}×{n}"
                          for t, n in stats["model_off"])
        elements.append(md_element(f"🔕 被关的模型：{offs}"))
        fb.append(f"被关的模型：{offs}")

    advice_text = pick_advice(stats, name_map)
    elements.append(card_kit.advice(advice_text))
    fb += ["", f"👉 {advice_text}"]

    scope = (f"📐 口径：cfzy_biz_push_pref 表按动作创建时间统计，窗口 **{start_date}** 起"
             f"（近{trading_days_n}个交易日，数据不足一周照发）；到线提醒生效数=当前未撤销"
             f"未过期的订阅行；系统未建推送量日志，推送总条数不在本报口径（留待以后）。")
    elements.append(card_kit.fold("统计口径", scope))

    return card_kit.Card(
        title="⚙️ 推送健康度周报", elements=elements, fallback="\n".join(fb),
        family="system",
        summary=card_kit.summary_text(
            "推送健康周报", f"动作{total}次",
            f"最常关{top_disp}" if top else "节奏健康"),
        subtitle=f"{start_date} 起 · 近{trading_days_n}个交易日",
        tags=[("周报", "grey")])


# ══════════════ 取数(只读现成表, 不新建推送日志表) ══════════════

async def _model_names(signal_ids: list) -> dict:
    """signal_id → 最近一次落库的中文 signal_name(展示禁代号); 查不到回落原 id。"""
    if not signal_ids:
        return {}
    from backend.models.repo._db import _fetchall
    try:
        ph = ",".join(["%s"] * len(signal_ids))
        rows = await _fetchall(
            f"SELECT signal_id, MAX(signal_name) AS name FROM cfzy_biz_signals "
            f"WHERE signal_id IN ({ph}) GROUP BY signal_id", tuple(signal_ids))
        return {str(r["signal_id"]): str(r["name"]) for r in rows if r.get("name")}
    except Exception as e:
        logger.warning(f"[push_health] 查模型中文名失败: {e}")
        return {}


async def _collect() -> dict:
    """凑齐构卡入参: 近5个交易日的 push_pref 动作行 + 当前生效到线订阅数。"""
    from backend.models.repo import push_pref as pref_repo
    from backend.models.repo._db import _fetchall

    tdays = recent_trading_days_desc(REPORT_DAYS)
    start = tdays[-1] if tdays else date.today().isoformat()

    rows: list = []
    try:
        rows = await _fetchall(
            "SELECT kind, target, created_at FROM cfzy_biz_push_pref "
            "WHERE created_at >= %s", (f"{start} 00:00:00",))
    except Exception as e:
        logger.warning(f"[push_health] 查本周动作失败: {e}")
    stats = summarize_actions(rows)

    name_map = await _model_names([t for t, _ in stats["model_off"]])

    active_ma = 0
    try:
        active_ma = len(await pref_repo.active_prefs_of_kinds(list(pref_svc.MA_ALERT_KINDS)))
    except Exception as e:
        logger.warning(f"[push_health] 查到线订阅失败: {e}")

    return dict(stats=stats, name_map=name_map, active_ma_alerts=active_ma,
                start_date=start, trading_days_n=len(tdays))


# ══════════════ 编排(cron 周五 17:10) ══════════════

async def run_push_health_report() -> None:
    """周五 17:10: 推送健康度周报, 无动作也发(注明口径); 当日一次去重(重启不重发)。"""
    if not is_workday():
        return
    today = date.today().isoformat()
    try:
        if await gt.last_date(_DEDUP_CODE, _DEDUP_RULE) == today:
            logger.info("[push_health] 今日已发过, 跳过")
            return
    except Exception as e:
        logger.error(f"[push_health] 去重查询失败, 本轮跳过: {e}")
        return

    data = await _collect()
    card = build_health_card(**data)

    # 先标记再推(防推送成功后标记失败 → 重启重发)
    await gt.bump(today, _DEDUP_CODE, _DEDUP_RULE, None)
    try:
        await notifier.send_card(card)
        logger.info(f"[push_health] 周报已推: 动作{data['stats']['total']}次 "
                    f"到线订阅{data['active_ma_alerts']}单 (口径 {data['start_date']} 起)")
    except Exception as e:
        logger.warning(f"[push_health] 推送失败: {e}")
