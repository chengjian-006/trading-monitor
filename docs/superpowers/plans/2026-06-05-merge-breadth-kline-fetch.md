# 合并广度刷新与全市场日线追加(单趟抓取) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把全市场日线落库折叠进每日 15:35 的广度刷新那一趟抓取,删掉独立的 17:00 追加任务,从源头把每日全市场新浪请求量从两趟(~1万次)降到一趟(~5千次)。

**Architecture:** `market_breadth_refresher` 的逐只抓取从「只取 close」升级为「抓全 OHLCV」。抓取扇出保持**只抓不写**(沿用现状的 pool 安全特性),抓完后用受限并发(信号量 8 < 连接池 maxsize 10)的独立批次统一把 K 线 upsert 进 `cfzy_sys_kline_cache`。广度计算抽成纯函数、逻辑与阈值完全不变(测试锁定)。独立的 `fullmarket_kline_append` 定时任务停用(移除 seed + 对存量 prod 行置 enabled=0)。

**Tech Stack:** Python 3 / httpx(异步, trust_env=False)/ aiomysql / pytest。复用 `backend/services/fullmarket_klines.py` 的 `_parse_sina_klines` 与 `repository.cache_klines`。

**Scope 边界(不含):** 不改广度阈值/口径;不删 `backfill_full_market`/`append_full_market_daily`(保留作手动兜底);不动一次性回填脚本。

---

### Task 1: 抽出纯函数 `_breadth_from_closes`(锁定广度口径)+ 测试

**Files:**
- Modify: `backend/services/market_breadth_refresher.py`(新增纯函数)
- Test: `backend/tests/test_market_breadth.py`

把「一堆个股收盘序列 → 站上 MA20/MA10/MA60 比例」的计算从 `refresh_market_breadth` 里抽成纯函数,口径与现状逐行一致(`c >= sum(closes[-N:])/N`,样本要求 `len(closes) >= 20`,MA10/MA60 各自要求足够长度)。重构后广度数值不变,由测试保证。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_market_breadth.py`:

```python
"""全市场广度纯函数测试 — _breadth_from_closes 口径锁定."""
from backend.services.market_breadth_refresher import _breadth_from_closes


def _seq(n, val):
    return [float(val)] * n


