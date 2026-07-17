# 观潮·智能监控系统 — 后端开发规范

> 适用范围：`backend/` 全部 Python 代码（FastAPI + aiomysql + APScheduler + 飞书/PushPlus 推送）。
> 本文档的主体是**把仓库中已在执行的惯例成文固化**，正例均引用真实代码（`文件路径:行号`）。
> 少量此前未成文、本次补立的条款，一律以 **【新立规矩】** 标注，其余条款均为现行惯例。
> 修订方式：规范变更随代码同一次提交更新本文件，不允许"代码先行、规范滞后"。

---

## 1. 分层架构与依赖方向

### 1.1 目录职责（现行分层）

| 目录 | 职责 | 依据 |
|---|---|---|
| `backend/routers/` | HTTP 路由层：参数解析、鉴权依赖、调用 repository/services，不含业务算法 | `backend/main.py:71-104` 逐个挂载 |
| `backend/services/` | 业务逻辑与后台任务：扫描器、刷新器、推送、回测、信号引擎 | `backend/services/scanner.py` 等 120+ 模块 |
| `backend/models/repo/` | 数据访问层：按业务域拆分的 40 个子模块，共享 `_db.py` 底层 helper | `backend/models/repo/_db.py:11-24` |
| `backend/models/database.py` | 连接池 + 全部建表/迁移 SQL + 定时任务种子 | `backend/models/database.py:1782-1805` |
| `backend/core/` | 横切模块：配置、JWT 鉴权、调度器单例、交易日历、WebSocket | `backend/core/trading_calendar.py` |
| `backend/fetcher/` | 外部数据源抓取（新浪/腾讯/同花顺/东财/baostock） | `backend/fetcher/quotes.py` |
| `backend/utils/` | 纯函数工具，无 IO、无状态 | `backend/utils/formatting.py`、`backend/utils/limit_calc.py` |

### 1.2 依赖方向规则

1. **允许的调用方向**：`routers → services / models.repository`；`services → services / models.repository / fetcher / core`；`repo → 仅 _db.py 与 database.py`；`utils` 不 import 项目内任何模块。
2. **facade 只出不进**：`backend/models/repository.py:1-17` 与 `backend/data_fetcher.py:1-18` 都是纯 re-export facade，docstring 明文"新加操作直接写进对应子模块，不要回填 facade"。新代码一律写进 `models/repo/xxx.py`、`fetcher/xxx.py`，facade 只加一行导出以兼容旧调用方。
3. **services 之间允许互调**（通知、卡片、板块上下文等公共能力被广泛复用，如 `backend/services/attack_direction_analyst.py:24`）。出现循环依赖时用**函数内延迟 import** 解开，正例 `backend/services/scanner.py:619`：`from backend.models.repo import push_pref as _pref_repo`。
4. **【新立规矩】routers 禁止 import `models.repo._db` 直接写裸 SQL**。动因：目前仓库存在个别例外（`backend/routers/signals.py:132-135` 直接 `_fetchall` 查 `cfzy_biz_market_risk`），一旦成风，SQL 会散落到路由层无法统一改表。规则：路由需要的新查询一律先在对应 `models/repo/` 子模块加函数再调用；存量例外遇改动时顺手迁移。检查方式：`grep -rn "repo._db" backend/routers/` 结果应只减不增。

### 1.3 应用装配

- 路由统一在 `backend/main.py:71-104` 挂载；生命周期事务（init_db → 任务加载 → scheduler.start → 健康首检）集中在 lifespan（`backend/main.py:35-65`），不允许模块 import 时产生副作用（连库、发请求）。
- SPA 静态文件回退必须保留路径穿越防护：归一化后仍在 dist 目录内才回文件（`backend/main.py:114-122`）。动因：v1.7.568 前可用 `../../config.json` 读走生产数据库凭证。
- 本地直跑只绑 `127.0.0.1`（`backend/main.py:125-129`），生产由 systemd uvicorn + nginx 反代，任何代码不得改绑 `0.0.0.0`。

---

## 2. 数据库规范

### 2.1 连接与连接池

