"""交易分析 API — 导入交割单并分析盈亏，同步持仓状态"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.trade_analyzer import (
    parse_trades_text,
    parse_history_text,
    parse_trades_excel,
    analyze_trades,
)
from backend.services.trade_model_compare import compare_trades_to_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trade-analysis", tags=["trade-analysis"])

_rebuild_tasks: set = set()   # 强引用防 create_task 被 GC 中途回收


def _schedule_rounds_rebuild(user_id: int) -> None:
    import asyncio
    from backend.services.trade_round_builder import rebuild_user_rounds

    async def _run():
        try:
            n = await rebuild_user_rounds(user_id)
            logger.info(f"[rounds] 导入后台重建完成 user={user_id}, {n} 回合")
        except Exception as e:
            logger.warning(f"[rounds] 导入后台重建失败 user={user_id}: {e}")

    task = asyncio.create_task(_run())
    _rebuild_tasks.add(task)
    task.add_done_callback(_rebuild_tasks.discard)


class ImportTextRequest(BaseModel):
    text: str


class ImportHistoryRequest(BaseModel):
    text: str
    trade_date: str   # YYYY-MM-DD, 前端日期选择器


def _db_rows_to_trades(rows: list[dict]) -> list[dict]:
    """将数据库查询结果转换为 analyze_trades 所需格式"""
    trades = []
    for r in rows:
        trade_date = r["trade_date"]
        if isinstance(trade_date, str):
            from datetime import datetime
            trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
        trades.append({
            "trade_date": trade_date,
            "trade_time": r["trade_time"] or "",
            "code": r["code"],
            "name": r["name"] or "",
            "direction": r["direction"],
            "quantity": int(r["quantity"]),
            "price": float(r["price"]),
            "amount": float(r["amount"]),
            "fee": float(r["fee"] or 0),
            "stamp_tax": float(r["stamp_tax"] or 0),
            "transfer_fee": float(r["transfer_fee"] or 0),
            "net_amount": float(r["net_amount"] or 0),
        })
    return trades


@router.get("/upload-status")
async def upload_status(user: Annotated[dict, Depends(get_current_user)]):
    """登录提示用: 今日是否已上传交割单 + 是否该提醒。
    提醒条件: 工作日 + 今日未上传 + 已过 21:00(晚上才提醒, 白天可能稍后再传)。
    """
    from datetime import datetime
    from backend.core.trading_calendar import is_workday
    uploaded = await repository.has_import_today(user["id"])
    last = await repository.get_latest_import_time(user["id"])
    now = datetime.now()
    should_remind = (not uploaded) and is_workday() and now.hour >= 21
    return {
        "uploaded_today": uploaded,
        "should_remind": should_remind,
        "last_import": str(last) if last else None,
    }


async def _import_and_analyze(user_id: int, new_trades: list[dict]) -> dict:
    """存入新记录，基于全量数据分析，同步持仓"""
    new_count = await repository.save_trade_records(user_id, new_trades)

    all_rows = await repository.get_all_trade_records(user_id)
    all_trades = _db_rows_to_trades(all_rows)

    result = analyze_trades(all_trades)

    holdings = {}
    for code, info in result["by_stock"].items():
        if info["still_holding"] > 0:
            holdings[code] = {"name": info["name"], "quantity": info["still_holding"]}
    await repository.sync_positions_from_trades(user_id, holdings)

    # 自动归位: 交割单是持仓真相。若某票导入后仍/又持有(holding>0), 撤销其「已卖出」手动标记,
    # 让它重回持仓被跟踪(处理「先手点已卖出→后来又买回并导入」)。真卖掉的(holding=0)标记留着无害。
    try:
        from backend.models.repo import push_pref as pref_repo
        for code in holdings:
            await pref_repo.revoke_kind(user_id, "mark_sold", code)
    except Exception as e:
        logger.warning(f"[trade_analysis] 撤销已卖出标记失败: {e}")

    # 导入后重建交易回合改后台执行: 逐股串行写远程库要40秒+, 同步做会把导入请求
    # 拖到超过前端超时(误报"请求失败"); 回合只供收益分析页, 晚几十秒就绪可接受
    _schedule_rounds_rebuild(user_id)

    # 原始成交流水(按时间倒序), 给前端"交易清单"核对最新导入是否正确
    records = [
        {
            "trade_date": str(t["trade_date"]),
            "trade_time": t.get("trade_time") or "",
            "code": t["code"],
            "name": t["name"],
            "direction": t["direction"],
            "quantity": t["quantity"],
            "price": t["price"],
            "amount": t["amount"],
            "fee": round(float(t.get("fee") or 0) + float(t.get("stamp_tax") or 0)
                         + float(t.get("transfer_fee") or 0), 2),
            "net_amount": t.get("net_amount") or 0,
        }
        for t in all_trades
    ]
    records.sort(key=lambda r: (r["trade_date"], r["trade_time"]), reverse=True)

    logger.info(
        f"[trade_analysis] user={user_id} imported {new_count} new records "
        f"(total {len(all_trades)}), {len(holdings)} holdings synced"
    )
    return {"new_count": new_count, "total_count": len(all_trades), "records": records, **result}


@router.post("/import-text")
async def import_text(
    req: ImportTextRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    trades = parse_trades_text(req.text)
    if not trades:
        return {"ok": False, "msg": "未识别到有效的交易记录。请连同表头行(成交日期 成交时间 证券代码 …)一起粘贴; 支持交割单与历史成交两种格式。"}

    result = await _import_and_analyze(user["id"], trades)
    return {"ok": True, "record_count": result["total_count"], **result}


@router.post("/import-history")
async def import_history(
    req: ImportHistoryRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """历史成交导入(平安证券「历史成交」格式, 无日期列)。日期由 trade_date 注入;
    替换该日: 先删该用户该日旧成交再写入, 防与交割单同日双重计数。"""
    from datetime import datetime
    try:
        d = datetime.strptime(req.trade_date.strip(), "%Y-%m-%d").date()
    except ValueError:
        return {"ok": False, "msg": "日期格式不对, 请用日期选择器选定成交日期。"}

    trades = parse_history_text(req.text, d)
    if not trades:
        return {"ok": False, "msg": "未识别到有效的历史成交记录。请连同表头行(成交时间 证券代码 操作 …)一起粘贴。"}

    deleted = await repository.delete_trades_on_date(user["id"], d)
    logger.info(f"[trade_analysis] user={user['id']} 历史成交替换该日 {d}: 清旧 {deleted} 行, 写入 {len(trades)} 行")
    result = await _import_and_analyze(user["id"], trades)
    return {"ok": True, "record_count": result["total_count"], **result}


@router.post("/import-excel")
async def import_excel(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        return {"ok": False, "msg": "文件过大，请上传10MB以内"}

    trades = parse_trades_excel(content)
    if not trades:
        return {"ok": False, "msg": "未识别到有效的交易记录"}

    result = await _import_and_analyze(user["id"], trades)
    return {"ok": True, "record_count": result["total_count"], **result}


@router.get("/compare")
async def compare(
    user: Annotated[dict, Depends(get_current_user)],
    signal_window: int = 5,
):
    """实盘交割单 vs 模型买卖点 对比 (基于已导入的全量交割单, 回测重跑检测器)。"""
    signal_window = max(1, min(int(signal_window), 15))
    return await compare_trades_to_model(user["id"], signal_window)


@router.get("/rounds")
async def list_rounds(
    user: Annotated[dict, Depends(get_current_user)],
    status: str | None = None,
    limit: int = 500,
):
    """交易回合列表 + 执行质量汇总 (v1.7.685 起回合表才有 MFE/MAE, 此前恒 NULL)。

    执行质量三件套(口径见 trade_round_builder.attach_excursions):
      入场效率 = MFE / (MFE + |MAE|)   低 → 买点位置偏(买完就套)
      出场效率 = 已实现收益 / MFE      低 → 拿不住/砍太早, 行业基准 65-80% 为健康
      持仓时长比 = 亏损单均持仓 / 盈利单均持仓   >1 → 截断利润、让亏损奔跑
    """
    from backend.models.repo.trade_rounds import get_rounds, get_round_legs

    rows = await get_rounds(user["id"], status, limit=max(1, min(int(limit), 2000)))
    legs = await get_round_legs([r["id"] for r in rows])
    for r in rows:
        r["legs"] = legs.get(r["id"], [])
    return {"rounds": rows, "summary": _round_summary(rows)}


def _round_summary(rows: list[dict]) -> dict:
    """回合列表 → 执行质量汇总。样本不足的指标返回 None 而不是 0, 避免看着像"效率0%"。"""
    closed = [r for r in rows if r.get("status") == "closed"]
    graded = [r for r in closed
              if r.get("mfe_pct") is not None and r.get("mae_pct") is not None]

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    # 入场效率: MFE / (MFE + |MAE|), 只对 MFE>0 的回合算(买完就没涨过的没有"效率"可言)
    entry_eff = _avg([
        r["mfe_pct"] / (r["mfe_pct"] + abs(r["mae_pct"])) * 100
        for r in graded
        if (r["mfe_pct"] or 0) > 0 and (r["mfe_pct"] + abs(r["mae_pct"])) > 0
    ])
    # 出场效率: 只对盈利单算(亏损单的"吃到多少浮盈"无意义)
    exit_eff = _avg([
        r["realized_pnl_pct"] / r["mfe_pct"] * 100
        for r in graded
        if (r.get("realized_pnl_pct") or 0) > 0 and (r["mfe_pct"] or 0) > 0
    ])
    win_hold = [r["holding_days"] for r in closed
                if (r.get("realized_pnl_pct") or 0) > 0 and r.get("holding_days")]
    loss_hold = [r["holding_days"] for r in closed
                 if (r.get("realized_pnl_pct") or 0) <= 0 and r.get("holding_days")]
    avg_win_hold = _avg(win_hold)
    avg_loss_hold = _avg(loss_hold)
    hold_ratio = (round(avg_loss_hold / avg_win_hold, 2)
                  if avg_win_hold and avg_loss_hold else None)

    # 三区分布(对标 TradesViz MFE-vs-PnL 散点): 吃满 / 落袋太早 / 浮盈坐成亏损
    good = sum(1 for r in graded if (r.get("realized_pnl_pct") or 0) > 0
               and (r["mfe_pct"] or 0) > 0
               and r["realized_pnl_pct"] >= r["mfe_pct"] * 0.6)
    early = sum(1 for r in graded if (r.get("realized_pnl_pct") or 0) > 0
                and (r["mfe_pct"] or 0) > 0
                and r["realized_pnl_pct"] < r["mfe_pct"] * 0.6)
    gaveback = sum(1 for r in graded if (r.get("realized_pnl_pct") or 0) <= 0
                   and (r["mfe_pct"] or 0) >= 3)
    return {
        "total": len(rows), "closed": len(closed), "graded": len(graded),
        "entry_efficiency": entry_eff, "exit_efficiency": exit_eff,
        "avg_win_holding_days": avg_win_hold, "avg_loss_holding_days": avg_loss_hold,
        "holding_ratio": hold_ratio,
        "avg_mfe_pct": _avg([r.get("mfe_pct") for r in graded]),
        "avg_mae_pct": _avg([r.get("mae_pct") for r in graded]),
        "zone_good": good, "zone_sold_early": early, "zone_gave_back": gaveback,
    }
