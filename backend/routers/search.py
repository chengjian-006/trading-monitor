from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend import data_fetcher
from backend.core.auth import get_current_user

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def search(user: Annotated[dict, Depends(get_current_user)], q: str = Query("")):
    if len(q) < 1:
        return []
    return await data_fetcher.search_stock(q)
