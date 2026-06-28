"""股票池标签刷新 - v1.7.x.

低频任务(默认 20min): 给股票池每只票刷新两类标签, 写入 cfzy_biz_stock_pool:
  - concepts      : 概念题材 (东财个股概念, 最多 4 个, 逗号分隔)
  - limit_up_days : 连板数 (连续涨停的交易日数, 从最近一个交易日往前数)

取数策略:
  - 盘中: 日K用缓存(prefer_cache=True), 今日未定盘连板按已完成交易日算
  - 盘后: 强制拉最新(prefer_cache=False)把今日收盘折进连板, 每段非交易时间只跑一次
"""
import asyncio
import logging

from backend.models import repository
from backend import data_fetcher
from backend.core.trading_calendar import is_trading_time as _is_trading_time

logger = logging.getLogger(__name__)

_off_hours_done = False

# 概念噪音过滤: 指数成分/风格/资金/打板标记等不是"题材", 只保留真正的游资题材概念
_NOISE_EXACT = {
    "昨日连板", "昨日涨停", "昨日炸板", "昨日触板", "今日涨停", "连板", "涨停",
    "最近多板", "次新股", "ST板块",
    "大盘股", "中盘股", "小盘股", "大盘价值", "大盘成长", "中盘价值", "中盘成长",
    "价值股", "成长股", "周期股", "权重股", "茅指数", "宁组合",
    "预盈预增", "预亏预减", "高送转", "行业龙头", "养老金",
    "AB股", "AH股", "百元股", "百日新高", "百亿成交",
    "参股新三板", "参股银行", "参股保险", "参股券商",
}
# 包含这些关键词即视为噪音 (指数成分 / 互联互通 / 两融 / 国家队持股 / 热度·新高·预增等非题材标记)
_NOISE_KEYWORDS = (
    "HS300", "沪深300", "上证180", "上证380", "上证50", "深证100", "深成500",
    "中证500", "中证100", "中证1000", "国证", "富时", "MSCI", "标准普尔", "标普",
    "道琼斯", "创业板综", "创业成份", "科创50", "沪股通", "深股通",
    "融资融券", "转债标的", "证金持股", "汇金持股", "社保重仓", "QFII重仓", "基金重仓",
    "热股", "新高", "预增", "预减", "机构重仓", "机构持仓", "机构调研",
)


def _is_noise_concept(name: str) -> bool:
    if not name:
        return True
    if name.startswith("昨日"):  # 昨日连板/昨日涨停/昨日炸板/昨日高振幅... 复盘打板标记, 非题材
        return True
    if name.endswith("板块"):   # 地域板块 (甘肃板块/海南板块...), 对游资题材价值低
        return True
    if name in _NOISE_EXACT:
        return True
    return any(k in name for k in _NOISE_KEYWORDS)


from backend.utils.limit_calc import get_limit_pct as _limit_threshold  # 统一涨跌停幅


def _calc_limit_up_days(df, code: str, name: str) -> int | None:
    """从日K(按日期升序)算连板数: 最近一个交易日往前连续涨停的天数.

    用收盘价 close-to-close 涨幅 >= 板幅-0.6 容差判定(覆盖 9.98%/19.97% 等四舍五入).
    最近一日非涨停 → 0 (当前不在连板中).
    """
    try:
        if df is None or len(df) < 2:
            return None
        closes = [float(c) for c in df["close"].tolist() if c is not None]
        if len(closes) < 2:
            return None
        thr = _limit_threshold(code, name) - 0.6
        days = 0
        for i in range(len(closes) - 1, 0, -1):
            prev = closes[i - 1]
            if prev <= 0:
                break
            pct = (closes[i] - prev) / prev * 100.0
            if pct >= thr:
                days += 1
            else:
                break
        return days
    except Exception:
        return None


