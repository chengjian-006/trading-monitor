# 股票池自定义预警 — 设计文档

- 日期: 2026-06-17
- 状态: 待评审
- 关联记忆: [[near-buy-panel]] (临近买点榜/贴线带思路), [[indicator-audit-0609]] (推送结构化机制/send_dual_card), [[app-framework]] (多租户隔离思路)

## 1. 目标

在「股票池」页面为池中股票增加**自定义预警**:用户可对单只股票设置一条或多条预警,每条预警可由多个条件做 AND 组合;满足时按用户独立渠道推送(企微+飞书),并在站内标记。多用户场景,数据与推送均按 `user_id` 隔离。

### 非目标 (YAGNI)
- 不做条件之间的 OR (多条预警天然就是"任一满足各自触发"=OR 的效果,无需组内 OR)。
- 不做用户 webhook 配置 UI (已存在 `/api/users/profile`,直接复用)。
- 不做"一键全部重启"入口,只逐条手动重启。
- 不做相对买入成本口径的涨跌幅 (仅当日涨跌幅)。

## 2. 需求确认 (来自 brainstorming)

| 维度 | 决策 |
|---|---|
| 条件组合 | 一只股票可挂多条预警;每条预警内部多条件 AND |
| 预警维度 | 价格 / 当日涨跌幅 / 接近均线(±带) / 上穿·跌破均线 |
| 通知方式 | 企微+飞书推送 + 站内标记,二者都要 |
| 触发频次 | 一次性,触发后该条置为 triggered 失效,需手动重启 |
| 检测频率/时段 | 跟现有股票池扫描同节奏,仅交易时段 |
| 行情来源 | 复用现有实时行情缓存 + kline_cache 均线 |
| 多用户 | 数据按 user_id 隔离;推送发到各用户自己的 webhook |

## 3. 数据模型

新建表 `cfzy_biz_stock_alerts` (在 `backend/models/database.py` 的建表 SQL + MIGRATION_STATEMENTS 同步)。

```sql
CREATE TABLE IF NOT EXISTS cfzy_biz_stock_alerts (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    user_id           INT NOT NULL,
    code              VARCHAR(16) NOT NULL,
    note              VARCHAR(100) NULL,           -- 备注, 如"回踩接回"
    conditions        JSON NOT NULL,               -- 条件数组, 全部满足(AND)才触发
    enabled           TINYINT NOT NULL DEFAULT 1,  -- 用户开关
    status            VARCHAR(12) NOT NULL DEFAULT 'active',  -- active / triggered
    last_triggered_at DATETIME NULL,
    triggered_price   DECIMAL(10,3) NULL,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_code (user_id, code),
    INDEX idx_scan (enabled, status)
);
```

### conditions JSON 结构

`conditions` 是一个数组,数组内所有条件**全部满足**才触发(AND)。每个元素:

```jsonc
// 价格
{ "dim": "price", "op": "gte" | "lte", "value": 15.20 }

// 当日涨跌幅 (%)
{ "dim": "pct", "op": "gte" | "lte", "value": 7 }     // 例: 涨幅>=7%  或  <=-5%

// 接近均线: 现价进入 MA(ma)±band% 带内
{ "dim": "ma_near", "ma": 5 | 10 | 20 | 60, "band": 2 }

// 上穿/跌破均线
{ "dim": "ma_cross", "ma": 5 | 10 | 20 | 60, "dir": "up" | "down" }
```

约束:
- `conditions` 至少 1 项;后端校验 dim/op/dir 取值与数值范围(band 0.1~10, ma ∈ {5,10,20,60})。
- 同一条预警内不强制各维度唯一(允许 price 区间用两条 price 条件做 AND)。

## 4. 检测逻辑

新增 `backend/services/custom_alert_scanner.py`,导出 `check_custom_alerts()`。

