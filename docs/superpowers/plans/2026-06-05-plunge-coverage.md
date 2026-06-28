# 退潮/风险信号补盲 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补两个今天暴露的风控盲区:(1) 大盘急跌探测从只盯上证扩到也盯创业板/科创;(2) 新增「强势退潮·赚钱效应消失」信号(昨日涨停股今日平均溢价转负)。

**Architecture:** 改 `plunge_detector.py` 的指数急跌判定为多指数(上证/创业板/科创取最狠);在 `market_ebb_detector.py` 新增 `detect_strength_ebb`(读情绪快照的 `yest_limit_up_premium`,≤阈值→推),注册成 5 分钟一跑的定时任务。核心判定都抽成纯函数,TDD 覆盖。

**Tech Stack:** Python / APScheduler / pytest。复用 `get_index_trends()`(返回 {code:{trends,pre_close,name}})、`repository.get_latest_emotion()`、`notifier.send_dual`。

**今日实测依据:** 上证 -0.74 / 创业板 -3.2 / 科创 -4.01;昨日涨停今日溢价 06-03 +2.99 → 06-04 +1.89 → 06-05 **-0.77**(翻负)。两个改动今天都会触发。

---

### Task 1: 指数急跌补盯创业板/科创(纯函数 + TDD)

**Files:**
- Modify: `backend/services/plunge_detector.py`
- Test: `backend/tests/test_plunge_index.py`

把单指数 `_check_index_drop` 换成两个纯函数:`_index_drop_pct(trends, window)`(算窗口内跌幅%)、`_worst_index_drop(cfg, index_trends)`(在上证/创业板/科创里挑跌得最狠且破阈值的,返回 alert 3 元组或 None)。再把 `detect_plunge` 里那一处单指数调用替换成 `_worst_index_drop`。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_plunge_index.py`:

```python
"""指数急跌多指数判定测试 — _index_drop_pct / _worst_index_drop."""
from backend.services.plunge_detector import _index_drop_pct, _worst_index_drop


def _trends(prices):
    return [{"price": p} for p in prices]


CFG = {"PLUNGE_INDEX": {"enabled": True, "time_window_min": 10, "drop_threshold_pct": 1.0}}


def _idx(trends, pre_close):
    return {"trends": trends, "pre_close": pre_close}


class TestIndexDropPct:
    def test_too_few_bars(self):
        assert _index_drop_pct(_trends([10] * 5), 10) is None

    def test_drop_pct(self):
        # 11 根, 窗口10: 首=100, 末=98 → -2%
        tr = _trends([100] + [100] * 8 + [99, 98])
        assert round(_index_drop_pct(tr, 10), 2) == -2.0


class TestWorstIndexDrop:
    def test_none_breaches(self):
        flat = _idx(_trends([100] * 11), 100)
        out = _worst_index_drop(CFG, {"sh000001": flat, "sz399006": flat, "sh000688": flat})
        assert out is None

    def test_chuangye_breaches_when_shanghai_flat(self):
        flat = _idx(_trends([100] * 11), 100)
        cyb = _idx(_trends([100] + [100] * 8 + [99, 98]), 101)   # -2% in window
        out = _worst_index_drop(CFG, {"sh000001": flat, "sz399006": cyb, "sh000688": flat})
        assert out is not None
        rule_id, name, parts = out
        assert rule_id == "PLUNGE_INDEX"
        assert "创业板指" in parts[0]

    def test_picks_worst_of_multiple(self):
        cyb = _idx(_trends([100] + [100] * 8 + [99, 98]), 100)    # -2%
        kc = _idx(_trends([100] + [100] * 8 + [97, 95]), 100)     # -5%
        out = _worst_index_drop(CFG, {"sh000001": _idx(_trends([100] * 11), 100),
                                      "sz399006": cyb, "sh000688": kc})
        assert "科创指数" in out[2][0]

    def test_disabled(self):
        cfg = {"PLUNGE_INDEX": {"enabled": False}}
        cyb = _idx(_trends([100] + [100] * 8 + [99, 98]), 100)
        assert _worst_index_drop(cfg, {"sz399006": cyb}) is None
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_plunge_index.py -v`
Expected: FAIL — `ImportError: cannot import name '_index_drop_pct'`
(若 `python` 是 store stub 返回 exit 49,用 `py`。)

- [ ] **Step 3: 改 `plunge_detector.py`**

把现有 `_check_index_drop` 函数(约 line 175-204)**整体替换**为下面两个函数:

```python
def _index_drop_pct(trends: list, window: int):
    """窗口内跌幅%(末价 vs window 根前的价). 数据不足/价格异常 → None."""
    if len(trends) < window + 1:
        return None
    recent = trends[-window:]
    p0 = recent[0]["price"]
    if p0 <= 0:
        return None
    return (trends[-1]["price"] - p0) / p0 * 100


