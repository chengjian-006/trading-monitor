# -*- coding: utf-8 -*-
"""模拟账户 5 分钟组合级回放重建 (v1.7.614)。

为什么要它
──────────
模拟盘的出场逻辑从来没执行过: 卖点只对「用户本人也持有」的票下发 (scanner `_filter_valid_signals`
的 `if not is_hold and sig.direction != "buy": continue`), 而模拟盘自己买的票不在用户持仓表里
→ 模拟盘只买不卖, 仓位被亏损票占死, 收益曲线不代表模型表现。根因修复在 paper_guard.py(前向),
本模块负责把已经跑坏的那段历史按「出场规则当初就在」重放一遍, 补出应该发生的卖出。

口径 (与实盘 1:1, 不是近似)
──────────────────────────
- 买入不重新检测: 直接用 cfzy_biz_signals 里真实发生过的买点信号(触发时刻 + 触发价)重放。
  模拟盘的买入本来就是忠实执行系统信号的, 这部分没坏。重放它 → 得到「如果出场当初就在,
  这个账户会长成什么样」。当年因假性资金不足而失败的买入, 在现金被正常释放后会有一部分变成真成交,
  这正是重放的意义所在。
- 卖出逐根 5 分钟 bar 判定: 拿模拟盘自己的成本/建仓日/建仓买点, 每根 bar 跑一遍生产的
  signal_engine.detect_signals, 取其中的 sell/reduce。用 bar 收盘价判定(等价于「每5分钟看一眼现价」),
  天然防插针; 卖出顺序保持引擎返回的原始顺序 —— 即 +7%止盈(卖半) 在 跌破MA(清剩) 之前,
  与实盘「卖半→剩半破MA清」的模型行为一致。
- 成交决策全部走 paper_trader.decide() 这个生产纯函数, 不另写一套, 保证口径不漂。
- 费用按账户自己的费率(佣金/印花/过户)算, 与实盘同。
- 5分钟表是后复权、日线表是前复权 → 逐日按 backtester_5m.rescale_day_bars 重定标到前复权刻度。
- 引擎有两处读墙上时钟(末根bar是否属今日 / SELL_BREAK_MA* 的确认闸: MA5=14:30、MA10·MA20=9:26),
  回放按 bar 时刻注入 detect_signals(now=...), 否则盘后跑会让闸门恒放行、早盘就误触发破位卖出。

事件顺序
────────
每个交易日把「买点信号(按真实触发时刻)」和「每根5分钟bar的出场检查」merge 成一条时间线按序处理;
同一时刻买在卖前(保守: 买入拿不到同一瞬间卖出释放的现金)。
"""
import logging
import re
from datetime import date, datetime, timedelta

import pandas as pd

from backend.models.repo._db import _fetchall
from backend.services import paper_trader, signal_specs
from backend.services.backtester_5m import rescale_day_bars
from backend.services.signal_engine import detect_signals

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^\d{6}$")   # 个股代码; 信号表里还混着板块代码(BK0428…), 必须挡掉


# ══════════════ 数据加载 ══════════════

async def load_account(user_id: int, account_key: str) -> dict:
    rows = await _fetchall(
        "SELECT * FROM cfzy_biz_paper_account WHERE user_id=%s AND account_key=%s",
        (user_id, account_key))
    if not rows:
        raise ValueError(f"模拟账户不存在: user={user_id} key={account_key}")
    return rows[0]


