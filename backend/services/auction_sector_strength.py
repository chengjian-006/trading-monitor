"""09:26 竞价板块强弱推送 (v1.7.387)

9:25 集合竞价撮合完成后, 板块指数已有竞价开盘涨跌, 9:26 推一条纯硬数据卡
(与同时段 AI「开盘共性」卡互补, 不走 AI, AI 挂了这条照发):

1. 行业板块(腾讯 hy, 共31个一级行业): 竞价涨幅最强 top5 + 最弱 bottom5,
   3列表格(板块/涨幅/领涨股, 领涨股命中自选标红);
2. 概念板块(腾讯 gn, ~800个, 分页拉全 + 关键词黑名单去噪): 竞价涨幅 top5;
3. 昨日热点承接度: 昨收盘涨停题材热度 top5 vs 今晨对应概念竞价名次 → 承接/转弱
   (对应中线六二法"重点看资金是否回流板块"的口径);
4. 持仓所在板块竞价名次提示(best-effort, 行业名匹配不上则跳过该票)。

数据源: 腾讯板块榜(东财 prod IP 被封不可用)。接口已改版: sort_type=price 必填,
旧 sort=3 会 400, 拉回后按 zdf 客户端排序。竞价数据有发布延迟 → 重试到 9:29 硬封顶。
"""
import asyncio
import logging
import time as _time
from datetime import datetime

from backend.fetcher.http_client import HEADERS, _get_client
from backend.models import repository
from backend.services import notifier

logger = logging.getLogger(__name__)

# 概念板块去噪: 名称含这些关键词的不是真题材(成分类/通道类/权重类), 排除
_GN_NOISE_KEYWORDS = ("融资", "融券", "股通", "MSCI", "成分", "标的", "重仓",
                      "昨日", "指数", "富时", "标普", "样本", "AH")


