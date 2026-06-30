"""问财候选榜 API — 同花顺问财自然语言选股 (v1.7.540).

  GET  /api/wencai            全部选股语句的最新候选快照(分策略成榜)
  POST /api/wencai/add-to-pool  把选中的候选股一键加入自选池(status=watch)

候选榜全局共享一份(scan_wencai 跑配置里的语句写库); 加自选是按当前用户写各自的 cfzy_biz_stock_pool。
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.quote_refresher import refresh_quotes_for_codes

router = APIRouter(prefix="/api/wencai", tags=["wencai"])


@router.get("")
async def get_wencai(user: Annotated[dict, Depends(get_current_user)]):
    """问财候选榜: 每条启用的选股语句一组, 含候选清单与本次刷新状态。"""
    rows = await repository.list_wencai_pool()
    strategies = [
        {
            "strategy_id": r.get("strategy_id"),
            "strategy_name": r.get("strategy_name"),
            "query_text": r.get("query_text"),
            "trade_date": r.get("trade_date"),
            "computed_at": r.get("computed_at"),
            "stock_count": r.get("stock_count", 0),
            "last_error": r.get("last_error") or "",
            "items": r.get("items") or [],
        }
        for r in rows
    ]
    return {"strategies": strategies}


class AddToPoolRequest(BaseModel):
    stocks: list[dict]   # [{code, name}]


@router.post("/add-to-pool")
async def add_to_pool(req: AddToPoolRequest, user: Annotated[dict, Depends(get_current_user)]):
    """把问财候选股一键加入自选池(逐只 upsert + 复活逻辑删, 最后批量刷行情)。"""
    added = 0
    codes: list[str] = []
    for item in req.stocks:
        code = str(item.get("code", "")).strip().zfill(6)
        name = str(item.get("name", "")).strip()
        if len(code) != 6 or not code.isdigit():
            continue
        await repository.add_stock(code, name, "short", "watch", user["id"])
        codes.append(code)
        added += 1
    if codes:
        await refresh_quotes_for_codes(codes)
    return {"ok": True, "added": added, "total": len(req.stocks)}