async def load_buy_signals(user_id: int, start: str, end: str) -> list[dict]:
    """窗口内的买点信号(时间升序)。准入闸门与 paper_trader.on_signal 逐条对齐, 一个不能少:
    ①排除大盘/板块组 ②code 必须是6位数字 —— 信号表里混着板块代码(BK0428 等东财板块),
    实盘靠 on_signal 的 `^\\d{6}$` 挡掉, 回放漏了这道会去"买板块" ③触发价必须为正。
    """
    rows = await _fetchall(
        "SELECT code, name, signal_id, signal_name, direction, price, triggered_at "
        "FROM cfzy_biz_signals WHERE user_id=%s AND direction='buy' "
        "AND triggered_at >= %s AND triggered_at < %s ORDER BY triggered_at, id",
        (user_id, f"{start} 00:00:00", f"{end} 23:59:59"))
    out = []
    for r in rows:
        sid = str(r["signal_id"])
        code = str(r["code"])
        if signal_specs.group_of(sid) in ("regime", "sector"):
            continue
        if not _CODE_RE.match(code):
            continue
        if not r["price"] or float(r["price"]) <= 0:
            continue
        out.append({
            "code": str(r["code"]), "name": r["name"] or "",
            "signal_id": sid, "signal_name": r["signal_name"] or "",
            "price": float(r["price"]), "at": r["triggered_at"],
        })
    return out


async def load_daily(codes: list[str]) -> dict[str, pd.DataFrame]:
    """{code: 日线df(前复权, 全历史)}。列: date/open/high/low/close/volume。"""
    out: dict[str, pd.DataFrame] = {}
    for k in range(0, len(codes), 100):
        part = codes[k:k + 100]
        ph = ",".join(["%s"] * len(part))
        rows = await _fetchall(
            f"SELECT code, trade_date, open, high, low, close, volume FROM cfzy_sys_kline_cache "
            f"WHERE code IN ({ph}) ORDER BY code, trade_date", tuple(part))
        by: dict[str, list] = {}
        for r in rows:
            by.setdefault(str(r["code"]), []).append(r)
        for c, rs in by.items():
            df = pd.DataFrame(rs).rename(columns={"trade_date": "date"})
            df["date"] = df["date"].astype(str).str[:10]
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            out[c] = df.drop(columns=["code"]).sort_values("date").reset_index(drop=True)
    return out


async def load_5m(codes: list[str], start: str, end: str) -> dict[str, dict[str, list]]:
    """{code: {日期: [(分钟, high, low, close, volume, amount), ...]}} — 后复权原值, 用时再重定标。"""
    out: dict[str, dict[str, list]] = {}
    for k in range(0, len(codes), 50):
        part = codes[k:k + 50]
        ph = ",".join(["%s"] * len(part))
        rows = await _fetchall(
            f"SELECT code, dt, high, low, close, volume, amount FROM cfzy_sys_kline_5m "
            f"WHERE code IN ({ph}) AND dt >= %s AND dt < %s ORDER BY code, dt",
            (*part, f"{start} 00:00:00", f"{end} 23:59:59"))
        for r in rows:
            dt = r["dt"]
            c = str(r["code"])
            out.setdefault(c, {}).setdefault(dt.strftime("%Y-%m-%d"), []).append(
                (dt.hour * 60 + dt.minute, float(r["high"] or 0), float(r["low"] or 0),
                 float(r["close"] or 0), float(r["volume"] or 0), float(r["amount"] or 0)))
    return out


# ══════════════ 回放核心 ══════════════

