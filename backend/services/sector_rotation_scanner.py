"""板块(题材)弱转强/强转弱预判 — 调度/IO 层。

三件套:
  1) scan_sector_rotation()    每3分钟: 拉涨停池→题材聚合→进程内当日时序→分类状态→
                                状态跃迁(启动/退潮)时推送, 并写盘中轮动快照供看板。
  2) predict_sector_next_day() 14:30:  theme_heat 多日序列 + 今日质地 → 次日预测 → 推送+落库。
  3) 看板/预测数据经 repository 落 cfzy_sys_sector_rotation, 前端 SectorRotationPanel 读。

纯判定逻辑在 sector_rotation.py(已单测), 本文件只做取数/状态机/推送编排。
复用涨停池(limit_pool, 已被 theme_heat 每5分钟在拉), 零新增高频外部请求。
推送阈值是启发式草案, 未回测; 先上线积累, 盘后对照再调参。
"""
import logging
from collections import defaultdict
from datetime import datetime

from backend.core.trading_calendar import is_workday
from backend.fetcher.limit_pool import get_limit_pool_cached
from backend.models import repository
from backend.services import alert_throttle, sector_rotation as sr
from backend.services.lark_notifier import md_element, table_element

logger = logging.getLogger(__name__)

# 进程内当日时序: {date: {theme: [涨停家数样本(升序)...]}}
_intraday: dict[str, dict[str, list[int]]] = {}
# 进程内当日各题材最近一次状态(用于跃迁判定): {date: {theme: state}}
_state: dict[str, dict[str, str]] = {}
# 当日已推送(防重): {date: set((theme, direction))}
_pushed: dict[str, set] = {}
# 当日转换流水(供看板头条·时间序累积): {date: [{at, direction, theme, ...}]}
# 与 _pushed 同步去重: 每题材每方向当日只记一条。进程内累积, 重启丢当日早段(同 _intraday 限制)。
_transitions: dict[str, list[dict]] = {}
# 昨日基准(上一交易日各题材最终涨停家数, 日基准口径用), 按日缓存一次: {date: {theme: 昨日涨停}}
_yest_baseline: dict[str, dict[str, int]] = {}


async def _load_yest_baseline(today: str) -> dict[str, int]:
    """取上一交易日各题材最终涨停家数(theme_heat 中最近一个 < today 的交易日)。按日缓存。

    theme_heat.trade_date 存紧凑格式(如 '20260630'), 而 today 传的是带连字符的
    '2026-07-01'。直接字符串比会因 '-'(0x2D) 小于数字而把紧凑日恒判为「大于」today,
    导致 prior 全空 → 昨日基准恒为 0 → 全题材误显示「昨0→今X」误报弱转强。
    故比较前统一去连字符归一(与 auction_sector_strength._yesterday_top_themes 同款)。
    """
    if today in _yest_baseline:
        return _yest_baseline[today]
    base: dict[str, int] = {}

    def _d(v) -> str:
        return str(v).replace("-", "")

    try:
        rows = await repository.get_theme_heat(8)
        today_c = _d(today)
        prior = sorted({_d(r["trade_date"]) for r in (rows or []) if _d(r["trade_date"]) < today_c})
        if prior:
            yd = prior[-1]
            for r in rows:
                if _d(r["trade_date"]) == yd:
                    base[r["theme"]] = int(r.get("limit_up_count") or 0)
    except Exception as e:
        logger.warning(f"[sector_rotation] 昨日基准取数失败: {e}")
    _yest_baseline[today] = base
    return base

# 看板里展示的题材上限(按状态优先级 + 涨停家数排)
_BOARD_MAX = 40
_STATE_ORDER = {"启动": 0, "高潮": 1, "升温": 2, "退潮": 3, "冷": 4}


def _scan_window(now: datetime) -> bool:
    """工作日 09:30~15:10(含收盘后定版一档)。"""
    if not is_workday(now):
        return False
    return "09:30" <= now.strftime("%H:%M") <= "15:10"


def _reset_if_new_day(date: str) -> None:
    """切日清理进程内旧状态(只保留当天, 防内存累积)。"""
    for d in (_intraday, _state, _pushed, _transitions, _yest_baseline):
        for k in list(d.keys()):
            if k != date:
                del d[k]
    _seeded.intersection_update({date})


# 已从DB回读补种的日期(每日只回读一次)
_seeded: set[str] = set()


