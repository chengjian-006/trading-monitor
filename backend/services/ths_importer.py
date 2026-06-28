import glob
import logging
import os
import xml.etree.ElementTree as ET

from backend import data_fetcher

logger = logging.getLogger(__name__)

A_SHARE_MARKETS = {"USHA", "USHT", "USZA"}
SKIP_MARKETS = {"UHKI", "UHKM", "URFI", "UCMS", "UGFF"}

DEFAULT_SEARCH_PATTERNS = [
    r"D:\Program Files\同花顺远航版\bin\users\*\blockstockV3.xml",
    r"D:\Program Files\同花顺\mo_*\custom_block\__base_\download\*",
]



def _patterns_from_base(base_dir: str) -> list[str]:
    base = base_dir.rstrip("/\\")
    return [os.path.join(base, "bin", "users", "*", "blockstockV3.xml")]


def find_ths_file(custom_path: str = "", base_dir: str = "") -> str | None:
    if custom_path and os.path.isfile(custom_path):
        return custom_path

    if base_dir:
        patterns = _patterns_from_base(base_dir)
    else:
        patterns = DEFAULT_SEARCH_PATTERNS

    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

    return None


def find_selfstock_cache(base_dir: str = "") -> str | None:
    if base_dir:
        base = base_dir.rstrip("/\\")
        patterns = [os.path.join(base, "bin", "users", "*", "SelfStockCache.json")]
    else:
        patterns = [r"D:\Program Files\同花顺远航版\bin\users\*\SelfStockCache.json"]

    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def parse_selfstock_cache(json_path: str) -> list[str]:
    """解析 SelfStockCache.json，返回 A 股代码列表"""
    import json as _json
    try:
        with open(json_path, encoding="utf-8") as f:
            data = _json.load(f)
        stocks_str = data.get("Data", {}).get("Selfstock", "")
        if not stocks_str:
            return []
        all_codes = stocks_str.split("|")
        return [c for c in all_codes if len(c) == 6 and c[0] in "036"]
    except Exception as e:
        logger.error(f"Failed to parse SelfStockCache.json: {e}")
        return []


def parse_selfstock_cache_text(text: str) -> list[str]:
    """从 SelfStockCache.json 文件内容(非路径)解析 A 股代码列表。供浏览器上传对比用。"""
    import json as _json
    try:
        data = _json.loads(text)
        stocks_str = data.get("Data", {}).get("Selfstock", "")
        if not stocks_str:
            return []
        return [c for c in stocks_str.split("|") if len(c) == 6 and c[0] in "036"]
    except Exception as e:
        logger.error(f"Failed to parse SelfStockCache content: {e}")
        return []


def extract_codes_from_upload(content: bytes) -> tuple[list[str], str]:
    """从上传的同花顺文件字节解析 A 股代码 + 来源标签。
    自动识别 SelfStockCache.json(自选主表) 或 blockstockV3.xml(全部分组合并去重)。
    只取数字代码, 中文名编码问题不影响(用 errors='ignore' 解码)。"""
    text = content.decode("utf-8", errors="ignore").lstrip("﻿").strip()
    if not text:
        return [], ""
    if text[:1] == "{":
        return parse_selfstock_cache_text(text), "SelfStockCache.json"
    if text[:1] == "<":
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.error(f"Failed to parse THS XML content: {e}")
            return [], ""
        seen: dict[str, None] = {}
        for sec in root.findall(".//security"):
            market = sec.get("market", "")
            code = sec.get("code", "")
            if market in A_SHARE_MARKETS and code:
                seen.setdefault(code.zfill(6), None)
        return list(seen.keys()), "blockstockV3.xml(全部分组)"
    # 兜底: 试 JSON
    codes = parse_selfstock_cache_text(text)
    return codes, "SelfStockCache.json" if codes else ""


def parse_groups(xml_path: str) -> list[dict]:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        logger.error(f"Failed to parse THS XML: {e}")
        return []

    root = tree.getroot()
    groups = []

    for block in root.findall(".//Block"):
        group_id = block.get("id", "")
        name = block.get("name", "")

        a_share_codes = []
        for sec in block.findall("security"):
            market = sec.get("market", "")
            code = sec.get("code", "")
            if market in A_SHARE_MARKETS and code:
                a_share_codes.append(code.zfill(6))

        groups.append({
            "id": group_id,
            "name": name,
            "count": len(a_share_codes),
            "codes": a_share_codes,
        })

    return groups


async def import_selfstock(user_id: int = 1, base_dir: str = "") -> dict:
    from backend.models import repository

    cache_path = find_selfstock_cache(base_dir=base_dir)
    if not cache_path:
        return {"ok": False, "msg": "未找到自选股缓存文件"}

    codes = parse_selfstock_cache(cache_path)
    if not codes:
        return {"ok": False, "msg": "自选股为空"}

    existing = await repository.list_stocks(user_id)
    existing_codes = {s["code"] for s in existing}

    imported = 0
    skipped = 0

    for idx, code in enumerate(codes, 1):
        if code in existing_codes:
            skipped += 1
            continue

        name = ""
        results = await data_fetcher.search_stock(code)
        if results:
            name = results[0]["name"]

        await repository.add_stock(code, name, user_id=user_id, sort_order=idx)
        imported += 1

    return {
        "ok": True,
        "msg": f"导入完成: 新增 {imported} 只, 跳过 {skipped} 只 (已存在)",
        "imported": imported,
        "skipped": skipped,
        "group_name": "自选股",
    }


async def import_group(xml_path: str, group_id: str, user_id: int = 1) -> dict:
    from backend.models import repository

    groups = parse_groups(xml_path)
    target = None
    for g in groups:
        if g["id"] == group_id:
            target = g
            break

    if not target:
        return {"ok": False, "msg": f"分组 {group_id} 不存在"}

    existing = await repository.list_stocks(user_id)
    existing_codes = {s["code"] for s in existing}

    imported = 0
    skipped = 0

    for idx, code in enumerate(target["codes"], 1):
        if code in existing_codes:
            skipped += 1
            continue

        name = ""
        results = await data_fetcher.search_stock(code)
        if results:
            name = results[0]["name"]

        await repository.add_stock(code, name, user_id=user_id, sort_order=idx)
        imported += 1

    return {
        "ok": True,
        "msg": f"导入完成: 新增 {imported} 只, 跳过 {skipped} 只 (已存在)",
        "imported": imported,
        "skipped": skipped,
        "group_name": target["name"],
    }
