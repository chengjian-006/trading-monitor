<script setup lang="ts">
// 藏龙岛观点 (v1.7.738) — 飞书群群主(藏龙岛)盘中点评/操作观点存档。
// 后端定时拉飞书群、只留藏龙岛发的入库(不推送, 用户本人飞书已收到)。纯留痕参考, 非回测背书的信号。
import { ref, onMounted, computed } from 'vue'
import { NButton, NIcon, NSkeleton, NEmpty, NInput, NDatePicker } from 'naive-ui'
import { RefreshOutline, BulbOutline, ChatbubbleEllipsesOutline } from '@vicons/ionicons5'
import { listCoachPosts, type CoachPost } from '../api/lark-coach'
import FilterPanel from '../components/common/FilterPanel.vue'
import { useGlobalMessage } from '../composables/useGlobalMessage'

const message = useGlobalMessage()

const posts = ref<CoachPost[]>([])
const loading = ref(false)
const loaded = ref(false)

const filterKeyword = ref('')
const filterDateRange = ref<[number, number] | null>(null)

function fmtDay(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 一条消息拆成「老师回答」+ 可选「被引用的提问」。
// lark-cli 把回复格式化成 "回答正文 ------------ -07-21 10:24 学员:提问",
// 分隔符是一串横杠(实测 12 个), 数量不作保证 —— 按「空白+5个以上横杠+空白」容错匹配。
function splitMsg(content: string): { answer: string; quoted: string } {
  const m = content.match(/\s-{5,}\s/)
  if (!m || m.index === undefined) return { answer: content.trim(), quoted: '' }
  return { answer: content.slice(0, m.index).trim(), quoted: content.slice(m.index + m[0].length).trim() }
}

const filtered = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  const range = filterDateRange.value
  const from = range ? fmtDay(range[0]) : ''
  const to = range ? fmtDay(range[1]) : ''
  return posts.value.filter((p) => {
    if (range) {
      const day = (p.posted_at || '').slice(0, 10)
      if (!day || day < from || day > to) return false
    }
    if (kw && !(p.content || '').toLowerCase().includes(kw)) return false
    return true
  })
})

// 按「日」分组(已按时间倒序), 组内保持倒序
const grouped = computed(() => {
  const groups: { day: string; items: CoachPost[] }[] = []
  for (const p of filtered.value) {
    const day = (p.posted_at || '').slice(0, 10) || '未知日期'
    let g = groups[groups.length - 1]
    if (!g || g.day !== day) { g = { day, items: [] }; groups.push(g) }
    g.items.push(p)
  }
  return groups
})

function timeOf(p: CoachPost): string {
  return (p.posted_at || '').slice(11, 16)
}

function resetFilters() {
  filterKeyword.value = ''
  filterDateRange.value = null
}

async function load() {
  loading.value = true
  try {
    const { posts: rows } = await listCoachPosts(300)
    posts.value = rows
  } catch {
    message.error('加载藏龙岛观点失败')
  } finally {
    loading.value = false
    loaded.value = true
  }
}

const hasData = computed(() => posts.value.length > 0)

onMounted(load)
</script>

