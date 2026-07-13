"""09:45 交易日「资金进攻方向」推送（确定性版, v1.7.587 重构）。

原 AI 版（deepseek 归纳成交额前20/涨幅前50）有两处硬伤已弃：
  1) 取数直打 push2.eastmoney，生产 IP 已被东财封 → 静默取空跳过，实际早已不推；
  2) LLM 归纳不确定、且无自选命中。

本版换成确定性双口径，全部走有备源的数据链，只推给自己（全局飞书 + PushPlus）：
  🔥 涨停扎堆题材 —— 读 sector_rotation 3min 快照（涨停池, 同花顺备源），资金真金白银；
  📊 领涨行业   —— get_sector_ranking（腾讯备源）；
  🔥双确认      —— 题材名与领涨行业软匹配，两口径共振 = 最强方向；
  ⭐ 自选/持仓命中 —— concepts 命中题材 / industry 命中领涨行业；
  情绪一句话     —— get_latest_emotion 派生 phase，冰点/退潮/数据降级 → 明说「无明显主线」。

调度: cron 09:45（开盘15分钟）, 复用任务 attack_direction_0945 / handler run_attack_direction_analysis。
"""
import logging
from datetime import datetime

from backend import data_fetcher
from backend.core.trading_calendar import is_workday
from backend.fetcher.limit_pool import get_limit_pool_cached
from backend.fetcher.sectors import get_sector_ranking
from backend.models import repository
from backend.services import notifier, sector_rotation as sr
from backend.services.lark_notifier import md_element, md_table

logger = logging.getLogger(__name__)


# ── 共享东财取数工具 (供 market_report / auction_summary_analyst 复用, 本模块 09:45 版已不用) ──
# m:0+t:6 深主板, m:0+t:80 创业板, m:1+t:2 沪主板, m:1+t:23 科创板
_EM_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_FIELDS = "f2,f3,f5,f6,f12,f14,f20"   # 价/涨跌%/量/成交额/code/name/流通市值


async def _fetch_em_top(by_field: str, top_n: int, order: str = "desc") -> list[dict]:
    """按 by_field='f3'(涨幅) 或 'f6'(成交额) 拉前 top_n 只。order='desc' 降序(默认), 'asc' 升序(跌幅榜)。"""
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
        logger.warning(f"[attack_dir] fetch top {by_field} failed: {e}")
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
        logger.warning(f"[attack_dir] concepts fetch failed: {e}")
        return
    for r in rows:
        c = concepts_map.get(r["code"], [])
        r["concepts"] = c[:3] if c else []

# ── 展示 / 判定阈值 ──
_THEME_TOP = 4          # 涨停扎堆题材最多列几个
_INDUSTRY_TOP = 5       # 领涨行业最多列几个
_THEME_MIN_LU = 2       # 「扎堆」下限: 涨停 ≥ 2 家才算一个方向
_WATCH_MAX = 10         # 自选命中最多列几条
_STATE_ORDER = {"启动": 0, "高潮": 1, "升温": 2, "持平": 3, "退潮": 4, "冷": 5}
# 「无明显主线」判据: 情绪冷 或 (无题材扎堆 且 领涨行业也弱)
_COLD_PHASES = {"冰点", "退潮", "数据降级"}
_STRONG_THEME_LU = 3    # 有题材涨停 ≥ 3 家 → 算有主线
_STRONG_IND_PCT = 2.0   # 有行业涨幅 ≥ 2% → 算有主线


def _is_trading_day(now: datetime | None = None) -> bool:
    return is_workday(now or datetime.now())


# ── 取数 ──
async def _load_hot_themes(date: str, date_compact: str) -> list[dict]:
    """涨停扎堆题材: 优先读 sector_rotation 3min 快照, 空则现拉涨停池聚合兜底。

    返回按涨停家数降序、限量的 [{theme, state, limit_up, max_height, broken, samples}]。"""
    items: list[dict] = []
    try:
        row = await repository.get_sector_rotation(date)
        items = ((row or {}).get("rotation_data") or {}).get("items") or []
    except Exception as e:
        logger.warning(f"[attack_dir] 读轮动快照失败: {e}")
    if not items:
        # 兜底: 09:45 快照理应已由 3min 扫描生成; 若无(重启/未扫)现拉一次涨停池聚合
        try:
            pool = await get_limit_pool_cached(date_compact)
            agg = sr.aggregate_themes((pool or {}).get("boards") or [])
            items = [{"theme": t, "state": "", **m} for t, m in agg.items()]
        except Exception as e:
            logger.warning(f"[attack_dir] 涨停池兜底聚合失败: {e}")
            items = []
    hot = [it for it in items if int(it.get("limit_up") or 0) >= _THEME_MIN_LU]
    hot.sort(key=lambda x: (-int(x.get("limit_up") or 0),
                            _STATE_ORDER.get(x.get("state"), 9)))
    return hot[:_THEME_TOP]