- **技术选型：aiomysql 裸 SQL，全项目不用 ORM/SQLAlchemy**。动因：生产库是跨云火山引擎 RDS，单次往返约 44ms，单 worker 部署，性能预算花在"减少往返次数"上，ORM 的隐式查询是负资产。
- 连接池唯一定义点 `backend/models/database.py:1782-1800`：`minsize=5`（常驻保温，避免冷连接每次付 280ms 重连）、`maxsize=25`、`pool_recycle=3600`（防远端 wait_timeout 掐断）、`autocommit=True`、`charset=utf8mb4`。**禁止任何模块私开第二个 MySQL 连接池**，一律 `get_pool()`（`backend/models/database.py:1821-1823`）。
- 数据库凭证只放项目根 `config.json`（已 gitignore），源码只留空占位（`backend/core/config.py:31-39`）。部署脚本打包时排除 `config.json` 防覆盖生产配置（`deploy.ps1` 第 3 步）。

### 2.2 表命名

- 所有表带 `cfzy_` 前缀，按归属分两类：
  - `cfzy_sys_`：全市场/系统级共享数据——行情缓存、日历、快照、调度任务、用户表。例：`cfzy_sys_kline_cache`（`backend/models/database.py:120`）、`cfzy_sys_scheduled_tasks`。
  - `cfzy_biz_`：与用户业务操作绑定的数据（通常带 user_id）——自选池、信号、交易、预警、模拟盘。例：`cfzy_biz_stock_pool`（`backend/models/database.py:23`，主键 `(code, user_id)`）、`cfzy_biz_signals`（:34）、`cfzy_biz_push_pref`（:67）。
- 新表必须归入其中一类并想清楚"是否带 user_id"；检查方式：建表语句必须进 `SCHEMA_STATEMENTS`，review 时看前缀。

### 2.3 建表与迁移（无 Alembic）

- 迁移体系是三段式、重启自动生效：
  1. 建表：`SCHEMA_STATEMENTS` 全部 `CREATE TABLE IF NOT EXISTS`（`backend/models/database.py:11` 起）。
  2. 加字段/索引：往 `MIGRATION_STATEMENTS`（:926 起）追加裸 `ALTER TABLE ... ADD COLUMN ...`，如 :936。
  3. 幂等执行：`_run_migrations`（:1098-1123）吞掉幂等错误码 `{1060, 1061, 1068, 1091, 1146}`（重复列/键等），其余错误记 warning 不中断启动。
- 复杂迁移（改主键等）写专门的条件迁移函数，正例 `_migrate_stock_pool_pk`（`backend/models/database.py:1090-1095`）。
- **【新立规矩】迁移只增不删**：`MIGRATION_STATEMENTS` 不写 `DROP TABLE` / `DROP COLUMN` / 破坏性 `MODIFY`。动因：迁移在每次启动时全量重放，破坏性语句一旦混入，任何一次重启都可能吃掉生产数据；废弃列先停止写入、保留列，确需清理时人工在维护窗口执行并在本文件记录。

### 2.4 数据访问写法

- 新的 DB 操作写在 `models/repo/` 对应业务域子模块，文件顶部注明对应表（`backend/models/repo/stocks.py:1`）；底层只用 `_fetchall/_fetchone/_execute/_executemany` 四个 helper（`backend/models/repo/_db.py:11-24`）。
- SQL 一律 `%s` 参数化，禁止字符串拼接用户输入（正例 `backend/models/repo/stocks.py:11-16`）。
- 44ms 往返预算：循环内逐条 INSERT/SELECT 是事故写法，批量写用 `_executemany`，批量读合并成一条 IN 查询；后台重算类任务限制并发别抢连接池（`backend/services/model_winrate_refresher.py:44`，`_CONCURRENCY = 6` 并注明"隔离小并发，不抢实时行情池"）。

---

## 3. API 设计规范

