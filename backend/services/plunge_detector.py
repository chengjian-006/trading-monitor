import asyncio
import logging
from datetime import datetime, date

from backend.core.config import load_config
from backend.core.websocket import ws_manager
from backend.models import repository
from backend.services import notifier
from backend.services.signal_engine import get_merged_config
from backend.services.ai_analyst import get_index_trends, get_market_stats

logger = logging.getLogger(__name__)

_sent_today: dict[str, set] = {}
_sent_date: date | None = None
_prev_limit_down: int | None = None
_prev_limit_down_time: str | None = None

# 合并推送哨兵: 同一交易日「大盘急跌」整体只推一次(三个角度仍各自落库)
_PLUNGE_PUSH_KEY = "PLUNGE_MERGED_PUSH"

# 全局(用户无关)对外推送哨兵: 飞书/PushPlus 已统一为单一全局机器人, 大盘急跌内容对所有人相同,
# 故对外只发一条; 站内 WebSocket 仍按用户各推。防多用户共用一个机器人导致的扇出重复(同一轮 N 个
# push_enabled 用户各推一条一模一样的)。进程级内存哨兵(重启丢失也无妨——重启后同角度 DB 去重会让
# newly_saved 为空, 不会重推; 真有新角度才再发, 也合理)。
_PLUNGE_EXTERNAL_KEY = "PLUNGE_EXTERNAL_PUSH"


def _reset_daily():
    global _sent_today, _sent_date, _prev_limit_down, _prev_limit_down_time
    today = date.today()
    if _sent_date != today:
        _sent_today = {}
        _sent_date = today
        _prev_limit_down = None
        _prev_limit_down_time = None


from backend.core.trading_calendar import is_trading_time as _is_trading_time  # v1.7.x 统一来源


def _dedup_key(user_id: int, rule_id: str) -> str:
    return f"{user_id}:{rule_id}"


def _already_sent(user_id: int, rule_id: str) -> bool:
    key = _dedup_key(user_id, rule_id)
    return key in _sent_today.get(rule_id, set())


def _mark_sent(user_id: int, rule_id: str):
    if rule_id not in _sent_today:
        _sent_today[rule_id] = set()
    _sent_today[rule_id].add(_dedup_key(user_id, rule_id))


def _global_already_sent(key: str) -> bool:
    """用户无关的当日哨兵: 用于对外(飞书/PushPlus)全局只发一次。"""
    return key in _sent_today.get(key, set())


def _global_mark_sent(key: str):
    _sent_today.setdefault(key, set()).add(key)