def _worst_index_drop(cfg: dict, index_trends: dict):
    """上证/创业板/科创里挑跌得最狠且破阈值的, 返回 (rule_id, signal_name, parts) 或 None."""
    sc = cfg.get("PLUNGE_INDEX", {})
    if not sc.get("enabled", True):
        return None
    window = int(sc.get("time_window_min", 10))
    threshold = sc.get("drop_threshold_pct", 1.0)
    worst = None   # (drop_pct, name, total_drop)
    for code, name in (("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000688", "科创指数")):
        d = index_trends.get(code, {})
        trends = d.get("trends", [])
        pre_close = d.get("pre_close", 0)
        drop = _index_drop_pct(trends, window)
        if drop is None or drop >= -threshold:
            continue
        cur = trends[-1]["price"]
        total = (cur - pre_close) / pre_close * 100 if pre_close > 0 else 0
        if worst is None or drop < worst[0]:
            worst = (drop, name, total)
    if worst is None:
        return None
    drop, name, total = worst
    return (
        "PLUNGE_INDEX", "指数急跌",
        [f"{name} {window}分钟内跌幅 {drop:.2f}%", f"日内总跌幅 {total:.2f}%"],
    )
```

- [ ] **Step 4: 在 `detect_plunge` 里换用多指数**

在 `detect_plunge` 函数体内,把这一处(约 line 93-95):
```python
        alert = _check_index_drop(cfg, sh_trends, sh_pre_close, current_price)
        if alert:
            alerts.append(alert)
```
替换为:
```python
        alert = _worst_index_drop(cfg, index_trends)
        if alert:
            alerts.append(alert)
```
保留上面的 `sh_data/sh_trends/sh_pre_close/sh_name/current_price` 提取与 `if not current_price or not sh_pre_close: return` 守卫不变(仍用上证作主价/守卫,`index_trends` 是函数前面已取的 `get_index_trends()` 返回值)。`_check_breadth`/`_check_speed` 两处调用不动。

- [ ] **Step 5: 运行测试 + 全量回归**

Run: `python -m pytest backend/tests/test_plunge_index.py -v`
Expected: 6 passed。

Run: `python -m pytest -q`
Expected: 全绿,无新失败。

- [ ] **Step 6: Commit**

```bash
git add backend/services/plunge_detector.py backend/tests/test_plunge_index.py
git commit -m "feat(plunge): index-drop watches 上证/创业板/科创, alert on worst"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 2: 新增「强势退潮·赚钱效应消失」探测器(纯函数 + TDD)

**Files:**
- Modify: `backend/services/market_ebb_detector.py`
- Test: `backend/tests/test_strength_ebb.py`

新增纯函数 `_premium_ebb(prem, threshold)` + 异步 `detect_strength_ebb()`(读 `get_latest_emotion()` 的 `yest_limit_up_premium`,≤阈值且当日未推→推一条)。结构镜像同文件的 `detect_market_ebb`。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_strength_ebb.py`:

```python
"""强势退潮(打板赚钱效应转亏)阈值判定测试."""
from backend.services.market_ebb_detector import _premium_ebb, PREMIUM_EBB_THRESHOLD


