import json
import logging
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)

EXTENDED_HOURS = [
    {"start": "09:15", "end": "11:35"},
    {"start": "12:55", "end": "15:05"},
]

_last_run_time: datetime | None = None


def _is_market_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    for period in EXTENDED_HOURS:
        if period["start"] <= t <= period["end"]:
            return True
    return False


def _should_run() -> bool:
    global _last_run_time
    now = datetime.now()
    if _last_run_time is None:
        return True
    elapsed = (now - _last_run_time).total_seconds()
    if _is_market_time():
        return elapsed >= 900   # 15分钟
    # 收盘后定点补刷一次: 锁定收盘价, 堵住 15:05收盘~16:00全量重刷 之间约1小时的空档
    # (否则人气表会停在最后一次盘中快照, 若那一刻在下挫就出现"实际收红、表里显绿")
    if now.weekday() < 5:
        post_start = now.replace(hour=15, minute=1, second=0, microsecond=0)
        post_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if post_start <= now <= post_end and _last_run_time < post_start:
            return True       # 当日收盘前的最后一刷之后, 进入该窗口强制补刷一次; 刷完 _last_run_time 进入窗口内即不再触发
    return elapsed >= 3600      # 1小时


async def _ai_analyze_popularity(data: dict) -> dict[str, str]:
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        return {}

    stocks = data.get("stocks", [])
    if not stocks:
        return {}

    stock_lines = []
    for s in stocks:
        ann_text = "; ".join(s.get("announcements", []))
        line = (f"排名{s['rank']}(排名变化:{s.get('rank_change',0)}) "
                f"{s['name']}({s['code']}) "
                f"涨跌幅{s['pct_change']}% 成交额{s.get('amount',0)} "
                f"换手率{s.get('turnover',0)}% 行业:{s['industry']} "
                f"概念板块:{','.join(s.get('concepts', []))} "
                f"最新新闻:{'; '.join(s.get('news', []))} "
                f"重要公告:{ann_text}")
        stock_lines.append(line)

    hot_concepts = data.get("hot_concepts", [])
    concepts_text = ", ".join(
        f"{c['name']}({c['count']}只: {', '.join(c.get('stocks', [])[:3])})"
        for c in hot_concepts
    )

    prompt = f"""以下是今日A股人气排行榜TOP{len(stocks)}的完整数据。

## 分析维度（按重要性排序）
1. **消息驱动**（最重要）：该股上榜的核心催化剂是什么？仔细阅读"最新新闻"和"重要公告"，提取关键驱动（绑定大客户/政策利好/游资抱团/业绩超预期/大额订单等）
2. **持续性判断**：这个催化剂是一次性消息还是可持续的产业逻辑？所属板块是否为当日市场主线？板块内是否有龙头连板、梯队跟涨？
3. **资金特征**：从成交额、换手率判断资金参与力度，是主力建仓、游资接力还是散户跟风？量价配合如何？
4. **风险提示**：当前位置是否已高？是否存在利好兑现/高位出货/板块退潮风险？
5. **操作建议**：给出明确的短线建议（强势追涨/回调低吸/观望等待/逢高减仓），附具体条件触发点（如"回踩10日线可介入"、"放量突破X元可追"）

## 今日热点板块
{concepts_text}

## 上榜股票数据
{chr(10).join(stock_lines)}

## 输出要求
严格按JSON格式返回，key为股票代码(6位)，value为分析文本（120-200字）。
格式必须包含4个标签段：【催化剂】【持续性】【资金】【建议】
不要返回JSON以外的任何文字。
示例：{{"002356": "【催化剂】公司公告绑定华为昇腾384超节点，算力订单超50亿，叠加游资作手新一3亿买入。【持续性】算力为当日主线，板块龙头已3连板，后排持续有首板跟涨，属可持续产业逻辑。【资金】成交额12亿换手15%，龙虎榜出现机构席位，非纯游资行情。【建议】强势主线龙头，持仓者跟随趋势设止盈，未入场者回踩5日线可低吸，不宜追涨打板。"}}"""

    import httpx
    model = cfg.get("ai_model", "deepseek-v4-pro")
    payload = {
        "model": model,
        "max_tokens": 16384,  # V4 默认带思考链, 20只批量 JSON + reasoning token 需足够空间, 否则截断致 json.loads 失败
        "messages": [
            {"role": "system", "content": "你是资深A股短线交易分析师，擅长从消息面、资金面、技术面三维度研判个股短线机会。分析必须具体有据，直接给结论，不说废话。只返回JSON格式。"},
            {"role": "user", "content": prompt},
        ],
    }

    base_url = cfg.get("ai_base_url", "https://api.deepseek.com/v1")
    url = f"{base_url.rstrip('/')}/chat/completions"
    logger.info(f"[popularity_ai] 开始调用AI分析，模型={model}，股票数={len(stocks)}，endpoint={url}")
    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            logger.info(f"[popularity_ai] 第{attempt}次请求返回 status={resp.status_code}")
            if resp.status_code != 200:
                logger.warning(f"[popularity_ai] 非200响应: {resp.text[:500]}")
                continue
            data_resp = resp.json()
            content = data_resp["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(content)
            if isinstance(result, dict):
                logger.info(f"[popularity_ai] 分析完成，{len(result)}只股票")
                return result
            else:
                logger.warning(f"[popularity_ai] 返回非dict类型: {type(result)}")
        except Exception as e:
            logger.warning(f"[popularity_ai] 第{attempt}次失败: {e}")
    logger.error("[popularity_ai] 所有重试均失败，返回空结果")
    return {}


async def _ai_analyze_single_stock(stock: dict, hot_concepts: list[dict]) -> str:
    """单只个股的 AI 人气解读 — 4 段格式同批量版, 输出纯文本.

    用于"AI 解读"按钮的按需触发, 不依赖交易时段限制.
    """
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        return ""

    ann_text = "; ".join(stock.get("announcements", []))
    stock_line = (f"排名{stock.get('rank', '-')}(变化:{stock.get('rank_change', 0)}) "
                  f"{stock['name']}({stock['code']}) "
                  f"涨跌幅{stock.get('pct_change', 0)}% 成交额{stock.get('amount', 0)} "
                  f"换手率{stock.get('turnover', 0)}% 行业:{stock.get('industry', '')} "
                  f"概念:{','.join(stock.get('concepts', []))} "
                  f"新闻:{'; '.join(stock.get('news', []))} "
                  f"公告:{ann_text}")
    concepts_text = ", ".join(
        f"{c['name']}({c['count']}只)" for c in hot_concepts[:8]
    )

    prompt = f"""请对下面这只人气榜个股做一份 4 段式短线解读 (120-200 字).

## 今日热点板块上下文
{concepts_text}

## 个股数据
{stock_line}

## 输出要求
- 4 段标签必须齐全, 顺序固定: 【催化剂】【持续性】【资金】【建议】
- 不要返回 JSON, 不要 markdown, 直接给一段连续文本, 4 个【】标签接在一起
- 建议段必须明确: 强势追涨 / 回调低吸 / 观望等待 / 逢高减仓 之一, 附触发条件 (如 "回踩10日线可介入"/"放量突破X元可追")
- 不说废话, 直给结论"""

    import httpx
    model = cfg.get("ai_model", "deepseek-v4-pro")
    payload = {
        "model": model,
        "max_tokens": 4096,  # V4 带思考链, 单股 200字解读 + reasoning token 需留足空间
        "messages": [
            {"role": "system", "content": "你是资深A股短线交易分析师，擅长从消息面、资金面、技术面三维度研判个股短线机会。直接给结论, 不说废话。"},
            {"role": "user", "content": prompt},
        ],
    }
    base_url = cfg.get("ai_base_url", "https://api.deepseek.com/v1")
    url = f"{base_url.rstrip('/')}/chat/completions"
    logger.info(f"[popularity_ai] 单股按需分析 code={stock['code']} model={model}")
    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url, json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"[popularity_ai_single] {stock['code']} 非200: {resp.text[:300]}")
                continue
            content = resp.json()["choices"][0]["message"]["content"].strip()
            # 去掉可能的 markdown 代码块
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return content
        except Exception as e:
            logger.warning(f"[popularity_ai_single] {stock['code']} 第{attempt}次失败: {e}")
    return ""


