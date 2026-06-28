from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend import data_fetcher

router = APIRouter(prefix="/api/paper-trading", tags=["paper-trading"])


@router.get("/summary")
async def summary(user: Annotated[dict, Depends(get_current_user)],
                  account_key: str = Query("default")):
    acct = await repository.paper_get_or_create_account(user["id"], account_key)
    positions = await repository.paper_list_positions(acct["id"])
    holdings_mv = 0.0
    today_pnl = 0.0       # 当日盈亏(金额) = Σ(现价-昨收)×股数, 昨收口径(同花顺式)
    prev_mv = 0.0         # 昨日持仓市值 = Σ(昨收×股数), 仅计昨收可得的票, 作当日盈亏%的分母
    if positions:
        codes = [p["code"] for p in positions]
        try:
            quotes = await data_fetcher.get_realtime_quotes(codes)
        except Exception:
            quotes = {}
        for p in positions:
            q = quotes.get(p["code"]) or {}
            qty = int(p["qty"])
            px = float(q.get("price") or 0) or \
                (float(p["cost_amount"]) / qty if qty else 0)
            holdings_mv += px * qty
            pc = float(q.get("pre_close") or 0)   # 新浪源含昨收; 东财备源为0则跳过该票
            if px > 0 and pc > 0:
                today_pnl += (px - pc) * qty
                prev_mv += pc * qty
    cash = float(acct["cash"])
    total = cash + holdings_mv
    init = float(acct["initial_capital"]) or 1.0
    today_pnl_pct = round(today_pnl / prev_mv * 100, 3) if prev_mv > 0 else None
    rs = await repository.paper_realized_stats(acct["id"])
    curve = await repository.paper_get_equity_curve(acct["id"])
    peak, mdd = init, 0.0
    for pt in curve:
        eq = float(pt["total_equity"])
        peak = max(peak, eq)
        mdd = min(mdd, (eq - peak) / peak * 100 if peak else 0)
    n = int(rs["n"] or 0); win = int(rs["win"] or 0)
    gain = float(rs["gain"] or 0); loss = float(rs["loss"] or 0)
    return {
        "initial_capital": init, "cash": round(cash, 2), "holdings_mv": round(holdings_mv, 2),
        "total_equity": round(total, 2), "total_return_pct": round((total - init) / init * 100, 3),
        "total_pnl": round(total - init, 2),                 # 总体盈亏(金额) = 总资产-初始本金
        "today_pnl": round(today_pnl, 2), "today_pnl_pct": today_pnl_pct,   # 当日盈亏(金额/%)
        "position_count": len(positions),
        "realized_pnl": round(float(rs["pnl"] or 0), 2),
        "closed_trades": n, "win_rate": round(win / n * 100, 1) if n else None,
        "profit_factor": round(gain / loss, 2) if loss > 0 else (None if gain == 0 else 99.0),
        "max_drawdown_pct": round(mdd, 2),
        "max_positions": int(acct["max_positions"]),
        "account_key": acct.get("account_key", "default"),
        "account_name": acct.get("name", "模拟账户"),
        "buy_position_pct": round(float(acct.get("buy_position_pct") or 0.20) * 100, 2),
        "unlimited_bullets": int(acct.get("unlimited_bullets") or 0),
    }


@router.get("/positions")
async def positions(user: Annotated[dict, Depends(get_current_user)],
                    account_key: str = Query("default")):
    acct = await repository.paper_get_or_create_account(user["id"], account_key)
    rows = await repository.paper_list_positions(acct["id"])
    if rows:
        try:
            quotes = await data_fetcher.get_realtime_quotes([r["code"] for r in rows])
        except Exception:
            quotes = {}
        for r in rows:
            qty = int(r["qty"]); cost = float(r["cost_amount"])
            px = float((quotes.get(r["code"]) or {}).get("price") or 0)
            r["price"] = px
            r["mv"] = round(px * qty, 2) if px else None
            r["float_pct"] = round((px * qty - cost) / cost * 100, 2) if (px and cost) else None
            r["avg_cost"] = round(cost / qty, 3) if qty else None
    return rows


@router.get("/trades")
async def trades(user: Annotated[dict, Depends(get_current_user)],
                 limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                 account_key: str = Query("default")):
    acct = await repository.paper_get_or_create_account(user["id"], account_key)
    return await repository.paper_list_trades(acct["id"], limit, offset)


@router.get("/equity")
async def equity(user: Annotated[dict, Depends(get_current_user)],
                 account_key: str = Query("default")):
    acct = await repository.paper_get_or_create_account(user["id"], account_key)
    return await repository.paper_get_equity_curve(acct["id"])


@router.get("/model-stats")
async def model_stats(user: Annotated[dict, Depends(get_current_user)],
                      account_key: str = Query("default")):
    acct = await repository.paper_get_or_create_account(user["id"], account_key)
    return await repository.paper_model_stats(acct["id"])


class SettingsBody(BaseModel):
    initial_capital: float | None = None
    max_positions: int | None = None
    account_key: str = "default"


@router.put("/settings")
async def update_settings(body: SettingsBody, user: Annotated[dict, Depends(get_current_user)]):
    await repository.paper_update_settings(user["id"], body.initial_capital, body.max_positions,
                                           account_key=body.account_key)
    return {"ok": True}


class ResetBody(BaseModel):
    initial_capital: float = 1000000.0
    max_positions: int = 10
    account_key: str = "default"


@router.post("/reset")
async def reset(body: ResetBody, user: Annotated[dict, Depends(get_current_user)]):
    await repository.paper_reset_account(user["id"], body.initial_capital, body.max_positions,
                                         account_key=body.account_key)
    return {"ok": True}
