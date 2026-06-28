<script setup lang="ts">
import { computed, h } from 'vue'
import { NDataTable } from 'naive-ui'
import type { DataTableColumn } from 'naive-ui'
import { useResponsive } from '../../composables/useResponsive'

/**
 * 响应式表格: 桌面/平板渲染标准 NDataTable; 手机按 mobileMode 降级。
 *   - card(默认): 每行→卡片。优先用 #card 插槽自定义; 未给则按列上 mobilePriority/mobileLabel 自动生成"标签: 值"卡片。
 *   - scroll: 保留表格, 首列固定, 套横滚(配合 mobile.css 的 overflow-x), 适合矩阵型宽表。
 *   - columns: 手机仍走表格, 但列集换成 mobileColumns(或 columns 中 mobilePriority 列)。
 * 详见 docs/superpowers/specs/2026-06-16-mobile-adaptation-foundation-design.md
 */

// 列定义在 NaiveUI 基础上扩展两个组件内识别字段(非原生)
export type ResponsiveColumn = DataTableColumn<any> & {
  mobilePriority?: boolean
  mobileLabel?: string
}

const props = withDefaults(defineProps<{
  columns: ResponsiveColumn[]
  data: any[]
  rowKey?: (row: any) => string | number
  loading?: boolean
  bordered?: boolean
  size?: 'small' | 'medium' | 'large'
  maxHeight?: string | number
  scrollX?: number
  mobileMode?: 'card' | 'scroll' | 'columns'
  /** 手机卡片/行渲染上限, 防无界拉伸 */
  mobileMax?: number
  /** 截断时是否显示"仅展示前 N 条"提示 */
  mobileMaxHint?: boolean
  mobileColumns?: ResponsiveColumn[]
}>(), {
  bordered: false,
  size: 'small',
  mobileMode: 'card',
  mobileMax: 300,
  mobileMaxHint: true,
})

const slots = defineSlots<{
  card?: (p: { row: any; index: number }) => any
}>()

const { isPhone } = useResponsive()

// 单元格取值: 有 render 用 render, 否则取 row[key]
function cellValue(col: any, row: any, index: number) {
  if (typeof col.render === 'function') return col.render(row, index)
  const v = col.key != null ? row[col.key] : ''
  return v == null || v === '' ? '-' : String(v)
}

// 自动卡片字段: 优先 mobilePriority 列; 若无标记则用全部带 key 的列
const cardFields = computed<any[]>(() => {
  const flagged = props.columns.filter((c: any) => c.mobilePriority)
  const base = flagged.length ? flagged : props.columns
  return base.filter((c: any) => c.key != null || typeof c.render === 'function')
})

// 手机精简列集(columns 模式)
const phoneColumns = computed<ResponsiveColumn[]>(() => {
  if (props.mobileColumns?.length) return props.mobileColumns
  const flagged = props.columns.filter((c: any) => c.mobilePriority)
  return flagged.length ? flagged : props.columns
})

// scroll 模式: 首列固定左侧
const scrollColumns = computed<ResponsiveColumn[]>(() =>
  props.columns.map((c, i) => (i === 0 ? { ...c, fixed: 'left' as const } : c)) as ResponsiveColumn[]
)

// 手机限量数据(card / columns 模式)
const limitedData = computed(() =>
  isPhone.value && props.mobileMode !== 'scroll'
    ? props.data.slice(0, props.mobileMax)
    : props.data
)
const truncated = computed(() => props.data.length > limitedData.value.length)

// 自动卡片单元格渲染(函数式组件, 复用列的 render)
const AutoCell = (p: { col: any; row: any; index: number }) =>
  h('div', { class: 'rt-card-row' }, [
    h('span', { class: 'rt-card-label' }, p.col.mobileLabel ?? p.col.title ?? ''),
    h('span', { class: 'rt-card-val' }, [cellValue(p.col, p.row, p.index)]),
  ])
</script>

<template>
  <!-- 手机: 卡片模式 -->
  <div v-if="isPhone && mobileMode === 'card'" class="rt-cards">
    <div v-for="(row, i) in limitedData" :key="rowKey ? rowKey(row) : i" class="rt-card">
      <slot name="card" :row="row" :index="i">
        <AutoCell v-for="(col, ci) in cardFields" :key="ci" :col="col" :row="row" :index="i" />
      </slot>
    </div>
    <div v-if="!data.length" class="rt-empty">暂无数据</div>
    <div v-else-if="truncated && mobileMaxHint" class="rt-more-hint">
      仅显示前 {{ limitedData.length }} 条，请用筛选缩小范围
    </div>
  </div>

  <!-- 手机: 精简列模式(仍走表格) -->
  <NDataTable
    v-else-if="isPhone && mobileMode === 'columns'"
    :columns="phoneColumns"
    :data="limitedData"
    :bordered="bordered"
    :size="size"
    :loading="loading"
    :row-key="rowKey"
    :scroll-x="scrollX"
    :max-height="maxHeight"
  />

  <!-- 桌面/平板, 以及手机 scroll 模式 -->
  <NDataTable
    v-else
    :columns="isPhone ? scrollColumns : columns"
    :data="data"
    :bordered="bordered"
    :size="size"
    :loading="loading"
    :row-key="rowKey"
    :resizable-columns="true"
    :scroll-x="scrollX"
    :max-height="maxHeight"
  />
</template>

<style scoped>
.rt-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rt-card {
  border: 1px solid var(--border, #eee);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--surface, #fff);
}
.rt-card-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
  line-height: 1.5;
}
.rt-card-label {
  color: var(--text3, #999);
  flex-shrink: 0;
}
.rt-card-val {
  color: var(--text1, #222);
  text-align: right;
  word-break: break-word;
}
.rt-empty,
.rt-more-hint {
  text-align: center;
  color: var(--text2, #888);
  font-size: 12px;
  padding: 14px 0;
}
</style>
