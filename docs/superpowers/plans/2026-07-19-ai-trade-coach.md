# AI 交易教练（Phase 1：共享 ai_client + 交易教练）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给交易者一份基于其真实交割单/回合数据、由后端确定性算数、LLM 只写人话的"私人交易复盘"，支持每周日自动推送 + 页面按需生成。

**Architecture:** 方案 A（厚后端+薄 LLM）。后端 `coach_facts` 纯函数把交易回合算成结构化"事实清单"（数字全真），`ai_client` 是唯一碰 LLM 的封装（provider 可配），`trade_coach` 组装 facts→narrate→结果。LLM 失败则只缺叙述、事实清单照常。

**Tech Stack:** Python 3.12 / FastAPI / aiomysql / 现有 ai_analyst 的 OpenAI 兼容客户端(DeepSeek) + anthropic SDK(Claude) / Vue3+TS+naive-ui。

## Global Constraints

- 红线（与官网一致，逐字进 system prompt）：不预测涨跌、不喊买卖点、不报目标价、不承诺胜率；LLM 只复述事实清单里的数字，禁止自己算数。
- 数字全由后端算；LLM 只在 `ai_client.narrate` 一处出现。
- 弹前端消息用 `useGlobalMessage`，禁 naive `useMessage()`。
- 所有前端改动同步做移动端（768 断点 / useResponsive / 宽表卡片化）。
- 改逻辑必须在 `frontend/src/data/changelog.ts` 头部加版本记录。
- 上线走校验闸：后端 `pytest` 全过 + `import backend.main` OK + 前端 `vue-tsc && vite build` 全过，再 commit/部署。
- 推送走 `send_wechat_signal` 统一入口 + 偏好闸门 + 交易日历，不新造通道。
- config 改动先与用户确认再写生产 config.json。

---

### Task 1: `ai_client` — provider 可配的 LLM 叙述封装

**Files:**
- Create: `backend/services/ai_advisor/__init__.py`（空）
- Create: `backend/services/ai_advisor/ai_client.py`
- Test: `backend/tests/test_ai_advisor_client.py`

**Interfaces:**
- Consumes: `backend.core.config.load_config`（读 `ai_advisor_provider` / `anthropic_api_key` / `ai_base_url` / `ai_model`）
- Produces: `async narrate(system_prompt: str, fact_sheet: dict, *, max_tokens: int = 4096) -> str | None`
  返回 LLM 生成的中文叙述；失败/超时/空/未启用 → `None`。`fact_sheet` 以 `json.dumps(ensure_ascii=False)` 作为 user 消息。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_ai_advisor_client.py
import backend.services.ai_advisor.ai_client as ac


async def test_narrate_returns_none_when_llm_empty(monkeypatch):
    monkeypatch.setattr(ac, "_call_provider", lambda *a, **k: "")   # 模拟空返回
    out = await ac.narrate("sys", {"a": 1})
    assert out is None


async def test_narrate_passes_factsheet_as_json(monkeypatch):
    captured = {}
    def fake_call(provider, model, system_prompt, user_content, max_tokens):
        captured["user"] = user_content
        captured["sys"] = system_prompt
        return "这是一段复盘。" * 10   # >100 字
    monkeypatch.setattr(ac, "_call_provider", fake_call)
    out = await ac.narrate("守红线", {"追高占比": 0.6})
    assert out and "复盘" in out
    assert "追高占比" in captured["user"]      # 事实清单以中文 JSON 进 prompt
    assert captured["sys"] == "守红线"


async def test_narrate_none_on_exception(monkeypatch):
    def boom(*a, **k): raise RuntimeError("api down")
    monkeypatch.setattr(ac, "_call_provider", boom)
    assert await ac.narrate("s", {}) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_ai_advisor_client.py -v`
Expected: FAIL（`ai_client` 模块不存在 / `narrate` 未定义）

- [ ] **Step 3: 写最小实现**

```python
# backend/services/ai_advisor/ai_client.py
"""统一 LLM 叙述封装 —— 两个 AI 顾问功能唯一碰模型的地方(方案A: LLM只写人话)。
provider 从 config.ai_advisor_provider 选(deepseek/claude); 失败/空/未配 → None, 上层降级。"""
import asyncio
import json
import logging

from backend.core.config import load_config

logger = logging.getLogger(__name__)


