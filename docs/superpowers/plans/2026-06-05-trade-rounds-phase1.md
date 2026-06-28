# 交易回合记录 一期(回合头+回合腿+回合构建器) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把真实交割单(`cfzy_biz_trades`)按 FIFO 聚合成「开仓→清仓」交易回合并落库,完成买点归因,作为后续收益分析与环境重建的基座。

**Architecture:** 纯函数 `build_rounds_from_trades()` 把单只票的成交列表切成回合(头+腿),不碰 DB、可单测;repo 层 `trade_rounds.py` 负责「按 user+code+source 整体重建」式持久化(删后插,幂等);orchestrator `rebuild_user_rounds()` 读交割单→建回合→归因买点→落库;一个收盘后定时任务 + 导入交割单后增量触发。MFE/MAE/持有天数/环境快照属二期,本期建表预留列但留空。

**Tech Stack:** Python 3 / aiomysql 异步 / MySQL(`EmailSender` 库,`cfzy_` 前缀)/ pytest(`asyncio_mode=auto`,纯函数测试)。

**Scope 边界(本计划不含):** `cfzy_biz_trade_context`、`cfzy_biz_decisions`、虚拟回合(`source='virtual'`)、全市场日线/分时(各自独立计划)。本期 `source` 恒为 `'real'`。

---

### Task 1: 建两张新表(回合头 + 回合腿)

**Files:**
- Modify: `backend/models/database.py`(`SCHEMA_STATEMENTS` 列表内,在 `cfzy_biz_trades` 的 CREATE 块之后追加两个 CREATE 字符串)

新表用 `CREATE TABLE IF NOT EXISTS` 放进 `SCHEMA_STATEMENTS`(与现有所有表一致),不走 `MIGRATION_STATEMENTS`(那是给已存在表加列用的)。`round_legs.round_id` 用外键 `ON DELETE CASCADE`,让「删回合自动删腿」,支撑幂等重建。

- [ ] **Step 1: 在 `SCHEMA_STATEMENTS` 追加回合头表**

在 `backend/models/database.py` 中 `cfzy_biz_trades` 的 `"""..."""` CREATE 块结束后(其后紧跟的逗号之后),插入:

```python
    # 交易回合头 (v1.7.x) — 把交割单按 FIFO 聚成"开仓→清仓"一个回合, 收益分析/买点归因的基座.
    # source='real'(交割单) | 'virtual'(二期再并). MFE/MAE/holding_days/环境列二期回填, 本期留空.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_trade_rounds (
        id                  INT AUTO_INCREMENT PRIMARY KEY,
        user_id             INT NOT NULL,
        code                VARCHAR(10) NOT NULL,
        name                VARCHAR(50) NOT NULL DEFAULT '',
        source              VARCHAR(10) NOT NULL DEFAULT 'real',
        source_ref          VARCHAR(40) NOT NULL DEFAULT '',
        status              VARCHAR(10) NOT NULL DEFAULT 'open',
        open_date           DATE NOT NULL,
        open_time           VARCHAR(8) NOT NULL DEFAULT '',
        close_date          DATE DEFAULT NULL,
        close_time          VARCHAR(8) DEFAULT NULL,
        entry_price         DECIMAL(10,3) NOT NULL DEFAULT 0,
        exit_price          DECIMAL(10,3) DEFAULT NULL,
        peak_qty            INT NOT NULL DEFAULT 0,
        is_scaled_in        TINYINT NOT NULL DEFAULT 0,
        is_scaled_out       TINYINT NOT NULL DEFAULT 0,
        total_buy_amount    DECIMAL(12,2) NOT NULL DEFAULT 0,
        total_sell_amount   DECIMAL(12,2) NOT NULL DEFAULT 0,
        total_fee           DECIMAL(10,2) NOT NULL DEFAULT 0,
        realized_pnl        DECIMAL(12,2) NOT NULL DEFAULT 0,
        realized_pnl_pct    DOUBLE DEFAULT NULL,
        holding_days        INT DEFAULT NULL,
        mfe_pct             DOUBLE DEFAULT NULL,
        mfe_date            DATE DEFAULT NULL,
        mae_pct             DOUBLE DEFAULT NULL,
        mae_date            DATE DEFAULT NULL,
        max_drawdown_pct    DOUBLE DEFAULT NULL,
        entry_signal_pk     INT DEFAULT NULL,
        entry_signal_id     VARCHAR(40) DEFAULT NULL,
        entry_model_name    VARCHAR(50) DEFAULT NULL,
        entry_deviation_pct DOUBLE DEFAULT NULL,
        exit_reason         VARCHAR(40) DEFAULT NULL,
        created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE INDEX uk_round (user_id, code, source, open_date, open_time),
        INDEX idx_user_status (user_id, status),
        INDEX idx_entry_signal (entry_signal_id),
        INDEX idx_close_date (close_date)
    )
    """,
```

