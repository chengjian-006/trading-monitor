# 系统自选 ⇄ 同花顺自选 对比同步 — 设计

日期: 2026-06-15
分支: trade-rounds-phase1

## 目标
把系统自选股与同花顺「自选股」主表做双向对比,列出差异个股,并提供"新增到系统 / 从系统删除"两个动作,帮助用户保持两边一致。

## 决策(已与用户确认)
1. **对比基准**: 同花顺「自选股」主表(`SelfStockCache.json`),不按自定义分组对比。
2. **删除范围**: 只动系统这边,同花顺文件始终只读、绝不反写(客户端正在读写,反写有冲突/损坏风险)。
3. **删除方式**: 逻辑删除(`remove_stock`,出池不可见但保留历史信号/回测样本)。
4. **入口位置**: 扩展现有「自选导入」弹窗(`PoolView.vue` 的 `showThsModal`),加分段切换「导入分组 / 对比自选」。
5. **新增口径**: 默认 `短线 / 关注`(同现有导入与 batch-import)。

## 架构

### 后端
- 新增 `GET /api/ths/compare`(`routers/ths.py`):
  1. 读用户 THS 路径 → `find_selfstock_cache` → `parse_selfstock_cache` 得同花顺自选代码集 `ths_codes`(已按 0/3/6 开头 6 位过滤,港股/指数/基金天然不入)。
  2. 读 `list_stocks(user_id)`,剔除 `trade_type='index'` 的指数条目,得系统代码集 `sys_codes` + 名称表。
  3. 差集:
     - `ths_only` = ths_codes − sys_codes(同花顺有、系统缺)→ 服务端用 `search_stock` 补名(差集通常很小)。
     - `system_only` = sys_codes − ths_codes(系统有、同花顺缺)→ 名称取自池。
  4. 返回 `{ok, ths_count, system_count, both, ths_only:[{code,name}], system_only:[{code,name}]}`;
     同花顺文件缺失 → `{ok:false, msg}`。
- 新增 `POST /api/stocks/batch-delete {codes}`(`routers/stocks.py`):逐个 `remove_stock`(逻辑删除)+ 写一条操作日志,返回 `{ok, deleted}`。
- 新增动作复用 `POST /api/stocks/batch-import`(已存在:补名 + 刷行情)。

### 前端
- `api/config.ts` 加 `fetchThsCompare()`;`api/stocks.ts` 加 `batchDeleteStocks(codes)`。
- `types.ts` 加 `ThsCompareResult`。
- `PoolView.vue` 在 THS 弹窗顶部加分段(`NTabs` 或 segment):
  - **导入分组**: 保留现状。
  - **对比自选**: 打开即拉 `compare`,展示
    - 摘要行:同花顺 N · 系统 M · 共有 K
    - 「同花顺有 · 系统缺」勾选列表(全选/清空)→「新增选中到系统」→ batch-import
    - 「系统有 · 同花顺缺」勾选列表(全选/清空)→「删除选中(出池)」(二次确认)→ batch-delete
  - 动作完成后 `stockStore.loadStocks(true)` + 重新拉 compare。
  - 复用 OCR 弹窗的勾选列表视觉风格;消息提示用 `useGlobalMessage`。

## 数据流
打开对比 tab → compare 一次返回两份差异 → 用户勾选 → 新增走 batch-import / 删除走 batch-delete → 刷新池 + 重拉 compare。

## 边界
- THS 路径未配/文件缺失 → 复用现有空状态提示。
- 系统逻辑删除的票若在同花顺存在 → 显示"系统缺失",勾选新增即复活(`add_stock` 清 `deleted_at`)。
- 删除二次确认,防误清。

## 不做(YAGNI)
- 不反写同花顺文件;不做定时自动同步(仅手动);不做按分组对比;不做物理删除入口(只逻辑删除)。
