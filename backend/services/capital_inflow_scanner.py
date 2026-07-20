"""资金回流·板块预警 (v1.7.17)

每 30 秒(盘中)扫所有板块,找出满足:
  板块涨 ≥ 1% + 龙头涨停 ≥ 9.5% + 全市场前5板块均涨 ≥ 5%
的板块,推送企微消息(含板块名/龙头/涨幅/该板块下用户自选的票)。
v1.7.x: 自选口径放宽到整个自选池(含观察票), 不再只限 关注/持仓。

当日同一板块只推送一次(进程级内存去重)。
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, date as date_cls

from backend.models import repository
from backend import data_fetcher
from backend.services import notifier
from backend.services import alert_throttle

logger = logging.getLogger(__name__)


from backend.core.trading_calendar import is_continuous_auction as _is_continuous_auction  # v1.7.x 统一来源


def _is_st(stock: dict) -> bool:
    return "ST" in (stock.get("name") or "").upper().strip()


def _first_num(*values) -> float:
    """取第一个"有效"数值(非 None 且非 0), 都没有则 0.0。

    v1.7.722 新增。替代 `rt.get(k, fallback)` 这种写法 —— dict.get 只在【键不存在】时才用默认值,
    而行情源常见的是"键在、值是 0/None", 那种写法的回退意图永远不会生效(0720 全 +0.00% 空卡的成因)。
    注: 真实涨幅恰为 0 时会继续往后取, 但后备源同样是 0, 结果仍是 0, 不影响正确性。
    """
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != 0:
            return f
    return 0.0


# 统一格式化 + 涨停判定(原本地副本已抽到 utils; 涨停判定顺带覆盖 301/689/92 等边界码)
from backend.utils.formatting import fmt_amount as _fmt_amount
from backend.utils.limit_calc import is_at_limit_up as _is_limit_up
from backend.utils.limit_calc import is_new_listing as _is_new_listing


# 进程级去重: { 日期字符串: {已推送板块名} }
_pushed_today: dict[str, set[str]] = {}


def _fmt_pct_range(lo: float, hi: float) -> str:
    """涨幅区间格式化: 相等时显示单值, 不等时显示 lo~hi。"""
    if abs(hi - lo) < 0.01:
        return f"{lo:+.2f}%"
    return f"{lo:+.2f}~{hi:+.2f}%"


def _fmt_rank(rank) -> str:
    """全市场成交额名次: 1~100 显示名次, 100名外显示 100+, 取不到显示 —。"""
    if rank is None:
        return "—"
    try:
        r = int(rank)
    except (ValueError, TypeError):
        return "—"
    return str(r) if 1 <= r <= 100 else "100+"


def _group_by_leader(items: list[dict]) -> list[dict]:
    """按 leader_code 去重合并: 同一只龙头股 = 同一波资金, 多板块联名展示。"""
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for it in items:
        key = it.get("leader_code") or it.get("leader_name") or it.get("sector_name") or ""
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(it)

    out: list[dict] = []
    for k in order:
        bucket = grouped[k]
        if len(bucket) == 1:
            out.append(bucket[0])
            continue
        # 合并: 板块名联名, 涨幅取范围, 关注个股按行去重并集
        sector_names = " / ".join(dict.fromkeys(g["sector_name"] for g in bucket))
        sector_pcts = [g["sector_pct"] for g in bucket]
        sector_top_avgs = [g["sector_top_avg"] for g in bucket]
        merged_lines: list[str] = []
        seen: set[str] = set()
        for g in bucket:
            for ln in g.get("stock_lines", []) or []:
                if ln not in seen:
                    seen.add(ln)
                    merged_lines.append(ln)
        merged_rows: list[dict] = []
        seen_codes: set[str] = set()
        for g in bucket:
            for r in g.get("stock_rows", []) or []:
                if r.get("code") and r["code"] not in seen_codes:
                    seen_codes.add(r["code"])
                    merged_rows.append(r)
        merged_top: list[dict] = []
        seen_top: set[str] = set()
        for g in bucket:
            for r in g.get("sector_top_rows", []) or []:
                if r.get("code") and r["code"] not in seen_top:
                    seen_top.add(r["code"])
                    merged_top.append(r)
        first = bucket[0]
        out.append({
            "sector_name": sector_names,
            "sector_pct_range": (min(sector_pcts), max(sector_pcts)),
            "leader_name": first["leader_name"],
            "leader_pct": first["leader_pct"],
            "sector_top_n": first["sector_top_n"],
            "sector_top_avg_range": (min(sector_top_avgs), max(sector_top_avgs)),
            "stock_lines": merged_lines,
            "stock_rows": merged_rows,
            "sector_top_rows": merged_top,
            "my_stocks_count": len(merged_lines),
            "_merged_count": len(bucket),
        })
    return out


def _merge_capital_inflow(items: list[dict]) -> str:
    """资金回流·板块预警 合并:
      1) 先按龙头股去重 — 同一龙头的多个板块联名展示 (避免 煤炭/煤炭开采 重复打扰)
      2) 不同龙头之间走列表式拼接
    """
    if not items:
        return ""

    groups = _group_by_leader(items)

    def _block_for(a: dict, list_mode: bool) -> str:
        if "sector_pct_range" in a:
            sector_pct_str = _fmt_pct_range(*a["sector_pct_range"])
            sector_top_avg_str = _fmt_pct_range(*a["sector_top_avg_range"])
        else:
            sector_pct_str = f"{a['sector_pct']:+.2f}%"
            sector_top_avg_str = f"{a['sector_top_avg']:+.2f}%"
        if list_mode:
            block = (
                f"▸ {a['sector_name']}  板块{sector_pct_str}\n"
                f"  龙头 {a['leader_name']} {a['leader_pct']:+.2f}%(涨停)  前{a['sector_top_n']}股均涨 {sector_top_avg_str}"
            )
            if a.get("stock_lines"):
                block += f"\n  你自选({a['my_stocks_count']}): " + ", ".join(
                    ln.lstrip(" •").split("  ")[0] for ln in a["stock_lines"]
                )
            else:
                block += "\n  (股池暂无)"
            return block
        # 单组详细格式
        text = (
            f"【资金回流·板块预警】 {a['sector_name']}\n\n"
            f"板块涨幅 {sector_pct_str}\n"
            f"龙头 {a['leader_name']} {a['leader_pct']:+.2f}%(涨停)\n"
            f"板块内前{a['sector_top_n']}股平均涨幅 {sector_top_avg_str}"
        )
        if a.get("sector_top_rows"):
            text += f"\n\n板块前{a['sector_top_n']}股:\n" + "\n".join(
                f"  • {r.get('name', '')}({r.get('code', '')})  "
                f"{float(r.get('pct', 0)):+.2f}%  成交 {_fmt_amount(float(r.get('amt', 0) or 0))}  "
                f"额排名{_fmt_rank(r.get('rank'))}"
                for r in a["sector_top_rows"]
            )
        if a.get("stock_lines"):
            text += f"\n\n你自选的该板块个股 ({a['my_stocks_count']}只):\n" + "\n".join(a["stock_lines"])
        else:
            text += "\n\n(你的股票池中暂无该板块个股)"
        return text

    if len(groups) == 1:
        return _block_for(groups[0], list_mode=False)

    header = f"【资金回流·板块预警】 近15分钟 {len(groups)} 波资金共振"
    return header + "\n\n" + "\n\n".join(_block_for(g, list_mode=True) for g in groups)


def _build_capital_inflow_lark(items: list[dict]):
    """资金回流·板块预警 → 飞书原生表格卡: 每组(按龙头去重) 板块/龙头/前N均涨 文字段 +
    你自选个股表(名称 / 涨幅 / 成交额, 涨幅红涨绿跌)。返回 (title, elements); 无票则纯文字回退。"""
    if not items:
        return None
    from backend.services import lark_notifier
    groups = _group_by_leader(items)
    elements: list = []
    if len(groups) > 1:
        elements.append(lark_notifier.md_element(f"**近15分钟 {len(groups)} 波资金共振**"))
    for a in groups:
        if "sector_pct_range" in a:
            sector_pct_str = _fmt_pct_range(*a["sector_pct_range"])
            top_avg_str = _fmt_pct_range(*a["sector_top_avg_range"])
        else:
            sector_pct_str = f"{a['sector_pct']:+.2f}%"
            top_avg_str = f"{a['sector_top_avg']:+.2f}%"
        head = (f"**{a['sector_name']}**　板块 {sector_pct_str}\n"
                f"龙头 **{a['leader_name']}** {a['leader_pct']:+.2f}%(涨停)　"
                f"前{a['sector_top_n']}股均涨 {top_avg_str}")
        elements.append(lark_notifier.md_element(head))
        # 板块前N股 → 逐条换行文本行(涨幅前置, 名称+代码+成交额换行不截), 命中自选⭐标记
        my_codes = {r.get("code") for r in (a.get("stock_rows") or []) if r.get("code")}

        # 移动优化(v1.7.581): 逐条换行文本行, 涨幅红绿前置, 名称+代码+成交额/名次全名换行不截, 自选⭐标记
        #   (原 名称格塞 名称+代码+成交额+名次, 手机端字符级截断→成交额/名次被吃掉)
        def _stock_line(r):
            pct = float(r.get("pct", 0) or 0)
            color = "red" if pct >= 0 else "green"
            amt = _fmt_amount(float(r.get("amt", 0) or 0))
            rk = _fmt_rank(r.get("rank"))
            sub = f"成交{amt}·额第{rk}" if rk not in ("—", "100+") else f"成交{amt}"
            mark = "⭐" if r.get("code") in my_codes else ""
            return (f"<font color='{color}'>{pct:+.2f}%</font>　{mark}**{r.get('name', '')}** "
                    f"{r.get('code', '')} · {sub}")

        top_rows = a.get("sector_top_rows") or []
        if top_rows:
            elements.append(lark_notifier.md_element(f"📈 板块前{a['sector_top_n']}股"))
            elements.append(lark_notifier.md_element("\n".join(_stock_line(r) for r in top_rows)))
        # 自选个股
        rows = a.get("stock_rows") or []
        if rows:
            elements.append(lark_notifier.md_element(f"⭐ 你自选的该板块个股（{len(rows)}只）"))
            elements.append(lark_notifier.md_element("\n".join(_stock_line(r) for r in rows)))
        else:
            elements.append(lark_notifier.md_element("_(你的股票池中暂无该板块个股)_"))
    # 👉 行动建议区(基线v1.1) + 信封字段(锁屏摘要/彩签)
    from backend.services import card_kit
    my_hits = sum(len(a.get("stock_rows") or []) for a in groups)
    elements.append(card_kit.advice(
        f"资金回流盯龙头，自选命中 {my_hits} 只先看" if my_hits else "资金回流盯龙头，自选暂无命中"))
    lead = groups[0]
    extra = {
        "summary": card_kit.summary_text(
            "资金回流", lead.get("sector_name", ""), f"龙头{lead.get('leader_name', '')}",
            f"{len(groups)}波" if len(groups) > 1 else ""),
        "text_tags": [("资金回流", "blue")],
    }
    return "📊 资金回流·板块预警", elements, extra


alert_throttle.register(
    "SECTOR_CAPITAL_INFLOW",
    _merge_capital_inflow,
    lark_card_builder=_build_capital_inflow_lark,
)


async def scan_capital_inflow():
    # v1.7.722 闸门修正: 原用 is_trading_time() —— 生产 config 的 trading_hours 起点是 09:15,
    # 且该函数注释即写明"含集合竞价撮合", 于是本扫描器从 09:15 就每 30s 开跑。
    # 0720 事故: 09:23:26 推出"半导体 自选票22只"卡, 22 只全是 +0.00% / 成交-。
    #   竞价时段板块榜已有撮合涨幅(半导体+1.48%)足以过"龙头涨停+前N均涨"的筛子 → 信号成立;
    #   但个股还没有连续成交, 逐票取价全 0 → 渲染出一张以成交额为主体的空卡。
    # 资金回流本质是【连续交易中的资金流向】, 竞价数据表达不了它 —— is_continuous_auction
    # (09:30 起, 不含集合竞价)的 docstring 正是为这类判断写的: "9:25 的集合竞价数据噪音过大不应触发"。
    if not _is_continuous_auction():
        return

    today = str(date_cls.today())
    pushed = _pushed_today.setdefault(today, set())
    # 清理过期日期
    for k in list(_pushed_today.keys()):
        if k != today:
            _pushed_today.pop(k, None)

    # 读用户1配置(单用户场景,跨用户共享一份配置)
    user_cfg = await repository.get_signal_config(1)
    cfg = (user_cfg or {}).get("SECTOR_CAPITAL_INFLOW", {})
    if cfg.get("enabled") is False:
        return

    # v1.7.x: 三件套收紧 — A 涨幅 1.0→1.5 / B 榜单前 20 / D 仅 ≤ leader_cutoff_time 推送
    min_sector = float(cfg.get("min_sector_pct", 1.5))
    # leader_limit_up_pct 配置已弃用 (v1.7.23: 按市场分类判真涨停, 不再用固定阈值)
    sector_top_n = int(cfg.get("sector_top_n_stocks", 10))
    min_sector_top_avg = float(cfg.get("min_sector_top_avg_pct", 4.0))
    sector_rank_limit = int(cfg.get("sector_rank_limit", 20))
    leader_cutoff = str(cfg.get("leader_cutoff_time", "10:30"))

    # D 门槛: 盘中后段(默认 10:30 后)不再扫描 — 此时启动的板块多为游资拉尾盘, 非真"资金回流"
    now_hm = datetime.now().strftime("%H:%M")
    if now_hm > leader_cutoff:
        return

    # 拉全市场板块涨幅榜 (用于确定候选板块, 已按涨幅倒序)
    try:
        rankings = await data_fetcher.get_sector_ranking(top_n=100)
    except Exception as e:
        logger.warning(f"[capital_inflow] 拉板块榜失败: {e}")
        return
    if not rankings:
        return

    # 建立"板块→用户自选个股"映射
    # v1.7.x: 口径放宽到整个自选池(含观察票), 不再只限 关注/持仓 —— 只要热点板块里有任意自选票就纳入
    all_stocks = await repository.list_all_stocks()
    sector_to_stocks: dict[str, list[dict]] = defaultdict(list)
    seen_codes: set[str] = set()  # 跨用户按 code 去重: 同一只票被多个自选池收录, 板块列表只列一次
    for s in all_stocks:
        if _is_st(s):
            continue
        code = s.get("code") or ""
        if code in seen_codes:
            continue
        seen_codes.add(code)
        ind = s.get("industry") or ""
        if ind:
            sector_to_stocks[ind].append(s)

    # 全市场成交额排名 top100(新浪源, prod可达) — 给推送表格的"额排名"列, 循环前取一次
    try:
        from backend.routers.market_report import _fetch_amount_rank_top100
        amount_rank = await _fetch_amount_rank_top100()
    except Exception as e:
        # v1.7.722: 原为 {e}, 遇上无 message 的异常打出来就是"取数失败:"空信息(0720 日志实例),
        # 排查时完全看不出所以然。改 {e!r} 带上异常类型。
        logger.warning(f"[capital_inflow] 成交额排名取数失败: {e!r}")
        amount_rank = {}

    pushed_count = 0
    for sec_idx, sec in enumerate(rankings):
        # B 门槛: 只看全市场板块榜前 sector_rank_limit 名
        if sec_idx >= sector_rank_limit:
            break
        sector_name = sec.get("industry") or ""
        bk_code = sec.get("bk_code") or sector_name
        sector_pct = float(sec.get("pct_today", 0))
        if sector_pct < min_sector:
            break  # rankings 按涨幅倒序, 后面都不满足
        if sector_name in pushed:
            continue
        # DB 级去重(防进程重启后内存清空导致重复推送)
        try:
            if await repository.signal_already_sent_today(bk_code, "SECTOR_CAPITAL_INFLOW", 1):
                pushed.add(sector_name)  # 同步内存
                continue
        except Exception as e:
            # 保守策略: DB 查询失败时本轮跳过, 避免 DB 抽风时反复推送
            logger.warning(f"[capital_inflow] DB 去重查询失败({sector_name})，本轮跳过保守处理: {e}")
            continue

        try:
            overview = await data_fetcher.get_sector_overview(sector_name, top_n=sector_top_n)
        except Exception:
            continue
        if not overview:
            continue
        # v1.7.23: 真涨停判定按市场分类 (主板9.85/科创创业19.85/北交29.85)
        leader_pct = float(overview.get("leader_pct", 0))
        leader_name = overview.get("leader_name", "")
        top_stocks = overview.get("top_stocks", []) or []
        leader_code = top_stocks[0].get("code", "") if top_stocks else ""
        # v1.7.704: 新股(N/C 开头)当龙头不算数 —— 它在标识期内无涨跌幅限制, "龙头涨停"
        # 这个条件对它天然恒真(首日可涨100%+), 于是任何板块只要当天有新股上市就可能被
        # 误判成资金回流; 而新股暴涨反映的是打新情绪, 不是板块资金回流。
        # 实测近一月 25 条信号里有 3 条(12%)龙头是 N 开头新股。
        if _is_new_listing(leader_name):
            logger.info(f"[capital_inflow] {sector_name} 龙头 {leader_name} 为次新股, 跳过")
            continue
        if not _is_limit_up(leader_code, leader_pct):
            continue

        # 板块内强势密度: 前 N 个股平均涨幅
        if len(top_stocks) < sector_top_n:
            continue
        sector_top_avg = sum(float(s.get("pct_change", 0)) for s in top_stocks[:sector_top_n]) / sector_top_n
        if sector_top_avg < min_sector_top_avg:
            continue

        # 找该板块下用户关注的票
        my_stocks = sector_to_stocks.get(sector_name, [])
        if not my_stocks:
            # v1.7.x: 股池暂无该板块个股 → 不纳入预警, 不入队/不写库/不去重标记
            # (留待用户后续加股后重新评估; 全市场板块榜中"股池暂无"的多数为噪音)
            continue
        # 取价: 自选票 + 板块前N股 合并一次取(去重), 用于补成交额
        top_n_stocks = top_stocks[:sector_top_n]
        quote_codes = [c for c in {*(s["code"] for s in my_stocks),
                                   *(t.get("code", "") for t in top_n_stocks)} if c]
        try:
            quotes = await data_fetcher.get_realtime_quotes(quote_codes) if quote_codes else {}
        except Exception as e:
            # v1.7.722: 原来这里静默吞异常(except Exception: quotes = {}), 取价整体失败也照发卡,
            # 推出来就是一张全 0 空卡。降级必须留痕 —— 同 0713"行情冻结致止损检查静默跳过"的教训。
            logger.warning(f"[capital_inflow] {sector_name} 取价失败, 跳过本板块: {e!r}")
            continue

        # v1.7.722 护栏: 整批取价一只都没有成交额 → 说明这批行情不可用(非交易时段/源降级),
        # 而这张卡主体正是"涨幅+成交额", 硬发就是 22 只全 +0.00%/成交-。宁可不发也不发空卡。
        if not any(float((v or {}).get("amount") or 0) > 0 for v in quotes.values()):
            logger.warning(
                f"[capital_inflow] {sector_name} 整批行情无成交额({len(quote_codes)}只), "
                f"判定行情不可用, 跳过本板块不推空卡")
            continue

        # 板块前N股结构化行(名称/涨幅/成交额); 实时取不到则回退榜单自带涨幅
        sector_top_rows = []
        for t in top_n_stocks:
            tc = t.get("code", "")
            rt = quotes.get(tc, {}) or {}
            sector_top_rows.append({
                "name": t.get("name", ""), "code": tc,
                # v1.7.722: 原为 rt.get("pct_change", t.get("pct_change", 0)) —— dict.get 只在
                # 【键不存在】时取默认值。行情源返回了这只票但 pct_change 是 0/None 时键是存在的,
                # 回退到榜单涨幅的意图【永远不会生效】, 直接渲染成 +0.00%。改成显式取首个有效值。
                "pct": _first_num(rt.get("pct_change"), t.get("pct_change")),
                "amt": float(rt.get("amount") or 0),
                "rank": amount_rank.get(tc),
            })

        # 自选个股结构化行 + 文本行
        # v1.7.722: 自选票原来连回退都没写(rt.get("pct_change", 0)), 取不到就是 0。
        # 板块榜里若有这只票, 用榜单涨幅兜底。
        board_pct = {t.get("code", ""): t.get("pct_change") for t in top_stocks if t.get("code")}
        stock_lines = []
        stock_rows = []   # {name, code, pct, amt}
        for s in my_stocks:
            rt = quotes.get(s["code"], {}) or {}
            price = rt.get("price") or 0
            pct = _first_num(rt.get("pct_change"), board_pct.get(s["code"]))
            amt = float(rt.get("amount") or 0)
            pct_str = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"
            stock_lines.append(f"  • {s['name']}({s['code']})  价格 {price:.2f}  涨幅 {pct_str}  "
                               f"成交 {_fmt_amount(amt)}  额排名{_fmt_rank(amount_rank.get(s['code']))}")
            stock_rows.append({"name": s["name"], "code": s["code"], "pct": pct, "amt": amt,
                               "rank": amount_rank.get(s["code"])})

        # 先持久化到 DB (入队前写入, 即使后续合并发送失败也不会下次重推)
        try:
            from backend.services import signal_specs
            await repository.save_signal(
                code=bk_code, name=sector_name,
                signal_id="SECTOR_CAPITAL_INFLOW",
                signal_name="资金回流·板块预警",
                direction="buy",
                price=sector_pct,
                detail=(
                    f"板块涨{sector_pct:+.2f}% | 龙头{leader_name}{leader_pct:+.2f}% | "
                    f"前{sector_top_n}股均涨{sector_top_avg:+.2f}% | 自选票{len(my_stocks)}只"
                ),
                user_id=1,
                signal_group=signal_specs.group_of("SECTOR_CAPITAL_INFLOW"),
            )
        except Exception as e:
            # 写库失败则跳过推送(否则会反复推送)
            logger.warning(f"[capital_inflow] save_signal 失败,本轮跳过推送({sector_name}): {e}")
            continue
        pushed.add(sector_name)  # 内存同步,本进程内不重推

        # v1.7.x: 走 alert_throttle, 15 分钟窗口内多板块合并成一条
        # v1.7.x: 带上 leader_code, merger 按龙头股去重合并 (如 煤炭/煤炭开采 共享同一龙头)
        await alert_throttle.enqueue("SECTOR_CAPITAL_INFLOW", {
            "sector_name": sector_name,
            "sector_pct": sector_pct,
            "leader_code": leader_code,
            "leader_name": leader_name,
            "leader_pct": leader_pct,
            "sector_top_n": sector_top_n,
            "sector_top_avg": sector_top_avg,
            "stock_lines": stock_lines,
            "stock_rows": stock_rows,
            "sector_top_rows": sector_top_rows,
            "my_stocks_count": len(my_stocks),
        })
        pushed_count += 1
        logger.info(f"[capital_inflow] 入队板块: {sector_name} ({sector_pct:+.2f}%) 自选票{len(my_stocks)}只")

    if pushed_count > 0:
        logger.info(f"[capital_inflow] 本轮共入队 {pushed_count} 个板块, 等待节流窗口合并发送")
