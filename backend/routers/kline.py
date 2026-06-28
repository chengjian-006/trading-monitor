import asyncio
import logging
import math
from datetime import datetime
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends

from backend import data_fetcher
from backend.core.auth import get_current_user
from backend.core.trading_calendar import effective_trade_date, is_trading_time
from backend.models import repository
from backend.services import signal_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kline", tags=["kline"])


async def _refresh_sparklines_bg(codes: list[str]) -> None:
    """后台异步刷新分时存盘 — 不阻塞接口响应, 让下一次轮询拿到新数据。"""
    try:
        live = await data_fetcher.get_batch_intraday_sparkline(codes) or {}
        good = {c: v for c, v in live.items() if v and v.get("trends")}
        if good:
            await repository.upsert_sparkline_snapshots(good, datetime.now().strftime("%Y-%m-%d"))
    except Exception as e:
        logger.debug(f"[sparkline_bg] 后台刷新失败: {e}")


def _isnan(v) -> bool:
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return True


@router.get("/batch-intraday")
async def get_batch_intraday(codes: str = ""):
    """批量获取分时走势数据（用于股票池迷你走势图）。

    存盘 + 回退: 实时取到的非空走势写盘; 实时取空(非交易时段/盘后)的用上一交易日存盘兜底,
    避免"走势"列在周末/盘后/重启后全空。
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return {}
    code_list = code_list[:50]

    # 存盘优先(秒返): 东财行情接口对生产IP封禁, 实时逐只取很慢(每只先试东财再退同花顺),
    # 直接读已存盘的分时立即返回; 只对没存盘的新票同步实时取一次。
    result = await repository.get_sparkline_snapshots(code_list)
    missing = [c for c in code_list if c not in result]
    if missing:
        live = await data_fetcher.get_batch_intraday_sparkline(missing) or {}
        good = {c: v for c, v in live.items() if v and v.get("trends")}
        if good:
            try:
                await repository.upsert_sparkline_snapshots(good, datetime.now().strftime("%Y-%m-%d"))
            except Exception:
                pass
            result.update(good)

    # v1.7.x: 不再每次调用都后台全池实时刷新(走势存盘由 prefetch_intraday_sparklines 任务每25s统一负责)。
    # 原来每次 batch-intraday(挂载+每30s×多用户/多标签页)都 create_task 全池实时取一次(~2.7s/50只),
    # 后台任务堆积 + 与 quote_refresher 抢事件循环 → 盘中股票池整体变慢。去掉重复劳动。
    return result


def _sparkline_to_points(trends: list[dict]) -> list[dict]:
    """迷你走势快照(time/price/volume) → 分时点(补 avg_price)。
    快照本身不存均价线, 这里用成交量加权(VWAP)就地算出真实均价, 让 DB 优先阶段也有完整均价线;
    随后台实时返回会被权威分时(含数据源均价)整体替换。"""
    pts = []
    cum_pv = 0.0
    cum_v = 0.0
    for t in trends:
        try:
            price = float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        vol = 0.0
        try:
            vol = float(t.get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        cum_pv += price * vol
        cum_v += vol
        avg = (cum_pv / cum_v) if cum_v > 0 else price
        pts.append({"time": t.get("time"), "price": price, "avg_price": round(avg, 3), "volume": vol})
    return pts


@router.get("/{code}/intraday")
async def get_intraday(code: str, date: str = "", source: str = ""):
    """获取个股分时数据 + 昨收。date 为空或当天 → 实时; 历史交易日 → 取归档快照(供回放)。

    source=snapshot: 当日走 DB 优先 —— 读迷你走势快照(prefetch 每 25s 焐热, 仅自选池)秒返,
      无快照(非自选/未焐热)则返回空, 由前端后台再补实时。均价线由 VWAP 现算。
    返回 {pre_close, points}; 昨收供分时图按"末价 vs 昨收"着色(与涨跌幅同基准)。
    历史归档快照不含昨收 → pre_close=0, 前端退化为按首点着色。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    if date and date != today:
        snap = await repository.get_intraday_snapshot(code, date)
        # 归档分时不含昨收, 从日K缓存取该日前一交易日收盘补上, 红绿基准与真实涨跌幅一致
        pre_close = await repository.get_prev_close_before(code, date) if snap else 0
        return {"pre_close": pre_close, "points": snap or []}
    if source == "snapshot":
        snap = await repository.get_sparkline_snapshot_today(code, effective_trade_date())
        trends = (snap or {}).get("trends") or []
        if trends:
            return {"pre_close": (snap or {}).get("pre_close") or 0, "points": _sparkline_to_points(trends)}
        return {"pre_close": 0, "points": []}
    points, pre_close = await data_fetcher.get_intraday_data(code)
    return {"pre_close": pre_close, "points": points}


