"""每日收盘重算 8 个买入模型 近3月/近6月 胜率+单笔均收益 —— 5分钟真实可成交口径 (v1.7.598)。

口径(诚实化, 修前视偏差):
  - 7 个K线模型(回踩MA10/20/60·缩量突破·平台突破·强势起点·弱势极限)走 backtester_5m 诚实引擎:
    候选日粗筛(必要条件) → 5分钟逐根注入「该时刻已知」信息精判 → 触发才算交易。
    与实时信号引擎同一份检测器、同一种数据构造(close=现价/high=游程最高/量额U型外推/MA增量修正),
    并传 code 使贴板不追与实盘对称。
    旧口径的前视偏差(用全天收盘/全天量筛交易、按盘中突破价入场 → 盘中触发尾盘走弱的失败样本
    被系统性剔除, 胜率高估)已除。
  - 竞价弱转强用日K模拟高开(开盘价9:25已知, 无前视), 板幅按股票代码判(30/68=20cm)。
  - 出场: backtester_5m._REG 生产口径(盘中触及止损/卖半, 破均线按收盘), 与模型回测页/图鉴同源。
  - 锚点 = cfzy_sys_kline_5m 最大交易日(5分钟数据覆盖到哪天, 战绩就截至哪天)。
  - 数据按需加载: 逐票日线 + 仅候选日的5分钟bar(单票单往返), 不再整表搬运。
胜率 = 单笔扣费后净收益 > 0 的占比; 窗口为滚动近91天/近182天(按触发日)。
CPU 重活走线程池, 不阻塞事件循环。结果 upsert cfzy_biz_model_winrate, 供买入提醒带战绩。
"""
import asyncio
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend.core.trading_calendar import is_workday
from backend.models import repository
from backend.models.repo._db import _fetchall
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.backtester_5m import (
    MODEL_IDS, build_model, candidate_days, eod_trades, load_5m_days, load_daily_one,
    scan_trades_5m, universe_codes,
)

# 盘中触发的模型走5分钟诚实口径(修前视); 收盘入场的弱势极限走快速EOD路径(无前视, 见 _REG eod_honest)。
_5M_MODEL_IDS = [mid for mid in MODEL_IDS if not build_model(mid).get("eod_honest")]
_EOD_MODEL_IDS = [mid for mid in MODEL_IDS if build_model(mid).get("eod_honest")]
from backend.services.model_backtest_weekly import (
    _sim_right, MODELS, FEE, DEDUP, MIN_BARS,
)

logger = logging.getLogger(__name__)

WIN_3M, WIN_6M = 91, 182        # 滚动近3月/近6月(自然日)
LOAD_DAYS = 300                 # 兼容旧诊断脚本引用(现按需加载, 不再用它切窗)
_CONCURRENCY = 6                # 逐票 DB 加载并发(隔离小并发, 不抢实时行情池)

# 真股票前缀: 沪深主板/创业板/科创板。剔除指数码(98x/88x)/板块/北交所(4x/8x/92x)/期货主连(lc..)。
_STOCK_PREFIX = ("00", "30", "60", "68")


def _is_stock(code: str) -> bool:
    """仅保留真 A 股(沪深主板/创业/科创); 缓存里残留的指数/板块/北交所码一律剔除。"""
    return str(code)[:2] in _STOCK_PREFIX


def _anchor_date(rows: list[dict]) -> str:
    """全市场最大交易日(YYYY-MM-DD)。

    必须取全局 max, 不能用 ORDER BY code,trade_date 后的 rows[-1]——
    那是排最后的 code(如指数码 980030)的最后一根 K 线, 早就停更, 会把锚点冻在旧日期。
    """
    return max(str(r["trade_date"])[:10] for r in rows)


