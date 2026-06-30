"""持仓研判晚报 — 交易日前夜 20:00 对持仓逐股做数据体检 + AI解读 + 次日操作建议 - v1.7.x.

纯函数(可单测, 不连库不联网):
  classify_holding_state : 把一只持仓的当前日K态归类(供"同类形态前向分布"查表)
  stock_fwd_records      : 单票逐bar 归态 + 取 T+1/T+3 前向收益
  aggregate_state_fwd_dist: 全市场样本聚合成每态分布

离线刷新(每周, 全市场扫描):
  refresh_holding_state_fwd : 扫 cfzy_sys_kline_cache → 每态前向分布 → 写 cfzy_biz_holding_state_fwd
"""
import asyncio
import logging
from collections import defaultdict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 真股票前缀: 沪深主板/创业板/科创板。剔除指数码(98x/88x)/板块/北交所/期货主连。
_STOCK_PREFIX = ("00", "30", "60", "68")
_LOAD_DAYS_FWD = 1500   # 前向分布用近~5年样本(自然日)
_MIN_BARS_FWD = 60      # 单票至少这么多根才参与(指标预热 + 前向窗)

# 持仓态分类(供前向分布查表)
STATE_BULL = "多头站均线"
STATE_PULLBACK = "回踩支撑"
STATE_HIGH_STALL = "高位放量滞涨"
STATE_BREAK_MA20 = "跌破MA20"
STATE_LOW_VOL = "缩量整理"
STATE_OTHER = "其他"

# 分类阈值
_HIGH_EXT = 0.15        # 高位: close 高出 ma20 的比例
_HIGH_VOL_RATIO = 1.5   # 放量: 今日量 / 20日均量
_STALL_PCT = 0.02       # 滞涨: 今日涨幅低于此(放量却不涨)
_LOW_VOL_RATIO = 0.7    # 缩量: 今日量 / 20日均量 低于此
_QUIET_PCT = 0.02       # 横盘: 今日涨跌幅绝对值低于此


def classify_holding_state(d: pd.DataFrame) -> str:
    """给一只持仓的当前日K态归类。d = compute_indicators 输出(带 ma5/10/20/60 等)。"""
    latest = d.iloc[-1]
    close = latest["close"]
    ma5 = latest.get("ma5")
    ma10 = latest.get("ma10")
    ma20 = latest.get("ma20")
    has_ma = all(v is not None and not pd.isna(v) for v in (ma5, ma10, ma20))

    if has_ma and close < ma20:
        return STATE_BREAK_MA20

    if has_ma and close > ma20 * (1 + _HIGH_EXT):
        vol_ratio = latest.get("vol_ratio_20")
        pct = latest.get("pct_change")
        high_vol = vol_ratio is not None and not pd.isna(vol_ratio) and vol_ratio > _HIGH_VOL_RATIO
        stall = pct is None or pd.isna(pct) or pct < _STALL_PCT
        if high_vol and stall:
            return STATE_HIGH_STALL

    if has_ma and close > ma5 > ma10 > ma20:
        return STATE_BULL

    if has_ma and close >= ma20 and ma10 > ma20 and close <= ma5:
        near_ma10 = abs(close / ma10 - 1) < 0.03
        near_ma20 = abs(close / ma20 - 1) < 0.03
        if near_ma10 or near_ma20:
            return STATE_PULLBACK

    if has_ma and close >= ma20:
        vol_ratio = latest.get("vol_ratio_20")
        pct = latest.get("pct_change")
        low_vol = vol_ratio is not None and not pd.isna(vol_ratio) and vol_ratio < _LOW_VOL_RATIO
        quiet = pct is not None and not pd.isna(pct) and abs(pct) < _QUIET_PCT
        if low_vol and quiet:
            return STATE_LOW_VOL

    return STATE_OTHER


