import asyncio
import time as _time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Body, HTTPException, Query
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.market_report import run_market_report
from backend.services import concept_buckets

router = APIRouter(prefix="/api/market-report", tags=["market-report"])

# v1.7.x hot-stocks 30s 内存缓存 (多用户共用一份, 避免每浏览器都打 EastMoney)
_HOT_CACHE: dict = {"at": 0.0, "data": None}
_HOT_TTL = 30.0
_HOT_LOCK = asyncio.Lock()


# v1.7.97: 改为从 DB 读 (由 refresh_market_overview 定时任务每 30s 写入)
# 多用户共享一份外部 API 调用, 避免每个浏览器都打新浪/akshare
@router.get("/overview")
async def get_market_overview(_: Annotated[dict, Depends(get_current_user)]):
    """实时市场概览: 全球指数 + A股四指数 + 市场温度。从 DB 读最新一份快照。"""
    row = await repository.get_market_overview()
    if not row:
        return {"global_indices": [], "indices": [], "market_stats": {}, "snapshot_at": None}
    return {
        "global_indices": row.get("global_indices") or [],
        "indices": row.get("a_indices") or [],
        "market_stats": row.get("market_stats") or {},
        "snapshot_at": str(row.get("snapshot_at")) if row.get("snapshot_at") else None,
    }


@router.get("/hot-stocks")
async def get_hot_stocks(_: Annotated[dict, Depends(get_current_user)]):
    """监控看板 tape 数据 — 涨幅前 20 + 跌幅前 15。30s 内存缓存。"""
    now = _time.time()
    if _HOT_CACHE["data"] is not None and (now - _HOT_CACHE["at"]) < _HOT_TTL:
        return _HOT_CACHE["data"]
    async with _HOT_LOCK:
        if _HOT_CACHE["data"] is not None and (_time.time() - _HOT_CACHE["at"]) < _HOT_TTL:
            return _HOT_CACHE["data"]
        from backend.services.attack_direction_analyst import _fetch_em_top
        try:
            gainers, losers = await asyncio.gather(
                _fetch_em_top("f3", 20, "desc"),
                _fetch_em_top("f3", 15, "asc"),
            )
        except Exception:
            gainers, losers = [], []
        data = {
            "gainers": [{"code": r["code"], "name": r["name"], "price": r["price"], "pct": r["pct"]} for r in gainers],
            "losers":  [{"code": r["code"], "name": r["name"], "price": r["price"], "pct": r["pct"]} for r in losers],
        }
        _HOT_CACHE["at"] = _time.time()
        _HOT_CACHE["data"] = data
        return data


# 全市场成交额排名 top100 (code→名次), 90s 缓存; 源同攻击方向分析(东财全市场成交额榜)
_AMRANK_CACHE: dict = {"at": 0.0, "data": None}
_AMRANK_TTL = 90.0
_AMRANK_LOCK = asyncio.Lock()


async def _fetch_amount_rank_top100() -> dict:
    """新浪全市场(沪深A股)成交额降序 top100 → {code: 名次}。
    东财全市场榜在 prod 被风控返回空, 故用新浪行情中心(主源, prod 可达)。"""
    import httpx
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "Market_Center.getHQNodeData?page=1&num=100&sort=amount&asc=0&node=hs_a&symbol=&_s_r_a=page")
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0",
                                           "Referer": "https://finance.sina.com.cn/"})
        arr = r.json()
    rank: dict = {}
    for i, it in enumerate(arr, 1):
        code = str(it.get("code") or "").zfill(6)
        if len(code) == 6 and code.isdigit() and code not in rank:
            rank[code] = i
    return rank


@router.get("/amount-rank")
async def get_amount_rank(_: Annotated[dict, Depends(get_current_user)]):
    """全市场成交额前100名 code→名次 映射。给股票池"成交额排名"列(前100显示名次, 否则100名外)。
    90s 内存缓存; 取数失败时保留上一份(不 blank)。"""
    now = _time.time()
    if _AMRANK_CACHE["data"] is not None and (now - _AMRANK_CACHE["at"]) < _AMRANK_TTL:
        return _AMRANK_CACHE["data"]
    async with _AMRANK_LOCK:
        if _AMRANK_CACHE["data"] is not None and (_time.time() - _AMRANK_CACHE["at"]) < _AMRANK_TTL:
            return _AMRANK_CACHE["data"]
        try:
            rank = await _fetch_amount_rank_top100()
        except Exception:
            rank = {}
        if rank:
            _AMRANK_CACHE["at"] = _time.time()
            _AMRANK_CACHE["data"] = rank
            return rank
        return _AMRANK_CACHE["data"] or {}


