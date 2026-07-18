<script setup lang="ts">
// 问财观点 · 看板紧凑面板 (v1.7.663): 折进监控看板, 显示最近几条 LLM 投顾观点摘要,
// 「查看全部」跳详情页 /wencai-opinion。原独立菜单项已移除。
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { NIcon, NButton } from 'naive-ui'
import { BulbOutline, RefreshOutline, ChevronForwardOutline } from '@vicons/ionicons5'
import { listWencaiOpinions, type WencaiOpinion } from '../../api/wencai'

const router = useRouter()
const opinions = ref<WencaiOpinion[]>([])
const loading = ref(false)

const latest = computed(() => opinions.value.slice(0, 4))

async function load() {
  loading.value = true
  try { opinions.value = (await listWencaiOpinions()).opinions || [] }
  catch { /* 静默: 面板非关键 */ }
  finally { loading.value = false }
}
onMounted(load)

function fmtTime(raw?: string): string {
  if (!raw) return ''
  const s = raw.replace('T', ' ')
  return s.slice(5, 16)   // MM-DD HH:mm
}
// 只显最终推荐(primary), 不回退到全部提及个股, 避免噪音干扰判断(v1.7.667)
function primaryStocks(op: WencaiOpinion) {
  return (op.stocks || []).filter((s) => s.primary).slice(0, 3)
}
</script>

<template>
  <div class="wo-panel">
    <div class="head">
      <div class="title"><NIcon :component="BulbOutline" :size="16" /><span>问财观点</span>
        <span v-if="opinions.length" class="meta">{{ opinions.length }} 条</span>
      </div>
      <div class="head-right">
        <NButton quaternary circle size="tiny" :loading="loading" title="刷新" aria-label="刷新" @click="load">
          <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
        </NButton>
        <button class="all-link" @click="router.push('/wencai-opinion')">查看全部<NIcon :component="ChevronForwardOutline" :size="12" /></button>
      </div>
    </div>

    <div v-if="!latest.length && !loading" class="empty">暂无问财观点 · 在 iwencai 点油猴上报后显示</div>

    <div v-else class="list">
      <div v-for="op in latest" :key="op.id" class="op" role="button" tabindex="0"
           @click="router.push('/wencai-opinion')" @keydown.enter="router.push('/wencai-opinion')">
        <div class="op-top">
          <div class="op-stocks">
            <span v-for="st in primaryStocks(op)" :key="st.code" class="st-chip">{{ st.name }}<i>{{ st.code }}</i></span>
            <span v-if="!primaryStocks(op).length" class="no-stock">未识别个股</span>
          </div>
          <span class="op-time">{{ fmtTime(op.created_at) }}<template v-if="op.uploader"> · {{ op.uploader }}</template></span>
        </div>
        <div class="op-q">{{ op.question }}</div>
        <div v-if="op.conclusion && op.conclusion.logic" class="op-c">{{ op.conclusion.logic }}</div>
      </div>
    </div>
    <p class="foot">LLM 投顾观点仅供参考, 非系统买卖信号。</p>
  </div>
</template>

<style scoped>
.wo-panel { background: var(--bg-surface); border: 1px solid var(--border-default); border-radius: 6px; padding: 10px 12px; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--border-muted); }
.title { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 700; letter-spacing: .02em; color: var(--fg-default); }
.title .meta { font-family: var(--font-mono); font-size: 10.5px; font-weight: 500; color: var(--fg-subtle); margin-left: 4px; }
.head-right { display: flex; align-items: center; gap: 6px; }
.all-link { appearance: none; border: 0; background: transparent; font: inherit; font-size: 12px; color: var(--accent-fg); cursor: pointer; display: inline-flex; align-items: center; gap: 1px; }
.all-link:hover { text-decoration: underline; }

.list { margin-top: 8px; display: flex; flex-direction: column; gap: 7px; }
.op { border: 1px solid var(--border-muted); border-radius: 5px; padding: 8px 10px; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.op:hover { border-color: var(--border-default); box-shadow: 0 2px 6px rgba(0,0,0,.06); }
.op-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 5px; }
.op-stocks { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
.st-chip { font-size: 11px; color: var(--tide-deep); background: var(--tide-bg-muted); border-radius: 3px; padding: 1px 6px; white-space: nowrap; }
.st-chip i { font-family: var(--font-mono); font-style: normal; color: var(--fg-subtle); margin-left: 4px; font-size: 10px; }
.no-stock { font-size: 11px; color: var(--fg-subtle); }
.op-time { font-family: var(--font-mono); font-size: 10.5px; color: var(--fg-subtle); flex-shrink: 0; }
.op-q { font-size: 12px; color: var(--fg-default); line-height: 1.45; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; }
.op-c { font-size: 11px; color: var(--fg-muted); line-height: 1.4; margin-top: 4px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; }
.foot { margin-top: 8px; font-size: 11px; color: var(--fg-subtle); }
.empty { margin-top: 14px; text-align: center; color: var(--fg-subtle); font-size: 12px; padding: 12px; line-height: 1.6; }
</style>
