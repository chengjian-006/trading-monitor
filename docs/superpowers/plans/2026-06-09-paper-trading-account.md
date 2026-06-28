# 模拟账户(纸面交易) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在系统内建模拟账户, 自选池触发个股买卖点即按触发价模拟成交(等额轮动+A股真实费用+实时执行), 用资金曲线/胜率验证严格执行模型是否持续正反馈。

**Architecture:** 独立 `cfzy_biz_paper_*` 4 表(账户/持仓/流水/资金曲线), 不污染实盘回合表。核心成交决策是纯函数 `paper_trader.decide()`(可单测), DB 读写为薄封装。执行器 `on_signal()` 挂在 `scanner.py` 单信号 emit 处(save_signal 之后), 仅生产环境跑。收盘盯市任务画资金曲线。FastAPI 路由 + Vue 页面展示。

**Tech Stack:** Python 3.13 / FastAPI / aiomysql / APScheduler; Vue 3 + TS + Naive UI + Pinia; pytest。

参考设计: `docs/superpowers/specs/2026-06-09-paper-trading-account-design.md`

---

## File Structure

- Create `backend/models/repo/paper_trading.py` — 账户/持仓/流水/曲线 CRUD + 成交事务 + 统计聚合。
- Create `backend/services/paper_trader.py` — 纯函数 `decide()`/费用计算 + `on_signal()` DB 封装。
- Create `backend/services/paper_equity.py` — 收盘盯市快照任务。
- Create `backend/routers/paper_trading.py` — `/api/paper-trading/*` API。
- Create `backend/tests/test_paper_trader.py` — 纯函数单测。
- Modify `backend/models/database.py` — 4 张表 SCHEMA + 盯市 scheduled_task seed。
- Modify `backend/models/repository.py` — re-export paper_trading repo 函数(项目惯例: routers/services 通过 repository 访问)。
- Modify `backend/services/task_registry.py` — 注册 `snapshot_paper_equity` handler。
- Modify `backend/services/scanner.py` — save_signal 后挂 `paper_trader.on_signal(...)`。
- Modify `backend/main.py` — include paper_trading router。
- Create `frontend/src/api/paper-trading.ts`, `frontend/src/stores/paper-trading.ts`, `frontend/src/views/PaperTradingView.vue`。
- Modify `frontend/src/router/index.ts`, `frontend/src/components/layout/AppSidebar.vue`, `frontend/src/data/changelog.ts`。

---

## Task 1: 数据库 4 表 + 盯市任务 seed

**Files:**
- Modify: `backend/models/database.py` (SCHEMA_STATEMENTS 列表 + scheduled_tasks seed)

- [ ] **Step 1: 在 SCHEMA_STATEMENTS 末尾追加 4 张表**

在 `backend/models/database.py` 的 `SCHEMA_STATEMENTS` 列表里(其它 `cfzy_biz_*` CREATE TABLE 同处)追加:

```python
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_account (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL DEFAULT 1,
        name VARCHAR(64) NOT NULL DEFAULT '模拟账户',
        initial_capital DECIMAL(14,2) NOT NULL DEFAULT 1000000.00,
        cash DECIMAL(14,2) NOT NULL DEFAULT 1000000.00,
        max_positions INT NOT NULL DEFAULT 10,
        commission_rate DECIMAL(7,6) NOT NULL DEFAULT 0.000250,
        min_commission DECIMAL(6,2) NOT NULL DEFAULT 5.00,
        stamp_rate DECIMAL(7,6) NOT NULL DEFAULT 0.001000,
        transfer_rate DECIMAL(7,6) NOT NULL DEFAULT 0.000010,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        reset_at DATETIME NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_paper_account_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_position (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        user_id INT NOT NULL DEFAULT 1,
        code VARCHAR(8) NOT NULL,
        name VARCHAR(32) NOT NULL DEFAULT '',
        qty INT NOT NULL,
        cost_amount DECIMAL(14,2) NOT NULL,
        open_date DATE NOT NULL,
        open_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        entry_signal_id VARCHAR(48) NOT NULL DEFAULT '',
        entry_model_name VARCHAR(48) NOT NULL DEFAULT '',
        UNIQUE KEY uk_paper_pos (account_id, code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_trade (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        user_id INT NOT NULL DEFAULT 1,
        code VARCHAR(8) NOT NULL,
        name VARCHAR(32) NOT NULL DEFAULT '',
        side ENUM('buy','sell') NOT NULL,
        qty INT NOT NULL,
        price DECIMAL(10,3) NOT NULL,
        amount DECIMAL(14,2) NOT NULL,
        fee DECIMAL(10,2) NOT NULL,
        cash_after DECIMAL(14,2) NOT NULL,
        signal_id VARCHAR(48) NOT NULL DEFAULT '',
        signal_name VARCHAR(64) NOT NULL DEFAULT '',
        signal_direction VARCHAR(12) NOT NULL DEFAULT '',
        realized_pnl DECIMAL(14,2) NULL,
        realized_pnl_pct DECIMAL(8,3) NULL,
        note VARCHAR(64) NOT NULL DEFAULT '',
        trade_date DATE NOT NULL,
        trade_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        KEY idx_paper_trade_acct_date (account_id, trade_date),
        KEY idx_paper_trade_sig (account_id, code, signal_id, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS cfzy_biz_paper_equity (
        id INT AUTO_INCREMENT PRIMARY KEY,
        account_id INT NOT NULL,
        user_id INT NOT NULL DEFAULT 1,
        snap_date DATE NOT NULL,
        cash DECIMAL(14,2) NOT NULL,
        holdings_mv DECIMAL(14,2) NOT NULL,
        total_equity DECIMAL(14,2) NOT NULL,
        total_return_pct DECIMAL(8,3) NOT NULL,
        position_count INT NOT NULL DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_paper_equity (account_id, snap_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
```

- [ ] **Step 2: seed 盯市定时任务**

在 `database.py` 里 seed scheduled_tasks 的列表(其它 `("job_id", "名称", ...)` 元组同处, 形如 line 957-961 的 cron 任务)追加一条:

```python
            ("paper_equity_snapshot", "模拟账户收盘盯市", "每交易日15:05对模拟持仓盯市并写资金曲线",
             "cron", {"hour": 15, "minute": 5}, "snapshot_paper_equity"),
```
(若该 seed 区用 `_json.dumps(...)` 包 config, 比照同区其它 cron 任务的写法对齐。)

- [ ] **Step 3: 重启后端建表 + 核对**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -c "import backend.main"`
Expected: 无导入错误。建表在应用启动 `init_db()` 时执行; 本地若连库可用 `SHOW TABLES LIKE 'cfzy_biz_paper_%'` 核对 4 张表。

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py
git commit -m "feat(paper): 模拟账户4张表 + 收盘盯市任务seed"
```

---

## Task 2: repo CRUD `paper_trading.py`

**Files:**
- Create: `backend/models/repo/paper_trading.py`
- Modify: `backend/models/repository.py` (re-export)

- [ ] **Step 1: 写 repo 模块**

参照同目录其它 repo(如 `repo/trade_rounds.py`) 的连接池用法 `from backend.models.database import get_pool` / `async with pool.acquire() as conn: async with conn.cursor(aiomysql.DictCursor) as cur`。创建 `backend/models/repo/paper_trading.py`:

```python
"""模拟账户(纸面交易) CRUD + 成交事务 + 统计聚合。"""
import aiomysql
from datetime import datetime
from backend.models.database import get_pool


async def get_or_create_account(user_id: int = 1) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM cfzy_biz_paper_account WHERE user_id=%s", (user_id,))
            row = await cur.fetchone()
            if row:
                return row
            await cur.execute(
                "INSERT INTO cfzy_biz_paper_account (user_id, cash, initial_capital) "
                "VALUES (%s, 1000000.00, 1000000.00)", (user_id,))
            await conn.commit()
            await cur.execute("SELECT * FROM cfzy_biz_paper_account WHERE user_id=%s", (user_id,))
            return await cur.fetchone()


async def update_settings(user_id: int, initial_capital: float | None,
                          max_positions: int | None) -> None:
    sets, args = [], []
    if initial_capital is not None:
        sets.append("initial_capital=%s"); args.append(initial_capital)
    if max_positions is not None:
        sets.append("max_positions=%s"); args.append(max_positions)
    if not sets:
        return
    args.append(user_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"UPDATE cfzy_biz_paper_account SET {', '.join(sets)} WHERE user_id=%s", args)
            await conn.commit()


async def reset_account(user_id: int, initial_capital: float, max_positions: int) -> None:
    """清空持仓/流水/曲线, 现金=本金, 记 reset_at。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT id FROM cfzy_biz_paper_account WHERE user_id=%s", (user_id,))
            acct = await cur.fetchone()
            if not acct:
                await cur.execute(
                    "INSERT INTO cfzy_biz_paper_account (user_id, cash, initial_capital, max_positions) "
                    "VALUES (%s,%s,%s,%s)", (user_id, initial_capital, initial_capital, max_positions))
                await conn.commit()
                return
            aid = acct["id"]
            await cur.execute("DELETE FROM cfzy_biz_paper_position WHERE account_id=%s", (aid,))
            await cur.execute("DELETE FROM cfzy_biz_paper_trade WHERE account_id=%s", (aid,))
            await cur.execute("DELETE FROM cfzy_biz_paper_equity WHERE account_id=%s", (aid,))
            await cur.execute(
                "UPDATE cfzy_biz_paper_account SET cash=%s, initial_capital=%s, max_positions=%s, "
                "started_at=NOW(), reset_at=NOW() WHERE id=%s",
                (initial_capital, initial_capital, max_positions, aid))
            await conn.commit()


async def get_position(account_id: int, code: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_position WHERE account_id=%s AND code=%s", (account_id, code))
            return await cur.fetchone()


async def list_positions(account_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_position WHERE account_id=%s ORDER BY open_time DESC", (account_id,))
            return list(await cur.fetchall())


async def position_count(account_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM cfzy_biz_paper_position WHERE account_id=%s", (account_id,))
            return int((await cur.fetchone())[0])


async def sum_position_cost(account_id: int) -> float:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(SUM(cost_amount),0) FROM cfzy_biz_paper_position WHERE account_id=%s", (account_id,))
            return float((await cur.fetchone())[0])


async def signal_processed(account_id: int, code: str, signal_id: str, trade_date) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM cfzy_biz_paper_trade WHERE account_id=%s AND code=%s AND signal_id=%s "
                "AND trade_date=%s LIMIT 1", (account_id, code, signal_id, trade_date))
            return (await cur.fetchone()) is not None


async def apply_fill(account: dict, action: dict, code: str, name: str,
                     signal_id: str, signal_name: str, direction: str,
                     entry_model_name: str = "") -> None:
    """单事务: 写流水 + 改持仓 + 改现金。action 来自 paper_trader.decide()。"""
    aid = account["id"]
    uid = account["user_id"]
    today = datetime.now().date()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_paper_trade (account_id,user_id,code,name,side,qty,price,amount,fee,"
                "cash_after,signal_id,signal_name,signal_direction,realized_pnl,realized_pnl_pct,note,trade_date) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (aid, uid, code, name, action["side"], action["qty"], action["price"], action["amount"],
                 action["fee"], action["cash_after"], signal_id, signal_name, direction,
                 action.get("realized_pnl"), action.get("realized_pnl_pct"), action.get("note", ""), today))
            if action["side"] == "buy":
                await cur.execute(
                    "INSERT INTO cfzy_biz_paper_position (account_id,user_id,code,name,qty,cost_amount,"
                    "open_date,entry_signal_id,entry_model_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (aid, uid, code, name, action["qty"], action["amount"] + action["fee"], today,
                     signal_id, entry_model_name))
            else:  # sell
                if action.get("close_position"):
                    await cur.execute(
                        "DELETE FROM cfzy_biz_paper_position WHERE account_id=%s AND code=%s", (aid, code))
                else:
                    await cur.execute(
                        "UPDATE cfzy_biz_paper_position SET qty=qty-%s, cost_amount=cost_amount-%s "
                        "WHERE account_id=%s AND code=%s",
                        (action["qty"], action["cost_basis_sold"], aid, code))
            await cur.execute(
                "UPDATE cfzy_biz_paper_account SET cash=%s WHERE id=%s", (action["cash_after"], aid))
            await conn.commit()


async def insert_trade_skip(account_id: int, code: str, signal_id: str):
    """占位: 跳过的买点不写流水, 仅靠日志。保留空函数避免调用方分支。"""
    return None


async def list_trades(account_id: int, limit: int = 100, offset: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT * FROM cfzy_biz_paper_trade WHERE account_id=%s ORDER BY trade_time DESC, id DESC "
                "LIMIT %s OFFSET %s", (account_id, limit, offset))
            return list(await cur.fetchall())


async def upsert_equity(account_id: int, user_id: int, snap_date, cash: float,
                        holdings_mv: float, total_equity: float, total_return_pct: float,
                        position_count_: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_biz_paper_equity (account_id,user_id,snap_date,cash,holdings_mv,"
                "total_equity,total_return_pct,position_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE cash=VALUES(cash), holdings_mv=VALUES(holdings_mv), "
                "total_equity=VALUES(total_equity), total_return_pct=VALUES(total_return_pct), "
                "position_count=VALUES(position_count)",
                (account_id, user_id, snap_date, cash, holdings_mv, total_equity, total_return_pct, position_count_))
            await conn.commit()


async def get_equity_curve(account_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT snap_date,total_equity,total_return_pct,position_count FROM cfzy_biz_paper_equity "
                "WHERE account_id=%s ORDER BY snap_date ASC", (account_id,))
            return list(await cur.fetchall())


async def realized_stats(account_id: int) -> dict:
    """已实现: 卖出笔数/胜笔/总盈亏/盈亏因子。胜=realized_pnl>0。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT COUNT(*) AS n, SUM(realized_pnl>0) AS win, "
                "COALESCE(SUM(realized_pnl),0) AS pnl, "
                "COALESCE(SUM(CASE WHEN realized_pnl>0 THEN realized_pnl ELSE 0 END),0) AS gain, "
                "COALESCE(-SUM(CASE WHEN realized_pnl<0 THEN realized_pnl ELSE 0 END),0) AS loss "
                "FROM cfzy_biz_paper_trade WHERE account_id=%s AND side='sell'", (account_id,))
            return await cur.fetchone()


async def model_stats(account_id: int) -> list[dict]:
    """按买入买点(持仓 entry_signal_id 在流水里没直接存, 改按卖出流水的 entry 归因不便;
    简化: 用买入流水的 signal_id 关联——这里按"卖出已实现盈亏 + 该票当时买入买点"统计。
    实现: 卖出流水 join 该票最近一次买入流水的 signal_id。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT b.signal_id AS model, COUNT(*) AS n, SUM(s.realized_pnl>0) AS win, "
                "COALESCE(SUM(s.realized_pnl),0) AS pnl, COALESCE(AVG(s.realized_pnl_pct),0) AS avg_pct "
                "FROM cfzy_biz_paper_trade s "
                "JOIN cfzy_biz_paper_trade b ON b.account_id=s.account_id AND b.code=s.code "
                "  AND b.side='buy' AND b.trade_time<=s.trade_time "
                "WHERE s.account_id=%s AND s.side='sell' "
                "  AND b.id=(SELECT MAX(b2.id) FROM cfzy_biz_paper_trade b2 WHERE b2.account_id=s.account_id "
                "    AND b2.code=s.code AND b2.side='buy' AND b2.trade_time<=s.trade_time) "
                "GROUP BY b.signal_id ORDER BY pnl DESC", (account_id,))
            return list(await cur.fetchall())
```