- [ ] **Step 2: 紧接着追加回合腿表**

在上一块之后插入:

```python
    # 交易回合腿 (v1.7.x) — 回合内每一笔买/卖动作, 真实腿 trade_id 指回交割单, 虚拟腿(二期)为 NULL.
    # round_id 外键 ON DELETE CASCADE: 重建回合时先删回合, 腿自动级联删, 保证幂等.
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_round_legs (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        round_id     INT NOT NULL,
        leg_type     VARCHAR(4) NOT NULL,
        trade_date   DATE NOT NULL,
        trade_time   VARCHAR(8) NOT NULL DEFAULT '',
        price        DECIMAL(10,3) NOT NULL,
        qty          INT NOT NULL,
        amount       DECIMAL(12,2) NOT NULL DEFAULT 0,
        fee          DECIMAL(10,2) NOT NULL DEFAULT 0,
        is_virtual   TINYINT NOT NULL DEFAULT 0,
        trade_id     INT DEFAULT NULL,
        running_qty  INT NOT NULL DEFAULT 0,
        INDEX idx_round (round_id),
        UNIQUE INDEX uk_leg_trade (trade_id),
        CONSTRAINT fk_leg_round FOREIGN KEY (round_id)
            REFERENCES cfzy_biz_trade_rounds (id) ON DELETE CASCADE
    )
    """,
```

注:`uk_leg_trade (trade_id)` 唯一索引允许多个 NULL(MySQL 唯一索引不约束 NULL),真实腿防重、虚拟腿不受限。

- [ ] **Step 3: 手工核对建表语句无语法错**

Run: `python -c "import ast,sys; ast.parse(open('backend/models/database.py',encoding='utf-8').read()); print('OK')"`
Expected: 输出 `OK`(Python 文件可解析,字符串拼接无误)。

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py
git commit -m "feat(rounds): add cfzy_biz_trade_rounds + cfzy_biz_round_legs schema"
```

---

### Task 2: 纯函数 FIFO 回合切分(核心,TDD)

**Files:**
- Create: `backend/services/trade_round_builder.py`
- Test: `backend/tests/test_trade_round_builder.py`

纯函数:输入单只票按时间升序的成交列表,输出回合列表(每个回合含 legs)。不碰 DB。FIFO 把卖出匹配最早买入;持仓归零即闭合回合;剩余持仓为 open 回合。`realized_pnl` 按 FIFO 已匹配成本算(对 open 回合的部分卖出也正确)。MFE/MAE/holding_days 不在此函数(二期)。

输入成交 dict 形状(来自 `cfzy_biz_trades` 行):
`{"id": int, "trade_date": "YYYY-MM-DD", "trade_time": "HH:MM:SS", "code": str, "name": str, "direction": "buy"|"sell", "quantity": int, "price": float, "amount": float, "fee_total": float}`
(`fee_total` = fee+stamp_tax+transfer_fee,由调用方在 Task 4 预合并。)

输出回合 dict 形状:
`{"code","name","source":"real","status":"open"|"closed","open_date","open_time","close_date","close_time","entry_price","exit_price","peak_qty","is_scaled_in","is_scaled_out","total_buy_amount","total_sell_amount","total_fee","realized_pnl","realized_pnl_pct","legs":[...]}`
leg 形状:`{"leg_type":"buy"|"sell","trade_date","trade_time","price","qty","amount","fee","trade_id","running_qty"}`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_trade_round_builder.py`:

```python
"""FIFO 回合切分纯函数测试 — build_rounds_from_trades 的边界行为."""
from backend.services.trade_round_builder import build_rounds_from_trades


def _t(tid, d, tm, direction, qty, price, fee=0.0):
    return {
        "id": tid, "trade_date": d, "trade_time": tm, "code": "600000",
        "name": "浦发银行", "direction": direction, "quantity": qty,
        "price": price, "amount": round(qty * price, 2), "fee_total": fee,
    }


class TestBuildRounds:
    def test_empty_returns_empty(self):
        assert build_rounds_from_trades([]) == []

    def test_single_buy_is_open_round(self):
        rounds = build_rounds_from_trades([_t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0)])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "open"
        assert r["open_date"] == "2026-01-05"
        assert r["entry_price"] == 10.0
        assert r["exit_price"] is None
        assert r["peak_qty"] == 1000
        assert r["close_date"] is None
        assert len(r["legs"]) == 1
        assert r["legs"][0]["running_qty"] == 1000

    def test_buy_then_full_sell_is_closed_round(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0, fee=5.0),
            _t(2, "2026-01-08", "14:00:00", "sell", 1000, 11.0, fee=6.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "closed"
        assert r["close_date"] == "2026-01-08"
        assert r["exit_price"] == 11.0
        # 已实现 = 卖额(11000) - 买额(10000) - 费(11) = 989
        assert r["realized_pnl"] == 989.0
        assert round(r["realized_pnl_pct"], 4) == round(989.0 / 10000 * 100, 4)
        assert r["peak_qty"] == 1000

    def test_two_independent_rounds(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-06", "14:00:00", "sell", 1000, 11.0),
            _t(3, "2026-01-09", "10:00:00", "buy", 500, 12.0),
        ])
        assert len(rounds) == 2
        assert rounds[0]["status"] == "closed"
        assert rounds[1]["status"] == "open"
        assert rounds[1]["open_date"] == "2026-01-09"

    def test_scaled_in_and_out_flags_and_avg_price(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-06", "09:31:00", "buy", 1000, 12.0),
            _t(3, "2026-01-08", "14:00:00", "sell", 500, 13.0),
            _t(4, "2026-01-09", "14:00:00", "sell", 1500, 13.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "closed"
        assert r["is_scaled_in"] is True
        assert r["is_scaled_out"] is True
        assert r["entry_price"] == 11.0          # (10*1000+12*1000)/2000
        assert r["peak_qty"] == 2000
        assert r["legs"][2]["running_qty"] == 1500   # 第3腿卖500后剩1500

    def test_partial_sell_open_round_realized_only_on_sold(self):
        rounds = build_rounds_from_trades([
            _t(1, "2026-01-05", "09:31:00", "buy", 1000, 10.0),
            _t(2, "2026-01-08", "14:00:00", "sell", 400, 11.0),
        ])
        assert len(rounds) == 1
        r = rounds[0]
        assert r["status"] == "open"
        # 卖400股, FIFO成本 400*10=4000, 卖额 400*11=4400 → 已实现 400
        assert r["realized_pnl"] == 400.0
        assert r["legs"][1]["running_qty"] == 600
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_trade_round_builder.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'backend.services.trade_round_builder'`

- [ ] **Step 3: 实现纯函数**

`backend/services/trade_round_builder.py`:

```python
"""交易回合 FIFO 切分纯函数 — cfzy_biz_trades 行 → 回合(头+腿)列表.

单只票按时间升序的成交流, 切成"开仓(持仓0→正)→清仓(持仓回0)"一个回合.
卖出按 FIFO 匹配最早买入批次, realized_pnl 只算已匹配卖出部分(open 回合的部分卖出也正确).
MFE/MAE/holding_days/环境 不在此处, 由二期从 K线缓存回填.
"""


def build_rounds_from_trades(trades: list[dict]) -> list[dict]:
    """trades: 单只票按 (trade_date, trade_time) 升序的成交 dict 列表.

    每个 dict 需含: id, trade_date, trade_time, code, name, direction('buy'/'sell'),
    quantity, price, amount, fee_total. 返回回合 dict 列表(见模块文档).
    """
    rounds: list[dict] = []
    cur: dict | None = None          # 当前 open 回合
    buy_lots: list[list] = []        # FIFO 队列, 每项 [price, remaining_qty]
    position = 0                     # 当前持仓股数

    for t in trades:
        qty = int(t["quantity"])
        price = float(t["price"])
        fee = float(t.get("fee_total", 0) or 0)
        amount = float(t["amount"])
        direction = t["direction"]

        if cur is None:
            if direction != "buy":
                continue  # 无持仓时的卖出(脏数据)忽略
            cur = _new_round(t)

        leg = {
            "leg_type": direction, "trade_date": t["trade_date"],
            "trade_time": t.get("trade_time") or "", "price": price,
            "qty": qty, "amount": round(amount, 2), "fee": round(fee, 2),
            "trade_id": t.get("id"), "running_qty": 0,
        }
        cur["total_fee"] += fee

        if direction == "buy":
            buy_lots.append([price, qty])
            position += qty
            cur["total_buy_amount"] += amount
            cur["_buy_qty"] += qty
            cur["_buy_cost"] += amount
            cur["_buy_legs"] += 1
        else:  # sell
            sell_qty = qty
            matched_cost = 0.0
            while sell_qty > 0 and buy_lots:
                lot = buy_lots[0]
                m = min(sell_qty, lot[1])
                matched_cost += lot[0] * m
                lot[1] -= m
                sell_qty -= m
                if lot[1] <= 0:
                    buy_lots.pop(0)
            position -= (qty - sell_qty)        # 只扣实际匹配掉的
            cur["total_sell_amount"] += amount
            cur["_sell_qty"] += qty
            cur["_sell_amount"] += amount
            cur["_sell_legs"] += 1
            cur["realized_pnl"] += amount - matched_cost  # 费在收尾统一扣

        leg["running_qty"] = position
        cur["peak_qty"] = max(cur["peak_qty"], position)
        cur["legs"].append(leg)

        if position == 0:
            _close_round(cur, t)
            rounds.append(cur)
            cur, buy_lots = None, []

    if cur is not None:
        _finalize_open(cur)
        rounds.append(cur)
    return rounds


def _new_round(t: dict) -> dict:
    return {
        "code": t["code"], "name": t.get("name") or "", "source": "real",
        "status": "open", "open_date": t["trade_date"],
        "open_time": t.get("trade_time") or "", "close_date": None,
        "close_time": None, "entry_price": 0.0, "exit_price": None,
        "peak_qty": 0, "is_scaled_in": False, "is_scaled_out": False,
        "total_buy_amount": 0.0, "total_sell_amount": 0.0, "total_fee": 0.0,
        "realized_pnl": 0.0, "realized_pnl_pct": None, "legs": [],
        "_buy_qty": 0, "_buy_cost": 0.0, "_buy_legs": 0,
        "_sell_qty": 0, "_sell_amount": 0.0, "_sell_legs": 0,
    }


def _common_finalize(r: dict):
    r["entry_price"] = round(r["_buy_cost"] / r["_buy_qty"], 3) if r["_buy_qty"] else 0.0
    r["exit_price"] = round(r["_sell_amount"] / r["_sell_qty"], 3) if r["_sell_qty"] else None
    r["is_scaled_in"] = r["_buy_legs"] > 1
    r["is_scaled_out"] = r["_sell_legs"] > 1
    r["realized_pnl"] = round(r["realized_pnl"] - r["total_fee"], 2)
    r["total_buy_amount"] = round(r["total_buy_amount"], 2)
    r["total_sell_amount"] = round(r["total_sell_amount"], 2)
    r["total_fee"] = round(r["total_fee"], 2)
    base = r["_buy_cost"]
    r["realized_pnl_pct"] = round(r["realized_pnl"] / base * 100, 4) if base else None
    for k in ("_buy_qty", "_buy_cost", "_buy_legs", "_sell_qty", "_sell_amount", "_sell_legs"):
        r.pop(k, None)


def _close_round(r: dict, last_trade: dict):
    r["status"] = "closed"
    r["close_date"] = last_trade["trade_date"]
    r["close_time"] = last_trade.get("trade_time") or ""
    _common_finalize(r)


def _finalize_open(r: dict):
    r["status"] = "open"
    _common_finalize(r)
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest backend/tests/test_trade_round_builder.py -v`
Expected: PASS(全部 6 个用例)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/trade_round_builder.py backend/tests/test_trade_round_builder.py
git commit -m "feat(rounds): FIFO round-segmentation pure function with tests"
```

---

### Task 3: 买点归因纯函数(回合 ↔ 信号,TDD)

**Files:**
- Modify: `backend/services/trade_round_builder.py`(追加 `attach_entry_signal`)
- Test: `backend/tests/test_trade_round_builder.py`(追加测试类)

把单个回合的 `open_date` 与该票买点信号列表就近匹配(±N 天,同距离优先买入日及之前),沿用 `holdings.get_holdings_entry_model` 的匹配口径,但抽成纯函数便于测。命中则写 `entry_signal_pk/entry_signal_id/entry_model_name/entry_deviation_pct`。

信号 dict 形状(调用方查 `cfzy_biz_signals` 得到):
`{"id": int, "signal_id": str, "signal_name": str, "price": float|None, "date": "YYYY-MM-DD"}`

- [ ] **Step 1: 写失败测试(追加到测试文件末尾)**

```python
from backend.services.trade_round_builder import attach_entry_signal


def _sig(sid, name, d, price=10.0, pk=1):
    return {"id": pk, "signal_id": sid, "signal_name": name, "price": price, "date": d}