1. **路由前缀**：一律 `/api/` 开头 + kebab-case，如 `/api/auction-pool`、`/api/market-breadth`；管理端点用 `/api/admin/` 前缀（`backend/routers/lark_templates.py:33`）。
2. **鉴权是默认项**：每个端点声明 `user: Annotated[dict, Depends(get_current_user)]`（`backend/routers/signals.py:24`）；管理操作用 `Depends(require_admin)`（`backend/routers/config.py:67`）。免鉴权端点是显式例外，必须在挂载处注释原因（`backend/main.py:104`：官网内测申请 POST 免鉴权）。JWT 细节（HS256、7 天过期、token_version 单点登录）见 `backend/core/auth.py:42-92`，密钥首启自动生成回写 config.json，**绝不硬编码进源码**（`backend/core/config.py:29-30`）。
3. **响应格式**：不做统一 `{code, data}` 包装，直接返回 dict/list 由 FastAPI JSON 化；多字段场景返回语义化 dict（`backend/routers/signals.py:145-146`）。错误一律 `raise HTTPException(状态码, "中文消息")`，422=参数校验错、404=不存在、401/403=鉴权（正例 `backend/routers/alerts.py:30`、:147）。
4. **分页与限流参数**：列表大表用 `page + page_size`，返回 `{total, page, page_size, <items>}`（`backend/routers/logs.py:12-25`）；轻量列表用 `limit` + 日期区间，且必须用 `Query` 上下界约束防拉爆（`backend/routers/signals.py:30-37`：`days_back: int = Query(30, ge=1, le=180)`）。

---

## 4. 推送开发规范

### 4.1 统一入口，禁止直连通道

- **个股信号推送的唯一对外入口是 `notifier.send_wechat_signal`**（`backend/services/notifier.py:536-608`）；文本/卡片类通用出口为 `send_wechat_text` / `send_dual` / `send_dual_card` / `send_card`（同文件）。任何业务代码**禁止自己 POST 飞书 webhook 或 PushPlus**——通道分发（飞书 schema 2.0 卡失败回退 markdown 卡、WxPusher、PushPlus 复用同一份 lark_md 转 HTML）全部收口在 `_send_wechat_signal_direct`（notifier.py:611-743）。动因：入口内串着四道闸门，绕过入口 = 绕过所有闸门。
- 四道闸门顺序（改推送逻辑前先读一遍）：
  1. 生产 IP 闸：`is_production()` 为假直接不发（notifier.py:545-548）；出口 IP 探测结果有进程级缓存、失败短 TTL 内不重探（`backend/core/config.py:110-134`）。排查"推送没发"先查这里，再查偏好闸。
  2. 用户存在闸（notifier.py:554-558）。
  3. 推送偏好闸：`push_pref decide()` 判静音/屏蔽，含"连续触发压住、新一轮突破自动放行"的条件式静音（notifier.py:561-586）。**闸门自身异常一律放行推送**（notifier.py:585-586）——宁可多推不可哑火，新加闸门必须遵守这个失败语义。
  4. 风暴聚合：sell/reduce 方向信号进 `storm_aggregator` 90 秒窗口，≥3 条合并聚合卡、<3 条到期原样逐发（`backend/services/storm_aggregator.py:36-39`）。
- 节假日/交易时段判断不在 notifier 内做，由扫描任务侧 `is_workday / is_trading_time` 负责（见 5.3）。

### 4.2 节流与聚合

- 榜单/板块类周期性提醒必须走 `alert_throttle`：`register(alert_type, merger, throttle_seconds, lark_card_builder)` 注册 + `enqueue` 入队，默认 15 分钟窗口、首条立即发、全渠道失败自动重排队（`backend/services/alert_throttle.py:25-76`）。正例：`backend/services/capital_inflow_scanner.py:234-238`。
- `lark_card_builder` 卡片槽：`(items) -> (title, elements)`，返回非空则飞书发表格卡（走 `send_dual_card`），否则纯文本兜底（alert_throttle.py:99-118）。
- 兜底 flush 由 60 秒的 `alert_throttle_flush` 种子任务统一驱动（`backend/models/database.py:1523-1525`），新聚合机制搭这班车，**不要新增自己的 flush 调度任务**（`backend/services/storm_aggregator.py:16-17` 即此做法）。

### 4.3 卡片与移动端