- [ ] **Step 2: 在 repository.py re-export**

在 `backend/models/repository.py`(项目惯例: 统一从 repository 访问)加:

```python
from backend.models.repo.paper_trading import (  # noqa: F401
    get_or_create_account as paper_get_or_create_account,
    update_settings as paper_update_settings,
    reset_account as paper_reset_account,
    get_position as paper_get_position,
    list_positions as paper_list_positions,
    position_count as paper_position_count,
    sum_position_cost as paper_sum_position_cost,
    signal_processed as paper_signal_processed,
    apply_fill as paper_apply_fill,
    list_trades as paper_list_trades,
    upsert_equity as paper_upsert_equity,
    get_equity_curve as paper_get_equity_curve,
    realized_stats as paper_realized_stats,
    model_stats as paper_model_stats,
)
```
(若 repository.py 用 `from .repo.xxx import *` 风格, 比照其现有 import 方式对齐, 关键是这些函数能经 `repository.paper_*` 调到。)

- [ ] **Step 3: 语法检查**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -m py_compile backend/models/repo/paper_trading.py backend/models/repository.py`
Expected: 无输出(通过)。

- [ ] **Step 4: Commit**

```bash
git add backend/models/repo/paper_trading.py backend/models/repository.py
git commit -m "feat(paper): 模拟账户 repo CRUD + 统计聚合"
```

---

## Task 3: 成交决策纯函数 `paper_trader.decide` (TDD)

**Files:**
- Create: `backend/services/paper_trader.py` (本任务只写纯函数部分)
- Test: `backend/tests/test_paper_trader.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_paper_trader.py`:

```python
from backend.services import paper_trader as pt

ACCT = {"cash": 1_000_000.0, "max_positions": 10,
        "commission_rate": 0.00025, "min_commission": 5.0,
        "stamp_rate": 0.001, "transfer_rate": 0.00001}


def _acct(**kw):
    a = dict(ACCT); a.update(kw); return a


def test_buy_fee_min_commission():
    # 小额买入佣金触底5元: amount=2000 -> 佣金 max(0.5,5)=5, 过户 0.02 -> 5.02
    assert round(pt.calc_buy_fee(2000.0, _acct()), 2) == 5.02