class TestAttachEntrySignal:
    def _round(self, open_date="2026-01-05", entry_price=10.5):
        return {"open_date": open_date, "entry_price": entry_price,
                "entry_signal_pk": None, "entry_signal_id": None,
                "entry_model_name": None, "entry_deviation_pct": None}

    def test_no_signals_leaves_none(self):
        r = self._round()
        attach_entry_signal(r, [])
        assert r["entry_signal_id"] is None

    def test_matches_nearest_within_window(self):
        r = self._round(open_date="2026-01-05", entry_price=10.5)
        attach_entry_signal(r, [
            _sig("BUY_WEAK_EXTREME", "弱势极限", "2026-01-03", price=10.0, pk=7),
            _sig("BUY_RALLY_MA20", "回踩20MA", "2026-01-20", price=9.0, pk=9),
        ])
        assert r["entry_signal_pk"] == 7
        assert r["entry_signal_id"] == "BUY_WEAK_EXTREME"
        assert r["entry_model_name"] == "弱势极限"
        # 偏离 = (10.5-10.0)/10.0*100 = +5.0
        assert round(r["entry_deviation_pct"], 2) == 5.0

    def test_outside_window_no_match(self):
        r = self._round(open_date="2026-01-05")
        attach_entry_signal(r, [_sig("BUY_WEAK_EXTREME", "弱势极限", "2025-12-01")],
                            window_days=7)
        assert r["entry_signal_id"] is None

    def test_tie_prefers_on_or_before_open(self):
        r = self._round(open_date="2026-01-10")
        attach_entry_signal(r, [
            _sig("BUY_A", "A", "2026-01-08", pk=1),   # 距2天, 之前
            _sig("BUY_B", "B", "2026-01-12", pk=2),   # 距2天, 之后
        ])
        assert r["entry_signal_pk"] == 1
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_trade_round_builder.py::TestAttachEntrySignal -v`
Expected: FAIL — `ImportError: cannot import name 'attach_entry_signal'`

- [ ] **Step 3: 实现(追加到 `trade_round_builder.py` 末尾)**

```python
from datetime import date, timedelta


def _as_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def attach_entry_signal(round_obj: dict, signals: list[dict], window_days: int = 7):
    """把回合 open_date 就近匹配到该票买点信号(±window_days, 同距离优先买入日及之前).

    signals: 同 code 的买点信号 dict 列表, 每项 {id, signal_id, signal_name, price, date}.
    命中则就地写 entry_signal_pk/entry_signal_id/entry_model_name/entry_deviation_pct.
    """
    buy_d = _as_date(round_obj.get("open_date"))
    if buy_d is None or not signals:
        return
    lo, hi = buy_d - timedelta(days=window_days), buy_d + timedelta(days=window_days)
    best = None  # (排序键, 信号)
    for s in signals:
        sd = _as_date(s.get("date"))
        if sd is None or not (lo <= sd <= hi):
            continue
        key = (abs((sd - buy_d).days), 0 if sd <= buy_d else 1)
        if best is None or key < best[0]:
            best = (key, s)
    if best is None:
        return
    s = best[1]
    round_obj["entry_signal_pk"] = s.get("id")
    round_obj["entry_signal_id"] = s.get("signal_id")
    round_obj["entry_model_name"] = s.get("signal_name")
    sp = s.get("price")
    ep = round_obj.get("entry_price")
    if sp and ep:
        round_obj["entry_deviation_pct"] = round((float(ep) - float(sp)) / float(sp) * 100, 4)
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest backend/tests/test_trade_round_builder.py -v`
Expected: PASS(原 6 + 新 4 = 10 个用例)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/trade_round_builder.py backend/tests/test_trade_round_builder.py
git commit -m "feat(rounds): entry-signal attribution pure function with tests"
```

---

### Task 4: repo 持久化层(整体重建式,幂等)

**Files:**
- Create: `backend/models/repo/trade_rounds.py`

`replace_rounds_for_code` 在一个连接里:先 `DELETE` 该 (user,code,source) 的全部回合(腿经外键级联自动删),再逐回合 `INSERT` 头取 `lastrowid`、`executemany` 插腿。配 `get_trades_for_rounds`(供 orchestrator 取成交,带 `fee_total` 合并 + id)、`get_buy_signals_by_code`(供归因)、`get_rounds`(读)。

- [ ] **Step 1: 写 repo 模块**

`backend/models/repo/trade_rounds.py`:

```python
"""交易回合持久化 — cfzy_biz_trade_rounds + cfzy_biz_round_legs.

幂等策略: 按 (user_id, code, source) 整体重建(删后插); 回合腿经外键 ON DELETE CASCADE 自动清理.
"""
import aiomysql

from backend.models.database import get_pool
from backend.models.repo._db import _fetchall

_ROUND_COLS = (
    "user_id, code, name, source, source_ref, status, open_date, open_time, "
    "close_date, close_time, entry_price, exit_price, peak_qty, is_scaled_in, "
    "is_scaled_out, total_buy_amount, total_sell_amount, total_fee, realized_pnl, "
    "realized_pnl_pct, entry_signal_pk, entry_signal_id, entry_model_name, "
    "entry_deviation_pct, exit_reason"
)
_ROUND_PH = ",".join(["%s"] * 25)


async def get_trades_for_rounds(user_id: int) -> list[dict]:
    """取用户全量成交, 合并三项费用为 fee_total, 按 code+时间升序(供回合构建器)."""
    return await _fetchall(
        "SELECT id, trade_date, trade_time, code, name, direction, quantity, price, "
        "amount, (COALESCE(fee,0)+COALESCE(stamp_tax,0)+COALESCE(transfer_fee,0)) AS fee_total "
        "FROM cfzy_biz_trades WHERE user_id = %s ORDER BY code, trade_date, trade_time",
        (user_id,),
    )


async def get_buy_signals_by_code(user_id: int, code: str) -> list[dict]:
    """取该票全部买点信号(BUY_ 前缀), 供回合买点归因."""
    return await _fetchall(
        "SELECT id, signal_id, signal_name, price, DATE(triggered_at) AS date "
        "FROM cfzy_biz_signals WHERE user_id = %s AND code = %s "
        "AND signal_id LIKE 'BUY\\_%%' ORDER BY triggered_at ASC",
        (user_id, code),
    )


def _round_row(user_id: int, r: dict) -> tuple:
    return (
        user_id, r["code"], r["name"], r["source"], r.get("source_ref", ""),
        r["status"], r["open_date"], r["open_time"], r["close_date"], r["close_time"],
        r["entry_price"], r["exit_price"], r["peak_qty"], int(r["is_scaled_in"]),
        int(r["is_scaled_out"]), r["total_buy_amount"], r["total_sell_amount"],
        r["total_fee"], r["realized_pnl"], r["realized_pnl_pct"],
        r.get("entry_signal_pk"), r.get("entry_signal_id"), r.get("entry_model_name"),
        r.get("entry_deviation_pct"), r.get("exit_reason"),
    )


async def replace_rounds_for_code(user_id: int, code: str, source: str, rounds: list[dict]):
    """删除该 (user,code,source) 全部回合后重插. rounds 含 legs."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM cfzy_biz_trade_rounds WHERE user_id=%s AND code=%s AND source=%s",
                (user_id, code, source),
            )
            for r in rounds:
                await cur.execute(
                    f"INSERT INTO cfzy_biz_trade_rounds ({_ROUND_COLS}) VALUES ({_ROUND_PH})",
                    _round_row(user_id, r),
                )
                round_id = cur.lastrowid
                legs = r.get("legs") or []
                if legs:
                    await cur.executemany(
                        "INSERT INTO cfzy_biz_round_legs "
                        "(round_id, leg_type, trade_date, trade_time, price, qty, amount, "
                        "fee, is_virtual, trade_id, running_qty) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(round_id, lg["leg_type"], lg["trade_date"], lg["trade_time"],
                          lg["price"], lg["qty"], lg["amount"], lg["fee"],
                          int(lg.get("is_virtual", 0)), lg.get("trade_id"), lg["running_qty"])
                         for lg in legs],
                    )
        await conn.commit()


async def get_rounds(user_id: int, status: str | None = None) -> list[dict]:
    """读回合头列表(供前端/分析), 可按 status 过滤."""
    sql = "SELECT * FROM cfzy_biz_trade_rounds WHERE user_id = %s"
    args: list = [user_id]
    if status:
        sql += " AND status = %s"
        args.append(status)
    sql += " ORDER BY open_date DESC, open_time DESC"
    return await _fetchall(sql, tuple(args))
```

注:`pool.acquire()` 默认 `autocommit=False`,故显式 `await conn.commit()`;`DELETE`+`INSERT` 在同一连接同一事务,保证重建原子性。

- [ ] **Step 2: 语法自检**

Run: `python -c "import ast; ast.parse(open('backend/models/repo/trade_rounds.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/models/repo/trade_rounds.py
git commit -m "feat(rounds): persistence repo with idempotent rebuild-by-code"
```

---

### Task 5: orchestrator(读成交→建回合→归因→落库)

