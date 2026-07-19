"""短线买点 买卖提醒任务 (RALLY_MODELS 名册内模型: 回踩MA20/MA10 + 缩量后放量突破 + 中继平台突破) - v1.7.x.

把名册内买点 + 卖点规则提炼成提醒, 企微+飞书推送。

持仓来源(混合): 触发即建跟踪持仓; 有买入交割单则以交割单价为准, 否则用触发价。
推送闸门(v1.7.525): 跟踪是「触发即建」的虚拟模型仓, 卖出提醒(减半/止损/时停)只对「真实持仓」(交割单FIFO)
  推送; 未持有的票仍按跟踪规则落库喂卖点胜率统计、推进跟踪态, 但不推送给用户(查持仓失败兜底放行)。
卖出规则(各模型共用, 仅"剩半跟踪线"锚点不同):
  +7%卖半 / -6%收盘止损 / 满10交易日(T+10)时停  —— 各模型相同
  剩半跟踪: 回踩20MA=收盘破MA20×0.98 ; 其余三模型=收盘破MA10×0.98
T+1 规则: 当天买入当天不能卖, 所有卖出判定均从 T+1 起(signal_date < 今日)才生效。

两个 handler:
  rally_reminder_tick : 盘中(interval 60s) — 建仓跟踪(买点推送由扫描器富卡片负责); +7% 盘中触及即推止盈减半
  rally_reminder_eod  : 尾盘 14:40(cron) — 收盘价判 -6%止损/剩半破跟踪线/T+10时停; 交割单对账
"""
import logging
from datetime import datetime

from backend import data_fetcher
from backend.models import repository
from backend.services import notifier
from backend.services.market_breadth_refresher import breadth_band
from backend.services.signal_specs import group_of

logger = logging.getLogger(__name__)

TARGET = 0.07
HARD = -0.06
CAP_DAYS = 10

# 各模型: 剩半跟踪均线窗 ma_win + 容差 runner_tol; plan=买入提醒里展示的交易计划文案
# v1.7.405: 缩量后放量突破/中继平台突破 出场口径与回踩10MA完全相同(回测同套出场寻优), 直接入册
# v1.7.584: 剩半跟踪改「沿5日线飘」(课件中线六二法「大涨减仓降成本→剩仓沿5日线飘」)。全市场双窗OOS
#   回测(实盘框架, 卖半后剩半破跟踪线): 回踩MA10/MA20/缩量突破 三模型独立样本 胜率/均收/PF 全升
#   (回踩MA20 56.0%/PF1.65→59.7%/PF1.87 提升最大)。ma_win=5 即剩半跟踪MA5(注: 这是【剩半跟踪均线窗】,
#   非买点回踩锚均线); runner_tol=0 即收盘破MA5即清(无×容差)。中继平台突破未验证B5, 保持破MA10×0.98。
RALLY_MODELS = {
    "BUY_RALLY_MA20": {"name": "回踩20MA缩量后突破昨高", "ma_win": 5, "runner_tol": 0.0,
                       "plan": "+7%卖半 / 剩半收盘破MA5清 / -6%收盘止损 / 满10交易日时停"},
    "BUY_RALLY_MA10": {"name": "回踩10MA缩量后突破昨高", "ma_win": 5, "runner_tol": 0.0,
                       "plan": "+7%卖半 / 剩半收盘破MA5清 / -6%收盘止损 / 满10交易日时停"},
    # v1.7.593 回踩MA60(中线六二法60日档): 出场OOS对比 剩半破MA5 PF1.96 > 破MA10 1.39 > 破MA20 1.19, 与回踩族同口径
    "BUY_RALLY_MA60": {"name": "回踩60MA缩量后突破昨高", "ma_win": 5, "runner_tol": 0.0,
                       "plan": "+7%卖半 / 剩半收盘破MA5清 / -6%收盘止损 / 满10交易日时停"},
    "BUY_VOL_BREAKOUT": {"name": "缩量后放量突破", "ma_win": 5, "runner_tol": 0.0,
                         "plan": "+7%卖半 / 剩半收盘破MA5清 / -6%收盘止损 / 满10交易日时停"},
    "BUY_PLATFORM_BREAKOUT": {"name": "中继平台突破", "ma_win": 10, "runner_tol": 0.02,
                              "plan": "+7%卖半 / 剩半收盘破MA10×0.98清 / -6%收盘止损 / 满10交易日时停"},
}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _model_of(signal_id: str) -> dict:
    return RALLY_MODELS.get(signal_id, RALLY_MODELS["BUY_RALLY_MA20"])