- 卡片构造统一用 `card_kit`（`backend/services/card_kit.py:1-12`，权威视觉规范见 `docs/push-design-baseline.md`）；图形硬规则在纯函数内兜死（如强度条 ≤8 格，card_kit.py:80-89），五大信号家族的 header 色查 `FAMILY_TEMPLATE`（:20-27）。
- 多行多列用 markdown 表格 `md_table`（`backend/services/lark_notifier.py:157-179`），原生 `table_element` 已弃用（手机端长内容截断）；移动版式三原则写在 :161-163 注释——列压到 2~3 列、关键值短且前置、长文本下沉。原生表格确需使用时**列宽必须写百分比且合计 100%**（`backend/services/notifier.py:929-933` 注释说明了手机端裁列的动因）。
- 飞书推送模版变更必须同步更新系统内的推送预览页（PC/手机端），保持预览与真实推送 1:1。
- 卡片上的快捷操作链接必须走 HMAC 签名：复用 JWT 密钥派生、exp 纳入签名原文、48h 过期防重放（`backend/services/push_pref.py:25-30, 52-72`）；禁止拼无签名的裸操作 URL。

---

## 5. 定时任务规范

### 5.1 注册方式：任务定义进 DB，代码只留 handler

- 调度器是全局单例 `backend/core/scheduler.py:3`；**`scheduler.add_job` 的唯一调用点是 `task_manager.register_task`**（`backend/services/task_manager.py:55-83`），业务代码不许自己 add_job。
- 新增任务三步：1) `services/` 写 handler；2) `task_registry.TASK_HANDLERS` 挂名字映射；3) `database.py` 的种子任务段加一行任务定义（interval 秒数或 cron 时刻，参考 `backend/models/database.py:1129` 起的现有条目）。启动时会做 DB 与 TASK_HANDLERS 的双向对账（task_manager.py:39-52），漏挂会被发现。
- 调度参数惯例：interval 任务 `max_instances=1` + `misfire_grace_time=max(间隔,10)`；cron 任务 `misfire_grace_time=60`（task_manager.py:66-82）。

### 5.2 异常与超时兜底

- 所有任务经 `wrapped_handler` 统一包裹（`backend/services/task_registry.py:244-267`）：`asyncio.wait_for` 超时控制 + 整体 try/except + 失败落库计连续失败次数 + 达阈值告警。handler 自身抛异常不会拖垮调度器，但**handler 内不要再套一层吞掉所有异常的裸 except**，否则失败计数失真、健康面板失明。

### 5.3 交易日历：单一来源

- 工作日/交易时段判断只用 `backend/core/trading_calendar.py`（`is_workday` 基于 chinese_calendar 剔法定节假日 :19-36、`is_trading_time` :50-62、`prev_trading_day` :39-47）。动因：曾因未剔节假日在休市日误推（v1.7.464 修复）。禁止任何模块自己写 `weekday() < 5` 之类的土日历。
- 开闸判断写在 handler 内部而非调度配置上（正例 `backend/services/sector_strength_scanner.py:54-57`），这样休市日任务空转直接返回，不产生副作用。

### 5.4 隔离 HTTP 客户端（性能红线）

- **高频后台任务、或调用慢源/可能被封源的任务，必须用模块级独立 `httpx.AsyncClient`，不得复用实时行情主池**。配方：小连接池 + 短超时 + `trust_env=False`，正例 `backend/services/sector_strength_scanner.py:29-43`（4 连接、4s 超时，注释明确"绝不会饿死每 3s 的实时报价刷新（主池 20 连接）"）。动因：0713 两次全池冻结事故，元凶即慢失败请求空耗共享连接池。
- 同款实例：`backend/fetcher/intraday.py:44-58`（分时护栏）、`backend/fetcher/stock_extra.py:23-36`（带连续空转熔断）、`backend/services/market_breadth_refresher.py:8-9`。
- 整轮扫描配硬封顶：`asyncio.wait_for(整轮, REFRESH_HARD_TIMEOUT)`，超时保留上轮缓存（sector_strength_scanner.py:27, 99-101）。

### 5.5 同步阻塞与重计算：to_thread

- 同步阻塞的第三方库（pywencai、baostock、同步 SDK）和重 CPU 计算，必须 `await asyncio.to_thread(...)` 卸到线程池，禁止直接在事件循环里跑。正例：`backend/fetcher/wencai_screener.py:126-146`（注释写明动因）、`backend/services/model_winrate_refresher.py:239-241`、`backend/services/ai_analyst.py:1096-1102`（"报告生成期间饿死 3s 行情"事故后改造）。