def _call_provider(provider: str, model: str, system_prompt: str, user_content: str,
                   max_tokens: int) -> str:
    """同步调具体 provider, 返回文本。deepseek 走 OpenAI 兼容, claude 走 anthropic。仅本模块内部用。"""
    cfg = load_config()
    api_key = cfg.get("anthropic_api_key", "") if provider == "claude" else cfg.get("anthropic_api_key", "")
    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.get("anthropic_api_key", ""),
                                     base_url=cfg.get("ai_base_url") or None)
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    # deepseek / OpenAI 兼容(复用 ai_analyst 现有做法)
    from openai import OpenAI
    client = OpenAI(api_key=cfg.get("anthropic_api_key", ""),
                    base_url=cfg.get("ai_base_url", "https://api.deepseek.com/v1"))
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_content}],
    )
    return resp.choices[0].message.content or ""


async def narrate(system_prompt: str, fact_sheet: dict, *, max_tokens: int = 4096) -> str | None:
    """把事实清单(dict)交给 LLM 写成中文叙述。返回文本; 失败/空/过短 → None(上层只缺叙述不瘫)。"""
    cfg = load_config()
    if not cfg.get("ai_advisor_enabled", False):
        return None
    provider = cfg.get("ai_advisor_provider", "deepseek")
    model = cfg.get("ai_model", "deepseek-chat")
    user_content = json.dumps(fact_sheet, ensure_ascii=False, default=str)
    try:
        text = await asyncio.to_thread(
            _call_provider, provider, model, system_prompt, user_content, max_tokens)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ai_advisor] narrate 调用失败({provider}): {e}")
        return None
    text = (text or "").strip()
    if len(text) < 100:
        logger.warning(f"[ai_advisor] narrate 返回过短({len(text)}字), 视作失败")
        return None
    return text
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_ai_advisor_client.py -v`
Expected: PASS（3 passed）。注: 测试 monkeypatch 掉 `_call_provider` 且需先让 `ai_advisor_enabled` 为真——若默认关，测试里 `monkeypatch.setattr(ac, "load_config", lambda: {"ai_advisor_enabled": True, "ai_advisor_provider": "deepseek", "ai_model": "x"})`。补进每个测试。

- [ ] **Step 5: 提交**

```bash
git add backend/services/ai_advisor/__init__.py backend/services/ai_advisor/ai_client.py backend/tests/test_ai_advisor_client.py
git commit -m "feat(ai-advisor): provider可配的LLM叙述封装ai_client (Phase1 T1)"
```

---

### Task 2: `coach_facts` — 交易复盘事实清单（纯函数，四类指标）

**Files:**
- Create: `backend/services/ai_advisor/coach_facts.py`
- Test: `backend/tests/test_coach_facts.py`

**Interfaces:**
- Consumes: 回合 dict 列表（形如 `cfzy_biz_trade_rounds` 行：`realized_pnl_pct`、`holding_days`、`entry_model_name`、`entry_deviation_pct`、`exit_reason`、`status`、`close_date`、`realized_pnl`）；模型胜率 dict（`{signal_id: {model_name, win_rate_3m, n_3m}}`）。
- Produces: `build_coach_facts(rounds: list[dict], winrate: dict, start: str, end: str) -> dict`
  纯函数、不连库不碰网络。返回结构化事实清单：
  ```
  {
    "window": {"start","end"}, "n_closed": int,
    "listen_vs_self": {"listen": {"n","win_rate","avg_pnl_pct"}, "self": {...}},
    "by_model": [{"model_name","n","win_rate","avg_pnl_pct","market_win_rate_3m","exec_gap"}],
    "cycle": {"hold_days_avg","winner_hold_avg","loser_hold_avg","pnl_dist":{...}},
    "habits": {"loser_hold_avg","winner_hold_avg","stop_discipline":{...},"scaled_out_ratio":...},
  }
  ```

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_coach_facts.py
from backend.services.ai_advisor.coach_facts import build_coach_facts


def _r(pnl_pct, hold, model=None, dev=None, status="closed", pnl=0.0):
    return {"realized_pnl_pct": pnl_pct, "holding_days": hold, "entry_model_name": model,
            "entry_deviation_pct": dev, "exit_reason": None, "status": status,
            "close_date": "2026-07-10", "realized_pnl": pnl}


def test_listen_vs_self_split_and_winrate():
    rounds = [
        _r(5.0, 3, model="回踩MA10"), _r(-2.0, 8, model="回踩MA10"),   # 听模型: 1胜1负=50%
        _r(-4.0, 12, model=None), _r(-1.0, 20, model=None), _r(3.0, 2, model=None),  # 自作主张: 1胜2负≈33%
    ]
    f = build_coach_facts(rounds, {}, "2026-06-01", "2026-07-10")
    assert f["listen_vs_self"]["listen"]["n"] == 2
    assert f["listen_vs_self"]["listen"]["win_rate"] == 50.0
    assert f["listen_vs_self"]["self"]["n"] == 3
    assert round(f["listen_vs_self"]["self"]["win_rate"], 1) == 33.3


def test_by_model_exec_gap_vs_market():
    rounds = [_r(6.0, 3, model="缩量突破"), _r(4.0, 4, model="缩量突破")]  # 实盘100%
    winrate = {"BUY_VOL_BREAKOUT": {"model_name": "缩量突破", "win_rate_3m": 65.0, "n_3m": 200}}
    f = build_coach_facts(rounds, winrate, "2026-06-01", "2026-07-10")
    m = next(x for x in f["by_model"] if x["model_name"] == "缩量突破")
    assert m["n"] == 2 and m["win_rate"] == 100.0
    assert m["market_win_rate_3m"] == 65.0
    assert m["exec_gap"] == 35.0    # 实盘胜率 - 全市场回测胜率


def test_winner_vs_loser_hold_days():
    rounds = [_r(10.0, 2), _r(8.0, 3), _r(-5.0, 15), _r(-3.0, 25)]
    f = build_coach_facts(rounds, {}, "s", "e")
    assert f["cycle"]["winner_hold_avg"] == 2.5   # 赢家平均持 (2+3)/2
    assert f["cycle"]["loser_hold_avg"] == 20.0   # 输家平均扛 (15+25)/2


def test_open_rounds_excluded_from_closed_stats():
    rounds = [_r(5.0, 3, status="closed"), _r(0.0, 0, status="open")]
    f = build_coach_facts(rounds, {}, "s", "e")
    assert f["n_closed"] == 1


def test_empty_rounds_safe():
    f = build_coach_facts([], {}, "s", "e")
    assert f["n_closed"] == 0 and f["by_model"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_coach_facts.py -v`
