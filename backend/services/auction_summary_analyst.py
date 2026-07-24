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
from backend.services import card_kit, notifier
from backend.services.lark_notifier import md_element
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


# 指数展示短名(卡片列名要短, 手机端一行放得下两个)
_IDX_SHORT = {"上证指数": "上证", "深证成指": "深成", "创业板指": "创业板",
              "科创指数": "科创50", "科创50": "科创50"}


def _index_pairs(indices: list | None) -> list[tuple[str, float]]:
    """四大指数 (短名, 竞价涨跌幅) —— 排除全A指数(非四大指数, 只作内部宽基参考)。"""
    out: list[tuple[str, float]] = []
    for x in (indices or []):
        name = str(x.get("name") or "")
        if not name or "全A" in name:
            continue
        out.append((_IDX_SHORT.get(name, name), float(x.get("pct_change") or 0)))
    return out[:4]


def _index_md(indices: list | None) -> str:
    """四大指数竞价行: 2×2 排布(手机端一行两个不折行), 涨红跌绿, 保两位小数。"""
    pairs = _index_pairs(indices)
    if not pairs:
        return ""
    cells = []
    for name, pct in pairs:
        color = "red" if pct > 0 else ("green" if pct < 0 else "grey")
        cells.append(f"{name} <font color='{color}'>**{pct:+.2f}%**</font>")
    rows = ["　".join(cells[i:i + 2]) for i in range(0, len(cells), 2)]
    return "📈 **四大指数竞价**\n" + "\n".join(rows)


def _index_text(indices: list | None) -> str:
    """四大指数竞价纯文本(企微/回退用)。"""
    pairs = _index_pairs(indices)
    return "　".join(f"{n} {p:+.2f}%" for n, p in pairs)


def _build_auction_card(d: dict, near_lu_count: int, strong_count: int,
                        indices: list | None = None, elapsed: float = 0.0):
    """情报卡(基线 v1.1): heading 定调 → KPI 三栏 → 氛围/风格/杀跌 → 题材主线 →
    👉 一句话定性 → 折叠方法论。返回 (企微文本, 飞书elements, meta)。
    密度(涨停预排/高开≥5%)用硬数据不靠 AI。"""
    headline = str(d.get("headline") or "").strip() or "集合竞价后开盘共性"
    vibe = str(d.get("vibe") or "").strip()
    style = str(d.get("style") or "").strip()
    kill = str(d.get("kill") or "").strip() or "无明显杀跌"
    action = str(d.get("action") or "").strip()
    density = f"涨停预排{near_lu_count}｜≥5% {strong_count}只"
    mls = [m for m in (d.get("mainlines") or []) if isinstance(m, dict)][:4]
    footer = (f"基于 4 大指数 + 高开 top 30 + 低开 top 20 + 强势密度，"
              f"AI 提炼开盘共性；密度为硬数据（涨停预排=高开≥9.5%）· 用时 {elapsed:.1f}s")

    # 结论区: heading 一句话定调 + KPI 三栏(涨停预排/高开≥5%/题材主线)
    # v1.7.791: 指数从 KPI 挪到独立行, 四大指数全列(原只有上证一个进 KPI)
    kpi_items: list = [
        ("涨停预排", f"{near_lu_count}只", "red" if near_lu_count else None),
        ("高开≥5%", f"{strong_count}只"),
        ("题材主线", f"{len(mls)}条"),
    ]

    elements = [
        card_kit.heading_md(f"📣 {headline}"),
        card_kit.kpi_row(kpi_items[:3]),
    ]
    idx_md = _index_md(indices)
    if idx_md:
        elements.append(md_element(idx_md))
    elements.append(
        md_element("\n".join([f"🌡 氛围　{vibe}", f"⚖ 风格　{style}", f"❄ 杀跌　{kill}"])))
    if mls:
        # 移动优化(v1.7.581): 逐条换行文本行, 方向加粗前置, 代表股全名换行不截
        mlines = [f"**{str(m.get('direction', ''))}**　{str(m.get('reps', ''))}" for m in mls]
        elements.append(md_element("🎯 **题材主线**\n" + "\n".join(mlines)))
    if action:
        elements.append(card_kit.advice(action))
    elements.append(card_kit.fold("方法论", footer))

    tlines = [
        "【集合竞价后开盘共性】", "",
        f"📣 {headline}", "",
    ]
    idx_text = _index_text(indices)
    if idx_text:
        tlines += [f"📈 指数  {idx_text}"]
    tlines += [
        f"🌡 氛围  {vibe}",
        f"🔥 密度  {density}",
        f"⚖ 风格  {style}",
        f"❄ 杀跌  {kill}",
    ]
    if mls:
        tlines += ["", "🎯 题材主线"]
        tlines += [f"  · {m.get('direction', '')} → {m.get('reps', '')}" for m in mls]
    if action:
        tlines += ["", f"👉 {action}"]
    tlines += ["", f"——{footer}"]
    meta = {"headline": headline, "near_lu": near_lu_count, "strong": strong_count}
    return "\n".join(tlines), elements, meta