def stock_fwd_records(d: pd.DataFrame, start: int = 20) -> list[dict]:
    """单票逐bar: 归态 + 取 T+1/T+3 前向收益。供全市场前向分布扫描。

    d: 日K(若无指标列则内部 compute_indicators)。start 前(MA20 未热)跳过;
    末尾不足 3 根前向窗的 bar 丢弃(避免用不完整未来数据)。
    返回 [{state, fwd1, fwd3}, ...] —— 每根bar一条(不做去重, 要的是整体前向分布)。
    """
    from backend.services.signal_engine_indicators import compute_indicators
    if "ma20" not in d.columns:
        d = compute_indicators(d)
    closes = d["close"].values
    n = len(d)
    out: list[dict] = []
    for i in range(start, n - 3):
        c0 = float(closes[i])
        if c0 <= 0:
            continue
        out.append({
            "state": classify_holding_state(d.iloc[:i + 1]),
            "fwd1": float(closes[i + 1]) / c0 - 1.0,
            "fwd3": float(closes[i + 3]) / c0 - 1.0,
        })
    return out


def aggregate_state_fwd_dist(records: list[dict], min_sample: int = 30) -> dict[str, dict]:
    """把全市场历史「某态→T+1/T+3 前向收益」样本聚合成每态分布(百分比)。

    records: [{state, fwd1, fwd3}, ...]  fwd 为收益率小数(0.02=+2%)。
    返回 {state: {n, up_rate_1, median_1, p10_1, p90_1, up_rate_3, median_3, p10_3, p90_3}}。
    样本数 < min_sample 的态略过(小样本噪声不当客观概率)。
    """
    by_state: dict[str, list[dict]] = {}
    for r in records:
        by_state.setdefault(r["state"], []).append(r)

    out: dict[str, dict] = {}
    for state, rows in by_state.items():
        if len(rows) < min_sample:
            continue
        stat = {"n": len(rows)}
        for h in (1, 3):
            arr = np.array([r[f"fwd{h}"] for r in rows], dtype="float64") * 100.0
            stat[f"up_rate_{h}"] = round(float((arr > 0).mean()) * 100, 1)
            stat[f"median_{h}"] = round(float(np.median(arr)), 2)
            stat[f"p10_{h}"] = round(float(np.percentile(arr, 10)), 2)
            stat[f"p90_{h}"] = round(float(np.percentile(arr, 90)), 2)
        out[state] = stat
    return out


def _is_stock(code: str) -> bool:
    """仅保留真 A 股(沪深主板/创业/科创); 缓存里残留的指数/板块/北交所码一律剔除。"""
    return str(code)[:2] in _STOCK_PREFIX


def _crunch_fwd(rows: list[dict], min_sample: int = 200) -> dict[str, dict]:
    """逐票取前向样本 + 按态聚合(纯CPU, 在线程池里跑)。

    rows: 全市场日线缓存行(code/trade_date/ohlcv, 已按 code,trade_date 排序)。
    返回 {state: {n, up_rate_1, median_1, ...}} —— 全市场五年某态的真实前向分布。
    """
    from backend.services.signal_engine_indicators import compute_indicators
    by_code: dict[str, list] = defaultdict(list)
    for r in rows:
        by_code[str(r["code"])].append(r)

    records: list[dict] = []
    for code, krows in by_code.items():
        if not _is_stock(code) or len(krows) < _MIN_BARS_FWD:
            continue
        df = pd.DataFrame(krows).rename(columns={"trade_date": "date"})
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        if len(df) < _MIN_BARS_FWD:
            continue
        try:
            records.extend(stock_fwd_records(compute_indicators(df)))
        except Exception:
            pass
    return aggregate_state_fwd_dist(records, min_sample=min_sample)


async def refresh_holding_state_fwd():
    """每周一次: 读全市场日线缓存 → 线程池算每态前向分布 → upsert cfzy_biz_holding_state_fwd。"""
    from datetime import datetime, timedelta

    from backend.models import repository
    from backend.models.repo._db import _fetchall

    load_from = (datetime.now() - timedelta(days=_LOAD_DAYS_FWD)).strftime("%Y-%m-%d")
    rows = await _fetchall(
        "SELECT code, trade_date, open, high, low, close, volume FROM cfzy_sys_kline_cache "
        "WHERE trade_date >= %s ORDER BY code, trade_date",
        (load_from,),
    )
    if not rows:
        logger.warning("[holding_fwd] 全市场日线缓存为空, 跳过")
        return
    run_date = max(str(r["trade_date"])[:10] for r in rows)
    dist = await asyncio.to_thread(_crunch_fwd, rows)
    await repository.save_holding_state_fwd(run_date, dist)
    logger.info("[holding_fwd] 重算完成 截至%s: " % run_date + " ".join(
        f"{st}(n{d['n']} T+1涨{d['up_rate_1']}%/中{d['median_1']:+}%)" for st, d in dist.items()))
    return {"as_of": run_date, "dist": dist}


