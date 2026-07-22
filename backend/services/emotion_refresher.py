"""短线情绪温度刷新 — 短线盯盘 P1。

每 3 分钟 (交易日内) 采集一份市场情绪温度, 落 cfzy_sys_emotion_snapshot:
  封板率 / 炸板率 / 连板梯队 / 最高连板 / 昨涨停今日溢价 → 派生情绪阶段。

数据源降级链 (见 fetcher/limit_pool.py + 探测脚本):
  东财涨停池 → 同花顺涨停池 → 全市场涨跌停估算(只有家数, 无封板率/连板) → 跳过保留旧值。

情绪阶段阈值集中在本模块常量, 后续可迁移到配置页 (与 signal_engine_config 一致风格)。
冰点判据 = 最高连板 ≤ 3 (用户盘感校准 v1)。
"""
import logging
from datetime import datetime

from backend.core.trading_calendar import is_workday
from backend.fetcher.limit_pool import get_limit_pool_cached
from backend.models import repository

logger = logging.getLogger(__name__)

# ── 情绪阶段阈值 (待实盘迭代, 后续可移到配置) ──
ICE_MAX_BOARD = 3          # 冰点: 最高连板 ≤ 3
EBB_SEAL_RATE = 0.5        # 退潮: 封板率偏弱阈值 (< 0.5)
EBB_SEAL_DROP = 0.12       # 退潮: 封板率较前一档骤降 ≥ 12 个百分点(0.12)算骤降
CLIMAX_BOARD = 5           # 高潮: 最高连板 ≥ 5
CLIMAX_SEAL_RATE = 0.8
CLIMAX_LIMIT_UP = 60       # 高潮: 涨停家数 ≥ 60
START_BOARD = 4            # 启动: 最高连板 ≥ 4
START_SEAL_RATE = 0.7


def _derive_phase(limit_up: int | None, seal_rate: float | None,
                  highest_board: int | None, premium: float | None,
                  prev_seal_rate: float | None = None) -> str:
    """派生情绪阶段。优先级: 数据降级 > 退潮 > 高潮 > 启动 > 冰点 > 修复 > 中性。

    退潮采用联合条件(避免静态低封板率误报): 封板率偏弱(<EBB_SEAL_RATE) 且
      (昨涨停溢价转负  OR  封板率较前一档骤降≥EBB_SEAL_DROP)。
    """
    if highest_board is None and seal_rate is None:
        return "数据降级"
    # 退潮: 封板率偏弱 + (昨涨停溢价转负 或 封板率较前一档骤降)
    if seal_rate is not None and seal_rate < EBB_SEAL_RATE:
        premium_turned_negative = premium is not None and premium < 0
        seal_plunged = prev_seal_rate is not None and seal_rate <= prev_seal_rate - EBB_SEAL_DROP
        if premium_turned_negative or seal_plunged:
            return "退潮"
    # 高潮: 高连板 + 高封板率 + 涨停家数高位
    if (highest_board is not None and highest_board >= CLIMAX_BOARD
            and seal_rate is not None and seal_rate >= CLIMAX_SEAL_RATE
            and (limit_up or 0) >= CLIMAX_LIMIT_UP):
        return "高潮"
    # 启动: 连板抬升 + 封板率稳
    if (highest_board is not None and highest_board >= START_BOARD
            and seal_rate is not None and seal_rate >= START_SEAL_RATE):
        return "启动"
    # 冰点: 连板高度低 且 昨涨停溢价不正(或未知)
    if highest_board is not None and highest_board <= ICE_MAX_BOARD \
            and (premium is None or premium <= 0):
        return "冰点"
    # 修复: 昨涨停溢价转正
    if premium is not None and premium > 0:
        return "修复"
    return "中性"


# ── 情绪温度分 (0-100) + 四阶段 —— 短线快指标 (v1.7.x) ──
# 6 因子各归一到 0-100 再加权; 因子缺失(数据降级)时在可用因子上重新归一权重, 不因缺一项就无分。
# 归一区间取短线盘感常识值, 后续可挪配置页。
SCORE_WEIGHTS = {
    "premium": 0.20,   # 昨涨停今日溢价 (最领先: 打板赚不赚钱)
    "seal":    0.20,   # 封板率 (承接力)
    "board":   0.15,   # 最高连板 (空间高度)
    "count":   0.15,   # 涨停家数 (热度/广度)
    "zha":     0.15,   # 炸板率 (反向: 分歧/退潮前兆)
    "vol":     0.15,   # 量能 (放量/缩量 vs 昨日全天)
}
CYCLE_CLIMAX_SCORE = 65    # 温度分 ≥ → 高潮
CYCLE_ICE_SCORE = 30       # 温度分 ≤ → 冰点