class _Book:
    """回放期间的账户账本(纯内存)。字段与 cfzy_biz_paper_* 三张表一一对应, 收盘后整体落库。"""

    def __init__(self, acct: dict):
        self.acct = dict(acct)                    # decide() 读 cash/max_positions/费率等
        self.acct["cash"] = float(acct["initial_capital"])
        self.positions: dict[str, dict] = {}      # code → {qty, cost_amount, open_date, entry_signal_id, ...}
        self.trades: list[dict] = []
        self.equity: list[dict] = []
        self.processed: set[tuple] = set()        # (code, signal_id, 日期) 当日去重, 同 paper_signal_processed
        # 本轮建仓已卖过半的票 —— 喂 detect_signals(took_half=) 实现「+7% 只卖半一次」;
        # 清仓时移除 → 同股再次建仓自动重新开闸(与实盘按 entry_date 判定等价)。
        self.half_done: set[str] = set()

    @property
    def cash(self) -> float:
        return float(self.acct["cash"])

    def equity_cost(self) -> float:
        """成本口径总资产 = 现金 + Σ持仓成本 (decide 的等额轮动定仓基准)。"""
        return self.cash + sum(float(p["cost_amount"]) for p in self.positions.values())

    def apply(self, action: dict, *, code, name, signal_id, signal_name, direction, when: datetime):
        """把 decide() 的成交动作落到账本(现金/持仓/流水), 与 repo.apply_fill 同逻辑。"""
        d = when.date().isoformat()
        if action["side"] == "buy":
            self.acct["cash"] = round(self.cash - action["amount"] - action["fee"], 2)
            p = self.positions.get(code)
            if p:   # 无限子弹账户同股加仓: 累加股数/成本, 首仓信息(开仓日/入仓买点)保留
                p["qty"] += action["qty"]
                p["cost_amount"] = round(p["cost_amount"] + action["amount"] + action["fee"], 2)
            else:
                self.positions[code] = {
                    "code": code, "name": name, "qty": action["qty"],
                    "cost_amount": round(action["amount"] + action["fee"], 2),
                    "open_date": d, "entry_signal_id": signal_id, "entry_model_name": signal_name,
                }
                self.half_done.discard(code)   # 新建仓 → 止盈闸复位
        else:
            self.acct["cash"] = round(self.cash + action["amount"] - action["fee"], 2)
            p = self.positions[code]
            if signal_id == "SELL_TAKE_PROFIT":
                self.half_done.add(code)       # 本轮已卖半, 不再重复止盈
            if action.get("close_position"):
                self.positions.pop(code, None)
                self.half_done.discard(code)
            else:
                p["qty"] -= action["qty"]
                p["cost_amount"] = round(p["cost_amount"] - action["cost_basis_sold"], 2)
        self.trades.append({
            "code": code, "name": name, "side": action["side"], "qty": action["qty"],
            "price": action["price"], "amount": action["amount"], "fee": action["fee"],
            "cash_after": self.cash, "signal_id": signal_id, "signal_name": signal_name,
            "signal_direction": direction,
            "realized_pnl": action.get("realized_pnl"), "realized_pnl_pct": action.get("realized_pnl_pct"),
            "note": action.get("note", ""), "trade_date": d, "trade_time": when,
            "status": "success", "fail_reason": "",   # 列是 NOT NULL, 成功成交写空串(同 apply_fill 的 DEFAULT)
        })

    def fail(self, *, code, name, signal_id, signal_name, direction, price, reason, when: datetime):
        self.trades.append({
            "code": code, "name": name, "side": "buy", "qty": 0, "price": price,
            "amount": 0, "fee": 0, "cash_after": self.cash,
            "signal_id": signal_id, "signal_name": signal_name, "signal_direction": direction,
            "realized_pnl": None, "realized_pnl_pct": None, "note": "",
            "trade_date": when.date().isoformat(), "trade_time": when,
            "status": "failed", "fail_reason": reason,
        })


def _intraday_df(daily: pd.DataFrame, day: str, bars: list, upto_min: int) -> pd.DataFrame | None:
    """日线 df 截到 day, 并把 day 那一根改写成「截至 upto_min 时刻已知」的盘中状态。

    关键: 日线表里 day 这一根是全天 OHLC = 未来信息。直接喂给引擎, _merge_realtime_bar 里
    `high = max(全天high, 实时high)` 会把全天最高泄进来。这里把它换成游程最高/最低/累计量,
    只保留该时刻真实可知的信息。close 由调用方的 realtime 覆盖为当根 bar 收盘。
    """
    idx = daily.index[daily["date"] == day]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    sub = daily.iloc[:i + 1].copy()
    run_hi = run_lo = None
    cum_vol = 0.0
    day_open = None
    for (mn, bh, bl, bc, bv, _ba) in bars:
        if mn > upto_min:
            break
        if day_open is None:
            day_open = bc if bl <= 0 else bl   # 首根: 开盘价用不到精确值(引擎只读 close/high/low/MA)
        run_hi = bh if run_hi is None else max(run_hi, bh)
        run_lo = bl if run_lo is None else min(run_lo, bl)
        cum_vol += bv
    if run_hi is None:
        return None
    j = sub.index[-1]
    sub.loc[j, "high"] = run_hi
    sub.loc[j, "low"] = run_lo
    sub.loc[j, "volume"] = cum_vol
    if day_open is not None:
        sub.loc[j, "open"] = day_open
    return sub