# ── 报告生成: 前向分布格式化 / AI prompt / 解析 / 渲染 ──

ACTION_EMOJI = {"持有": "🟢", "加仓": "🔵", "减仓": "🟡", "清仓": "🔴"}


def fmt_fwd(state: str, fwd: dict | None) -> str:
    """把某态前向分布渲染成一句客观概率。无样本返回空串。"""
    if not fwd or not fwd.get("n"):
        return ""
    return (f"同类形态({state})历史 次日↑{fwd['up_rate_1']}%·中位{fwd['median_1']:+}% / "
            f"3日↑{fwd['up_rate_3']}%·中位{fwd['median_3']:+}% (n{fwd['n']})")


def build_brief_prompt(payloads: list[dict], market_brief: str) -> tuple[str, str]:
    """构造 (system, user)。要求 AI 逐股输出方向性建议 JSON, 便于解析。"""
    import json

    system = (
        "你是A股短线交易助手。下面给你某用户每只持仓的客观数据(成本/浮盈/持有天数/建仓买点模型及其实测胜率/"
        "当前K线技术态/同类形态历史次日真实分布/板块内强弱/持仓守护信号)。请逐股给出**次日**方向性操作建议。\n"
        "严格只输出一个 JSON 数组, 每只持仓一个对象, 字段:\n"
        '  code(股票代码), action(必须是 持有/加仓/减仓/清仓 之一), '
        'target(次日目标价 number), stop(止损价 number), reason(一句话理由, ≤40字, 须结合数据)。\n'
        "建议要基于给的客观数据(尤其同类形态历史分布与实测胜率), 不要套话。只输出JSON, 不要任何额外文字。"
    )
    user = f"次日大盘环境: {market_brief}\n\n持仓数据(JSON):\n" + json.dumps(payloads, ensure_ascii=False)
    return system, user


def parse_ai_verdicts(text: str) -> dict[str, dict]:
    """从AI回复里抽出逐股建议, 返回 {code: {action, target, stop, reason}}。
    解析失败/字段缺失一律返回空(或略过该股) —— 三层兜底之一。"""
    import json

    if not text:
        return {}
    s = text.strip()
    if "```" in s:   # 去掉 ```json ... ``` 围栏
        parts = s.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("["):
                s = p
                break
    lo, hi = s.find("["), s.rfind("]")
    if lo == -1 or hi == -1 or hi < lo:
        return {}
    try:
        arr = json.loads(s[lo:hi + 1])
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        action = str(it.get("action", "")).strip()
        if not code or action not in ACTION_EMOJI:
            continue
        out[code] = {"action": action, "target": it.get("target"),
                     "stop": it.get("stop"), "reason": str(it.get("reason", "")).strip()}
    return out