def test_sell_fee_includes_stamp():
    # amount=100000: 佣金25, 印花税100, 过户1 -> 126.0
    assert round(pt.calc_sell_fee(100000.0, _acct()), 2) == 126.0


def test_decide_buy_normal():
    # equity_cost=100万, max=10 -> target=10万; price=10 -> 100手=10000股, amount=10万
    a = pt.decide(_acct(), None, {"direction": "buy", "signal_id": "BUY_RALLY_MA10", "price": 10.0},
                  held_count=0, equity_cost=1_000_000.0)
    assert a["side"] == "buy"
    assert a["qty"] == 10000
    assert a["amount"] == 100000.0
    assert a["cash_after"] == round(1_000_000.0 - 100000.0 - a["fee"], 2)


def test_decide_buy_already_held_skips():
    a = pt.decide(_acct(), {"qty": 10000, "cost_amount": 100000.0},
                  {"direction": "buy", "signal_id": "BUY_RALLY_MA10", "price": 10.0},
                  held_count=1, equity_cost=1_000_000.0)
    assert a["side"] == "skip" and a["reason"] == "已持仓"


def test_decide_buy_positions_full_skips():
    a = pt.decide(_acct(), None, {"direction": "buy", "signal_id": "BUY_RALLY_MA10", "price": 10.0},
                  held_count=10, equity_cost=1_000_000.0)
    assert a["side"] == "skip" and a["reason"] == "仓位满"


def test_decide_buy_insufficient_cash_skips():
    a = pt.decide(_acct(cash=500.0), None, {"direction": "buy", "signal_id": "BUY_X", "price": 10.0},
                  held_count=0, equity_cost=500.0)
    assert a["side"] == "skip"


def test_decide_sell_full_clears_position():
    pos = {"qty": 10000, "cost_amount": 100000.0}
    a = pt.decide(_acct(), pos, {"direction": "sell", "signal_id": "SELL_BREAK_MA10", "price": 11.0},
                  held_count=1, equity_cost=200000.0)
    assert a["side"] == "sell" and a["qty"] == 10000 and a["close_position"] is True
    # 卖出额11万, 费=佣金27.5+印花110+过户1.1=138.6, 净=109861.4, 成本10万 -> 盈9861.4
    assert round(a["realized_pnl"], 1) == 9861.4


def test_decide_sell_half():
    pos = {"qty": 10000, "cost_amount": 100000.0}
    a = pt.decide(_acct(), pos, {"direction": "reduce", "signal_id": "SELL_LOSS_5", "price": 9.0},
                  held_count=1, equity_cost=180000.0)
    assert a["side"] == "sell" and a["qty"] == 5000 and a["close_position"] is False
    assert a["cost_basis_sold"] == 50000.0


def test_decide_sell_not_held_skips():
    a = pt.decide(_acct(), None, {"direction": "sell", "signal_id": "SELL_BREAK_MA10", "price": 11.0},
                  held_count=0, equity_cost=1_000_000.0)
    assert a["side"] == "skip" and a["reason"] == "未持仓"
```

- [ ] **Step 2: 运行确认失败**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -m pytest backend/tests/test_paper_trader.py -v`
Expected: FAIL (module/函数未定义)。

- [ ] **Step 3: 写纯函数实现**

创建 `backend/services/paper_trader.py`(本任务仅纯函数部分):

```python
"""模拟账户成交决策(纯函数, 可单测) + on_signal 执行器(Task 4)。"""
import logging
import math

logger = logging.getLogger(__name__)

_HALF_SUFFIX = "_HALF"


def calc_buy_fee(amount: float, account: dict) -> float:
    comm = max(amount * float(account["commission_rate"]), float(account["min_commission"]))
    transfer = amount * float(account["transfer_rate"])
    return round(comm + transfer, 2)


def calc_sell_fee(amount: float, account: dict) -> float:
    comm = max(amount * float(account["commission_rate"]), float(account["min_commission"]))
    stamp = amount * float(account["stamp_rate"])
    transfer = amount * float(account["transfer_rate"])
    return round(comm + stamp + transfer, 2)


def _is_half_sell(signal_id: str, direction: str) -> bool:
    return direction == "reduce" or (signal_id or "").upper().endswith(_HALF_SUFFIX)


def decide(account: dict, position: dict | None, signal: dict,
           held_count: int, equity_cost: float) -> dict:
    """返回成交动作。side ∈ buy/sell/skip。
    equity_cost = 现金 + Σ持仓成本(成本口径总资产), 用于等额轮动定仓。"""
    direction = signal["direction"]
    price = float(signal["price"])
    cash = float(account["cash"])
    max_pos = int(account["max_positions"])

    if direction == "buy":
        if position is not None:
            return {"side": "skip", "reason": "已持仓"}
        if held_count >= max_pos:
            return {"side": "skip", "reason": "仓位满"}
        target = equity_cost / max_pos
        budget = min(target, cash)
        lots = int(budget // (price * 100))
        # 含费回退: amount+fee 不得超现金
        while lots >= 1:
            amount = round(lots * 100 * price, 2)
            fee = calc_buy_fee(amount, account)
            if amount + fee <= cash:
                return {"side": "buy", "qty": lots * 100, "price": price, "amount": amount,
                        "fee": fee, "cash_after": round(cash - amount - fee, 2), "note": ""}
            lots -= 1
        return {"side": "skip", "reason": "资金不足"}

    if direction in ("sell", "reduce"):
        if position is None or int(position["qty"]) <= 0:
            return {"side": "skip", "reason": "未持仓"}
        qty = int(position["qty"])
        cost_amount = float(position["cost_amount"])
        if _is_half_sell(signal["signal_id"], direction):
            sell_qty = int(qty // 2 // 100) * 100
            if sell_qty < 100:           # 不足整手 -> 全清
                sell_qty = qty
            note = "卖半" if sell_qty < qty else "卖半→不足整手全清"
        else:
            sell_qty = qty
            note = "清仓"
        close_position = sell_qty >= qty
        amount = round(sell_qty * price, 2)
        fee = calc_sell_fee(amount, account)
        cost_basis_sold = round(cost_amount * sell_qty / qty, 2)
        realized_pnl = round((amount - fee) - cost_basis_sold, 2)
        realized_pnl_pct = round(realized_pnl / cost_basis_sold * 100, 3) if cost_basis_sold else 0.0
        return {"side": "sell", "qty": sell_qty, "price": price, "amount": amount, "fee": fee,
                "cash_after": round(cash + amount - fee, 2), "close_position": close_position,
                "cost_basis_sold": cost_basis_sold, "realized_pnl": realized_pnl,
                "realized_pnl_pct": realized_pnl_pct, "note": note}

    return {"side": "skip", "reason": "非交易方向"}
```