def _is_trading_day(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5


async def _fetch_tx_boards(board_type: str) -> list[dict]:
    """腾讯板块榜: hy 行业(31个一把拉全) / gn 概念(~800个分页拉全)。
    返回按竞价涨幅(zdf)降序: [{name, pct, zgb, lb, leader_name, leader_code, leader_pct}]。
    接口现要求 sort_type=price(旧 sort=3 已 400), 排序在客户端按 zdf 做。"""
    client = _get_client()
    rows: list[dict] = []
    offset = 0
    while True:
        url = ("https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
               f"?board_type={board_type}&sort_type=price&direct=down&offset={offset}&count=200")
        try:
            resp = await client.get(url, headers=HEADERS)
            data = resp.json().get("data") or {}
        except Exception as e:
            logger.warning(f"[auction_sector] 腾讯{board_type}榜取数失败(offset={offset}): {e}")
            break
        batch = data.get("rank_list") or []
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        total = data.get("total")
        if not total or offset >= int(total):
            break

    out = []
    for it in rows:
        name = str(it.get("name") or "")
        if not name:
            continue
        try:
            pct = float(it.get("zdf") or 0)
        except (TypeError, ValueError):
            continue
        lzg = it.get("lzg") or {}
        leader_code = str(lzg.get("code") or "")
        if leader_code[:2] in ("sh", "sz", "bj"):
            leader_code = leader_code[2:]
        out.append({
            "name": name,
            "pct": pct,
            "zgb": str(it.get("zgb") or ""),                 # "涨家数/总家数"
            "lb": str(it.get("lb") or ""),                   # 量比
            "leader_name": str(lzg.get("name") or ""),
            "leader_code": leader_code,
            "leader_pct": float(lzg.get("zdf") or 0) if lzg.get("zdf") else None,
        })
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


def _filter_gn_noise(boards: list[dict]) -> list[dict]:
    return [b for b in boards if not any(k in b["name"] for k in _GN_NOISE_KEYWORDS)]


def _norm_theme(s: str) -> str:
    """题材/概念名归一: 去掉"概念/板块"后缀方便互相包含匹配。"""
    return (s or "").replace("概念", "").replace("板块", "").strip()


def _match_board(theme: str, boards: list[dict]) -> tuple[int, dict] | None:
    """昨日涨停题材名 → 今晨概念板块(含名次, 1-based)。
    精确同名(归一后)直接命中; 否则互相包含取板块名最长(最具体)的, 防"存储芯片"撞到泛"芯片概念"。"""
    t = _norm_theme(theme)
    if not t:
        return None
    best: tuple[int, int, dict] | None = None   # (板块名长度, 名次, board)
    for i, b in enumerate(boards, 1):
        bn = _norm_theme(b["name"])
        if not bn:
            continue
        if bn == t:
            return i, b
        if (t in bn or bn in t) and (best is None or len(bn) > best[0]):
            best = (len(bn), i, b)
    return (best[1], best[2]) if best else None


async def _yesterday_top_themes(today: str, top_n: int = 5) -> list[dict]:
    """昨收盘涨停题材热度 top N (cfzy_sys_theme_heat 最近一个早于今天的快照日)。
    表内 trade_date 为 '20260612' 无连字符格式 → 统一去连字符后比较。"""
    try:
        rows = await repository.get_theme_heat(days=6)
    except Exception as e:
        logger.warning(f"[auction_sector] 题材热度取数失败: {e}")
        return []
    today_c = today.replace("-", "")
    def _d(r) -> str:
        return str(r["trade_date"])[:10].replace("-", "")
    prev_dates = sorted({_d(r) for r in rows if _d(r) < today_c})
    if not prev_dates:
        return []
    prev = prev_dates[-1]
    day_rows = [r for r in rows if _d(r) == prev]
    day_rows.sort(key=lambda r: int(r.get("limit_up_count") or 0), reverse=True)
    return [{"theme": r["theme"], "count": int(r.get("limit_up_count") or 0), "date": prev}
            for r in day_rows[:top_n]]


async def _yesterday_limitup_by_theme(prev_date: str) -> dict[str, list[dict]]:
    """昨日涨停池按题材(reason 首段, 与 theme_heat 同口径)分组 → {题材: [{code,name}]}。
    theme_heat 的 sample_codes 只存名字, 拿不到代码 → 直接重拉昨日涨停池(THS 支持历史日期)。"""
    from backend.fetcher.limit_pool import get_limit_pool
    try:
        pool = await get_limit_pool(prev_date)
    except Exception as e:
        logger.warning(f"[auction_sector] 昨日涨停池({prev_date})取数失败: {e}")
        return {}
    groups: dict[str, list[dict]] = {}
    for b in (pool or {}).get("boards") or []:
        reason = (b.get("reason") or "").strip()
        theme = reason.split("+")[0].strip() if reason else ""
        code = str(b.get("code") or "").zfill(6)
        if not theme or not code.isdigit():
            continue
        groups.setdefault(theme, []).append({"code": code, "name": b.get("name") or code})
    return groups


# ── 承接度判级 + 操作指引 (v1.7.394: 板块面+昨停股竞价溢价双口径, 落到操作) ──
# 溢价 premium = 昨日该题材涨停股今晨竞价涨幅均值 — 隔日承接的核心指标
# (此前竞价弱转强回测结论: edge 在隔日反包, 竞价溢价直接反映接力资金意愿)

def _relay_verdict(board_pct: float | None, board_rank: int | None, total: int,
                   premium: float | None) -> tuple[str, str]:
    """承接判级: (等级, 图标)。板块面强 + 昨停股溢价高 = 强承接; 任一面明显走弱 = 转弱。"""
    strong_board = board_pct is not None and (
        board_pct >= 1.0 or (board_rank is not None and board_rank <= max(3, total // 10)))
    if premium is not None and premium >= 3.0 and strong_board:
        return "强承接", "✅"
    if strong_board and (premium is None or premium >= 0):
        return "承接", "✅"
    if (premium is not None and premium < -1.0) or (board_pct is not None and board_pct < 0):
        return "转弱", "⚠️"
    # 中性图标用 ○: 原来的长破折号"—"和"一般"拼一起显示成"—一般", 像重复的"一"
    return "一般", "○"


async def _build_relay_data(themes: list[dict], gn_clean: list[dict],
                            pool: list[dict]) -> tuple[list[dict], str]:
    """承接度结构化数据 (v1.7.399 简洁版: 操作指引按判级是固定文案、板块名次与判级同义,
    全部冗余信息收进判级图标+图例一行, 明细进3列表格)。
    返回 ([{theme, count, level, icon, premium, up_n, total_n, my}], 整体定调)。"""
    from backend import data_fetcher
    lu_by_theme = await _yesterday_limitup_by_theme(themes[0]["date"]) if themes else {}
    all_codes = list({s["code"] for t in themes for s in lu_by_theme.get(t["theme"], [])})
    quotes: dict = {}
    if all_codes:
        try:
            quotes = await data_fetcher.get_realtime_quotes(all_codes) or {}
        except Exception as e:
            logger.warning(f"[auction_sector] 昨停股竞价行情失败: {e}")

    def _has_pool_stock(theme: str) -> bool:
        # 只允许"概念包含题材"方向(零售⊂新零售可以, 航天⊂商业航天不行), 防短词误吸
        t = _norm_theme(theme)
        return bool(t) and any(
            any(_norm_theme(c) == t or t in _norm_theme(c)
                for c in str(s.get("concepts") or "").split(",") if c.strip())
            for s in pool)

    rows: list[dict] = []
    n_ok = n_weak = 0
    for t in themes:
        m = _match_board(t["theme"], gn_clean)
        board_pct, board_rank = (m[1]["pct"], m[0]) if m else (None, None)

        # 昨停股竞价溢价
        qs = [quotes[s["code"]] for s in lu_by_theme.get(t["theme"], [])
              if quotes.get(s["code"]) and quotes[s["code"]].get("price")]
        premium, up_n = None, 0
        if qs:
            pcts = [float(q.get("pct_change") or 0) for q in qs]
            premium = sum(pcts) / len(pcts)
            up_n = sum(1 for p in pcts if p > 0)

        level, icon = _relay_verdict(board_pct, board_rank, len(gn_clean), premium)
        if icon == "✅":
            n_ok += 1
        elif icon == "⚠️":
            n_weak += 1
        rows.append({"theme": t["theme"], "count": t["count"], "level": level, "icon": icon,
                     "premium": premium, "up_n": up_n, "total_n": len(qs),
                     "my": _has_pool_stock(t["theme"])})

    # 整体定调
    if n_ok >= 3:
        mood = "热点延续日: 顺主线做, 低吸为主"
    elif n_weak >= 2 and n_ok <= 1:
        mood = "退潮日: 控仓观望, 别接力昨日热点"
    else:
        mood = "分化轮动日: 只做承接住的方向, 弱的回避"
    summary = f"**承接{n_ok}/转弱{n_weak}/共{len(themes)}** — {mood}"
    return rows, summary


def _board_row(b: dict, my_codes: set[str]) -> dict:
    """单板块一行(移动优化 markdown 表格, 2列): 涨幅独占前置短列, 板块名+领涨并进一格。
    (原原生 3 列表格手机端长内容被截需点开; 改 md 表格换行不截, 领涨命中自选仍标红。)"""
    leader = b["leader_name"] or ""
    if b["leader_code"] and b["leader_code"] in my_codes:
        leader = f"<font color='red'>{leader[:5]}</font>"   # 命中自选标红
    else:
        leader = leader[:5]
    pct_color = "red" if b["pct"] >= 0 else "green"
    name = b["name"][:6]
    return {
        "pct": f"<font color='{pct_color}'>{b['pct']:+.1f}%</font>",
        "bk": f"{name}　{leader}" if leader else name,
    }


async def build_auction_sector_part() -> tuple[list[str], list] | None:
    """计算竞价板块强弱 → (企微文本行, 飞书elements); 非交易日/榜未就绪→None。

    供 run_auction_0926 合并卡 与 run_auction_sector_strength 独立推送 共用(v1.7.553 抽出)。
    """
    if not _is_trading_day():
        logger.info("[auction_sector] 非交易日, 跳过")
        return None

    t0 = _time.time()
    today = datetime.now().strftime("%Y-%m-%d")

    # 竞价数据源有发布延迟 → 重试到 09:29 硬封顶 (与 auction_summary 同款机制)
    DEADLINE = "09:29:00"
    attempt = 0
    hy: list[dict] = []
    gn: list[dict] = []
    while True:
        attempt += 1
        hy, gn = await asyncio.gather(_fetch_tx_boards("hy"), _fetch_tx_boards("gn"))
        # ready 判定: 行业榜非空且涨跌幅已动(全 0 = 源还没刷出竞价价)
        if hy and any(abs(b["pct"]) > 0.01 for b in hy):
            break
        if datetime.now().strftime("%H:%M:%S") >= DEADLINE:
            logger.warning(f"[auction_sector] 到{DEADLINE}板块榜仍未就绪(第{attempt}次), 放弃本日推送")
            return None
        logger.info(f"[auction_sector] 第{attempt}次板块榜未就绪, 20s后重试 (竞价数据延迟)")
        await asyncio.sleep(20)

    gn_clean = _filter_gn_noise(gn)

    # 指数竞价开盘一行(best-effort) + 自选池代码(标红用, 含持仓)
    from backend.services import ai_analyst
    indices, pool = await asyncio.gather(
        asyncio.to_thread(ai_analyst.get_market_indices),
        repository.list_stocks(1),
        return_exceptions=True,
    )
    if isinstance(indices, Exception):
        indices = []
    if isinstance(pool, Exception):
        pool = []
    my_codes = {s["code"] for s in pool}
    hold_stocks = [s for s in pool if s.get("status") == "hold"]

    hy_top, hy_bottom = hy[:5], hy[-5:][::-1]          # 最强/最弱各前5; 最弱按从弱到次弱排
    gn_top = gn_clean[:5]

    # 昨日热点承接度(板块面 + 昨停股竞价溢价 → 判级图标, 明细进表格)
    themes = await _yesterday_top_themes(today)
    relay_rows, relay_summary = (await _build_relay_data(themes, gn_clean, pool)) if themes else ([], "")

    # 持仓所在板块竞价名次(行业/概念名 best-effort 匹配)
    hold_rows: list[dict] = []
    all_boards = gn_clean + hy
    for s in hold_stocks[:8]:
        ind = (s.get("industry") or "").strip()
        if not ind:
            continue
        m = _match_board(ind, all_boards)
        if not m:
            continue
        _, b = m
        rank_in_hy = next((i for i, x in enumerate(hy, 1) if x["name"] == b["name"]), None)
        hold_rows.append({"name": s["name"], "pct": b["pct"],
                          # 板块名截4字: 与板块涨幅并入64%一列(约7字宽), "被动元件概念"全名装不下
                          "board": f"行业{rank_in_hy}/{len(hy)}" if rank_in_hy else b["name"][:4]})

    # ── 飞书 V2 卡: 精简重构 — 竞价最强TOP5 / 持仓板块 / 承接 ──
    from backend.services import lark_notifier
    idx_line = "　".join(
        f"{x['name']} {x['pct_change']:+.2f}%" for x in (indices or [])[:4]
    ) or "指数取数失败"
    elapsed = _time.time() - t0

    # header: 竞价时间 + 指数 + 用时
    elements = [
        lark_notifier.md_element(f"🕤 **09:26 集合竞价**　{idx_line}　|　腾讯板块榜 · 用时 {elapsed:.1f}s"),
    ]

    # 合并行业+概念 → "竞价最强 TOP5"
    merged = hy_top + gn_top
    merged.sort(key=lambda x: -x["pct"])
    top5 = merged[:5]
    cols = [
        {"name": "pct", "display_name": "涨幅", "data_type": "lark_md"},
        {"name": "bk", "display_name": "板块·领涨", "data_type": "lark_md"},
    ]
    def t_of(bs): return lark_notifier.md_table(cols, [_board_row(b, my_codes) for b in bs])
    elements.append(lark_notifier.md_element(f"🔥 **竞价最强 TOP5**（{len(hy)}行业 + {len(gn_clean)}概念）"))
    elements.append(t_of(top5))

    # 持仓关联
    if hold_rows:
        hold_cols = [
            {"name": "pct", "display_name": "涨幅", "data_type": "lark_md"},
            {"name": "nb", "display_name": "持仓·所在板块", "data_type": "lark_md"},
        ]
        hrows = [{"pct": f"<font color='{'red' if r['pct'] >= 0 else 'green'}'>{r['pct']:+.1f}%</font>",
                  "nb": f"{r['name']}　{r['board']}"}
                 for r in hold_rows]
        elements.append(lark_notifier.md_element("💼 **持仓关联板块**"))
        elements.append(lark_notifier.md_table(hold_cols, hrows))

    # 承接 (简化)
    if relay_rows:
        relay_cols = [
            {"name": "lv", "display_name": "承接·溢价", "data_type": "lark_md"},
            {"name": "theme", "display_name": "昨日热点", "data_type": "text"},
        ]
        rrows = []
        for r in relay_rows[:5]:
            lv_color = "red" if r["icon"] == "✅" else ("green" if r["icon"] == "⚠️" else "grey")
            cell = f"<font color='{lv_color}'>{r['icon']} {r['level']}</font>"
            if r["premium"] is not None:
                pc = "red" if r["premium"] >= 0 else "green"
                cell += f"　<font color='{pc}'>{r['premium']:+.1f}%</font>"
            rrows.append({"theme": ("⭐" if r["my"] else "") + r["theme"], "lv": cell})
        elements.append(lark_notifier.md_element(f"🔁 **昨日热点 · 今晨承接**　{relay_summary}"))
        elements.append(lark_notifier.md_table(relay_cols, rrows))
        elements.append(lark_notifier.md_element(
            "<font color='grey'>✅强承接 🔶一般 ⚠️转弱　溢价=昨停今竞均涨　⭐=自选有票</font>"))

    # footer
    elements.append(lark_notifier.md_element(
        "<font color='grey'>领涨红=命中自选 · 数据源:腾讯板块榜</font>"))

    # ── 企微纯文本回退 ──
    def tline(b: dict) -> str:
        leader = f" 领涨 {b['leader_name']} {b['leader_pct']:+.1f}%" if b["leader_name"] and b.get("leader_pct") is not None else ""
        return f"  {b['name']} {b['pct']:+.2f}% ({b['zgb']}){leader}"

    tlines = ["【竞价分析】", "", f"🕤 {idx_line}", "", "🔥 行业最强"]
    tlines += [tline(b) for b in hy_top]
    tlines += ["", "🎯 概念最强"]
    tlines += [tline(b) for b in gn_top]
    tlines += ["", "❄️ 行业最弱"]
    tlines += [tline(b) for b in hy_bottom]
    if relay_rows:
        tlines += ["", "🔁 昨日热点承接度"]
        for r in relay_rows:
            prem = (f" 溢价{r['premium']:+.1f}% {r['up_n']}/{r['total_n']}高开"
                    if r["premium"] is not None else "")
            star = "⭐" if r["my"] else ""
            tlines.append(f"  {star}{r['theme']}(昨{r['count']}板) {r['icon']}{r['level']}{prem}")
        tlines += [f"  {relay_summary.replace('**', '')}"]
    if hold_rows:
        tlines += ["", "💼 持仓所在板块"]
        tlines += [f"  {r['name']} → {r['board']} {r['pct']:+.1f}%" for r in hold_rows]

    logger.info(f"[auction_sector] 板块强弱计算完成, 行业{len(hy)} 概念{len(gn_clean)}, "
                f"承接{len(relay_rows)}条 持仓{len(hold_rows)}条, 耗时{elapsed:.1f}s")
    return tlines, elements


async def run_auction_sector_strength():
    """09:26 竞价板块强弱独立推送(现默认走合并卡 run_auction_0926, 本函数保留备用)。"""
    built = await build_auction_sector_part()
    if not built:
        return
    tlines, elements = built
    sent = await notifier.send_dual_card("\n".join(tlines), lark_title="📊 竞价分析", elements=elements)
    logger.info(f"[auction_sector] 独立推送结果={sent}")