挂载方式: 注册为独立 handler `check_custom_alerts` 到 `task_registry.py` 的 `TASK_HANDLERS`,在 `_seed_scheduled_tasks()` / `database.py` 种子任务里以与 `scan_stock_pool` 相同的调度(interval 同 `scan_interval_seconds`)启用;仅交易时段执行(沿用现有交易时段判断工具/守卫,非交易时段直接 return)。

### 流程

```
def check_custom_alerts():
    if not 交易时段: return
    rows = repo.list_active_alerts()          # enabled=1 AND status='active', 全用户
    if not rows: return
    # 按 code 去重取行情, 减少重复取价
    codes = {r.code for r in rows}
    quotes = 行情缓存批量取现价/涨跌幅(codes)   # 复用 refresh_quotes 落地的实时缓存
    triggered_by_user = defaultdict(list)
    for r in rows:
        q = quotes.get(r.code)
        if q is None or 停牌/无现价: continue
        if not eval_conditions(r.conditions, q, r.code): continue
        repo.mark_triggered(r.id, q.price)     # status=triggered, last_triggered_at, triggered_price
        triggered_by_user[r.user_id].append((r, q))
    for user_id, items in triggered_by_user.items():
        push_user_alerts(user_id, items)       # 逐用户聚合一张卡, 发该用户 webhook
```

### eval_conditions(conditions, quote, code)

- `price`: 比较 `quote.price` 与 value (gte/lte)。
- `pct`: 比较 `quote.pct_change`(当日涨跌幅%) 与 value。
- `ma_near`: 取该 code 近 N-1 根 kline_cache 日收盘 + 现价作最新点算 `MA_N`;命中条件 `abs(price - MA_N) / MA_N * 100 <= band`。
- `ma_cross`: 设 `prev = 昨收`, `cur = 现价`, 线 `MA_N` (同上口径)。`dir=up` 命中 `prev < MA_N <= cur`;`dir=down` 命中 `prev > MA_N >= cur`。
- 任一条件所需数据缺失(均线 bar 不足 / 无昨收 / 无现价) → 该**条件判为不满足** → 整条预警不触发 (绝不误触发)。
- 所有条件为真 → 返回 True。

### 推送 push_user_alerts(user_id, items)

- 读取该用户 webhook (沿用 `send_wechat_signal` 466-483 行的"读用户级 webhook,缺省回退全局"模式)。
- 飞书: 用 `send_dual_card` 的飞书原生表格 (schema 2.0),列: 股票 / 触发条件 / 现价 / 涨跌幅 / 时间;企微: 文本回退。
- 改造 `backend/services/notifier.py` 的 `send_dual_card`: 增加可选关键字参数 `wecom_webhook` / `wecom_on` / `lark_webhook` / `lark_on`;不传时维持读全局 config(向后兼容,现有调用不受影响)。
- 触发条件文案: 把 conditions 翻成中文,如 "价格≥15.20 且 涨幅≥7%"、"接近MA10(±2%)"、"上穿MA20"。

## 5. 后端 API

