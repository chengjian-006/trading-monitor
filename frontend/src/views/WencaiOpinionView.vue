<script setup lang="ts">
// 问财观点参考 (v1.7.627) — 同花顺问财 chat「智能调度」投顾式推荐的存档。
// 由本地油猴脚本(登录态浏览器发 stream-query SSE)手动问一句、上报整段话术, 服务器撞字典抽出被提及的股票。
// 明确定位: 这是 LLM 投顾观点, 非回测背书的信号。点股弹K线, 可一键加自选。
import { ref, onMounted, computed } from 'vue'
import { NButton, NIcon, NSkeleton, NEmpty, NTag, NPopconfirm, NCollapse, NCollapseItem } from 'naive-ui'
import { RefreshOutline, TrashOutline, BulbOutline, AddCircleOutline } from '@vicons/ionicons5'
import { listWencaiOpinions, deleteWencaiOpinion, addWencaiToPool, type WencaiOpinion, type WencaiConclusion } from '../api/wencai'
import { useUiStore } from '../stores/ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'

const ui = useUiStore()
const message = useGlobalMessage()

const opinions = ref<WencaiOpinion[]>([])
const loading = ref(false)
const loaded = ref(false)
const addingId = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const { opinions: rows } = await listWencaiOpinions()
    opinions.value = rows
  } catch {
    message.error('加载问财观点失败')
  } finally {
    loading.value = false
    loaded.value = true
  }
}

function fmtTime(t: string | null) {
  return t ? String(t).slice(0, 16).replace('T', ' ') : ''
}

function modeLabel(m: string) {
  if (m === 'deep_research') return '深度研究'
  if (m === 'normal') return '普通'
  return m || '普通'
}

// 话术轻量渲染: 转义 HTML → **加粗** 转 <strong> → 换行转 <br>
function renderAnswer(text: string): string {
  // 先去掉问财内嵌图表/模型占位块(```visual{...uuid...}```), 纯文本里是噪音
  const cleaned = (text || '')
    .replace(/```\s*visual[\s\S]*?```/gi, '')
    .replace(/```[\s\S]*?```/g, (m) => (/"uuid"\s*:/.test(m) ? '' : m))
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  const esc = cleaned
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}

function hasConclusion(c: WencaiConclusion | undefined): boolean {
  return !!c && !!(c.stock || c.buy || c.takeProfit || c.stopLoss || c.logic || c.risk)
}
function conclusionRows(c: WencaiConclusion | undefined) {
  const defs: [string, string, string | undefined][] = [
    ['📌', '主推', c?.stock], ['🟢', '买点', c?.buy], ['🎯', '止盈', c?.takeProfit],
    ['🛑', '止损', c?.stopLoss], ['⏳', '周期', c?.period], ['💡', '逻辑', c?.logic], ['⚠️', '风险', c?.risk],
  ]
  return defs.filter((d) => d[2]).map((d) => ({ icon: d[0], label: d[1], val: d[2] as string }))
}

async function removeOpinion(id: number) {
  try {
    await deleteWencaiOpinion(id)
    opinions.value = opinions.value.filter((o) => o.id !== id)
    message.success('已删除')
  } catch {
    message.error('删除失败')
  }
}

async function addStocksToPool(op: WencaiOpinion) {
  const picks = op.stocks.map((s) => ({ code: s.code, name: s.name }))
  if (!picks.length) return
  addingId.value = op.id
  try {
    const r = await addWencaiToPool(picks)
    message.success(`已加入自选 ${r.added} 只`)
  } catch {
    message.error('加自选失败')
  } finally {
    addingId.value = null
  }
}

const hasData = computed(() => opinions.value.length > 0)

onMounted(load)
</script>

