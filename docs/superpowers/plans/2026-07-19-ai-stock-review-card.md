# AI 个股研判卡（Phase 2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 点一只票 → 后端确定性汇总它的信号历史/同形态胜率/财务红旗/板块强弱/(若持仓)成本上下文成"事实清单"，LLM 只把清单写成一段"这票现在什么位置、风险点在哪"的大白话研判卡；按需触发、当日缓存、每人每日 200 次上限。

**Architecture:** 方案 A（厚后端+薄 LLM），复用 Phase 1 的 `ai_client.narrate`（唯一碰 LLM 处）。`stock_facts.build(...)` 纯函数（接收已 gather 好的各源数据、只做组装，不碰网络/DB），`stock_review.py` 做 async gather+narrate+缓存+限额。摆事实不预测，红线同 Phase 1。

**Tech Stack:** Python3.12/FastAPI/aiomysql，复用 `backend/services/ai_advisor/ai_client.py`，前端 Vue3+TS+naive-ui。

## Global Constraints

- 红线（逐字进 system prompt）：不预测涨跌、不喊买卖、不报目标价、不承诺胜率；LLM 只复述事实清单里的数字，禁止自己算数、禁止预测方向。同形态胜率是"历史客观分布"，措辞须是"历史上同类形态次日/3日涨跌分布"，不得说成"这票会涨/会跌"。
- LLM 只在 `ai_client.narrate` 一处出现；`stock_facts.build` 纯函数、可单测、不碰网络/DB。
- 缓存：按 `(user_id, code, gen_date)`；每人每日研判上限 200 次（`ai_advisor_daily_cap`，缺省 200）。
- 受 `ai_advisor_enabled` 总闸控制：关时 narrate 返回 None（facts 仍可返回，前端"AI 叙述暂不可用"）。
- 前端：`useGlobalMessage`（禁 naive `useMessage()`）；移动端 `useResponsive`/768/卡片化；loading 在 finally 复位；`changelog.ts` 头部加版本记录。
- 校验闸：后端 `pytest` 全过 + `import backend.main` OK + 前端 `vue-tsc && vite build` 过，再 commit。
- 非持仓票：成本上下文段留空，不报错。缺失数据源（如该票无财务红旗记录）→ 该段标"无记录"，不编造。
- 卡底部固定红线声明：`客观历史数据 + AI 归纳，非投资建议、不预测涨跌`。

---

### Task 1: `stock_facts` — 个股研判事实清单（纯函数）

**Files:**
- Create: `backend/services/ai_advisor/stock_facts.py`
- Test: `backend/tests/test_stock_facts.py`

**Interfaces:**
- Produces: `build_stock_facts(code, name, *, signals, winrate, fin_risk, sector, holding, near_buy) -> dict`
  纯函数。入参都是已 gather 好的原生数据（signals=该票信号历史 list；winrate=`get_model_winrate()` dict；fin_risk=`get_fin_risk(code)` dict|None；sector={board_strength,sector_rank,theme_heat 摘要}；holding=该票持仓 dict|None；near_buy=该票临近买点 dict|None）。返回：
  ```
  {"code","name",
   "signal_history":{"recent":[{signal_name,date,direction}],"n":int},
   "model_winrate":[{model_name,win_rate_3m,n_3m}],   # 该票历史触发过的买点模型的全市场同形态胜率
   "risk_flags":{"has_data":bool,"score":..,"flags":[...]} | {"has_data":False},
   "sector":{"board_strength":..,"sector_rank":..,"hot_themes":[...]},
   "holding":{"is_holding":bool,"cost":..,"float_pct":..,"entry_model":..} | {"is_holding":False},
   "near_buy":{"approaching":bool,"model":..,"gap_pct":..} | {"approaching":False}}
  ```

