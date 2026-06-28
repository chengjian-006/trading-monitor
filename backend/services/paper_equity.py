"""模拟账户收盘盯市: 每交易日 15:05 对持仓按当日收盘价估值, 写资金曲线。"""
import logging
from datetime import datetime

from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)


async def snapshot_paper_equity(user_id: int = 1) -> None:
    now = datetime.now()
    if now.weekday() >= 5:
        return
    from backend.models.repo.paper_trading import ACCOUNT_KEYS
    for account_key in ACCOUNT_KEYS:
        try:
            await _snapshot_account(user_id, account_key, now)
        except Exception as e:
            logger.warning(f"[paper_equity:{account_key}] 盯市失败, 忽略: {e}")


async def _snapshot_account(user_id: int, account_key: str, now: datetime) -> None:
    acct = await repository.paper_get_or_create_account(user_id, account_key)
    positions = await repository.paper_list_positions(acct["id"])
    holdings_mv = 0.0
    if positions:
        codes = [p["code"] for p in positions]
        try:
            quotes = await data_fetcher.get_realtime_quotes(codes)
        except Exception as e:
            logger.warning(f"[paper_equity:{account_key}] 取价失败, 用成本估值: {e}")
            quotes = {}
        for p in positions:
            px = float((quotes.get(p["code"]) or {}).get("price") or 0)
            if px <= 0:
                px = float(p["cost_amount"]) / int(p["qty"]) if p["qty"] else 0
            holdings_mv += px * int(p["qty"])
    cash = float(acct["cash"])
    total = round(cash + holdings_mv, 2)
    init = float(acct["initial_capital"]) or 1.0
    ret_pct = round((total - init) / init * 100, 3)
    await repository.paper_upsert_equity(acct["id"], user_id, now.date(), round(cash, 2),
                                         round(holdings_mv, 2), total, ret_pct, len(positions))
    logger.info(f"[paper_equity:{account_key}] {now.date()} 总资产={total:.0f} "
                f"收益率={ret_pct:+.2f}% 持仓{len(positions)}")