Expected: FAIL（`build_coach_facts` 未定义）

- [ ] **Step 3: 写实现**

```python
# backend/services/ai_advisor/coach_facts.py
"""交易复盘事实清单构造器(纯函数, 不连库不碰LLM): 把交易回合算成结构化真数字, 交给 ai_client 写人话。
四类: 听模型vs自作主张 / 按买点模型归因成绩 / 盈亏持仓周期 / 买卖习惯坏毛病。"""
from collections import defaultdict


def _rate(wins: int, n: int) -> float:
    return round(wins / n * 100, 1) if n else 0.0


def _avg(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def _closed(rounds: list[dict]) -> list[dict]:
    return [r for r in rounds if str(r.get("status")) == "closed" and r.get("realized_pnl_pct") is not None]


def build_coach_facts(rounds: list[dict], winrate: dict, start: str, end: str) -> dict:
    cl = _closed(rounds)
    # 名称→全市场近3月胜率(model_winrate 值以 signal_id 为键, 取 model_name 反查)
    mkt = {v.get("model_name"): v for v in (winrate or {}).values()}

    # 1) 听模型 vs 自作主张
    def group_stats(rs):
        pcts = [float(r["realized_pnl_pct"]) for r in rs]
        return {"n": len(rs), "win_rate": _rate(sum(1 for p in pcts if p > 0), len(rs)),
                "avg_pnl_pct": _avg(pcts)}
    listen = [r for r in cl if r.get("entry_model_name")]
    myself = [r for r in cl if not r.get("entry_model_name")]

    # 2) 按买点模型归因
    by_model = []
    grp = defaultdict(list)
    for r in listen:
        grp[r["entry_model_name"]].append(float(r["realized_pnl_pct"]))
    for name, pcts in sorted(grp.items(), key=lambda kv: -len(kv[1])):
        m = mkt.get(name) or {}
        wr = _rate(sum(1 for p in pcts if p > 0), len(pcts))
        mwr = m.get("win_rate_3m")
        by_model.append({
            "model_name": name, "n": len(pcts), "win_rate": wr, "avg_pnl_pct": _avg(pcts),
            "market_win_rate_3m": mwr,
            "exec_gap": round(wr - mwr, 1) if mwr is not None else None,
        })

    # 3) 盈亏/持仓周期
    winners = [r for r in cl if float(r["realized_pnl_pct"]) > 0]
    losers = [r for r in cl if float(r["realized_pnl_pct"]) <= 0]
    hold = [int(r["holding_days"]) for r in cl if r.get("holding_days") is not None]
    cycle = {
        "hold_days_avg": _avg([float(h) for h in hold]),
        "winner_hold_avg": _avg([float(r["holding_days"]) for r in winners if r.get("holding_days") is not None]),
        "loser_hold_avg": _avg([float(r["holding_days"]) for r in losers if r.get("holding_days") is not None]),
        "pnl_dist": {
            "best_pct": round(max((float(r["realized_pnl_pct"]) for r in cl), default=0.0), 2),
            "worst_pct": round(min((float(r["realized_pnl_pct"]) for r in cl), default=0.0), 2),
            "avg_pct": _avg([float(r["realized_pnl_pct"]) for r in cl]),
        },
    }

    # 4) 习惯坏毛病(能从回合列直接算的先做; 追高/卖飞需 leg/K线, Phase1.5 补, 见 spec 开放点)
    habits = {
        "winner_hold_avg": cycle["winner_hold_avg"],
        "loser_hold_avg": cycle["loser_hold_avg"],
        "loser_holds_longer": cycle["loser_hold_avg"] > cycle["winner_hold_avg"],  # 输家扛更久=手输家倾向
        "scaled_out_ratio": _rate(sum(1 for r in cl if r.get("is_scaled_out")), len(cl)),
        "stop_loss_rounds": _rate(
            sum(1 for r in cl if str(r.get("exit_reason") or "").startswith("SELL_") and float(r["realized_pnl_pct"]) < 0),
            len(cl)),
    }

    return {
        "window": {"start": start, "end": end},
        "n_closed": len(cl),
        "listen_vs_self": {"listen": group_stats(listen), "self": group_stats(myself)},
        "by_model": by_model,
        "cycle": cycle,
        "habits": habits,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_coach_facts.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/services/ai_advisor/coach_facts.py backend/tests/test_coach_facts.py
git commit -m "feat(ai-advisor): 交易复盘事实清单coach_facts纯函数 (Phase1 T2)"
```