class TestBreadthFromCloses:
    def test_empty(self):
        assert _breadth_from_closes([]) == {
            "ma20_ratio": 0.0, "ma10_ratio": 0.0, "ma60_ratio": 0.0, "total": 0}

    def test_too_short_excluded(self):
        # 少于20根不计入 total
        out = _breadth_from_closes([_seq(19, 10)])
        assert out["total"] == 0

    def test_all_above_all_mas(self):
        # 60根全10, 最后一根抬到100 → 站上 MA20/MA10/MA60
        closes = _seq(60, 10) + [100.0]
        out = _breadth_from_closes([closes])
        assert out["total"] == 1
        assert out["ma20_ratio"] == 100.0
        assert out["ma10_ratio"] == 100.0
        assert out["ma60_ratio"] == 100.0

    def test_below_all_mas(self):
        closes = _seq(60, 100) + [1.0]   # 最后一根砸到1, 低于各均线
        out = _breadth_from_closes([closes])
        assert out["total"] == 1
        assert out["ma20_ratio"] == 0.0
        assert out["ma10_ratio"] == 0.0
        assert out["ma60_ratio"] == 0.0

    def test_mixed_ratio_and_ma60_sample_subset(self):
        # 两只: A 有60根且站上MA60; B 只有25根(算MA20但不算MA60)
        a = _seq(60, 10) + [100.0]            # 站上全部
        b = _seq(25, 10) + [100.0]            # 站上MA20/MA10, 不计入MA60分母
        out = _breadth_from_closes([a, b])
        assert out["total"] == 2
        assert out["ma20_ratio"] == 100.0     # 2/2 站上MA20
        # MA60: 只有 A 够长且站上 → 分母只看够长的? 见实现: 与现状一致(总分母=total)
        # 现状口径: 分子按"够长且站上"计, 分母统一用 total. A站上, B不够长不计分子.
        assert out["ma60_ratio"] == 50.0      # 1/2
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_market_breadth.py -v`
Expected: FAIL — `ImportError: cannot import name '_breadth_from_closes'`
(若 `python` 是 Microsoft Store stub 返回 exit 49,用 `py -m pytest ...`。)

- [ ] **Step 3: 在 `market_breadth_refresher.py` 实现纯函数**

在 `backend/services/market_breadth_refresher.py` 顶部(`refresh_market_breadth` 之前)加入。口径**严格照搬**现有 `refresh_market_breadth` 里的累加逻辑(分子按"序列够长且收盘≥对应均线"计数,分母统一用有效样本 `total`,有效样本=至少 20 根):

```python
def _breadth_from_closes(closes_list: list[list]) -> dict:
    """一组个股收盘序列 → 站上 MA20/MA10/MA60 比例(%) 与有效样本数.

    口径与历史一致: 有效样本=长度≥20 的序列; 分子按"够长且收盘≥该均线"计, 分母统一用有效样本数.
    """
    a20 = a10 = a60 = tot = 0
    for closes in closes_list:
        if not closes or len(closes) < 20:
            continue
        tot += 1
        c = closes[-1]
        if c >= sum(closes[-20:]) / 20:
            a20 += 1
        if len(closes) >= 10 and c >= sum(closes[-10:]) / 10:
            a10 += 1
        if len(closes) >= 60 and c >= sum(closes[-60:]) / 60:
            a60 += 1
    if tot == 0:
        return {"ma20_ratio": 0.0, "ma10_ratio": 0.0, "ma60_ratio": 0.0, "total": 0}
    return {
        "ma20_ratio": round(a20 / tot * 100, 2),
        "ma10_ratio": round(a10 / tot * 100, 2),
        "ma60_ratio": round(a60 / tot * 100, 2),
        "total": tot,
    }
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest backend/tests/test_market_breadth.py -v`
Expected: 5 passed。

- [ ] **Step 5: Commit**

```bash
git add backend/services/market_breadth_refresher.py backend/tests/test_market_breadth.py
git commit -m "refactor(breadth): extract _breadth_from_closes pure function with tests"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 2: 广度抓取改抓全 OHLCV + 顺带落库 K 线(单趟)

**Files:**
- Modify: `backend/services/market_breadth_refresher.py`

把 `_fetch_closes` 升级为抓全 OHLCV 的 `_fetch_one`(复用 `fullmarket_klines._parse_sina_klines`),返回 `(sym, rows)`。`refresh_market_breadth`:抓取扇出保持只抓不写;抓完后(1)用纯函数 `_breadth_from_closes` 算广度并存库(口径不变),(2)再用受限并发(信号量 8)的独立批次把每只票的 K 线 upsert 进 `cfzy_sys_kline_cache`。

- [ ] **Step 1: 顶部 import 复用解析与落库**

在 `backend/services/market_breadth_refresher.py` 顶部 import 区(`from backend.models import repository` 旁)追加:

```python
from backend.fetcher.codes import _normalize_code
from backend.services.fullmarket_klines import _parse_sina_klines
```

- [ ] **Step 2: 用 `_fetch_one` 替换 `_fetch_closes`**

把现有 `_fetch_closes` 函数整体替换为(抓全 OHLCV,返回 `(sym, rows)`;rows 为 `[(date,open,high,low,close,volume), ...]`):

```python
async def _fetch_one(client, sym, sem):
    """抓单只全 OHLCV(datalen=_DATALEN). 返回 (sym, rows); 失败/过短返回 (sym, [])."""
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={_DATALEN}")
    async with sem:
        try:
            r = await client.get(url, headers=_HEADERS)
            rows = _parse_sina_klines(r.text)
            return sym, (rows if len(rows) >= 20 else [])
        except Exception:
            return sym, []
```

