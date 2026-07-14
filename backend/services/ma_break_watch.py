# -*- coding: utf-8 -*-
"""尾盘破位警戒 (v1.7.582) — 持仓股尾盘(14:40)贴线判 MA5/MA10/MA20 破位, 带连续N日标注。

与「跌破MAx卖出」事件卡(SELL_BREAK_MA*)的分工:
  事件卡: 首日破位需 ≥2% 深度 + 新鲜击穿 + 上涨日不报 + 300s 确认 → 交易信号, 只报首日;
  本卡:   每个交易日尾盘对持仓逐票【贴线判】(现价 < MA 即算), 连续 N 日从日线收盘序列回算
          (反弹日只要仍收在线下也计入连续), 每日尾盘都报直到收复 — 防"喊一次就被划过去"。
警戒跟踪, 不落信号库(不污染胜率)。静音: 卡片带"当日/本周不提醒"(ma_watch_snooze, target=code),
只静音本卡, 不影响该票其它买卖点/异动推送。

连续天数口径: 今日均线 = (最近 n-1 个历史收盘 + 尾盘现价)/n; 历史日均线用当日收盘序列;
"连续"= 从今日往回逐日 close < MA 不断链的天数, 今日未破则该档为 0(收复即消失)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MA_PERIODS = (5, 10, 20)

# 自选观察票只判 MA20 —— 观察票破 MA5/MA10 太家常便饭, 报了全是噪声; MA20 = 中期趋势线,
# 破了才值得"这票还留不留在自选里"重估一次。持仓段仍判 MA5/10/20 全档(见 MA_PERIODS)。
WATCH_MA = 20

# 自选段最多列几只: 普通下跌日约 5~10 只, 大盘暴跌日可能 30~50 只 —— 不设限卡片会长到手机没法看,
# 且飞书 markdown 元素 4000 字符硬截断会【静默】吃掉尾部。超出的按破位深度截断, 并在卡里明说省了几只。
MAX_WATCH_ROWS = 15


# ══════════════ 纯函数(可单测) ══════════════

def break_streaks(closes: list[float], price: float) -> dict[int, int]:
    """各均线档的连续破位天数(含今日)。

    closes: 不含今日的历史收盘价(升序, 最新在末位); price: 今日尾盘现价。
    返回 {5: n, 10: n, 20: n}; 今日未破该均线 / 历史数据不足该周期 → 0。
    """
    out: dict[int, int] = {}
    m = len(closes)
    for n in MA_PERIODS:
        if m < n - 1 or price <= 0:
            out[n] = 0
            continue
        ma_today = (sum(closes[-(n - 1):]) + price) / n if n > 1 else price
        if price >= ma_today:
            out[n] = 0
            continue
        streak = 1
        j = m - 1
        while j >= n - 1:
            ma_j = sum(closes[j - n + 1:j + 1]) / n
            if closes[j] < ma_j:
                streak += 1
                j -= 1
            else:
                break
        out[n] = streak
    return out


def find_cost_line(closes: list[float], highs: list[float], lows: list[float],
                   vols: list[float], vol_mult: float = 2.0, newhigh_win: int = 20):
    """识别【最近一个放量起涨点K线】的最低价=主力成本线(用户0705逻辑: 放量起涨=主力建仓成本区)。

    起涨点定义(与回测 bt_cost_line.py 一致): 某根K线 量≥近10日均量×vol_mult 且 收盘≥近20日最高收盘
    (突破/新高=主升启动) 且 上涨(close>前一日)。从最近往前找第一个满足者, 返回其最低价(成本线下沿)。
    输入均为不含今日的历史序列(升序)。返回 {"low": 成本价, "idx": 起涨点索引} 或 None。
    全市场OOS回测: 跌破成本线后跑输大盘~0.4%(edge弱, 故仅作持仓风险提示、不做硬卖点/不落信号库)。
    """
    n = len(closes)
    if n < newhigh_win + 1 or n < 11:
        return None
    lo_bound = max(10, newhigh_win - 1)
    for i in range(n - 1, lo_bound - 1, -1):
        vavg10 = sum(vols[i - 10:i]) / 10 if i >= 10 else 0
        if vavg10 <= 0:
            continue
        hh20 = max(closes[i - newhigh_win + 1:i + 1])
        if vols[i] >= vavg10 * vol_mult and closes[i] >= hh20 * 0.999 and closes[i] > closes[i - 1]:
            return {"low": float(lows[i]), "idx": i}
    return None


def _streak_label(n: int, days: int) -> str:
    return f"破MA{n}·今日新破" if days == 1 else f"破MA{n}·连续{days}日"


def deepest_broken(streaks: dict[int, int]) -> int | None:
    """同时破多档只报最深(最长周期)那档 —— 破MA5+MA10 只报MA10, 破到MA20 只报MA20。
    与「跌破MAx卖出」事件卡 v1.7.178「只推最深破位」同口径(最深=最长周期均线, 丢失中期
    趋势最严重)。返回最长周期的已破均线, 全未破返回 None。"""
    broken = [n for n in MA_PERIODS if streaks.get(n, 0) > 0]
    return max(broken) if broken else None


def build_watch_card(items: list[dict], watch_items: list[dict] | None = None) -> tuple[str, str]:
    """合并警戒卡 (title, body_md)。

    items(持仓段) 每项:
      {name, code, price, pct(今日涨跌%), streaks:{5:n,10:n,20:n}, cost_break:{price,date}|None, actions_md}
      每票均线只报最深破位那档(见 deepest_broken); 另叠加「主力成本线」维度(跌破放量起涨点最低价 → 醒目标注)。
      入卡条件 = 破均线 或 跌破成本线(只破成本线也入卡)。每日尾盘复报直到收复。

    watch_items(自选段, v1.7.606) 每项:
      {name, code, price, pct, dist_pct(距MA20 %), model(当初买点中文名), model_at(触发日)}
      只收【今日新跌破 MA20】(昨天还收在线上、今天掉下来), 只在转弱当天报这一次 —— 观察票不持仓、
      不需要每天喊, 需要的是"这票中期趋势变了, 复盘时重估还留不留在自选里"。

    换行文本行版式(手机端不截)。
    """
    lines: list[str] = []
    for it in items:
        deep = deepest_broken(it["streaks"])
        cost = it.get("cost_break")
        if deep is None and not cost:
            continue
        marks = []
        if deep is not None:
            marks.append(_streak_label(deep, it["streaks"][deep]))
        if cost:
            marks.append(f"🔴跌破主力成本区¥{cost['price']:.2f}({str(cost['date'])[5:10]}放量起涨)")
        lines.append(f"**{it['name']}({it['code']})**　现价 {it['price']:.2f}（{it['pct']:+.1f}%）")
        lines.append("　" + "　┃　".join(marks))
        if it.get("actions_md"):
            lines.append("　" + it["actions_md"])
        lines.append("")

    watch_items = watch_items or []
    shown = watch_items[:MAX_WATCH_ROWS]        # 已按破位深度排序, 截断留最深的
    omitted = len(watch_items) - len(shown)
    wlines: list[str] = []
    for it in shown:
        wlines.append(f"**{it['name']}({it['code']})**　现价 {it['price']:.2f}（{it['pct']:+.1f}%）"
                      f"　距MA20 {it['dist_pct']:+.1f}%")
        origin = (f"当初买点: {it['model']}（{it['model_at']}）" if it.get("model")
                  else "手工加入, 无买点信号记录")
        wlines.append(f"　<font color='grey'>{origin}</font>")
        wlines.append("")
    if omitted > 0:      # 绝不静默截断: 少列了几只必须说出来
        wlines.append(f"<font color='grey'>…另有 {omitted} 只新破MA20未列出(按破位深度截前{MAX_WATCH_ROWS})，"
                      f"完整名单见股票池「未站上20线」筛选。</font>")

    segs = []
    if lines:
        segs.append(f"{len(items)}只持仓")
    if wlines:
        segs.append(f"{len(watch_items)}只自选")
    title = "📉 尾盘破位警戒 · " + " · ".join(segs)

    parts = []
    if lines:
        parts.append("【持仓 · 每日尾盘复报直到收复】\n" + "\n".join(lines).rstrip())
    if wlines:
        parts.append("【自选 · 今日新跌破MA20 · 只报这一次】\n" + "\n".join(wlines).rstrip())
    body = "\n\n".join(parts)

    notes = []
    if lines:
        notes.append("均线同破多档只报最深；🔴主力成本区=放量起涨K线最低价(跌破=资金弃守,仅提示非硬卖点)；收复自动消失。")
    if wlines:
        notes.append("自选段=中期趋势转弱, 仅供复盘重估是否保留, 非卖点。")
    return title, body + "\n\n" + " ".join(notes)


# ══════════════ 自选段(v1.7.606): 今日新跌破 MA20 的观察票 ══════════════

async def _collect_watch_items(user_id: int, prefs: list, hold_codes: set) -> list[dict]:
    """自选池(排除持仓, 持仓已在持仓段)里【今日新跌破 MA20】的票, 按破得最深排前。

    行情直接取股票池行的 price/pct_change —— quote_refresher 每3秒刷一遍, 14:40 必然新鲜,
    省掉一次全池外部请求。K线走一次批量查询(不含今日), 不逐票拉。
    """
    from datetime import date as _date
    from backend.models import repository
    from backend.services import push_pref as pp

    try:
        pool = await repository.list_stocks(user_id)
    except Exception as e:
        logger.warning(f"[ma_break_watch] 取自选池失败: {e}")
        return []

    rows = {}
    for s in pool:
        code = str(s.get("code") or "")
        if not code or code in hold_codes or pp.ma_watch_snooze_active(prefs, code):
            continue
        rows[code] = s
    if not rows:
        return []

    try:
        # 取 MA20+1 根历史(不含今日): 20 根才能既算今日MA20, 又回算昨日MA20 判"是不是新破"
        closes_map = await repository.fetch_kline_close_batch(
            list(rows), WATCH_MA + 1, before=_date.today().isoformat())
    except Exception as e:
        logger.warning(f"[ma_break_watch] 取自选日K失败: {e}")
        return []

    fresh: list[dict] = []
    for code, s in rows.items():
        price = float(s.get("price") or 0)
        if price <= 0:
            continue
        closes = [float(x) for x in closes_map.get(code, []) if x and float(x) > 0]
        if len(closes) < WATCH_MA:      # 不足20根历史 → 昨日MA20 算不出, 无从判断"新破"
            continue
        closes.reverse()                # repo 返回最新在前, break_streaks 要升序(最新在末位)
        if break_streaks(closes, price).get(WATCH_MA) != 1:
            continue                    # 只要"今日新跌破"(==1); 0=没破, ≥2=早就破了不再喊
        ma = (sum(closes[-(WATCH_MA - 1):]) + price) / WATCH_MA
        fresh.append({
            "name": s.get("name") or code, "code": code, "price": price,
            "pct": float(s.get("pct_change") or 0),
            "dist_pct": (price - ma) / ma * 100,
        })
    if not fresh:
        return []

    try:    # 只对真破位的那几只反查"当初买点", 不是全池扫
        models = await repository.get_last_buy_model_batch([f["code"] for f in fresh], user_id)
    except Exception as e:
        logger.warning(f"[ma_break_watch] 反查买点模型失败: {e}")
        models = {}
    for f in fresh:
        m = models.get(f["code"]) or {}
        f["model"] = m.get("model", "")
        f["model_at"] = m.get("at", "")

    fresh.sort(key=lambda f: f["dist_pct"])      # 破得最深的排最前
    return fresh


# ══════════════ 编排(定时 14:40) ══════════════

async def run_ma_break_watch():
    """交易日尾盘14:40: 持仓逐票贴线判 MA5/10/20 破位 + 自选今日新破MA20, 合并推一张警戒卡。"""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    from datetime import date as _date
    from backend.core.config import load_config
    from backend.models import repository
    from backend.models.repo import push_pref as pp_repo
    from backend.services import push_pref as pp
    from backend.services import notifier
    from backend import data_fetcher

    user_id = 1
    try:
        prefs = await pp_repo.active_prefs(user_id)
    except Exception:
        prefs = []
    try:
        qty_map = await repository.get_holdings_qty(user_id)
    except Exception as e:
        logger.warning(f"[ma_break_watch] 取持仓失败: {e}")
        qty_map = {}
    codes = [c for c in qty_map if qty_map.get(c, 0) > 0]

    # 自选段独立于持仓段: 空仓时也要出(观察票转弱照样该知道)
    watch_items = await _collect_watch_items(user_id, prefs, set(codes))

    quotes: dict = {}
    if codes:
        try:
            quotes = await data_fetcher.get_realtime_quotes(codes)
        except Exception as e:
            logger.warning(f"[ma_break_watch] 取现价失败: {e}")
            codes = []

    site = (load_config().get("site_url", "") or "").rstrip("/")
    today = _date.today().isoformat()
    items: list[dict] = []
    for code in codes:
        if pp.ma_watch_snooze_active(prefs, code):
            continue
        if pp.mark_sold_active(prefs, code):
            continue
        q = quotes.get(code)
        if not q or not q.get("price"):
            continue
        price = float(q["price"])
        try:
            df = await data_fetcher.get_daily_kline(code, days=60)
        except Exception as e:
            logger.warning(f"[ma_break_watch] 取日K失败({code}): {e}")
            continue
        if df is None or df.empty:
            continue
        # 剔除今日行(有些源盘中带当日半根bar): 今日一律用尾盘现价
        d = df[df["date"].astype(str).str.slice(0, 10) < today]
        closes = [float(x) for x in d["close"].tolist() if x and float(x) > 0]
        if len(closes) < 4:
            continue
        streaks = break_streaks(closes, price)
        # 主力成本线: 最近放量起涨点最低价, 现价(尾盘)跌破则标记(仅提示, 不落库)
        cost_break = None
        cl = find_cost_line(closes,
                            [float(x) for x in d["high"].tolist()],
                            [float(x) for x in d["low"].tolist()],
                            [float(x) for x in d["volume"].tolist()])
        if cl and price < cl["low"]:
            cost_break = {"price": cl["low"], "date": str(d["date"].iloc[cl["idx"]])[:10]}
        # 入卡 = 破均线 或 跌破成本线
        if not any(v > 0 for v in streaks.values()) and not cost_break:
            continue
        actions_md = pp.build_ma_watch_actions_md(site, user_id, code) if site else ""
        sold_md = pp.build_mark_sold_md(site, user_id, code, q.get("name") or code)
        if sold_md:
            actions_md = (actions_md + "　·　" + sold_md) if actions_md else sold_md
        items.append({
            "name": q.get("name") or code, "code": code,
            "price": price, "pct": float(q.get("pct_change") or 0),
            "streaks": streaks, "cost_break": cost_break,
            "actions_md": actions_md,
        })

    if not items and not watch_items:
        return
    # 排序: 跌破成本线(资金弃守,最重)优先 → 再按最深破位均线周期 → 连续天数
    items.sort(key=lambda it: (1 if it.get("cost_break") else 0,
                               deepest_broken(it["streaks"]) or 0,
                               it["streaks"].get(deepest_broken(it["streaks"]) or 0, 0)),
               reverse=True)
    title, body = build_watch_card(items, watch_items)
    logger.info(f"[ma_break_watch] 持仓破位 {len(items)} 只, 自选今日新破MA20 {len(watch_items)} 只")
    try:
        await notifier.send_dual(body, lark_title=title, template="orange")
    except Exception as e:
        logger.warning(f"[ma_break_watch] 推送失败: {e}")