---

### Task 3: `trade_coach` — 组装(取数→facts→narrate→结果) + 缓存表

**Files:**
- Create: `backend/services/ai_advisor/trade_coach.py`
- Modify: `backend/models/database.py`（SCHEMA_STATEMENTS 加缓存表 `cfzy_biz_coach_report`）
- Create: `backend/models/repo/coach_report.py`（缓存 CRUD）
- Modify: `backend/models/repository.py`（导出缓存 CRUD）
- Test: `backend/tests/test_trade_coach.py`

**Interfaces:**
- Consumes: `trade_rounds.get_rounds(user_id)`、`repository.get_model_winrate()`、`coach_facts.build_coach_facts`、`ai_client.narrate`
- Produces: `async generate_coach_report(user_id: int, start: str, end: str, *, use_cache: bool = True) -> dict`
  返回 `{"facts": <事实清单>, "narrative": <str|None>, "as_of": <date>, "cached": bool}`。LLM 失败时 `narrative=None`、`facts` 照常。

**缓存表 schema（加入 `SCHEMA_STATEMENTS`）：**
```sql
CREATE TABLE IF NOT EXISTS cfzy_biz_coach_report (
    user_id     INT NOT NULL,
    period_key  VARCHAR(40) NOT NULL,   -- f"{start}~{end}"
    gen_date    DATE NOT NULL,          -- 生成当天(同用户+同区间+同天命中缓存)
    facts_json  MEDIUMTEXT NOT NULL,
    narrative   MEDIUMTEXT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, period_key, gen_date)
)
```

- [ ] **Step 1: 写失败测试**（mock 取数与 narrate，验证组装 + LLM 失败降级）