def _should_notify(code: str, held) -> bool:
    """卖出提醒是否推送: 只对真实持仓推(v1.7.525)。跟踪是「触发即建」的虚拟模型仓, 与真实持仓无关,
    故未持有的票只落库喂卖点胜率统计、不推送。held=None(查持仓失败)兜底放行, 不漏真实持仓风控提醒。"""
    if held is None:
        return True
    return code in held


async def _real_holdings():
    """真实持仓代码集(user 1, 交割单 FIFO)。查失败返回 None → 调用方兜底放行(宁多推不漏止损)。"""
    try:
        from backend.models.repo.holdings import get_holdings_cost
        return set((await get_holdings_cost(1)).keys())
    except Exception as e:
        logger.warning(f"[rally_reminder] 查真实持仓失败, 本轮卖出提醒兜底放行: {e}")
        return None


def _sell_sid(buy_sid: str, half: bool = False) -> str:
    """由买点 signal_id 派生卖点 signal_id: BUY_RALLY_MA20 → SELL_RALLY_MA20(_HALF)。
    减半与清仓用不同 sid, 避免同日 INSERT IGNORE 互相覆盖。"""
    base = buy_sid.replace("BUY_", "SELL_", 1) if buy_sid and buy_sid.startswith("BUY_") else f"SELL_{buy_sid or 'RALLY'}"
    return base + ("_HALF" if half else "")


async def _save_sell_signal(code: str, name: str, sid: str, signal_name: str,
                            direction: str, price: float, detail: str):
    """把回踩MA卖出提醒落库成信号, 让分时图/日K图能标注 + 进卖点胜率统计。
    rally_reminder 自身已推送; 这里仅持久化(save_signal 用 INSERT IGNORE 当日去重)。
    user 固定 1, 与本系统单用户(交割单/跟踪持仓均按 user 1)一致。"""
    try:
        await repository.save_signal(code, name, sid, signal_name, direction,
                                     float(price or 0), detail, user_id=1,
                                     signal_group=group_of(sid) or "exit")
    except Exception as e:
        logger.warning(f"[rally_reminder] 卖点落库失败({code} {sid}): {e}")


async def _breadth_note() -> str:
    try:
        b = await repository.get_latest_breadth()
        if not b or b.get("ma20_ratio") is None:
            return ""
        pct = float(b["ma20_ratio"])
        label, _level, _hint = breadth_band(pct)
        warn = " ⚠️环境偏弱、谨慎" if pct < 45 else ""
        return f"强势股占比{pct:.0f}%({label}){warn}"
    except Exception:
        return ""


async def _resolve_entry(code: str, signal_date: str, trigger_price: float):
    """有买入交割单(成交日≥signal_date)则以其价为准, 否则用触发价。"""
    try:
        trades = await repository.get_all_trade_records(1)
        cands = [t for t in trades
                 if t.get("code") == code and t.get("direction") == "buy"
                 and str(t.get("trade_date", "")) >= signal_date]
        if cands:
            b = min(cands, key=lambda x: str(x.get("trade_date", "")))
            return float(b["price"]), "交割单"
    except Exception as e:
        logger.debug(f"[rally_reminder] 交割单查询失败({code}): {e}")
    return float(trigger_price or 0), "触发价"


