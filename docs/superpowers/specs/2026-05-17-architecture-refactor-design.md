# A股交易信号监控系统 — 架构重构设计

## 概述

将当前单文件混合架构重构为 Monorepo 前后端分离架构，前端迁移到 Vue 3 + Naive UI，后端 FastAPI 按职责分层，支持 PC 和移动端响应式布局。

## 决策记录

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 端适配 | 响应式 Web | 一套代码，维护成本最低 |
| 前端框架 | Vue 3 + Vite + Naive UI | 上手快、暗色/亮色主题开箱即用、响应式支持好 |
| 后端策略 | FastAPI 分层重构 | 保持 Python 生态（pandas/akshare），按职责拆分 |
| UI 组件库 | Naive UI | Vue 3 原生、TS 支持佳、适合工具类应用 |
| 主题 | 浅色为主 | 用户要求 |
| 重构范围 | 架构重构 + 小幅增强 | 保持现有功能，增加导入、加载态、错误提示等 |

## 1. 项目结构

```
trading-monitor/
├── frontend/                  ← Vue 3 + Vite + Naive UI
│   ├── index.html
│   ├── vite.config.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── main.ts            ← 入口，挂载 App + Naive UI
│       ├── App.vue            ← 根组件（布局框架 + 路由出口）
│       ├── router/
│       │   └── index.ts       ← 5 个路由
│       ├── stores/
│       │   ├── signal.ts      ← 今日信号 + WebSocket 实时推送
│       │   ├── stock.ts       ← 股票池 CRUD + MA 状态
│       │   └── config.ts      ← 系统配置
│       ├── api/
│       │   ├── client.ts      ← axios 实例（baseURL、拦截器）
│       │   ├── stocks.ts
│       │   ├── signals.ts
│       │   ├── kline.ts
│       │   └── config.ts
│       ├── composables/
│       │   ├── useWebSocket.ts    ← WebSocket 连接 + 自动重连
│       │   └── useResponsive.ts   ← 响应式断点检测
│       ├── views/
│       │   ├── SignalView.vue
│       │   ├── PoolView.vue
│       │   ├── ChartView.vue
│       │   ├── HistoryView.vue
│       │   └── ConfigView.vue
│       ├── components/
│       │   ├── layout/
│       │   │   ├── AppHeader.vue  ← PC 顶部导航
│       │   │   └── AppTabBar.vue  ← 移动端底部 Tab
│       │   ├── signal/
│       │   │   └── SignalCard.vue
│       │   ├── stock/
│       │   │   ├── StockTable.vue ← PC 表格
│       │   │   └── StockList.vue  ← 移动端列表
│       │   └── chart/
│       │       └── KLineChart.vue
│       └── styles/
│           └── variables.css
├── backend/                   ← FastAPI 分层
│   ├── main.py                ← 应用入口、lifespan、挂载静态
│   ├── core/
│   │   ├── config.py          ← 配置加载/保存
│   │   ├── scheduler.py       ← APScheduler 封装
│   │   └── websocket.py       ← WebSocket 管理 + 广播
│   ├── routers/
│   │   ├── stocks.py          ← /api/stocks CRUD
│   │   ├── signals.py         ← /api/signals/*
│   │   ├── kline.py           ← /api/kline/{code}
│   │   ├── search.py          ← /api/search
│   │   ├── config.py          ← /api/config*
│   │   ├── scan.py            ← /api/scan (手动扫描)
│   │   ├── ws.py              ← /ws (WebSocket 端点)
│   │   └── ths.py             ← /api/ths/* (同花顺导入)
│   ├── services/
│   │   ├── scanner.py         ← 核心扫描逻辑
│   │   ├── signal_engine.py   ← 信号检测（从现有迁移）
│   │   ├── notifier.py        ← 企业微信推送（从现有迁移）
│   │   └── ths_importer.py    ← 同花顺自选股导入
│   ├── models/
│   │   ├── database.py        ← 数据库初始化 + 连接
│   │   └── repository.py      ← SQL 操作
│   └── data_fetcher.py        ← Sina 数据获取（从现有迁移）
├── trading.db
├── config.json
└── requirements.txt
```

## 2. 后端架构

### 2.1 分层职责