- [ ] **Step 4: 运行确认通过**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -m pytest backend/tests/test_paper_trader.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/services/paper_trader.py backend/tests/test_paper_trader.py
git commit -m "feat(paper): 成交决策纯函数 decide + 费用计算(TDD)"
```

---

## Task 4: 执行器 `on_signal` + 接入 scanner

**Files:**
- Modify: `backend/services/paper_trader.py` (追加 on_signal)
- Modify: `backend/services/scanner.py:447` (save_signal 之后)

- [ ] **Step 1: 在 paper_trader.py 追加 on_signal**

在 `backend/services/paper_trader.py` 末尾追加:

```python
import re as _re


async def on_signal(*, code: str, name: str, signal_id: str, signal_name: str,
                    direction: str, price: float, user_id: int) -> None:
    """信号确认触发(会推送)时调用; 仅生产环境执行; 任何异常吞掉不影响主流程。"""
    from backend.core.config import is_production
    if not await is_production():
        return
    try:
        if direction not in ("buy", "sell", "reduce"):
            return
        if not code or not _re.match(r"^\d{6}$", code):
            return
        if not price or float(price) <= 0:
            return
        from backend.services import signal_specs
        if signal_specs.group_of(signal_id) in ("regime", "sector"):
            return
        from backend.models import repository
        from datetime import datetime
        acct = await repository.paper_get_or_create_account(user_id)
        today = datetime.now().date()
        if await repository.paper_signal_processed(acct["id"], code, signal_id, today):
            return
        position = await repository.paper_get_position(acct["id"], code)
        held = await repository.paper_position_count(acct["id"])
        equity_cost = float(acct["cash"]) + await repository.paper_sum_position_cost(acct["id"])
        action = decide(acct, position, {"direction": direction, "signal_id": signal_id,
                                         "price": float(price)}, held, equity_cost)
        if action["side"] == "skip":
            logger.info(f"[paper] 跳过 {direction} {name}({code}) {signal_id}: {action['reason']}")
            return
        await repository.paper_apply_fill(acct, action, code, name, signal_id, signal_name,
                                          direction, entry_model_name=signal_name)
        logger.info(f"[paper] 模拟{action['side']} {name}({code}) {action['qty']}股 @ {price} "
                    f"({signal_id}) cash={action['cash_after']:.0f}")
    except Exception as e:
        logger.warning(f"[paper] on_signal 异常({code} {signal_id}), 忽略: {e}")
```

- [ ] **Step 2: 在 scanner.py 接入(save_signal 之后)**

在 `backend/services/scanner.py` 的单信号 emit 函数里, `await repository.save_signal(...)`(约 line 439-447) 之后、WS 推送之前, 插入:

```python
    # 模拟账户: 个股买卖点实时模拟成交(仅生产环境, 异常自吞)
    from backend.services import paper_trader
    await paper_trader.on_signal(
        code=code, name=name, signal_id=sig.signal_id, signal_name=sig.signal_name,
        direction=sig.direction, price=price, user_id=user_id,
    )
```

- [ ] **Step 3: 语法检查**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -m py_compile backend/services/paper_trader.py backend/services/scanner.py`
Expected: 通过。

- [ ] **Step 4: Commit**

```bash
git add backend/services/paper_trader.py backend/services/scanner.py
git commit -m "feat(paper): on_signal 执行器 + 接入 scanner 单信号 emit"
```

---

## Task 5: 收盘盯市任务 + 注册

**Files:**
- Create: `backend/services/paper_equity.py`
- Modify: `backend/services/task_registry.py`

- [ ] **Step 1: 写盯市任务**

创建 `backend/services/paper_equity.py`:

```python
"""模拟账户收盘盯市: 每交易日 15:05 对持仓按当日收盘价估值, 写资金曲线。"""
import logging
from datetime import datetime

from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)


async def snapshot_paper_equity(user_id: int = 1) -> None:
    now = datetime.now()
    if now.weekday() >= 5:
        return
    acct = await repository.paper_get_or_create_account(user_id)
    positions = await repository.paper_list_positions(acct["id"])
    holdings_mv = 0.0
    if positions:
        codes = [p["code"] for p in positions]
        try:
            quotes = await data_fetcher.get_realtime_quotes(codes)
        except Exception as e:
            logger.warning(f"[paper_equity] 取价失败, 用成本估值: {e}")
            quotes = {}
        for p in positions:
            px = float((quotes.get(p["code"]) or {}).get("price") or 0)
            if px <= 0:                       # 停牌/取不到 -> 用成本均价
                px = float(p["cost_amount"]) / int(p["qty"]) if p["qty"] else 0
            holdings_mv += px * int(p["qty"])
    cash = float(acct["cash"])
    total = round(cash + holdings_mv, 2)
    init = float(acct["initial_capital"]) or 1.0
    ret_pct = round((total - init) / init * 100, 3)
    await repository.paper_upsert_equity(acct["id"], user_id, now.date(), round(cash, 2),
                                         round(holdings_mv, 2), total, ret_pct, len(positions))
    logger.info(f"[paper_equity] {now.date()} 总资产={total:.0f} 收益率={ret_pct:+.2f}% 持仓{len(positions)}")
```

- [ ] **Step 2: 注册 handler**

在 `backend/services/task_registry.py` 的 import 区加 `from backend.services.paper_equity import snapshot_paper_equity`, 并在 `TASK_HANDLERS` 字典加 `"snapshot_paper_equity": snapshot_paper_equity,`。