async def replay(user_id: int = 1, account_key: str = "default",
                 end: str | None = None, user_config: dict | None = None) -> _Book:
    """重放一个模拟账户, 返回内存账本(不落库 — 落库由调用方决定)。"""
    acct = await load_account(user_id, account_key)
    started = acct["started_at"]
    start = (started.date() if isinstance(started, datetime) else date.today()).isoformat()
    end = end or date.today().isoformat()
    book = _Book(acct)

    sigs = await load_buy_signals(user_id, start, end)
    codes = sorted({s["code"] for s in sigs})
    logger.info(f"[replay:{account_key}] 窗口 {start}~{end}, 买点信号 {len(sigs)} 条, 涉及 {len(codes)} 只票")
    if not codes:
        return book

    daily = await load_daily(codes)
    five = await load_5m(codes, start, end)
    names = {s["code"]: s["name"] for s in sigs}

    # 交易日 = 日线表在窗口内出现过的日期(以数据为准, 不猜日历)
    tdays = sorted({d for c in codes for d in daily.get(c, pd.DataFrame({"date": []}))["date"]
                    if start <= d <= end})
    sigs_by_day: dict[str, list] = {}
    for s in sigs:
        sigs_by_day.setdefault(s["at"].date().isoformat(), []).append(s)

    for day in tdays:
        # 当日各持仓票的 5 分钟 bar(重定标到前复权刻度)
        day_bars: dict[str, list] = {}
        for c in set(list(book.positions) + [s["code"] for s in sigs_by_day.get(day, [])]):
            raw = five.get(c, {}).get(day)
            df = daily.get(c)
            if not raw or df is None:
                continue
            row = df[df["date"] == day]
            if row.empty:
                continue
            scaled, _f = rescale_day_bars(raw, float(row.iloc[0]["close"]))
            day_bars[c] = scaled

        # 事件时间线: 买点(按真实触发时刻) + 每根bar的出场检查; 同一时刻买在卖前(保守)
        events: list[tuple] = []
        for s in sigs_by_day.get(day, []):
            at = s["at"]
            events.append((at.hour * 60 + at.minute, 0, s))
        bar_mins = sorted({mn for bars in day_bars.values() for (mn, *_r) in bars})
        for mn in bar_mins:
            events.append((mn, 1, None))
        events.sort(key=lambda e: (e[0], e[1]))

        for mn, kind, payload in events:
            when = datetime.combine(date.fromisoformat(day), datetime.min.time()) \
                + timedelta(minutes=mn)
            if kind == 0:
                _do_buy(book, payload, when, day)
            else:
                await _do_exits(book, day, mn, when, day_bars, daily, names, user_config)

        _snapshot(book, day, daily)

    return book


def _do_buy(book: _Book, s: dict, when: datetime, day: str) -> None:
    code, sid = s["code"], s["signal_id"]
    key = (code, sid, day)
    if key in book.processed:
        return
    perm = paper_trader.board_permission_error(code)
    if perm:
        book.fail(code=code, name=s["name"], signal_id=sid, signal_name=s["signal_name"],
                  direction="buy", price=s["price"], reason=perm, when=when)
        book.processed.add(key)   # 无权限是终态
        return
    action = paper_trader.decide(
        book.acct, book.positions.get(code),
        {"direction": "buy", "signal_id": sid, "price": s["price"]},
        len(book.positions), book.equity_cost())
    if action["side"] == "skip":
        reason = paper_trader._SKIP_REASON_MAP.get(action["reason"], action["reason"])
        book.fail(code=code, name=s["name"], signal_id=sid, signal_name=s["signal_name"],
                  direction="buy", price=s["price"], reason=reason, when=when)
        # 资金不足/仓位已满是可重试(后续现金释放后同信号可再成交), 不锁定
        if reason not in ("资金不足", "仓位已满"):
            book.processed.add(key)
        return
    book.apply(action, code=code, name=s["name"], signal_id=sid, signal_name=s["signal_name"],
               direction="buy", when=when)
    book.processed.add(key)