async def _ma(code: str, win: int):
    try:
        df = await data_fetcher.get_daily_kline(code, days=win + 25)
        if df is None or df.empty:
            return None
        # 剔今日半根bar再取均: get_daily_kline盘中可能带当日未收盘bar, 直接tail会把今日现价
        # 算进MA(自引用)、且与回测/全系统"完整日线MA"口径不一致。剩半破MA=今日价 vs 完整trailing均线。
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        hist = df[df["date"].astype(str).str[:10] < today]
        if len(hist) < win:
            return None
        return float(hist["close"].tail(win).mean())
    except Exception:
        return None


async def rally_reminder_tick():
    """盘中: 建仓+买入提醒(回踩20MA缩量后突破昨高/回踩10MA缩量后突破昨高); +7% 盘中触及即推(仅 T+1 起的持仓)。"""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    # 交易窗口门(09:25~15:10): 信号只在盘中产生, 原来全天每60s查一次今日信号纯属空转
    hm = datetime.now().strftime("%H:%M")
    if not ("09:25" <= hm <= "15:10"):
        return
    today = _today()

    # ① 建仓跟踪 (买点推送由扫描器富卡片负责, 此处仅建跟踪供卖出端用)
    try:
        sigs = await repository.get_today_signals_all()
    except Exception as e:
        logger.warning(f"[rally_reminder] 查今日信号失败: {e}")
        sigs = []
    for s in sigs:
        sid = s.get("signal_id")
        if sid not in RALLY_MODELS or s.get("direction") != "buy":
            continue
        code = s.get("code")
        sd = str(s.get("triggered_at"))[:10] if s.get("triggered_at") else today
        if not code or await repository.track_exists(code, sd):  # 同股同日只跟踪一笔
            continue
        entry, src = await _resolve_entry(code, sd, s.get("price") or 0)
        if entry <= 0:
            continue
        name = s.get("name") or code
        # 只建跟踪持仓供卖出端(+7%止盈减半/止损/时停)使用; 买点推送已由扫描器富卡片
        # (带模型战绩/分时图, scanner._push_strong_wechat)覆盖, 此处不再重复推"·买入提醒"
        # (曾致同一买点两条推送, 见 v1.7.434)。
        await repository.create_track(code, name, sid, sd, entry, src)

    # ② +7% 盘中止盈减半 (仅 T+1 起: signal_date < 今日)
    from backend.services.intraday_estimator import is_intraday
    if not is_intraday():
        return
    tracks = await repository.get_holding_tracks()
    pend = [t for t in tracks if not t["half_sold"] and str(t["signal_date"]) < today]
    if not pend:
        return
    held = await _real_holdings()   # 卖出提醒只推真实持仓
    try:
        quotes = await data_fetcher.get_realtime_quotes([t["code"] for t in pend])
    except Exception:
        quotes = {}
    for t in pend:
        q = quotes.get(t["code"])
        if not q or not q.get("price"):
            continue
        if float(q["price"]) >= t["entry_price"] * (1 + TARGET):
            tgt = t["entry_price"] * (1 + TARGET)
            m = _model_of(t.get("signal_id"))
            msg = f"已达+7%(¥{tgt:.2f})，按计划卖出50%，剩半改用 收盘破跟踪线 跟踪"
            try:
                if _should_notify(t["code"], held):
                    await notifier.send_wechat_signal(
                        t["code"], t["name"], f"{m['name']}·止盈减半", "reduce", float(q["price"]), msg)
                # 落库+喂统计无论是否持仓都做; 跟踪态(half_sold)照常推进
                await _save_sell_signal(t["code"], t["name"],
                                        _sell_sid(t.get("signal_id"), half=True),
                                        f"{m['name']}·止盈减半", "reduce", float(q["price"]), msg)
                await repository.mark_half_sold(t["id"])
            except Exception as e:
                logger.warning(f"[rally_reminder] 减半提醒失败({t['code']}): {e}")