---

## 6. 外部数据源规范

### 6.1 源选择与封禁现实

- **实时行情唯一源是新浪**（`backend/fetcher/quotes.py:1-5`）：东财 push2 生产 IP 被封，请求必败且慢失败空耗连接池，已于 v1.7.610 移除。新代码接实时行情不准再引入东财 push2。
- 日 K 降级链固定为 **新浪 → 同花顺 → DB 缓存**（`backend/fetcher/klines.py:1-5, 172-205`），三源全败向 `data_health.report("kline_network_down")` 埋点（:199-204）。
- 东财唯一放行的域是 `datacenter.eastmoney.com`（生产实测可达，`backend/fetcher/earnings_data.py:3,16`，业绩预告/披露日历专用）。板块榜东财主 + 腾讯备（`backend/services/api_health.py:79`）；美股/港股走腾讯 qt.gtimg（`backend/services/ai_analyst.py:58,283`）。
- 新增数据源接入顺序：新浪/腾讯优先，同花顺次之，东财仅 datacenter，且必须配备源或缓存兜底。

### 6.2 请求写法

- 各源 Referer/UA 请求头集中在 `backend/fetcher/http_client.py:16-29`（新浪必须带 `Referer: https://finance.sina.com.cn`），不要在业务代码里散写请求头。
- 出站一律 `trust_env=False` 禁走系统代理（http_client.py:114），国内行情域名已进 NO_PROXY（`backend/data_fetcher.py:21-30`）。
- 新的外部 HTTP 调用必须经 `TrackedAsyncClient`（或复用其打点约定），每次请求自动按 URL 归类记入 5 分钟滚动成功率（`backend/fetcher/http_client.py:79-99` → `api_metrics`）。动因：健康面板读的是真实业务调用成功率而非模拟探活（`backend/services/api_health.py:1-17`），绕开打点的调用在面板上是盲区。

### 6.3 健康预警与数据校验（三层，职责勿混）

| 层 | 模块 | 职责 |
|---|---|---|
| 源健康 | `backend/services/data_health.py:25-29` | "源挂了让人知道"：三类事件计数达阈值推灰卡，每类每天最多一次，开盘 09:32 前抖动静默 |
| 数据自愈 | `backend/services/data_sanity.py:73-132` | 盘中陈旧行情自动补刷（90s）+ 合理性自检（300s，30min 告警冷却） |
| 跨源交叉 | `backend/services/data_cross_checker.py:2-8` | 每小时抽样比对新浪 vs 东财涨跌幅、涨跌家数偏差，异常并入盘后系统健康汇总 |

- 分工原则（data_health.py:6 原文）："校验下沉负责垃圾数据不进下游，data_health 负责源挂了让人知道"。**数据校验做在写入源头（fetcher/repo 层），不做在消费端**。动因：交割单列错位脏数据曾致假浮亏止损误报，教训是校验必须下沉。
- 信号质量终审：每交易日 17:00 `signal_eod_audit` 用收盘真实日线复核当日全部信号（K 线指纹 + 触发价区间），结果写 `cfzy_biz_signals.eod_audit`，只标记不自动删（`backend/services/signal_eod_audit.py:1-18`，任务注册 `backend/models/database.py:1700-1703`）。新增信号类模型默认纳入该复核，绕过需在 PR 说明。

---

## 7. 错误处理与日志

1. **logger 获取**：每个模块统一 `logger = logging.getLogger(__name__)`（全库 121 个文件的既成惯例）；全局配置只在 `backend/main.py:22-31`（stdout + `app.log` 按天轮转保留 30 天），任何模块不得再 `basicConfig`。
2. **后台任务**：异常兜底交给 `wrapped_handler`（见 5.2），handler 内部只 catch 自己能处理的具体异常；确需吞异常继续的分支必须 `logger.warning/exception` 留痕（正例 `backend/main.py:39-43`）。
3. **推送闸门失败语义**：闸门/装饰性逻辑异常时放行主流程（notifier.py:585-586、`backend/main.py:58-62` 飞书长连接启动失败仅警告不阻断），核心数据写入失败则如实报错，两类语义不要用反。
4. **注释规约**：涉及事故修复或行为变更的代码，注释带 `v1.7.NNN:` 版本前缀说明动因（如 `backend/main.py:116` 路径穿越修复、quotes.py:3 东财移除），让"为什么这么写"可考古。
5. **【新立规矩】对外发布后，日志与推送文案不得包含密钥、cookie、完整数据库连接串**。动因：系统将对外发布，`app.log` 与飞书卡片的受众不再只有作者本人。检查方式：review 时对 `logger.*` 与推送正文里的插值变量过一遍敏感项。