@router.get("/overview-public")
async def get_market_overview_public():
    """登录页用的公开版市场概览 — 无需鉴权, 只返回脱敏后的核心数据
    (4 大指数 + 涨停数 + 快照时间), 不含全球指数和市场温度详情。
    """
    row = await repository.get_market_overview()
    if not row:
        return {"indices": [], "limit_up": 0, "limit_down": 0, "snapshot_at": None}
    a_indices = row.get("a_indices") or []
    stats = row.get("market_stats") or {}
    return {
        "indices": [
            {"name": q.get("name", ""), "price": q.get("price", 0), "pct_change": q.get("pct_change", 0)}
            for q in a_indices[:4]
        ],
        "limit_up": stats.get("limit_up", 0),
        "limit_down": stats.get("limit_down", 0),
        "snapshot_at": str(row.get("snapshot_at")) if row.get("snapshot_at") else None,
    }


@router.get("")
async def get_today_reports(_: Annotated[dict, Depends(get_current_user)]):
    reports = await repository.get_today_reports()
    for r in reports:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"]).replace("T", " ")
    return reports


@router.get("/latest")
async def get_latest_report(_: Annotated[dict, Depends(get_current_user)]):
    report = await repository.get_latest_report()
    if report and report.get("created_at"):
        report["created_at"] = str(report["created_at"]).replace("T", " ")
    return report


@router.get("/index-trends")
async def get_index_trends_api(_: Annotated[dict, Depends(get_current_user)]):
    snapshot = await repository.get_market_snapshot()
    if snapshot and snapshot.get("index_trends"):
        return snapshot["index_trends"]
    return {}


@router.get("/market-stats")
async def get_market_stats_api(_: Annotated[dict, Depends(get_current_user)]):
    snapshot = await repository.get_market_snapshot()
    if snapshot and snapshot.get("market_stats"):
        return snapshot["market_stats"]
    return {}


# /regime 接口已删(v1.7.752 Deploy 2B): regime_filter 整层退役, 大盘状态统一走
# /api/signals/market-risk(三档+0-100风险分+大白话); 实时成交额并入下方 /turnover。


@router.get("/index-daily")
async def get_index_daily_api(_: Annotated[dict, Depends(get_current_user)], days: int = Query(30, ge=10, le=120)):
    """4 大指数最近 N 个交易日的日 K (date, open, high, low, close, volume).

    用新浪 K 线接口, 与 ai_analyst.INDEX_CODES 对齐 (sh000001/sz399001/sz399006/sh000688).
    给前端"指数趋势图"页面提供日级数据.
    """
    import httpx
    import json as _json
    INDEX_SINA = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
        ("sh000688", "科创指数"),
        ("sz399317", "全A指数"),   # 国证A指(全部A股)
    ]
    result: dict = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for sym, name in INDEX_SINA:
            url = (
                f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
                f"CN_MarketDataService.getKLineData"
                f"?symbol={sym}&scale=240&ma=no&datalen={days}"
            )
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
                text = resp.text
                start = text.find("(")
                end = text.rfind(")")
                if start < 0 or end <= start:
                    continue
                raw = _json.loads(text[start + 1:end])
                rows = []
                for r in raw:
                    try:
                        rows.append({
                            "date": r.get("day", ""),
                            "open": float(r.get("open", 0)),
                            "high": float(r.get("high", 0)),
                            "low": float(r.get("low", 0)),
                            "close": float(r.get("close", 0)),
                            "volume": float(r.get("volume", 0)),
                        })
                    except (ValueError, TypeError):
                        continue
                result[sym] = {"name": name, "data": rows}
            except Exception:
                result[sym] = {"name": name, "data": []}
        # 港股指数日K(腾讯 ifzq): 列序 [date, open, close, high, low, amount]
        for qcode, name in (("hkHSI", "恒生指数"), ("hkHSTECH", "恒生科技")):
            try:
                resp = await client.get(
                    f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={qcode},day,,,{days},qfq",
                    headers={"User-Agent": "Mozilla/5.0"})
                d = resp.json()
                node = (d.get("data") or {}).get(qcode) or {}
                day = node.get("qfqday") or node.get("day") or []
                rows = []
                for x in day:
                    try:
                        rows.append({"date": x[0], "open": float(x[1]), "close": float(x[2]),
                                     "high": float(x[3]), "low": float(x[4]),
                                     "volume": float(x[5]) if len(x) > 5 else 0})
                    except (ValueError, TypeError, IndexError):
                        continue
                result[qcode] = {"name": name, "data": rows}
            except Exception:
                result[qcode] = {"name": name, "data": []}
    return result


