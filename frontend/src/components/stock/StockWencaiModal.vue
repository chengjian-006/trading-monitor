<script setup lang="ts">
// 个股「问财提问」弹窗 (v1.7.777): 选预设模版 / 自定义, 新标签打开同花顺问财并把问题填好。
// 为什么走 deep-link 而非后端调问财: 生产服务器 IP 被同花顺风控封, 后端连结构化选股都调不通,
// 更别说对话式 chat; 而在【用户自己浏览器】打开 iwencai.com 是用户本人已登录的会话, 不碰风控。
import { ref, computed, watch } from 'vue'
import { NModal, NInput, NButton, NIcon } from 'naive-ui'
import { OpenOutline, HelpCircleOutline } from '@vicons/ionicons5'
import type { Stock } from '../../types'

const props = defineProps<{ show: boolean; row: Stock | null }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const name = computed(() => props.row?.name || '')
const code = computed(() => props.row?.code || '')

// 预设模版: build(name) 生成带股名的自然语言问题(问财按股名解析并自取实时数据, 故问题保持简洁最好)
interface Preset { key: string; label: string; build: (n: string) => string }
const PRESETS: Preset[] = [
  { key: 'buy', label: '现在能不能买', build: (n) => `${n} 现在能不能买,当前股价位置、支撑位和压力位、买卖点` },
  { key: 'news', label: '消息面(利好利空/公告)', build: (n) => `${n} 最近有哪些利好利空消息和最新公告` },
  { key: 'fundamental', label: '基本面(业绩/估值/行业)', build: (n) => `${n} 最新业绩、估值(市盈率、市净率)和所属行业地位` },
  { key: 'theme', label: '题材强弱 + 技术面', build: (n) => `${n} 属于哪些概念题材、题材热度如何,以及当前技术形态和短线操作建议` },
]

const customText = ref('')
watch(() => props.show, (v) => { if (v) customText.value = '' })

// 打开同花顺问财(新标签): 用户已登录问财, 直接看答复。querytype=stock 让问财按个股问答解析。
function ask(question: string) {
  const q = question.trim()
  if (!q) return
  const url = `https://www.iwencai.com/unifiedwap/result?w=${encodeURIComponent(q)}&querytype=stock`
  window.open(url, '_blank', 'noopener,noreferrer')
}

function askCustom() {
  const t = customText.value.trim()
  if (!t) return
  // 自动带上股名(除非用户自己已写了股名/代码)
  const q = (t.includes(name.value) || t.includes(code.value)) ? t : `${name.value} ${t}`
  ask(q)
}
</script>

<template>
  <NModal :show="show" @update:show="emit('update:show', $event)" preset="card"
          :title="`问财提问 · ${name}${code ? '(' + code + ')' : ''}`"
          style="max-width: 520px" :bordered="false">
    <p class="wm-hint">
      <NIcon :component="HelpCircleOutline" :size="14" style="vertical-align: -2px" />
      选一个模版即在<b>新标签打开同花顺问财</b>并填好问题(已自动带上「{{ name }}」);或自己写。需你已登录问财。
    </p>

    <div class="wm-presets">
      <button v-for="t in PRESETS" :key="t.key" type="button" class="wm-preset" @click="ask(t.build(name))">
        <span class="wm-label">{{ t.label }}</span>
        <span class="wm-full">{{ t.build(name) }}</span>
        <NIcon :component="OpenOutline" :size="14" class="wm-open" />
      </button>
    </div>

    <div class="wm-custom">
      <NInput v-model:value="customText" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }"
              placeholder="或输入自定义问题(会自动带上股名)…" @keydown.enter.exact.prevent="askCustom" />
      <NButton type="primary" :disabled="!customText.trim()" @click="askCustom">
        <template #icon><NIcon :component="OpenOutline" /></template>去问财提问
      </NButton>
    </div>
  </NModal>
</template>

<style scoped>
.wm-hint { margin: 0 0 12px; font-size: 12px; color: var(--fg-subtle); line-height: 1.5; }
.wm-hint b { color: var(--fg-muted); }
.wm-presets { display: flex; flex-direction: column; gap: 8px; }
.wm-preset {
  position: relative; text-align: left; cursor: pointer; appearance: none; font: inherit;
  border: 1px solid var(--border-muted); border-radius: 8px; background: var(--bg-sunken);
  padding: 8px 34px 8px 12px; transition: border-color 0.12s, background 0.12s;
}
.wm-preset:hover { border-color: color-mix(in srgb, var(--accent-fg) 40%, transparent); background: var(--accent-bg-muted); }
.wm-label { display: block; font-size: 13px; font-weight: 600; color: var(--fg-default); }
.wm-full { display: block; margin-top: 2px; font-size: 11.5px; color: var(--fg-subtle); line-height: 1.4; }
.wm-open { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: var(--fg-subtle); }
.wm-preset:hover .wm-open { color: var(--accent-fg); }
.wm-custom { display: flex; align-items: flex-end; gap: 8px; margin-top: 14px; }
.wm-custom :deep(.n-input) { flex: 1; }
</style>