```python
# backend/tests/test_trade_coach.py
import backend.services.ai_advisor.trade_coach as tc


async def test_generate_assembles_facts_and_narrative(monkeypatch):
    monkeypatch.setattr(tc, "_load_rounds", lambda uid: [
        {"status": "closed", "realized_pnl_pct": 5.0, "holding_days": 3,
         "entry_model_name": "回踩MA10", "entry_deviation_pct": 0.0, "exit_reason": None}])
    monkeypatch.setattr(tc, "_load_winrate", lambda: {})
    async def fake_narrate(sys, facts, **k): return "复盘正文" * 30
    monkeypatch.setattr(tc.ai_client, "narrate", fake_narrate)
    monkeypatch.setattr(tc, "_get_cached", lambda *a: None)
    monkeypatch.setattr(tc, "_save_cache", lambda *a: None)
    out = await tc.generate_coach_report(1, "2026-06-01", "2026-07-10", use_cache=False)
    assert out["facts"]["n_closed"] == 1
    assert out["narrative"] and "复盘" in out["narrative"]


async def test_llm_failure_still_returns_facts(monkeypatch):
    monkeypatch.setattr(tc, "_load_rounds", lambda uid: [])
    monkeypatch.setattr(tc, "_load_winrate", lambda: {})
    async def none_narrate(sys, facts, **k): return None
    monkeypatch.setattr(tc.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(tc, "_get_cached", lambda *a: None)
    monkeypatch.setattr(tc, "_save_cache", lambda *a: None)
    out = await tc.generate_coach_report(1, "s", "e", use_cache=False)
    assert out["narrative"] is None
    assert "facts" in out and out["facts"]["n_closed"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_trade_coach.py -v`
Expected: FAIL（`trade_coach` 未定义）

- [ ] **Step 3: 写实现**（含红线 system prompt；`_load_rounds`/`_load_winrate`/`_get_cached`/`_save_cache` 为可被 monkeypatch 的薄封装）

```python
# backend/services/ai_advisor/trade_coach.py
"""交易教练组装层: 取数→coach_facts→ai_client.narrate→结果(+当日缓存)。LLM失败仅缺叙述。"""
import json
import logging
from datetime import date

from backend.models import repository
from backend.models.repo import trade_rounds
from backend.services.ai_advisor import ai_client, coach_facts

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是一名A股交易复盘助手。下面给你一份该用户真实交易的【事实清单】(JSON, 所有数字均已由系统算好)。\n"
    "严格要求: 只能复述清单里已有的数字和事实; 禁止自己计算或推算任何数字; 禁止预测涨跌方向; "
    "禁止给出买入/卖出/加仓/减仓建议; 禁止承诺或暗示胜率会如何。\n"
    "任务: 用中文、大白话、简明地把清单里的规律讲给用户听, 指出其交易习惯的客观特征(如输家比赢家扛得更久、"
    "某买点模型实盘执行得比全市场回测差多少), 只陈述事实与客观倾向, 不做投资建议。结尾不加免责声明(前端已固定)。"
)


async def _load_rounds(user_id: int):
    return await trade_rounds.get_rounds(user_id)

async def _load_winrate():
    return await repository.get_model_winrate()

async def _get_cached(user_id, period_key, gen_date):
    return await repository.get_coach_report(user_id, period_key, gen_date)

async def _save_cache(user_id, period_key, gen_date, facts, narrative):
    await repository.save_coach_report(user_id, period_key, gen_date, facts, narrative)


async def generate_coach_report(user_id: int, start: str, end: str, *, use_cache: bool = True) -> dict:
    period_key = f"{start}~{end}"
    today = date.today()
    if use_cache:
        row = await _get_cached(user_id, period_key, today)
        if row:
            return {"facts": json.loads(row["facts_json"]), "narrative": row.get("narrative"),
                    "as_of": str(today), "cached": True}
    rounds = await _load_rounds(user_id)
    winrate = await _load_winrate()
    facts = coach_facts.build_coach_facts(rounds, winrate, start, end)
    narrative = await ai_client.narrate(_SYSTEM_PROMPT, facts)
    try:
        await _save_cache(user_id, period_key, today, facts, narrative)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ai_advisor] 复盘缓存写入失败(忽略): {e}")
    return {"facts": facts, "narrative": narrative, "as_of": str(today), "cached": False}
```

`coach_report.py` 缓存 CRUD：

```python
# backend/models/repo/coach_report.py
import json
from backend.models.repo._db import _execute, _fetchone


async def save_coach_report(user_id, period_key, gen_date, facts: dict, narrative):
    await _execute(
        "INSERT INTO cfzy_biz_coach_report (user_id, period_key, gen_date, facts_json, narrative) "
        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE facts_json=VALUES(facts_json), "
        "narrative=VALUES(narrative), created_at=CURRENT_TIMESTAMP",
        (user_id, period_key, gen_date, json.dumps(facts, ensure_ascii=False, default=str), narrative),
    )


async def get_coach_report(user_id, period_key, gen_date):
    return await _fetchone(
        "SELECT facts_json, narrative FROM cfzy_biz_coach_report "
        "WHERE user_id=%s AND period_key=%s AND gen_date=%s", (user_id, period_key, gen_date))
```

