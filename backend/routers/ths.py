import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services import ths_importer
from backend.services.quote_refresher import refresh_quotes_for_codes
from backend import data_fetcher

router = APIRouter(prefix="/api/ths", tags=["ths"])


class ThsImportRequest(BaseModel):
    group_id: str


class ThsPathRequest(BaseModel):
    ths_path: str


@router.get("/groups")
async def list_groups(user: Annotated[dict, Depends(get_current_user)]):
    base_dir = await repository.get_user_ths_path(user["id"])
    xml_path = ths_importer.find_ths_file(base_dir=base_dir)
    if not xml_path:
        return {"ok": False, "msg": "未找到同花顺自选股文件，请先设置同花顺安装路径", "groups": [], "ths_path": base_dir}

    groups = ths_importer.parse_groups(xml_path)

    selfstock_path = ths_importer.find_selfstock_cache(base_dir=base_dir)
    if selfstock_path:
        codes = ths_importer.parse_selfstock_cache(selfstock_path)
        if codes:
            groups.insert(0, {"id": "__selfstock__", "name": "自选股", "count": len(codes)})

    return {
        "ok": True,
        "path": xml_path,
        "ths_path": base_dir,
        "groups": [{"id": g["id"], "name": g["name"], "count": g["count"]} for g in groups],
    }


@router.post("/import")
async def import_group(req: ThsImportRequest, user: Annotated[dict, Depends(get_current_user)]):
    base_dir = await repository.get_user_ths_path(user["id"])
    user_id = user["id"]

    if req.group_id == "__selfstock__":
        codes = _get_selfstock_codes(base_dir)
    else:
        codes = _get_group_codes(base_dir, req.group_id)

    if codes is None:
        return {"ok": False, "msg": "未找到对应分组"}
    if not codes:
        return {"ok": False, "msg": "分组为空"}

    async def stream():
        existing = await repository.list_stocks(user_id)
        existing_codes = {s["code"] for s in existing}
        total = len(codes)
        imported = 0
        skipped = 0

        for idx, code in enumerate(codes, 1):
            if code in existing_codes:
                skipped += 1
                yield _sse({"type": "progress", "current": idx, "total": total,
                            "code": code, "action": "skip"})
                continue

            name = ""
            results = await data_fetcher.search_stock(code)
            if results:
                name = results[0]["name"]

            await repository.add_stock(code, name, user_id=user_id, sort_order=idx)
            imported += 1
            yield _sse({"type": "progress", "current": idx, "total": total,
                        "code": code, "name": name, "action": "import"})

        if imported > 0:
            all_stocks = await repository.list_stocks(user_id)
            all_codes = [s["code"] for s in all_stocks]
            yield _sse({"type": "status", "msg": "正在刷新行情数据..."})
            await refresh_quotes_for_codes(all_codes)

        yield _sse({"type": "done", "imported": imported, "skipped": skipped,
                    "msg": f"导入完成: 新增 {imported} 只, 跳过 {skipped} 只"})

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _get_selfstock_codes(base_dir: str) -> list[str] | None:
    path = ths_importer.find_selfstock_cache(base_dir=base_dir)
    if not path:
        return None
    return ths_importer.parse_selfstock_cache(path)


def _get_group_codes(base_dir: str, group_id: str) -> list[str] | None:
    xml_path = ths_importer.find_ths_file(base_dir=base_dir)
    if not xml_path:
        return None
    groups = ths_importer.parse_groups(xml_path)
    for g in groups:
        if g["id"] == group_id:
            return g["codes"]
    return None


@router.post("/path")
async def save_ths_path(req: ThsPathRequest, user: Annotated[dict, Depends(get_current_user)]):
    new_path = req.ths_path.strip()
    await repository.update_user_ths_path(user["id"], new_path)
    return {"ok": True}


async def _build_compare(ths_codes: list[str], user_id: int, source: str = "") -> dict:
    """系统自选 ⇄ 给定同花顺自选代码集 双向对比。
    返回差集: ths_only(同花顺有系统缺, 可新增) / system_only(系统有同花顺缺, 可删除)。"""
    ths_codes = list(dict.fromkeys(ths_codes))   # 去重保序
    ths_set = set(ths_codes)

    # 系统侧: 剔除指数条目(同花顺自选主表只含个股, 指数会恒判"系统多出"成噪音)
    stocks = await repository.list_stocks(user_id)
    sys_rows = [s for s in stocks if s.get("trade_type") != "index"]
    sys_set = {s["code"] for s in sys_rows}
    name_of = {s["code"]: (s.get("name") or "") for s in sys_rows}

    both = ths_set & sys_set

    # 同花顺有、系统缺 → 服务端补名(差集通常很小)
    ths_only = []
    for code in ths_codes:
        if code in sys_set:
            continue
        name = ""
        try:
            results = await data_fetcher.search_stock(code)
            if results:
                name = results[0]["name"]
        except Exception:
            pass
        ths_only.append({"code": code, "name": name})

    # 系统有、同花顺缺 → 名称取自池
    system_only = [{"code": s["code"], "name": name_of.get(s["code"], "")}
                   for s in sys_rows if s["code"] not in ths_set]

    return {
        "ok": True,
        "source": source,
        "ths_count": len(ths_set),
        "system_count": len(sys_set),
        "both": len(both),
        "ths_only": ths_only,
        "system_only": system_only,
    }


@router.get("/compare")
async def compare_selfstock(user: Annotated[dict, Depends(get_current_user)]):
    """[本地后端用] 按服务器路径找同花顺自选主表对比。云端读不到本地文件, 走 /compare-upload。"""
    base_dir = await repository.get_user_ths_path(user["id"])
    cache_path = ths_importer.find_selfstock_cache(base_dir=base_dir)
    if not cache_path:
        return {"ok": False, "msg": "未找到同花顺自选股缓存文件，请先设置同花顺安装路径"}
    ths_codes = ths_importer.parse_selfstock_cache(cache_path)
    return await _build_compare(ths_codes, user["id"], source="SelfStockCache.json")


@router.post("/compare-upload")
async def compare_upload(file: UploadFile = File(...),
                         user: dict = Depends(get_current_user)):
    """上传同花顺自选文件(SelfStockCache.json 或 blockstockV3.xml)做对比。
    云端不依赖服务器本地路径, 由浏览器把本地文件传上来解析。"""
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return {"ok": False, "msg": "文件过大(>5MB), 请确认是同花顺自选文件"}
    codes, source = ths_importer.extract_codes_from_upload(content)
    if not codes:
        return {"ok": False, "msg": "未从文件中解析到A股自选代码, 请上传 SelfStockCache.json 或 blockstockV3.xml"}
    return await _build_compare(codes, user["id"], source=source)
