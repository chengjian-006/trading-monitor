from fastapi import APIRouter, Query

from backend import data_fetcher

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def search(q: str = Query("")):
    if len(q) < 1:
        return []
    return await data_fetcher.search_stock(q)
