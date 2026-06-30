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

        <!-- Lark Card -->
        <div class="lark-card" :class="viewMode" :style="{ fontSize: cardFontSize }">
          <!-- Header -->
          <div class="card-header" :class="selected.card.header?.template || 'blue'">
            <div class="header-bar"></div>
            <span class="header-title">{{ selected.card.header?.title?.content || '' }}</span>
          </div>

          <!-- V2 body -->
          <div class="card-body" v-if="selected.card.body?.elements">
            <template v-for="(el, ei) in selected.card.body.elements" :key="ei">
              <div v-if="el.tag === 'markdown'" class="el-markdown"
                   :style="{ textAlign: el.text_align || 'left' }"
                   v-html="renderMd(el.content)" />
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
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/\n/g, '<br>')
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

/* Header */
.card-header { display: flex; align-items: center; height: 44px; }
.header-bar { width: 4px; height: 100%; flex-shrink: 0; border-radius: 0; }
.card-header.red .header-bar    { background: #e65c5c; }
.card-header.green .header-bar  { background: #45b078; }
.card-header.blue .header-bar   { background: #4b8ef0; }
.card-header.orange .header-bar { background: #f0a040; }
.card-header.yellow .header-bar { background: #e6b840; }
.header-title {
  padding: 0 16px; font-size: 15px; font-weight: 600; color: #1f1f1f;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
}
.lark-card.mobile .header-title { font-size: 14px; padding: 0 12px; }

/* Body */
.card-body { padding: 12px 16px; }
.lark-card.mobile .card-body { padding: 10px 12px; }
.el-markdown { margin-bottom: 8px; font-size: 14px; line-height: 1.65; color: #333; }
.el-markdown:last-child { margin-bottom: 0; }
.lark-card.mobile .el-markdown { font-size: 13px; }
.el-markdown :deep(strong) { color: #1f1f1f; font-weight: 600; }
.el-markdown :deep(a) { color: #2b6de8; text-decoration: none; }
.el-markdown :deep(font) { font-size: 12px; }

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

</style>