async def _seed_from_db(date: str, pushed: set, trans_log: list) -> None:
    """重启后回读当日快照里的转换流水, 补种去重集合。

    去重集合 _pushed 原本只在进程内存, 部署重启即丢 → 同一「弱转强启动」当日重推
    (0703实况: 机器人题材 13:41/13:58/14:04 三连推, 对应三次部署重启)。快照本就
    每3分钟把当日 transitions 落库, 重启后第一轮回读补种, 即可重启安全。"""
    if date in _seeded:
        return
    _seeded.add(date)
    try:
        row = await repository.get_sector_rotation(date)
        stored = ((row or {}).get("rotation_data") or {}).get("transitions") or []
    except Exception as e:
        logger.warning(f"[sector_rotation] 回读当日转换流水失败(按无历史继续): {e}")
        return
    for t in reversed(stored):   # 库里存倒序, 回放成升序
        theme, direction = t.get("theme"), t.get("direction")
        if theme and direction and (theme, direction) not in pushed:
            pushed.add((theme, direction))
            trans_log.append(t)


async def scan_sector_rotation() -> None:
    now = datetime.now()
    if not _scan_window(now):
        return
    date = now.strftime("%Y-%m-%d")
    date_compact = now.strftime("%Y%m%d")
    _reset_if_new_day(date)

    pool = await get_limit_pool_cached(date_compact)
    boards = (pool or {}).get("boards") or []
    if not boards:
        return
    agg = sr.aggregate_themes(boards)
    if not agg:
        return

    series_map = _intraday.setdefault(date, defaultdict(list))
    state_map = _state.setdefault(date, {})
    pushed = _pushed.setdefault(date, set())
    trans_log = _transitions.setdefault(date, [])
    await _seed_from_db(date, pushed, trans_log)   # 重启后补种当日已推流水, 防重推

    # 日基准口径(v1.7.x): 拿昨日各题材涨停家数当基准比今日盘中; 强转弱仅下午判(防早盘未封满)
    yest_by_theme = await _load_yest_baseline(date)
    is_afternoon = now.strftime("%H:%M") >= "13:00"

    board_items: list[dict] = []
    transitions: list[tuple[str, str, dict]] = []   # (direction, theme, metrics)

    for theme, m in agg.items():
        series = series_map[theme]
        series.append(m["limit_up"])
        texture = {"max_height": m["max_height"], "broken": m["broken"],
                   "first_board": m["first_board"]}
        yest = yest_by_theme.get(theme, 0)
        state = sr.classify_daily(yest, m["limit_up"], texture, is_afternoon)
        prev = state_map.get(theme)
        state_map[theme] = state

        direction = sr.detect_transition(prev, state)
        # 弱转强失败跟踪: 早先已广播「启动」, 现回落到 退潮/冷 → 补一条失败提醒(每题材每日一次)
        if (direction is None and state in ("退潮", "冷")
                and (theme, "weak_to_strong") in pushed
                and (theme, "wts_failed") not in pushed):
            direction = "wts_failed"
        if direction and (theme, direction) not in pushed:
            pushed.add((theme, direction))
            slope = sr.compute_slope(list(series))
            peak = max(series) if series else m["limit_up"]
            transitions.append((direction, theme,
                                {**m, "slope": slope, "yest": yest, "peak": peak}))
            # 记入当日转换流水(供看板头条), 带触发时刻
            trans_log.append({
                "at": now.strftime("%H:%M"), "direction": direction, "theme": theme,
                "limit_up": m["limit_up"], "yest": yest, "slope": slope, "peak": peak,
                "max_height": m["max_height"], "broken": m["broken"],
                "samples": m["samples"][:3],
            })

        board_items.append({
            "theme": theme, "state": state,
            "limit_up": m["limit_up"], "yest": yest, "slope": sr.compute_slope(list(series)),
            "max_height": m["max_height"], "broken": m["broken"],
            "first_board": m["first_board"], "samples": m["samples"],
        })

    # 看板快照: 按状态优先级 + 涨停家数排序, 限量
    board_items.sort(key=lambda x: (_STATE_ORDER.get(x["state"], 9), -x["limit_up"]))
    snapshot = {
        "computed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "items": board_items[:_BOARD_MAX],
        "transitions": list(reversed(trans_log)),   # 时间倒序: 最新转换在前
    }
    try:
        await repository.upsert_sector_rotation(date, snapshot)
    except Exception as e:
        logger.warning(f"[sector_rotation] 写轮动快照失败: {e}")

    # 推送状态跃迁
    #   弱转强(启动): 全市场广播, 照旧。
    #   强转弱(退潮): 合并自原 detect_sector_ebb — 只推用户持仓踩该题材的, 不再全市场广播。
    holds: list[dict] | None = None   # 懒取: 只在出现强转弱时取一次持仓
    for direction, theme, m in transitions:
        if direction == "strong_to_weak":
            if holds is None:
                try:
                    holds = [s for s in (await repository.list_all_stocks())
                             if s.get("status") == "hold"]
                except Exception:
                    holds = []
            matched = [s for s in holds if theme in (s.get("concepts") or "")]
            if not matched:                       # 没踩线持仓, 与你无关, 不推
                continue
            hold_names = "、".join(f"{s['name']}({s['code']})" for s in matched[:10])
            try:
                await alert_throttle.enqueue("SECTOR_STRONG_TO_WEAK", {
                    "theme": theme, "limit_up": m["limit_up"], "yest": m.get("yest", 0),
                    "slope": m.get("slope", 0),
                    "max_height": m["max_height"], "broken": m["broken"],
                    "samples": m["samples"], "holds": hold_names,
                })
            except Exception as e:
                logger.warning(f"[sector_rotation] {theme} {direction} 推送入队失败: {e}")
        elif direction == "wts_failed":
            # 弱转强失败: 早先广播过启动, 现回落 — 补一条失败提醒(同样全市场广播)
            try:
                await alert_throttle.enqueue("SECTOR_WTS_FAILED", {
                    "theme": theme, "limit_up": m["limit_up"], "yest": m.get("yest", 0),
                    "peak": m.get("peak", m["limit_up"]),
                    "broken": m["broken"], "samples": m["samples"],
                })
            except Exception as e:
                logger.warning(f"[sector_rotation] {theme} {direction} 推送入队失败: {e}")
        else:
            try:
                await alert_throttle.enqueue("SECTOR_WEAK_TO_STRONG", {
                    "theme": theme, "limit_up": m["limit_up"], "yest": m.get("yest", 0),
                    "slope": m.get("slope", 0),
                    "max_height": m["max_height"], "broken": m["broken"],
                    "samples": m["samples"],
                })
            except Exception as e:
                logger.warning(f"[sector_rotation] {theme} {direction} 推送入队失败: {e}")
    if transitions:
        logger.info(f"[sector_rotation] {date} 状态跃迁 {len(transitions)} 个已入队")


