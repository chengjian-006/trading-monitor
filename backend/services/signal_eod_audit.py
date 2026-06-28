"""信号EOD自动复核 (v1.7.387) — 每交易日17:00用收盘真实日线复核当日全部信号.

背景(0612误报普查整改): 序列错位/分时冻结回放/竞价涨跌家数0/0三类数据层假象靠人工普查
才暴露, 本模块把普查自动化: 收盘后日线已确定, 逐条核当日信号, 对不上的标记存疑(suspect)
并推送提醒 — 只标记不自动删, 留人工确认。

复核手段(按信号类型):
  BUY_/SELL_ 个股信号:
    1. 指纹核对 — 触发时 indicators 里落的 prev_bar_date/prev_close(检测器当时认的"昨日")
       vs 真实昨日bar → K线序列错位的精确判别(v1.7.387 起新信号才有指纹, 旧信号跳过)。
    2. 触发价区间 — 触发价必须落在当日真实 [低×0.98, 高×1.02] → 垃圾行情判别。
  PLUNGE_INDEX: 当日指数真实高低波幅容不下宣称的"N分钟跌X%" → 冻结回放判别(跨源核对:
    复核用新浪日线, 触发用东财/同花顺分时, 同源造假骗不过)。
  PLUNGE_BREADTH / PLUNGE_SPEED: detail 里 上涨0家且下跌0家 / 跌停家数超合理上限 → 竞价假象。
  SECTOR_/SCORE_ 等不复核(无单一价格断言)。

结果写 cfzy_biz_signals.eod_audit('ok'/'suspect'/'unverified') + eod_audit_note;
有存疑时推送企微文本+飞书表格卡。
"""
import json
import logging
import re
from datetime import date

from backend.core.trading_calendar import is_workday
from backend.fetcher import klines
from backend.models import repository
from backend.services import notifier

logger = logging.getLogger(__name__)

# 触发价区间容差(实时行情与日线源之间的正常口径差)
_PRICE_TOL = 0.02
# 跌停家数合理上限: A股历史极端(2015股灾/2016熔断)约2000家, 超过即数据假象
_LIMIT_DOWN_CAP = 1500
# 宣称急跌幅 vs 当日真实波幅的容差系数
_RANGE_TOL = 0.7

_INDEX_NAME_TO_SYMBOL = (
    ("上证指数", "sh000001"),
    ("创业板指", "sz399006"),
    ("科创指数", "sh000688"),
)


def check_fingerprint(indicators: dict | None, true_prev_date: str,
                      true_prev_close: float) -> str | None:
    """触发时认的"昨日" vs 真实昨日。无指纹(旧信号/plunge)返回 None 不拦。"""
    if not indicators:
        return None
    fp_date = indicators.get("prev_bar_date")
    fp_close = indicators.get("prev_close")
    if not fp_date or fp_close is None:
        return None
    if str(fp_date)[:10] != str(true_prev_date)[:10]:
        return f"K线序列错位: 触发时认的昨日={str(fp_date)[:10]}, 实际昨日={str(true_prev_date)[:10]}"
    try:
        if true_prev_close > 0 and abs(float(fp_close) - true_prev_close) / true_prev_close > 0.005:
            return f"昨收不符: 触发时昨收={fp_close}, 实际昨收={true_prev_close}"
    except (TypeError, ValueError):
        return None
    return None


def check_price_range(price: float, day_low: float, day_high: float) -> str | None:
    """触发价不在当日真实价格区间(±2%容差) → 垃圾行情。"""
    try:
        price, day_low, day_high = float(price), float(day_low), float(day_high)
    except (TypeError, ValueError):
        return None
    if price <= 0 or day_low <= 0 or day_high <= 0:
        return None
    if price < day_low * (1 - _PRICE_TOL) or price > day_high * (1 + _PRICE_TOL):
        return f"触发价{price:g}不在当日真实区间[{day_low:g}, {day_high:g}]"
    return None


