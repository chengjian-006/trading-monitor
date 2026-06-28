# 观潮 · A股短线交易盯盘系统

> 面向 A 股短线交易者的盘中信号监控、买卖点回测、持仓守护与复盘系统。
> 后台多任务定时扫描全市场与自选股，命中模型即通过飞书 / 个人微信(PushPlus)推送，前端提供盯盘看板、模型回测、交易分析等一体化界面。
>
> 当前版本：**v1.7.526**

---

## 这是什么

观潮把一名短线交易者每天要盯的事自动化成一组后台任务 + 一个 Web 控制台：

- **盘中信号扫描**：每 30s 扫自选股池，命中买点 / 卖点模型即推送（回踩MA10/MA20、缩量后放量突破、中继平台突破、强势起点、弱势极限等）。
- **持仓守护**：接近前高、盈利保护、急速拉升/跳水、涨跌停封单松动等持仓异动实时提醒。
- **市场情绪与风控**：涨停池情绪温度、题材热度、板块弱转强/强转弱、大盘两级风险预警、退潮信号。
- **黑天鹅预警**：自选股风险公告(立案/问询/非标审计…) + 财务红旗打分 + AI 逐股研判。
- **模型回测**：全市场 / 自选股、日线及 5 分钟口径，按真实可成交规则回测胜率 / 收益 / 盈利因子，逐笔明细 + 历史存档。
- **交易分析与复盘**：交割单导入、交易回合归因、实盘 vs 模型对比、收盘复盘、区间复盘清单。
- **纸面交易**：触发买卖点自动模拟成交（真实费用 + 等额轮动），验证严格执行模型的长期效果。
- **博主跟踪**：同花顺博主发帖跟踪推送。

界面与推送全中文，红涨绿跌，支持移动端适配。

---

## 技术栈