def _clamp100(x: float) -> float:
    return 0.0 if x < 0 else (100.0 if x > 100 else x)


def _score_factors(premium, seal_rate, highest, limit_up, zha_rate, vol_ratio) -> dict:
    """各因子 → 0-100 子分 (缺失=None)。"""
    return {
        "premium": None if premium is None else _clamp100((premium + 4.0) / 8.0 * 100),      # -4%→0, +4%→100
        "seal":    None if seal_rate is None else _clamp100((seal_rate - 0.45) / 0.45 * 100),  # 45%→0, 90%→100
        "board":   None if highest is None else _clamp100((highest - 1) / 6.0 * 100),          # 1板→0, 7板→100
        "count":   None if limit_up is None else _clamp100(limit_up / 80.0 * 100),             # 0→0, 80→100
        "zha":     None if zha_rate is None else _clamp100((0.5 - zha_rate) / 0.5 * 100),      # 炸0%→100, 50%→0
        "vol":     None if vol_ratio is None else _clamp100((vol_ratio + 20.0) / 40.0 * 100),  # -20%→0, 0%→50, +20%→100
    }


def _emotion_score(premium, seal_rate, highest, limit_up, zha_rate, vol_ratio) -> int | None:
    """6 因子加权 → 0-100 情绪温度分。全缺则 None。"""
    subs = _score_factors(premium, seal_rate, highest, limit_up, zha_rate, vol_ratio)
    num = den = 0.0
    for k, w in SCORE_WEIGHTS.items():
        if subs[k] is not None:
            num += w * subs[k]
            den += w
    return None if den <= 0 else round(num / den)


def _derive_cycle(score: int | None, phase: str) -> str | None:
    """四阶段: 冰点 / 回暖 / 高潮 / 退潮。
    退潮沿用已调优的 _derive_phase 退潮判据(封板弱 + 溢价负/骤降), 优先级最高;
    其余按温度分分档 (高潮≥65 / 冰点≤30 / 之间=回暖)。"""
    if score is None:
        return None
    if phase == "退潮":
        return "退潮"
    if score >= CYCLE_CLIMAX_SCORE:
        return "高潮"
    if score <= CYCLE_ICE_SCORE:
        return "冰点"
    return "回暖"


# ── 情绪拐点提醒: 四阶段跨带即推市场级卡 (领先, 复用 notifier.send_dual_card) ──
# 只提醒对短线有动作意义的转场; 每日限次 + 「与上一档快照不同才推」双重防抖。
CYCLE_MAX_PUSHES = 4
_CYCLE_PUSH_COUNT: dict[str, int] = {}


def _cycle_alert_spec(prev: str, cur: str):
    """转场 → (标题, header色, 盘面一句, 建议, (彩签文,彩签色)); 无动作意义的转场返回 None。"""
    if cur == "回暖" and prev == "冰点":
        return ("🌡️ 情绪回暖 · 启动", "orange", "情绪自冰点回暖，赚钱效应回升",
                "可小仓试错、超跌反抽，别追高", ("回暖", "orange"))
    if cur == "高潮" and prev in ("回暖", "冰点"):
        return ("🔥 情绪进入高潮", "red", "封板率/连板/溢价齐升，情绪最热",
                "做龙头或低位连板，但防高潮见顶、冲高减", ("高潮", "red"))
    if cur == "退潮" and prev in ("高潮", "回暖"):
        return ("🌊 情绪退潮 · 减仓", "grey", "封板率转弱且溢价转负/骤降，赚钱效应变差",
                "减仓避险、只做低吸，别追板", ("退潮", "green"))
    return None