在 `repository.py` 加：`from backend.models.repo.coach_report import save_coach_report, get_coach_report  # noqa: F401`
在 `database.py` 的 `SCHEMA_STATEMENTS` 加上面的 `CREATE TABLE cfzy_biz_coach_report`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_trade_coach.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: import 冒烟 + 提交**

Run: `python -c "import backend.main; print('OK')"`
```bash
git add backend/services/ai_advisor/trade_coach.py backend/models/repo/coach_report.py backend/models/repository.py backend/models/database.py backend/tests/test_trade_coach.py
git commit -m "feat(ai-advisor): trade_coach组装层+复盘缓存表 (Phase1 T3)"
```

---

### Task 4: 后端路由 — 页面按需生成复盘

**Files:**
- Create: `backend/routers/coach.py`
- Modify: `backend/main.py`（`app.include_router(coach.router)`）
- Test: `backend/tests/test_coach_route.py`

**Interfaces:**
- Consumes: `trade_coach.generate_coach_report`、`get_current_user`
- Produces: `GET /api/coach/report?start=YYYY-MM-DD&end=YYYY-MM-DD` → `{facts, narrative, as_of, cached}`（鉴权, user 取自 token）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_coach_route.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
import backend.routers.coach as coach_router


def _client(monkeypatch):
    async def fake_gen(user_id, start, end, **k):
        return {"facts": {"n_closed": 3}, "narrative": "x"*120, "as_of": "2026-07-19", "cached": False}
    monkeypatch.setattr(coach_router.trade_coach, "generate_coach_report", fake_gen)
    async def fake_user(): return {"id": 1, "username": "u", "role": "user"}
    app = FastAPI(); app.include_router(coach_router.router)
    app.dependency_overrides[coach_router.get_current_user] = fake_user
    return TestClient(app)


