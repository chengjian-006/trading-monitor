"""
交易分析服务 — 解析同花顺交割单，配对买卖交易，计算盈亏
"""

import io
import re
import logging
from datetime import datetime, date
from collections import defaultdict

import pandas as pd

logger = logging.getLogger(__name__)


def _split_row(line: str) -> list[str]:
    """鲁棒分列: Tab 优先(保留空单元格); 否则按 2+ 空格(对齐导出)切; 再退化到任意空白。"""
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) < 6:
        parts = line.strip().split()
    return [c.strip() for c in parts]


# 按表头名匹配列 — 兼容不同券商/导出口径(交割单 与 历史成交 列顺序不同)。
# 自带表头时优先用表头映射; 无表头则回退到旧的固定列号 _parse_row。
_FIELD_ALIASES = {
    "date": ["成交日期", "发生日期", "日期"],
    "time": ["成交时间", "委托时间", "时间"],
    "code": ["证券代码", "股票代码"],
    "name": ["证券名称", "股票名称"],
    "op": ["操作", "业务名称", "买卖标志"],
    "quantity": ["成交数量", "成交股数"],
    "price": ["成交均价", "成交价格", "成交价"],
    "amount": ["成交金额"],
    "fee": ["手续费", "佣金"],
    "stamp_tax": ["印花税"],
    "transfer_fee": ["过户费"],
    "net_amount": ["发生金额", "清算金额"],
    "deal_no": ["成交编号", "成交序号"],   # 每笔成交全局唯一号; 用于区分等量拆单 vs 重复导入(可选列)
}
_REQUIRED = ("date", "code", "op", "quantity", "price", "amount")
# 历史成交: 无成交日期列(日期由外部注入), 故 date 不在必需集。
_REQUIRED_HISTORY = ("code", "op", "quantity", "price", "amount")


def _build_colmap(header_cells: list[str], required: tuple = _REQUIRED) -> dict | None:
    """从表头行构造 字段→列号 映射; 缺必需列则返回 None(交给固定列号兜底)。"""
    h = [c.strip() for c in header_cells]
    m: dict[str, int] = {}
    for field, names in _FIELD_ALIASES.items():
        for nm in names:
            if nm in h:
                m[field] = h.index(nm)
                break
    return m if all(k in m for k in required) else None


def _cell(cols: list[str], idx) -> str:
    return cols[idx].strip() if isinstance(idx, int) and 0 <= idx < len(cols) else ""


def _parse_row_mapped(cols: list[str], m: dict, inject_date: date | None = None) -> dict | None:
    """按表头映射解析一行(列顺序无关)。

    inject_date 不为空时(历史成交: 行内无成交日期列), 直接用它作 trade_date,
    不再解析行内日期; 为空时(交割单)从行内 date 列解析。
    """
    op = _cell(cols, m.get("op"))
    if op not in ("证券买入", "证券卖出"):
        return None
    code = _cell(cols, m.get("code"))
    if not code or not code.isdigit():
        return None
    if inject_date is not None:
        trade_date = inject_date
    else:
        try:
            trade_date = datetime.strptime(_cell(cols, m.get("date")), "%Y%m%d").date()
        except ValueError:
            return None
    try:
        quantity = int(float(_cell(cols, m.get("quantity"))))
        price = float(_cell(cols, m.get("price")))
        amount = float(_cell(cols, m.get("amount")))
    except ValueError:
        return None
    if quantity <= 0 or price <= 0:
        return None
    if not _amount_consistent(price, quantity, amount):
        logger.warning(f"[trade_parse] 拒收脏行(金额≠价×量): code={code} px={price} qty={quantity} amt={amount}")
        return None
    return {
        "trade_date": trade_date,
        "trade_time": _cell(cols, m.get("time")),
        "code": code.zfill(6),
        "name": _cell(cols, m.get("name")),
        "direction": "buy" if op == "证券买入" else "sell",
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "fee": _safe_float(_cell(cols, m.get("fee"))),
        "stamp_tax": _safe_float(_cell(cols, m.get("stamp_tax"))),
        "transfer_fee": _safe_float(_cell(cols, m.get("transfer_fee"))),
        "net_amount": _safe_float(_cell(cols, m.get("net_amount"))),
        "deal_no": (_cell(cols, m.get("deal_no")).strip() or None) if m.get("deal_no") is not None else None,
    }


