# 全市场日线回填 + 每日追加 Implementation Plan(工作流二 A 期)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把全市场(剔北交所/ST/退市)约 5400 只股票的近 5 年日线一次性回填进 `cfzy_sys_kline_cache`,并每日收盘后增量追加,放大买点回测样本广度。

**Architecture:** 复用线上已验证的「新浪全A列表(Market_Center hs_a)+ 新浪 getKLineData(scale=240)」拉取路径(与 `market_breadth_refresher` 同源同款,非东财、prod 安全)。新增服务 `backend/services/fullmarket_klines.py`:纯函数(解析/过滤/续跑判定,可单测)+ 异步编排(回填/追加)。一次性回填走 `backend/scripts/` 脚本手动跑(断点续跑);每日追加走定时任务。落库复用已有 `repository.cache_klines`(`ON DUPLICATE KEY UPDATE` 幂等)。

**Tech Stack:** Python 3 / httpx(异步,`trust_env=False` 隔离)/ aiomysql / MySQL `cfzy_sys_kline_cache`(PK `(code, trade_date)`)/ pytest(纯函数测试)。

**关键事实(已实测,2026-06-05):** 新浪 `getKLineData?...&datalen=N` 返回的就是 N 根(实测 datalen=1500 → 1500 根、回溯到 2020-03-26),不是网传的 ~1023 上限。故 `datalen=1300`(≈5 年交易日)一次调用即可覆盖 5 年。

**Scope 边界(不含):** 全市场分时(Plan 5)、交易回合环境重建(Plan 2)、前端展示。本计划只做日线数据层 + 抓取任务。

---

### Task 1: repo 增加「各 code 已缓存根数」查询(断点续跑用)

**Files:**
- Modify: `backend/models/repo/signal_config.py`(K-line Cache 区追加 `get_kline_counts`)
- Modify: `backend/models/repository.py`(facade 导出 `get_kline_counts`,与 `cache_klines`/`get_cached_klines` 并列)

回填要「已回填的票跳过」,需要一次性拿到每只 code 已缓存的根数。用一条 `GROUP BY` 查询返回 `{code: count}`,避免对 5400 只逐只查。

- [ ] **Step 1: 在 `signal_config.py` 的 K-line Cache 区追加函数**

在 `backend/models/repo/signal_config.py` 末尾(`get_cached_klines` 之后)追加:

```python
async def get_kline_counts() -> dict[str, int]:
    """返回 {code: 已缓存日线根数}, 供全市场回填断点续跑判定."""
    rows = await _fetchall(
        "SELECT code, COUNT(*) AS c FROM cfzy_sys_kline_cache GROUP BY code"
    )
    return {r["code"]: int(r["c"]) for r in rows}
```

- [ ] **Step 2: 在 `repository.py` facade 导出**

打开 `backend/models/repository.py`,找到已导出 `cache_klines, get_cached_klines` 的 `from backend.models.repo.signal_config import (...)`(或等价的逐行 import)。在同一处把 `get_kline_counts` 一并导出,紧挨 `get_cached_klines`。例如该 import 块若为:

```python
from backend.models.repo.signal_config import (
    get_signal_config,
    save_signal_config,
    cache_klines,
    get_cached_klines,
)
```

改为追加一行 `    get_kline_counts,`。若 repository.py 用的是 `from ... import a, b, c` 单行形式,则在该行尾部加 `, get_kline_counts`。

- [ ] **Step 3: 验证导出可用**

Run: `python -c "from backend.models import repository; print(hasattr(repository,'get_kline_counts'))"`
Expected: `True`
(若本地 `python` 是 Microsoft Store stub 返回 exit 49,用 `py -c "..."` 代替;且需在仓库根目录、`PYTHONPATH` 含根目录。)

- [ ] **Step 4: Commit**

```bash
git add backend/models/repo/signal_config.py backend/models/repository.py
git commit -m "feat(fullkline): repo get_kline_counts for backfill resumability"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 2: 纯函数(新浪K线解析 / 列表过滤 / 续跑判定)+ 测试

**Files:**
- Create: `backend/services/fullmarket_klines.py`
- Test: `backend/tests/test_fullmarket_klines.py`

三个纯函数:`_parse_sina_klines`(jsonp 文本 → OHLCV 元组列表)、`_filter_symbols`(Market_Center 行 → 新浪 symbol,剔北交所/ST/退)、`_needs_backfill`(已缓存根数是否不足)。先建文件含这三个函数 + 模块常量,异步编排在 Task 3 追加。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_fullmarket_klines.py`:

```python
"""全市场日线 纯函数测试 — 解析/过滤/续跑判定."""
from backend.services.fullmarket_klines import (
    _parse_sina_klines, _filter_symbols, _needs_backfill,
)


class TestParseSinaKlines:
    def test_empty_text(self):
        assert _parse_sina_klines("") == []

    def test_no_parens(self):
        assert _parse_sina_klines("garbage no json") == []

    def test_valid_jsonp(self):
        text = ('jsonp([{"day":"2026-06-04","open":"10.0","high":"11.0",'
                '"low":"9.5","close":"10.5","volume":"1000"}])')
        rows = _parse_sina_klines(text)
        assert rows == [("2026-06-04", 10.0, 11.0, 9.5, 10.5, 1000.0)]

    def test_skips_malformed_entry(self):
        text = ('cb([{"day":"2026-06-04","open":"10","high":"11","low":"9",'
                '"close":"10.5","volume":"1000"},{"day":"2026-06-05"}])')
        rows = _parse_sina_klines(text)
        assert len(rows) == 1
        assert rows[0][0] == "2026-06-04"

    def test_empty_array(self):
        assert _parse_sina_klines("cb([])") == []


class TestFilterSymbols:
    def test_keeps_sh_sz_drops_bj_st_delisted(self):
        rows = [
            {"symbol": "sh600519", "name": "贵州茅台"},
            {"symbol": "sz000001", "name": "平安银行"},
            {"symbol": "bj830799", "name": "艾融软件"},
            {"symbol": "sz000004", "name": "ST国华"},
            {"symbol": "sh600891", "name": "退市秋林"},
            {"symbol": "sz000005", "name": "*ST星源"},
        ]
        assert _filter_symbols(rows) == ["sh600519", "sz000001"]

    def test_empty(self):
        assert _filter_symbols([]) == []


class TestNeedsBackfill:
    def test_below_threshold(self):
        assert _needs_backfill(0, 1000) is True
        assert _needs_backfill(500, 1000) is True

    def test_at_or_above_threshold(self):
        assert _needs_backfill(1000, 1000) is False
        assert _needs_backfill(1300, 1000) is False
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_fullmarket_klines.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.services.fullmarket_klines'`

- [ ] **Step 3: 建文件实现三个纯函数 + 常量**

`backend/services/fullmarket_klines.py`:

```python
"""全市场日线回填 + 每日追加 - 工作流二 A 期.

复用 market_breadth_refresher 同源路径: 新浪全A列表(Market_Center hs_a) + 新浪
getKLineData(scale=240, ma=no). 非东财, prod 安全. 落 cfzy_sys_kline_cache(幂等 upsert).
- 一次性回填: backfill_full_market(datalen≈1300 → 近5年), 断点续跑(已≥MIN_BARS的票跳过).
- 每日追加: append_full_market_daily(datalen=8 → 刷新最近几日), 收盘后定时跑.
"""
import json
import logging

logger = logging.getLogger(__name__)

_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
_KLINE_URL = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
              "CN_MarketDataService.getKLineData")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
_CONCURRENCY = 15
BACKFILL_DATALEN = 1300   # ≈5 年交易日(实测新浪按 datalen 返回, 1300 够5年)
APPEND_DATALEN = 8        # 每日追加只需最近几根
MIN_BARS = 1000           # 已缓存≥此根数视为已回填, 回填时跳过(断点续跑)


def _parse_sina_klines(text: str) -> list[tuple]:
    """新浪 getKLineData jsonp 文本 → [(date, open, high, low, close, volume), ...].

    取首个 '(' 与末个 ')' 之间的 JSON 数组(与 fetcher/klines.py 同款). 解析失败/空 → [].
    """
    s = text.find("(")
    e = text.rfind(")")
    if s < 0 or e <= s:
        return []
    try:
        data = json.loads(text[s + 1:e])
    except (json.JSONDecodeError, ValueError):
        return []
    if not data:
        return []
    rows: list[tuple] = []
    for d in data:
        try:
            rows.append((
                str(d["day"])[:10], float(d["open"]), float(d["high"]),
                float(d["low"]), float(d["close"]), float(d["volume"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _filter_symbols(rows: list[dict]) -> list[str]:
    """Market_Center 行 → 新浪 symbol 列表, 剔北交所(bj)/ST/退市/*."""
    out: list[str] = []
    for it in rows:
        sym = it.get("symbol", "")
        name = it.get("name", "")
        if sym.startswith("bj") or not (sym.startswith("sh") or sym.startswith("sz")):
            continue
        if "ST" in name or "退" in name or name.startswith("*"):
            continue
        out.append(sym)
    return out


def _needs_backfill(cached_count: int, min_bars: int) -> bool:
    """已缓存根数不足 min_bars → 需要(继续)回填."""
    return cached_count < min_bars
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest backend/tests/test_fullmarket_klines.py -v`
Expected: 9 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/services/fullmarket_klines.py backend/tests/test_fullmarket_klines.py
git commit -m "feat(fullkline): pure parse/filter/resume helpers with tests"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 3: 异步编排(全市场拉取 → 回填 / 追加)

