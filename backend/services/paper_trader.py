"""模拟账户成交决策(纯函数, 可单测) + on_signal 执行器(下个任务追加)。"""
import logging

logger = logging.getLogger(__name__)

_HALF_SUFFIX = "_HALF"

# 每笔买入目标仓位 = 总资产(成本口径)的比例。2026-06-10 用户定: "2层"≈20%/笔(固定, 不按 max_positions 等分);
# 资金不足一个完整份额时用剩余全部现金尽量买。
BUY_POSITION_PCT = 0.20


def calc_buy_fee(amount: float, account: dict) -> float:
    comm = max(amount * float(account["commission_rate"]), float(account["min_commission"]))
    transfer = amount * float(account["transfer_rate"])
    return round(comm + transfer, 2)


def calc_sell_fee(amount: float, account: dict) -> float:
    comm = max(amount * float(account["commission_rate"]), float(account["min_commission"]))
    stamp = amount * float(account["stamp_rate"])
    transfer = amount * float(account["transfer_rate"])
    return round(comm + stamp + transfer, 2)


def _is_half_sell(signal_id: str, direction: str) -> bool:
    return direction == "reduce" or (signal_id or "").upper().endswith(_HALF_SUFFIX)


def decide(account: dict, position, signal: dict,
           held_count: int, equity_cost: float) -> dict:
    """返回成交动作。side ∈ buy/sell/skip。
    equity_cost = 现金 + Σ持仓成本(成本口径总资产), 用于等额轮动定仓。"""
    direction = signal["direction"]
    price = float(signal["price"])
    cash = float(account["cash"])
    max_pos = int(account["max_positions"])
    # 每笔仓位比例随账户走(default 20% / unlimited 5%); 兼容旧调用(无此键时退回 BUY_POSITION_PCT)。
    pct = float(account.get("buy_position_pct") or BUY_POSITION_PCT)
    # 无限子弹: 现金可透支(不卡资金不足) + 不限持仓数 + 同股可加仓。
    unlimited = bool(int(account.get("unlimited_bullets") or 0))

    if direction == "buy":
        if position is not None and not unlimited:
            return {"side": "skip", "reason": "已持仓"}
        if held_count >= max_pos and not unlimited:
            return {"side": "skip", "reason": "仓位满"}
        lot_cost = price * 100
        target = equity_cost * pct
        if unlimited:
            # 无限子弹: 按目标份额买(至少1手), 不受现金约束(现金可为负); 已持仓即视为加仓。
            lots = int(target // lot_cost) or 1
            amount = round(lots * 100 * price, 2)
            fee = calc_buy_fee(amount, account)
            return {"side": "buy", "qty": lots * 100, "price": price, "amount": amount,
                    "fee": fee, "cash_after": round(cash - amount - fee, 2),
                    "note": "加仓" if position is not None else ""}
        # 普通账户定仓: 每笔尽量买"总资产(成本口径)的 pct"(2层≈20%); 不看 max_pos。
        # 现金够一个完整份额 → 买 floor(份额/手), 单手已超份额则买最接近的 1 手;
        # 现金不足一个完整份额("没仓位了") → 用剩余全部现金尽量买。
        if cash >= target:
            lots = int(target // lot_cost) or 1
        else:
            lots = int(cash // lot_cost)
        while lots >= 1:
            amount = round(lots * 100 * price, 2)
            fee = calc_buy_fee(amount, account)
            if amount + fee <= cash:
                return {"side": "buy", "qty": lots * 100, "price": price, "amount": amount,
                        "fee": fee, "cash_after": round(cash - amount - fee, 2), "note": ""}
            lots -= 1
        return {"side": "skip", "reason": "资金不足"}

    if direction in ("sell", "reduce"):
        if position is None or int(position["qty"]) <= 0:
            return {"side": "skip", "reason": "未持仓"}
        qty = int(position["qty"])
        cost_amount = float(position["cost_amount"])
        if _is_half_sell(signal["signal_id"], direction):
            sell_qty = int(qty // 2 // 100) * 100
            if sell_qty < 100:
                sell_qty = qty
            note = "卖半" if sell_qty < qty else "卖半→不足整手全清"
        else:
            sell_qty = qty
            note = "清仓"
        close_position = sell_qty >= qty
        amount = round(sell_qty * price, 2)
        fee = calc_sell_fee(amount, account)
        cost_basis_sold = round(cost_amount * sell_qty / qty, 2)
        realized_pnl = round((amount - fee) - cost_basis_sold, 2)
        realized_pnl_pct = round(realized_pnl / cost_basis_sold * 100, 3) if cost_basis_sold else 0.0
        return {"side": "sell", "qty": sell_qty, "price": price, "amount": amount, "fee": fee,
                "cash_after": round(cash + amount - fee, 2), "close_position": close_position,
                "cost_basis_sold": cost_basis_sold, "realized_pnl": realized_pnl,
                "realized_pnl_pct": realized_pnl_pct, "note": note}

    return {"side": "skip", "reason": "非交易方向"}


import re as _re

# 已开通的"额外"板块交易权限(主板60/00默认都有, 不在此列)。
# 2026-06-10 用户确认: 仅开通创业板, 未开科创板/北交所 → 688/北交所买点在模拟盘记"无权限"失败。
# 后续如开通科创板, 加 "star"; 开通北交所加 "bse"。
GRANTED_BOARDS = {"chinext"}

# decide() 跳过原因 → 流水展示用的失败原因
_SKIP_REASON_MAP = {"已持仓": "已持有该股", "仓位满": "仓位已满", "资金不足": "资金不足"}


def board_permission_error(code: str) -> str | None:
    """该股所属板块是否缺交易权限。返回失败原因, 有权限则 None。仅对买入有意义。"""
    if code.startswith("688"):
        return None if "star" in GRANTED_BOARDS else "无科创板交易权限"
    if code.startswith(("8", "4")) or code.startswith("920"):
        return None if "bse" in GRANTED_BOARDS else "无北交所交易权限"
    if code.startswith("30"):
        return None if "chinext" in GRANTED_BOARDS else "无创业板交易权限"
    return None  # 主板 60/00


async def on_signal(*, code: str, name: str, signal_id: str, signal_name: str,
                    direction: str, price: float, user_id: int) -> None:
    """信号确认触发(会推送)时调用; 仅生产环境执行; 任何异常吞掉不影响主流程。

    买点触发但买不进时(无板块权限/已持有/仓位满/资金不足)也留一笔 status='failed' 流水+原因;
    资金不足/仓位满为可重试(资金/仓位释放后下次扫描补买), 无权限/已持有为终态。"""
    try:
        from backend.core.config import is_production
        if not await is_production():
            return
        if direction not in ("buy", "sell", "reduce"):
            return
        if not code or not _re.match(r"^\d{6}$", code):
            return
        if not price or float(price) <= 0:
            return
        from backend.services import signal_specs
        if signal_specs.group_of(signal_id) in ("regime", "sector"):
            return
        from backend.models import repository
        from backend.models.repo.paper_trading import ACCOUNT_KEYS
        from datetime import datetime
        today = datetime.now().date()
        price = float(price)
        # 信号同时灌入全部模拟账户(默认 + 无限子弹), 各账户独立成交/失败留痕。
        for account_key in ACCOUNT_KEYS:
            try:
                acct = await repository.paper_get_or_create_account(user_id, account_key)
                await _process_account(acct, code=code, name=name, signal_id=signal_id,
                                       signal_name=signal_name, direction=direction, price=price,
                                       today=today, repository=repository)
            except Exception as e:
                logger.warning(f"[paper:{account_key}] 成交异常({code} {signal_id}), 忽略: {e}")
    except Exception as e:
        logger.warning(f"[paper] on_signal 异常({code} {signal_id}), 忽略: {e}")


async def _process_account(acct, *, code, name, signal_id, signal_name, direction,
                           price, today, repository) -> None:
    """单个模拟账户的成交处理(决策→成交/失败留痕)。"""
    tag = acct.get("account_key", "default")
    if await repository.paper_signal_processed(acct["id"], code, signal_id, today):
        return

    # 板块交易权限(仅买): 终态失败, 不进 decide
    if direction == "buy":
        perm_err = board_permission_error(code)
        if perm_err:
            await repository.paper_record_failure(acct, code, name, signal_id, signal_name,
                                                  direction, price, perm_err)
            logger.info(f"[paper:{tag}] 买入失败 {name}({code}) {signal_id}: {perm_err}")
            return

    position = await repository.paper_get_position(acct["id"], code)
    held = await repository.paper_position_count(acct["id"])
    equity_cost = float(acct["cash"]) + await repository.paper_sum_position_cost(acct["id"])
    action = decide(acct, position, {"direction": direction, "signal_id": signal_id,
                                     "price": price}, held, equity_cost)
    if action["side"] == "skip":
        # 卖出类跳过(未持仓等)静默; 仅买入失败留痕
        if direction == "buy":
            reason = _SKIP_REASON_MAP.get(action["reason"], action["reason"])
            await repository.paper_record_failure(acct, code, name, signal_id, signal_name,
                                                  direction, price, reason)
            logger.info(f"[paper:{tag}] 买入失败 {name}({code}) {signal_id}: {reason}")
        else:
            logger.info(f"[paper:{tag}] 跳过 {direction} {name}({code}) {signal_id}: {action['reason']}")
        return
    await repository.paper_apply_fill(acct, action, code, name, signal_id, signal_name,
                                      direction, entry_model_name=signal_name)
    logger.info(f"[paper:{tag}] 模拟{action['side']} {name}({code}) {action['qty']}股 @ {price} "
                f"({signal_id}){action.get('note') and ' '+action['note']} cash={action['cash_after']:.0f}")
