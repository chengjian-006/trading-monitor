"""信号配置 + K线缓存 CRUD - cfzy_biz_signal_config / cfzy_sys_kline_cache 表."""
from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone


# ── Signal Config ──

async def get_signal_config(user_id: int) -> dict | None:
    row = await _fetchone("SELECT config FROM cfzy_biz_signal_config WHERE user_id = %s", (user_id,))
    if row and row["config"]:
        import json
        import logging
        cfg = row["config"]
        parsed = json.loads(cfg) if isinstance(cfg, str) else cfg
        logging.getLogger(__name__).info(
            f"[signal_config] load user={user_id} type={type(cfg).__name__} "
            f"keys={list(parsed.keys()) if isinstance(parsed, dict) else 'NOT_DICT'}"
        )
        return parsed
    return None


async def save_signal_config(user_id: int, config: dict):
    import json
    config_json = json.dumps(config, ensure_ascii=False)
    await _execute(
        "INSERT INTO cfzy_biz_signal_config (user_id, config) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE config = %s, updated_at = NOW()",
        (user_id, config_json, config_json),
    )


# ── K-line Cache ──

async def cache_klines(code: str, rows: list[tuple]):
    await _executemany(
        "INSERT INTO cfzy_sys_kline_cache (code, trade_date, open, high, low, close, volume) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE open=VALUES(open), high=VALUES(high), "
        "low=VALUES(low), close=VALUES(close), volume=VALUES(volume)",
        [(code, *r) for r in rows],
    )


async def get_cached_klines(code: str, limit: int = 120) -> list[dict]:
    rows = await _fetchall(
        "SELECT * FROM cfzy_sys_kline_cache WHERE code = %s ORDER BY trade_date DESC LIMIT %s",
        (code, limit),
    )
    return list(reversed(rows))


async def get_kline_counts() -> dict[str, int]:
    """返回 {code: 已缓存日线根数}, 供全市场回填断点续跑判定."""
    rows = await _fetchall(
        "SELECT code, COUNT(*) AS c FROM cfzy_sys_kline_cache GROUP BY code"
    )
    return {r["code"]: int(r["c"]) for r in rows}