**Files:**
- Modify: `backend/services/trade_round_builder.py`(追加 `rebuild_user_rounds` 协程)
- Test: `backend/tests/test_trade_round_builder.py`(追加分组纯逻辑测试)

`rebuild_user_rounds(user_id)`:取全量成交→按 code 分组→每组 `build_rounds_from_trades`→对每个回合 `attach_entry_signal`(查该票买点信号)→`replace_rounds_for_code`。把「按 code 分组」抽成纯函数 `group_trades_by_code` 单测,DB 调用部分不单测(项目惯例:纯逻辑测,IO 不测)。

- [ ] **Step 1: 写失败测试(追加)**

```python
from backend.services.trade_round_builder import group_trades_by_code


class TestGroupByCode:
    def test_groups_preserve_order(self):
        trades = [
            {"code": "600000", "trade_date": "2026-01-05", "trade_time": "09:31:00"},
            {"code": "000001", "trade_date": "2026-01-05", "trade_time": "09:32:00"},
            {"code": "600000", "trade_date": "2026-01-06", "trade_time": "09:31:00"},
        ]
        grouped = group_trades_by_code(trades)
        assert set(grouped.keys()) == {"600000", "000001"}
        assert len(grouped["600000"]) == 2
        assert grouped["600000"][0]["trade_date"] == "2026-01-05"
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_trade_round_builder.py::TestGroupByCode -v`
Expected: FAIL — `ImportError: cannot import name 'group_trades_by_code'`

- [ ] **Step 3: 实现(追加到 `trade_round_builder.py`)**

```python
from collections import defaultdict

from backend.models.repo.trade_rounds import (
    get_trades_for_rounds, get_buy_signals_by_code, replace_rounds_for_code,
)


def group_trades_by_code(trades: list[dict]) -> dict[str, list[dict]]:
    """把成交按 code 分组, 保持各组内原有(已按时间升序)顺序."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        grouped[t["code"]].append(t)
    return dict(grouped)


async def rebuild_user_rounds(user_id: int) -> int:
    """全量重建某用户的真实交易回合. 返回写入回合数."""
    trades = await get_trades_for_rounds(user_id)
    grouped = group_trades_by_code(trades)
    total = 0
    for code, code_trades in grouped.items():
        rounds = build_rounds_from_trades(code_trades)
        if not rounds:
            continue
        signals = await get_buy_signals_by_code(user_id, code)
        for r in rounds:
            attach_entry_signal(r, signals)
        await replace_rounds_for_code(user_id, code, "real", rounds)
        total += len(rounds)
    return total
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest backend/tests/test_trade_round_builder.py -v`
Expected: PASS(11 个用例)。

- [ ] **Step 5: Commit**

```bash
git add backend/services/trade_round_builder.py backend/tests/test_trade_round_builder.py
git commit -m "feat(rounds): orchestrator rebuild_user_rounds + group-by-code"
```

---

### Task 6: 定时任务 + 导入后触发接线

**Files:**
- Modify: `backend/models/database.py`(`migration_tasks` 列表追加一条种子任务)
- Modify: 调度 handler 注册处(与现有 handler 同文件;实现时用 `grep -rn "backfill_signal_outcomes" backend/` 定位 handler 注册映射,把新 handler 名 `rebuild_trade_rounds` 注册到同一映射)
- Modify: 交割单导入端点(用 `grep -rn "save_trade_records" backend/` 定位导入路由,导入成功后调用 `rebuild_user_rounds(user_id)`)

- [ ] **Step 1: 注册定时任务种子**

在 `backend/models/database.py` 的 `migration_tasks` 列表(约 line 512 起)中追加一条:

```python
            # v1.7.x: 交易回合重建 — 收盘后按 FIFO 把交割单聚成回合(头+腿)并归因买点, 供收益分析
            ("rebuild_trade_rounds", "交易回合重建·15:20",
             "收盘后把各用户交割单按 FIFO 聚成开→平交易回合(cfzy_biz_trade_rounds/round_legs)并就近归因买点信号",
             "cron", _json.dumps({"hour": 15, "minute": 20}), "rebuild_trade_rounds"),
```

- [ ] **Step 2: 定位并接入调度 handler 映射**

Run: `grep -rn "snapshot_signal_perf" backend/ --include=*.py`
找到把 `handler` 字符串映射到协程的地方(调度器分发表)。仿照现有项,新增:

```python
    "rebuild_trade_rounds": _make_all_users_handler(rebuild_user_rounds),
```