async def rally_reminder_eod():
    """尾盘14:40: 收盘价判 -6%止损/剩半破跟踪线/T+10时停 (仅 T+1 起的持仓)。"""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    today = _today()
    tracks = await repository.get_holding_tracks()
    tracks = [t for t in tracks if str(t["signal_date"]) < today]   # T+1 起
    if not tracks:
        return
    held = await _real_holdings()   # 卖出提醒只推真实持仓
    try:
        quotes = await data_fetcher.get_realtime_quotes([t["code"] for t in tracks])
    except Exception:
        quotes = {}

    missing = []   # 拿不到价的持仓: 不能静默跳过(0713 行情冻结致止损整轮漏检, v1.7.647)
    for t in tracks:
        code = t["code"]
        m = _model_of(t.get("signal_id"))
        # 交割单对账: 仍是触发价的, 若已有买入交割单则以其为准
        if t["entry_source"] == "触发价":
            ep, src = await _resolve_entry(code, t["signal_date"], t["entry_price"])
            if src == "交割单" and ep > 0 and abs(ep - t["entry_price"]) > 1e-6:
                await repository.update_entry(t["id"], ep, src)
                t["entry_price"] = ep
        q = quotes.get(code)
        price = float(q["price"]) if q and q.get("price") else None
        if price is None:
            missing.append(f"{t['name']}({code})")
            continue
        entry = t["entry_price"]

        notify = _should_notify(code, held)
        if not t["half_sold"]:
            if price <= entry * (1 + HARD):
                await _push_close(t, m, price, "止损",
                                  f"收盘{(price/entry-1)*100:+.1f}%(≤-6%)，全部清仓", notify)
                continue
        else:
            ma = await _ma(code, m["ma_win"])
            if ma and price < ma * (1 - m["runner_tol"]):
                line = ma * (1 - m["runner_tol"])
                # runner_tol=0(沿5日线) 文案不带×系数; >0 保留 ×mult 显示
                ma_lbl = f"MA{m['ma_win']}" if m["runner_tol"] == 0 else f"MA{m['ma_win']}×{1-m['runner_tol']:.2f}"
                await _push_close(t, m, price, "清剩半",
                                  f"剩半收盘跌破{ma_lbl}(¥{line:.2f})，清仓剩余50%", notify)
                continue
        days = int(t["days_held"]) + 1
        await repository.set_days_held(t["id"], days)
        if days >= CAP_DAYS:
            await _push_close(t, m, price, "时间止损",
                              f"持有满{CAP_DAYS}交易日仍未离场，收盘清仓", notify)

    if missing:
        # 主源+备源都拿不到价 → 今日止损/时停对这些票没查成, 明日EOD自动补查; 但必须告警别静默
        msg = ("⚠️ 尾盘止损检查失败：" + "、".join(missing) +
               " 实时行情不可用(主源+备源均失败)，今日 -6%止损/T+10时停 未检查，"
               "明日尾盘自动补查。若持有请人工核对收盘价！")
        logger.error(f"[rally_reminder] {msg}")
        try:
            await notifier.send_wechat_text(msg)
        except Exception as e:
            logger.warning(f"[rally_reminder] 止损检查失败告警推送失败: {e}")


async def _push_close(track: dict, model: dict, price: float, action: str, msg: str, notify: bool = True):
    """notify=False(非真实持仓): 只落库喂卖点胜率统计 + 关跟踪, 不推送给用户(v1.7.525)。"""
    if notify:
        try:
            await notifier.send_wechat_signal(
                track["code"], track["name"], f"{model['name']}·{action}", "sell", price, msg)
        except Exception as e:
            logger.warning(f"[rally_reminder] 卖出提醒推送失败({track['code']}): {e}")
    await _save_sell_signal(track["code"], track["name"],
                            _sell_sid(track.get("signal_id"), half=False),
                            f"{model['name']}·{action}", "sell", price, msg)
    await repository.close_track(track["id"], action)