async def build_auction_summary_part() -> tuple[list[str], list, dict] | None:
    """计算集合竞价开盘共性 → (企微文本行, 飞书elements, meta); 未就绪/LLM无返回→None。

    供 run_auction_0926 合并卡 与 run_auction_summary 独立推送 共用(v1.7.553 抽出)。
    meta = {headline, near_lu, strong} 供信封摘要(基线 v1.1)。
    JSON 解析失败时返回 (纯文本行, [], meta) —— 文本仍出, 只是无飞书结构卡。
    """
    if not _is_trading_day():
        logger.info("[auction_summary] 非交易日, 跳过")
        return None

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
            return None
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
        return None

    elapsed = _time.time() - t0
    parsed = _parse_llm_json(analysis)
    if parsed and (parsed.get("headline") or parsed.get("vibe") or parsed.get("mainlines")):
        text, elements, meta = _build_auction_card(
            parsed, near_lu_count, strong_count, indices, elapsed)
        return text.split("\n"), elements, meta
    # JSON 解析失败 → 纯文本行(无 elements, 不丢推送)
    logger.warning("[auction_summary] JSON 解析失败, 回退纯文本")
    text = (
        f"【集合竞价后开盘共性】\n\n"
        f"{analysis.strip()}\n\n"
        f"——基于 4 大指数 + 高开 top 30 + 低开 top 20 + 强势密度 · 用时 {elapsed:.1f}s"
    )
    return text.split("\n"), [], {"headline": "", "near_lu": near_lu_count, "strong": strong_count}


async def run_auction_summary():
    """09:26 集合竞价开盘共性 AI 独立推送(现默认走合并卡 run_auction_0926, 本函数保留备用)。"""
    built = await build_auction_summary_part()
    if not built:
        return
    tlines, elements, meta = built
    if elements:
        card = card_kit.Card(
            title="📊 盘面播报", elements=elements, fallback="\n".join(tlines),
            family="intel",
            summary=card_kit.summary_text(
                "盘面播报", meta.get("headline"),
                f"涨停预排{meta.get('near_lu', 0)}只"),
        )
        sent = await notifier.send_card(card)
    else:
        sent = await notifier.send_wechat_text("\n".join(tlines))
    logger.info(f"[auction_summary] 独立推送结果={sent}")


async def run_auction_0926():
    """09:26 合并竞价卡 = AI开盘共性 + 竞价板块强弱, 一张卡(原两条推送合并, v1.7.553)。

    两部分独立取数, 任一失败仍发另一半; 全失败则不发。取代原 auction_summary_0926 +
    auction_sector_strength_0926 两个 09:26 任务(后者已 enabled=0)。
    """
    if not _is_trading_day():
        return
    from backend.services.auction_sector_strength import build_auction_sector_part

    summary, sector = await asyncio.gather(
        build_auction_summary_part(),
        build_auction_sector_part(),
        return_exceptions=True,
    )
    if isinstance(summary, Exception):
        logger.warning(f"[auction_0926] 开盘共性部分异常: {summary}")
        summary = None
    if isinstance(sector, Exception):
        logger.warning(f"[auction_0926] 板块强弱部分异常: {sector}")
        sector = None

    tlines: list[str] = []
    elements: list = []
    s_meta: dict = summary[2] if summary else {}
    b_meta: dict = sector[2] if sector else {}
    if summary:
        tlines += summary[0]
        elements += summary[1]
    if sector:
        if elements:
            elements.append(md_element(
                "<font color='grey'>━━━━━━ 竞价板块强弱 ━━━━━━</font>"))
            tlines += ["", "──────────"]
        tlines += sector[0]
        elements += sector[1]

    if not tlines and not elements:
        logger.warning("[auction_0926] 两部分都无内容, 跳过")
        return
    if elements:
        # 摘要 = 定调一句 + 涨停预排 + 竞价最强板块(基线 v1.1: 标的+事件+关键数)
        bits = [s_meta.get("headline") or ""]
        if s_meta:
            bits.append(f"涨停预排{s_meta.get('near_lu', 0)}只")
        if b_meta.get("top_board"):
            bits.append(f"最强{b_meta['top_board']}{b_meta.get('top_pct', 0):+.1f}%")
        card = card_kit.Card(
            title="📊 竞价播报", elements=elements, fallback="\n".join(tlines),
            family="intel", subtitle="开盘共性 + 板块强弱",
            summary=card_kit.summary_text("竞价播报", *bits),
        )
        sent = await notifier.send_card(card)
    else:
        sent = await notifier.send_wechat_text("\n".join(tlines))
    logger.info(f"[auction_0926] 合并竞价卡推送={sent} "
                f"(共性={'✓' if summary else '✗'} 板块={'✓' if sector else '✗'})")