---

## 8. 测试与提交闸门

### 8.1 测试组织

- `backend/tests/` 平铺 86 个 `test_*.py`，按被测模块命名；`pytest.ini`：`asyncio_mode = auto`、`testpaths = backend/tests`。
- **测试不连库不联网**：`conftest.py` 只做 sys.path 注入（`backend/tests/conftest.py:1-8`），DB/网络由各测试文件用 monkeypatch/AsyncMock 自理（约 70/86 个文件如此），多个测试文件头部明文声明"不连库不联网"（`backend/tests/test_baseline_v11_cards.py:3`）。新测试沿用此约定，禁止写依赖生产库的测试。

### 8.2 提交闸门（人工硬闸，现行惯例）

改动逻辑代码必须全过以下三关才允许 commit/push/部署，半成品不上：

1. 后端：`pytest`（backend/tests 全绿）；
2. 后端：import 检查（`python -c "import backend.main"`，兜住语法与循环依赖）；
3. 前端（若涉及）：`vue-tsc && vite build` 通过——这一关同时是部署脚本的唯一自动闸门（`deploy.ps1` 第 1 步本机 build 失败即 exit 1，`frontend/package.json:8`）。

- **【新立规矩】把后端闸自动化进部署脚本**：`deploy.ps1` 目前不跑 pytest 与 import 检查（仅前端 build 闸），后端闸完全靠人工自觉，对外发布后多人协作时必然漏。规则：deploy.ps1 在打包步骤前增加 `pytest backend/tests` + import 检查，任一失败终止部署。在此之前，人工三关仍是硬性要求。
- 部署其余红线（现行惯例）：打包排除 `config.json` 防覆盖生产配置；服务器侧不 build 前端（v1.7.584 服务器 build 内存事故后改为本机 build 上传产物）；部署后必须验证服务 active + 版本真上线 + 关键接口可用。

### 8.3 变更配套义务（跨端同步，现行硬规矩）

1. **changelog**：每次调整代码逻辑，必须在 `frontend/src/data/changelog.ts` 数组头部加版本记录（`v1.7.NNN` 逐次 +1、date、title、changes 带 new/improve/fix 标签，格式见 changelog.ts:1-17）。
2. **模型三处同步**：买卖点模型的新增/参数/口径变更（动 `signal_engine_config.py` 的 DEFAULT_SIGNAL_CONFIG、`signal_engine_detectors.py`、`signal_engine.py` 接线中任一处），必须同步前端三处——`frontend/src/data/models.ts` 名册、`ModelsView.vue` 流程图（与 models.ts 是两份手写副本，最易漂移）、changelog。项目已有 `model-roster-sync` skill 与 Stop hook 拦截"只改一份"。
3. **回测 OOS 闸**：单时段回测大幅提升的模型/参数，上线前必须用零重叠独立样本复验，翻车不上（过拟合教训，属一级硬规范）。
4. **推送模版与预览同步**：见 4.3。

---

## 附：新立规矩清单（本次补立，共 5 条）

| 条款 | 位置 | 一句话动因 |
|---|---|---|
| routers 禁直连 `repo._db` 裸 SQL | 1.2-4 | 防 SQL 散落路由层，存量例外只减不增 |
| 迁移只增不删 | 2.3 | 迁移每次启动全量重放，破坏性语句会吃生产数据 |
| 日志/推送不含敏感信息 | 7-5 | 对外发布后受众扩大 |
| deploy.ps1 增加后端 pytest + import 自动闸 | 8.2 | 人工闸在多人协作下必然漏 |
| （随附）本文档随代码同次提交更新 | 文首 | 防规范与实现漂移 |