**Files:**
- Modify: `backend/services/fullmarket_klines.py`(追加异步函数)

复用 `market_breadth_refresher` 的列表分页与并发拉取写法。`_run_full_market` 是回填/追加共用内核:`only_missing=True` 时按 `get_kline_counts` 跳过已回填的票。落库用 `repository.cache_klines(code, rows)`。不写单测(项目惯例:网络/DB IO 不单测;纯逻辑已在 Task 2 覆盖)。

- [ ] **Step 1: 在 `fullmarket_klines.py` 末尾追加异步编排**

```python
import asyncio

import httpx

from backend.fetcher.codes import _normalize_code
from backend.models import repository


async def _fetch_symbols(client: httpx.AsyncClient) -> list[str]:
    """新浪 hs_a 全A列表(分页), 经 _filter_symbols 剔除. 返回新浪 symbol(如 sh600519)."""
    out: list[str] = []
    page = 1
    while page <= 90:
        params = {"page": page, "num": 80, "sort": "symbol", "asc": 1,
                  "node": "hs_a", "symbol": "", "_s_r_a": "page"}
        try:
            r = await client.get(_LIST_URL, params=params, headers=_HEADERS)
            txt = (r.text or "").strip()
        except Exception as e:
            logger.warning(f"[fullkline] 列表第{page}页失败: {e}")
            break
        if not txt or txt == "null":
            break
        try:
            rows = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            break
        if not rows:
            break
        out.extend(_filter_symbols(rows))
        page += 1
    return out


async def _fetch_klines(client: httpx.AsyncClient, sym: str, datalen: int,
                        sem: asyncio.Semaphore) -> list[tuple]:
    url = f"{_KLINE_URL}?symbol={sym}&scale=240&ma=no&datalen={datalen}"
    async with sem:
        try:
            r = await client.get(url, headers=_HEADERS)
            return _parse_sina_klines(r.text)
        except Exception:
            return []


async def _run_full_market(datalen: int, only_missing: bool) -> dict:
    """全市场逐只拉日线写缓存. only_missing=True 跳过已≥MIN_BARS的票(回填续跑)."""
    counts = await repository.get_kline_counts() if only_missing else {}
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        trust_env=False,
    )
    ok = skipped = empty = done = 0
    try:
        symbols = await _fetch_symbols(client)
        total = len(symbols)
        if total == 0:
            logger.warning("[fullkline] 全市场列表为空, 跳过")
            return {"total": 0, "ok": 0, "skipped": 0, "empty": 0}
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _one(sym: str):
            nonlocal ok, skipped, empty, done
            code = _normalize_code(sym)
            if only_missing and not _needs_backfill(counts.get(code, 0), MIN_BARS):
                skipped += 1
                done += 1
                return
            rows = await _fetch_klines(client, sym, datalen, sem)
            if rows:
                try:
                    await repository.cache_klines(code, rows)
                    ok += 1
                except Exception as e:
                    empty += 1
                    logger.debug(f"[fullkline] 写库失败 {code}: {e}")
            else:
                empty += 1
            done += 1
            if done % 500 == 0:
                logger.info(f"[fullkline] {done}/{total} ok={ok} skip={skipped} empty={empty}")

        await asyncio.gather(*[_one(s) for s in symbols])
    finally:
        await client.aclose()
    logger.info(f"[fullkline] DONE total={total} ok={ok} skip={skipped} empty={empty}")
    return {"total": total, "ok": ok, "skipped": skipped, "empty": empty}


async def backfill_full_market() -> dict:
    """一次性回填全市场近5年日线(断点续跑). 手动脚本触发, 耗时较长."""
    return await _run_full_market(BACKFILL_DATALEN, only_missing=True)


async def append_full_market_daily() -> dict:
    """每日收盘后给全市场补最近几日日线(全量 upsert 刷新最新交易日)."""
    return await _run_full_market(APPEND_DATALEN, only_missing=False)
```

