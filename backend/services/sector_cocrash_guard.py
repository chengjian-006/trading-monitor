# -*- coding: utf-8 -*-
"""板块共振·禁补仓提示 (v1.7.598) — 交易日尾盘14:30判"板块共振跌", 自选池命中才推。

回测背书(bt_avoid_rules2, 全市场2023~2026-07, IS/OOS一致):
  破位大跌日抄底盈亏完全取决于语境 —
  大盘恐慌日(全市场≤-5%家数占比≥10%): 抄底历史大幅为正(V型底), 本卡不发;
  板块共振跌(大盘正常 + 某行业大跌占比超出大盘≥20pp): 最差抄底语境,
    T+5~T+20 期望为负、日胜率33%~47% → 自选池有票踩线时推一张禁抄底/禁补仓提示卡。

数据源: 新浪 Market_Center 全A列表页(带当日涨跌幅, ~60个请求, 独立httpx client不抢实时行情);
行业归属: cfzy_sys_industry_map(问财同花顺三级行业, 每周日刷新, 见 industry_map_refresher)。
警戒提示, 不落信号库(不污染胜率)。恐慌普跌日/无行业触发/自选池无命中 → 静默不发。
"""
from __future__ import annotations

import json
import logging
from datetime import time as _time

logger = logging.getLogger(__name__)

PANIC_TH = 0.10      # 全市场大跌占比 ≥10% = 恐慌普跌日, 本卡不适用(回测该语境抄底为正)
EXCESS_TH = 0.20     # 行业大跌占比 - 全市场 ≥20pp = 板块共振(回测最差抄底语境)
MIN_MEMBERS = 8      # 行业成员数下限(太小两三只跌停就100%, 无统计意义)
DOWN_PCT = -5.0      # "大跌"口径: 当日跌幅 ≤ -5%(与回测事件口径一致)

# 盘中实时: 每票每天只报一次(内存去重), 板块下杀期间新破位的票增量推, 不刷屏
_SCANNED_DATE = ""
_FIRED_TODAY: set[str] = set()


def _in_window(now_t: _time) -> bool:
    """盘中 09:45~15:00 运行。太早(开盘竞价)行业占比噪音大, 且个股破位判定需成型走势。"""
    return _time(9, 45) <= now_t <= _time(15, 0)


def _reset_if_new_day(today: str):
    global _SCANNED_DATE, _FIRED_TODAY
    if today != _SCANNED_DATE:
        _SCANNED_DATE = today
        _FIRED_TODAY = set()


# ══════════════ 纯函数(可单测) ══════════════

def detect_cocrash_sectors(quotes: list[dict], ind_map: dict[str, str], *,
                           panic_th: float = PANIC_TH, excess_th: float = EXCESS_TH,
                           min_members: int = MIN_MEMBERS,
                           down_pct: float = DOWN_PCT) -> dict:
    """全市场行情 + 行业映射 → {"panic": 全市场大跌占比, "sectors": {行业: {down,total,ratio}}}。

    quotes 每项 {code, pct(当日涨跌%)}; 恐慌日(panic≥panic_th)返回空 sectors(禁抄闸不适用)。
    """
    total = down = 0
    sec_total: dict[str, int] = {}
    sec_down: dict[str, int] = {}
    for q in quotes:
        pct = q.get("pct")
        if pct is None:
            continue
        total += 1
        is_down = pct <= down_pct
        if is_down:
            down += 1
        ind = ind_map.get(q.get("code") or "")
        if ind:
            sec_total[ind] = sec_total.get(ind, 0) + 1
            if is_down:
                sec_down[ind] = sec_down.get(ind, 0) + 1
    panic = down / total if total else 0.0
    sectors: dict[str, dict] = {}
    if panic < panic_th:
        for ind, tot in sec_total.items():
            if tot < min_members:
                continue
            d = sec_down.get(ind, 0)
            ratio = d / tot
            if ratio - panic >= excess_th:
                sectors[ind] = {"down": d, "total": tot, "ratio": round(ratio, 4)}
    return {"panic": round(panic, 4), "sectors": sectors}


DD_TH = -0.15        # 距峰回撤 ≤ -15% = 已确实跌入下跌段(非见顶第一天)
MA_WIN = 20          # 判"收在MA20下 + MA20拐头"
PEAK_WIN = 120       # 距峰回看窗(约半年, 与回测 bt_avoid_rules2 同口径)