def test_report_requires_dates_defaults_ok(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/api/coach/report?start=2026-06-01&end=2026-07-19")
    assert r.status_code == 200
    assert r.json()["facts"]["n_closed"] == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_coach_route.py -v`
Expected: FAIL（`backend.routers.coach` 不存在）

- [ ] **Step 3: 写实现**

```python
# backend/routers/coach.py
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from backend.core.auth import get_current_user
from backend.services.ai_advisor import trade_coach

router = APIRouter(prefix="/api/coach", tags=["coach"])


@router.get("/report")
async def get_report(
    user: Annotated[dict, Depends(get_current_user)],
    start: str = Query(...), end: str = Query(...),
):
    return await trade_coach.generate_coach_report(user["id"], start, end)
```

在 `backend/main.py` 顶部 `from backend.routers import ... coach` 并 `app.include_router(coach.router)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_coach_route.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/routers/coach.py backend/main.py backend/tests/test_coach_route.py
git commit -m "feat(ai-advisor): 交易复盘按需生成路由 GET /api/coach/report (Phase1 T4)"
```

---

### Task 5: 每周日定时推送

**Files:**
- Modify: `backend/services/ai_advisor/trade_coach.py`（加 `run_trade_coach_weekly`）
- Modify: `backend/services/task_registry.py`（import + 注册 handler `run_trade_coach_weekly`）
- Modify: `backend/models/database.py`（`_seed_scheduled_tasks` / `migration_tasks` 加 seed 行）
- Test: `backend/tests/test_coach_weekly.py`

**Interfaces:**
- Consumes: `repository.list_users`、`generate_coach_report`、`send_wechat_signal`（现有推送统一入口）、`trading_calendar.is_workday`（周日照跑，用户拍板周日晚）
- Produces: `async run_trade_coach_weekly()` — 遍历用户, 生成近一月复盘, 走推送闸门发一张卡。
- 调度时间: **周日 22:30**（避开 19:00/20:00/21:00 现有重任务；`schedule_config={"day_of_week":"sun","hour":22,"minute":30}`）。

- [ ] **Step 1: 写失败测试**（mock 用户列表、generate、发送，断言每个用户各发一次）

```python
# backend/tests/test_coach_weekly.py
import backend.services.ai_advisor.trade_coach as tc


async def test_weekly_pushes_per_user(monkeypatch):
    sent = []
    async def fake_users(): return [{"id": 1}, {"id": 2}]
    async def fake_gen(uid, s, e, **k): return {"facts": {"n_closed": 2}, "narrative": "n"*120, "as_of": "x", "cached": False}
    async def fake_send(*a, **k): sent.append(a)
    monkeypatch.setattr(tc.repository, "list_users", fake_users)
    monkeypatch.setattr(tc, "generate_coach_report", fake_gen)
    monkeypatch.setattr(tc, "_send_coach_card", fake_send)
    await tc.run_trade_coach_weekly()
    assert len(sent) == 2


async def test_weekly_skips_user_with_no_closed_rounds(monkeypatch):
    sent = []
    async def fake_users(): return [{"id": 1}]
    async def fake_gen(uid, s, e, **k): return {"facts": {"n_closed": 0}, "narrative": None, "as_of": "x", "cached": False}
    async def fake_send(*a, **k): sent.append(a)
    monkeypatch.setattr(tc.repository, "list_users", fake_users)
    monkeypatch.setattr(tc, "generate_coach_report", fake_gen)
    monkeypatch.setattr(tc, "_send_coach_card", fake_send)
    await tc.run_trade_coach_weekly()
    assert sent == []   # 本周无平仓回合不打扰
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_coach_weekly.py -v`
Expected: FAIL（`run_trade_coach_weekly` 未定义）

- [ ] **Step 3: 写实现**（加到 `trade_coach.py`）

```python
# 追加到 backend/services/ai_advisor/trade_coach.py
from datetime import timedelta


async def _send_coach_card(user_id: int, report: dict):
    """把复盘事实清单+叙述组装成飞书卡, 走现有推送统一入口(偏好闸门在入口内)。"""
    from backend.services.notifier import send_wechat_signal   # 复用统一入口
    facts, narrative = report["facts"], report.get("narrative")
    lines = [f"📋 本月交易复盘（{report['as_of']}）", f"已平仓 {facts['n_closed']} 笔"]
    lvs = facts["listen_vs_self"]
    lines.append(f"听模型 {lvs['listen']['n']}笔 胜率{lvs['listen']['win_rate']}% / "
                 f"自作主张 {lvs['self']['n']}笔 胜率{lvs['self']['win_rate']}%")
    if narrative:
        lines.append("")
        lines.append(narrative)
    lines.append("")
    lines.append("客观历史数据 + AI 归纳，非投资建议、不预测涨跌")
    await send_wechat_signal(user_id, "交易复盘", "\n".join(lines))


async def run_trade_coach_weekly():
    """每周日晚: 给每个有平仓回合的用户生成近一月复盘并推送。无平仓不打扰。"""
    users = await repository.list_users()
    if not users:
        return
    end = date.today()
    start = end - timedelta(days=30)
    for u in users:
        try:
            report = await generate_coach_report(u["id"], str(start), str(end))
            if report["facts"].get("n_closed", 0) <= 0:
                continue
            await _send_coach_card(u["id"], report)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ai_advisor] 周复盘推送失败 user={u.get('id')}: {e}")