async def analyze_stock_on_demand(code: str, trade_date: str | None = None) -> tuple[str, str]:
    """从最新人气快照里找 code, 跑单股 AI, 回写到快照 JSON, 返回 (analysis, refreshed_at).

    refreshed_at 格式 'YYYY-MM-DD HH:MM:SS'; 失败返回 ('', '').
    """
    row = await repository.get_popularity_snapshot(trade_date)
    if not row or not row.get("data"):
        return "", ""
    data = row["data"]
    stocks = data.get("stocks", [])
    target = next((s for s in stocks if s.get("code") == code), None)
    if not target:
        return "", ""
    analysis = await _ai_analyze_single_stock(target, data.get("hot_concepts", []))
    if not analysis:
        return "", ""
    refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target["ai_analysis"] = analysis
    target["ai_analysis_at"] = refreshed_at
    await repository.upsert_popularity_snapshot(row["trade_date"], data)
    return analysis, refreshed_at


async def refresh_popularity():
    global _last_run_time
    if not _should_run():
        return
    _last_run_time = datetime.now()
    await refresh_popularity_now(skip_ai=not _is_market_time())


async def refresh_popularity_now(skip_ai: bool = False):
    trade_date = datetime.now().strftime("%Y-%m-%d")
    data = await data_fetcher.get_popularity_full(20)

    if not data or not data.get("stocks"):
        logger.warning("Popularity refresh: no data returned")
        return

    if not skip_ai:
        try:
            ai_results = await _ai_analyze_popularity(data)
            if ai_results:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for stock in data["stocks"]:
                    analysis = ai_results.get(stock["code"], "")
                    if analysis:
                        stock["ai_analysis"] = analysis
                        stock["ai_analysis_at"] = now_str
        except Exception as e:
            logger.warning(f"[popularity_ai] AI分析异常，跳过: {e}")
    else:
        logger.info("[popularity] 非交易时段，跳过AI分析")

    await repository.upsert_popularity_snapshot(trade_date, data)