def is_broken_down(closes: list[float], price: float, *,
                   dd_th: float = DD_TH, ma_win: int = MA_WIN,
                   peak_win: int = PEAK_WIN) -> bool:
    """该股是否"破位下跌"—— 三条全中: 距 peak_win 日峰≤dd_th + 收盘(现价)<MA20 + MA20拐头向下。

    与回测 bt_avoid_rules2 的"破位大跌日"个股前提一致: 板块共振跌禁抄底的结论建立在此语境上,
    只有命中票本身处于该语境才推(均线上方的强势票即便跟随行业跌一下也不提示)。
    closes: 不含今日的历史收盘价(升序, 最新在末位); price: 今日尾盘现价。数据不足→False(不判)。
    """
    m = len(closes)
    if m < peak_win or price <= 0:
        return False
    # 距峰: 近 peak_win 根(近 peak_win-1 历史 + 今日)最高价
    peak = max(max(closes[-(peak_win - 1):]), price)
    if price / peak - 1.0 > dd_th:
        return False
    # 收在 MA20 下: 今日 MA20 = (近19历史 + 今日现价)/20
    ma_today = (sum(closes[-(ma_win - 1):]) + price) / ma_win
    if price >= ma_today:
        return False
    # MA20 拐头: 今日 MA20 < 5 交易日前 MA20(用历史收盘算, closes[-24:-4])
    ma_5ago = sum(closes[-(ma_win + 4):-4]) / ma_win
    return ma_today < ma_5ago


def pick_pool_hits(pool_rows: list[dict], sectors: dict, ind_map: dict[str, str],
                   quote_map: dict[str, float], holding_codes: set[str],
                   broken_codes: set[str] | None = None) -> list[dict]:
    """自选池里踩中触发行业的票, 按当日跌幅升序(跌最深在前)。

    pool_rows 每项含 code/name; quote_map = {code: 当日涨跌%}(无行情的票跳过);
    holding_codes 标 💼持仓。broken_codes 非 None 时只保留其中的票(个股破位下跌闸,
    见 is_broken_down); None(默认)不做破位过滤。返回 [{code,name,pct,industry,held}]。
    """
    hits: list[dict] = []
    seen: set[str] = set()
    for row in pool_rows:
        code = str(row.get("code") or "")
        if not code or code in seen:
            continue
        ind = ind_map.get(code)
        if not ind or ind not in sectors:
            continue
        pct = quote_map.get(code)
        if pct is None:
            continue
        if broken_codes is not None and code not in broken_codes:
            continue
        seen.add(code)
        hits.append({"code": code, "name": str(row.get("name") or code),
                     "pct": float(pct), "industry": ind, "held": code in holding_codes})
    hits.sort(key=lambda h: h["pct"])
    return hits


def build_cocrash_card(panic: float, sectors: dict, hits: list[dict]) -> tuple[str, str]:
    """合并提示卡 (title, body_md)。行业按大跌占比降序, 每行业一段+踩线自选票一行(💼=持仓)。"""
    ordered = sorted(sectors.items(), key=lambda kv: kv[1]["ratio"], reverse=True)
    by_ind: dict[str, list[dict]] = {}
    for h in hits:
        by_ind.setdefault(h["industry"], []).append(h)
    lines: list[str] = []
    hit_inds = 0
    for ind, s in ordered:
        stocks = by_ind.get(ind)
        if not stocks:
            continue
        hit_inds += 1
        lines.append(f"**{ind}**　行业大跌占比 **{s['ratio']*100:.0f}%**（{s['down']}/{s['total']}）"
                     f" ｜ 全市场 {panic*100:.1f}%")
        parts = [f"{'💼' if st['held'] else ''}{st['name']}({st['code']}) **{st['pct']:+.1f}%**"
                 for st in stocks]
        lines.append("　" + "　┃　".join(parts))
        lines.append("")
    title = f"⛔ 板块共振·禁补仓 · {hit_inds}个行业"
    body = ("\n".join(lines).rstrip()
            + "\n\n大盘正常但行业集体大跌=行业逻辑受损。全市场回测(2023~2026): 此语境抄底/补仓"
              " T+5~T+20 期望为负、日胜率仅33%~47% — 行业停止超额大跌前别抄底、别补仓(💼=当前持仓)。"
              "仅纳入本身已破位下跌(距峰≤-15%+收MA20下+MA20拐头)的票, 均线上方的强势票即便跟跌也不提示。"
              "恐慌普跌日(全市场大跌占比≥10%)不适用本提示。")
    return title, body


# ══════════════ 取数 + 编排(定时 14:30) ══════════════

_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


async def _fetch_market_pct() -> list[dict]:
    """新浪 hs_a 列表页拉全A当日涨跌幅(剔北交所/ST/退市)。返回 [{code, pct}]。

    只翻列表页(~60个请求), 不逐票拉K线; 独立 httpx client(trust_env=False)隔离,
    不与 3s 实时行情扫描抢连接(同 market_breadth_refresher 的隔离思路)。
    """
    import httpx
    out: list[dict] = []
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0),
                               limits=httpx.Limits(max_connections=10),
                               trust_env=False)
    try:
        page = 1
        while page <= 90:
            params = {"page": page, "num": 80, "sort": "symbol", "asc": 1,
                      "node": "hs_a", "symbol": "", "_s_r_a": "page"}
            try:
                r = await client.get(_LIST_URL, params=params, headers=_HEADERS)
                txt = (r.text or "").strip()
                if not txt or txt == "null":
                    break
                rows = json.loads(txt)
            except Exception as e:
                logger.warning(f"[sector_cocrash] 列表第{page}页失败: {e}")
                break
            if not rows:
                break
            for it in rows:
                sym = it.get("symbol", "")
                name = it.get("name", "")
                if sym.startswith("bj") or not (sym.startswith("sh") or sym.startswith("sz")):
                    continue
                if "ST" in name or "退" in name or name.startswith("*"):
                    continue
                try:
                    pct = float(it.get("changepercent"))
                except (TypeError, ValueError):
                    continue
                code = str(it.get("code") or "")
                if code:
                    out.append({"code": code, "pct": pct})
            page += 1
    finally:
        await client.aclose()
    return out


