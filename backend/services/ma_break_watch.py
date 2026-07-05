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


def build_watch_card(items: list[dict]) -> tuple[str, str]:
    """合并警戒卡 (title, body_md)。items 每项:
    {name, code, price, pct(今日涨跌%), streaks:{5:n,10:n,20:n}, cost_break:{price,date}|None, actions_md}。
    每票均线只报最深破位那档(见 deepest_broken); 另叠加「主力成本线」维度(跌破放量起涨点最低价 → 醒目标注)。
    入卡条件 = 破均线 或 跌破成本线(只破成本线也入卡)。换行文本行版式(手机端不截)。
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
    title = f"📉 尾盘破位警戒 · {len(items)}只持仓"
    body = ("\n".join(lines).rstrip()
            + "\n\n均线同破多档只报最深；🔴主力成本区=放量起涨K线最低价(跌破=资金弃守,仅提示非硬卖点)；收复自动消失。")
    return title, body


# ══════════════ 编排(定时 14:40) ══════════════

async def run_ma_break_watch():
    """交易日尾盘14:40: 真实持仓逐票贴线判 MA5/10/20 破位, 有破位合并推一张警戒卡。"""
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
        qty_map = await repository.get_holdings_qty(user_id)
    except Exception as e:
        logger.warning(f"[ma_break_watch] 取持仓失败: {e}")
        return
    codes = [c for c in qty_map if qty_map.get(c, 0) > 0]
    if not codes:
        return

    try:
        prefs = await pp_repo.active_prefs(user_id)
    except Exception:
        prefs = []
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception as e:
        logger.warning(f"[ma_break_watch] 取现价失败: {e}")
        return

    site = (load_config().get("site_url", "") or "").rstrip("/")
    today = _date.today().isoformat()
    items: list[dict] = []
    for code in codes:
        if pp.ma_watch_snooze_active(prefs, code):
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
        items.append({
            "name": q.get("name") or code, "code": code,
            "price": price, "pct": float(q.get("pct_change") or 0),
            "streaks": streaks, "cost_break": cost_break,
            "actions_md": pp.build_ma_watch_actions_md(site, user_id, code) if site else "",
        })

    if not items:
        return
    # 排序: 跌破成本线(资金弃守,最重)优先 → 再按最深破位均线周期 → 连续天数
    items.sort(key=lambda it: (1 if it.get("cost_break") else 0,
                               deepest_broken(it["streaks"]) or 0,
                               it["streaks"].get(deepest_broken(it["streaks"]) or 0, 0)),
               reverse=True)
    title, body = build_watch_card(items)
    try:
        await notifier.send_dual(body, lark_title=title, template="orange")
    except Exception as e:
        logger.warning(f"[ma_break_watch] 推送失败: {e}")
