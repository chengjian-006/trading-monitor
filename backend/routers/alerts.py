"""股票池自定义预警 API - cfzy_biz_stock_alerts.

一条预警 = 内部多条件 AND; 一只股票可挂多条。条件维度:
  price    {dim:'price', op:'gte'|'lte', value}
  pct      {dim:'pct',   op:'gte'|'lte', value}        当日涨跌幅%
  ma_near  {dim:'ma_near', ma:5|10|20|60, band:0.1~10} 现价进入 MA±band% 带内
  ma_cross {dim:'ma_cross', ma:5|10|20|60, dir:'up'|'down'}
"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stocks", tags=["alerts"])

_VALID_MA = {5, 10, 20, 60}
_VALID_OP = {"gte", "lte"}
_VALID_DIR = {"up", "down"}


def _validate_conditions(conditions: list) -> list:
    """校验并归一化条件数组; 非法抛 422。"""
    if not isinstance(conditions, list) or not conditions:
        raise HTTPException(422, "至少需要一个预警条件")
    if len(conditions) > 6:
        raise HTTPException(422, "单条预警最多 6 个条件")
    out = []
    for c in conditions:
        if not isinstance(c, dict):
            raise HTTPException(422, "条件格式错误")
        dim = c.get("dim")
        if dim in ("price", "pct"):
            if c.get("op") not in _VALID_OP:
                raise HTTPException(422, f"{dim} 运算符必须是 gte/lte")
            try:
                value = float(c.get("value"))
            except (TypeError, ValueError):
                raise HTTPException(422, f"{dim} 阈值必须是数字")
            out.append({"dim": dim, "op": c["op"], "value": value})
        elif dim == "ma_near":
            if c.get("ma") not in _VALID_MA:
                raise HTTPException(422, "均线必须是 5/10/20/60")
            try:
                band = float(c.get("band"))
            except (TypeError, ValueError):
                raise HTTPException(422, "贴线带必须是数字")
            if not (0.1 <= band <= 10):
                raise HTTPException(422, "贴线带需在 0.1~10% 之间")
            out.append({"dim": "ma_near", "ma": int(c["ma"]), "band": band})
        elif dim == "ma_cross":
            if c.get("ma") not in _VALID_MA:
                raise HTTPException(422, "均线必须是 5/10/20/60")
            if c.get("dir") not in _VALID_DIR:
                raise HTTPException(422, "方向必须是 up/down")
            out.append({"dim": "ma_cross", "ma": int(c["ma"]), "dir": c["dir"]})
        else:
            raise HTTPException(422, f"未知维度: {dim}")
    return out


class AlertCreateRequest(BaseModel):
    conditions: list
    note: Optional[str] = ""
    enabled: Optional[int] = 1


# 均线快捷提醒预设: 碰线±0.5%即报, 每股每档每天最多一次(repeat_daily), 次日自动恢复
_PRESETS = {
    "ma10": {"ma": 10, "label": "10日线提醒"},
    "ma20": {"ma": 20, "label": "20日线提醒"},
    "ma60": {"ma": 60, "label": "60日线提醒"},
}
_PRESET_BAND = 0.5


class PresetToggleRequest(BaseModel):
    preset: str      # 'ma10' | 'ma20' | 'ma60'
    on: bool


class AlertUpdateRequest(BaseModel):
    conditions: Optional[list] = None
    note: Optional[str] = None
    enabled: Optional[int] = None
    status: Optional[str] = None   # 'active' 即重启


@router.get("/alerts")
async def list_all_alerts(user: Annotated[dict, Depends(get_current_user)]):
    """当前用户全部预警(供股票池汇总打标)。"""
    return await repository.list_alerts(user["id"])


@router.get("/{code}/alerts")
async def list_stock_alerts(code: str, user: Annotated[dict, Depends(get_current_user)]):
    return await repository.list_alerts_by_code(user["id"], code.strip().zfill(6))


@router.post("/{code}/alerts/preset")
async def toggle_preset_alert(code: str, req: PresetToggleRequest,
                              user: Annotated[dict, Depends(get_current_user)]):
    """均线快捷提醒一键开关: 开=建一条 ma_near±0.5% 每日一次的预警, 关=删掉。幂等。"""
    from backend.models.repo import alerts as alerts_repo
    if req.preset not in _PRESETS:
        raise HTTPException(422, "preset 只能是 ma10/ma20/ma60")
    code = code.strip().zfill(6)
    meta = _PRESETS[req.preset]
    existing = await alerts_repo.get_preset_alert(user["id"], code, req.preset)
    if req.on:
        if existing:
            # 已有: 保证是启用+active(曾被暂停/触发的也拉回来)
            await alerts_repo.update_alert(user["id"], existing["id"], enabled=1, status="active")
            return {"ok": True, "id": existing["id"]}
        alert_id = await alerts_repo.create_alert(
            user["id"], code,
            conditions=[{"dim": "ma_near", "ma": meta["ma"], "band": _PRESET_BAND}],
            note=meta["label"], preset=req.preset, repeat_daily=1,
        )
        return {"ok": True, "id": alert_id}
    if existing:
        await alerts_repo.delete_preset_alert(user["id"], code, req.preset)
    return {"ok": True}


@router.post("/{code}/alerts")
async def create_stock_alert(code: str, req: AlertCreateRequest,
                             user: Annotated[dict, Depends(get_current_user)]):
    conditions = _validate_conditions(req.conditions)
    alert_id = await repository.create_alert(
        user["id"], code.strip().zfill(6), conditions,
        note=(req.note or "").strip()[:100], enabled=1 if req.enabled is None else int(req.enabled),
    )
    return {"ok": True, "id": alert_id}


@router.put("/alerts/{alert_id}")
async def update_stock_alert(alert_id: int, req: AlertUpdateRequest,
                             user: Annotated[dict, Depends(get_current_user)]):
    existing = await repository.get_alert(user["id"], alert_id)
    if not existing:
        raise HTTPException(404, "预警不存在")
    conditions = _validate_conditions(req.conditions) if req.conditions is not None else None
    status = req.status
    if status is not None and status not in ("active", "triggered"):
        raise HTTPException(422, "status 只能是 active/triggered")
    await repository.update_alert(
        user["id"], alert_id,
        conditions=conditions,
        note=None if req.note is None else req.note.strip()[:100],
        enabled=None if req.enabled is None else int(req.enabled),
        status=status,
    )
    return {"ok": True}


@router.delete("/alerts/{alert_id}")
async def delete_stock_alert(alert_id: int, user: Annotated[dict, Depends(get_current_user)]):
    existing = await repository.get_alert(user["id"], alert_id)
    if not existing:
        raise HTTPException(404, "预警不存在")
    await repository.delete_alert(user["id"], alert_id)
    return {"ok": True}
