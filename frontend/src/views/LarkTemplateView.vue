<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import client from '../api/client'

interface TemplateItem {
  id: string
  category: string
  name: string
  description: string
  timing: string
  card: any
}

const templates = ref<TemplateItem[]>([])
const updatedAt = ref<string>('')
const selectedId = ref<string>('')
const loading = ref(true)
const viewMode = ref<'pc' | 'mobile'>('pc')

const categories = computed(() => {
  const cats: Record<string, TemplateItem[]> = {}
  for (const t of templates.value) {
    if (!cats[t.category]) cats[t.category] = []
    cats[t.category].push(t)
  }
  return Object.entries(cats)
})

const selected = computed(() => templates.value.find(t => t.id === selectedId.value))

onMounted(async () => {
  try {
    const { data } = await client.get('/api/admin/lark-templates')
    templates.value = data.templates || []
    updatedAt.value = data.updated_at || ''
    if (templates.value.length > 0) selectedId.value = templates.value[0].id
  } finally {
    loading.value = false
  }
})

const cardWidth = computed(() => viewMode.value === 'mobile' ? '360px' : '100%')
const cardFontSize = computed(() => viewMode.value === 'mobile' ? '13px' : '14px')

// 基线 v1.1 信封三件: 锁屏摘要 / header 副标题 / header 彩色标签(≤3)
const cardSummary = computed(() => selected.value?.card?.config?.summary?.content || '')
const headerSubtitle = computed(() => selected.value?.card?.header?.subtitle?.content || '')
const headerTags = computed(() => {
  const list = selected.value?.card?.header?.text_tag_list
  return Array.isArray(list) ? list.slice(0, 3) : []
})

// 折叠面板(collapsible_panel)展开状态: 按 模版id:元素序号 记, 切模版不串状态
const panelOpen = ref<Record<string, boolean>>({})
function isPanelOpen(ei: number, def?: boolean): boolean {
  const k = `${selectedId.value}:${ei}`
  return k in panelOpen.value ? panelOpen.value[k] : !!def
}
function togglePanel(ei: number, def?: boolean) {
  const k = `${selectedId.value}:${ei}`
  panelOpen.value = { ...panelOpen.value, [k]: !isPanelOpen(ei, def) }
}
</script>