async def detect_plunge():
    if not _is_trading_time():
        return

    _reset_daily()

    # 两个都是同步阻塞函数(串行curl 7个指数+重试sleep, 被封时单轮可达几十秒),
    # 直接在30s任务里跑会冻结event loop饿死3s行情 → 卸线程池
    index_trends = await asyncio.to_thread(get_index_trends)
    market_stats = await asyncio.to_thread(get_market_stats)

    # 用今日情绪快照的精确涨停/跌停(同花顺官方)覆盖近似值, 让大盘急跌的家数/跌停判断更准
    try:
        snap = await repository.get_latest_emotion()
        if snap and snap.get("trade_date") == date.today().strftime("%Y-%m-%d") and isinstance(market_stats, dict):
            if snap.get("limit_up_count") is not None:
                market_stats["limit_up"] = snap["limit_up_count"]
            if snap.get("limit_down_count") is not None:
                market_stats["limit_down"] = snap["limit_down_count"]
    except Exception as e:
        logger.warning(f"[plunge] 情绪快照精确涨跌停覆盖失败: {e}")

    sh_data = index_trends.get("sh000001", {})
    sh_trends = sh_data.get("trends", [])
    sh_pre_close = sh_data.get("pre_close", 0)
    sh_name = sh_data.get("name", "上证指数")

    current_price = sh_trends[-1]["price"] if sh_trends else 0
    if not current_price or not sh_pre_close:
        return

    users = await repository.list_users()
    if not users:
        return

    for user in users:
        user_id = user["id"]
        if not user.get("push_enabled", 1):
            continue

        user_cfg = await repository.get_signal_config(user_id)
        cfg = get_merged_config(user_cfg)

        alerts = []

        alert = _worst_index_drop(cfg, index_trends)
        if alert:
            alerts.append(alert)

        alert = _check_breadth(cfg, market_stats)
        if alert:
            alerts.append(alert)

        alert = _check_speed(cfg, market_stats)
        if alert:
            alerts.append(alert)

        # v1.7.x 方案A: 三个角度仍各自落库(矩阵/历史保持三行粒度),
        # 但盘中跳水一次最多推一条「大盘急跌」, 避免指数急跌/家数恶化/跌停加速三连推。
        newly_saved = []   # [(signal_name, detail_parts), ...] 本轮新落库、尚未推过的角度
        for rule_id, signal_name, detail_parts in alerts:
            # 内存级去重(快速短路)
            if _already_sent(user_id, rule_id):
                continue
            # 数据库级去重(主权威,防进程重启丢失内存状态)
            try:
                if await repository.signal_already_sent_today("000001", rule_id, user_id):
                    _mark_sent(user_id, rule_id)   # 同步内存,避免本次循环后续误推
                    continue
            except Exception as e:
                # v1.7.22: DB 去重查询失败 → 保守跳过本轮,避免 DB 抽风时反复推
                logger.error(f"Plunge DB 去重查询失败,本轮跳过 ({rule_id}): {e}")
                continue

            detail = "|".join(detail_parts)

            # v1.7.22: 先写库 — 写库失败则不计入推送(否则重启会重推)
            try:
                from backend.services import signal_specs
                await repository.save_signal(
                    code="000001", name=sh_name,
                    signal_id=rule_id, signal_name=signal_name,
                    direction="plunge",
                    price=current_price, detail=detail,
                    user_id=user_id,
                    signal_group=signal_specs.group_of(rule_id),
                )
            except Exception as e:
                logger.error(f"Plunge save_signal 失败,跳过推送避免重启重推 ({rule_id}): {e}")
                continue
            _mark_sent(user_id, rule_id)
            newly_saved.append((signal_name, detail_parts))

        # 合并推送: 本轮有新角度落库, 且今日尚未推过「大盘急跌」, 才推一条。
        # 用内存哨兵 _PLUNGE_PUSH_KEY 控制"一日一推"; 进程重启后哨兵丢失也无妨——
        # 各角度的 DB 去重会让 newly_saved 为空, 自然不会重推。
        if newly_saved and not _already_sent(user_id, _PLUNGE_PUSH_KEY):
            _mark_sent(user_id, _PLUNGE_PUSH_KEY)
            merged_name = "大盘急跌"
            detail = " ;; ".join(
                f"{name}: {'|'.join(parts)}" for name, parts in newly_saved
            )

            signal_data = {
                "type": "signal",
                "code": "000001",
                "name": sh_name,
                "signal_id": "PLUNGE_INDEX",   # 前端展示归到 regime 组即可
                "signal_name": merged_name,
                "direction": "plunge",
                "price": current_price,
                "detail": detail,
                "time": datetime.now().strftime("%H:%M:%S"),
            }
            # 站内实时通知: 按用户各推(各自浏览器会话, 本就该一人一份)
            await ws_manager.send_to_user(user_id, signal_data)

            # 对外推送(飞书/PushPlus): 单一全局机器人 → 全交易日只发一条, 防多用户扇出重复。
            angles = "/".join(name for name, _ in newly_saved)
            if not _global_already_sent(_PLUNGE_EXTERNAL_KEY):
                _global_mark_sent(_PLUNGE_EXTERNAL_KEY)
                await notifier.send_wechat_signal(
                    code="000001", name=sh_name,
                    signal_name=merged_name,
                    direction="plunge",
                    price=current_price, detail=detail,
                    user_id=user_id,
                )
                logger.info(f"Plunge alert(merged): [{angles}] -> 对外推送(全局一次, 触发 user {user_id})")
            else:
                logger.info(f"Plunge alert(merged): [{angles}] -> user {user_id} 仅站内(对外当日已推)")


def _index_drop_pct(trends: list, window: int):
    """窗口内跌幅%(末价 vs window 根前的价). 数据不足/价格异常 → None."""
    if len(trends) < window + 1:
        return None
    recent = trends[-window:]
    p0 = recent[0]["price"]
    if p0 <= 0:
        return None
    return (trends[-1]["price"] - p0) / p0 * 100


# v1.7.387: 陈旧度判断上移 trading_calendar 作单一来源(get_index_trends 源头也用),
# 这里保留别名 — 检测端二道防线 + 既有测试导入名不变
from backend.core.trading_calendar import TREND_STALE_MIN as _TREND_STALE_MIN
from backend.core.trading_calendar import trading_minute as _trading_minute
from backend.core.trading_calendar import trends_stale as _trends_stale