- [ ] **Step 1: 写失败测试**（覆盖：持仓票带成本段、非持仓票 holding.is_holding=False、无财务红旗时 has_data=False、同形态胜率按信号历史里出现过的 model_name 反查 winrate）
```python
# backend/tests/test_stock_facts.py
from backend.services.ai_advisor.stock_facts import build_stock_facts


def test_holding_context_present_when_held():
    f = build_stock_facts("300085", "银之杰",
        signals=[{"signal_name": "回踩MA10（右侧）", "trigger_date": "2026-07-01", "direction": "buy"}],
        winrate={"BUY_MA10": {"model_name": "回踩MA10", "win_rate_3m": 60.0, "n_3m": 100}},
        fin_risk=None, sector={"board_strength": 2, "sector_rank": 3, "theme_heat": []},
        holding={"cost": 30.0, "float_pct": 3.0, "entry_model": "回踩MA10"},
        near_buy=None)
    assert f["holding"]["is_holding"] is True
    assert f["holding"]["cost"] == 30.0
    assert f["risk_flags"]["has_data"] is False


def test_non_holding_and_no_risk_data():
    f = build_stock_facts("600000", "浦发银行",
        signals=[], winrate={}, fin_risk=None,
        sector={"board_strength": None, "sector_rank": None, "theme_heat": []},
        holding=None, near_buy=None)
    assert f["holding"]["is_holding"] is False
    assert f["risk_flags"]["has_data"] is False
    assert f["signal_history"]["n"] == 0


def test_model_winrate_backfilled_from_signal_history():
    f = build_stock_facts("300085", "银之杰",
        signals=[{"signal_name": "回踩MA10（右侧）", "trigger_date": "2026-07-01", "direction": "buy"}],
        winrate={"BUY_MA10": {"model_name": "回踩MA10", "win_rate_3m": 60.0, "n_3m": 100}},
        fin_risk=None, sector={"board_strength": None, "sector_rank": None, "theme_heat": []},
        holding=None, near_buy=None)
    names = [m["model_name"] for m in f["model_winrate"]]
    assert "回踩MA10" in names
```

- [ ] **Step 2: 跑测试确认失败** — Run: `python -m pytest backend/tests/test_stock_facts.py -v` → FAIL（未定义）
- [ ] **Step 3: 写实现**（纯函数；`signal_name` 归一去掉 `（左侧）/（右侧）` 后缀再撞 winrate 的 model_name，与 notifier `_signals_tables` 同口径）
```python
# backend/services/ai_advisor/stock_facts.py
"""个股研判事实清单构造器(纯函数, 不连库不碰LLM): 把已gather的各源数据组装成结构化真数字。
摆事实不预测: 同形态胜率是历史客观分布, 非涨跌预测。"""
import re

_SUFFIX = re.compile(r"（[左右]侧）$")


def _norm_model(name: str) -> str:
    return _SUFFIX.sub("", str(name or "")).strip()


def build_stock_facts(code, name, *, signals, winrate, fin_risk, sector, holding, near_buy) -> dict:
    sigs = signals or []
    recent = [{"signal_name": s.get("signal_name"), "date": str(s.get("trigger_date") or "")[:10],
               "direction": s.get("direction")} for s in sigs[:10]]
    # 该票历史出现过的买点模型 → 反查全市场同形态胜率
    mkt = {v.get("model_name"): v for v in (winrate or {}).values()}
    seen, models = set(), []
    for s in sigs:
        if s.get("direction") != "buy":
            continue
        mn = _norm_model(s.get("signal_name"))
        if mn and mn in mkt and mn not in seen:
            seen.add(mn)
            m = mkt[mn]
            models.append({"model_name": mn, "win_rate_3m": m.get("win_rate_3m"), "n_3m": m.get("n_3m")})

    if fin_risk:
        risk = {"has_data": True, "score": fin_risk.get("score"),
                "flags": fin_risk.get("flags") or fin_risk.get("flag_list") or []}
    else:
        risk = {"has_data": False}

    sec = sector or {}
    sector_out = {"board_strength": sec.get("board_strength"), "sector_rank": sec.get("sector_rank"),
                  "hot_themes": [t.get("theme") if isinstance(t, dict) else t for t in (sec.get("theme_heat") or [])][:5]}

    hold = {"is_holding": True, "cost": holding.get("cost"), "float_pct": holding.get("float_pct"),
            "entry_model": holding.get("entry_model")} if holding else {"is_holding": False}

    nb = {"approaching": True, "model": near_buy.get("model"), "gap_pct": near_buy.get("gap_pct")} \
        if near_buy else {"approaching": False}

    return {"code": code, "name": name,
            "signal_history": {"recent": recent, "n": len(sigs)},
            "model_winrate": models, "risk_flags": risk, "sector": sector_out,
            "holding": hold, "near_buy": nb}
```
- [ ] **Step 4: 跑测试确认通过** — Run: `python -m pytest backend/tests/test_stock_facts.py -v` → 3 passed
- [ ] **Step 5: 提交**
```bash
git add backend/services/ai_advisor/stock_facts.py backend/tests/test_stock_facts.py
git commit -m "feat(ai-advisor): 个股研判事实清单stock_facts纯函数 (Phase2 T1)"
```

