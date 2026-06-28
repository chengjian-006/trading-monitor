"""09:26 集合竞价后开盘情况 AI 分析

逻辑:
1. 9:25 集合竞价撮合完成 → 9:26 开盘价已定
2. 拉取数据:
   - 大盘 4 个指数集合竞价开盘情况 (上证/深证/创业/科创)
   - 高开榜 top 30 (按当前涨幅 desc, 反映抢筹方向)
   - 低开榜 top 20 (按当前涨幅 asc, 反映抛压方向)
   - 高开 + 涨幅 ≥ 9.5% 的票数 (早盘涨停预排, 强弱信号)
3. 给两份榜单并集前 30 只补 concepts
4. AI 提炼共性: 题材集中度 / 大小盘倾向 / 涨停预排强度 / 整体氛围
5. 企微推送一条短评

v1.7.98: report_0926 (通用早盘报告) 已下线, 9:26 时段只保留本任务。
"""
import asyncio
import logging
import time as _time
from datetime import datetime

from backend.core.config import load_config
from backend import data_fetcher
from backend.services import notifier
from backend.services.attack_direction_analyst import _fmt_mcap, _enrich_with_concepts

logger = logging.getLogger(__name__)


def _is_trading_day(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5


async def _fetch_sina_pct_rank(top_n: int, asc: bool) -> list[dict]:
    """新浪行情中心: 沪深A按涨幅排序拉 top_n。
    asc=False → 高开榜(涨幅降序); asc=True → 低开榜(涨幅升序)。
    用新浪替代东财(prod IP 常被东财风控断连, 集合竞价时段尤甚); 排除北交所(与原东财主板口径一致)。
    单位: amount 元 / nmc 万元→元。"""
    import httpx
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page=1&num={top_n + 15}&sort=changepercent"
           f"&asc={1 if asc else 0}&node=hs_a&symbol=&_s_r_a=page")
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://finance.sina.com.cn/"})
            arr = r.json()
    except Exception as e:
        logger.warning(f"[auction_summary] 新浪{'低' if asc else '高'}开榜取数失败: {e}")
        return []
    out = []
    for it in arr or []:
        sym = str(it.get("symbol") or "")
        code = str(it.get("code") or "").zfill(6)
        name = str(it.get("name") or "")
        if not code or not name or sym.startswith("bj"):   # 排除北交所
            continue
        out.append({
            "code": code, "name": name,
            "price": float(it.get("trade") or 0),
            "pct": float(it.get("changepercent") or 0),
            "amount": float(it.get("amount") or 0),          # 元
            "market_cap": float(it.get("nmc") or 0) * 1e4,   # nmc 万元 → 元
        })
    return out[:top_n]


async def _count_near_limit_up(top_high: list[dict]) -> tuple[int, int]:
    """从高开榜中统计: (涨幅≥9.5% 的票数, 涨幅≥5% 的票数)。"""
    near_lu = sum(1 for r in top_high if r["pct"] >= 9.5)
    strong = sum(1 for r in top_high if r["pct"] >= 5.0)
    return near_lu, strong