# ── 推送合并 + 飞书表格卡 ──
def _merge_weak_to_strong(items: list[dict]) -> str:
    lines = ["🟢 板块弱转强·启动预警\n"]
    for a in items:
        lines.append(f"▸ [{a['theme']}] 涨停 昨{a.get('yest', 0)}→今{a['limit_up']}家 "
                     f"最高{a['max_height']}板 — {('、'.join(a.get('samples', [])[:3]))}")
    lines.append("\n昨日冷、今日涨停家数较昨明显抬升, 关注是否成当日主线。")
    return "\n".join(lines)


def _merge_strong_to_weak(items: list[dict]) -> str:
    lines = ["🔴 板块强转弱·退潮预警\n"]
    for a in items:
        lines.append(f"▸ [{a['theme']}] 涨停 昨{a.get('yest', 0)}→今{a['limit_up']}家 炸板{a['broken']}只 — "
                     f"{('、'.join(a.get('samples', [])[:3]))}")
        if a.get("holds"):
            lines.append(f"  └ 你持仓踩此线: {a['holds']} — 龙头转弱整板抽血, 沿5日线飘到头就减。")
    lines.append("\n昨日热、今日涨停较昨腰斩, 踩线持仓注意逢高减。")
    return "\n".join(lines)


def _build_rotation_card(items: list[dict], title: str, dir_label: str):
    cols = [
        {"name": "theme", "display_name": "题材", "data_type": "text", "width": "30%"},
        {"name": "lu", "display_name": "昨→今涨停", "data_type": "text", "width": "22%"},
        {"name": "trend", "display_name": dir_label, "data_type": "text", "width": "14%"},
        {"name": "rep", "display_name": "代表股", "data_type": "text", "width": "34%"},
    ]
    rows = []
    for a in items:
        trend = f"{a['max_height']}板" if dir_label == "质地" else f"炸{a['broken']}"
        rows.append({
            "theme": a["theme"], "lu": f"昨{a.get('yest', 0)}→今{a['limit_up']}家",
            "trend": trend, "rep": "、".join(a.get("samples", [])[:3]),
        })
    return title, [table_element(cols, rows, page_size=10)]