@router.get("/{code}/signal-markers")
async def get_signal_markers(
    code: str, user: Annotated[dict, Depends(get_current_user)], date: str = "",
):
    """某票某日的买卖点(给分时图标记)。date 为空取"有效交易日"(盘后过夜/周末=上一交易日,
    与实时分时图实际显示的那一段对齐, 否则过夜/周末复盘时线是上一交易日的、买点却按当日历日查=空)。
    来自已固化的 cfzy_biz_signals。"""
    from backend.core.trading_calendar import effective_trade_date
    rows = await repository.get_signals_by_code_date(code, user["id"], date or effective_trade_date())
    out = []
    for r in rows:
        ts = str(r.get("triggered_at") or "")
        hm = ts[11:16] if len(ts) >= 16 else ""
        if not hm:
            continue
        out.append({
            "time": hm,
            "price": r.get("price"),
            "direction": r.get("direction"),
            "signal_name": r.get("signal_name"),
        })
    return out


@router.get("/{code}/signal-days")
async def get_signal_days(code: str, user: Annotated[dict, Depends(get_current_user)]):
    """某票有买卖点的交易日列表(给历史回放日期选择器)。"""
    return await repository.get_signal_days_for_code(code, user["id"])


@router.get("/{code}/signal-markers-daily")
async def get_signal_markers_daily(
    code: str, user: Annotated[dict, Depends(get_current_user)], days: int = 150,
):
    """某票近 N 天的买卖点(按日期, 给日K图标记)。来自已固化的 cfzy_biz_signals。"""
    rows = await repository.get_signals_by_code_since(code, user["id"], days)
    out = []
    for r in rows:
        ts = str(r.get("triggered_at") or "")
        d = ts[:10]
        if not d:
            continue
        out.append({
            "date": d,
            "time": ts[11:16],   # HH:MM 触发时刻, 供图表标记悬浮详情
            "price": r.get("price"),
            "direction": r.get("direction"),
            "signal_name": r.get("signal_name"),
        })
    return out


@router.get("/{code}/big-orders")
async def get_big_orders(code: str, threshold: float = 15_000_000):
    """获取个股当日大单逐笔 (主动买/卖, 金额 ≥ threshold 元)。空返回 {}。"""
    data = await data_fetcher.get_big_orders_today(code, threshold)
    return data or {}


_CONCEPT_CACHE_KEY = "concept:{}"
_CONCEPT_MAX_STALE = 14 * 86400   # 题材准静态, 库内14天内都算可用
_TURNOVER_CACHE_KEY = "turnover:{}"
_TURNOVER_MAX_STALE = 2 * 86400   # 换手日度, 2天内的快照可作离线兜底


async def _resolve_concept(code: str, pooled: str | None) -> str | None:
    """题材解析(DB优先, 不在请求时硬撞东财):
    1) 自选库已维护的题材(quote/tag refresher, 不碰东财, 由调用方传入) →
    2) 通用 DB 缓存(写穿) →
    3) 实时取一次(东财, prod 多半失败), 取到则写穿缓存。
    全程 best-effort, 失败返回 None。
    """
    def _fmt(raw: str) -> str:
        tags = [t for t in raw.replace("，", ",").split(",") if t.strip()]
        return "+".join(tags[:4])

    if pooled and pooled.strip():
        return _fmt(pooled)
    try:
        cached = await repository.api_cache_get(_CONCEPT_CACHE_KEY.format(code), max_stale_seconds=_CONCEPT_MAX_STALE)
        if cached and cached[0]:
            return str(cached[0])
    except Exception:
        pass
    try:
        cmap, _ = await data_fetcher.get_stock_concepts([code])
        tags = cmap.get(code) or []
        if tags:
            disp = "+".join(tags[:4])
            try:
                await repository.api_cache_set(_CONCEPT_CACHE_KEY.format(code), disp)
            except Exception:
                pass
            return disp
    except Exception:
        pass
    return None