新增 `backend/models/repo/alerts.py` 与 `backend/routers/alerts.py` (注册进主 app router)。所有端点经 `Depends(get_current_user)`,以 `user["id"]` 隔离。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stocks/alerts` | 当前用户全部预警 (供池页面汇总打标) |
| GET | `/api/stocks/{code}/alerts` | 单股预警列表 |
| POST | `/api/stocks/{code}/alerts` | 新建 (body: conditions, note, enabled) |
| PUT | `/api/stocks/alerts/{id}` | 编辑 / 启停 / 重启 (triggered→active 即重启) |
| DELETE | `/api/stocks/alerts/{id}` | 删除 |

repo 方法: `list_alerts(user_id)`, `list_alerts_by_code(user_id, code)`, `list_active_alerts()` (全用户,供扫描), `create_alert(...)`, `update_alert(user_id, id, ...)`, `delete_alert(user_id, id)`, `mark_triggered(id, price)`。写操作必带 `user_id` 防越权。

校验: POST/PUT 校验 conditions 结构(dim/op/dir 枚举、数值范围、非空数组);非法返回 422。

### 边界处理
- 股票移出池 (`remove_stock` 逻辑删除) → 同步逻辑/物理删除该 code 的预警 (在 `stocks` repo 的删除处联动,或扫描时跳过已不在池中的 code)。本设计采用: `remove_stock` 时一并删除该用户该 code 的预警。
- 越权: 所有按 id 的写操作 `WHERE id=%s AND user_id=%s`。

## 6. 前端

### API 层
`frontend/src/api/stocks.ts` 新增 alerts 系列调用 (或新建 `alerts.ts`,与现有风格一致)。

### PoolView.vue
- 每行加「预警」铃铛入口;filter-bar 可加「预警管理」汇总按钮 (可选)。
- 行内徽标: 该股有 active 预警显示铃铛(实心),有 triggered 预警高亮提示(如红点),无则空心/无。
- 预警编辑 Modal:
  - 展示该股现有预警列表 (条件中文摘要 / 状态 / 启停开关 / 重启按钮 / 删除)。
  - 新增/编辑表单: 选维度 → 运算符/参数 → 值;「+ 添加条件」可继续加条件做 AND;每条目可删。
  - 维度对应输入: 价格(数值)、涨跌幅(数值%)、接近均线(选MA + band%)、上穿跌破(选MA + 方向)。
- 用 `useGlobalMessage` 反馈成功/失败 (禁 naive useMessage)。

### 移动端 (硬规范)
- 用 `useResponsive` + 768 断点;Modal 与列表在移动端卡片化展示,表单纵向堆叠;触控目标尺寸合理。
- 池页面行徽标在移动端卡片上同样可见、可点。

### Changelog
- 在 `frontend/src/data/changelog.ts` 数组头部加版本记录 (本次为自定义预警功能上线)。

## 7. 涉及文件清单

新增:
- `backend/models/repo/alerts.py`
- `backend/routers/alerts.py`
- `backend/services/custom_alert_scanner.py`
- `frontend/src/api/alerts.ts` (或并入 stocks.ts)
- 前端预警 Modal 组件 (PoolView 内或抽独立组件)

修改:
- `backend/models/database.py` (建表 + 种子任务)
- `backend/services/task_registry.py` (注册 handler)
- `backend/services/notifier.py` (`send_dual_card` 支持指定 webhook)
- `backend/models/repo/stocks.py` (remove_stock 联动删预警)
- `backend/main` / app 装配处 (注册 alerts router)
- `frontend/src/views/PoolView.vue` (入口/徽标/Modal)
- `frontend/src/api/stocks.ts` (如并入)
- `frontend/src/data/changelog.ts`

## 8. 测试

- 后端 repo: 增删改查 + user_id 隔离 + mark_triggered 状态流转。
- 后端 eval_conditions: 各维度命中/不命中/数据缺失不误触发;ma_cross 上穿/跌破边界。
- 后端 API: 鉴权、校验(非法 conditions 422)、越权(改他人预警拒绝)。
- 扫描: 多用户多条预警聚合、推送按用户路由(notifier 可 mock/断言目标 webhook)。
- 前端: build 通过;Modal 增删条件、移动端展示。

## 9. 风险与备注

- kline_cache 当日覆盖不全 (见 [[cash-alert-design]]): ma_near/ma_cross 用"近 N-1 日收盘 + 现价"口径,不依赖当日已落盘的 kline;bar 不足则该条件不满足,安全降级。
- 行情缓存现价口径: 用 refresh_quotes 落地的实时缓存,避免额外拉行情接口 (见 [[avoid-eastmoney-api]] 接口压力顾虑)。
- 一次性失效后次日不自动重置,需用户手动重启 (符合"触发后失效"决策)。