def _build_prompt(indices: list[dict],
                  top_high: list[dict],
                  top_low: list[dict],
                  near_lu_count: int,
                  strong_count: int) -> tuple[str, str]:
    system_prompt = (
        "你是一位 A 股早盘资金面分析师。9:25 集合竞价撮合完成, 9:26 给你看以下盘前数据:\n"
        "1) 4 大指数集合竞价开盘涨跌\n"
        "2) 集合竞价后高开榜 top 30 (含 concepts/流通市值)\n"
        "3) 集合竞价后低开榜 top 20\n"
        "4) 高开榜中涨幅 ≥ 5% / ≥ 9.5% 的票数 (反映强弱密度)\n\n"
        "任务: 提炼集合竞价后开盘共性, 用严格 JSON 输出(只输出 JSON 本身, 不要任何 JSON 以外的文字, 不要 markdown 代码块):\n"
        "{\n"
        '  "headline": "一句话定调, ≤22字, 概括 氛围+主线+操作倾向",\n'
        '  "vibe": "整体氛围: 高开多还是低开多/指数倾向, ≤30字",\n'
        '  "style": "大小盘谁更强(流通市值梯队), ≤20字",\n'
        '  "kill": "低开/连带杀跌方向, 无则填 \\"无明显杀跌\\", ≤24字",\n'
        '  "mainlines": [{"direction": "题材方向", "reps": "代表股1/代表股2"}],\n'
        '  "action": "开盘后操作倾向(谨慎/积极/观望)+一句话, ≤24字"\n'
        "}\n"
        "要求: 全部中文; mainlines 2-4 条, 按强度降序, reps 从高开榜点名真实个股名(不要编造、不要写代码);\n"
        "不要在 JSON 里放涨停预排数量(由系统用硬数据填), 你只管定性。\n"
    )

    def line(i, r):
        cstr = "/".join(r.get("concepts", [])) or "—"
        return (f"{i:>2}. {r['name']}({r['code']}) {r['pct']:+.2f}% "
                f"流通市值{_fmt_mcap(r['market_cap'])} [{cstr}]")

    idx_lines = "\n".join(
        f"  {x['name']}: 现价{x['price']:.2f} {x['pct_change']:+.2f}% 成交{x.get('amount', 0):.1f}亿"
        for x in indices
    ) or "  (取不到)"

    high_lines = "\n".join(line(i, r) for i, r in enumerate(top_high, 1))
    low_lines = "\n".join(line(i, r) for i, r in enumerate(top_low, 1))

    user_content = (
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## 大盘指数集合竞价开盘\n{idx_lines}\n\n"
        f"## 强势密度\n"
        f"  高开榜 ≥ 9.5% (涨停预排): {near_lu_count} 只\n"
        f"  高开榜 ≥ 5%: {strong_count} 只\n\n"
        f"## 高开榜 top {len(top_high)}\n{high_lines}\n\n"
        f"## 低开榜 top {len(top_low)}\n{low_lines}\n"
    )
    return system_prompt, user_content


def _call_llm(system_prompt: str, user_content: str) -> str | None:
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        logger.warning("[auction_summary] AI api_key 未配置, 跳过")
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
        logger.error(f"[auction_summary] LLM 调用失败: {e}")
        return None