@router.get("/{code}/summary")
async def get_stock_summary(
    code: str, user: Annotated[dict, Depends(get_current_user)],
):
    """通用个股弹窗头部速览(聚合, 纯只读):
    现价/涨跌幅/换手/振幅/5日涨幅/MA位置/量能倍数/题材/最新信号/临近买点。
    全部 best-effort —— 任一数据源失败仅缺该字段, 不影响其余, 不抛错。
    """
    from backend.services.signal_engine_detectors import get_stock_ma_status

    out: dict = {"code": code, "name": None, "close": None, "pct_change": None,
                 "amplitude": None, "pct_5d": None, "turnover": None,
                 "ma_status": None, "vol_ratio_avg10": None, "concept": None,
                 "latest_signal": None, "near_buy": None}

    # 1) 日K(DB缓存, 主源): MA位置 / 量能倍数 / 5日涨幅 / 振幅 / 收盘兜底
    try:
        df = await data_fetcher.get_daily_kline(code, 120)
    except Exception:
        df = None
    if df is not None and not df.empty:
        try:
            ind = signal_engine.compute_indicators(df)
            last = ind.iloc[-1]
            close = float(last["close"])
            out["close"] = round(close, 2)
            if len(ind) >= 2:
                pc = float(ind.iloc[-2]["close"])
                if pc > 0:
                    out["pct_change"] = round((close - pc) / pc * 100, 2)
                    out["amplitude"] = round((float(last["high"]) - float(last["low"])) / pc * 100, 2)
            if len(ind) >= 6:
                c5 = float(ind.iloc[-6]["close"])
                if c5 > 0:
                    out["pct_5d"] = round((close - c5) / c5 * 100, 2)
            if len(ind) >= 11:
                avg10 = float(ind["volume"].iloc[-11:-1].mean())   # 前10日均量(不含今日)
                vtoday = float(last["volume"])
                if avg10 > 0:
                    out["vol_ratio_avg10"] = round(vtoday / avg10, 2)
        except Exception:
            pass
        try:
            out["ma_status"] = (get_stock_ma_status(df) or {}).get("position")
        except Exception:
            pass

    # 1.5) 自选库整行(quote_refresher 存价/涨跌/换手/量比, tag_refresher 存题材) —— 读库优先, 离线兜底
    pool = None
    try:
        pool = await repository.get_pool_row(code)
    except Exception:
        pool = None
    pooled_concepts = pool.get("concepts") if pool else None
    if pool:                                      # 自选: 名称/价/涨跌/换手 先用库内值兜底
        out["name"] = pool.get("name") or out["name"]
        if pool.get("price"):
            out["close"] = round(float(pool["price"]), 2)
        if pool.get("pct_change") is not None:
            out["pct_change"] = round(float(pool["pct_change"]), 2)
        if pool.get("turnover"):
            out["turnover"] = round(float(pool["turnover"]), 2)

    # 2) 实时行情 / 换手 / 题材(DB优先写穿) / 最新信号 / 临近买点 —— 并发 best-effort
    quotes, extra, concept, signals, nb = await asyncio.gather(
        data_fetcher.get_realtime_quotes([code]),
        data_fetcher.get_stock_extra([code]),
        _resolve_concept(code, pooled_concepts),
        repository.get_signals_by_code_since(code, user["id"], 150),
        repository.get_near_buy_snapshot(user["id"]),
        return_exceptions=True,
    )

    if isinstance(quotes, dict) and quotes.get(code):
        q = quotes[code]
        out["name"] = q.get("name") or out["name"]
        if q.get("price"):                       # 实时价覆盖库/日K(更新)
            out["close"] = round(float(q["price"]), 2)
            # 涨跌幅只随实时价一起覆盖: 竞价未撮合 price=0 时 pct 是脏值(-100%),
            # 不能让脏 pct 配昨收价显示(如 7.76/-100%)
            if q.get("pct_change") is not None:
                out["pct_change"] = round(float(q["pct_change"]), 2)
    # 换手: 库内值(自选)优先; 否则实时取到则用并写穿缓存; 再否则读缓存兜底
    if out["turnover"] is None and isinstance(extra, dict) and extra.get(code):
        tv = extra[code].get("turnover")
        if tv:
            out["turnover"] = round(float(tv), 2)
            try:
                await repository.api_cache_set(_TURNOVER_CACHE_KEY.format(code), out["turnover"])
            except Exception:
                pass
    if out["turnover"] is None:
        try:
            ct = await repository.api_cache_get(_TURNOVER_CACHE_KEY.format(code), max_stale_seconds=_TURNOVER_MAX_STALE)
            if ct and ct[0] is not None:
                out["turnover"] = float(ct[0])
        except Exception:
            pass
    if isinstance(concept, str) and concept:
        out["concept"] = concept
    if isinstance(signals, list) and signals:
        s = signals[-1]
        ts = str(s.get("triggered_at") or "")
        out["latest_signal"] = {
            "name": s.get("signal_name"),
            "date": ts[:10],
            "time": ts[11:16] if len(ts) >= 16 else "",
            "direction": s.get("direction"),
        }
    if isinstance(nb, dict) and nb.get("items"):
        for it in nb["items"]:
            if str(it.get("code", "")).zfill(6) == str(code).zfill(6):
                hits = it.get("hits") or []
                out["near_buy"] = {
                    "tier": it.get("tier"),               # 2=触发 1=接近
                    "dist": it.get("dist"),               # 距相关均线 %
                    "name": (hits[0].get("buy_name") if hits else None) or it.get("status_label"),
                }
                break

    return out