- [ ] **Step 3: 语法检查**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -m py_compile backend/services/paper_equity.py backend/services/task_registry.py`
Expected: 通过。

- [ ] **Step 4: Commit**

```bash
git add backend/services/paper_equity.py backend/services/task_registry.py
git commit -m "feat(paper): 收盘盯市任务 + 注册 handler"
```

---

## Task 6: API 路由

**Files:**
- Create: `backend/routers/paper_trading.py`
- Modify: `backend/main.py`

- [ ] **Step 1: 写路由**

参照 `backend/routers/market_report.py` 的鉴权依赖 `Depends(get_current_user)`。创建 `backend/routers/paper_trading.py`:

```python
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository
from backend import data_fetcher

router = APIRouter(prefix="/api/paper-trading", tags=["paper-trading"])


@router.get("/summary")
async def summary(user: Annotated[dict, Depends(get_current_user)]):
    acct = await repository.paper_get_or_create_account(user["id"])
    positions = await repository.paper_list_positions(acct["id"])
    holdings_mv = 0.0
    if positions:
        codes = [p["code"] for p in positions]
        try:
            quotes = await data_fetcher.get_realtime_quotes(codes)
        except Exception:
            quotes = {}
        for p in positions:
            px = float((quotes.get(p["code"]) or {}).get("price") or 0) or \
                (float(p["cost_amount"]) / int(p["qty"]) if p["qty"] else 0)
            holdings_mv += px * int(p["qty"])
    cash = float(acct["cash"])
    total = cash + holdings_mv
    init = float(acct["initial_capital"]) or 1.0
    rs = await repository.paper_realized_stats(acct["id"])
    curve = await repository.paper_get_equity_curve(acct["id"])
    peak, mdd = init, 0.0
    for pt in curve:
        eq = float(pt["total_equity"])
        peak = max(peak, eq)
        mdd = min(mdd, (eq - peak) / peak * 100 if peak else 0)
    n = int(rs["n"] or 0); win = int(rs["win"] or 0)
    gain = float(rs["gain"] or 0); loss = float(rs["loss"] or 0)
    return {
        "initial_capital": init, "cash": round(cash, 2), "holdings_mv": round(holdings_mv, 2),
        "total_equity": round(total, 2), "total_return_pct": round((total - init) / init * 100, 3),
        "position_count": len(positions),
        "realized_pnl": round(float(rs["pnl"] or 0), 2),
        "closed_trades": n, "win_rate": round(win / n * 100, 1) if n else None,
        "profit_factor": round(gain / loss, 2) if loss > 0 else (None if gain == 0 else 99.0),
        "max_drawdown_pct": round(mdd, 2),
        "max_positions": int(acct["max_positions"]),
    }


@router.get("/positions")
async def positions(user: Annotated[dict, Depends(get_current_user)]):
    acct = await repository.paper_get_or_create_account(user["id"])
    rows = await repository.paper_list_positions(acct["id"])
    if rows:
        try:
            quotes = await data_fetcher.get_realtime_quotes([r["code"] for r in rows])
        except Exception:
            quotes = {}
        for r in rows:
            qty = int(r["qty"]); cost = float(r["cost_amount"])
            px = float((quotes.get(r["code"]) or {}).get("price") or 0)
            r["price"] = px
            r["mv"] = round(px * qty, 2) if px else None
            r["float_pct"] = round((px * qty - cost) / cost * 100, 2) if (px and cost) else None
            r["avg_cost"] = round(cost / qty, 3) if qty else None
    return rows