def check_breadth_detail(detail: str) -> str | None:
    """涨跌家数恶化 detail 自洽性: 0/0=无数据假象; 跌停家数超合理上限=全场脏值。"""
    m_up = re.search(r"上涨(\d+)家", detail or "")
    m_down = re.search(r"下跌(\d+)家", detail or "")
    if m_up and m_down and int(m_up.group(1)) == 0 and int(m_down.group(1)) == 0:
        return "上涨0家且下跌0家 = 竞价时段/降级源无数据假象"
    m_ld = re.search(r"跌停(\d+)家", detail or "")
    if m_ld and int(m_ld.group(1)) > _LIMIT_DOWN_CAP:
        return f"跌停{m_ld.group(1)}家超合理上限{_LIMIT_DOWN_CAP}"
    return None


def check_speed_detail(detail: str) -> str | None:
    """跌停加速 detail 自洽性: 当前/新增跌停家数超合理上限。"""
    m_total = re.search(r"当前跌停共(\d+)家", detail or "")
    if m_total and int(m_total.group(1)) > _LIMIT_DOWN_CAP:
        return f"当前跌停{m_total.group(1)}家超合理上限{_LIMIT_DOWN_CAP}"
    m_new = re.search(r"新增跌停(\d+)家", detail or "")
    if m_new and int(m_new.group(1)) > 500:
        return f"新增跌停{m_new.group(1)}家超合理上限500"
    return None


def check_index_drop_detail(detail: str, day_high: float, day_low: float,
                            pre_close: float) -> str | None:
    """宣称的"N分钟跌X%"必须被当日真实高低波幅容纳, 容不下=分时冻结回放。"""
    m = re.search(r"分钟内跌幅 (-?\d+(?:\.\d+)?)%", detail or "")
    if not m:
        return None
    try:
        day_high, day_low, pre_close = float(day_high), float(day_low), float(pre_close)
    except (TypeError, ValueError):
        return None
    if pre_close <= 0 or day_high <= 0 or day_low <= 0:
        return None
    claimed = abs(float(m.group(1)))
    day_range_pct = (day_high - day_low) / pre_close * 100
    if day_range_pct < claimed * _RANGE_TOL:
        return (f"当日真实波幅{day_range_pct:.2f}%容不下宣称的{claimed:.2f}%急跌, "
                f"疑似分时冻结回放")
    return None


def parse_index_symbol(detail: str) -> str | None:
    for name, sym in _INDEX_NAME_TO_SYMBOL:
        if name in (detail or ""):
            return sym
    return None


def _load_indicators(sig: dict) -> dict | None:
    ind = sig.get("indicators")
    if isinstance(ind, str):
        try:
            return json.loads(ind)
        except (json.JSONDecodeError, TypeError):
            return None
    return ind if isinstance(ind, dict) else None


async def _audit_stock_signal(sig: dict, kline_cache: dict, today: str) -> tuple[str, str]:
    """个股 BUY_/SELL_ 信号 → (status, note)."""
    code = sig["code"]
    if code not in kline_cache:
        try:
            kline_cache[code] = await klines.get_daily_kline(code, days=10)
        except Exception as e:
            logger.warning(f"[eod_audit] {code} 日线获取失败: {e}")
            kline_cache[code] = None
    df = kline_cache[code]
    if df is None or df.empty:
        return "unverified", "当日真实日线获取失败"
    df = df[df["date"].astype(str).str[:10] <= today]
    if df.empty or str(df.iloc[-1]["date"])[:10] != today:
        return "unverified", "日线源尚无今日bar"
    today_bar = df.iloc[-1]
    note = check_price_range(sig.get("price"), today_bar["low"], today_bar["high"])
    if note:
        return "suspect", note
    if len(df) >= 2:
        prev_bar = df.iloc[-2]
        note = check_fingerprint(_load_indicators(sig),
                                 str(prev_bar["date"])[:10], float(prev_bar["close"]))
        if note:
            return "suspect", note
    return "ok", ""