# ── 全市场成交额: 较上一日 / 5日均额 / 60日均额 ──
# 两市成交额 = 上证综指(zs_1A0001) + 深证成指(zs_399001) 的日成交额之和。
# 用同花顺日K(第6列=成交额, prod 可达; 东财对 prod IP 风控断连故不用)。120s 缓存 + 一次只取 2 指数。
_TURNOVER_CACHE: dict = {"at": 0.0, "data": None}
_TURNOVER_TTL = 120.0


def _trading_fraction_today() -> float:
    """已过交易时段占比 (0~1), 用于把今日成交额外推全天。"""
    from datetime import datetime
    n = datetime.now()
    m = n.hour * 60 + n.minute
    if m <= 570:      # 09:30 前
        e = 0
    elif m <= 690:    # 上午 09:30-11:30
        e = m - 570
    elif m < 780:     # 午休
        e = 120
    elif m <= 900:    # 下午 13:00-15:00
        e = 120 + (m - 780)
    else:             # 收盘后
        e = 240
    return min(max(e / 240, 0.0), 1.0)


async def _fetch_index_amounts(client, ths_code: str, ndays: int) -> dict:
    """同花顺指数日K取近 ndays 日成交额(元), 返回 {date(yyyymmdd): amount}。

    ths_code: zs_1A0001=上证综指 / zs_399001=深证成指。data 每行
    `日期,开,高,低,收,成交量,成交额,换手率,...`, 第6列(下标6)=成交额。
    """
    import json as _json
    url = f"http://d.10jqka.com.cn/v6/line/{ths_code}/01/last.js"
    out: dict[str, float] = {}
    try:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "http://stockpage.10jqka.com.cn/"})
        text = resp.text
        i, j = text.find("("), text.rfind(")")
        if i < 0 or j <= i:
            return out
        obj = _json.loads(text[i + 1:j])
        rows = (obj.get("data") or "").split(";")
        for r in rows[-ndays:]:
            parts = r.split(",")
            if len(parts) >= 7:
                try:
                    out[parts[0]] = float(parts[6])
                except (ValueError, TypeError):
                    continue
    except Exception:
        pass
    return out


async def _overlay_live_turnover(data: dict) -> dict:
    """把实时两市成交额(market_overview 快照 a_indices 上证+深证 amount 之和, 单位亿)
    盖到今日值上, 并按 U 型时点系数重算全天预测。

    历史序列源(同花顺指数日K)盘中往往没有今日行, 原来由 regime.total_amount_yi 补 —
    regime 已删(v1.7.752), 补值收进本接口, /turnover 成为成交额唯一出口。取不到快照原样返回。"""
    try:
        from backend.models import repository
        overview = await repository.get_market_overview()
        total = 0.0
        for idx in (overview or {}).get("a_indices") or []:
            if idx.get("name") in ("上证指数", "深证成指"):
                total += float(idx.get("amount") or 0)
        if total > 0:
            data["today_yi"] = round(total)
            frac = _trading_fraction_today()
            if frac >= 0.05:
                from backend.services.intraday_estimator import project_full_day_amount
                est = project_full_day_amount(float(total))
                data["projected_yi"] = round(est) if est else round(total / frac)
            data["ok"] = True
    except Exception:
        pass
    return data