async def _maybe_push_cycle_alert(trade_date: str, prev_cycle: str | None,
                                  cur_cycle: str | None, score: int | None) -> None:
    """四阶段较上一档快照跨带 → 推一张市场级情绪拐点卡。失败只记日志。"""
    if not prev_cycle or not cur_cycle or prev_cycle == cur_cycle:
        return
    spec = _cycle_alert_spec(prev_cycle, cur_cycle)
    if not spec:
        return
    if _CYCLE_PUSH_COUNT.get(trade_date, 0) >= CYCLE_MAX_PUSHES:
        logger.info(f"[emotion] 情绪拐点 {prev_cycle}→{cur_cycle} 达当日限次, 跳过")
        return
    title, template, line, advice, tag = spec
    try:
        from backend.services import notifier
        from backend.services.card_kit import advice as _advice_el
        from backend.services.lark_notifier import md_element
        head = f"{prev_cycle}　→　{cur_cycle}"
        detail = f"情绪温度 {score}　·　{line}" if score is not None else line
        elements = [md_element(f"**{head}**"), md_element(detail), _advice_el(advice)]
        text = "\n".join([head, "", detail, "", f"👉 {advice}"])
        await notifier.send_dual_card(
            text, lark_title=title, elements=elements, template=template,
            summary=f"短线情绪 {head}" + (f" 温度{score}" if score is not None else ""),
            text_tags=[tag])
        _CYCLE_PUSH_COUNT[trade_date] = _CYCLE_PUSH_COUNT.get(trade_date, 0) + 1
        for d in [d for d in _CYCLE_PUSH_COUNT if d != trade_date]:
            _CYCLE_PUSH_COUNT.pop(d, None)   # 清非当日计数
        logger.info(f"[emotion] 情绪拐点卡已推 {prev_cycle}→{cur_cycle} 温度{score}")
    except Exception as e:
        logger.warning(f"[emotion] 情绪拐点卡推送失败({prev_cycle}→{cur_cycle}): {e}")


async def _two_market_amount() -> float | None:
    """当前两市(上证综指+深证成指)累计成交额(亿), 取自 market_overview a_indices(新浪源, 生产可达)。"""
    try:
        overview = await repository.get_market_overview()
    except Exception:
        overview = None
    total = 0.0
    got = False
    for idx in (overview or {}).get("a_indices") or []:
        if idx.get("name") in ("上证指数", "深证成指"):
            try:
                total += float(idx.get("amount") or 0)
                got = True
            except (TypeError, ValueError):
                continue
    return round(total, 1) if got and total > 0 else None


async def _compute_volume_ratio(trade_date: str, now: datetime, cur_amount: float | None) -> float | None:
    """量能: 今日「U型预测全天」两市额 vs 昨日全天两市额 → 放量/缩量 %(正=放量)。
    昨日全天取上一交易日最后一档快照的 market_amount; 首日/无历史 → None(温度分自动少算一项)。
    用 U 型系数反推全天(不线性外推), 与 /turnover 口径一致。"""
    if cur_amount is None or cur_amount <= 0:
        return None
    from backend.services.ai_analyst import _estimate_full_day_amount
    proj = _estimate_full_day_amount(cur_amount, now.strftime("%H:%M"))
    if not proj or proj <= 0:
        return None
    prev = await repository.get_last_emotion_before(trade_date)
    yest = prev.get("market_amount") if prev else None
    if not yest or yest <= 0:
        return None
    return round((proj - yest) / yest * 100, 1)


def _build_ladder(boards: list[dict]) -> tuple[list[dict], int | None]:
    """连板梯队 [{height, count}] (按高度降序) + 最高连板。"""
    if not boards:
        return [], None
    counts: dict[int, int] = {}
    for b in boards:
        h = int(b.get("height") or 1)
        counts[h] = counts.get(h, 0) + 1
    ladder = [{"height": h, "count": c} for h, c in sorted(counts.items(), reverse=True)]
    return ladder, max(counts)


def _connected_stocks(boards: list[dict]) -> list[dict]:
    """连板梯队详单: 只取 ≥2 板的票(首板不算连板且每天太多), 按连板高度降序、同档按涨幅降序。
    供前端「连板梯队」逐只列出(代码/名称/真连板/同花顺原始标签/题材/涨幅/炸板次数)。"""
    conn = [b for b in (boards or []) if int(b.get("height") or 1) >= 2]
    conn.sort(key=lambda b: (int(b.get("height") or 1), b.get("pct") or -999), reverse=True)
    return [{
        "code": b.get("code") or "",
        "name": b.get("name") or "",
        "height": int(b.get("height") or 1),
        "streak_label": b.get("streak_label") or "",   # 同花顺原始描述 "N天M板"/"N连板", 做个股标签
        "reason": b.get("reason") or "",
        "pct": b.get("pct"),
        "open_times": int(b.get("open_times") or 0),
    } for b in conn if b.get("code")]