class TestPremiumEbb:
    def test_none(self):
        assert _premium_ebb(None, -0.5) is False

    def test_above_threshold(self):
        assert _premium_ebb(1.89, -0.5) is False
        assert _premium_ebb(0.0, -0.5) is False

    def test_at_or_below_threshold(self):
        assert _premium_ebb(-0.5, -0.5) is True
        assert _premium_ebb(-0.77, -0.5) is True   # 今天 06-05

    def test_default_threshold_is_negative(self):
        assert PREMIUM_EBB_THRESHOLD < 0
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest backend/tests/test_strength_ebb.py -v`
Expected: FAIL — `ImportError: cannot import name '_premium_ebb'`

- [ ] **Step 3: 在 `market_ebb_detector.py` 末尾追加**

```python
# ── 强势退潮: 昨日涨停股今日平均溢价转负 = 打板/强势资金亏钱, 赚钱效应消失 ──
PREMIUM_EBB_THRESHOLD = -0.5     # yest_limit_up_premium ≤ 此值 → 退潮(今 -0.77 会触发, 可调)
_strength_alerted_date: str | None = None


def _premium_ebb(prem, threshold: float) -> bool:
    """昨日涨停今日溢价 ≤ 阈值 → 赚钱效应转亏。None 视为不触发。"""
    return prem is not None and prem <= threshold


async def detect_strength_ebb():
    global _strength_alerted_date
    if not is_trading_time():
        return
    now = datetime.now()
    if now.hour < 11:                 # 上午溢价未稳定
        return
    today = now.strftime("%Y-%m-%d")
    if _strength_alerted_date == today:
        return
    cur = await repository.get_latest_emotion()
    if not cur or str(cur.get("trade_date") or "")[:10] != today:
        return
    prem = cur.get("yest_limit_up_premium")
    if not _premium_ebb(prem, PREMIUM_EBB_THRESHOLD):
        return
    _strength_alerted_date = today
    text = (
        f"🌊 强势退潮·赚钱效应消失\n\n"
        f"昨日涨停股今日平均溢价 {prem:.2f}%(≤{PREMIUM_EBB_THRESHOLD}%),打板/强势资金转亏。\n"
        f"短线赚钱效应退潮 — 对手中强势/高位股谨慎,控制仓位与开新仓节奏。"
    )
    try:
        from backend.services import notifier
        await notifier.send_dual(text, lark_title="🌊 强势退潮·赚钱效应消失", template="red")
        logger.warning(f"[strength_ebb] 退潮提示已推送: 昨涨停今溢价 {prem:.2f}%")
    except Exception as e:
        logger.warning(f"[strength_ebb] 推送失败: {e}")
```
(`datetime`、`is_trading_time`、`repository`、`logger` 该文件顶部已 import,无需新增。)

- [ ] **Step 4: 运行测试 + 全量**

Run: `python -m pytest backend/tests/test_strength_ebb.py -v`
Expected: 4 passed。

Run: `python -m pytest -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/services/market_ebb_detector.py backend/tests/test_strength_ebb.py
git commit -m "feat(strength-ebb): alert when yesterday's limit-ups turn loss-making today"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 3: 强势退潮定时任务 + handler 接线

**Files:**
- Modify: `backend/models/database.py`(`migration_tasks` 追加种子)
- Modify: `backend/services/task_registry.py`(import + 注册 handler)

镜像 `detect_market_ebb` 的接线(interval 300s)。

- [ ] **Step 1: 注册定时任务种子**

在 `backend/models/database.py` 的 `migration_tasks` 列表里,`detect_market_ebb` 那条附近(`grep -n "detect_market_ebb" backend/models/database.py` 定位),追加:

```python
            # v1.7.x: 强势退潮 — 昨日涨停股今日平均溢价转负(打板亏钱), 盘中(≥11:00)推一次
            ("detect_strength_ebb", "强势退潮·赚钱效应消失",
             "盘中(≥11:00)每5分钟判昨日涨停股今日平均溢价是否≤阈值(打板/强势资金转亏), 是则推强势退潮提示, 每日一次",
             "interval", _json.dumps({"seconds": 300}), "detect_strength_ebb"),
```

