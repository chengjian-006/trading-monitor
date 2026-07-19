"""个股研判组装层: 并发gather各源→stock_facts→ai_client.narrate→结果(+当日缓存)。
LLM失败仅缺叙述, facts照常返回。镜像 trade_coach.py 的薄封装模式。

_fetch_*/_gather/_get_cached/_save_cache 做成模块级薄封装(而非直接内联调 repo),
方便测试用 monkeypatch 打桩、不必连真库。它们在测试里可能被换成同步 lambda(返回值而非协程),
所以组装函数用 _maybe_await 兼容"真实 async 实现"与"打桩的同步返回值"两种情况。
"""
import asyncio
import inspect
import json
import logging
from datetime import date

from backend.models import repository
from backend.services.ai_advisor import ai_client, stock_facts

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是一名A股个股研判助手。下面给你一份这只股票的【事实清单】(JSON, 所有数字均已由系统算好)。\n"
    "严格要求: 只能复述清单里已有的数字和事实; 禁止自己计算或推算任何数字; 禁止预测涨跌方向; "
    "禁止给出买入/卖出/加仓/减仓建议; 禁止报目标价; 禁止承诺或暗示胜率会如何。"
    "清单里 model_winrate(同形态胜率)是该买点模型历史上的客观统计分布(历史上同类形态触发后一段时间内"
    "的涨跌分布), 不是对这只票未来涨跌的预测, 讲述时必须用“历史上/过去”这类措辞, 不得说成“这票会涨/会跌”。"
    "清单里某些数字可能是 null(样本不足/无记录), 遇到 null 直接跳过、不要编造。\n"
    "任务: 用中文、大白话、简明地把这只票当前的信号历史、同形态历史胜率、财务红旗、板块强弱、"
    "(若持仓)成本位置等客观事实讲清楚, 只陈述事实与客观倾向, 不做投资建议。结尾不加免责声明(前端已固定)。"
)


async def _maybe_await(value):
    """真实实现是 async def, 调用后拿到 coroutine 需要 await; 测试打桩多用同步 lambda, 拿到的是
    已经算好的返回值, 不能再 await。两种情况都兼容, 组装函数无需关心调的是真实现还是桩。"""
    if inspect.isawaitable(value):
        return await value
    return value