# 真连板缓存: {(code, trade_date): 截至昨日连续涨停板数}; 当日内稳定, 每3min刷新只算一次/票
_YEST_STREAK_CACHE: dict[tuple[str, str], int | None] = {}


def _limit_of(code: str) -> float:
    """涨停幅: 创业板/科创板 20%, 其余 10% (忽略 ST/北交所, 近似)。"""
    return 0.20 if (code.startswith("30") or code.startswith("68")) else 0.10


async def _yest_consecutive(code: str, trade_date: str) -> int | None:
    """截至 trade_date 前一交易日的连续涨停板数(从日K线倒数)。失败返 None。当日缓存。"""
    key = (code, trade_date)
    if key in _YEST_STREAK_CACHE:
        return _YEST_STREAK_CACHE[key]
    val = None
    try:
        from backend import data_fetcher
        df = await data_fetcher.get_daily_kline(code, 30)
        if df is not None and not df.empty:
            df = df.sort_values("date").reset_index(drop=True)
            c = df["close"].tolist()
            dts = [str(x)[:10] for x in df["date"].tolist()]
            lim = _limit_of(code)
            cnt = 0
            for i in range(len(c) - 1, 0, -1):
                if dts[i] >= trade_date:      # 跳过今日及以后, 只数到昨日为止
                    continue
                pc = c[i - 1]
                if pc <= 0:
                    break
                if c[i] / pc - 1.0 >= lim - 0.005:
                    cnt += 1
                else:
                    break
            val = cnt
    except Exception as e:
        logger.warning(f"[emotion] {code} 连板数K线计算失败: {e!r}")
    _YEST_STREAK_CACHE[key] = val
    return val


async def _attach_real_streaks(boards: list[dict], trade_date: str) -> None:
    """把每只涨停股的 height 改为真连板数 = 截至昨日连续涨停 + 今日封板1(在涨停池=今日已封板)。
    首板(板数<2)直接=1 无需查K线; 其余按K线; K线失败回退到同花顺板数占位。"""
    for k in [k for k in _YEST_STREAK_CACHE if k[1] != trade_date]:
        _YEST_STREAK_CACHE.pop(k, None)   # 清掉非当日缓存
    import asyncio
    sem = asyncio.Semaphore(5)

    async def _one(b: dict) -> None:
        code = b.get("code") or ""
        board_count = int(b.get("height") or 1)   # limit_pool 暂存的板数 M
        if not code or board_count < 2:
            b["height"] = 1
            return
        async with sem:
            yest = await _yest_consecutive(code, trade_date)
        b["height"] = (yest + 1) if yest is not None else board_count

    await asyncio.gather(*[_one(b) for b in boards])


async def _compute_yest_premium(trade_date: str) -> float | None:
    """昨涨停今日溢价 = 上一交易日涨停股今日平均涨幅。

    取上一交易日快照存的涨停 codes (避免猜上一交易日, 抗节假日), 拉今日实时行情算均值。
    首次运行无历史 → None。
    """
    prev = await repository.get_last_emotion_before(trade_date)
    if not prev:
        return None
    codes = prev.get("limit_up_codes") or []
    if not codes:
        return None
    try:
        from backend import data_fetcher
        quotes = await data_fetcher.get_realtime_quotes(list(codes))
        pcts = [q["pct_change"] for q in quotes.values() if q.get("pct_change") is not None]
        if not pcts:
            return None
        return round(sum(pcts) / len(pcts), 2)
    except Exception as e:
        logger.warning(f"[emotion] 昨涨停溢价计算失败: {e!r}")
        return None


def _in_emotion_window(now: datetime) -> bool:
    """采集窗口: 工作日 09:25(集合竞价后)~15:10(收盘后一档)。
    避开盘前盘后空数据污染当日情绪曲线。"""
    if not is_workday(now):
        return False
    return "09:25" <= now.strftime("%H:%M") <= "15:10"