- [ ] **Step 2: 导入与语法自检(不连网不连库)**

Run: `python -c "import ast; ast.parse(open('backend/services/fullmarket_klines.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

Run: `python -m pytest backend/tests/test_fullmarket_klines.py -v`
Expected: 仍 9 passed(追加异步代码未破坏纯函数与导入;`import httpx`/`repository` 在测试收集时不触发网络或 DB)。

- [ ] **Step 3: Commit**

```bash
git add backend/services/fullmarket_klines.py
git commit -m "feat(fullkline): async full-market fetch + backfill/append orchestrators"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 4: 一次性回填脚本

**Files:**
- Create: `backend/scripts/backfill_fullmarket_klines.py`

操作者在服务器上手动跑一次完成 5 年回填;断点续跑,重复运行只补缺口。仿照 `backend/scripts/bt_fetch_market.py` 的「独立脚本」定位。

- [ ] **Step 1: 建脚本**

`backend/scripts/backfill_fullmarket_klines.py`:

```python
"""一次性回填全市场近5年日线到 cfzy_sys_kline_cache(断点续跑).

服务器运行:
  cd /opt/trading-monitor && PYTHONPATH=. venv/bin/python backend/scripts/backfill_fullmarket_klines.py
重复运行只补缺口(已≥1000根的票跳过). 预计 5400 只, 视网络十几到几十分钟.
"""
import asyncio

from backend.models.database import init_db
from backend.services.fullmarket_klines import backfill_full_market


async def main():
    await init_db()
    res = await backfill_full_market()
    print("backfill done:", res)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 语法自检**

Run: `python -c "import ast; ast.parse(open('backend/scripts/backfill_fullmarket_klines.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/backfill_fullmarket_klines.py
git commit -m "feat(fullkline): one-off 5y full-market backfill script"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 5: 每日追加定时任务 + handler 接线

**Files:**
- Modify: `backend/models/database.py`(`migration_tasks` 追加一条种子)
- Modify: `backend/services/task_registry.py`(定义并注册 `fullmarket_kline_append` handler)

每日 17:00(避开 15:35 广度刷新、16:00 信号回填的高峰)给全市场补最近几日日线。零参 async handler,与 `rebuild_trade_rounds` 等同款注册进 `TASK_HANDLERS`。

- [ ] **Step 1: 注册定时任务种子**

在 `backend/models/database.py` 的 `migration_tasks` 列表(约 line 512 起,与其他 cron 任务并列)追加:

```python
            # v1.7.x: 全市场日线·每日追加 — 收盘后给全市场补最近几日日线(新浪源, 非东财), 保回测样本新鲜
            ("fullmarket_kline_append", "全市场日线·收盘追加17:00",
             "收盘后给全市场(剔北交所/ST/退市)补最近几日日线到 cfzy_sys_kline_cache, 保持回测样本新鲜",
             "cron", _json.dumps({"hour": 17, "minute": 0}), "fullmarket_kline_append"),
```

- [ ] **Step 2: 在 `task_registry.py` 定义并注册 handler**

打开 `backend/services/task_registry.py`。在文件顶部 import 区(与其他 `from backend.services... import` 并列)加:

```python
from backend.services.fullmarket_klines import append_full_market_daily
```

在定义其它零参 handler 的区域(参照 `rebuild_trade_rounds` 那段)加一个 handler 函数:

```python
async def fullmarket_kline_append():
    """收盘后给全市场补最近几日日线(保回测样本新鲜)。"""
    res = await append_full_market_daily()
    logger.info(f"[fullkline] 追加完成 {res}")
```

在 `TASK_HANDLERS` 字典里(与 `"rebuild_trade_rounds": rebuild_trade_rounds,` 并列)加一项:

```python
    "fullmarket_kline_append": fullmarket_kline_append,
```

- [ ] **Step 3: 验证 handler 已注册、可导入**