**后端**（`backend/`，Python 3.14）
- [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn — Web 框架 / ASGI 服务
- [APScheduler](https://apscheduler.readthedocs.io/) — 定时任务调度（任务定义落库，启动时加载）
- [aiomysql](https://github.com/aio-libs/aiomysql) — 异步 MySQL 连接池（表前缀 `cfzy_`）
- [httpx](https://www.python-httpx.org/) — 行情 / 公告抓取（新浪、腾讯、巨潮 cninfo、同花顺）
- pandas / akshare — 数据处理与回测
- openai SDK → DeepSeek 官方接口（AI 研判 / 解读）
- PyMuPDF — 公告 PDF 正文解析
- PyJWT — 鉴权

**前端**（`frontend/`，Vue 3 + TypeScript）
- Vite 6 构建，Naive UI 组件库，Pinia 状态，vue-router 路由
- lightweight-charts — K线 / 分时图
- xlsx — 报表导出

**部署**：tar 打包 → ssh 上传服务器 → uvicorn 跑在 `127.0.0.1:8888`，nginx 80 端口反代；前端静态资源由后端托管。

---

## 目录结构

```
trading-monitor/
├── backend/
│   ├── main.py              # FastAPI 入口 + lifespan(初始化DB/加载定时任务/启动调度器)
│   ├── core/                # 配置加载、调度器
│   ├── models/              # 数据库连接、repository、各业务表 repo/
│   ├── routers/             # 32 个 API 路由模块(stocks/signals/backtest/...)
│   ├── services/            # 85 个业务服务(扫描器/检测器/回测/推送/AI...)
│   ├── fetcher/             # 行情数据抓取
│   ├── scripts/             # 一次性脚本与回测脚本(多数 gitignore)
│   ├── utils/               # 工具
│   └── tests/               # pytest 测试
├── frontend/
│   └── src/
│       ├── views/           # 19 个页面(盯盘/池子/回测/复盘/纸面交易/配置...)
│       ├── components/       # 组件
│       ├── composables/      # 组合式逻辑
│       └── data/            # changelog.ts / models.ts(模型图鉴) 等单一真相源
├── docs/                    # 设计文档、策略定稿、术语表(TERMS.md)
├── config.json              # 运行配置(含密钥, gitignore, 见下)
├── requirements.txt
└── deploy.ps1               # 部署脚本
```

---

## 本地运行

### 前置

- Python 3.14（仓库本地已统一为 D 盘唯一解释器，全依赖已装）
- Node.js 18+（前端）
- 一个可连接的 MySQL 实例

### 后端

```bash
# 安装依赖
pip install -r requirements.txt

# 准备 config.json(见下方配置说明), 然后从项目根启动
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8888
```

> 后端导入路径基于 `backend.*`，请始终从**项目根目录**运行。`init_db()` 会在启动时建表 + seed 默认定时任务。

### 前端

```bash
cd frontend
npm install
npm run dev        # 开发(Vite, 默认 5173)
npm run build      # 生产构建(vue-tsc 类型检查 + vite build → dist/)
```

### 测试

```bash
pytest             # 后端单测
cd frontend && npm run build   # 前端类型检查作为门禁
```

---

## 配置（config.json）

`config.json` 含数据库口令、AI Key、推送 Webhook 等敏感信息，**已 gitignore，不入库**。部署时也必须排除 `config.json`，避免覆盖生产配置。

字段说明（值用占位符）：

```jsonc
{
  "push_enabled": false,                 // 个人微信(PushPlus)推送总开关
  "lark_enabled": true,                  // 飞书推送总开关
  "lark_webhook": "<飞书机器人 webhook>",
  "scan_interval_seconds": 30,           // 盘中扫描间隔
  "trading_hours": [                     // 交易时段(后台任务闸门)
    { "start": "09:25", "end": "11:30" },
    { "start": "13:00", "end": "15:00" }
  ],
  "ai_report_enabled": true,
  "ai_base_url": "https://api.deepseek.com/v1",
  "ai_model": "deepseek-v4-pro",
  "anthropic_api_key": "<AI 接口 Key>",  // 复用字段名, 实际指向 ai_base_url
  "database": {
    "host": "<MySQL host>",
    "port": 3306,
    "user": "<user>",
    "password": "<password>",
    "db": "<dbname>"
  },
  "site_url": "http://<部署地址>"
}
```

---

## 数据源约定

- 外部行情 / 公告优先 **新浪、腾讯、巨潮 cninfo**；**尽量避免东方财富**（生产服务器出口 IP 被东财封禁）。
- 同花顺接口（人气榜、博主发帖）有 IP 反爬，高频请求会临时封出口 IP，已加指数退避。
- 数据库表统一 `cfzy_` 前缀（`cfzy_sys_*` 系统/行情缓存、`cfzy_biz_*` 业务）。

---

## 推送通道

- **飞书**：机器人 webhook，卡片走 schema 2.0 原生 table 组件，内置快捷设置（点链接即静音/snooze/关模型）。推送模版有 1:1 预览页。
- **个人微信**：通过 **PushPlus** 转发（企业微信已移除）。
- 推送统一经 `send_wechat_*` 全渠道入口 + 偏好闸门 + `is_production` 缓存控制。

---

## 部署

`deploy.ps1` 负责：前端构建 → 打包（**排除 `config.json`**）→ ssh 上传服务器解压 → 重启 `trading-monitor` 服务。

注意事项：

- 部署用 `tar -xzf` 解压，**只覆盖不删除**——删除文件需在服务器上显式 `rm`。
- 长回测（全市场 / 5 分钟口径）用 `systemd-run` 拉独立临时单元跑，脱离主服务，重启不中断。
- 每次改代码逻辑务必在 `frontend/src/data/changelog.ts` 顶部追加版本记录。

---

## 文档

- `docs/TERMS.md` — 项目术语规约（信号 / 预警 / 推送 / 报告 / 快照用词）
- `docs/回踩MA策略体系_定稿_20260601.md` — 核心买点策略定稿
- `docs/买点信号持有曲线分析_20260530.md`、`docs/短线盯盘模块_设计文档_v1.md` 等专题设计

---

## 免责声明

本系统输出的所有信号、回测胜率、AI 研判均为**历史统计与策略提示，非投资建议**。据此操作风险自负。