def parse_trades_text(text: str) -> list[dict]:
    lines = [ln for ln in text.strip().split("\n") if ln.strip()]
    if not lines:
        return []

    has_header = "成交日期" in lines[0]
    colmap = _build_colmap(_split_row(lines[0])) if has_header else None
    start = 1 if has_header else 0

    trades = []
    for line in lines[start:]:
        cols = _split_row(line)
        if colmap:
            record = _parse_row_mapped(cols, colmap)
        else:
            record = _parse_row(cols) if len(cols) >= 17 else None
        if record:
            trades.append(record)

    if not trades:
        logger.warning(
            f"[trade_parse] 解析0条 行数={len(lines)} 有表头={has_header} "
            f"建表头映射={'成功' if colmap else '失败'} 首行列数={len(_split_row(lines[0]))} "
            f"首行={lines[0][:120]!r}"
        )
    return trades


def parse_history_text(text: str, trade_date: date) -> list[dict]:
    """解析平安证券「历史成交」粘贴文本(无成交日期列, 按笔明细)。

    日期由参数 trade_date 注入每一行(前端日期选择器决定); 表头识别用历史成交特征列
    (成交时间/成交编号), date 不要求; 仍走金额自洽护栏。无表头或缺必需列 → []。
    """
    lines = [ln for ln in text.strip().split("\n") if ln.strip()]
    if not lines:
        return []
    colmap = _build_colmap(_split_row(lines[0]), required=_REQUIRED_HISTORY)
    if not colmap:
        logger.warning(
            f"[history_parse] 表头映射失败 首行列数={len(_split_row(lines[0]))} 首行={lines[0][:120]!r}"
        )
        return []
    trades = []
    for line in lines[1:]:
        record = _parse_row_mapped(_split_row(line), colmap, inject_date=trade_date)
        if record:
            trades.append(record)
    if not trades:
        logger.warning(f"[history_parse] 解析0条 行数={len(lines)} 日期={trade_date}")
    return trades


def parse_trades_excel(file_bytes: bytes) -> list[dict]:
    df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
    colmap = _build_colmap([str(c) for c in df.columns])
    trades = []
    for _, row in df.iterrows():
        cols = [str(row.iloc[i]) if pd.notna(row.iloc[i]) else "" for i in range(len(row))]
        record = _parse_row_mapped(cols, colmap) if colmap else _parse_row(cols)
        if record:
            trades.append(record)
    return trades


def _parse_row(cols: list[str]) -> dict | None:
    op = cols[4].strip() if len(cols) > 4 else ""
    if op not in ("证券买入", "证券卖出"):
        return None

    code = cols[2].strip()
    if not code or not code.isdigit():
        return None

    try:
        date_str = cols[0].strip()
        trade_date = datetime.strptime(date_str, "%Y%m%d").date()
    except (ValueError, IndexError):
        return None

    try:
        quantity = int(float(cols[5].strip()))
        price = float(cols[7].strip())
        amount = float(cols[8].strip())
    except (ValueError, IndexError):
        return None

    if quantity <= 0 or price <= 0:
        return None
    if not _amount_consistent(price, quantity, amount):
        logger.warning(f"[trade_parse] 拒收脏行(金额≠价×量): code={code} px={price} qty={quantity} amt={amount}")
        return None

    fee = _safe_float(cols[11]) if len(cols) > 11 else 0
    stamp_tax = _safe_float(cols[12]) if len(cols) > 12 else 0
    transfer_fee = _safe_float(cols[15]) if len(cols) > 15 else 0
    net_amount = _safe_float(cols[10]) if len(cols) > 10 else 0
    deal_no = (cols[6].strip() or None) if len(cols) > 6 else None   # 定位版列6=成交编号(同花顺远航版式)

    return {
        "trade_date": trade_date,
        "trade_time": cols[1].strip() if len(cols) > 1 else "",
        "code": code.zfill(6),
        "name": cols[3].strip() if len(cols) > 3 else "",
        "direction": "buy" if op == "证券买入" else "sell",
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "fee": fee,
        "stamp_tax": stamp_tax,
        "transfer_fee": transfer_fee,
        "net_amount": net_amount,
        "deal_no": deal_no,
    }


def _safe_float(s: str) -> float:
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return 0.0


def _amount_consistent(price: float, quantity: int, amount: float) -> bool:
    """护栏: A股成交金额 = 成交价 × 成交数量(费前)。两者严重不符 → 判定脏数据(列错位/
    单位错乱), 拒收。amount<=0 时不校验(部分券商格式无金额列)。
    背景: 2026-06-07 导入整行列错位, 金额落进价格列, 写入 price=金额 的脏数据污染成本。"""
    if amount <= 0:
        return True
    expected = price * quantity
    return abs(amount - expected) <= max(1.0, expected * 0.01)