若调度器是「单用户循环」模式(参照 `backfill_signal_outcomes` 等任务如何遍历用户),则按同款写法包一层遍历所有 user_id 调用 `rebuild_user_rounds`;`rebuild_user_rounds` 已是按 user 的协程,直接复用现有「遍历用户」工具函数即可。导入 `from backend.services.trade_round_builder import rebuild_user_rounds`。

- [ ] **Step 3: 导入交割单后增量触发**

Run: `grep -rn "save_trade_records" backend/ --include=*.py`
在导入路由「保存成功」之后追加(同一 `user_id` 作用域):

```python
    from backend.services.trade_round_builder import rebuild_user_rounds
    try:
        await rebuild_user_rounds(user_id)
    except Exception as e:
        logger.warning(f"[rounds] 导入后重建交易回合失败 user={user_id}: {e}")
```

- [ ] **Step 4: 冒烟验证(导入流程跑通且回合落库)**

手动触发一次导入(或调用 `rebuild_user_rounds(1)`),然后:

Run: `python -c "import asyncio; from backend.models.database import init_db; from backend.models.repo.trade_rounds import get_rounds; asyncio.run(init_db()); print(asyncio.run(get_rounds(1))[:2])"`
Expected: 打印用户1的若干回合行(`status`/`entry_price`/`entry_signal_id` 等有值),无异常。
(若本地不便连库,跳过此步,在云端部署后用同一查询验证。)

- [ ] **Step 5: Commit**

```bash
git add backend/models/database.py backend/<调度handler文件> backend/<导入路由文件>
git commit -m "feat(rounds): schedule daily rebuild + trigger after trade import"
```

---

### Task 7: changelog 版本记录(项目规范)

**Files:**
- Modify: `frontend/src/data/changelog.ts`(数组头部加一条)

- [ ] **Step 1: 在 changelog 数组头部插入新版本**

`frontend/src/data/changelog.ts` 中 `const changelog: VersionEntry[] = [` 之后、`v1.7.307` 块之前插入(版本号取当前最新 +1,实现时确认):

```typescript
  {
    version: 'v1.7.308',
    date: '2026-06-05',
    title: '新增「交易回合」记录: 交割单按FIFO聚成开→平回合并归因买点',
    changes: [
      { text: '新增 cfzy_biz_trade_rounds / cfzy_biz_round_legs 两张表: 把真实交割单按FIFO切成"开仓→清仓"交易回合(含分批加减仓、峰值持仓、已实现盈亏), 并把每个回合就近(±7天)归因到买点信号, 记录我的买价相对信号触发价的偏离。收盘后15:20定时重建+导入交割单后增量重建。MFE/MAE/持有天数/环境快照属二期。为后续收益分析与回测打底', tag: 'new' },
    ],
  },
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/data/changelog.ts
git commit -m "docs(changelog): v1.7.308 交易回合一期"
```

---

## Self-Review

**Spec coverage(对照 spec §4.1/§4.2/§5):**
- §4.1 回合头表 → Task 1 Step 1(列与索引齐全;MFE/MAE/holding_days/环境列建表预留、二期回填,已在计划开头 Scope 注明)。
- §4.2 回合腿表 → Task 1 Step 2。
- §5 回合构建器(FIFO/归因/幂等/触发)→ Task 2(FIFO)+ Task 3(归因)+ Task 4(幂等删后插)+ Task 5(orchestrator)+ Task 6(定时+导入触发)。
- §7 与 holdings FIFO 衔接 → Task 3 复用其 `entry_model` 匹配口径;holdings 运行时计算保留作校验基准(本期不改 holdings)。
- §10 配套(SCHEMA_STATEMENTS 建表 / scheduled_tasks 种子 / changelog)→ Task 1 / Task 6 / Task 7。
- 二期(`trade_context`)、三期(`decisions`)、虚拟回合、全市场数据 → 明确不在本计划,另起计划。无遗漏。

**Placeholder 扫描:** 无 TBD/TODO;唯二需实现时定位的是「调度 handler 注册文件」与「导入路由文件」,已给出精确 `grep` 定位命令与要插入的完整代码,非占位。

**类型一致性:** 成交 dict 用 `fee_total`(Task 4 SQL 现算 + Task 2 函数消费,一致);回合 dict 字段(`entry_price`/`peak_qty`/`is_scaled_in`/`realized_pnl_pct`/`legs[].running_qty` 等)在 Task 2 产出、Task 4 `_round_row`/leg 插入消费,逐字段对齐;`attach_entry_signal` 写入的 `entry_signal_pk/entry_signal_id/entry_model_name/entry_deviation_pct` 与表列、`_round_row` 一致。
