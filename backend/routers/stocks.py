import asyncio
import base64
import logging
from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field

from backend.core.auth import get_current_user
from backend.core.config import load_config
from backend.models import repository
from backend import data_fetcher
from backend.services.quote_refresher import refresh_quotes_for_codes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


class StockAddRequest(BaseModel):
    code: str
    name: str = ""
    trade_type: str = "short"
    status: str = "watch"


class StockUpdateRequest(BaseModel):
    trade_type: Optional[str] = None
    status: Optional[str] = None
    focused: Optional[int] = None
    strategy: Optional[str] = None
    grp: Optional[str] = None       # 分组 (v1.7.670)
    tags: Optional[str] = None      # 标签, 逗号分隔
    note: Optional[str] = None      # 备注


class StockReorderRequest(BaseModel):
    codes: list[str]


# v1.7.643: pct_5d 基准价当日进程内缓存 — 「5个交易日前收盘」一整天不变, 原来盘中每3秒的
# /api/stocks 轮询都对全池(~187码)拉20天K线(跨云RDS大结果集), 纯属重复劳动。
# 缓存 {code: (ref_close, last_close)}, 日期翻转整体作废; 当日只有缓存缺码(新加票)才查库。
_PCT5D_CACHE: dict = {"date": "", "refs": {}}


async def _attach_pct_5d(stocks: list[dict]) -> None:
    """给每只票算 5 日涨幅 = (最新价 - 5个交易日前收盘) / 5个交易日前收盘 ×100。
    5个交易日前收盘 取自 K线缓存(只为股票池保温, 必有数据)中早于今天的第5根。"""
    codes = [s["code"] for s in stocks if s.get("code")]
    if not codes:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    if _PCT5D_CACHE["date"] != today:
        _PCT5D_CACHE["date"] = today
        _PCT5D_CACHE["refs"] = {}
    refs: dict = _PCT5D_CACHE["refs"]

    missing = [c for c in codes if c not in refs]
    if missing:
        min_td = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        try:
            kmap = await repository.fetch_kline_cache_for_codes(missing, min_td)
        except Exception as e:
            logger.warning(f"[pct_5d] K线缓存取数失败: {e}")
            kmap = None
        if kmap is not None:
            for c in missing:
                prev = [k for k in (kmap.get(c) or []) if str(k["trade_date"]) < today]  # 排除今天
                if len(prev) >= 5:
                    refs[c] = (float(prev[-5].get("close") or 0),   # 5个交易日前收盘
                               float(prev[-1].get("close") or 0))   # 最近收盘(price缺失时兜底)
                else:
                    refs[c] = (0.0, 0.0)   # 数据不足也缓存, 防每次轮询重查

    for s in stocks:
        s["pct_5d"] = None
        ref, last_close = refs.get(s["code"]) or (0.0, 0.0)
        price = float(s.get("price") or 0) or last_close
        if ref > 0 and price > 0:
            s["pct_5d"] = round((price - ref) / ref * 100, 2)


@router.get("")
async def list_stocks(user: Annotated[dict, Depends(get_current_user)]):
    stocks = await repository.list_stocks(user["id"])
    await _attach_pct_5d(stocks)
    return stocks


@router.post("")
async def add_stock(req: StockAddRequest, user: Annotated[dict, Depends(get_current_user)]):
    code = req.code.strip().zfill(6)
    name = req.name
    if not name:
        results = await data_fetcher.search_stock(code)
        if results:
            name = results[0]["name"]
    await repository.add_stock(code, name, req.trade_type, req.status, user["id"])
    await refresh_quotes_for_codes([code])
    return {"ok": True, "code": code, "name": name}


@router.post("/reorder")
async def reorder_stocks(req: StockReorderRequest, user: Annotated[dict, Depends(get_current_user)]):
    """股票池手动拖拽排序: 按 codes 顺序写 sort_order。"""
    codes = [c.strip().zfill(6) for c in (req.codes or []) if c.strip()]
    await repository.batch_update_sort_order(user["id"], codes)
    return {"ok": True, "count": len(codes)}


@router.put("/{code}")
async def update_stock(code: str, req: StockUpdateRequest, user: Annotated[dict, Depends(get_current_user)]):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True}
    await repository.update_stock(code, user["id"], **updates)
    return {"ok": True}