<template>
  <div class="lark-preview-page">
    <!-- Sidebar -->
    <aside class="sidebar">
      <h2>推送模版</h2>
      <div v-if="updatedAt" class="updated-at">更新 {{ updatedAt }}</div>
      <div v-if="loading" class="loading">加载中...</div>
      <template v-else>
        <div v-for="[cat, items] in categories" :key="cat" class="cat-group">
          <div class="cat-title">{{ cat }}</div>
          <div
            v-for="t in items" :key="t.id"
            class="tpl-item"
            :class="{ active: t.id === selectedId }"
            role="button"
            tabindex="0"
            @click="selectedId = t.id"
            @keydown.enter="selectedId = t.id"
          >
            <span class="tpl-name">{{ t.name }}</span>
            <span class="tpl-desc">{{ t.description }}</span>
            <span v-if="t.timing" class="tpl-timing">⏱ {{ t.timing }}</span>
          </div>
        </div>
      </template>
    </aside>

    <!-- Preview -->
    <main class="preview-area" v-if="selected">
      <!-- View toggle -->
      <div class="view-toggle-bar">
        <button :class="{ active: viewMode === 'pc' }" aria-label="PC 预览" @click="viewMode = 'pc'"><span aria-hidden="true">💻</span> PC</button>
        <button :class="{ active: viewMode === 'mobile' }" aria-label="手机预览" @click="viewMode = 'mobile'"><span aria-hidden="true">📱</span> 手机</button>
      </div>

      <!-- Phone frame wrapper -->
      <div class="preview-wrapper" :class="viewMode">
        <!-- Background -->
        <div class="lark-bg" v-if="viewMode === 'mobile'">
          <div class="phone-notch"></div>
        </div>

        <!-- 锁屏摘要预览(基线v1.1 config.summary): 模拟通知横幅, 让用户看到锁屏/会话列表效果 -->
        <div v-if="cardSummary" class="notify-banner" :class="viewMode">
          <span class="notify-bell" aria-hidden="true">🔔</span>
          <span class="notify-text">{{ cardSummary }}</span>
        </div>

        <!-- Lark Card -->
        <div class="lark-card" :class="viewMode" :style="{ fontSize: cardFontSize }">
          <!-- Header -->
          <div class="card-header" :class="selected.card.header?.template || 'blue'">
            <div class="header-bar"></div>
            <div class="header-main">
              <div class="header-title-row">
                <span class="header-title">{{ selected.card.header?.title?.content || '' }}</span>
                <!-- 彩色标签(基线v1.1 text_tag_list ≤3) -->
                <span v-for="(tg, ti) in headerTags" :key="ti"
                      class="hdr-tag" :class="tagColorClass(tg.color)">
                  {{ tg.text?.content || '' }}
                </span>
              </div>
              <!-- 副标题(基线v1.1 header.subtitle): 小一号灰字, 单行超长省略 -->
              <div v-if="headerSubtitle" class="header-subtitle">{{ headerSubtitle }}</div>
            </div>
          </div>

          <!-- V2 body -->
          <div class="card-body" v-if="selected.card.body?.elements">
            <template v-for="(el, ei) in selected.card.body.elements" :key="ei">
              <div v-if="el.tag === 'markdown'" class="el-markdown"
                   :class="{ 'el-heading': el.text_size === 'heading' }"
                   :style="{ textAlign: el.text_align || 'left' }"
                   v-html="renderMd(el.content)" />
              <!-- KPI 三栏(基线v1.1 column_set): 数字层 heading 大字 + 标签层小灰字, 手机端保持3栏字号略缩 -->
              <div v-else-if="el.tag === 'column_set'" class="el-columns">
                <div v-for="(col, ci) in (el.columns || [])" :key="ci" class="el-column">
                  <div v-for="(ce, cei) in (col.elements || [])" :key="cei"
                       class="el-markdown col-md"
                       :class="{ 'el-heading': ce.text_size === 'heading' }"
                       :style="{ textAlign: ce.text_align || 'center' }"
                       v-html="renderMd(ce.content)" />
                </div>
              </div>
              <!-- chart 占位框(基线v1.1): 按 aspect_ratio 的浅色框, 内部用真实数据画示意折线/柱 -->
              <div v-else-if="el.tag === 'chart'" class="el-chart"
                   :style="{ aspectRatio: chartBox(el).ratio }"
                   v-html="chartBox(el).svg" />
              <div v-else-if="el.tag === 'table'" class="el-table-wrap">
                <table class="el-table">
                  <thead>
                    <tr>
                      <th v-for="col in el.columns" :key="col.name"
                          :style="{ width: col.width, textAlign: col.horizontal_align || 'left' }">
                        {{ col.display_name }}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, ri) in el.rows" :key="ri">
                      <td v-for="col in el.columns" :key="col.name"
                          :style="{ textAlign: col.horizontal_align || 'left' }">
                        <template v-if="Array.isArray(row[col.name])">
                          <span v-for="opt in row[col.name]" :key="opt.text"
                                class="tag" :class="opt.color">{{ opt.text }}</span>
                        </template>
                        <template v-else>{{ row[col.name] }}</template>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else-if="el.tag === 'collapsible_panel'" class="el-collapsible">
                <div class="el-collapse-head" @click="togglePanel(ei, el.expanded)">
                  <span class="el-collapse-title" v-html="renderMd(el.header?.title?.content || '')" />
                  <span class="el-collapse-icon" :class="{ open: isPanelOpen(ei, el.expanded) }">▾</span>
                </div>
                <div v-if="isPanelOpen(ei, el.expanded)" class="el-collapse-body">
                  <div v-for="(ce, ci) in (el.elements || [])" :key="ci"
                       class="el-markdown" v-html="renderMd(ce.content)" />
                </div>
              </div>
            </template>
          </div>

          <!-- V1 body -->
          <div class="card-body" v-else-if="selected.card.elements">
            <template v-for="(el, ei) in selected.card.elements" :key="ei">
              <div v-if="el.tag === 'markdown'" class="el-markdown"
                   :style="{ textAlign: el.text_align || 'left' }"
                   v-html="renderMd(el.content)" />
              <div v-else-if="el.tag === 'div' && el.text?.tag === 'lark_md'"
                   class="el-markdown" v-html="renderMd(el.text.content)" />
              <div v-else-if="el.tag === 'action'" class="el-actions">
                <button v-for="act in el.actions" :key="act.text?.content"
                        class="lark-btn" :class="act.type">
                  {{ act.text?.content }}
                </button>
              </div>
            </template>
          </div>

          <!-- Bottom bar -->
          <div class="card-footer">
            <span class="footer-text">飞书卡片消息</span>
          </div>
        </div>
      </div>

    </main>
  </div>