def _auction_trades(ind: pd.DataFrame, code: str = "") -> list[tuple[str, float]]:
    """竞价弱转强(日K模拟高开): T-1强势缩量小回调 + T高开≥3%, 入场=T开盘价。

    板幅按股票代码判(v1.7.598 修笔误: 旧代码拿日期前两位判创业/科创, 日期恒"20"开头 →
    lim 恒 0.10, 创业/科创板高开 8%~19% 的样本全被错误剔除)。
    返回 [(触发日, 扣费净收益), ...]; 同 DEDUP 天内去重。"""
    o = ind["open"].values; h = ind["high"].values; c = ind["close"].values
    m10a = ind["ma10"].values
    vol_avg10 = ind["volume"].rolling(10, min_periods=5).mean().values
    dts = ind["date"].values
    n = len(ind)
    lim = 0.20 if str(code)[:2] in ("30", "68") else 0.10
    out: list[tuple[str, float]] = []
    last_dt = None
    for i in range(MIN_BARS, n):
        dt = pd.Timestamp(str(dts[i])[:10])
        if last_dt is not None and (dt - last_dt).days <= DEDUP:
            continue
        prev = ind.iloc[i - 1]; pc = float(prev["close"])
        pv = float(prev["volume"])
        pm10 = float(prev.get("ma10", 0) or 0); pm20 = float(prev.get("ma20", 0) or 0)
        pm60 = float(prev.get("ma60", 0) or 0)
        pavg10 = float(vol_avg10[i - 1]) if i > 0 and not np.isnan(vol_avg10[i - 1]) else 0
        p20c = ind["close"].iloc[i - 21] if i >= 21 else pc
        if not (pc > 0 and pm20 > 0 and pm60 > 0 and pm10 > 0 and pavg10 > 0):
            continue
        strong = pc > pm20 > pm60 and pc / p20c - 1.0 >= 0.15
        shrink = pv < pavg10 * 0.8
        pullback = -0.05 <= pc / ind["close"].iloc[i - 2] - 1.0 <= 0.01 if i >= 2 else False
        above_m10 = pc > pm10
        if not (strong and shrink and pullback and above_m10):
            continue
        gap = o[i] / pc - 1.0
        if not (0.03 <= gap < lim - 0.01):
            continue
        last_dt = dt
        r = _sim_right(o[i], o, h, c, m10a, i, n)
        if r:
            out.append((str(dts[i])[:10], r[0] - FEE))
    return out


# 模型注册: 7 个K线模型走诚实5分钟引擎; 名称对齐 cfzy_biz_model_winrate 既有行(weekly MODELS 中文名)
_ID_TO_NAME = {sid: name for sid, name in MODELS}


def _prep_candidates(df: pd.DataFrame, start: str, end: str):
    """纯CPU: 算指标 + 盘中触发模型候选日并集(仅这些需按需加载5分钟bar; 弱势极限走EOD不需要)。"""
    ind = compute_indicators(df)
    ind["amount_est"] = ind["volume"] * ind["close"]
    days: set[str] = set()
    for mid in _5M_MODEL_IDS:
        days.update(candidate_days(build_model(mid), ind, start, end))
    return ind, sorted(days)


def _crunch_one(code: str, ind: pd.DataFrame, day5m: dict, start: str, end: str):
    """纯CPU: 一只票 盘中触发模型5分钟诚实扫描 + 弱势极限EOD + 竞价弱转强日K模拟
    → [(model_name, date, ret), ...]。"""
    out: list[tuple[str, str, float]] = []
    for mid in _5M_MODEL_IDS:
        model = build_model(mid)
        for t in scan_trades_5m(model, ind, day5m, start, end, code=code):
            out.append((_ID_TO_NAME[mid], t["buy_date"], t["ret"]))
    for mid in _EOD_MODEL_IDS:
        model = build_model(mid)
        for t in eod_trades(model, ind, start, end, code=code):
            out.append((_ID_TO_NAME[mid], t["buy_date"], t["ret"]))
    for d, ret in _auction_trades(ind, code=code):
        if start <= d <= end:
            out.append((_ID_TO_NAME["BUY_AUCTION_STRENGTH"], d, ret))
    return out