---

### Task 2: `stock_review` — gather 各源 + narrate + 当日缓存 + 每日限额

**Files:**
- Create: `backend/services/ai_advisor/stock_review.py`
- Modify: `backend/models/database.py`（SCHEMA_STATEMENTS 加 `cfzy_biz_stock_review` 缓存表）
- Create: `backend/models/repo/stock_review.py`（缓存 CRUD + 当日计数）
- Modify: `backend/models/repository.py`（导出）
- Test: `backend/tests/test_stock_review.py`

**Interfaces:**
- Consumes: `repository` 的 `get_signals_by_code_since`、`get_model_winrate`、`get_fin_risk`、`get_sector_rotation`、`get_theme_heat`、`get_holdings_full_info`、`get_near_buy_snapshot`、`get_pool_row`；`stock_facts.build_stock_facts`；`ai_client.narrate`。
- Produces: `async generate_stock_review(user_id, code, *, use_cache=True) -> dict`
  返回 `{"facts","narrative","as_of","cached"}`。`async count_reviews_today(user_id) -> int`（供路由限额）。

**缓存表（加入 SCHEMA_STATEMENTS）：**
```sql
CREATE TABLE IF NOT EXISTS cfzy_biz_stock_review (
    user_id     INT NOT NULL,
    code        VARCHAR(10) NOT NULL,
    gen_date    DATE NOT NULL,
    facts_json  MEDIUMTEXT NOT NULL,
    narrative   MEDIUMTEXT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, code, gen_date)
)
```

- [ ] **Step 1: 写失败测试**（mock 各 gather 薄封装 + narrate；验证组装、LLM 失败降级仍返回 facts、当日计数）
```python
# backend/tests/test_stock_review.py
import backend.services.ai_advisor.stock_review as sr


async def test_generate_assembles_and_degrades(monkeypatch):
    monkeypatch.setattr(sr, "_gather", lambda uid, code: {
        "name": "银之杰", "signals": [], "winrate": {}, "fin_risk": None,
        "sector": {"board_strength": None, "sector_rank": None, "theme_heat": []},
        "holding": None, "near_buy": None})
    async def none_narrate(sys, facts, **k): return None
    monkeypatch.setattr(sr.ai_client, "narrate", none_narrate)
    monkeypatch.setattr(sr, "_get_cached", lambda *a: None)
    monkeypatch.setattr(sr, "_save_cache", lambda *a: None)
    out = await sr.generate_stock_review(1, "300085", use_cache=False)
    assert out["narrative"] is None
    assert out["facts"]["code"] == "300085"
```
（`_gather` 做成可 monkeypatch 的薄封装：内部 `asyncio.gather` 拉 7 个源，返回 dict；其中 holding/near_buy 从 user 维度结果里按 code 取。）

- [ ] **Step 2-4**: 跑失败 → 写实现（system prompt 复用 Phase 1 红线口吻，强调"同形态胜率=历史分布非预测"；`_gather` 用 `asyncio.gather` 并发拉源，单源失败不拖垮整体：各源包 try/except 降级为 None/空；narrate 失败→facts 照常）→ 跑通。`stock_review.py` 结构镜像 `trade_coach.py`（`_gather`/`_get_cached`/`_save_cache`/`_maybe_await` 薄封装 + `generate_stock_review`）。`repo/stock_review.py` 提供 `save_stock_review`/`get_stock_review`/`count_reviews_today(user_id)`（`SELECT COUNT(*) FROM cfzy_biz_stock_review WHERE user_id=%s AND gen_date=CURDATE()`）。database.py 加表；repository.py 加导出。
- [ ] **Step 5: 提交** — `feat(ai-advisor): stock_review组装层(gather并发+缓存+当日计数) (Phase2 T2)`；`python -c "import backend.main"` 须过。

---

### Task 3: 路由 `GET /api/stock/{code}/review` + 每日限额

**Files:**
- Create: `backend/routers/stock_review.py`
- Modify: `backend/main.py`（include_router）
- Test: `backend/tests/test_stock_review_route.py`