Run: `python -c "import ast; ast.parse(open('backend/models/database.py',encoding='utf-8').read()); ast.parse(open('backend/services/task_registry.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

Run: `python -c "from backend.services.task_registry import TASK_HANDLERS; print('fullmarket_kline_append' in TASK_HANDLERS and callable(TASK_HANDLERS['fullmarket_kline_append']))"`
Expected: `True`
(若 `python` 是 store stub 用 `py`;需在仓库根、PYTHONPATH 含根。)

- [ ] **Step 4: 全量测试无回归**

Run: `python -m pytest -q`
Expected: 全绿(report 末行,如 `83 passed`);不得引入新失败。

- [ ] **Step 5: Commit**

```bash
git add backend/models/database.py backend/services/task_registry.py
git commit -m "feat(fullkline): daily 17:00 full-market kline append task"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 6: changelog 版本记录(项目规范)

**Files:**
- Modify: `frontend/src/data/changelog.ts`(数组头部加一条)

- [ ] **Step 1: 确认当前最新版本号**

Run: `python -c "import re; s=open('frontend/src/data/changelog.ts',encoding='utf-8').read(); print(re.findall(r\"version: '(v[0-9.]+)'\", s)[0])"`
记下输出(如 `v1.7.310`),新版本号取其末位 +1(如 `v1.7.311`)。

- [ ] **Step 2: 在 `changelog.ts` 数组头部插入新版本**

在 `const changelog: VersionEntry[] = [` 之后、当前第一个 `{ version: ... }` 之前插入(版本号用 Step 1 得到的 +1):

```typescript
  {
    version: 'v1.7.311',
    date: '2026-06-05',
    title: '全市场日线回填+每日追加(放大回测样本)',
    changes: [
      { text: '新增全市场(剔北交所/ST/退市约5400只)近5年日线回填进 cfzy_sys_kline_cache: 一次性回填脚本 backfill_fullmarket_klines.py(断点续跑) + 每日17:00收盘追加定时任务, 数据走新浪源(非东财, 与线上模型同源不复权)。把买点回测样本从自选池放大到全市场。分时数据属下一期', tag: 'new' },
    ],
  },
```
(若 Step 1 显示的最新版不是 v1.7.310,则把这里的 `v1.7.311` 改成「实际最新版末位 +1」。)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/data/changelog.ts
git commit -m "docs(changelog): full-market daily klines"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Self-Review

**Spec coverage(对照 spec §9.1):**
- 复用 `cfzy_sys_kline_cache`、population 池→全市场 → Task 3 `_run_full_market` 写全市场 + Task 1 续跑计数。
- 历史回填 5 年、非东财源、分批限速、断点续跑、`INSERT IGNORE`/幂等 → Task 2/3(datalen=1300≈5年、新浪源、Semaphore=15、`only_missing` 跳过、`cache_klines` 的 `ON DUPLICATE KEY UPDATE`)+ Task 4 脚本。
- 每日追加任务 → Task 5(17:00 定时 + handler)。
- 收益:回测样本池→全市场 → changelog Task 6 记录。
- §9.3「环境重建只需成交票日线、不强依赖本流」与本计划无冲突;Plan 5(分时)、Plan 2(环境)明确不在此计划。无遗漏。

**Placeholder 扫描:** 无 TBD/TODO。Task 1 Step 2 / Task 5 Step 2 需在现有文件里「就近插入」,已给出参照锚点(`cache_klines` 导出行、`rebuild_trade_rounds` handler)与完整待插代码,非占位。Task 6 版本号给了「+1」规则与确认命令,非占位。

**类型一致性:** 纯函数签名 `_parse_sina_klines(text)->list[tuple]`、`_filter_symbols(rows)->list[str]`、`_needs_backfill(count,min)->bool` 在 Task 2 定义、Task 3 消费一致;K-line 元组 `(date,open,high,low,close,volume)` 与 `repository.cache_klines` 期望的 rows 形状(见 `signal_config.cache_klines`:`(code,*r)`)一致;`get_kline_counts()->dict[code,int]`(Task 1)被 Task 3 `counts.get(code,0)` 消费,key 为 `_normalize_code(sym)` 输出的 6 位 code,与 `cache_klines` 写入的 code 同口径;handler `fullmarket_kline_append` 为零参 async,与 `TASK_HANDLERS` 调用约定(`await handler()`,见 rebuild_trade_rounds)一致;种子 tuple 的 handler 字段字符串 `fullmarket_kline_append` 与注册 key 一致。