async def refresh_stock_tags():
    global _off_hours_done
    trading = _is_trading_time()
    if trading:
        _off_hours_done = False
    else:
        if _off_hours_done:
            return
        _off_hours_done = True

    all_stocks = await repository.list_all_stocks()
    if not all_stocks:
        return

    name_by_code: dict[str, str] = {}
    for s in all_stocks:
        name_by_code.setdefault(s["code"], s.get("name") or "")
    codes = list(name_by_code.keys())

    # ── 概念题材 ──
    concepts_map: dict[str, list[str]] = {}
    try:
        concepts_map, _ = await data_fetcher.get_stock_concepts(codes)
    except Exception as e:
        logger.warning(f"[stock_tags] 概念取数失败: {e}")

    # ── 连板数 (盘中用缓存, 盘后拉最新折入今日收盘) ──
    prefer_cache = trading
    sem = asyncio.Semaphore(5)

    async def _one(code: str):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=12, prefer_cache=prefer_cache)
                return code, _calc_limit_up_days(df, code, name_by_code.get(code, ""))
            except Exception:
                return code, None

    pairs = await asyncio.gather(*[_one(c) for c in codes])
    lu_map = dict(pairs)

    updates: list[dict] = []
    for code in codes:
        raw = concepts_map.get(code) or []
        cleaned = [c for c in raw if not _is_noise_concept(c)]
        updates.append({
            "code": code,
            "concepts": ",".join(cleaned[:4]),
            "limit_up_days": lu_map.get(code),
        })

    await repository.batch_update_stock_tags(updates)
    n_concepts = sum(1 for u in updates if u["concepts"])
    n_boards = sum(1 for u in updates if (u["limit_up_days"] or 0) >= 1)
    logger.info(f"[stock_tags] 刷新 {len(updates)} 只 (概念 {n_concepts}, 连板 {n_boards}), prefer_cache={prefer_cache}")

    # ── 额外池: 连板梯队 + 今日信号(不在自选里的票)→ 题材/换手落通用缓存, 供个股弹窗 summary 读 ──
    await _refresh_extra_pool_cache(set(codes))


async def _refresh_extra_pool_cache(pool_codes: set[str], cap: int = 80) -> None:
    """给"连板梯队 + 今日信号"里不在自选的票, 预热题材/换手到 cfzy_sys_api_cache。
    自选票的题材/换手已写在 stock_pool(quote/tag refresher), 这里只补"非自选但常出现在盯盘里"的票,
    让弹窗 summary 不用在请求时硬撞东财。"""
    extra: set[str] = set()
    try:
        emo = await repository.get_latest_emotion()
        for b in (emo or {}).get("board_stocks") or []:
            c = str(b.get("code") or "").zfill(6)
            if len(c) == 6 and c.isdigit():
                extra.add(c)
    except Exception as e:
        logger.debug(f"[stock_tags] 连板池取数失败: {e}")
    try:
        for s in (await repository.get_today_signals_all()) or []:
            c = str(s.get("code") or "").zfill(6)
            if len(c) == 6 and c.isdigit():
                extra.add(c)
    except Exception as e:
        logger.debug(f"[stock_tags] 信号池取数失败: {e}")

    extra -= pool_codes                  # 自选已在 stock_pool 维护, 跳过
    codes = list(extra)[:cap]            # 限量, 防止东财/THS 取数被打爆
    if not codes:
        return

    concepts_map: dict[str, list[str]] = {}
    try:
        concepts_map, _ = await data_fetcher.get_stock_concepts(codes)
    except Exception as e:
        logger.debug(f"[stock_tags] 额外池概念取数失败: {e}")
    extra_data: dict = {}
    try:
        extra_data = await data_fetcher.get_stock_extra(codes)
    except Exception as e:
        logger.debug(f"[stock_tags] 额外池换手取数失败: {e}")

    n_c = n_t = 0
    for c in codes:
        cleaned = [x for x in (concepts_map.get(c) or []) if not _is_noise_concept(x)]
        if cleaned:
            try:
                await repository.api_cache_set(f"concept:{c}", "+".join(cleaned[:4]))
                n_c += 1
            except Exception:
                pass
        tv = (extra_data.get(c) or {}).get("turnover")
        if tv:
            try:
                await repository.api_cache_set(f"turnover:{c}", round(float(tv), 2))
                n_t += 1
            except Exception:
                pass
    logger.info(f"[stock_tags] 额外池预热 {len(codes)} 只 (题材 {n_c}, 换手 {n_t})")