@router.delete("/{code}")
async def delete_stock(code: str, user: Annotated[dict, Depends(get_current_user)]):
    """逻辑删除: 出池不可见, 但历史信号与回测仍保留。"""
    await repository.remove_stock(code, user["id"])
    await repository.add_log(user["id"], user["username"], "delete_stock", code)
    return {"ok": True}


@router.delete("/{code}/purge")
async def purge_stock(code: str, user: Annotated[dict, Depends(get_current_user)]):
    """物理删除: 连历史信号一起清除, 不可恢复。用于误加的票。"""
    await repository.purge_stock(code, user["id"])
    await repository.add_log(user["id"], user["username"], "purge_stock", code)
    return {"ok": True}


@router.post("/ocr-recognize")
async def ocr_recognize(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """上传自选股截图，AI识别股票代码和名称"""
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        return {"error": "文件过大，请上传10MB以内的图片"}

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    mime = mime_map.get(ext, "image/png")
    b64 = base64.b64encode(content).decode()

    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        return {"error": "未配置AI API密钥"}

    from openai import OpenAI
    base_url = cfg.get("ai_base_url", "https://api.deepseek.com/v1")
    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt = (
        "请从这张股票软件截图中识别出所有A股股票。"
        "返回JSON数组格式，每个元素包含code(6位股票代码)和name(股票名称)。"
        "只返回JSON数组，不要其他文字。示例: [{\"code\":\"000001\",\"name\":\"平安银行\"}]"
    )

    try:
        # 同步 OpenAI 客户端卸到线程, 否则视觉模型 10~60s 会冻住整个事件循环、拖慢全站所有请求。
        # 显式 timeout 兜底, 防外部 API 挂死。
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=cfg.get("ai_vision_model", "qwen/qwen3-vl-235b-a22b-instruct"),
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            timeout=90,
        )
        result_text = response.choices[0].message.content.strip()
        # 提取JSON部分
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if result_text.startswith("["):
            import json
            stocks = json.loads(result_text)
        else:
            start = result_text.find("[")
            end = result_text.rfind("]") + 1
            if start >= 0 and end > start:
                import json
                stocks = json.loads(result_text[start:end])
            else:
                return {"error": "识别结果格式异常", "raw": result_text}

        for s in stocks:
            s["code"] = str(s.get("code", "")).zfill(6)
        return {"stocks": stocks}
    except Exception as e:
        logger.error(f"OCR recognize failed: {e}")
        return {"error": f"识别失败: {str(e)}"}


class BatchDeleteRequest(BaseModel):
    # 上限防几千条逐条 DB 往返(单worker+跨云44ms)打爆连接池、拖垮实时行情
    codes: list[str] = Field(..., max_length=1000)


@router.post("/batch-delete")
async def batch_delete(req: BatchDeleteRequest, user: Annotated[dict, Depends(get_current_user)]):
    """批量逻辑删除(出池留历史)。用于自选对比里"系统有·同花顺缺"的一键剔除。"""
    deleted = 0
    for raw in req.codes:
        code = str(raw or "").strip().zfill(6)
        if not code or len(code) != 6:
            continue
        await repository.remove_stock(code, user["id"])
        deleted += 1
    if deleted > 0:
        await repository.add_log(user["id"], user["username"], "batch_delete_stock",
                                 ",".join(str(c).strip().zfill(6) for c in req.codes))
    return {"ok": True, "deleted": deleted}


class BatchImportRequest(BaseModel):
    stocks: list[dict] = Field(..., max_length=1000)   # 同 batch-delete: 防无界批量逐条往返


@router.post("/batch-import")
async def batch_import(req: BatchImportRequest, user: Annotated[dict, Depends(get_current_user)]):
    """批量导入确认后的股票"""
    success = 0
    for item in req.stocks:
        code = str(item.get("code", "")).strip().zfill(6)
        name = item.get("name", "")
        if not code or len(code) != 6:
            continue
        await repository.add_stock(code, name, "short", "watch", user["id"])
        success += 1
    if success > 0:
        all_stocks = await repository.list_stocks(user["id"])
        all_codes = [s["code"] for s in all_stocks]
        await refresh_quotes_for_codes(all_codes)
    return {"ok": True, "success": success, "total": len(req.stocks)}
