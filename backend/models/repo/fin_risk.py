"""自选股财务红旗快照/去重台账 CRUD - cfzy_biz_fin_risk 表(v1.7.x, 黑天鹅预警二期).

  get_fin_risk      — 读某票当前快照(取 pushed_key 做去重判定)
  upsert_fin_risk   — 每票一行 UPSERT(存最新报告期/风险分/红旗明细/已推命中键)
  list_fin_risk     — 风险分倒序列表(给接口/复盘用, 可选)

去重: pushed_key=达门槛时的命中组合键; 命中组合不变不重推(扫描层比对)。
"""
from backend.models.repo._db import _execute, _fetchall, _fetchone


async def get_fin_risk(code: str) -> dict | None:
    return await _fetchone(
        "SELECT * FROM cfzy_biz_fin_risk WHERE code = %s", (code,))


async def upsert_fin_risk(code: str, name: str, report_year: str, score: int,
                          flags_json: str, pushed_key: str):
    await _execute(
        "INSERT INTO cfzy_biz_fin_risk "
        "(code, name, report_year, score, flags_json, pushed_key) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE name=%s, report_year=%s, score=%s, "
        "flags_json=%s, pushed_key=%s, computed_at=NOW()",
        (code, name, report_year, score, flags_json, pushed_key,
         name, report_year, score, flags_json, pushed_key),
    )


async def list_fin_risk(limit: int = 100) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_fin_risk WHERE score > 0 "
        "ORDER BY score DESC, code LIMIT %s", (limit,))