<template>
  <div class="opinion-view">
    <div class="head">
      <div class="title-wrap">
        <h2>问财观点参考</h2>
        <NButton size="small" secondary :loading="loading" @click="load">
          <template #icon><NIcon :component="RefreshOutline" /></template>
          刷新
        </NButton>
      </div>
      <div class="note">
        <NIcon :component="BulbOutline" />
        <span>同花顺问财 chat「智能调度」的投顾式推荐存档。这是 <b>LLM 投顾观点</b>,同一问题不同时候答案会变,<b>非回测背书的买卖信号</b>,仅供参考。由本地油猴脚本手动问一句后上报。</span>
      </div>
    </div>

    <div v-if="!loaded" class="cards">
      <NSkeleton v-for="i in 3" :key="i" height="120px" style="margin-bottom:12px;border-radius:10px" />
    </div>

    <NEmpty v-else-if="!hasData" description="还没有问财观点" class="empty">
      <template #extra>
        <div class="empty-hint">在浏览器登录 iwencai 后, 用「问财观点上报」油猴脚本手动问一句, 结果会出现在这里。</div>
      </template>
    </NEmpty>

    <div v-else class="cards">
      <div v-for="op in opinions" :key="op.id" class="op-card">
        <div class="op-top">
          <div class="q">{{ op.question }}</div>
          <div class="meta">
            <NTag v-if="op.uploader" size="small" type="info" round>{{ op.uploader }}</NTag>
            <NTag size="small" :type="op.agent_mode === 'deep_research' ? 'warning' : 'default'" round>
              {{ modeLabel(op.agent_mode) }}
            </NTag>
            <span class="time">{{ fmtTime(op.created_at) }}</span>
            <NPopconfirm @positive-click="removeOpinion(op.id)">
              <template #trigger>
                <NButton size="tiny" quaternary circle>
                  <template #icon><NIcon :component="TrashOutline" /></template>
                </NButton>
              </template>
              删除这条观点?
            </NPopconfirm>
          </div>
        </div>

        <div v-if="hasConclusion(op.conclusion)" class="concl">
          <div class="concl-h">🎯 结论速览</div>
          <div v-for="row in conclusionRows(op.conclusion)" :key="row.label" class="concl-row">
            <span class="ci">{{ row.icon }}</span><span class="cl">{{ row.label }}</span><span class="cv">{{ row.val }}</span>
          </div>
        </div>

        <div v-if="op.stocks.length" class="stocks">
          <span class="lbl">提及个股:</span>
          <NTag
            v-for="s in op.stocks" :key="s.code"
            size="small" :type="s.primary ? 'primary' : 'default'"
            :bordered="!s.primary" checkable :checked="s.primary"
            class="stk" @click="ui.openStock(s.code, s.name)"
          >
            {{ s.name }}<span class="code">{{ s.code }}</span>
          </NTag>
          <NButton
            size="tiny" secondary type="primary" class="add-btn"
            :loading="addingId === op.id" @click="addStocksToPool(op)"
          >
            <template #icon><NIcon :component="AddCircleOutline" /></template>
            加自选
          </NButton>
        </div>
        <div v-else class="stocks no-stk">未从话术中识别出具体个股(可能是纯观点/多票对比, 见下方原文)</div>

        <NCollapse class="ans-collapse">
          <NCollapseItem title="展开完整分析" name="ans">
            <div class="answer" v-html="renderAnswer(op.answer_text)"></div>
          </NCollapseItem>
          <NCollapseItem v-if="op.reasoning" title="思考过程" name="rz">
            <div class="reasoning">{{ op.reasoning }}</div>
          </NCollapseItem>
        </NCollapse>
      </div>
    </div>
  </div>
</template>

<style scoped>
.opinion-view { padding: 16px; max-width: 900px; margin: 0 auto; }
.head { margin-bottom: 16px; }
.title-wrap { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.title-wrap h2 { margin: 0; font-size: 18px; }
.note {
  display: flex; align-items: flex-start; gap: 6px;
  font-size: 12px; color: var(--text-3, #888); line-height: 1.6;
  background: var(--card-2, rgba(240, 200, 60, 0.08)); padding: 8px 10px; border-radius: 8px;
}
.note b { color: var(--text-2, #d09a2a); }
.cards { display: flex; flex-direction: column; gap: 12px; }
.op-card {
  border: 1px solid var(--border, rgba(0,0,0,0.08)); border-radius: 10px;
  padding: 12px 14px; background: var(--card, #fff);
}
.op-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.q { font-weight: 600; font-size: 14px; line-height: 1.5; flex: 1; }
.meta { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.time { font-size: 11px; color: var(--text-3, #999); }
.stocks { margin-top: 10px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.lbl { font-size: 12px; color: var(--text-3, #999); }
.stk { cursor: pointer; }
.stk .code { margin-left: 4px; opacity: 0.6; font-size: 11px; }
.no-stk { font-size: 12px; color: var(--text-3, #999); }
.add-btn { margin-left: 4px; }
.ans-collapse { margin-top: 6px; }
.answer { font-size: 13px; line-height: 1.7; white-space: normal; word-break: break-word; }
.reasoning { font-size: 12px; line-height: 1.7; color: var(--text-3, #94a3b8); white-space: pre-wrap; word-break: break-word; }
.concl { margin: 10px 0; padding: 10px 12px; border: 1px solid var(--border, rgba(0,0,0,0.08)); border-radius: 10px; background: var(--card-2, rgba(37,99,235,0.04)); }
.concl-h { font-weight: 700; font-size: 13px; margin-bottom: 6px; }
.concl-row { display: flex; gap: 8px; align-items: flex-start; padding: 3px 0; font-size: 13px; line-height: 1.5; }
.concl-row .ci { flex-shrink: 0; }
.concl-row .cl { flex-shrink: 0; width: 34px; color: var(--text-3, #888); }
.concl-row .cv { flex: 1; }

@media (max-width: 768px) {
  .opinion-view { padding: 12px; }
  .op-top { flex-direction: column; gap: 6px; }
  .meta { align-self: flex-end; }
}
</style>