@router.get("/trades")
async def trades(user: Annotated[dict, Depends(get_current_user)],
                 limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    acct = await repository.paper_get_or_create_account(user["id"])
    return await repository.paper_list_trades(acct["id"], limit, offset)


@router.get("/equity")
async def equity(user: Annotated[dict, Depends(get_current_user)]):
    acct = await repository.paper_get_or_create_account(user["id"])
    return await repository.paper_get_equity_curve(acct["id"])


@router.get("/model-stats")
async def model_stats(user: Annotated[dict, Depends(get_current_user)]):
    acct = await repository.paper_get_or_create_account(user["id"])
    return await repository.paper_model_stats(acct["id"])


class SettingsBody(BaseModel):
    initial_capital: float | None = None
    max_positions: int | None = None


@router.put("/settings")
async def update_settings(body: SettingsBody, user: Annotated[dict, Depends(get_current_user)]):
    await repository.paper_update_settings(user["id"], body.initial_capital, body.max_positions)
    return {"ok": True}


class ResetBody(BaseModel):
    initial_capital: float = 1000000.0
    max_positions: int = 10


@router.post("/reset")
async def reset(body: ResetBody, user: Annotated[dict, Depends(get_current_user)]):
    await repository.paper_reset_account(user["id"], body.initial_capital, body.max_positions)
    return {"ok": True}
```

- [ ] **Step 2: 注册路由**

在 `backend/main.py`: import 区加 `paper_trading`(比照现有 `from backend.routers import ... market_report ...` 那行追加), 并加 `app.include_router(paper_trading.router)`。

- [ ] **Step 3: 语法检查 + 导入**

Run: `C:/Users/成剑/AppData/Local/Programs/Python/Python313/python.exe -c "import backend.main"`
Expected: 无导入错误。

- [ ] **Step 4: Commit**

```bash
git add backend/routers/paper_trading.py backend/main.py
git commit -m "feat(paper): /api/paper-trading 路由(概览/持仓/流水/曲线/模型胜率/设置/重置)"
```

---

## Task 7: 前端 api + store

**Files:**
- Create: `frontend/src/api/paper-trading.ts`
- Create: `frontend/src/stores/paper-trading.ts`

- [ ] **Step 1: api 封装**

参照 `frontend/src/api/trade-analysis.ts` 用 `client`(axios 实例)。创建 `frontend/src/api/paper-trading.ts`:

```typescript
import client from './client'

export interface PaperSummary {
  initial_capital: number; cash: number; holdings_mv: number; total_equity: number
  total_return_pct: number; position_count: number; realized_pnl: number
  closed_trades: number; win_rate: number | null; profit_factor: number | null
  max_drawdown_pct: number; max_positions: number
}

export const fetchPaperSummary = () => client.get<PaperSummary>('/paper-trading/summary').then(r => r.data)
export const fetchPaperPositions = () => client.get('/paper-trading/positions').then(r => r.data)
export const fetchPaperTrades = (limit = 100, offset = 0) =>
  client.get('/paper-trading/trades', { params: { limit, offset } }).then(r => r.data)
export const fetchPaperEquity = () => client.get('/paper-trading/equity').then(r => r.data)
export const fetchPaperModelStats = () => client.get('/paper-trading/model-stats').then(r => r.data)
export const updatePaperSettings = (initial_capital?: number, max_positions?: number) =>
  client.put('/paper-trading/settings', { initial_capital, max_positions }).then(r => r.data)
export const resetPaperAccount = (initial_capital: number, max_positions: number) =>
  client.post('/paper-trading/reset', { initial_capital, max_positions }).then(r => r.data)
```
(若项目 axios 实例的 baseURL 已含 `/api`, 上面路径正确; 否则改为 `/api/paper-trading/...` 与现有 api 文件一致。)

- [ ] **Step 2: store**

参照 `frontend/src/stores/stock.ts` 的 setup 式 store。创建 `frontend/src/stores/paper-trading.ts`:

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/paper-trading'

export const usePaperStore = defineStore('paper', () => {
  const summary = ref<api.PaperSummary | null>(null)
  const positions = ref<any[]>([])
  const trades = ref<any[]>([])
  const equity = ref<any[]>([])
  const modelStats = ref<any[]>([])
  const loading = ref(false)

  async function loadAll() {
    loading.value = true
    try {
      const [s, p, t, e, m] = await Promise.all([
        api.fetchPaperSummary(), api.fetchPaperPositions(), api.fetchPaperTrades(),
        api.fetchPaperEquity(), api.fetchPaperModelStats(),
      ])
      summary.value = s; positions.value = p; trades.value = t; equity.value = e; modelStats.value = m
    } finally { loading.value = false }
  }
  return { summary, positions, trades, equity, modelStats, loading, loadAll }
})
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend; npx vue-tsc --noEmit` (若项目用此命令; 否则在 Task 8 末尾随 `npm run build` 一并校验)。
Expected: 无与新文件相关的类型错误。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/paper-trading.ts frontend/src/stores/paper-trading.ts
git commit -m "feat(paper): 前端 api 封装 + pinia store"
```

---

## Task 8: 前端页面 + 路由 + 菜单

**Files:**
- Create: `frontend/src/views/PaperTradingView.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/layout/AppSidebar.vue`

- [ ] **Step 1: 写页面**

创建 `frontend/src/views/PaperTradingView.vue`(用 Naive UI; 资金曲线先用简易 SVG 或复用项目现有图表组件; 概览卡 + 持仓表 + 流水表 + 模型胜率表 + 设置/重置)。遵循 [[coding-standards]]: 所有按钮操作给 NMessage 反馈, 加载用 NSkeleton:

```vue
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NDataTable, NButton, NInputNumber, NPopconfirm, NStatistic, NGrid, NGi, useMessage } from 'naive-ui'
import { usePaperStore } from '../stores/paper-trading'
import { resetPaperAccount, updatePaperSettings } from '../api/paper-trading'

const store = usePaperStore()
const message = useMessage()
const initCap = ref(1000000)
const maxPos = ref(10)

onMounted(async () => {
  await store.loadAll()
  if (store.summary) { initCap.value = store.summary.initial_capital; maxPos.value = store.summary.max_positions }
})

async function onSaveSettings() {
  try { await updatePaperSettings(initCap.value, maxPos.value); message.success('设置已保存(本金在下次重置生效)'); await store.loadAll() }
  catch (e: any) { message.error('保存失败: ' + (e?.message || e)) }
}
async function onReset() {
  try { await resetPaperAccount(initCap.value, maxPos.value); message.success('已重置模拟账户'); await store.loadAll() }
  catch (e: any) { message.error('重置失败: ' + (e?.message || e)) }
}

const posCols = [
  { title: '名称', key: 'name' }, { title: '代码', key: 'code' },
  { title: '股数', key: 'qty' }, { title: '成本', key: 'avg_cost' },
  { title: '现价', key: 'price' },
  { title: '浮盈%', key: 'float_pct', render: (r: any) => (r.float_pct ?? '—') + (r.float_pct != null ? '%' : '') },
  { title: '买点', key: 'entry_signal_id' },
]
const tradeCols = [
  { title: '时间', key: 'trade_time' }, { title: '名称', key: 'name' },
  { title: '方向', key: 'side', render: (r: any) => (r.side === 'buy' ? '买' : '卖') },
  { title: '股数', key: 'qty' }, { title: '价', key: 'price' }, { title: '费', key: 'fee' },
  { title: '信号', key: 'signal_name' },
  { title: '已实现盈亏', key: 'realized_pnl', render: (r: any) => r.realized_pnl ?? '—' },
]
const modelCols = [
  { title: '买点模型', key: 'model' }, { title: '笔数', key: 'n' },
  { title: '胜', key: 'win' }, { title: '总盈亏', key: 'pnl' },
  { title: '平均%', key: 'avg_pct', render: (r: any) => Number(r.avg_pct).toFixed(2) + '%' },
]
</script>