- [ ] **Step 3: 改写 `refresh_market_breadth` 主体**

把 `refresh_market_breadth` 里「`results = await asyncio.gather(...)` 到写库」这段,替换为下面这版(抓取仍并发 `_CONCURRENCY`,只抓不写;广度用纯函数;再加一个并发 8 的落库批次)。保留函数开头的 client 构造与 `symbols = await _fetch_all_symbols(client)`、空列表早退不变;`finally: await client.aclose()` 不变。

```python
        sem = asyncio.Semaphore(_CONCURRENCY)
        fetched = await asyncio.gather(*[_fetch_one(client, s, sem) for s in symbols])

        # 广度: 口径不变, 用纯函数
        closes_list = [[row[4] for row in rows] for _, rows in fetched if rows]
        last_date = None
        for _, rows in fetched:
            if rows:
                last_date = rows[-1][0] or last_date
        b = _breadth_from_closes(closes_list)
        if b["total"] == 0:
            logger.warning("[breadth] 有效样本0, 跳过写库")
            return
        await repository.save_market_breadth(
            last_date, b["ma20_ratio"], b["ma10_ratio"], b["ma60_ratio"], b["total"])
        logger.info(f"[breadth] {last_date}: 站上MA20 {b['ma20_ratio']}% "
                    f"(MA10 {b['ma10_ratio']}% / MA60 {b['ma60_ratio']}%), 样本{b['total']}")

        # 顺带把全市场日线落库(替代原独立17:00任务). 写库受限并发8 < 连接池10, 不抢实盘查询.
        wsem = asyncio.Semaphore(8)
        kok = 0

        async def _persist(sym, rows):
            nonlocal kok
            if not rows:
                return
            async with wsem:
                try:
                    await repository.cache_klines(_normalize_code(sym), rows)
                    kok += 1
                except Exception as e:
                    logger.debug(f"[breadth] K线落库失败 {sym}: {e}")

        await asyncio.gather(*[_persist(sym, rows) for sym, rows in fetched])
        logger.info(f"[breadth] 顺带落库全市场日线 {kok} 只")
```

注意:这段替换掉原来从 `sem = asyncio.Semaphore(_CONCURRENCY)` 到 `logger.info(f"[breadth] {last_date}: ...")` 的全部内容(原来用 `_fetch_closes` + 手写 a20/a10/a60 累加 + 写库的那一整段)。原 `a20=a10=a60=tot=0` 累加循环、`ma20r/ma10r/ma60r` 计算、原 `save_market_breadth` 调用都删除,由上面新逻辑取代。

- [ ] **Step 4: 语法 + 回归测试**

Run: `python -c "import ast; ast.parse(open('backend/services/market_breadth_refresher.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

Run: `python -m pytest backend/tests/test_market_breadth.py backend/tests/test_fullmarket_klines.py -v`
Expected: 全通过(广度纯函数 5 + 全市场纯函数 9);import 改动未破坏收集。

Run: `python -m pytest -q`
Expected: 全绿,无新失败。

- [ ] **Step 5: Commit**

```bash
git add backend/services/market_breadth_refresher.py
git commit -m "feat(breadth): fetch full OHLCV once and persist klines in the 15:35 sweep"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 3: 停用独立的 17:00 全市场日线追加任务

**Files:**
- Modify: `backend/models/database.py`(移除 seed tuple + 对存量行置 enabled=0)

新装环境不再种该任务;已存在的 prod 行用 UPDATE 停用(沿用 `sector_leader` 同款写法)。handler `fullmarket_kline_append` 与 `append_full_market_daily` 函数保留(手动兜底/未来复用),不删。

- [ ] **Step 1: 移除 `migration_tasks` 里的 seed tuple**

在 `backend/models/database.py` 删除 Plan 4 加的这条(用 `grep -n "fullmarket_kline_append" backend/models/database.py` 定位;删除整条 tuple 含其上方注释行):