def _build_weak_to_strong_card(items: list[dict]):
    return _build_rotation_card(items, "🟢 板块弱转强·启动", "质地")


def _build_strong_to_weak_card(items: list[dict]):
    """强转弱合并自板块退潮: 只推持仓踩线题材, 表里多一列「你持仓踩此线」。"""
    cols = [
        {"name": "theme", "display_name": "题材", "data_type": "text", "width": "20%"},
        {"name": "lu", "display_name": "昨→今涨停", "data_type": "text", "width": "20%"},
        {"name": "trend", "display_name": "炸板", "data_type": "text", "width": "12%"},
        {"name": "rep", "display_name": "代表股", "data_type": "text", "width": "24%"},
        {"name": "holds", "display_name": "你持仓踩此线", "data_type": "text", "width": "24%"},
    ]
    rows = []
    for a in items:
        rows.append({
            "theme": a["theme"], "lu": f"昨{a.get('yest', 0)}→今{a['limit_up']}家",
            "trend": f"炸{a['broken']}", "rep": "、".join(a.get("samples", [])[:3]),
            "holds": a.get("holds", ""),
        })
    return "🔴 板块强转弱·退潮", [table_element(cols, rows, page_size=10)]


def _merge_wts_failed(items: list[dict]) -> str:
    lines = ["⚠️ 板块弱转强·失败\n"]
    for a in items:
        lines.append(f"▸ [{a['theme']}] 早先弱转强启动, 现涨停回落: "
                     f"峰值{a.get('peak', 0)}家→现{a['limit_up']}家(昨{a.get('yest', 0)}) — "
                     f"{('、'.join(a.get('samples', [])[:3]))}")
    lines.append("\n启动没接住, 追进的注意风控。")
    return "\n".join(lines)


def _build_wts_failed_card(items: list[dict]):
    cols = [
        {"name": "theme", "display_name": "题材", "data_type": "text", "width": "26%"},
        {"name": "peak", "display_name": "峰值→现在", "data_type": "text", "width": "24%"},
        {"name": "yest", "display_name": "昨日", "data_type": "text", "width": "14%"},
        {"name": "rep", "display_name": "代表股", "data_type": "text", "width": "36%"},
    ]
    rows = [{"theme": a["theme"],
             "peak": f"{a.get('peak', 0)}家→{a['limit_up']}家",
             "yest": f"{a.get('yest', 0)}家",
             "rep": "、".join(a.get("samples", [])[:3])} for a in items]
    return "⚠️ 板块弱转强·失败", [table_element(cols, rows, page_size=10)]


alert_throttle.register("SECTOR_WEAK_TO_STRONG", _merge_weak_to_strong,
                        lark_card_builder=_build_weak_to_strong_card)
alert_throttle.register("SECTOR_STRONG_TO_WEAK", _merge_strong_to_weak,
                        lark_card_builder=_build_strong_to_weak_card)
alert_throttle.register("SECTOR_WTS_FAILED", _merge_wts_failed,
                        lark_card_builder=_build_wts_failed_card)