async def _fetch_signals(code: str, user_id: int) -> list[dict]:
    """该票近150天信号历史, 归一成 stock_facts 要的字段名(trigger_date), 最近的排前面
    (repo 按触发时间升序返回, 这里反转, 配合 stock_facts 取前10条当"recent")。单源失败降级为[]。"""
    try:
        rows = await repository.get_signals_by_code_since(code, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-信号历史取数失败({code}): {e}")
        return []
    out = [{"signal_name": r.get("signal_name"), "direction": r.get("direction"),
            "trigger_date": str(r.get("triggered_at") or "")[:10]} for r in rows]
    out.reverse()
    return out


async def _fetch_winrate() -> dict:
    try:
        return await repository.get_model_winrate()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-模型胜率取数失败: {e}")
        return {}


async def _fetch_fin_risk(code: str):
    try:
        return await repository.get_fin_risk(code)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-财务红旗取数失败({code}): {e}")
        return None


async def _fetch_theme_heat() -> list:
    try:
        return await repository.get_theme_heat(days=15)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-题材热度取数失败: {e}")
        return []


async def _fetch_pool_row(code: str):
    try:
        return await repository.get_pool_row(code)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-股票池行取数失败({code}): {e}")
        return None


async def _fetch_holdings(user_id: int):
    """(cost_map, date_map, model_map) 三件套; 失败降级为三个空 dict。"""
    try:
        return await repository.get_holdings_full_info(user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-持仓取数失败: {e}")
        return {}, {}, {}


async def _fetch_near_buy_snapshot(user_id: int):
    try:
        return await repository.get_near_buy_snapshot(user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 个股研判-临近买点取数失败: {e}")
        return None


def _pick_holding(cost_map: dict, model_map: dict, pool_row: dict | None, code: str) -> dict | None:
    """从持仓三件套(按 code)+ 池行现价 拼该票持仓段: 非持仓 → None。
    float_pct 用池行现价(quote_refresher 维护, 免于本层再打一次实时行情)算, 现价缺失则 None。"""
    if code not in cost_map:
        return None
    cost = cost_map.get(code)
    price = (pool_row or {}).get("price")
    float_pct = round((price - cost) / cost * 100, 2) if (price is not None and cost) else None
    return {"cost": cost, "float_pct": float_pct, "entry_model": model_map.get(code)}


def _pick_near_buy(snapshot: dict | None, code: str) -> dict | None:
    """从临近买点快照(整表, 多票 items)里按 code 挑出该票那条; 不接近/未在榜 → None。
    model 列出该票接近的全部买点名(hits 无 per-hit 距离、顺序也非贴线度, 故不单挑一个,
    避免与 item 级 dist 张冠李戴); gap_pct 用 item.dist(该票距最近相关均线%, item 级)。"""
    if not snapshot:
        return None
    for item in (snapshot.get("items") or []):
        if item.get("code") == code:
            hits = item.get("hits") or []
            names = [h.get("buy_name") for h in hits if h.get("buy_name")]
            model = " / ".join(names) if names else None
            return {"model": model, "gap_pct": item.get("dist")}
    return None


def _pick_sector(heat: list, pool_row: dict | None) -> dict:
    """板块段: board_strength 取自池行 board_name/board_rank/board_total(持仓在最热题材内的当日
    涨幅名次, sector_strength_scanner 只算持仓票, 非持仓通常为空); sector_rank 是遗留字段(长期未回填,
    见 get_pool_row 注释, 恒 None); theme_heat 摘要原样透传给 stock_facts。"""
    row = pool_row or {}
    board_rank = row.get("board_rank")
    board_strength = None
    if board_rank is not None:
        board_strength = {"board_name": row.get("board_name"), "board_rank": board_rank,
                           "board_total": row.get("board_total")}
    return {"board_strength": board_strength, "sector_rank": row.get("sector_rank"),
            "theme_heat": heat or []}


async def _gather(user_id: int, code: str) -> dict:
    """并发拉 6 个源(信号历史/模型胜率/财务红旗/题材热度/持仓/临近买点, 另加池行取现价与
    板块名次); 每源已在各自 _fetch_* 内 try/except 降级, 单源失败不拖垮整体、也不让 gather 抛出。"""
    (signals, winrate, fin_risk, heat, pool_row, holdings, near_buy_snap) = \
        await asyncio.gather(
            _fetch_signals(code, user_id), _fetch_winrate(), _fetch_fin_risk(code),
            _fetch_theme_heat(), _fetch_pool_row(code),
            _fetch_holdings(user_id), _fetch_near_buy_snapshot(user_id),
        )
    cost_map, _date_map, model_map = holdings if holdings else ({}, {}, {})
    name = (pool_row or {}).get("name") or code
    return {
        "name": name, "signals": signals, "winrate": winrate, "fin_risk": fin_risk,
        "sector": _pick_sector(heat, pool_row),
        "holding": _pick_holding(cost_map, model_map, pool_row, code),
        "near_buy": _pick_near_buy(near_buy_snap, code),
    }


async def _get_cached(user_id, code, gen_date):
    return await repository.get_stock_review(user_id, code, gen_date)


async def _save_cache(user_id, code, gen_date, facts, narrative):
    await repository.save_stock_review(user_id, code, gen_date, facts, narrative)


async def generate_stock_review(user_id: int, code: str, *, use_cache: bool = True) -> dict:
    """并发取信号历史/同形态胜率/财务红旗/板块强弱/持仓/临近买点→算事实清单→交给LLM写人话。
    LLM挂了只缺narrative, facts照常给全; 当日同票命中缓存直接返回、不重新调 LLM。"""
    today = date.today()
    if use_cache:
        row = await _maybe_await(_get_cached(user_id, code, today))
        if row:
            return {"facts": json.loads(row["facts_json"]), "narrative": row.get("narrative"),
                    "as_of": str(today), "cached": True}

    gathered = await _maybe_await(_gather(user_id, code))
    facts = stock_facts.build_stock_facts(
        code, gathered.get("name"), signals=gathered.get("signals"), winrate=gathered.get("winrate"),
        fin_risk=gathered.get("fin_risk"), sector=gathered.get("sector"),
        holding=gathered.get("holding"), near_buy=gathered.get("near_buy"))
    narrative = await ai_client.narrate(_SYSTEM_PROMPT, facts)

    if narrative is not None:
        try:
            await _maybe_await(_save_cache(user_id, code, today, facts, narrative))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ai_advisor] 个股研判缓存写入失败(忽略): {e}")

    return {"facts": facts, "narrative": narrative, "as_of": str(today), "cached": False}