@router.get("/turnover")
async def get_turnover_api(_: Annotated[dict, Depends(get_current_user)]):
    """两市成交额: 今日/较上一日/5日均额/60日均额/预测全天 (单位: 亿)。

    历史均额走同花顺日K(120s缓存); 今日实时值每次请求从 market_overview 快照覆盖(v1.7.752)。
    """
    now = _time.time()
    if _TURNOVER_CACHE["data"] and now - _TURNOVER_CACHE["at"] < _TURNOVER_TTL:
        return await _overlay_live_turnover(dict(_TURNOVER_CACHE["data"]))

    import httpx
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for code in ("zs_1A0001", "zs_399001"):  # 上证综指 + 深证成指
            amts = await _fetch_index_amounts(client, code, 70)
            for d, a in amts.items():
                sums[d] = sums.get(d, 0.0) + a
                counts[d] = counts.get(d, 0) + 1
    # 仅保留两指数都有数据的交易日(各源更新进度不一, 某日只有一个指数会算成半个市场)
    per_date = {d: v for d, v in sums.items() if counts.get(d) == 2}

    if not per_date:
        data = {"today_yi": None, "prev_yi": None, "ma5_yi": None,
                "ma60_yi": None, "projected_yi": None, "as_of": None, "ok": False}
        return await _overlay_live_turnover(data)

    from datetime import datetime
    today_str = datetime.now().strftime("%Y%m%d")
    # 均额/较上一日只取「早于今日」的已收盘交易日(各源更新进度不一, 按日期切分更稳)
    completed_yi = [per_date[d] / 1e8 for d in sorted(per_date) if d < today_str]

    def _avg(n: int):
        s = completed_yi[-n:]
        return round(sum(s) / len(s)) if s else None

    today_amt = per_date.get(today_str)
    today_yi = round(today_amt / 1e8) if today_amt else None
    # 预测全天: 用 U型时点系数(开盘前高/午间清淡/尾盘回升), 而非线性按时间外推。
    # 线性会在上午前高后低时高估(如11:30实际已成交~55%却按50%算→高估~10%, 连带"较上一日"虚高)。
    frac = _trading_fraction_today()
    if today_yi and frac >= 0.05:
        from backend.services.intraday_estimator import project_full_day_amount
        est = project_full_day_amount(float(today_yi))   # 单位无关(传亿得亿)
        projected_yi = round(est) if est else round(today_yi / frac)
    else:
        projected_yi = None

    data = {
        "today_yi": today_yi,                                   # THS 序列今日值(常缺); 实时值由 overlay 补
        "prev_yi": round(completed_yi[-1]) if completed_yi else None,
        "ma5_yi": _avg(5),
        "ma60_yi": _avg(60),
        "projected_yi": projected_yi,                           # overlay 会按实时值重算
        "as_of": today_str if today_amt else (sorted(per_date)[-1] if per_date else None),
        "ok": True,
    }
    _TURNOVER_CACHE["at"] = now
    _TURNOVER_CACHE["data"] = data
    return await _overlay_live_turnover(dict(data))


@router.get("/volume-surge")
async def get_volume_surge_api(user: Annotated[dict, Depends(get_current_user)]):
    """今日成交放量股: 自选池+持仓中量比(volume_ratio)高的票, 按量比降序 top N。

    复用已刷新的 pool 行情(零外部请求、无东财风险)。范围=用户自选+持仓
    (全市场放量榜需可靠的全市场量比源, 东财对 prod 风控、暂不做)。
    """
    stocks = await repository.list_stocks(user["id"])
    rows = []
    for s in stocks:
        try:
            vr = float(s.get("volume_ratio") or 0)
        except (ValueError, TypeError):
            vr = 0.0
        try:
            amount = float(s.get("amount") or 0)
        except (ValueError, TypeError):
            amount = 0.0
        rows.append({
            "code": s["code"],
            "name": s.get("name") or "",
            "volume_ratio": round(vr, 2),
            "amount": amount,  # 成交额, 单位「元」(前端按 亿/万 格式化)
            "pct_change": round(float(s.get("pct_change") or 0), 2),
            "status": s.get("status"),
        })
    rows.sort(key=lambda r: r["volume_ratio"], reverse=True)
    return {"items": rows[:10]}  # 自选股池当日量比前 10