async def _patch_today_bar(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """DB 优先取日K后, 用实时行情补/替当日最后一根 —— 盘中末根随现价跳, 不停在上次落库值。

    新浪源含 开/高/低/收/量, 字段齐: 当日已在缓存→替最后一根(取更全的高低收量), 缺当日→追加一根。
    东财备源只有现价(其余 0)时只补收盘价, 不伪造开高低量。best-effort, 失败保持纯 DB 结果。
    """
    try:
        quotes = await data_fetcher.get_realtime_quotes([code])
        q = quotes.get(code) or quotes.get(str(code).zfill(6))
        if not q:
            return df
        price = float(q.get("price") or 0)
        if price <= 0:
            return df
        today = effective_trade_date()
        last_date = str(df.iloc[-1]["date"])[:10]
        hi = float(q.get("high") or 0)
        lo = float(q.get("low") or 0)
        op = float(q.get("open") or 0)
        vol = float(q.get("volume") or 0)
        if last_date == today:
            i = df.index[-1]
            df.at[i, "close"] = price
            if hi > 0:
                df.at[i, "high"] = max(float(df.at[i, "high"]), hi, price)
            if lo > 0:
                df.at[i, "low"] = min(float(df.at[i, "low"]), lo, price)
            if vol > 0:
                df.at[i, "volume"] = vol
        elif today > last_date and op > 0:
            row = {c: None for c in df.columns}
            row.update({
                "date": today, "open": op,
                "high": max(hi, price) if hi > 0 else price,
                "low": min(lo, price) if lo > 0 else price,
                "close": price, "volume": vol,
            })
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    except Exception as e:
        logger.debug(f"[kline] 补当日最新一根失败({code}): {e}")
    return df


@router.get("/{code}")
async def get_kline(code: str, days: int = 120):
    # DB 优先(全市场 kline_cache 已回填, 秒返; 缓存不足才回退联网), 再用实时行情补当日最后一根。
    df = await data_fetcher.get_daily_kline(code, days, prefer_cache=True)
    if df.empty:
        return []
    df = await _patch_today_bar(df, code)
    ind = signal_engine.compute_indicators(df)
    records = []
    for _, row in ind.iterrows():
        records.append({
            "date": row["date"],
            "open": round(row["open"], 2),
            "high": round(row["high"], 2),
            "low": round(row["low"], 2),
            "close": round(row["close"], 2),
            "volume": round(row["volume"], 0),
            "ma5": round(row["ma5"], 2) if not _isnan(row["ma5"]) else None,
            "ma10": round(row["ma10"], 2) if not _isnan(row["ma10"]) else None,
            "ma20": round(row["ma20"], 2) if not _isnan(row["ma20"]) else None,
            "ma60": round(row["ma60"], 2) if not _isnan(row["ma60"]) else None,
        })
    return records


@router.get("/{code}/week")
async def get_kline_week(code: str, weeks: int = 80):
    """周K: 从日K聚合, 以周一为周起始。返回近N周 OHLCV。"""
    df = await data_fetcher.get_daily_kline(code, max(weeks * 7 + 10, 250), prefer_cache=True)
    if df.empty:
        return []
    df["date_"] = pd.to_datetime(df["date"])
    df = df.set_index("date_").sort_index()
    # 日K → 周K聚合: 周一为一周之首
    w = df.resample("W-MON").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    records = []
    for dt, row in w.iterrows():
        if len(records) >= weeks:
            break
        records.append({
            "date": dt.strftime("%Y-%m-%d"),
            "open": round(row["open"], 2),
            "high": round(row["high"], 2),
            "low": round(row["low"], 2),
            "close": round(row["close"], 2),
            "volume": round(row["volume"], 0),
        })
    return records
