"""交易教练组装层: 取数→coach_facts→ai_client.narrate→结果(+当日缓存)。LLM失败仅缺叙述, facts照常返回。

_load_rounds/_load_winrate/_get_cached/_save_cache 做成模块级薄封装(而非直接内联调 repo),
方便测试用 monkeypatch 打桩、不必连真库。它们在测试里可能被换成同步 lambda(返回值而非协程),
所以组装函数用 _maybe_await 兼容"真实 async 实现"与"打桩的同步返回值"两种情况。
"""
import inspect
import json
import logging
from datetime import date

from backend.models import repository
from backend.models.repo import trade_rounds
from backend.services.ai_advisor import ai_client, coach_facts

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是一名A股交易复盘助手。下面给你一份该用户真实交易的【事实清单】(JSON, 所有数字均已由系统算好)。\n"
    "严格要求: 只能复述清单里已有的数字和事实; 禁止自己计算或推算任何数字; 禁止预测涨跌方向; "
    "禁止给出买入/卖出/加仓/减仓建议; 禁止承诺或暗示胜率会如何; 清单里某些数字可能是 null(样本不足), "
    "遇到 null 直接跳过、不要编造。\n"
    "任务: 用中文、大白话、简明地把清单里的规律讲给用户听, 指出其交易习惯的客观特征(如输家比赢家扛得更久、"
    "某买点模型实盘执行得比全市场回测差多少), 只陈述事实与客观倾向, 不做投资建议。结尾不加免责声明(前端已固定)。"
)


async def _maybe_await(value):
    """真实实现是 async def, 调用后拿到 coroutine 需要 await; 测试打桩多用同步 lambda, 拿到的是
    已经算好的返回值, 不能再 await。两种情况都兼容, 组装函数无需关心调的是真实现还是桩。"""
    if inspect.isawaitable(value):
        return await value
    return value


async def _load_rounds(user_id: int):
    return await trade_rounds.get_rounds(user_id)


async def _load_winrate():
    return await repository.get_model_winrate()


async def _get_cached(user_id, period_key, gen_date):
    return await repository.get_coach_report(user_id, period_key, gen_date)


async def _save_cache(user_id, period_key, gen_date, facts, narrative):
    await repository.save_coach_report(user_id, period_key, gen_date, facts, narrative)


async def generate_coach_report(user_id: int, start: str, end: str, *, use_cache: bool = True) -> dict:
    """取回合+胜率→算事实清单→交给LLM写人话。LLM挂了只缺narrative, facts照常给全。"""
    period_key = f"{start}~{end}"
    today = date.today()
    if use_cache:
        row = await _maybe_await(_get_cached(user_id, period_key, today))
        if row:
            return {"facts": json.loads(row["facts_json"]), "narrative": row.get("narrative"),
                    "as_of": str(today), "cached": True}

    rounds = await _maybe_await(_load_rounds(user_id))
    winrate = await _maybe_await(_load_winrate())
    facts = coach_facts.build_coach_facts(rounds, winrate, start, end)
    narrative = await ai_client.narrate(_SYSTEM_PROMPT, facts)

    try:
        await _maybe_await(_save_cache(user_id, period_key, today, facts, narrative))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 复盘缓存写入失败(忽略): {e}")

    return {"facts": facts, "narrative": narrative, "as_of": str(today), "cached": False}