- [ ] **Step 2: 注册 handler**

在 `backend/services/task_registry.py`:
- `grep -n "detect_market_ebb" backend/services/task_registry.py` 找到现有 import 与 `TASK_HANDLERS` 里的注册。
- 在那条 `from backend.services.market_ebb_detector import detect_market_ebb`(可能是 `import detect_market_ebb, detect_sector_ebb`)里**一并** import `detect_strength_ebb`。
- 在 `TASK_HANDLERS` 字典里,`"detect_market_ebb": detect_market_ebb,` 旁边加 `"detect_strength_ebb": detect_strength_ebb,`。

- [ ] **Step 3: 验证**

Run: `python -c "import ast; ast.parse(open('backend/models/database.py',encoding='utf-8').read()); ast.parse(open('backend/services/task_registry.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

Run: `python -c "from backend.services.task_registry import TASK_HANDLERS; print('detect_strength_ebb' in TASK_HANDLERS and callable(TASK_HANDLERS['detect_strength_ebb']))"`
Expected: `True`

Run: `python -m pytest -q`
Expected: 全绿。

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py backend/services/task_registry.py
git commit -m "feat(strength-ebb): register detect_strength_ebb scheduled task"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 4: changelog 版本记录

**Files:**
- Modify: `frontend/src/data/changelog.ts`

- [ ] **Step 1: 确认当前最新版本号**

Run: `python -c "import re; print(re.findall(r\"version: '(v[0-9.]+)'\", open('frontend/src/data/changelog.ts',encoding='utf-8').read())[0])"`
新版本号 = 末位 +1。

- [ ] **Step 2: 头部插入(版本号用 Step 1 +1)**

```typescript
  {
    version: 'v1.7.313',
    date: '2026-06-05',
    title: '退潮/风险信号补盲: 急跌补盯创业板科创 + 新增强势退潮',
    changes: [
      { text: '修复大盘急跌只盯上证的盲区: 指数急跌(PLUNGE_INDEX)改为同时盯上证/创业板/科创, 取跌得最狠的判, 推送注明是哪个指数。今天创业板-3.2%/科创-4%本被漏掉, 现可捕捉', tag: 'fix' },
      { text: '新增「强势退潮·赚钱效应消失」信号: 盘中(≥11:00)判昨日涨停股今日平均溢价≤阈值(打板/强势资金转亏)即推一次。捕捉"指数温和但热门股/龙头杀跌"这类广度指标不崩、情绪却退潮的分化日(今天昨日涨停今日溢价从前两日+1.9/+3.0翻负到-0.77)', tag: 'new' },
    ],
  },
```
(若 Step 1 最新版不是 v1.7.312,把 `v1.7.313` 改成实际 +1。)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/data/changelog.ts
git commit -m "docs(changelog): plunge index coverage + strength-ebb signal"
```
(commit body 末行: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Self-Review

**覆盖:** 第1条(急跌补盯创业板/科创)= Task 1;第2条(强势退潮/打板亏钱效应)= Task 2 + Task 3 接线。两条今天都会触发(创业板-3.2%/科创-4%;溢价-0.77≤-0.5)。

**Placeholder:** 无。替换点都给了原文锚点(`_check_index_drop` 约175-204、detect_plunge 约93-95、detect_market_ebb 接线)与完整新代码;changelog 给了 +1 规则。

**类型一致:** `_worst_index_drop(cfg, index_trends)->(rule_id,signal_name,parts)` 3 元组,与 detect_plunge 的 `alerts` 解包 `for rule_id, signal_name, detail_parts in alerts` 一致;`index_trends` 即 `get_index_trends()` 返回的 `{code:{trends,pre_close,name}}`,键 sh000001/sz399006/sh000688 已核实存在;`_premium_ebb(prem,threshold)->bool` 与 detector 消费一致;handler 名 `detect_strength_ebb` 字符串在 seed/注册/函数名三处一致。