def analyze_trades(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": [], "by_stock": {}, "summary": _empty_summary()}

    # 按股票分组
    by_code = defaultdict(list)
    for t in trades:
        by_code[t["code"]].append(t)

    paired_trades = []
    stock_summary = {}

    for code, records in by_code.items():
        # 按日期+时间排序
        records.sort(key=lambda x: (x["trade_date"], x["trade_time"]))
        name = next((r["name"] for r in records if r["name"]), code)

        # FIFO 配对
        buy_queue = []  # [(date, price, remaining_qty, fee_per_share)]
        total_buy_amount = 0.0
        total_sell_amount = 0.0
        total_fee = 0.0
        completed_pairs = []
        still_holding = 0

        for r in records:
            total_fee += r["fee"] + r["stamp_tax"] + r["transfer_fee"]

            if r["direction"] == "buy":
                buy_queue.append({
                    "date": r["trade_date"],
                    "price": r["price"],
                    "qty": r["quantity"],
                    "remaining": r["quantity"],
                })
                total_buy_amount += r["amount"]
            else:
                # 卖出 — FIFO消耗买入
                sell_qty = r["quantity"]
                total_sell_amount += r["amount"]

                while sell_qty > 0 and buy_queue:
                    buy = buy_queue[0]
                    match_qty = min(sell_qty, buy["remaining"])

                    pair = {
                        "code": code,
                        "name": name,
                        "buy_date": buy["date"].isoformat(),
                        "buy_price": buy["price"],
                        "sell_date": r["trade_date"].isoformat(),
                        "sell_price": r["price"],
                        "quantity": match_qty,
                        "buy_amount": round(buy["price"] * match_qty, 2),
                        "sell_amount": round(r["price"] * match_qty, 2),
                        "profit": round((r["price"] - buy["price"]) * match_qty, 2),
                        "return_pct": round((r["price"] - buy["price"]) / buy["price"] * 100, 2),
                        "hold_days": (r["trade_date"] - buy["date"]).days,
                    }
                    completed_pairs.append(pair)

                    buy["remaining"] -= match_qty
                    sell_qty -= match_qty

                    if buy["remaining"] <= 0:
                        buy_queue.pop(0)

        # 统计仍持仓
        for buy in buy_queue:
            still_holding += buy["remaining"]

        # 股票汇总
        if completed_pairs:
            profits = [p["profit"] for p in completed_pairs]
            returns = [p["return_pct"] for p in completed_pairs]
            hold_days_list = [p["hold_days"] for p in completed_pairs]
            total_profit = sum(profits)
            win_count = len([p for p in profits if p > 0])

            stock_summary[code] = {
                "name": name,
                "total_trades": len(completed_pairs),
                "win_count": win_count,
                "loss_count": len(completed_pairs) - win_count,
                "total_profit": round(total_profit, 2),
                "total_fee": round(total_fee, 2),
                "net_profit": round(total_profit - total_fee, 2),
                "avg_return_pct": round(sum(returns) / len(returns), 2),
                "avg_hold_days": round(sum(hold_days_list) / len(hold_days_list), 1),
                "still_holding": still_holding,
            }
        elif still_holding > 0:
            stock_summary[code] = {
                "name": name,
                "total_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "total_profit": 0,
                "total_fee": round(total_fee, 2),
                "net_profit": round(-total_fee, 2),
                "avg_return_pct": 0,
                "avg_hold_days": 0,
                "still_holding": still_holding,
            }

        paired_trades.extend(completed_pairs)

    # 总汇总
    all_profits = [p["profit"] for p in paired_trades]
    all_returns = [p["return_pct"] for p in paired_trades]
    all_hold_days = [p["hold_days"] for p in paired_trades]
    total_fee_all = sum(s["total_fee"] for s in stock_summary.values())
    wins = [p for p in all_profits if p > 0]

    summary = {
        "total_trades": len(paired_trades),
        "win_count": len(wins),
        "loss_count": len(paired_trades) - len(wins),
        "win_rate": round(len(wins) / len(paired_trades) * 100, 1) if paired_trades else 0,
        "total_profit": round(sum(all_profits), 2),
        "total_fee": round(total_fee_all, 2),
        "net_profit": round(sum(all_profits) - total_fee_all, 2),
        "avg_return_pct": round(sum(all_returns) / len(all_returns), 2) if all_returns else 0,
        "max_profit_pct": round(max(all_returns), 2) if all_returns else 0,
        "max_loss_pct": round(min(all_returns), 2) if all_returns else 0,
        "avg_hold_days": round(sum(all_hold_days) / len(all_hold_days), 1) if all_hold_days else 0,
    }

    return {
        "trades": paired_trades,
        "by_stock": stock_summary,
        "summary": summary,
    }


def _empty_summary() -> dict:
    return {
        "total_trades": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0,
        "total_profit": 0,
        "total_fee": 0,
        "net_profit": 0,
        "avg_return_pct": 0,
        "max_profit_pct": 0,
        "max_loss_pct": 0,
        "avg_hold_days": 0,
    }
