<script setup lang="ts">
// 藏龙岛观点 (v1.7.738) — 飞书群群主(藏龙岛)盘中点评/操作观点存档。
// 后端定时拉飞书群、只留藏龙岛发的入库(不推送, 用户本人飞书已收到)。纯留痕参考, 非回测背书的信号。
import { ref, onMounted, computed, watch } from 'vue'
import { NButton, NIcon, NSkeleton, NEmpty, NInput, NDatePicker } from 'naive-ui'
import { RefreshOutline, BulbOutline, ChatbubbleEllipsesOutline } from '@vicons/ionicons5'
import { listCoachPosts, fetchCoachMedia, type CoachPost, type CoachMentionedStock } from '../api/lark-coach'
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
// 分隔符是一串横杠(实测 5~12 个不等), 且横杠前不保证有空白(实见"出现。-----"直连),
// 故只要求「5个以上横杠+空白」; 引用段开头孤立的日期横杠(如 -07-21)一并去掉。
function splitMsg(content: string): { answer: string; quoted: string } {
  const m = content.match(/-{5,}\s/)
  if (!m || m.index === undefined) return { answer: content.trim(), quoted: '' }
  const quoted = content.slice(m.index + m[0].length).trim().replace(/^-(?=\d)/, '')
  return { answer: content.slice(0, m.index).trim(), quoted }
}

// 正文只给「短标签＋中文冒号」(如 核心票：/注意一下：)加粗, 其余常规字重。
// 判定=紧跟全角冒号的1~8个非标点字符, 且位于开头或句读之后, 避免长句误判成标签。
function emphasize(text: string): { t: string; b: boolean }[] {
  const segs: { t: string; b: boolean }[] = []
  const re = /(^|[\s；;。！？!?、，,])([^\s，,。；;：:！？!?]{1,8})：/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const start = m.index + m[1].length
    if (start > last) segs.push({ t: text.slice(last, start), b: false })
    segs.push({ t: m[2] + '：', b: true })
    last = start + m[2].length + 1
  }
  if (last < text.length) segs.push({ t: text.slice(last), b: false })
  return segs
}

// 个股链接 (v1.7.780): 后端撞出正文里被提及的个股(code+name), 前端把股名/6位代码标成链接,
// 点击跳同花顺个股页。段落沿用 emphasize 的加粗标签, 再在其上切出链接子段。
const THS_STOCK_URL = (code: string) => `https://stockpage.10jqka.com.cn/${code}/`