def _aggregate(acc: dict, anchor: str) -> list[dict]:
    """按近3月/近6月桶聚合 + 近3月胜率排名(与旧口径同构, 表结构不变)。"""
    sid_of = {name: sid for sid, name in MODELS}

    def agg(lst):
        if not lst:
            return None, None, 0
        arr = np.array(lst)
        return round(float((arr > 0).mean() * 100), 1), round(float(arr.mean() * 100), 2), len(arr)

    out = []
    for _, name in MODELS:
        wr3, nt3, c3 = agg(acc[name]["3m"])
        wr6, nt6, c6 = agg(acc[name]["6m"])
        out.append({"signal_id": sid_of[name], "model_name": name,
                    "win_rate_3m": wr3, "net_3m": nt3, "n_3m": c3,
                    "win_rate_6m": wr6, "net_6m": nt6, "n_6m": c6})

    ranked = sorted([o for o in out if o["n_3m"] and o["win_rate_3m"] is not None],
                    key=lambda x: x["win_rate_3m"], reverse=True)
    rank_n = len(ranked)
    for idx, o in enumerate(ranked, 1):
        o["rank_3m"] = idx
    for o in out:
        o.setdefault("rank_3m", None)
        o["rank_n"] = rank_n
    return out


async def refresh_model_winrate():
    """每日(工作日)收盘后: 5分钟诚实口径重算全模型胜率 → upsert cfzy_biz_model_winrate。"""
    if not is_workday(datetime.now()):
        return
    rows = await _fetchall("SELECT DATE(MAX(dt)) AS d FROM cfzy_sys_kline_5m")
    if not rows or not rows[0]["d"]:
        logger.warning("[model_winrate] 5分钟K线表为空, 跳过(需先跑 kline_5m 回填/追加)")
        return
    anchor = str(rows[0]["d"])[:10]
    base = datetime.strptime(anchor, "%Y-%m-%d")
    cut3 = (base - timedelta(days=WIN_3M)).strftime("%Y-%m-%d")
    cut6 = (base - timedelta(days=WIN_6M)).strftime("%Y-%m-%d")

    codes = sorted(c for c in await universe_codes("all") if _is_stock(c))
    if not codes:
        logger.warning("[model_winrate] 5分钟表无可用股票, 跳过")
        return
    logger.info(f"[model_winrate] 5分钟诚实口径重算: {len(codes)}只 窗口{cut6}~{anchor}")

    acc = {name: {"3m": [], "6m": []} for _, name in MODELS}
    sem = asyncio.Semaphore(_CONCURRENCY)
    done = [0]

    async def work(code: str):
        try:
            async with sem:
                df = await load_daily_one(code)
                if df is None:
                    return
                ind, cand = await asyncio.to_thread(_prep_candidates, df, cut6, anchor)
                day5m = await load_5m_days(code, cand) if cand else {}
            trades = await asyncio.to_thread(_crunch_one, code, ind, day5m, cut6, anchor)
            for name, d, ret in trades:
                if d >= cut6:
                    acc[name]["6m"].append(ret)
                    if d >= cut3:
                        acc[name]["3m"].append(ret)
        except Exception:
            logger.exception(f"[model_winrate] {code} 重算失败, 跳过")
        finally:
            done[0] += 1
            if done[0] % 500 == 0:
                logger.info(f"[model_winrate] {done[0]}/{len(codes)}")

    await asyncio.gather(*[work(c) for c in codes])

    out = _aggregate(acc, anchor)
    await repository.save_model_winrate(anchor, out)
    logger.info("[model_winrate] 重算完成(5分钟口径) 截至%s: " % anchor + " ".join(
        f"{r['model_name']}(3月{r['win_rate_3m']}%/{r['n_3m']}笔, 6月{r['win_rate_6m']}%/{r['n_6m']}笔)"
        for r in out))
    return {"as_of": anchor, "models": out}
