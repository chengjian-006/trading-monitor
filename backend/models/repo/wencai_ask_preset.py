"""个股「问财提问」预设问题 CRUD - cfzy_biz_wencai_ask_preset 表 (v1.7.786)。

自选股行内「问财提问」弹窗里那几个一点即问的模版, 原来写死在前端常量里(改一条要发版),
现在每用户一套存库、弹窗内就地增删改排序, PC 与手机同步。

template 用 {name}/{code} 占位, 渲染在前端做(两个占位都没有 → 自动在最前补股名)。
"""
from backend.models.repo._db import _execute, _fetchall, _fetchone

# 系统默认模版 (= v1.7.777 起写死在前端的那 4 条): 用户首次打开时播种, 也是「恢复默认」的内容
DEFAULT_ASK_PRESETS: list[tuple[str, str]] = [
    ("现在能不能买", "{name} 现在能不能买,当前股价位置、支撑位和压力位、买卖点"),
    ("消息面(利好利空/公告)", "{name} 最近有哪些利好利空消息和最新公告"),
    ("基本面(业绩/估值/行业)", "{name} 最新业绩、估值(市盈率、市净率)和所属行业地位"),
    ("题材强弱 + 技术面", "{name} 属于哪些概念题材、题材热度如何,以及当前技术形态和短线操作建议"),
]


async def list_presets(user_id: int) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_wencai_ask_preset WHERE user_id=%s ORDER BY sort_order, id",
        (user_id,),
    )


async def get_preset(preset_id: int, user_id: int) -> dict | None:
    return await _fetchone(
        "SELECT * FROM cfzy_biz_wencai_ask_preset WHERE id=%s AND user_id=%s",
        (preset_id, user_id),
    )


async def add_preset(user_id: int, label: str, template: str,
                     enabled: int = 1, sort_order: int | None = None) -> int:
    """新增一条模版, 返回新 id。sort_order 省略 → 排到末尾。"""
    from backend.models.database import get_pool
    if sort_order is None:
        row = await _fetchone(
            "SELECT COALESCE(MAX(sort_order), 0) AS m FROM cfzy_biz_wencai_ask_preset WHERE user_id=%s",
            (user_id,),
        )
        sort_order = int((row or {}).get("m") or 0) + 10
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_wencai_ask_preset (user_id, label, template, enabled, sort_order) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, label, template, enabled, sort_order),
            )
            return cur.lastrowid


async def update_preset(preset_id: int, user_id: int, **fields) -> None:
    """改 label/template/enabled/sort_order (仅本人)。"""
    allowed = {"label", "template", "enabled", "sort_order"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=%s")
            args.append(v)
    if not sets:
        return
    args += [preset_id, user_id]
    await _execute(
        f"UPDATE cfzy_biz_wencai_ask_preset SET {', '.join(sets)} WHERE id=%s AND user_id=%s",
        tuple(args),
    )


async def delete_preset(preset_id: int, user_id: int) -> None:
    await _execute(
        "DELETE FROM cfzy_biz_wencai_ask_preset WHERE id=%s AND user_id=%s",
        (preset_id, user_id),
    )


async def reorder_presets(user_id: int, ids: list[int]) -> None:
    """按给定 id 顺序重排(只动本人的行, 外来 id 自然不匹配)。"""
    for i, pid in enumerate(ids):
        await _execute(
            "UPDATE cfzy_biz_wencai_ask_preset SET sort_order=%s WHERE id=%s AND user_id=%s",
            ((i + 1) * 10, int(pid), user_id),
        )


async def seed_defaults(user_id: int, replace: bool = False) -> list[dict]:
    """播种系统默认模版; replace=True 先清空(用于「恢复默认」)。返回播种后的清单。"""
    if replace:
        await _execute("DELETE FROM cfzy_biz_wencai_ask_preset WHERE user_id=%s", (user_id,))
    for i, (label, template) in enumerate(DEFAULT_ASK_PRESETS):
        await add_preset(user_id, label, template, 1, (i + 1) * 10)
    return await list_presets(user_id)
