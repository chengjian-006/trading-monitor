"""每日涨停复盘 API (v1.7.572).

  GET /api/limit-up?date=YYYYMMDD   某交易日涨停复盘 {meta, boards}(缺省=最新存档日; 今日无存档则实时拉)
  GET /api/limit-up/dates            有存档的交易日列表(日期选择器)
  GET /api/limit-up/export?date=     导出当日涨停 CSV(Excel 可直接打开)
"""
import csv
import io
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/limit-up", tags=["limit-up"])


async def _resolve(date: str | None) -> dict | None:
    """取指定日(缺省最新存档日)的复盘; 若请求今日但尚无存档, 实时拉一次涨停池兜底。"""
    today = datetime.now().strftime("%Y%m%d")
    if not date:
        date = await repository.latest_limit_up_date() or today
    got = await repository.get_limit_up_daily(date)
    if got:
        return got
    if date == today:   # 今日盘中/未到存档时刻: 实时拉一份(不落库)
        from backend.fetcher.limit_pool import get_limit_pool
        pool = await get_limit_pool(today)
        if pool and pool.get("boards"):
            meta = {k: pool.get(k) for k in
                    ("limit_up_count", "limit_up_history", "limit_down_count",
                     "broken_board_count", "seal_rate")}
            boards = sorted(pool["boards"], key=lambda b: (-(b.get("height") or 1), b.get("code", "")))
            return {"trade_date": today, "meta": meta, "boards": boards, "live": True}
    return None


@router.get("/dates")
async def get_dates(_: Annotated[dict, Depends(get_current_user)]):
    return {"dates": await repository.list_limit_up_dates(120)}


@router.get("/export")
async def export_csv(_: Annotated[dict, Depends(get_current_user)],
                     date: str | None = Query(None)):
    got = await _resolve(date)
    if not got:
        return {"error": "该日无涨停复盘数据"}
    d = got["trade_date"]
    buf = io.StringIO()
    buf.write("﻿")  # BOM, 让 Excel 正确识别 UTF-8 中文
    w = csv.writer(buf)
    w.writerow(["代码", "名称", "板数标签", "板数", "涨幅%", "炸板次数", "涨停概念"])
    for b in got["boards"]:
        w.writerow([b.get("code", ""), b.get("name", ""), b.get("streak_label", ""),
                    b.get("height", ""), b.get("pct", ""), b.get("open_times", 0),
                    b.get("reason", "")])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="limit_up_{d}.csv"'})


@router.get("")
async def get_limit_up(_: Annotated[dict, Depends(get_current_user)],
                       date: str | None = Query(None)):
    got = await _resolve(date)
    if not got:
        return {"trade_date": date, "meta": {}, "boards": [], "live": False}
    return {"trade_date": got["trade_date"], "meta": got.get("meta") or {},
            "boards": got.get("boards") or [], "live": got.get("live", False)}