interface LinkSeg { t: string; b: boolean; code?: string }
function buildSegs(text: string, stocks?: CoachMentionedStock[]): LinkSeg[] {
  const base = emphasize(text)
  if (!stocks || !stocks.length) return base
  // 名称与6位代码都可命中; 命中优先更长的 token(防「药明康德」被短名截断)
  const tokens: { token: string; code: string }[] = []
  for (const s of stocks) {
    if (s.name && s.name.length >= 2) tokens.push({ token: s.name, code: s.code })
    if (s.code && /^\d{6}$/.test(s.code)) tokens.push({ token: s.code, code: s.code })
  }
  tokens.sort((a, b) => b.token.length - a.token.length)
  const out: LinkSeg[] = []
  for (const seg of base) {
    let rest = seg.t
    while (rest) {
      let best: { idx: number; len: number; code: string } | null = null
      for (const tk of tokens) {
        const idx = rest.indexOf(tk.token)
        if (idx < 0) continue
        if (!best || idx < best.idx || (idx === best.idx && tk.token.length > best.len)) {
          best = { idx, len: tk.token.length, code: tk.code }
        }
      }
      if (!best) { out.push({ t: rest, b: seg.b }); break }
      if (best.idx > 0) out.push({ t: rest.slice(0, best.idx), b: seg.b })
      out.push({ t: rest.slice(best.idx, best.idx + best.len), b: seg.b, code: best.code })
      rest = rest.slice(best.idx + best.len)
    }
  }
  return out
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

// ── 图片消息: 拉 blob 转 objectURL 显示(接口带 JWT, <img src> 直连带不上) ──
const mediaUrls = ref<Record<string, string>>({})
const mediaFailed = ref<Record<string, boolean>>({})

function isImagePost(p: CoachPost): boolean {
  return p.msg_type === 'image'
}

function openImage(messageId: string) {
  const url = mediaUrls.value[messageId]
  if (url) window.open(url, '_blank')
}

watch(filtered, (list) => {
  for (const p of list) {
    if (!isImagePost(p) || mediaUrls.value[p.message_id] || mediaFailed.value[p.message_id]) continue
    fetchCoachMedia(p.message_id)
      .then((url) => { mediaUrls.value[p.message_id] = url })
      .catch(() => { mediaFailed.value[p.message_id] = true })
  }
}, { immediate: true })

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
            <template v-if="isImagePost(p)">
              <div class="answer"><span class="coach-name">{{ p.coach_name || '藏龙岛' }}：</span></div>
              <img v-if="mediaUrls[p.message_id]" :src="mediaUrls[p.message_id]" class="msg-img" alt="藏龙岛发的图片" @click="openImage(p.message_id)" />
              <div v-else-if="mediaFailed[p.message_id]" class="img-fallback">图片加载失败(原图见飞书群)</div>
              <NSkeleton v-else height="160px" width="240px" style="border-radius:8px" />
            </template>
            <template v-else>
              <div class="answer"><span class="coach-name">{{ p.coach_name || '藏龙岛' }}：</span><template v-for="(seg, si) in buildSegs(splitMsg(p.content).answer, p.stocks)" :key="si"><a v-if="seg.code" class="stock-link" :class="{ em: seg.b }" :href="THS_STOCK_URL(seg.code)" target="_blank" rel="noopener noreferrer" :title="`${seg.t} 同花顺个股页`" @click.stop>{{ seg.t }}</a><span v-else :class="seg.b ? 'em' : undefined">{{ seg.t }}</span></template></div>
              <div v-if="splitMsg(p.content).quoted" class="quoted">
                <NIcon :component="ChatbubbleEllipsesOutline" class="q-ico" />
                <span><template v-for="(seg, si) in buildSegs(splitMsg(p.content).quoted, p.stocks)" :key="si"><a v-if="seg.code" class="stock-link" :href="THS_STOCK_URL(seg.code)" target="_blank" rel="noopener noreferrer" :title="`${seg.t} 同花顺个股页`" @click.stop>{{ seg.t }}</a><template v-else>{{ seg.t }}</template></template></span>
              </div>
            </template>
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
.answer { font-size: 14px; line-height: 1.7; font-weight: 400; color: var(--fg-default); white-space: pre-wrap; word-break: break-word; }
.answer .coach-name { font-weight: 700; }
.answer .em { font-weight: 700; }
/* 个股链接: 主题蓝 + 虚下划线, 悬停实线; 点击跳同花顺个股页 */
.stock-link {
  color: var(--accent-fg); text-decoration: underline; text-decoration-style: dotted;
  text-underline-offset: 2px; cursor: pointer; font-weight: 600;
}
.stock-link:hover { text-decoration-style: solid; }
.stock-link.em { font-weight: 700; }
.quoted {
  margin-top: 10px; display: flex; gap: 6px; align-items: flex-start;
  font-size: 12px; line-height: 1.6; color: var(--fg-subtle);
  padding: 8px 10px; border-radius: 8px;
  background: color-mix(in srgb, var(--fg-default) 4%, transparent);
  border-left: 3px solid var(--border-hard);
}
.quoted .q-ico { flex-shrink: 0; margin-top: 2px; opacity: .6; }
.msg-img {
  display: block; margin-top: 6px; max-width: min(420px, 100%); max-height: 480px;
  border-radius: 8px; border: 1px solid var(--border-default); cursor: zoom-in;
}
.img-fallback { margin-top: 6px; font-size: 12px; color: var(--fg-subtle); }
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
