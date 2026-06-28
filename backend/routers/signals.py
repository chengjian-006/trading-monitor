import json
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/signals", tags=["signals"])


def _parse_indicators(rows: list[dict]) -> list[dict]:
    for row in rows:
        ind = row.get("indicators")
        if isinstance(ind, str):
            try:
                row["indicators"] = json.loads(ind)
            except (json.JSONDecodeError, TypeError):
                row["indicators"] = None
    return rows


@router.get("/today")
async def today_signals(user: Annotated[dict, Depends(get_current_user)], code: Optional[str] = Query(None)):
    rows = await repository.get_today_signals(user["id"], code=code)
    return _parse_indicators(rows)


@router.get("/history")
async def signal_history(
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = 200,
    date: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    signal_id: Optional[str] = Query(None),
    with_perf: bool = Query(True),
):
    """信号历史。date 精确单日; 或 start_date/end_date 闭区间(默认前端给最近5个交易日)。
    signal_id 可选: 只取该信号(预警总览成功率明细用)。with_perf=True 时附带触发后 +5d/+10d/+20d 最高涨幅。"""
    if with_perf:
        rows = await repository.get_signals_history_with_perf(
            user["id"], limit, date=date, start_date=start_date, end_date=end_date, signal_id=signal_id)
    else:
        rows = await repository.get_signals_history(
            user["id"], limit, date=date, start_date=start_date, end_date=end_date, signal_id=signal_id)
    return _parse_indicators(rows)


@router.get("/stats")
async def signal_stats(
    user: Annotated[dict, Depends(get_current_user)],
    days_back: int = Query(30, ge=1, le=180),
):
    """按 signal_id 分组的统计: 命中数 / 平均最高涨幅 / 各档胜率(摸高视角)。"""
    return await repository.get_signal_stats(user["id"], days_back=days_back)


@router.get("/outcome-stats")
async def signal_outcome_stats(
    user: Annotated[dict, Depends(get_current_user)],
    days_back: int = Query(90, ge=7, le=365),
):
    """按 signal_id 聚合的实际表现统计(信号闭环视角).

    与 /stats 区别:
      /stats          基于 5/10/20 日内"最高价"(天花板, 反映信号能不能给到机会)
      /outcome-stats  基于 1/3/5 日"收盘价"(实际持仓视角, 反映严格跟单的真实胜率)
    """
    return await repository.get_signal_outcome_stats(user["id"], days_back=days_back)


@router.get("/review-list")
async def review_signal_list(
    user: Annotated[dict, Depends(get_current_user)],
    start: str = Query(..., description="区间起 YYYY-MM-DD"),
    end: str = Query(..., description="区间止 YYYY-MM-DD"),
    categories: str = Query("buy,sell,reduce", description="逗号分隔: buy/sell/reduce/sector/plunge"),
):
    """区间复盘清单: 该区间触发的信号逐条明细(当前收益/区间最大浮盈浮亏/T+1·3·5/评估) + 按类型汇总。"""
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    return await repository.get_review_signal_list(user["id"], start, end, cats)


@router.get("/outcome-compare")
async def outcome_compare(
    user: Annotated[dict, Depends(get_current_user)],
    days_back: int = Query(90, ge=7, le=365),
):
    """买点 vs 卖点(含减仓) 整体胜率并排对比 (实际收盘口径, 近 N 天)。"""
    return await repository.get_outcome_compare(user["id"], days_back=days_back)


@router.get("/weekly-trend")
async def weekly_trend(
    user: Annotated[dict, Depends(get_current_user)],
    weeks: int = Query(12, ge=2, le=52),
):
    """买/卖成功率按周趋势 (近 N 周)。"""
    return await repository.get_weekly_outcome_trend(user["id"], weeks=weeks)


@router.get("/model-weekly")
async def model_weekly(
    user: Annotated[dict, Depends(get_current_user)],
    weeks: int = Query(8, ge=2, le=26),
):
    """按 (买点模型 × 周) 的真实成功率矩阵 + 近2周排行(当前行情适合哪个模型)。"""
    return await repository.get_model_weekly_outcome(user["id"], weeks=weeks)


@router.get("/model-backtest")
async def model_backtest(user: Annotated[dict, Depends(get_current_user)]):
    """最近一次全市场按周回测结果(各模型 胜率/资金加权占用/年化资金效率/盈利因子)。"""
    return await repository.get_latest_model_backtest()


@router.get("/model-winrate")
async def model_winrate(user: Annotated[dict, Depends(get_current_user)]):
    """各买入模型 近3月/近6月 全市场回测胜率+单笔均收益 + 近3月胜率排名(每日收盘重算)。"""
    data = await repository.get_model_winrate()
    rows = list(data.values())
    # 有近3月排名的在前(rank_3m 升序), 无近3月样本的垫底
    rows.sort(key=lambda r: (r.get("rank_3m") is None, r.get("rank_3m") or 999))
    run_date = rows[0].get("run_date") if rows else None
    return {"run_date": run_date, "models": rows}


@router.get("/market-risk")
async def market_risk_status(user: Annotated[dict, Depends(get_current_user)]):
    """市场风险两级预警: 最新状态 + 近60交易日指标."""
    from backend.models.repo._db import _fetchall
    rows = await _fetchall(
        "SELECT * FROM cfzy_biz_market_risk ORDER BY trade_date DESC LIMIT 60")
    return {"latest": rows[0] if rows else None, "rows": rows}


@router.get("/matrix")
async def signal_matrix(
    user: Annotated[dict, Depends(get_current_user)],
    days: int = Query(14, ge=1, le=90),
):
    """按 (日期 × signal_id) 聚合的命中矩阵, 给预警总览页用.

    返回 { dates: ["YYYY-MM-DD"], rows: [{signal_id, signal_name, signal_group,
    direction, counts: [int], total: int}] }. counts 与 dates 一一对应.
    """
    return await repository.get_signal_matrix(user["id"], days_back=days)