**Interfaces:**
- `GET /api/stock/{code}/review` 挂 `get_current_user`；先查 `stock_review.count_reviews_today(user["id"])`，`>= ai_advisor_daily_cap(缺省200)` → 返回 429（"今日研判次数已达上限"）；否则 `generate_stock_review(user["id"], code)`。

- [ ] **Step 1: 写失败测试** — 用 dependency_overrides 覆盖 get_current_user；mock generate_stock_review + count_reviews_today；两个用例：正常 200 返回 facts；计数≥上限 → 429。
- [ ] **Step 2-4**: 跑失败→写实现（读 `load_config().get("ai_advisor_daily_cap", 200)`）→跑通。
```python
# backend/routers/stock_review.py 关键逻辑
n = await stock_review.count_reviews_today(user["id"])
cap = load_config().get("ai_advisor_daily_cap", 200)
if n >= cap:
    raise HTTPException(status_code=429, detail="今日研判次数已达上限")
return await stock_review.generate_stock_review(user["id"], code)
```
（注意：缓存命中不应占额度——`count_reviews_today` 数的是当日已落缓存的不同票数；命中同票同日直接返回缓存、不新增计数。实现时 generate 内 cache 命中分支不写库即不增计数，天然成立。）
- [ ] **Step 5: 提交** — `feat(ai-advisor): 个股研判路由+每日限额 GET /api/stock/{code}/review (Phase2 T3)`

---

### Task 4: 前端研判卡

**Files:**
- Create: `frontend/src/api/stockReview.ts`
- Create: `frontend/src/components/stock/StockReviewCard.vue`（或复用现有 stock 详情弹窗接入按钮）
- Modify: 股票池/个股操作处加"AI 研判"按钮（探明现有 StockTable/StockList 行操作后接入）
- Modify: `frontend/src/data/changelog.ts`（版本记录）

**Interfaces:** Consumes `GET /api/stock/{code}/review`。

- [ ] **Step 1**: `api/stockReview.ts` 封装（照 `api/coach.ts` 用 `import client from './client'`）。
- [ ] **Step 2**: `StockReviewCard.vue`——顶部事实速览（信号历史/同形态胜率/财务红旗/板块名次/持仓成本，结构化，null 渲染成"—"/"无记录"）+ 中间 narrative（无则"AI 叙述暂不可用（数据仍完整）"）+ 底部固定红线声明。`useGlobalMessage` 报错，429 时提示"今日研判次数已达上限"，loading finally 复位，`useResponsive` 移动端卡片化。
- [ ] **Step 3**: 股票池行/个股详情加"AI 研判"按钮（探明现有组件结构后接入，弹出 StockReviewCard）；`changelog.ts` 加版本记录（tag: 'new'，"AI 个股研判卡：点一只票，把它的信号历史/同形态胜率/财务红旗/板块强弱/持仓成本综合成一段大白话研判，摆事实不预测"）。
- [ ] **Step 4: 校验闸** — `cd frontend && npm run build`（vue-tsc 零错误+build 成功）。
- [ ] **Step 5: 提交** — `feat(ai-advisor): 前端个股研判卡(按需+移动端) (Phase2 T4)`

---

## 部署验证（全部任务后）
- [ ] 后端 `pytest -q --ignore=backend/tests/test_bt_follow.py` 全过；`import backend.main` OK；前端 build 过
- [ ] 部署；service active + 首页200 + `/api/stock/600000/review` 匿名401 + 迁移建 `cfzy_biz_stock_review`
- [ ] 与 Phase1 共用 `ai_advisor_enabled` 总闸：关时 narrate 返回 None、facts 照常（研判卡是用户按需触发, 不像周推有"静默自动"问题, 故按需路径不额外加总闸, facts-only 可看）

## Self-Review
- Spec §4 覆盖：五源采集(T1 组装 + T2 gather)/按需触发(T3 路由)/当日缓存+每人200/天上限(T2+T3)/卡片展示+红线声明(T4)/复用 ai_client(既有)。
- 复用 Phase1 `ai_client.narrate`，不重复造 LLM 封装。
- null/缺源全链路处理：stock_facts 各段 has_data/is_holding/approaching 标志 → 前端渲染"—"/"无记录"。
- 待实现者核对：`get_fin_risk` 返回 dict 的实际字段名(score/flags)、`get_holdings_full_info` 返回结构里取 code 的成本/浮盈字段名、股票池行操作组件的真实结构（T4 探明后接）。