async def _audit_plunge_signal(sig: dict, index_cache: dict, today: str) -> tuple[str, str]:
    sid = sig.get("signal_id") or ""
    detail = sig.get("detail") or ""
    if sid == "PLUNGE_BREADTH":
        note = check_breadth_detail(detail)
        return ("suspect", note) if note else ("ok", "")
    if sid == "PLUNGE_SPEED":
        note = check_speed_detail(detail)
        return ("suspect", note) if note else ("ok", "")
    if sid == "PLUNGE_INDEX":
        sym = parse_index_symbol(detail) or "sh000001"
        if sym not in index_cache:
            try:
                index_cache[sym] = await klines.get_index_kline(sym, days=5)
            except Exception as e:
                logger.warning(f"[eod_audit] 指数{sym}日线获取失败: {e}")
                index_cache[sym] = None
        df = index_cache[sym]
        if df is None or df.empty or str(df.iloc[-1]["date"])[:10] != today:
            return "unverified", "指数日线源尚无今日bar"
        today_bar = df.iloc[-1]
        pre_close = float(df.iloc[-2]["close"]) if len(df) >= 2 else 0.0
        note = check_index_drop_detail(detail, today_bar["high"], today_bar["low"], pre_close)
        return ("suspect", note) if note else ("ok", "")
    return "ok", ""


async def run_signal_eod_audit():
    """每交易日17:00: 复核当日全部信号, 存疑标记+推送。模型胜率重算在17:30, 错开。"""
    if not is_workday():
        return
    today = date.today().isoformat()
    sigs = await repository.get_today_signals_all()
    if not sigs:
        logger.info("[eod_audit] 今日无信号, 跳过")
        return

    kline_cache: dict = {}
    index_cache: dict = {}
    suspects = []
    n_ok = n_unverified = n_skipped = 0
    for sig in sigs:
        sid = sig.get("signal_id") or ""
        try:
            if sid.startswith("PLUNGE_"):
                status, note = await _audit_plunge_signal(sig, index_cache, today)
            elif sid.startswith(("BUY_", "SELL_")):
                status, note = await _audit_stock_signal(sig, kline_cache, today)
            else:
                n_skipped += 1
                continue
        except Exception as e:
            logger.warning(f"[eod_audit] 信号id={sig.get('id')}复核异常: {e}")
            status, note = "unverified", f"复核异常: {e}"[:255]
        if status == "suspect":
            suspects.append((sig, note))
        elif status == "ok":
            n_ok += 1
        else:
            n_unverified += 1
        try:
            await repository.set_eod_audit(sig["id"], status, note[:255])
        except Exception as e:
            logger.error(f"[eod_audit] 写复核结果失败 id={sig.get('id')}: {e}")

    logger.info(f"[eod_audit] 复核完成: 共{len(sigs)}条, 正常{n_ok}, 存疑{len(suspects)}, "
                f"无法核{n_unverified}, 不适用{n_skipped}")
    if suspects:
        await _push_suspects(suspects, today)


async def _push_suspects(suspects: list, today: str):
    """存疑信号推送: 企微文本 + 飞书原生表格卡(列宽必须百分比)。"""
    from backend.services import lark_notifier
    lines = [f"信号EOD复核: {today} 共{len(suspects)}条存疑(已标记未删除, 待人工确认)"]
    rows = []
    for sig, note in suspects[:20]:
        t = str(sig.get("triggered_at") or "")[11:16]
        lines.append(f"{t} {sig.get('name')} {sig.get('signal_name')}: {note}")
        rows.append({"time": t, "name": f"{sig.get('name')}",
                     "sig": f"{sig.get('signal_name')}", "note": note})
    if len(suspects) > 20:
        lines.append(f"... 仅列前20条, 其余见信号历史(eod_audit=suspect)")
    cols = [
        {"name": "time", "display_name": "时间", "data_type": "text", "width": "10%"},
        {"name": "name", "display_name": "名称", "data_type": "text", "width": "18%"},
        {"name": "sig", "display_name": "信号", "data_type": "text", "width": "24%"},
        {"name": "note", "display_name": "疑点", "data_type": "text", "width": "48%"},
    ]
    elements = [lark_notifier.table_element(cols, rows, page_size=10)]
    try:
        await notifier.send_dual_card(
            "\n".join(lines),
            lark_title=f"信号EOD复核: {len(suspects)}条存疑",
            elements=elements)
    except Exception as e:
        logger.error(f"[eod_audit] 存疑推送失败: {e}")