# ── 14:30 收盘前次日预测 ──
async def predict_sector_next_day(return_only: bool = False):
    """收盘前次日板块预测。return_only=True 只返回 (文本, elements) 不推送(供 14:40 尾盘决策合并卡)。"""
    now = datetime.now()
    if not is_workday(now):
        return
    date = now.strftime("%Y-%m-%d")
    date_compact = now.strftime("%Y%m%d")

    rows = await repository.get_theme_heat(8)
    if not rows:
        logger.info("[sector_predict] theme_heat 无数据, 跳过")
        return
    dates = sorted({str(r["trade_date"]) for r in rows})
    by_theme: dict[str, dict[str, int]] = defaultdict(dict)
    samples_map: dict[str, str] = {}
    for r in rows:
        by_theme[r["theme"]][str(r["trade_date"])] = int(r["limit_up_count"] or 0)
        if str(r["trade_date"]) == dates[-1]:
            samples_map[r["theme"]] = r.get("sample_codes") or ""

    # 今日质地(判强势/终结): 拉一次涨停池
    pool = await get_limit_pool_cached(date_compact)
    agg = sr.aggregate_themes((pool or {}).get("boards") or [])

    groups: dict[str, list[dict]] = {"弱转强候选": [], "强转弱候选": [],
                                     "强势延续": [], "疑似终结": []}
    for theme, series_by_date in by_theme.items():
        daily_series = [series_by_date.get(d, 0) for d in dates]
        tm = agg.get(theme, {})
        pred = sr.predict_next_day(daily_series, tm)
        direction = pred["direction"]
        if direction not in groups:
            continue
        traj = "→".join(str(v) for v in daily_series[-6:])
        groups[direction].append({
            "theme": theme, "reason": pred["reason"], "traj": traj,
            "today": daily_series[-1], "samples": samples_map.get(theme, ""),
        })

    if not any(groups.values()):
        logger.info("[sector_predict] 无可预测题材, 跳过推送")
        return

    payload = {"computed_at": now.strftime("%Y-%m-%d %H:%M:%S"), "groups": groups}
    try:
        await repository.upsert_sector_prediction(date, payload)
    except Exception as e:
        logger.warning(f"[sector_predict] 写预测失败: {e}")

    if return_only:
        return await _push_prediction(groups, return_only=True)
    await _push_prediction(groups)
    logger.info(f"[sector_predict] {date} 次日预测已出: "
                + " ".join(f"{k}{len(v)}" for k, v in groups.items() if v))


_PRED_ICON = {"弱转强候选": "🟢", "强转弱候选": "🔴", "强势延续": "⬆️", "疑似终结": "⚰️"}


_TABLE_CAP = {"弱转强候选": 10, "强转弱候选": 10, "强势延续": 8}  # 可操作组才上表, 各自封顶
_ENDED_EXAMPLES = 6  # 疑似终结只举几个例子, 不堆全量


async def _push_prediction(groups: dict[str, list[dict]], return_only: bool = False):
    n = {k: len(groups.get(k) or []) for k in _PRED_ICON}
    # ── 顶部计数概览(一眼看清四类各多少) ──
    overview = "　·　".join(f"{_PRED_ICON[k]}{k.replace('候选', '')} {n[k]}" for k in _PRED_ICON)
    text_lines = ["📅 次日板块预测(收盘前 · 启发式未回测)", overview, ""]
    elements = [
        md_element("**📅 次日板块预测**　_收盘前启发式预判, 未回测, 仅供布局参考_"),
        md_element(overview),
    ]
    cols = [
        {"name": "theme", "display_name": "题材", "data_type": "text", "width": "26%"},
        {"name": "traj", "display_name": "近期轨迹", "data_type": "text", "width": "30%"},
        {"name": "reason", "display_name": "理由", "data_type": "text", "width": "44%"},
    ]
    # ── 可操作三组上表(封顶, 多余只标计数) ──
    for direction in ("弱转强候选", "强转弱候选", "强势延续"):
        gitems = groups.get(direction) or []
        if not gitems:
            continue
        icon = _PRED_ICON[direction]
        cap = _TABLE_CAP[direction]
        shown = gitems[:cap]
        more = f"　等共 {len(gitems)} 个" if len(gitems) > cap else ""
        text_lines.append(f"{icon} {direction}: "
                          + "、".join(a["theme"] for a in shown) + more)
        elements.append(md_element(f"{icon} **{direction}**（{len(gitems)}）"))
        rows = [{"theme": a["theme"], "traj": a["traj"], "reason": a["reason"]} for a in shown]
        elements.append(table_element(cols, rows, page_size=10))
    # ── 疑似终结: 数量+几个例子, 折叠不展开(沉寂题材, 无操作价值) ──
    ended = groups.get("疑似终结") or []
    if ended:
        icon = _PRED_ICON["疑似终结"]
        ex = "、".join(a["theme"] for a in ended[:_ENDED_EXAMPLES])
        tail = " 等" if len(ended) > _ENDED_EXAMPLES else ""
        line = f"{icon} 疑似终结 {len(ended)} 个（已沉寂, 不展开）：{ex}{tail}"
        text_lines.append("")
        text_lines.append(line)
        elements.append(md_element(line))
    if return_only:
        return "\n".join(text_lines), elements
    try:
        from backend.services import notifier
        await notifier.send_dual_card("\n".join(text_lines),
                                      lark_title="📅 次日板块预测", elements=elements)
    except Exception as e:
        logger.warning(f"[sector_predict] 推送失败: {e}")
