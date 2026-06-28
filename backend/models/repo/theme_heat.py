"""市场情绪温度表(题材热度) CRUD - cfzy_sys_theme_heat 表.

按 日期×题材 记当日各涨停题材的涨停家数。save_theme_heat 整日幂等覆盖,
get_theme_heat 取最近 N 个交易日的全部题材行(供前端拼日期×题材矩阵)。
"""
from backend.models.repo._db import _execute, _fetchall


async def save_theme_heat(trade_date: str, rows: list[tuple]) -> None:
    """整日覆盖写入。rows: [(theme, count, sample_codes_str), ...]。先删当日再插, 幂等。"""
    await _execute("DELETE FROM cfzy_sys_theme_heat WHERE trade_date = %s", (trade_date,))
    for theme, count, sample in rows:
        await _execute(
            "INSERT INTO cfzy_sys_theme_heat (trade_date, theme, limit_up_count, sample_codes) "
            "VALUES (%s, %s, %s, %s)",
            (trade_date, theme, int(count), sample or ""),
        )


async def get_theme_heat(days: int = 15) -> list[dict]:
    """取最近 N 个交易日(有数据的日期)的全部题材行, 按日期升序。"""
    return await _fetchall(
        "SELECT trade_date, theme, limit_up_count, sample_codes FROM cfzy_sys_theme_heat "
        "WHERE trade_date IN ("
        "  SELECT trade_date FROM ("
        "    SELECT DISTINCT trade_date FROM cfzy_sys_theme_heat ORDER BY trade_date DESC LIMIT %s"
        "  ) t"
        ") ORDER BY trade_date ASC, limit_up_count DESC",
        (days,),
    )