async def _load_lead_industries() -> list[dict]:
    """领涨行业: 板块涨幅榜(腾讯备源), 取涨幅 > 0 的前 N。"""
    try:
        rank = await get_sector_ranking(top_n=12)
    except Exception as e:
        logger.warning(f"[attack_dir] 板块榜取数失败: {e}")
        return []
    lead = [r for r in (rank or []) if float(r.get("pct_today") or 0) > 0]
    return lead[:_INDUSTRY_TOP]


def _cross_confirm(theme: str, industries: list[dict]) -> bool:
    """题材名与某领涨行业名软匹配(任一方向包含), 两口径共振 = 双确认。"""
    for r in industries:
        ind = str(r.get("industry") or "")
        if ind and (theme in ind or ind in theme):
            return True
    return False


async def _match_watchlist(hot_themes: list[dict], lead: list[dict]) -> list[dict]:
    """自选/持仓落在今日进攻方向里的票。concepts 命中题材 优先于 industry 命中行业。"""
    try:
        stocks = await repository.list_all_stocks()
    except Exception as e:
        logger.warning(f"[attack_dir] 读自选池失败: {e}")
        return []
    theme_names = [it["theme"] for it in hot_themes if it.get("theme")]
    ind_names = [str(r.get("industry") or "") for r in lead if r.get("industry")]
    out: list[dict] = []
    for s in stocks:
        code = s.get("code") or ""
        name = s.get("name") or code
        hold = s.get("status") == "hold"
        concepts = s.get("concepts") or ""
        industry = s.get("industry") or ""
        hit_theme = next((t for t in theme_names if t and t in concepts), None)
        if hit_theme:
            out.append({"name": name, "code": code, "hold": hold,
                        "where": hit_theme, "strong": True})
            continue
        if industry and industry in ind_names:
            out.append({"name": name, "code": code, "hold": hold,
                        "where": industry, "strong": False})
    # 持仓优先、题材命中(强)优先
    out.sort(key=lambda x: (not x["hold"], not x["strong"]))
    return out[:_WATCH_MAX]


def _emotion_line(emo: dict | None, hot_themes: list[dict],
                  lead: list[dict]) -> tuple[str, bool]:
    """返回 (情绪展示行, 是否无明显主线)。"""
    phase = (emo or {}).get("emotion_phase") or ""
    seal = (emo or {}).get("seal_rate")
    lu = (emo or {}).get("limit_up_count")
    hb = (emo or {}).get("highest_board")
    bits: list[str] = []
    if phase:
        bits.append(phase)
    if seal is not None:
        bits.append(f"封板率{float(seal) * 100:.0f}%")
    if lu is not None:
        bits.append(f"涨停{int(lu)}家")
    if hb is not None:
        bits.append(f"最高{int(hb)}板")
    metric = " · ".join(bits) if bits else "情绪数据暂缺"

    top_lu = max((int(it.get("limit_up") or 0) for it in hot_themes), default=0)
    top_pct = max((float(r.get("pct_today") or 0) for r in lead), default=0.0)
    no_main = (phase in _COLD_PHASES) or (
        top_lu < _STRONG_THEME_LU and top_pct < _STRONG_IND_PCT)
    return metric, no_main


# ── 渲染 ──
def _rep_lines(items: list[dict], max_names: int = 3) -> list[str]:
    """代表股逐题材一行(手机端表格单元格塞长股名列表会被截断, 故移到表下)。"""
    out = []
    for a in items:
        s = a.get("samples") or []
        if not s:
            continue
        head = "、".join(s[:max_names])
        more = f" 等{len(s)}只" if len(s) > max_names else ""
        out.append(f"• {a['theme']}: {head}{more}")
    return out


def _theme_tag(it: dict, confirmed: bool) -> str:
    if confirmed:
        return "🔥双确认"
    return it.get("state") or ""