<template>
  <div style="padding: 12px; display: flex; flex-direction: column; gap: 12px;">
    <NCard title="模拟账户 · 概览" size="small">
      <NGrid v-if="store.summary" :cols="4" :x-gap="12" :y-gap="12" responsive="screen">
        <NGi><NStatistic label="总资产" :value="store.summary.total_equity" /></NGi>
        <NGi><NStatistic label="累计收益率%" :value="store.summary.total_return_pct" /></NGi>
        <NGi><NStatistic label="已实现胜率%" :value="store.summary.win_rate ?? '—'" /></NGi>
        <NGi><NStatistic label="盈亏因子" :value="store.summary.profit_factor ?? '—'" /></NGi>
        <NGi><NStatistic label="现金" :value="store.summary.cash" /></NGi>
        <NGi><NStatistic label="持仓市值" :value="store.summary.holdings_mv" /></NGi>
        <NGi><NStatistic label="最大回撤%" :value="store.summary.max_drawdown_pct" /></NGi>
        <NGi><NStatistic label="持仓数" :value="store.summary.position_count + '/' + store.summary.max_positions" /></NGi>
      </NGrid>
    </NCard>

    <NCard title="账户设置" size="small">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <span>初始资金</span><NInputNumber v-model:value="initCap" :min="10000" :step="10000" style="width: 160px" />
        <span>最大持仓数</span><NInputNumber v-model:value="maxPos" :min="1" :max="50" style="width: 120px" />
        <NButton size="small" @click="onSaveSettings">保存设置</NButton>
        <NPopconfirm @positive-click="onReset">
          <template #trigger><NButton size="small" type="warning">重置账户</NButton></template>
          确认用初始资金 {{ initCap }} 重置(清空持仓/流水/曲线)?
        </NPopconfirm>
      </div>
    </NCard>

    <NCard title="当前持仓" size="small">
      <NDataTable :columns="posCols" :data="store.positions" :bordered="false" size="small" :resizable-columns="true" />
    </NCard>
    <NCard title="按买点模型 · 已实现胜率" size="small">
      <NDataTable :columns="modelCols" :data="store.modelStats" :bordered="false" size="small" :resizable-columns="true" />
    </NCard>
    <NCard title="成交流水" size="small">
      <NDataTable :columns="tradeCols" :data="store.trades" :bordered="false" size="small" :resizable-columns="true" />
    </NCard>
  </div>
</template>
```
(资金曲线图: v1 先省略图表, 概览数字足够验证正反馈; 后续可加 ECharts/项目现有图组件。)

- [ ] **Step 2: 加路由**

在 `frontend/src/router/index.ts` 的 routes 数组加(比照现有 `/trade-analysis` 路由项):

```typescript
  { path: '/paper-trading', name: 'paper-trading', component: () => import('../views/PaperTradingView.vue') },
```

- [ ] **Step 3: 加菜单(复盘组)**

在 `frontend/src/components/layout/AppSidebar.vue` 的 review 分组(约 line 33-69)加一条菜单项, 比照现有 `/trade-analysis`/`/review` 项的写法(label「模拟账户」、key/path `/paper-trading`、配一个图标)。

- [ ] **Step 4: 构建校验**

Run: `cd frontend; npm run build`
Expected: `✓ built`, 无类型/编译错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/PaperTradingView.vue frontend/src/router/index.ts frontend/src/components/layout/AppSidebar.vue
git commit -m "feat(paper): 模拟账户前端页面 + 路由 + 复盘菜单"
```

---

## Task 9: changelog + 部署

**Files:**
- Modify: `frontend/src/data/changelog.ts`

- [ ] **Step 1: 加 changelog(置顶, 版本号取当前最大+1)**

在 `frontend/src/data/changelog.ts` 数组头部加一条(版本号按当时实际最大号 +1):

```typescript
  {
    version: 'v1.7.xxx',
    date: '2026-06-09',
    title: '新增模拟账户: 按模型买卖点自动模拟成交, 验证持续正反馈',
    changes: [
      { text: '新增「模拟账户」(复盘组): 自选池触发个股买点即模拟买入、卖点即模拟卖出, 等额轮动+A股真实费用(佣金万2.5最低5元/印花千1/过户万0.1)+按信号触发价实时成交。初始资金与最大持仓数可设、可一键重置。展示总资产/累计收益率/已实现胜率/盈亏因子/最大回撤+持仓/流水/按买点模型胜率, 用来验证严格执行模型是否持续正反馈。', tag: 'new' },
    ],
  },
```

- [ ] **Step 2: 部署(按 [[auto-deploy]] 流程)**

`tar -C "/d/财务管理/交易系统/trading-monitor" --exclude=node_modules --exclude=__pycache__ --exclude=.git --exclude=frontend/dist --exclude=.claude --exclude='*.tar.gz' -czf "/d/财务管理/交易系统/trading-deploy.tar.gz" .` → scp → ssh(解压 + `cd frontend && npm run build` + `systemctl restart trading-monitor`)。部署后 `ssh grep` 核对后端改动在服务器 + `systemctl is-active`。

- [ ] **Step 3: 部署后验证**

- 服务 active。
- `curl http://124.71.75.5/api/paper-trading/summary`(带 admin token) 返回账户概览(首次自动建账户)。
- 前端「模拟账户」页可打开。
- 等下一个交易日盘中有个股买卖点触发后, 回看持仓/流水是否如期生成。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/data/changelog.ts
git commit -m "feat(paper): changelog 模拟账户上线"
```

---

## Self-Review 备注(已核)

- **Spec 覆盖**: 账户规则(Task 1 表默认值/Task 3 decide)、初始资金可设(Task 2 settings/reset、Task 6 API、Task 8 UI)、信号范围过滤(Task 4 on_signal)、实时执行(Task 4 hook)、费用(Task 3 calc_*)、卖半/清仓(Task 3 _is_half_sell)、盯市曲线(Task 5)、统计与按模型胜率(Task 2 realized_stats/model_stats、Task 6)、前端(Task 7/8) — 全覆盖。
- **类型一致**: decide() 返回字段(side/qty/price/amount/fee/cash_after/close_position/cost_basis_sold/realized_pnl/realized_pnl_pct/note/reason) 在 apply_fill 与测试中一致使用。
- **幂等**: on_signal 用 (account,code,signal_id,today) 查 paper_trade 防重 + 上游 signal_already_sent_today 双保险。
- **风险点**: scanner.py 单信号 emit 函数的确切行号以实际为准(本计划定位在 save_signal 之后); repository.py re-export 方式以其现有惯例对齐。