async def _broken_candidates(candidates: list[dict], quote_map: dict[str, float]) -> set[str]:
    """对行业命中候选逐票拉日线, 返回其中"个股本身破位下跌"的 code 集合(见 is_broken_down)。

    今日一律用现价(quote_map 的当日涨跌%由现价换算, 剔日线里可能带的当日半根bar)。
    拉日线失败/数据不足的票不算破位(不推), 宁可漏报不误报。
    """
    from datetime import date as _date
    from backend import data_fetcher

    today = _date.today().isoformat()
    broken: set[str] = set()
    for c in candidates:
        code = c["code"]
        try:
            df = await data_fetcher.get_daily_kline(code, days=PEAK_WIN + 30)
        except Exception as e:
            logger.warning(f"[sector_cocrash] 取日K失败({code}): {e}")
            continue
        if df is None or df.empty:
            continue
        d = df[df["date"].astype(str).str.slice(0, 10) < today]
        closes = [float(x) for x in d["close"].tolist() if x and float(x) > 0]
        if len(closes) < PEAK_WIN:
            continue
        # 今日现价: 用最近历史收盘 × (1+当日涨跌%) 复原(尾盘14:30 ≈ 收盘)
        price = closes[-1] * (1.0 + quote_map.get(code, 0.0) / 100.0)
        if is_broken_down(closes, price):
            broken.add(code)
    return broken


async def run_sector_cocrash_watch():
    """盘中实时(每3分钟): 全市场涨跌幅 → 板块共振判定 → 自选池破位命中则推禁补仓提示卡。

    每票每天只报一次(内存去重); 板块下杀期间新破位的票增量推; 恐慌普跌日/无破位命中不发。
    """
    from datetime import datetime
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    now = datetime.now()
    if not _in_window(now.time()):
        return
    _reset_if_new_day(now.date().isoformat())

    from backend.models import repository
    from backend.services import notifier

    ind_map = await repository.load_industry_map()
    if len(ind_map) < 1000:
        logger.warning(f"[sector_cocrash] 行业映射过少({len(ind_map)}), 跳过 — "
                       f"待 industry_map_refresh 首刷或手动补种")
        return
    quotes = await _fetch_market_pct()
    if len(quotes) < 3000:
        logger.warning(f"[sector_cocrash] 全市场行情样本过少({len(quotes)}), 跳过")
        return
    res = detect_cocrash_sectors(quotes, ind_map)
    if not res["sectors"]:
        logger.info(f"[sector_cocrash] 无板块共振(全市场大跌占比{res['panic']*100:.1f}%), 不发")
        return

    user_id = 1
    try:
        pool_rows = await repository.list_stocks(user_id)
    except Exception as e:
        logger.warning(f"[sector_cocrash] 取自选池失败: {e}")
        return
    try:
        qty_map = await repository.get_holdings_qty(user_id)
        holding_codes = {c for c, q in qty_map.items() if q > 0}
    except Exception:
        holding_codes = set()
    quote_map = {q["code"]: q["pct"] for q in quotes}
    # 先取行业命中候选(要拉日线判破位的票), 再逐票判"个股本身破位下跌", 只推破位的
    candidates = pick_pool_hits(pool_rows, res["sectors"], ind_map, quote_map, holding_codes)
    if not candidates:
        logger.info(f"[sector_cocrash] 触发{len(res['sectors'])}个行业但自选池无命中, 不发")
        return
    # 只判今天还没报过的候选(每票每天一次), 省掉已报票的日线拉取
    fresh = [c for c in candidates if c["code"] not in _FIRED_TODAY]
    if not fresh:
        return
    broken_codes = await _broken_candidates(fresh, quote_map)
    hits = pick_pool_hits(pool_rows, res["sectors"], ind_map, quote_map, holding_codes,
                          broken_codes=broken_codes)
    if not hits:
        logger.info(f"[sector_cocrash] 触发{len(res['sectors'])}个行业, 自选新命中{len(fresh)}只 "
                    f"但无一处于破位下跌, 不发(强势票跟跌不提示)")
        return
    title, body = build_cocrash_card(res["panic"], res["sectors"], hits)
    try:
        await notifier.send_dual(body, lark_title=title, template="orange")
        _FIRED_TODAY.update(h["code"] for h in hits)   # 推成功才标记, 每票当天不再重报
        logger.info(f"[sector_cocrash] 已推送: {title}, 新命中{len(hits)}只")
    except Exception as e:
        logger.warning(f"[sector_cocrash] 推送失败: {e}")
