"""全市场行业映射 repo (v1.7.598).

cfzy_sys_industry_map: 全A 代码→同花顺行业(三级, 如"电力设备-电池-电池化学品"),
由 industry_map_refresher 每周日问财刷新, 供板块共振·禁补仓提示算行业大跌占比。
"""
from backend.models.repo._db import _executemany, _fetchall, _fetchone


async def upsert_many(rows: list[tuple]) -> int:
    """批量 upsert。rows = [(code, industry), ...]。返回受影响行数。"""
    if not rows:
        return 0
    return await _executemany(
        "INSERT INTO cfzy_sys_industry_map (code, industry) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE industry=VALUES(industry)",
        rows,
    )


async def load_all() -> dict[str, str]:
    """全量 {code: industry}(板块共振判定一次要全市场, 直接整表拉)。"""
    rows = await _fetchall(
        "SELECT code, industry FROM cfzy_sys_industry_map WHERE industry<>''")
    return {str(r["code"]): str(r["industry"]) for r in rows}


async def count() -> int:
    r = await _fetchone("SELECT COUNT(*) AS cnt FROM cfzy_sys_industry_map")
    return int(r["cnt"]) if r else 0
