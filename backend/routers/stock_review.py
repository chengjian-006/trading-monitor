"""Route for GET /api/stock/{code}/review — individual stock AI review with daily cap."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.core.auth import get_current_user
from backend.core.config import load_config
from backend.models import repository
from backend.services.ai_advisor import stock_review

router = APIRouter(prefix="/api/stock", tags=["stock_review"])


@router.get("/{code}/review")
async def get_stock_review(
    code: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Get AI individual stock review.

    Enforces daily cap on number of different stocks reviewed per user.
    Cache hits (same user + same code + same day) don't count against cap.

    Args:
        code: Stock code (e.g., "600000")
        user: Current authenticated user (injected via get_current_user)

    Returns:
        {
            "facts": {...stock facts dict...},
            "narrative": "...LLM-generated narrative or null if LLM failed...",
            "as_of": "YYYY-MM-DD",
            "cached": bool
        }

    Raises:
        HTTPException(429): When review count >= ai_advisor_daily_cap
    """
    user_id = user["id"]

    # Check daily cap
    n = await repository.count_reviews_today(user_id)
    cap = load_config().get("ai_advisor_daily_cap", 200)

    if n >= cap:
        raise HTTPException(
            status_code=429,
            detail="今日研判次数已达上限"
        )

    # Generate review (with cache; cache hits don't increase count)
    return await stock_review.generate_stock_review(user_id, code)