async def _do_exits(book: _Book, day: str, mn: int, when: datetime,
                    day_bars: dict, daily: dict, names: dict, user_config) -> None:
    """当前 bar 对每只持仓跑一遍生产卖点检测器, 触发就在账本内成交。"""
    for code in list(book.positions):
        pos = book.positions.get(code)
        if not pos:
            continue
        bars = day_bars.get(code)
        df = daily.get(code)
        if not bars or df is None:
            continue
        bar = next((b for b in bars if b[0] == mn), None)
        if bar is None:
            continue
        price = float(bar[3])
        if price <= 0:
            continue
        sub = _intraday_df(df, day, bars, mn)
        if sub is None or len(sub) < 20:
            continue
        qty = int(pos["qty"])
        cost_ps = float(pos["cost_amount"]) / qty if qty else 0.0
        rt = {"code": code, "name": pos.get("name") or names.get(code, ""),
              "price": price, "high": float(bar[1]), "low": float(bar[2]),
              "volume": float(sub.iloc[-1]["volume"]), "amount": 0}
        try:
            sigs = detect_signals(
                sub, "short", rt, user_config,
                entry_cost=cost_ps, entry_date=pos["open_date"],
                entry_model=pos["entry_signal_id"], now=when,
                took_half=code in book.half_done)
        except Exception as e:
            logger.debug(f"[replay] {code} {day} {mn} 检测异常: {e}")
            continue
        # 顺序保持引擎原样: +7%止盈(卖半) 在 跌破MA(清剩) 之前 —— 与实盘模型行为一致
        for sig in sigs:
            if sig.direction not in ("sell", "reduce"):
                continue
            key = (code, sig.signal_id, day)
            if key in book.processed:
                continue
            book.processed.add(key)
            pos = book.positions.get(code)
            if not pos:
                break
            action = paper_trader.decide(
                book.acct, pos,
                {"direction": sig.direction, "signal_id": sig.signal_id, "price": price},
                len(book.positions), book.equity_cost())
            if action["side"] != "sell":
                continue
            book.apply(action, code=code, name=pos["name"], signal_id=sig.signal_id,
                       signal_name=sig.signal_name, direction=sig.direction, when=when)
            logger.info(f"[replay] {day} {mn // 60:02d}:{mn % 60:02d} 卖出 {pos['name']}({code}) "
                        f"{action['qty']}股 @{price:.2f} {sig.signal_id} "
                        f"盈亏{action.get('realized_pnl', 0):+.0f}")
            if not book.positions.get(code):
                break


def _snapshot(book: _Book, day: str, daily: dict) -> None:
    """收盘盯市: 持仓按当日日线收盘估值, 写一条资金曲线。"""
    mv = 0.0
    for code, p in book.positions.items():
        df = daily.get(code)
        px = 0.0
        if df is not None:
            row = df[df["date"] <= day]
            if not row.empty:
                px = float(row.iloc[-1]["close"])
        if px <= 0:
            px = float(p["cost_amount"]) / int(p["qty"]) if p["qty"] else 0
        mv += px * int(p["qty"])
    init = float(book.acct["initial_capital"]) or 1.0
    total = round(book.cash + mv, 2)
    book.equity.append({
        "snap_date": day, "cash": round(book.cash, 2), "holdings_mv": round(mv, 2),
        "total_equity": total, "total_return_pct": round((total - init) / init * 100, 3),
        "position_count": len(book.positions),
    })