def _parse_llm_json(raw: str) -> dict | None:
    """从 LLM 文本里抽出 JSON 对象(容忍 ```json 包裹/前后多余文字)。失败返回 None。"""
    import json
    import re
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
    s = re.sub(r"\n?```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _build_auction_card(d: dict, near_lu_count: int, strong_count: int, elapsed: float):
    """混合式结构化卡片: 一句话定调 + 字段行(氛围/密度/风格/杀跌) + 题材主线表 + 操作。
    返回 (企微文本, 飞书elements)。密度行用硬数据(near_lu/strong)不靠 AI。"""
    from backend.services import lark_notifier
    headline = str(d.get("headline") or "").strip()
    vibe = str(d.get("vibe") or "").strip()
    style = str(d.get("style") or "").strip()
    kill = str(d.get("kill") or "").strip() or "无明显杀跌"
    action = str(d.get("action") or "").strip()
    density = f"涨停预排{near_lu_count}｜≥5% {strong_count}只"
    mls = [m for m in (d.get("mainlines") or []) if isinstance(m, dict)][:4]
    footer = f"——基于 4 大指数 + 高开 top 30 + 低开 top 20 + 强势密度 · 用时 {elapsed:.1f}s"

    field_md = "\n".join([
        f"📣 **{headline}**" if headline else "📣 **集合竞价后开盘共性**",
        "",
        f"🌡 氛围　{vibe}",
        f"🔥 密度　{density}",
        f"⚖ 风格　{style}",
        f"❄ 杀跌　{kill}",
    ])
    elements = [lark_notifier.md_element(field_md)]
    if mls:
        cols = [
            {"name": "dir", "display_name": "方向", "data_type": "text", "width": "42%"},
            {"name": "reps", "display_name": "代表股", "data_type": "text", "width": "58%"},
        ]
        rows = [{"dir": str(m.get("direction", "")), "reps": str(m.get("reps", ""))} for m in mls]
        elements.append(lark_notifier.md_element("🎯 **题材主线**"))
        elements.append(lark_notifier.table_element(cols, rows, page_size=10))
    if action:
        elements.append(lark_notifier.md_element(f"✅ **操作**　{action}"))
    elements.append(lark_notifier.md_element(footer))

    tlines = [
        "【集合竞价后开盘共性】", "",
        f"📣 {headline}" if headline else "📣 集合竞价后开盘共性", "",
        f"🌡 氛围  {vibe}",
        f"🔥 密度  {density}",
        f"⚖ 风格  {style}",
        f"❄ 杀跌  {kill}",
    ]
    if mls:
        tlines += ["", "🎯 题材主线"]
        tlines += [f"  · {m.get('direction', '')} → {m.get('reps', '')}" for m in mls]
    if action:
        tlines += ["", f"✅ 操作: {action}"]
    tlines += ["", footer]
    return "\n".join(tlines), elements


async def run_auction_summary():
    """09:26 集合竞价后开盘共性 AI 推送入口。"""
    if not _is_trading_day():
        logger.info("[auction_summary] 非交易日, 跳过")
        return

    t0 = _time.time()
    # get_market_indices 在 ai_analyst 里, 是同步函数, 包到线程里
    from backend.services import ai_analyst

    # 集合竞价数据源(09:25撮合后)有发布延迟, 故重试到拿到高开榜为止, 开盘前 09:29 硬封顶。
    # 解决"09:26 一次取数失败就整天不发"的老问题。
    DEADLINE = "09:29:00"
    indices: list = []
    top_high: list = []
    top_low: list = []
    attempt = 0
    while True:
        attempt += 1
        try:
            indices, top_high, top_low = await asyncio.gather(
                asyncio.to_thread(ai_analyst.get_market_indices),
                _fetch_sina_pct_rank(30, asc=False),
                _fetch_sina_pct_rank(20, asc=True),
            )
        except Exception as e:
            logger.warning(f"[auction_summary] 第{attempt}次并发取数异常: {e}")
            indices, top_high, top_low = (indices or []), [], []
        if top_high:
            break
        if datetime.now().strftime("%H:%M:%S") >= DEADLINE:
            logger.warning(f"[auction_summary] 到{DEADLINE}高开榜仍为空(第{attempt}次), 放弃本日推送")
            return
        logger.info(f"[auction_summary] 第{attempt}次高开榜为空, 25s后重试 (竞价数据源延迟)")
        await asyncio.sleep(25)

    # 强势密度统计
    near_lu_count, strong_count = await _count_near_limit_up(top_high)

    # 给两份的并集前 30 只补 concepts
    union_rows = list({r["code"]: r for r in top_high + top_low}.values())
    await _enrich_with_concepts(union_rows, max_codes=30)
    cmap = {r["code"]: r.get("concepts", []) for r in union_rows}
    for r in top_high + top_low:
        r["concepts"] = cmap.get(r["code"], [])

    system_prompt, user_content = _build_prompt(indices, top_high, top_low, near_lu_count, strong_count)
    analysis = _call_llm(system_prompt, user_content)
    if not analysis:
        logger.warning("[auction_summary] LLM 无返回, 跳过推送")
        return

    elapsed = _time.time() - t0
    parsed = _parse_llm_json(analysis)
    if parsed and (parsed.get("headline") or parsed.get("vibe") or parsed.get("mainlines")):
        text, elements = _build_auction_card(parsed, near_lu_count, strong_count, elapsed)
        sent = await notifier.send_dual_card(text, lark_title="📊 盘面播报", elements=elements)
    else:
        # JSON 解析失败 → 回退纯文本(不丢推送)
        logger.warning("[auction_summary] JSON 解析失败, 回退纯文本推送")
        text = (
            f"【集合竞价后开盘共性】\n\n"
            f"{analysis.strip()}\n\n"
            f"——基于 4 大指数 + 高开 top 30 + 低开 top 20 + 强势密度 · 用时 {elapsed:.1f}s"
        )
        sent = await notifier.send_wechat_text(text)
    logger.info(
        f"[auction_summary] 推送结果={sent}, 高开≥9.5%={near_lu_count} 高开≥5%={strong_count}, 耗时{elapsed:.1f}s"
    )