async def refresh_emotion_snapshot() -> None:
    now = datetime.now()
    if not _in_emotion_window(now):
        return  # 非交易日 / 非采集窗口不采集

    trade_date = now.strftime("%Y-%m-%d")
    pool = await get_limit_pool_cached(now.strftime("%Y%m%d"))

    # 上涨/下跌家数(宽度) — 走 get_market_stats(新浪源, 10min缓存, 与 market_overview 共享缓存),
    # 存入快照给情绪曲线"上涨/下跌"两条线; 也作降级时涨跌停兜底来源
    import asyncio
    from backend.services import ai_analyst
    stats = {}
    try:
        stats = await asyncio.get_event_loop().run_in_executor(None, ai_analyst.get_market_stats) or {}
    except Exception as e:
        logger.warning(f"[emotion] 涨跌家数取数失败: {e!r}")
    up_count = stats.get("up_count")
    down_count = stats.get("down_count")

    lu_history = ld_history = None
    if pool:
        boards = pool.get("boards") or []
        await _attach_real_streaks(boards, trade_date)   # height 改为真连板数(K线倒数), 覆盖同花顺"N天M板"误读
        ladder, highest = _build_ladder(boards)
        board_stocks = _connected_stocks(boards)
        lu = pool.get("limit_up_count")
        broken = pool.get("broken_board_count")
        # 封板率: 同花顺官方 seal_rate(0-1) 优先; 东财备源无 rate → 自算
        seal_rate = pool.get("seal_rate")
        if seal_rate is None and lu is not None and broken is not None and (lu + broken) > 0:
            seal_rate = round(lu / (lu + broken), 4)
        source = pool.get("source", "")
        limit_down = pool.get("limit_down_count")
        lu_history = pool.get("limit_up_history")
        ld_history = pool.get("limit_down_history")
        codes = pool.get("codes") or []
    else:
        # 降级: 涨停池两源皆失败 → 用 get_market_stats 的涨跌停近似 (只有家数, 无封板率/连板梯队)
        logger.info("[emotion] 涨停池两源皆失败, 降级到全市场涨跌停估算")
        ladder, highest, seal_rate, codes = [], None, None, []
        board_stocks = []
        broken = None
        lu = stats.get("limit_up")
        limit_down = stats.get("limit_down")
        source = "quote_estimate"
        if lu is None:
            logger.warning("[emotion] 无任何数据, 跳过本次保留旧值")
            return

    premium = await _compute_yest_premium(trade_date)
    # 取当日前一档快照的封板率(判退潮骤降) + 四阶段(判情绪拐点跨带)
    prev_seal = None
    prev_cycle = None
    try:
        prev_snap = await repository.get_latest_emotion()
        if prev_snap and prev_snap.get("trade_date") == trade_date:
            prev_seal = prev_snap.get("seal_rate")
            prev_cycle = prev_snap.get("emotion_cycle")
    except Exception as e:
        logger.warning(f"[emotion] 取前一档快照失败: {e}")
    phase = _derive_phase(lu, seal_rate, highest, premium, prev_seal)

    # 短线快指标: 量能(放量/缩量) + 0-100 情绪温度分 + 四阶段
    market_amount = await _two_market_amount()
    vol_ratio = await _compute_volume_ratio(trade_date, now, market_amount)
    # 炸板率(从家数算, 与同花顺官方封板率口径不同, 作独立反向因子)
    zha_rate = round(broken / (lu + broken), 4) if (lu and broken is not None and (lu + broken) > 0) else None
    emotion_score = _emotion_score(premium, seal_rate, highest, lu, zha_rate, vol_ratio)
    emotion_cycle = _derive_cycle(emotion_score, phase)

    # 情绪拐点提醒(四阶段较上一档跨带即推; 内部限次防抖)
    await _maybe_push_cycle_alert(trade_date, prev_cycle, emotion_cycle, emotion_score)

    await repository.save_emotion_snapshot({
        "trade_date": trade_date,
        "source": source,
        "limit_up_count": lu,
        "limit_up_history": lu_history,
        "limit_down_count": limit_down,
        "limit_down_history": ld_history,
        "broken_board_count": broken,
        "up_count": up_count,
        "down_count": down_count,
        "seal_rate": seal_rate,
        "highest_board": highest,
        "board_ladder": ladder,
        "board_stocks": board_stocks,
        "limit_up_codes": codes,
        "yest_limit_up_premium": premium,
        "emotion_phase": phase,
        "market_amount": market_amount,
        "volume_ratio": vol_ratio,
        "emotion_score": emotion_score,
        "emotion_cycle": emotion_cycle,
    })
    logger.debug(
        f"[emotion] {trade_date} src={source} 涨停{lu}(曾{lu_history}) 跌停{limit_down}(曾{ld_history}) "
        f"炸板{broken} 封板率{seal_rate} 最高{highest}连板 溢价{premium} 量能{vol_ratio} "
        f"温度{emotion_score} → {phase}/{emotion_cycle}"
    )