def _fmt_num(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def render_wechat_text(payloads: list[dict], verdicts: dict[str, dict], market_brief: str) -> str:
    """微信/PushPlus 精简文本: 顶部前言 + 逐股三行 + 底部免责。空仓一行。"""
    if not payloads:
        return "📋 持仓研判晚报\n今日空仓，无需研判。"
    lines = [f"📋 持仓研判晚报 · 次日{market_brief}", ""]
    for p in payloads:
        code = p["code"]
        v = verdicts.get(code)
        lines.append(
            f"▸ {p['name']}({code}) {_fmt_num(p.get('price'))} {p.get('pct_change', 0):+.1f}% · "
            f"持{p.get('hold_days', 0)}天 {p.get('entry_model_name', '')} 浮盈{p.get('profit_pct', 0):+.1f}%")
        if v:
            emoji = ACTION_EMOJI.get(v["action"], "")
            lines.append(f"  {emoji}{v['action']} 目标{_fmt_num(v.get('target'))}·止损{_fmt_num(v.get('stop'))}")
            if v.get("reason"):
                lines.append(f"  {v['reason']}")
        else:
            lines.append("  —（AI研判未生成）")
    lines.append("")
    lines.append("AI研判仅供参考，最终决策在你。")
    return "\n".join(lines)


def build_lark_elements(payloads: list[dict], verdicts: dict[str, dict], market_brief: str) -> list:
    """飞书卡 elements: 前言 md + 逐股表格 + 免责 md。空仓一条 md。"""
    from backend.services.lark_notifier import md_element, table_element

    if not payloads:
        return [md_element("**今日空仓**，无需研判。")]
    els = [md_element(f"**次日大盘环境**：{market_brief}")]
    columns = [
        {"name": "stock", "display_name": "股票", "data_type": "text", "width": "26%", "horizontal_align": "left"},
        {"name": "state", "display_name": "状态/浮盈", "data_type": "text", "width": "24%", "horizontal_align": "left"},
        {"name": "advice", "display_name": "次日建议", "data_type": "text", "width": "22%", "horizontal_align": "left"},
        {"name": "reason", "display_name": "理由", "data_type": "text", "width": "28%", "horizontal_align": "left"},
    ]
    rows = []
    for p in payloads:
        v = verdicts.get(p["code"])
        if v:
            emoji = ACTION_EMOJI.get(v["action"], "")
            advice = f"{emoji}{v['action']}\n目标{_fmt_num(v.get('target'))} 止损{_fmt_num(v.get('stop'))}"
            reason = v.get("reason", "")
        else:
            advice, reason = "—", "AI研判未生成"
        rows.append({
            "stock": f"{p['name']}\n{p['code']} {_fmt_num(p.get('price'))}",
            "state": f"{p.get('state', '')}\n浮盈{p.get('profit_pct', 0):+.1f}% 持{p.get('hold_days', 0)}天",
            "advice": advice,
            "reason": reason,
        })
    els.append(table_element(columns, rows))
    els.append(md_element("**AI研判仅供参考，最终决策在你。** 客观概率源自全市场五年同类形态回测。"))
    return els


# ── 编排: 数据组装 / AI调用 / 20:00 入口 ──

BRIEF_TITLE = "📋 持仓研判晚报"
_RISK_BRIEF = {
    "GREEN": "风险偏低，可正常持仓",
    "YELLOW": "谨慎，控制仓位",
    "RED": "高风险，防守为主",
}


def _hold_days(entry_date: str | None) -> int:
    from datetime import datetime
    if not entry_date:
        return 0
    try:
        return max(0, (datetime.now() - datetime.strptime(entry_date[:10], "%Y-%m-%d")).days)
    except Exception:
        return 0


async def _market_brief() -> str:
    """次日大盘环境一句话: 复用市场风险等级(GREEN/YELLOW/RED)。"""
    try:
        from backend.services.market_risk_controller import get_risk_state
        return _RISK_BRIEF.get(await get_risk_state(), "震荡")
    except Exception:
        return "震荡"


async def gather_holdings_payload(user_id: int = 1) -> list[dict]:
    """组装每只持仓的逐股 payload(成本/浮盈/持有天数/建仓模型+战绩/持仓态/同类形态前向分布/板块名次/守护信号)。"""
    from backend import data_fetcher
    from backend.models import repository
    from backend.services import buy_model_stats
    from backend.services.sector_strength_scanner import compute_board_rank
    from backend.services.signal_engine_indicators import compute_indicators
    from backend.services import holding_guard as hg

    cost_map, date_map, model_map = await repository.get_holdings_full_info(user_id)
    codes = list(cost_map.keys())
    if not codes:
        return []
    quotes = await data_fetcher.get_realtime_quotes(codes)
    stats = await buy_model_stats.get_buy_model_stats()
    fwd_all = await repository.get_holding_state_fwd()

    payloads: list[dict] = []
    for code in codes:
        try:
            q = quotes.get(code, {}) or {}
            price = q.get("price")
            cost = cost_map.get(code) or 0.0
            entry_date = date_map.get(code)
            sid = model_map.get(code, "")
            st_row = stats.get(sid, {}) if sid else {}
            entry_model_name = st_row.get("model_name") or sid or "—"

            df = await data_fetcher.get_daily_kline(code, days=max(80, _hold_days(entry_date) + 20))
            state = classify_holding_state(compute_indicators(df)) if df is not None and len(df) else "其他"
            fwd = fwd_all.get(state)

            # 守护信号(复用 holding_guard 口径)
            near_high = False
            if df is not None and len(df) and price:
                ph, _ = hg.prior_high(df)
                near_high = bool(ph and hg.is_near_high(price, ph))
            profit_protect = False
            if df is not None and len(df) and price and cost:
                peak = hg.compute_peak(df, entry_date, price)
                profit_protect = hg.profit_protect_triggered(cost, peak, price)

            board = compute_board_rank(code, q.get("pct_change"))
            # cost>0 才算浮盈%; 摊薄成本 ≤0(超额落袋)极罕见, 回退0防 None 格式化崩 & 假亏损显示
            profit_pct = round((price / cost - 1) * 100, 1) if (price and cost and cost > 0) else 0.0

            payloads.append({
                "code": code,
                "name": q.get("name") or code,
                "price": price,
                "pct_change": round(q.get("pct_change", 0.0) or 0.0, 2),
                "cost": round(cost, 3),
                "profit_pct": profit_pct,
                "hold_days": _hold_days(entry_date),
                "entry_model_name": entry_model_name,
                "winrate_text": (f"实测胜率{st_row.get('win_rate_3m')}%·均收{st_row.get('net_3m')}%"
                                 f"·第{st_row.get('rank_3m')}名" if st_row.get("win_rate_3m") is not None else ""),
                "state": state,
                "fwd_text": fmt_fwd(state, fwd),
                "board_text": (f"{board['board_name']}内第{board['board_rank']}/{board['board_total']}"
                               if board else ""),
                "near_prior_high": near_high,
                "profit_protect_alert": profit_protect,
            })
        except Exception as e:
            logger.warning(f"[holding_brief] 组装 {code} 失败: {e}")
    return payloads


async def generate_holding_brief_verdicts(payloads: list[dict], market_brief: str) -> dict[str, dict]:
    """喂 DeepSeek 出逐股方向性建议。任何失败返回空(渲染层兜底标'未生成')。"""
    if not payloads:
        return {}
    from backend.core.config import load_config
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key:
        logger.warning("[holding_brief] 未配置 AI key, 跳过研判")
        return {}
    system, user = build_brief_prompt(payloads, market_brief)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=cfg.get("ai_base_url", "https://api.deepseek.com/v1"))
        resp = await asyncio.to_thread(lambda: client.chat.completions.create(
            model=cfg.get("ai_model", "deepseek-chat"),
            max_tokens=4096,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        ))
        return parse_ai_verdicts(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"[holding_brief] AI 研判失败: {e}")
        return {}


async def run_holding_evening_report(user_id: int = 1):
    """交易日前夜 20:00: 仅当"明天是交易日"才发。逐股数据→AI研判→推送一张卡。"""
    from datetime import datetime, timedelta

    from backend.core.trading_calendar import is_workday
    from backend.services import notifier

    if not is_workday(datetime.now() + timedelta(days=1)):
        return   # 明天非交易日(周五晚/节假日前)不发
    market_brief = await _market_brief()
    payloads = await gather_holdings_payload(user_id)
    if not payloads:
        await notifier.send_dual(render_wechat_text([], {}, market_brief), lark_title=BRIEF_TITLE)
        return {"holdings": 0}
    verdicts = await generate_holding_brief_verdicts(payloads, market_brief)
    text = render_wechat_text(payloads, verdicts, market_brief)
    els = build_lark_elements(payloads, verdicts, market_brief)
    await notifier.send_dual_card(text, lark_title=BRIEF_TITLE, elements=els)
    logger.info(f"[holding_brief] 已推送 {len(payloads)} 只持仓研判(AI {len(verdicts)} 条)")
    return {"holdings": len(payloads), "verdicts": len(verdicts)}