def _build_card(hot_themes: list[dict], lead: list[dict], watch_hits: list[dict],
                emotion_metric: str, no_main: bool) -> list[dict]:
    elements: list[dict] = []
    # 情绪 / 主线判定行
    if no_main:
        elements.append(md_element(
            f"⚠️ **资金分散·无明显主线**，观望为主\n情绪：{emotion_metric}"))
    else:
        elements.append(md_element(f"**主线清晰** ｜ 情绪：{emotion_metric}"))

    # 🔥 涨停扎堆题材(资金真金白银)
    if hot_themes:
        elements.append(md_element("🔥 **涨停扎堆题材**（资金真金白银）"))
        cols = [{"name": "theme", "display_name": "题材", "data_type": "text"},
                {"name": "lu", "display_name": "涨停", "data_type": "text"}]
        rows = []
        for it in hot_themes:
            tag = _theme_tag(it, _cross_confirm(it["theme"], lead))
            rows.append({
                "theme": it["theme"] + (f"·{tag}" if tag else ""),
                "lu": f"{int(it.get('limit_up') or 0)}家"
                      + (f"·最高{int(it['max_height'])}板" if it.get("max_height") else ""),
            })
        elements.append(md_table(cols, rows))
        reps = _rep_lines(hot_themes)
        if reps:
            elements.append(md_element("代表股\n" + "\n".join(reps)))
    else:
        elements.append(md_element("🔥 涨停扎堆题材：暂无题材涨停扎堆"))

    # 📊 领涨行业(板块涨幅榜)
    if lead:
        elements.append(md_element("📊 **领涨行业**（板块涨幅榜）"))
        cols = [{"name": "ind", "display_name": "行业", "data_type": "text"},
                {"name": "pct", "display_name": "涨幅", "data_type": "text"}]
        rows = [{"ind": r.get("industry") or "",
                 "pct": f"+{float(r.get('pct_today') or 0):.1f}%"} for r in lead]
        elements.append(md_table(cols, rows))

    # ⭐ 自选/持仓命中
    if watch_hits:
        lines = []
        for h in watch_hits:
            who = "持仓" if h["hold"] else "自选"
            suffix = "今日主攻" if h["strong"] else "领涨行业"
            lines.append(f"• {h['name']}({h['code']}) {who} → 在【{h['where']}】{suffix}")
        elements.append(md_element("⭐ **你的自选/持仓命中**\n" + "\n".join(lines)))
    else:
        elements.append(md_element("⭐ 自选/持仓暂未命中今日进攻方向"))

    return elements


def _build_text(hot_themes: list[dict], lead: list[dict], watch_hits: list[dict],
                emotion_metric: str, no_main: bool) -> str:
    lines = ["📈 今日资金进攻方向 · 09:45"]
    lines.append(("⚠️ 资金分散·无明显主线，观望为主" if no_main else "主线清晰")
                 + f"（{emotion_metric}）")
    lines.append("")
    if hot_themes:
        lines.append("🔥 涨停扎堆题材（资金真金白银）")
        for it in hot_themes:
            tag = _theme_tag(it, _cross_confirm(it["theme"], lead))
            s = "、".join((it.get("samples") or [])[:3])
            lines.append(f" · {it['theme']}{('·' + tag) if tag else ''} "
                         f"涨停{int(it.get('limit_up') or 0)}家 {s}".rstrip())
    else:
        lines.append("🔥 涨停扎堆题材：暂无")
    if lead:
        lines.append("")
        lines.append("📊 领涨行业（板块涨幅榜）")
        for r in lead:
            lines.append(f" · {r.get('industry') or ''} +{float(r.get('pct_today') or 0):.1f}%")
    lines.append("")
    if watch_hits:
        lines.append("⭐ 你的自选/持仓命中")
        for h in watch_hits:
            who = "持仓" if h["hold"] else "自选"
            suffix = "今日主攻" if h["strong"] else "领涨行业"
            lines.append(f" · {h['name']}({h['code']}) {who} → 【{h['where']}】{suffix}")
    else:
        lines.append("⭐ 自选/持仓暂未命中今日进攻方向")
    return "\n".join(lines)


# ── 入口 ──
async def run_attack_direction_analysis():
    """09:45 交易日「资金进攻方向」推送(确定性版)。"""
    now = datetime.now()
    if not _is_trading_day(now):
        logger.info("[attack_dir] 非交易日, 跳过")
        return
    date = now.strftime("%Y-%m-%d")
    date_compact = now.strftime("%Y%m%d")

    hot_themes = await _load_hot_themes(date, date_compact)
    lead = await _load_lead_industries()
    if not hot_themes and not lead:
        logger.warning("[attack_dir] 题材/行业两口径均空, 数据源可能不可用, 跳过")
        return

    try:
        emo = await repository.get_latest_emotion()
    except Exception as e:
        logger.warning(f"[attack_dir] 读情绪快照失败: {e}")
        emo = None
    watch_hits = await _match_watchlist(hot_themes, lead)
    emotion_metric, no_main = _emotion_line(emo, hot_themes, lead)

    elements = _build_card(hot_themes, lead, watch_hits, emotion_metric, no_main)
    text = _build_text(hot_themes, lead, watch_hits, emotion_metric, no_main)
    sent = await notifier.send_dual_card(
        text, lark_title="📈 今日资金进攻方向·09:45", elements=elements)
    logger.info(f"[attack_dir] 推送={sent} 题材{len(hot_themes)} 行业{len(lead)} "
                f"自选命中{len(watch_hits)} 无主线={no_main}")
