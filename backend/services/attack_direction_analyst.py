"""09:45 交易日资金进攻方向 AI 分析

逻辑:
1. 拉取盘中实时:
   - 成交额前 20  (fid=f6) — 主力资金布的位置
   - 涨幅榜前 50  (fid=f3) — 资金当下集中推升的方向
2. 给并集前 30 只票补 concepts(题材) — 用于 LLM 共性归纳
3. 调 LLM 分析共性: 板块 / 题材 / 市值梯队 / 龙头跟风结构
4. 企微推送一条精简结论

调度: cron 09:45 — 开盘 15 分钟后, 早盘虚拟全天量噪声渐稳, 资金方向初步明朗
"""
import asyncio
import logging
import time as _time
from datetime import datetime

from backend.core.config import load_config
from backend import data_fetcher
from backend.services import notifier

logger = logging.getLogger(__name__)

# m:0+t:6 深主板, m:0+t:80 创业板, m:1+t:2 沪主板, m:1+t:23 科创板
_EM_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_FIELDS = "f2,f3,f5,f6,f12,f14,f20"   # 价/涨跌%/量/成交额/code/name/流通市值


def _is_trading_day(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5


async def _fetch_em_top(by_field: str, top_n: int, order: str = "desc") -> list[dict]:
    """按 by_field='f3'(涨幅) 或 'f6'(成交额) 拉前 top_n 只。order='desc' 降序(默认), 'asc' 升序(取跌幅榜)。"""
    from backend.data_fetcher import _get_client, EM_HEADERS
    po = 1 if order == "desc" else 0
    url = (f"https://push2.eastmoney.com/api/qt/clist/get"
           f"?pn=1&pz={top_n}&po={po}&np=1&fltt=2&invt=2"
           f"&fid={by_field}&fs={_EM_FILTER}&fields={_FIELDS}")
    client = _get_client()
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
        diff = data.get("data", {}).get("diff", []) if data.get("data") else []
    except Exception as e:
        logger.warning(f"[attack_direction] fetch top {by_field} failed: {e}")
        return []
    out = []
    for item in diff:
        code = str(item.get("f12") or "").zfill(6)
        name = str(item.get("f14") or "")
        if not code or not name:
            continue
        out.append({
            "code": code,
            "name": name,
            "price": float(item.get("f2") or 0),
            "pct": float(item.get("f3") or 0),
            "amount": float(item.get("f6") or 0),
            "market_cap": float(item.get("f20") or 0),
        })
    return out[:top_n]


def _fmt_amt(a: float) -> str:
    if a >= 1e8:
        return f"{a / 1e8:.1f}亿"
    if a >= 1e4:
        return f"{a / 1e4:.0f}万"
    return f"{int(a)}元"


def _fmt_mcap(m: float) -> str:
    if m >= 1e8:
        return f"{m / 1e8:.0f}亿"
    if m >= 1e4:
        return f"{m / 1e4:.0f}万"
    return f"{int(m)}元"


async def _enrich_with_concepts(rows: list[dict], max_codes: int = 30) -> None:
    """给前 max_codes 只票补 concepts(原地写 r['concepts'])。"""
    codes = [r["code"] for r in rows[:max_codes]]
    if not codes:
        return
    try:
        concepts_map, _ = await data_fetcher.get_stock_concepts(codes)
    except Exception as e:
        logger.warning(f"[attack_direction] concepts fetch failed: {e}")
        return
    for r in rows:
        c = concepts_map.get(r["code"], [])
        r["concepts"] = c[:3] if c else []


def _build_prompt(top_amount: list[dict], top_gainers: list[dict]) -> tuple[str, str]:
    system_prompt = (
        "你是一位 A 股市场资金面分析师。给你两份盘中实时数据:\n"
        "1) 全市场成交额前 20 (主力资金布的位置 — 大体量推升才能上榜)\n"
        "2) 全市场涨幅前 50 (资金当下集中推升的方向)\n\n"
        "任务: 输出当前『资金进攻方向』结构化简报。\n\n"
        "输出格式 (严格按此 5 行结构, 每行【标签】+ 一句话, 不要加任何前言/总结/空行):\n"
        "【主线】当前最聚焦的板块/题材主线 (一句话点明方向, 不超过 30 字)\n"
        "【中军】1-3 只大市值龙头股名 — 流动性核心位置的判断 (从成交额前 20 找)\n"
        "【先锋】1-3 只小市值弹性股名 — 涨停/拔高的跟风梯队 (从涨幅前 50 找)\n"
        "【结构】梯队/集中度判断 (大票守阵 vs 小票拔高 / 集中 vs 分散 / 有无外溢)\n"
        "【操作】一句话操作倾向建议 (持仓/滚动/观望/规避)\n\n"
        "硬性要求:\n"
        "- 中文, 每行控制在 50 字以内, 短促有力, 不要书面化套话\n"
        "- 必须严格 5 行, 标签用中文方括号【】, 不要 markdown 加粗/项目符号\n"
        "- 不要重复表述 (如'高度聚焦'和'强度集中'不要并存)\n"
    )

    lines_a = []
    for i, r in enumerate(top_amount, 1):
        cstr = "/".join(r.get("concepts", [])) or "—"
        lines_a.append(
            f"{i:>2}. {r['name']}({r['code']}) {r['pct']:+.2f}% "
            f"成交{_fmt_amt(r['amount'])} 流通市值{_fmt_mcap(r['market_cap'])} [{cstr}]"
        )
    lines_g = []
    for i, r in enumerate(top_gainers, 1):
        cstr = "/".join(r.get("concepts", [])) or "—"
        lines_g.append(
            f"{i:>2}. {r['name']}({r['code']}) {r['pct']:+.2f}% "
            f"成交{_fmt_amt(r['amount'])} 流通市值{_fmt_mcap(r['market_cap'])} [{cstr}]"
        )

    user_content = (
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## 成交额前 20\n" + "\n".join(lines_a) + "\n\n"
        f"## 涨幅前 50\n" + "\n".join(lines_g) + "\n"
    )
    return system_prompt, user_content


def _call_llm(system_prompt: str, user_content: str) -> str | None:
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        logger.warning("[attack_direction] AI api_key 未配置, 跳过")
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=cfg.get("ai_base_url", "https://api.deepseek.com/v1"),
        )
        resp = client.chat.completions.create(
            model=cfg.get("ai_model", "deepseek-chat"),
            max_tokens=1500,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"[attack_direction] LLM 调用失败: {e}")
        return None


