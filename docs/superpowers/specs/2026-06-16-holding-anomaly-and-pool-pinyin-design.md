# 持仓异动推送 + 股票池拼音检索 设计 (2026-06-16)

两个独立功能, 一并落地。

---

## 功能一: 持仓异动推送(扩展 holding_guard 守护族)

在已上线的 `holding_guard.py`(接近前高/盈利保护, 盘中 60s tick)里**加 5 条规则**, 复用同一 tick 的持仓拉取 / 实时行情 / 双推 / 节流, 不另起任务。只提醒不下单, 不落信号库。

### 规则与触发(已与用户确认默认值)

| 规则 | 触发 | 节流 |
|---|---|---|
| 🔥 涨停 | 封死涨停(价达涨停 且 卖一量=0) | 每股每日 1 次 |
| 🧊 跌停 | 封死跌停(价达跌停 且 买一量=0) | 每股每日 1 次 |
| ⚡ 急速拉升 | 约 3 分钟内涨幅 ≥ **+3pp** | 每股每日 ≤ 2 次 |
| 🪨 急速跳水 | 约 3 分钟内涨幅 ≤ **−3pp** | 每股每日 ≤ 2 次 |
| ⚠️ 封单松动 / 💥 开板 | 封板期间封单较峰值回落 **≥50% / ≥75%**(两档各 1 次);价脱离涨跌停=开板(1 次) | 见左 |

补充口径:
- **涨停/跌停只在封板那刻报**(不做临近提醒)。
- **急跌/跌停 与现有卖点去重**:当天该股已发过卖点(direction sell/reduce, `get_today_signals(1, code)`)则**抑制**急跌/跌停 heads-up, 避免与 −10%止损 / 跌破MA 重复。
- **封单增加不单独推**(信息价值低)。

### 封板 / 封单 判定(新浪五档)

扩展 `fetcher/quotes.py` 的新浪解析, 在 quote dict 增加一档买卖盘:
`bid1_vol`(买一量, fields[10]) / `bid1_price`(fields[11]) / `ask1_vol`(卖一量, fields[20]) / `ask1_price`(fields[21])。

- **涨停封死** = `is_limit_up(price)` 且 `ask1_vol == 0`(无人卖, 全堆买一=涨停价)。封单额 = `bid1_vol × bid1_price`。
- **跌停封死** = `is_limit_down(price)` 且 `bid1_vol == 0`。封单额 = `ask1_vol × ask1_price`。
- **开板** = 曾封死、现 `ask1_vol > 0`(涨停)或价跌破涨停价。
- 走备用源(腾讯/东财)无五档 → 封单/开板/松动 不可判 → 仅保留 pct 近似的基础涨停/跌停提醒(封单行省略, 沿用"拿不到字段就省")。

### 涨跌停阈值(板别 + ST, 自实现, 不依赖 bt 脚本)

`limit_pct(code, name)`: 名含 "ST" → 0.05; `300/301/688` → 0.20; `8/43/92/920/4` 开头(北交所) → 0.30; 其余 → 0.10。
`is_limit_up(code,name,price,pre_close)` = `price >= pre_close*(1+pct)*0.995`;`is_limit_down` 对称 `<= pre_close*(1-pct)*1.005`。pre_close 来自新浪 quote。

### 急涨/急跌速率(进程内历史)

模块级 `_pct_hist: {code: [(ts, pct)]}`, 每 tick 追加, 剪枝 >720s。`surge_delta(hist, now_ts, now_pct, window=180)` = 当前 pct − ~180s 前 pct;不足一个窗口的历史 → None(不触发)。pct 为涨跌幅(百分点), 盘中 pre_close 恒定故 Δpct ≈ 价格变动%。重启清空(同节流, 可接受)。

### 封单峰值跟踪

模块级 `_seal_peak: {(code, side): 峰值封单额}`, 封板期间每 tick 取大;`seal_weaken_tier(peak, cur, tiers=(0.5,0.75))` 返回越过的最高档(cur ≤ peak*(1−tier))。开板/解封后清该键。

### 节流(GuardThrottle 改计数版, 向后兼容)

`GuardThrottle` 内部改用计数 dict:`count(code,rule,today)` / `mark(...)`(自增) / `throttled(code,rule,today,limit=1)`(count≥limit)。现有 接近前高/盈利保护 调用(默认 limit=1)行为不变;急拉/急跌用 limit=2。

### 文案构建(纯函数, 见早先草案)

`build_limit_up_msg / build_limit_down_msg / build_surge_msg / build_plunge_msg / build_seal_weaken_msg / build_board_open_msg`;`fmt_amount(元)` → "1.2亿" / "3,500万"。字段只用稳拿的(现价/涨跌幅/封单/量比/成交额), 缺则省略。推送走 `send_dual`, lark_title 分别如 "🔥 持仓异动·涨停"。

### tick 集成

每 tick 在现有 per-code 循环里(已有 quote/价格): 追加 `_pct_hist`;按上表判涨停/跌停/急涨/急跌/封单松动/开板, 过节流+去重后 `send_dual`。新浪 quote 才有五档;无则降级。

### 测试(TDD 纯函数)

`test_holding_anomaly.py`: limit_pct(板别+ST) / is_limit_up/down 边界 / seal_amount(涨跌停取对侧、缺档返 None) / 涨停封死=卖一量0、开板=卖一量>0 / surge_delta(够窗/不够窗/正负) / seal_weaken_tier(50/75/未到) / fmt_amount(亿/万) / 计数节流(急拉第3次被挡) / 各文案含关键字段。

---

## 功能二: 股票池拼音检索(纯前端, 扩展 v1.7.419 筛选)

现有 `usePoolFilter.fKeyword` 已匹配 代码+名称 子串(埋在高级面板)。本次:
1. **加拼音首字母匹配**: 关键词同时匹配 `代码 / 名称 / 名称拼音首字母`(输入 `gzmt` 命中 贵州茅台)。
2. **搜索框提到池子顶部**(常用功能不埋), 高级面板里的同项移除。

### 拼音首字母来源(纯前端轻量)

新增 `frontend/src/utils/pinyin.ts`: 内置常用汉字→声母首字母映射(GB2312 一级字按拼音序的紧凑串, 索引取首字母), `pinyinInitials(name)` → 逐字取首字母拼成小写串(非汉字原样保留小写)。无第三方依赖、覆盖 A 股名足够(多音字少数偏差可接受, 检索容错)。

### 接线

- `usePoolFilter`: keyword 匹配处 `hay` 增加 `pinyinInitials(s.name)`;`includes(kw)` 命中即可。
- `PoolView.vue`: 在池子表格上方放一个搜索 `NInput`(绑定 `pf.fKeyword`, placeholder "代码/名称/拼音 查找…", clearable);移除高级面板里的「代码/名称」项。
- 移动端: 顶部搜索框单行自适应, 沿用响应式。

### 测试

拼音为纯前端无 pytest;靠 vue-tsc + 构建 + 手动核对(gzmt→贵州茅台, 600519/茅台 子串仍命中)。

---

## 不做(YAGNI)

- 封单增加提醒;涨停临近提前提醒;炸板后回封二次提醒;拼音全拼/多音字词典;异动落信号库。后续按需。

## 变更记录

`changelog.ts` 头部加版本(持仓异动 + 拼音检索, 预计 v1.7.438)。
