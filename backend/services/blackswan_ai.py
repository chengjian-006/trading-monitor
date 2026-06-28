"""黑天鹅预警·AI 逐股研判 - v1.7.x.

在「自选股黑天鹅预警」风险公告区给每只命中风险的票加一句 AI 研判
(严重度🔴高/🟡中/⚪低 + 风险研判 + 对持仓影响)。

纯函数(可单测, 不联网):
  extract_pdf_text   : 解析巨潮公告 PDF 正文(截断控 token)
  group_hits_by_stock: 命中公告按股分组
  build_risk_prompt  : 逐股研判 prompt
  parse_risk_verdict : 解析 AI 严重度+研判句

编排(联网, 三层兜底绝不卡 18:30 推送):
  generate_risk_verdicts : 每股下载命中公告 PDF → 喂 DeepSeek → {code: verdict}
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

# 正文截断上限(控 token; 单份公告取前部即可覆盖核心信息)
PDF_MAX_CHARS = 4000
SEVERITY_EMOJI = {"高": "🔴", "中": "🟡", "低": "⚪"}

# 编排闸门(三层兜底之硬上限)
MAX_STOCKS = 10        # 单次研判股票数上限(超出略过+log)
CONCURRENCY = 3        # 并发上限
AI_TIMEOUT = 30        # 单股 AI 调用超时(秒)
PDF_TIMEOUT = 20       # 单份 PDF 下载超时(秒)


def extract_pdf_text(pdf_bytes: bytes, max_chars: int = PDF_MAX_CHARS) -> str:
    """解析 PDF 字节为正文文本(截断至 max_chars)。解析失败返回空串(兜底退回只用标题)。"""
    if not pdf_bytes:
        return ""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        parts = []
        total = 0
        for page in doc:
            t = page.get_text() or ""
            parts.append(t)
            total += len(t)
            if total >= max_chars:
                break
        doc.close()
        return "".join(parts)[:max_chars]
    except Exception as e:
        logger.warning(f"[blackswan_ai] PDF 解析失败: {e}")
        return ""


def group_hits_by_stock(hits: list[dict], max_per_stock: int = 3) -> dict[str, dict]:
    """命中公告按股分组(保留最近 max_per_stock 条代表公告控 token)。
    返回 {code: {name, hits:[hit,...]}}。"""
    out: dict[str, dict] = {}
    for h in hits:
        code = str(h.get("code", ""))
        if not code:
            continue
        g = out.setdefault(code, {"name": h.get("name", code), "hits": []})
        if len(g["hits"]) < max_per_stock:
            g["hits"].append(h)
    return out


def build_risk_prompt(code: str, name: str, hits: list[dict], pdf_text: str) -> tuple[str, str]:
    """逐股研判 prompt。要求 AI 输出 严重度(高/中/低)+一句研判 的 JSON。"""
    system = (
        "你是A股风控助手。下面是某只自选股最近命中风险规则的公告(标题+风险标签+正文节选)。"
        "请判断该风险对持有人的实际严重程度并给一句研判。\n"
        "严格只输出一个 JSON 对象, 字段:\n"
        '  severity(必须是 高/中/低 之一: 高=可能重大利空/退市风险/财务造假/立案处罚实锤; '
        '中=需警惕但未定性/问询/非标; 低=程序性/常规/影响有限),\n'
        '  verdict(一句话研判≤40字, 含风险性质判断+对持仓影响, 不要套话)。\n'
        "区分常规审核(如交易所例行问询)与真雷(如造假立案)。只输出JSON, 无额外文字。"
    )
    titles = "；".join(f"[{h.get('tags','')}] {h.get('title','')}" for h in hits)
    user = (f"股票: {name}({code})\n命中公告: {titles}\n\n公告正文节选:\n{pdf_text or '(正文未取到, 仅据标题与风险标签判断)'}")
    return system, user


def parse_risk_verdict(text: str) -> dict | None:
    """解析 AI 回复为 {severity, emoji, text}。失败/严重度非法返回 None(该股略过研判)。"""
    import json

    if not text:
        return None
    s = text.strip()
    if "```" in s:
        for p in s.split("```"):
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                s = p
                break
    lo, hi = s.find("{"), s.rfind("}")
    if lo == -1 or hi == -1 or hi < lo:
        return None
    try:
        obj = json.loads(s[lo:hi + 1])
    except Exception:
        return None
    sev = str(obj.get("severity", "")).strip()
    verdict = str(obj.get("verdict", "")).strip()
    if sev not in SEVERITY_EMOJI or not verdict:
        return None
    return {"severity": sev, "emoji": SEVERITY_EMOJI[sev], "text": verdict}


# ── 编排: 每股下PDF → 喂AI → 解析。三层兜底绝不卡 18:30 推送 ──

async def _fetch_pdf_text(client, url: str) -> str:
    """下载单份公告 PDF 并解析正文。失败返回空(第一层兜底: 退回只用标题+风险标签)。"""
    if not url:
        return ""
    try:
        r = await client.get(url, timeout=PDF_TIMEOUT)
        if r.status_code == 200 and r.content:
            return extract_pdf_text(r.content)
    except Exception as e:
        logger.warning(f"[blackswan_ai] PDF 下载失败 {url}: {e}")
    return ""


async def _call_ai(cfg: dict, system: str, user: str) -> str:
    """同步 OpenAI SDK 卸线程池(复用 ai_analyst 模式)。"""
    from openai import OpenAI
    client = OpenAI(api_key=cfg.get("anthropic_api_key", ""),
                    base_url=cfg.get("ai_base_url", "https://api.deepseek.com/v1"))
    resp = await asyncio.to_thread(lambda: client.chat.completions.create(
        model=cfg.get("ai_model", "deepseek-chat"),
        max_tokens=1024,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    ))
    return resp.choices[0].message.content or ""


async def _verdict_one(sem, http_client, code: str, name: str, hits: list[dict], cfg: dict):
    """单股: 下PDF→拼正文→喂AI→解析。任何失败→该股略过(第二层兜底)。"""
    async with sem:
        texts: list[str] = []
        for h in hits:
            t = await _fetch_pdf_text(http_client, h.get("url", ""))
            if t:
                texts.append(t)
            if sum(len(x) for x in texts) >= PDF_MAX_CHARS:
                break
        pdf_text = "".join(texts)[:PDF_MAX_CHARS]
        system, user = build_risk_prompt(code, name, hits, pdf_text)
        try:
            content = await asyncio.wait_for(_call_ai(cfg, system, user), timeout=AI_TIMEOUT)
        except Exception as e:
            logger.warning(f"[blackswan_ai] {name}({code}) AI 研判失败/超时: {e}")
            return code, None
        return code, parse_risk_verdict(content)


async def generate_risk_verdicts(hits: list[dict]) -> dict[str, dict]:
    """对风险公告命中逐股出 AI 研判。返回 {code: {severity, emoji, text}}。
    三层兜底: PDF失败退标题研判 / AI失败略过该股 / 股数超 MAX_STOCKS 截断。无 key 或空命中返回 {}。"""
    if not hits:
        return {}
    from backend.core.config import load_config
    cfg = load_config()
    if not cfg.get("anthropic_api_key"):
        logger.warning("[blackswan_ai] 未配置 AI key, 跳过研判")
        return {}
    grouped = group_hits_by_stock(hits)
    items = list(grouped.items())
    if len(items) > MAX_STOCKS:
        logger.info(f"[blackswan_ai] 命中 {len(items)} 只超上限 {MAX_STOCKS}, 截断(其余略过研判)")
        items = items[:MAX_STOCKS]
    sem = asyncio.Semaphore(CONCURRENCY)
    import httpx
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        results = await asyncio.gather(
            *[_verdict_one(sem, http_client, code, g["name"], g["hits"], cfg) for code, g in items])
    return {code: v for code, v in results if v}