```python
            # v1.7.x: 全市场日线·每日追加 — 收盘后给全市场补最近几日日线(新浪源, 非东财), 保回测样本新鲜
            ("fullmarket_kline_append", "全市场日线·收盘追加17:00",
             "收盘后给全市场(剔北交所/ST/退市)补最近几日日线到 cfzy_sys_kline_cache, 保持回测样本新鲜",
             "cron", _json.dumps({"hour": 17, "minute": 0}), "fullmarket_kline_append"),
```

- [ ] **Step 2: 对存量 prod 行加停用 UPDATE**

在 `_run_migrations` 里 `sector_leader` 停用那段(`grep -n "sector_leader" backend/models/database.py` 定位的 `UPDATE cfzy_sys_scheduled_tasks SET enabled = 0` 块)之后,照同款加一段:

```python
        # v1.7.x: 广度刷新已合并全市场日线落库, 停用独立的 17:00 追加任务(存量环境)
        try:
            await cur.execute(
                "UPDATE cfzy_sys_scheduled_tasks SET enabled = 0 WHERE job_id = %s",
                ("fullmarket_kline_append",),
            )
        except Exception:
            pass
```

- [ ] **Step 3: 语法自检 + 全量测试**

Run: `python -c "import ast; ast.parse(open('backend/models/database.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

Run: `python -m pytest -q`
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py
git commit -m "chore(fullkline): retire standalone 17:00 append task (merged into 15:35 breadth sweep)"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 4: changelog 版本记录

**Files:**
- Modify: `frontend/src/data/changelog.ts`

- [ ] **Step 1: 确认当前最新版本号**

Run: `python -c "import re; print(re.findall(r\"version: '(v[0-9.]+)'\", open('frontend/src/data/changelog.ts',encoding='utf-8').read())[0])"`
新版本号取其末位 +1。

- [ ] **Step 2: 数组头部插入新版本**

在 `const changelog: VersionEntry[] = [` 之后、当前第一个 `{ version: ... }` 之前插入(版本号用 Step 1 +1):

```typescript
  {
    version: 'v1.7.312',
    date: '2026-06-05',
    title: '全市场日线落库并入广度刷新(单趟抓取, 砍半新浪请求)',
    changes: [
      { text: '把全市场日线 upsert 折叠进每日15:35的广度刷新那一趟抓取(原来广度只取收盘、日线另起17:00一趟, 同源同IP两趟约1万次请求): 广度抓取改抓全OHLCV、抓完顺带落库(写库并发限8<连接池, 不抢实盘查询), 广度口径抽纯函数不变; 停用独立17:00任务。每日全市场新浪请求量减半', tag: 'improve' },
    ],
  },
```
(若 Step 1 最新版不是 v1.7.311,把 `v1.7.312` 改成「实际最新版末位 +1」。)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/data/changelog.ts
git commit -m "docs(changelog): merge full-market kline persistence into breadth sweep"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Self-Review

**目标覆盖:** 单趟抓取 = Task 2(广度抓取抓全OHLCV+顺带落库)+ Task 3(停17:00任务);广度口径不漂 = Task 1 纯函数+测试 + Task 2 复用它;请求量减半 = 删一趟全市场抓取。pool 安全 = Task 2 写库批次信号量8<池10、且抓取扇出仍只抓不写。无遗漏。

**Placeholder 扫描:** 无 TBD;Task 2/3 的"替换/删除"段都给了被替换的原文锚点与完整新代码;Task 4 版本号给了 +1 规则与确认命令。非占位。

**类型一致:** `_breadth_from_closes(list[list])->dict{ma20_ratio,ma10_ratio,ma60_ratio,total}`(Task1 定义、Task2 消费一致);`_fetch_one(client,sym,sem)->(sym, rows)`、rows=`[(date,open,high,low,close,volume),...]`(来自 `_parse_sina_klines`,与 `repository.cache_klines` 期望的 rows 一致);`_normalize_code(sym)` 产 6 位 code 与缓存读写同口径;停用任务的 job_id 字符串 `fullmarket_kline_append` 与 Plan 4 所建一致。
