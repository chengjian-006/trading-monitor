"""诊断买点「生成 vs 推送」是否被闸门压制 — 只读。
python -m backend.scripts.diag_buy_push_gate
"""
import asyncio
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from backend.models.database import init_db
from backend.models.repo import _db


async def main():
    await init_db()
    cols = await _db._fetchall("SHOW COLUMNS FROM cfzy_biz_signals")
    print("signals列:", [r["Field"] for r in cols])


if __name__ == "__main__":
    asyncio.run(main())
