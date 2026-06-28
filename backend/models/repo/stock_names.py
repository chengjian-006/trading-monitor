"""全市场股票名称字典 repo - v1.7.x.

cfzy_sys_stock_names: 全A 代码→名称, 由 refresh_stock_names 定时刷新.
模型回测逐笔明细给全市场票补名(自选股池只覆盖少量), backtester_5m.load_names 优先读本表.
"""
from backend.models.repo._db import _executemany, _fetchall, _fetchone


async def upsert_many(rows: list[tuple]) -> int:
    """批量 upsert 名称。rows = [(code, name), ...]。返回受影响行数。"""
    if not rows:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_sys_stock_names (code, name) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE name=VALUES(name)",
        rows,
    )


async def get_names(codes: list[str]) -> dict:
    """代码→名称 (分批 IN 查询)。"""
    out: dict[str, str] = {}
    if not codes:
        return out
    for k in range(0, len(codes), 500):
        part = codes[k:k + 500]
        ph = ",".join(["%s"] * len(part))
        rows = await _fetchall(
            f"SELECT code, name FROM cfzy_sys_stock_names WHERE code IN ({ph}) AND name<>''",
            tuple(part))
        for r in rows:
            out[str(r["code"])] = str(r["name"])
    return out


async def count() -> int:
    r = await _fetchone("SELECT COUNT(*) AS cnt FROM cfzy_sys_stock_names")
    return int(r["cnt"]) if r else 0