</template>

<script lang="ts">
function renderMd(content: string): string {
  if (!content) return ''
  return content
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')   // 先转义防 v-html XSS
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // 链接只放行 http(s), 防 javascript: 等协议注入
    .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\n/g, '<br>')
}

// 飞书 text_tag 枚举色 → chip 样式类(未知色回退 grey)
const TAG_COLORS = new Set([
  'red', 'carmine', 'orange', 'yellow', 'green', 'turquoise',
  'blue', 'wathet', 'indigo', 'purple', 'violet', 'lime', 'grey', 'neutral',
])
function tagColorClass(color?: string): string {
  const c = (color || '').toLowerCase()
  if (c === 'neutral') return 'grey'
  return TAG_COLORS.has(c) ? c : 'grey'
}

// chart 元素(基线v1.1)占位渲染: 不接真实图表库, 按 chart_spec.data[0].values 画示意
// 折线(polyline+点)或柱(rect), 颜色取 chart_spec.color[0], 宽高比取 aspect_ratio(2:1/1:1/4:3/16:9)
function chartBox(el: any): { ratio: string; svg: string } {
  const spec = el?.chart_spec || {}
  const raw = (spec.data && spec.data[0] && spec.data[0].values) || []
  const vals: number[] = (Array.isArray(raw) ? raw : []).map((v: any) => Number(v?.y) || 0)
  let color: string = (Array.isArray(spec.color) && spec.color[0]) || '#4b8ef0'
  if (!/^#[0-9a-fA-F]{3,8}$|^[a-zA-Z]+$/.test(color)) color = '#4b8ef0'
  const isBar = spec.type === 'bar'
  const m = /^(\d+):(\d+)$/.exec(String(el?.aspect_ratio || '2:1'))
  const rw = m ? Number(m[1]) : 2
  const rh = m ? Number(m[2]) : 1
  const W = 200
  const H = Math.round((W * rh) / rw)
  const padX = 10, padTop = 8, padBot = 10
  const iw = W - padX * 2, ih = H - padTop - padBot
  let inner = ''
  if (vals.length) {
    let min = Math.min(...vals)
    let max = Math.max(...vals)
    if (isBar) min = Math.min(0, min)          // 柱状从 0 基线起
    if (max === min) max = min + 1             // 全等值防除零
    const yPix = (y: number) => padTop + ih - ((y - min) / (max - min)) * ih
    if (isBar) {
      const gap = iw / vals.length
      const bw = gap * 0.55
      const base = yPix(Math.max(0, min))
      inner = vals.map((y, i) => {
        const x = padX + gap * i + (gap - bw) / 2
        const yv = yPix(y)
        const top = Math.min(base, yv)
        const h = Math.max(1, Math.abs(base - yv))
        return `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="1" fill="${color}" opacity="0.85"/>`
      }).join('')
    } else {
      const step = vals.length > 1 ? iw / (vals.length - 1) : 0
      const pts = vals.map((y, i) => [padX + step * i, yPix(y)] as const)
      inner = `<polyline points="${pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')}"`
        + ` fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>`
      if (spec.point && spec.point.visible) {
        inner += pts.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="1.8" fill="${color}"/>`).join('')
      }
    }
  } else {
    // 无数据: 画一条中位虚线占位
    const y = (padTop + ih / 2).toFixed(1)
    inner = `<line x1="${padX}" y1="${y}" x2="${W - padX}" y2="${y}" stroke="${color}" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.5"/>`
  }
  const svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="图表示意">${inner}</svg>`
  return { ratio: `${rw} / ${rh}`, svg }
}
</script>

<style scoped>
.lark-preview-page { display: flex; height: calc(100vh - 60px); background: #f5f6f7; }

/* Sidebar */
.sidebar {
  width: 260px; background: #fff; border-right: 1px solid #e5e5e5;
  overflow-y: auto; padding: 16px; flex-shrink: 0;
}
.sidebar h2 { font-size: 16px; margin: 0 0 16px; color: #1f1f1f; }
.loading { color: #999; font-size: 13px; }
.cat-group { margin-bottom: 16px; }
.cat-title { font-size: 11px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; padding-left: 4px; }
.tpl-item { padding: 8px 8px; border-radius: 6px; cursor: pointer; margin-bottom: 2px; transition: background .15s; touch-action: manipulation; }
.tpl-item:hover { background: #f0f0f0; }
.tpl-item.active { background: #e8f0fe; }
.tpl-name { display: block; font-size: 13px; color: #1f1f1f; font-weight: 500; }
.tpl-desc { display: block; font-size: 11px; color: #999; margin-top: 1px; }
.tpl-timing { display: block; font-size: 10px; color: #bbb; margin-top: 1px; }
.updated-at { font-size: 11px; color: #999; margin-bottom: 12px; }

/* Preview area */
.preview-area { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; align-items: center; }

.view-toggle-bar { display: flex; justify-content: center; gap: 4px; margin-bottom: 16px; }
.view-toggle-bar button {
  padding: 4px 10px; border: 1px solid #ddd; border-radius: 4px;
  background: #fff; font-size: 12px; cursor: pointer; transition: all .15s;
  touch-action: manipulation;
}
.view-toggle-bar button.active { background: #4b8ef0; color: #fff; border-color: #4b8ef0; }

/* Preview wrapper */
.preview-wrapper { transition: all .3s; }
.preview-wrapper.pc { width: 100%; max-width: 520px; }
.preview-wrapper.mobile {
  width: 360px; border-radius: 24px; overflow: hidden;
  box-shadow: 0 0 0 2px #333, 0 0 0 4px #555, 0 4px 20px rgba(0,0,0,.3);
}
.lark-bg {
  background: #f0f1f2; padding: 12px 0 8px; text-align: center;
}
.phone-notch { width: 60px; height: 4px; background: #ccc; border-radius: 2px; margin: 0 auto; }

/* Lark card — 1:1 with real Feishu */
.lark-card {
  background: #fff; overflow: hidden; line-height: 1.6; color: #1f1f1f;
}
.lark-card.pc { border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.lark-card.mobile { border-radius: 0; box-shadow: none; }

/* 锁屏摘要通知横幅(config.summary 预览) */
.notify-banner {
  display: flex; align-items: center; gap: 6px;
  background: #eceef1; border: 1px solid #e2e4e8; border-radius: 8px;
  padding: 6px 10px; margin-bottom: 8px;
  font-size: 12px; color: #666;
}
.notify-banner.mobile { border-radius: 0; border-left: none; border-right: none; margin-bottom: 0; background: #e9ebee; }
.notify-bell { flex-shrink: 0; font-size: 12px; }
.notify-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Header */
.card-header { display: flex; align-items: stretch; min-height: 44px; }
.header-bar { width: 4px; flex-shrink: 0; border-radius: 0; }
.card-header.red .header-bar    { background: #e65c5c; }
.card-header.green .header-bar  { background: #45b078; }
.card-header.blue .header-bar   { background: #4b8ef0; }
.card-header.orange .header-bar { background: #f0a040; }
.card-header.yellow .header-bar { background: #e6b840; }
.card-header.grey .header-bar   { background: #8f959e; }
.card-header.purple .header-bar { background: #935af6; }
.card-header.carmine .header-bar { background: #d94a8c; }
.card-header.wathet .header-bar { background: #3da8f5; }
.card-header.turquoise .header-bar { background: #14b0b0; }
.header-main {
  flex: 1; min-width: 0; display: flex; flex-direction: column;
  justify-content: center; gap: 1px; padding: 6px 16px;
}
.lark-card.mobile .header-main { padding: 6px 12px; }
.header-title-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
.header-title {
  font-size: 15px; font-weight: 600; color: #1f1f1f;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
}
.lark-card.mobile .header-title { font-size: 14px; }

/* header 副标题(subtitle): 小一号灰字, 单行省略 */
.header-subtitle {
  font-size: 11px; color: #8f959e; font-weight: 400;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lark-card.mobile .header-subtitle { font-size: 10px; }

/* header 彩色标签(text_tag_list): 小圆角 chip, 飞书枚举色 */
.hdr-tag {
  flex-shrink: 0; font-size: 10px; line-height: 1; font-weight: 500;
  padding: 3px 6px; border-radius: 4px;
  background: #f2f3f5; color: #646a73; white-space: nowrap;
}
.lark-card.mobile .hdr-tag { font-size: 9px; padding: 2px 5px; }
.hdr-tag.red       { background: #feeceb; color: #d83931; }
.hdr-tag.carmine   { background: #fde5ef; color: #b82879; }
.hdr-tag.orange    { background: #fff0e1; color: #de7802; }
.hdr-tag.yellow    { background: #fcf3ce; color: #997a00; }
.hdr-tag.green     { background: #e4f7e7; color: #2ea121; }
.hdr-tag.turquoise { background: #ddf6f0; color: #078372; }
.hdr-tag.blue      { background: #e1eaff; color: #245bdb; }
.hdr-tag.wathet    { background: #e0f3fb; color: #1177b0; }
.hdr-tag.indigo    { background: #e7e9fd; color: #4752e6; }
.hdr-tag.purple    { background: #f0e5fc; color: #7a35f0; }
.hdr-tag.violet    { background: #f9e2fb; color: #a922b5; }
.hdr-tag.lime      { background: #eff8c8; color: #667900; }
.hdr-tag.grey      { background: #f2f3f5; color: #646a73; }

/* Body */
.card-body { padding: 12px 16px; }
.lark-card.mobile .card-body { padding: 10px 12px; }
.el-markdown { margin-bottom: 8px; font-size: 14px; line-height: 1.65; color: #333; }
.el-markdown:last-child { margin-bottom: 0; }
.lark-card.mobile .el-markdown { font-size: 13px; }
.el-markdown :deep(strong) { color: #1f1f1f; font-weight: 600; }
.el-markdown :deep(a) { color: #2b6de8; text-decoration: none; }
.el-markdown :deep(font) { font-size: 12px; }

/* markdown text_size=heading: 大号加粗行(单行核心结论/KPI数字层) */
.el-markdown.el-heading { font-size: 20px; font-weight: 600; line-height: 1.35; color: #1f1f1f; }
.lark-card.mobile .el-markdown.el-heading { font-size: 17px; }
.el-markdown.el-heading :deep(font) { font-size: inherit; }
.el-markdown.el-heading :deep(strong) { font-weight: 600; }

/* KPI 三栏(column_set): 并排居中, 数字层 heading + 标签层小灰字; 手机端保持3栏字号略缩 */
.el-columns { display: flex; gap: 8px; margin: 10px 0; }
.el-column { flex: 1; min-width: 0; overflow: hidden; }
.el-column .el-markdown { margin-bottom: 2px; }
.el-column .el-markdown:last-child { margin-bottom: 0; }
.el-column .el-markdown:not(.el-heading) { font-size: 11px; color: #8f959e; }
.el-column .el-markdown :deep(font) { font-size: inherit; }
.lark-card.mobile .el-columns { gap: 4px; }
.lark-card.mobile .el-column .el-markdown.el-heading { font-size: 15px; }
.lark-card.mobile .el-column .el-markdown:not(.el-heading) { font-size: 10px; }

/* chart 占位框: 浅底+边框, 内部 SVG 按真实数据画示意折线/柱 */
.el-chart {
  width: 100%; margin: 10px 0;
  background: #fafbfc; border: 1px solid #ebedf0; border-radius: 6px;
  overflow: hidden;
}
.el-chart :deep(svg) { display: block; width: 100%; height: 100%; }

/* Table */
/* 折叠面板预览: 头部常显(第一句)+点击展开正文, 与飞书 collapsible_panel 一致 */
.el-collapsible { margin: 8px 0; border: 1px solid #ebedf0; border-radius: 6px; overflow: hidden; }
.el-collapse-head { display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 10px; cursor: pointer; background: #fafbfc; user-select: none; }
.el-collapse-head:hover { background: #f2f4f7; }
.el-collapse-title { flex: 1; min-width: 0; font-size: 13px; color: #333; line-height: 1.5; }
.el-collapse-title :deep(strong) { color: #1f1f1f; font-weight: 600; }
.el-collapse-icon { flex-shrink: 0; color: #999; font-size: 12px; transition: transform 0.2s; }
.el-collapse-icon.open { transform: rotate(180deg); }
.el-collapse-body { padding: 8px 10px; border-top: 1px solid #ebedf0; font-size: 13px; color: #555; line-height: 1.6; }

.el-table-wrap { margin: 10px 0; }
.el-table {
  width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px;
  border: 1px solid #e5e6eb; border-radius: 6px; overflow: hidden;
}
.lark-card.mobile .el-table { font-size: 11px; }
.el-table th {
  background: #f5f6f7; padding: 8px 10px; font-weight: 500; font-size: 12px;
  color: #8f959e; border-bottom: 1px solid #e5e6eb; text-align: left;
}
.lark-card.mobile .el-table th { padding: 6px 6px; font-size: 10px; }
.el-table td {
  padding: 8px 10px; border-bottom: 1px solid #f2f3f5; color: #1f1f1f;
}
.lark-card.mobile .el-table td { padding: 6px 6px; }
.el-table tr:last-child td { border-bottom: none; }

.tag { display: inline-block; padding: 2px 6px; border-radius: 3px; margin-right: 3px; font-size: 11px; }
.tag.red { background: #fef0f0; color: #e65c5c; }
.tag.green { background: #e8f5e9; color: #45b078; }

.el-actions { margin-top: 10px; }
.lark-btn { padding: 7px 18px; border: none; border-radius: 4px; font-size: 13px; }
.lark-btn.primary { background: #4b8ef0; color: #fff; }

/* Footer */
.card-footer {
  padding: 8px 16px; border-top: 1px solid #f2f3f5; display: flex; align-items: center; gap: 6px;
}
.lark-card.mobile .card-footer { padding: 6px 12px; }
.footer-text { font-size: 11px; color: #bbb; }

/* ===== 移动端适配(≤768): 侧栏顶部全宽 + 预览卡全宽, 消除横向溢出 ===== */
@media (max-width: 768px) {
  /* 横排改纵向堆叠, 不再钉死视口高 */
  .lark-preview-page { flex-direction: column; height: auto; }

  /* 分类侧栏从固定 260px 左栏改顶部全宽块 */
  .sidebar {
    width: 100%; flex-shrink: 1;
    border-right: none; border-bottom: 1px solid #e5e5e5;
  }

  /* 预览区允许收缩, 防 flex 子项撑破 */
  .preview-area { min-width: 0; width: 100%; padding: 16px 12px; }

  /* 预览卡全宽不溢出(PC/手机两种壳都收进容器宽) */
  .preview-wrapper.pc,
  .preview-wrapper.mobile { max-width: 100%; width: 100%; }
}

</style>