```

在 `task_registry.py`：`from backend.services.ai_advisor.trade_coach import run_trade_coach_weekly` + `TASK_HANDLERS["run_trade_coach_weekly"] = run_trade_coach_weekly`。
在 `database.py` 的 seed 任务列表加一行：`("trade_coach_weekly", "交易复盘·周日22:30", "每周日22:30给有平仓回合的用户生成近一月AI交易复盘并推送(听模型对比/模型归因/盈亏周期/习惯)", "cron", _json.dumps({"day_of_week":"sun","hour":22,"minute":30}), "run_trade_coach_weekly")`。

> 注意：确认 `send_wechat_signal` 的真实签名（参数顺序）后再定 `_send_coach_card` 的调用；若签名不同按实际调整，保持"走统一入口+偏好闸门"不变。

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `python -m pytest backend/tests/test_coach_weekly.py -v && python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/ai_advisor/trade_coach.py backend/services/task_registry.py backend/models/database.py backend/tests/test_coach_weekly.py
git commit -m "feat(ai-advisor): 交易复盘每周日22:30定时推送 (Phase1 T5)"
```

---

### Task 6: 前端"交易复盘"页

**Files:**
- Create: `frontend/src/views/TradeCoachView.vue`
- Create: `frontend/src/api/coach.ts`
- Modify: `frontend/src/router/index.ts`（加路由 `/trade-coach`）
- Modify: `frontend/src/components/AppSidebar.vue` + `AppTabBar.vue`（"策略绩效"组加入口）
- Modify: `frontend/src/data/changelog.ts`（版本记录）

**Interfaces:**
- Consumes: `GET /api/coach/report`
- Produces: 页面——时间段选择 + 生成按钮 → 事实清单结构化展示（听模型对比表 / 按模型成绩表 / 盈亏周期）+ LLM 叙述段 + 底部红线声明。

- [ ] **Step 1: api 封装**

```typescript
// frontend/src/api/coach.ts
import { request } from './client'
export interface CoachReport { facts: any; narrative: string | null; as_of: string; cached: boolean }
export function getCoachReport(start: string, end: string) {
  return request.get<CoachReport>('/api/coach/report', { params: { start, end } })
}
```

- [ ] **Step 2: 页面组件**（用 `useGlobalMessage` 报错；`useResponsive` 做移动端；宽表移动端卡片化；loading 用 finally 复位）

写 `TradeCoachView.vue`：默认区间近一月，`NDatePicker` 选区间 + 生成按钮；调 `getCoachReport`；把 `facts.listen_vs_self`/`by_model`/`cycle`/`habits` 用 `NDataTable`（桌面）/卡片（移动 `isMobile`）展示；`narrative` 存在则渲染在下方，不存在显示"AI 叙述暂不可用（数据仍完整）"；底部固定 `客观历史数据 + AI 归纳，非投资建议、不预测涨跌`。错误走 `useGlobalMessage().error`，`loading` 在 `finally` 复位。

- [ ] **Step 3: 路由 + 菜单 + changelog**

`router/index.ts` 加 `{ path: '/trade-coach', component: () => import('../views/TradeCoachView.vue') }`；`AppSidebar.vue`/`AppTabBar.vue` 的"策略绩效"组加入口；`changelog.ts` 头部加版本记录（tag: 'new'，说明"AI 交易复盘：按你真实交割单算成绩规律+AI 大白话复盘，每周日自动推一份"）。

- [ ] **Step 4: 校验闸**

Run: `cd frontend && npm run build`
Expected: vue-tsc 零错误 + build 成功

- [ ] **Step 5: 提交**

```bash
git add frontend/src/views/TradeCoachView.vue frontend/src/api/coach.ts frontend/src/router/index.ts frontend/src/components/AppSidebar.vue frontend/src/components/AppTabBar.vue frontend/src/data/changelog.ts
git commit -m "feat(ai-advisor): 前端交易复盘页(页面按需+移动端) (Phase1 T6)"
```

---

## 部署验证（全部任务后）

- [ ] 后端 `python -m pytest -q` 全过；`python -c "import backend.main"` OK
- [ ] 前端 `npm run build` 过
- [ ] 与用户确认后在生产 config.json 配 `ai_advisor_enabled`/`ai_advisor_provider`（改前备份）
- [ ] `deploy.ps1` 部署；验证 service active + 首页200 + `/api/coach/report` 鉴权401(匿名) + 迁移建了 `cfzy_biz_coach_report`
- [ ] 登录态手动打一次 `/api/coach/report` 看事实清单结构 + 叙述（若 ai_advisor 已开）

## Self-Review 结论

- **Spec 覆盖**：ai_client(T1)/coach 四类事实(T2)/组装+缓存(T3)/按需路由(T4)/周日推送(T5)/前端页(T6)+红线护栏(T1 prompt+T5/T6 声明)+错误处理(T3/T5 降级)+测试(每任务)+配置项(T1 读)。研判卡属 Phase 2，另出计划——本计划不含（符合分阶段）。
- **开放点**（spec §10）：coach_facts 的"追高%/卖飞窗口"需 leg/K线，Task 2 只做了能从回合列直接算的（听模型/归因/周期/扛单/止损纪律/卖半比例），追高与卖飞留 Phase 1.5，已在 T2 注释标注，不阻塞本阶段可用。
- **类型一致**：`generate_coach_report`/`build_coach_facts`/`narrate` 签名跨任务一致；缓存表键 `(user_id, period_key, gen_date)` 与 repo/组装层一致。
- **待实现者核对**：`send_wechat_signal` 真实签名（T5 已标注按实际调整）。