async def run_attack_direction_analysis():
    """09:45 交易日 AI 分析任务入口。"""
    if not _is_trading_day():
        logger.info("[attack_direction] 非交易日, 跳过")
        return

    t0 = _time.time()
    top_amount, top_gainers = await asyncio.gather(
        _fetch_em_top("f6", 20),   # 成交额前 20
        _fetch_em_top("f3", 50),   # 涨幅前 50
    )
    if not top_amount or not top_gainers:
        logger.warning(
            f"[attack_direction] 取数为空 amount={len(top_amount)} gainers={len(top_gainers)}, 跳过"
        )
        return

    # 给两份的并集补 concepts (限 30 只避免拖延)
    union_rows = list({r["code"]: r for r in top_amount + top_gainers}.values())
    await _enrich_with_concepts(union_rows, max_codes=30)
    # 反写回原两份
    cmap = {r["code"]: r.get("concepts", []) for r in union_rows}
    for r in top_amount + top_gainers:
        r["concepts"] = cmap.get(r["code"], [])

    system_prompt, user_content = _build_prompt(top_amount, top_gainers)
    analysis = _call_llm(system_prompt, user_content)
    if not analysis:
        logger.warning("[attack_direction] LLM 无返回, 跳过推送")
        return

    elapsed = _time.time() - t0
    text = (
        f"【资金进攻方向·09:45】\n\n"
        f"{analysis.strip()}\n\n"
        f"——基于成交额前 20 + 涨幅前 50 共性分析 · 用时 {elapsed:.1f}s"
    )
    sent = await notifier.send_wechat_text(text)
    logger.info(f"[attack_direction] 推送结果={sent}, 耗时{elapsed:.1f}s")