async def refresh_popularity_full_ai():
    """v1.7.166: 16:00 / 19:00 定时全量 AI 重刷入口.

    强制跑 _ai_analyze_popularity (绕过 _should_run 节流与 _is_market_time 过滤),
    给所有 TOP20 股票重新生成 AI 解读, 覆盖之前可能在盘中 / 早盘生成的旧版本.
    """
    logger.info("[popularity] 定时全量 AI 重刷启动 (16:00/19:00 计划任务)")
    await refresh_popularity_now(skip_ai=False)


async def record_daily_popularity():
    """每晚22:00拉全量自选股人气排名存档到 cfzy_biz_popularity_daily.

    用于回溯任意历史日期的个股人气, 供回测/区间复盘/情绪分析用.
    """
    from datetime import date as date_cls
    from backend.models.repo.stocks import list_all_stocks
    from backend.fetcher.popularity import get_popularity_rank_for_codes

    today = date_cls.today()
    record_date = today.strftime("%Y-%m-%d")

    # 获取自选池所有股票代码
    pool = await list_all_stocks(include_deleted=False)
    all_codes = [s["code"] for s in pool if s.get("code")]

    if not all_codes:
        logger.warning("[popularity_daily] 自选池为空, 跳过")
        return

    logger.info(f"[popularity_daily] 开始拉取 {len(all_codes)} 只自选股人气排名")

    # 批量拉取人气排名
    rank_map = await get_popularity_rank_for_codes(all_codes)

    if not rank_map:
        logger.warning("[popularity_daily] 人气排名拉取为空, 跳过")
        return

    # 批量写入 DB
    from backend.models.repo._db import _executemany
    rows = [(code, record_date, rank) for code, rank in rank_map.items()]
    if rows:
        await _executemany(
            # `rank` 是 MySQL 8.0 保留字(RANK()窗口函数), 列名必须反引号, 否则语法报错(建表/SELECT侧早已加)
            "INSERT INTO cfzy_biz_popularity_daily (code, record_date, `rank`) "
            "VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE `rank` = VALUES(`rank`)",
            rows,
        )

    logger.info(f"[popularity_daily] 完成: {len(rows)}/{len(all_codes)} 只写入人气排名 (日期={record_date})")