def _worst_index_drop(cfg: dict, index_trends: dict, now_hhmm: str | None = None):
    """上证/创业板/科创里挑跌得最狠且破阈值的, 返回 (rule_id, signal_name, parts) 或 None."""
    sc = cfg.get("PLUNGE_INDEX", {})
    if not sc.get("enabled", True):
        return None
    if now_hhmm is None:
        now_hhmm = datetime.now().strftime("%H:%M")
    window = int(sc.get("time_window_min", 10))
    threshold = sc.get("drop_threshold_pct", 1.0)
    worst = None   # (drop_pct, name, total_drop)
    for code, name in (("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000688", "科创指数")):
        d = index_trends.get(code, {})
        trends = d.get("trends", [])
        pre_close = d.get("pre_close", 0)
        if _trends_stale(trends, now_hhmm):
            logger.warning(
                f"[plunge] {name} 分时末点{trends[-1].get('time')}距now({now_hhmm})超{_TREND_STALE_MIN}交易分钟, "
                f"疑似源冻结回放, 跳过急跌判定")
            continue
        drop = _index_drop_pct(trends, window)
        if drop is None or drop >= -threshold:
            continue
        cur = trends[-1]["price"]
        total = (cur - pre_close) / pre_close * 100 if pre_close > 0 else 0
        if worst is None or drop < worst[0]:
            worst = (drop, name, total)
    if worst is None:
        return None
    drop, name, total = worst
    return (
        "PLUNGE_INDEX", "指数急跌",
        [f"{name} {window}分钟内跌幅 {drop:.2f}%", f"日内总跌幅 {total:.2f}%"],
    )


def _check_breadth(cfg: dict, stats: dict):
    sc = cfg.get("PLUNGE_BREADTH", {})
    if not sc.get("enabled", True):
        return None
    if not stats:
        return None

    ratio_threshold = sc.get("down_up_ratio", 3.0)
    drop_gt3_threshold = sc.get("drop_gt3_pct", 25.0)

    up = stats.get("up_count", 0)
    down = stats.get("down_count", 0)

    # 竞价时段/源降级假象: 上涨0+下跌0(此时"跌停"常=全场) = 无数据, 不是极端恶化
    if up <= 0 and down <= 0:
        return None

    if up <= 0:
        ratio = 99.0
    else:
        ratio = down / up

    if ratio < ratio_threshold:
        return None

    total = up + down
    limit_down = stats.get("limit_down", 0)
    drop_gt3_est = limit_down * 3 if total == 0 else (limit_down * 5)
    drop_gt3_pct = drop_gt3_est / total * 100 if total > 0 else 0

    if drop_gt3_pct < drop_gt3_threshold and ratio < ratio_threshold * 1.5:
        return None

    return (
        "PLUNGE_BREADTH",
        "涨跌家数恶化",
        [
            f"下跌/上涨比 = {ratio:.1f} (阈值{ratio_threshold})",
            f"下跌{down}家 / 上涨{up}家",
            f"跌停{limit_down}家",
        ],
    )


def _check_speed(cfg: dict, stats: dict):
    global _prev_limit_down, _prev_limit_down_time
    sc = cfg.get("PLUNGE_SPEED", {})
    if not sc.get("enabled", True):
        return None
    if not stats:
        return None
    # 同涨跌家数0/0假象: 此时 limit_down=全场脏值, 既不判加速也不污染基线
    if stats.get("up_count", 0) <= 0 and stats.get("down_count", 0) <= 0:
        return None

    threshold = int(sc.get("new_limit_down", 8))
    window = int(sc.get("time_window_min", 5))

    current_ld = stats.get("limit_down", 0)
    now_str = datetime.now().strftime("%H:%M")

    if _prev_limit_down is None:
        _prev_limit_down = current_ld
        _prev_limit_down_time = now_str
        return None

    new_ld = current_ld - _prev_limit_down

    if _prev_limit_down_time:
        try:
            prev_t = datetime.strptime(_prev_limit_down_time, "%H:%M")
            now_t = datetime.strptime(now_str, "%H:%M")
            elapsed = (now_t - prev_t).total_seconds() / 60
        except ValueError:
            elapsed = window
    else:
        elapsed = window

    if elapsed >= window:
        _prev_limit_down = current_ld
        _prev_limit_down_time = now_str

        if new_ld >= threshold:
            return (
                "PLUNGE_SPEED",
                "跌停加速",
                [
                    f"{int(elapsed)}分钟内新增跌停{new_ld}家 (阈值{threshold})",
                    f"当前跌停共{current_ld}家",
                ],
            )

    return None