<template>
  <div class="coach-view">
    <div class="head">
      <div class="title-wrap">
        <h2>藏龙岛观点</h2>
        <NButton size="small" secondary :loading="loading" @click="load">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          刷新
        </NButton>
      </div>
      <div class="note">
        <NIcon :component="BulbOutline" />
        <span>飞书群「藏龙岛」群主的盘中点评与操作观点存档,系统自动留痕。这是 <b>个人观点</b>,<b>非回测背书的买卖信号</b>,仅供参考。</span>
      </div>
    </div>

    <FilterPanel v-if="loaded && hasData">
      <div class="filter-bar">
        <div class="filter-fields">
          <div class="filter-item" style="min-width: 200px">
            <label for="coach-kw">关键词</label>
            <NInput
              v-model:value="filterKeyword"
              size="small"
              clearable
              placeholder="正文/个股/关键词"
              :input-props="{ id: 'coach-kw', name: 'keyword', type: 'search' }"
            />
          </div>
          <div class="filter-item" style="min-width: 220px">
            <label>时间段</label>
            <NDatePicker
              v-model:value="filterDateRange"
              type="daterange"
              size="small"
              clearable
              format="yyyy-MM-dd"
              placement="bottom"
              to="body"
            />
          </div>
        </div>
        <div class="filter-actions">
          <NButton size="small" secondary @click="resetFilters">
            <template #icon><NIcon :component="RefreshOutline" /></template>
            重置
          </NButton>
        </div>
      </div>
    </FilterPanel>

    <div v-if="!loaded" class="feed">
      <NSkeleton v-for="i in 4" :key="i" height="72px" style="margin-bottom:10px;border-radius:10px" />
    </div>

    <NEmpty v-else-if="!hasData" description="还没有藏龙岛观点" class="empty">
      <template #extra>
        <div class="empty-hint">后端开始拉群后,藏龙岛盘中发的观点会自动出现在这里。</div>
      </template>
    </NEmpty>

    <div v-else class="feed">
      <NEmpty v-if="!filtered.length" description="没有匹配的观点" class="empty" />
      <div v-for="g in grouped" :key="g.day" class="day-group">
        <div class="day-label">{{ g.day }}</div>
        <div v-for="p in g.items" :key="p.id" class="msg">
          <div class="msg-time">{{ timeOf(p) }}</div>
          <div class="msg-body">
            <div class="answer"><span class="coach-name">{{ p.coach_name || '藏龙岛' }}：</span>{{ splitMsg(p.content).answer }}</div>
            <div v-if="splitMsg(p.content).quoted" class="quoted">
              <NIcon :component="ChatbubbleEllipsesOutline" class="q-ico" />
              <span>{{ splitMsg(p.content).quoted }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.coach-view { padding: 16px; max-width: 860px; margin: 0 auto; }
.head { margin-bottom: 16px; }
.title-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.title-wrap h2 { margin: 0; font-size: 18px; }
.note {
  display: flex; align-items: flex-start; gap: 6px;
  font-size: 12px; color: var(--fg-subtle); line-height: 1.6;
  background: color-mix(in srgb, var(--warn-fg) 8%, transparent); padding: 8px 10px; border-radius: 8px;
}
.note b { color: var(--warn-fg); }

.filter-bar {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 16px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px 24px;
  align-items: end;
}
.filter-fields { display: flex; gap: 12px; flex-wrap: wrap; }
.filter-item { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 120px; }
.filter-item label { font-size: 12px; color: var(--fg-subtle); white-space: nowrap; }
.filter-actions { display: flex; gap: 8px; align-items: flex-end; justify-content: flex-end; }

.feed { display: flex; flex-direction: column; gap: 18px; }
.day-group { display: flex; flex-direction: column; gap: 8px; }
.day-label {
  position: sticky; top: 0; z-index: 1;
  font-size: 12px; font-weight: 700; color: var(--fg-subtle);
  padding: 4px 0; letter-spacing: .02em;
}

/* 一条观点: 左侧时间 + 右侧正文卡 */
.msg { display: flex; gap: 12px; align-items: flex-start; }
.msg-time {
  flex-shrink: 0; width: 42px; text-align: right;
  font-size: 12px; font-weight: 600; color: var(--fg-subtle);
  font-variant-numeric: tabular-nums; padding-top: 12px;
}
.msg-body {
  flex: 1; min-width: 0;
  border: 1px solid var(--border-default); border-radius: 12px;
  padding: 12px 14px; background: var(--bg-surface);
  box-shadow: 0 1px 2px rgba(20,30,50,.05);
}
/* 老师的话加粗+主题蓝突出(用户指定); 学员引用保持灰色弱化形成对比 */
.answer { font-size: 14px; line-height: 1.7; font-weight: 600; color: var(--accent-fg); white-space: pre-wrap; word-break: break-word; }
.answer .coach-name { font-weight: 700; }
.quoted {
  margin-top: 10px; display: flex; gap: 6px; align-items: flex-start;
  font-size: 12px; line-height: 1.6; color: var(--fg-subtle);
  padding: 8px 10px; border-radius: 8px;
  background: color-mix(in srgb, var(--fg-default) 4%, transparent);
  border-left: 3px solid var(--border-hard);
}
.quoted .q-ico { flex-shrink: 0; margin-top: 2px; opacity: .6; }
.empty { margin-top: 40px; }
.empty-hint { font-size: 12px; color: var(--fg-subtle); }

@media (max-width: 768px) {
  .coach-view { padding: 12px; }
  .filter-bar { grid-template-columns: 1fr; }
  .filter-actions { justify-content: flex-start; }
  .msg { gap: 8px; }
  .msg-time { width: 38px; padding-top: 11px; }
  .answer { font-size: 13.5px; }
}
</style>