@router.get("/theme-heat")
async def get_theme_heat_api(_: Annotated[dict, Depends(get_current_user)], days: int = Query(15, ge=5, le=40)):
    """市场情绪温度表: 最近 N 个交易日 × 题材 的涨停家数矩阵 (按炒作大类归并)。

    底层 cfzy_sys_theme_heat 存的是细题材(涨停原因首标签), 这里读取时套 concept_buckets
    归并成少数几条主线大类(PCB·覆铜板 / 玻璃基板·面板 / 半导体·存储 …), 未归类的进「其他」。
    每个大类格子带 sub(内部细题材分布)供前端下钻; themes 带 members(成员关键词)供前端
    「自选概念命中主线」匹配。归并在读取层做, 历史数据不动、随时可回退。

    返回:
      dates:  ["20260601", ...] 升序
      themes: [{name, total, days_on, members:[关键词...]}] 按窗口总热度降序('其他'置底)
      cells:  {date: {bucket: {c, s, sub:[{theme,c,s}...]}}}
    """
    rows = await repository.get_theme_heat(days)
    dates: list[str] = []
    bucket_total: dict[str, int] = {}
    bucket_days: dict[str, set] = {}
    cells: dict[str, dict] = {}
    for r in rows:
        d = r["trade_date"]
        theme = r["theme"]
        cnt = int(r["limit_up_count"] or 0)
        sample = r.get("sample_codes") or ""
        bucket = concept_buckets.classify(theme)
        if d not in cells:
            cells[d] = {}
            dates.append(d)
        slot = cells[d].setdefault(bucket, {"c": 0, "_samples": [], "sub": []})
        slot["c"] += cnt
        if sample:
            slot["_samples"].append(sample)
        slot["sub"].append({"theme": theme, "c": cnt, "s": sample})
        bucket_total[bucket] = bucket_total.get(bucket, 0) + cnt
        bucket_days.setdefault(bucket, set()).add(d)
    # 收口: 合并样本股去重(留前8), 细分按家数降序
    for d in cells:
        for bucket, slot in cells[d].items():
            names: list[str] = []
            for s in slot.pop("_samples"):
                for n in s.split(","):
                    n = n.strip()
                    if n and n not in names:
                        names.append(n)
            slot["s"] = ",".join(names[:8])
            slot["sub"].sort(key=lambda x: -x["c"])
    themes = [
        {"name": b, "total": bucket_total[b], "days_on": len(bucket_days[b]),
         "members": concept_buckets.bucket_keywords(b)}
        # '其他' 永远置底, 其余按窗口总热度降序
        for b in sorted(bucket_total, key=lambda x: (x == concept_buckets.OTHER, -bucket_total[x], -len(bucket_days[x])))
    ]
    return {"dates": dates, "themes": themes, "cells": cells}


@router.post("/generate")
async def trigger_report(_: Annotated[dict, Depends(get_current_user)]):
    await run_market_report()
    return {"ok": True}


# ── AI 报告反馈 (点赞/点踩) ──

class ReportFeedbackPayload(BaseModel):
    vote: str  # 'up' | 'down'
    notes: Optional[str] = None


@router.post("/{report_id}/feedback")
async def upsert_feedback(
    report_id: int,
    payload: ReportFeedbackPayload = Body(...),
    user: Annotated[dict, Depends(get_current_user)] = None,
):
    if payload.vote not in ("up", "down"):
        raise HTTPException(400, "vote 必须是 up 或 down")
    rid = await repository.upsert_report_feedback(
        user_id=user["id"], report_id=report_id, vote=payload.vote,
        notes=(payload.notes or "").strip() or None,
    )
    return {"id": rid, "ok": True}


@router.delete("/{report_id}/feedback")
async def delete_feedback(
    report_id: int,
    user: Annotated[dict, Depends(get_current_user)],
):
    await repository.delete_report_feedback(user["id"], report_id)
    return {"ok": True}


@router.get("/feedback")
async def list_feedback(
    user: Annotated[dict, Depends(get_current_user)],
    report_ids: Optional[str] = Query(None, description="逗号分隔的 report id 列表"),
):
    ids_list: Optional[list[int]] = None
    if report_ids is not None:
        try:
            ids_list = [int(x) for x in report_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "report_ids 必须是逗号分隔的整数")
    return await repository.list_report_feedback(user["id"], ids_list)