- **routers/**: 纯路由定义，参数校验，调用 service 层
- **services/**: 业务逻辑，不直接操作数据库
- **models/**: 数据库 schema 定义 + 所有 SQL 操作
- **core/**: 横切关注点（配置、调度、WebSocket）

### 2.2 核心模块迁移

| 现有文件 | 迁移目标 | 变更 |
|----------|----------|------|
| `app.py` 路由部分 | `routers/*.py` (8 个文件) | 拆分，每个路由文件专注一组端点 |
| `app.py` scan_stock_pool | `services/scanner.py` | 提取为独立 service |
| `app.py` WebSocket | `core/websocket.py` + `routers/ws.py` | 连接管理独立 |
| `app.py` lifespan | `main.py` | 保留 |
| `signal_engine.py` | `services/signal_engine.py` | 直接迁移，逻辑不变 |
| `data_fetcher.py` | `backend/data_fetcher.py` | 直接迁移，逻辑不变 |
| `notifier.py` | `services/notifier.py` | 直接迁移，逻辑不变 |
| `models.py` | `models/database.py` + `models/repository.py` | schema 和操作分离 |
| `config.py` | `core/config.py` | 直接迁移 |

### 2.3 新增：同花顺远航版自选股导入

**数据源**: `D:\Program Files\同花顺远航版\bin\users\mo_292753904\blockstockV3.xml`

**格式**: XML，结构如下：
```xml
<hevo version="3407" sort_list="23,24,25,">
  <Block name="Base64编码组名" id="35">
    <security market="USHA" code="600519" />  <!-- 沪市 -->
    <security market="USZA" code="300750" />  <!-- 深市 -->
    <security market="UHKI" code="HSI" />     <!-- 港股，过滤 -->
  </Block>
</hevo>
```

**市场代码映射**:
- `USHA` / `USHT` = 沪市 A 股 → 导入
- `USZA` = 深市 A 股 → 导入
- `UHKI` / `UHKM` = 港股 → 跳过
- `URFI` = 指数 → 跳过
- `UCMS` = 期货 → 跳过
- `UGFF` = 基金 → 跳过

**API 设计**:
- `GET /api/ths/groups` — 列出所有自选分组（id + 名称 + 股票数量）
- `POST /api/ths/import` — 导入指定分组，body: `{ group_id: "35", trade_type: "short" }`
- 导入时自动通过 Sina API 查询股票名称
- 已存在的股票跳过，返回导入结果统计

**路径检测**: 按优先级扫描：
1. 配置文件中用户自定义路径
2. `D:\Program Files\同花顺远航版\bin\users\*\blockstockV3.xml`
3. `D:\Program Files\同花顺\mo_*\custom_block\__base_\download\*`

## 3. 前端架构

### 3.1 技术栈

- Vue 3 (Composition API + `<script setup>`)
- TypeScript
- Vite (构建 + 开发服务器)
- Pinia (状态管理)
- Vue Router (路由)
- Naive UI (组件库)
- Lightweight Charts (K 线图表，沿用现有)
- Axios (HTTP 请求)

### 3.2 路由

| 路由 | 页面 | 组件 |
|------|------|------|
| `/` | 信号监控 | `SignalView.vue` |
| `/pool` | 股票池 | `PoolView.vue` |
| `/chart` | K 线图表 | `ChartView.vue` |
| `/history` | 信号历史 | `HistoryView.vue` |
| `/config` | 设置 | `ConfigView.vue` |

### 3.3 状态管理 (Pinia)

**signal store**:
- `signals: Signal[]` — 今日信号列表
- `loadTodaySignals()` — 初始加载
- `addSignal(signal)` — WebSocket 推送时追加
- WebSocket 连接在 store 内管理

**stock store**:
- `stocks: Stock[]` — 股票池
- `loadStocks()` / `addStock()` / `removeStock()` / `updateStock()`
- 数据缓存，切换页面不重复请求

**config store**:
- `config: Config` — 系统配置
- `loadConfig()` / `saveConfig()`

### 3.4 WebSocket

`composables/useWebSocket.ts` 封装：
- 自动连接 `ws://host/ws`
- 断线自动重连（指数退避，3s → 6s → 12s，上限 30s）
- 收到消息后分发到 signal store

## 4. 响应式布局

### 4.1 断点

- `≥768px`: PC 布局
- `<768px`: 移动端布局

### 4.2 导航

- **PC**: 顶部水平导航栏，品牌名 + Tab 按钮 + 手动扫描 + 状态灯
- **移动端**: 简化顶栏（品牌名 + 扫描按钮）+ 底部 5 Tab 栏（信号/股票池/K线/历史/设置）

### 4.3 各页面适配

| 页面 | PC 布局 | 移动端布局 |
|------|---------|------------|
| 信号监控 | 双列信号卡片网格 | 单列卡片堆叠 |
| 股票池 | 完整表格（代码/名称/类型/状态/价格/均线位置/MA距离/操作） | 卡片列表（名称+价格+状态+操作） |
| K 线图表 | 大图 480px 高 + 完整 MA 图例 | 紧凑竖屏 320px / 可选全屏横屏 |
| 信号历史 | 表格（时间/代码/名称/信号/方向/价格/详情） | 时间线卡片，按天分组 |
| 设置 | 表单布局 | 全宽表单 |

## 5. 色彩体系（浅色主题）

| 用途 | 色值 | 说明 |
|------|------|------|
| 页面底色 | `#F6F8FA` | 浅灰底色 |
| 卡片/表面 | `#FFFFFF` | 纯白 |
| 主色 | `#0969DA` | 蓝色，导航高亮、品牌 |
| 主要文字 | `#1F2328` | 深色 |
| 次要文字 | `#656D76` | 灰色 |
| 边框 | `#D1D9E0` | 浅灰边框 |
| 买入 | `#1A7F37` | 绿色 |
| 卖出 | `#CF222E` | 红色 |
| 减仓 | `#BF8700` | 橙色 |

CSS 变量统一管理，通过 Naive UI 的 `themeOverrides` 全局配置。

## 6. 小幅增强

1. **同花顺自选股导入**: 股票池页面增加"导入同花顺自选"按钮，弹窗选择分组，一键导入
2. **加载状态**: 使用 Naive UI `NSkeleton` 骨架屏，API 请求时展示
3. **错误提示**: 使用 Naive UI `useMessage` 全局提示，替代 `alert()`
4. **数据缓存**: Pinia store 缓存股票池和信号数据，避免切换页面重复请求
5. **浅色主题**: 全局浅色色彩体系

## 7. 数据流

### 开发模式

```
浏览器 :5173  ←→  Vite Dev Server  ──proxy──→  FastAPI :8888
                  (热更新)                       (API + WebSocket)
```

Vite 配置代理：`/api/*` 和 `/ws` 转发到 `localhost:8888`。

### 生产模式

```
浏览器 :8888  ←→  FastAPI
                   ├── /api/*     → 路由处理
                   ├── /ws        → WebSocket
                   └── /*         → Vue 构建产物 (frontend/dist/)
```

FastAPI 的 `main.py` 在生产模式下挂载 `frontend/dist/` 为静态文件，单进程部署。

### 启动方式

**开发**:
```bash
# 终端 1: 后端
cd backend && python main.py

# 终端 2: 前端
cd frontend && npm run dev
```

**生产**:
```bash
cd frontend && npm run build    # 构建前端到 dist/
cd ../backend && python main.py  # FastAPI 托管一切
```

## 8. API 接口（与现有保持一致）

所有现有 API 端点不变，新增：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ths/groups` | 列出同花顺自选分组 |
| POST | `/api/ths/import` | 导入指定分组到股票池 |

## 9. 不变的部分

- **信号引擎** (`signal_engine.py`): 所有检测逻辑保持不变
- **数据获取** (`data_fetcher.py`): Sina API 调用逻辑保持不变
- **通知推送** (`notifier.py`): 企业微信 Webhook 推送保持不变
- **数据库 Schema**: 三张表迁移到 MySQL，加 `cfzy_` 前缀 (cfzy_stock_pool, cfzy_signals, cfzy_kline_cache)
- **数据库**: 从 SQLite 迁移到 MySQL（火山引擎 veDB）
- **配置文件** (`config.json`): 格式保持不变，新增数据库连接配置
