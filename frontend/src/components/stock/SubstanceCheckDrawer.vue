<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  NDrawer, NDrawerContent, NInput, NButton, NRate, NSpace, NTag, NSpin, NEmpty, NIcon,
} from 'naive-ui'
import { SparklesOutline, SaveOutline, ReloadOutline } from '@vicons/ionicons5'
import { useGlobalMessage } from '../../composables/useGlobalMessage'
import {
  analyzeSubstance, saveSubstanceScore, getSubstance, type SubstanceResult,
} from '../../api/substance'

interface Props {
  show: boolean
  code: string
  name: string
  industry?: string
  strategy?: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:show': [val: boolean]
}>()

const message = useGlobalMessage()

const loading = ref(false)
const analyzing = ref(false)
const saving = ref(false)
const theme = ref('')
const report = ref('')
const score = ref(0)
const note = ref('')
const updatedAt = ref<string | null>(null)
const errorMsg = ref('')

const visible = computed({
  get: () => props.show,
  set: (v) => emit('update:show', v),
})

// 默认题材:优先用策略首段(题材·角色),否则用 industry
function _defaultTheme(): string {
  const s = (props.strategy || '').trim()
  if (s) {
    // "PCB板·铲子股(钻针) | xxx" → "PCB板"
    const first = s.split('|')[0].trim()
    const main = first.split('·')[0].trim()
    if (main) return main
  }
  return props.industry || ''
}

async function loadExisting() {
  if (!props.code) return
  loading.value = true
  errorMsg.value = ''
  try {
    const data: SubstanceResult = await getSubstance(props.code)
    report.value = data.substance_analysis || ''
    score.value = data.substance_score || 0
    note.value = data.substance_note || ''
    updatedAt.value = data.substance_updated_at
  } catch (e: any) {
    if (e?.response?.status === 404) {
      report.value = ''
      score.value = 0
      note.value = ''
      updatedAt.value = null
    } else {
      errorMsg.value = e?.message || '加载失败'
    }
  } finally {
    loading.value = false
  }
}

async function handleAnalyze() {
  if (!theme.value.trim()) {
    message.warning('请输入题材关键词')
    return
  }
  analyzing.value = true
  errorMsg.value = ''
  try {
    const result = await analyzeSubstance(props.code, theme.value.trim())
    if (result.ok && result.report) {
      report.value = result.report
      message.success('AI 核查完成,报告已保存')
      // 重新拉一次,同步 updatedAt
      await loadExisting()
    } else {
      errorMsg.value = result.error || 'AI 返回失败'
      message.error(errorMsg.value)
    }
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.detail || e?.message || '调用失败'
    message.error(errorMsg.value)
  } finally {
    analyzing.value = false
  }
}

async function handleSaveScore() {
  saving.value = true
  try {
    await saveSubstanceScore(props.code, score.value, note.value)
    message.success(`评分已保存:${score.value} 星`)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

// 简单 markdown 渲染:## 标题、**加粗**、- 列表
function renderMarkdown(text: string): string {
  if (!text) return ''
  let html = text
  html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  // 加粗
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  // ### 三级标题
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>')
  // ## 二级标题
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>')
  // 分隔线
  html = html.replace(/^---$/gm, '<hr/>')
  // 列表
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>')
  html = html.replace(/(<li>[\s\S]*?<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`)
  // 换行
  html = html.replace(/\n{2,}/g, '<br/><br/>')
  html = html.replace(/\n/g, '<br/>')
  // 还原列表里的 br
  html = html.replace(/<\/li><br\/>/g, '</li>')
  html = html.replace(/<\/ul><br\/>/g, '</ul>')
  return html
}

const renderedReport = computed(() => renderMarkdown(report.value))

const scoreLabel = computed(() => {
  const labels: Record<number, string> = {
    0: '未评分', 1: '蹭概念', 2: '边缘', 3: '一般', 4: '良好', 5: '核心受益',
  }
  return labels[score.value] || ''
})

watch(visible, (v) => {
  if (v && props.code) {
    theme.value = _defaultTheme()
    loadExisting()
  }
})
</script>

<template>
  <NDrawer v-model:show="visible" :width="640" placement="right">
    <NDrawerContent :title="`AI 真受益核查 — ${name} (${code})`" closable>
      <NSpin :show="loading">
        <!-- 题材输入 + 核查按钮 -->
        <div class="theme-bar">
          <div class="theme-label">题材关键词</div>
          <NInput
            v-model:value="theme"
            placeholder="例如:PCB / 算力租赁 / 人形机器人"
            size="small"
            style="flex: 1"
          />
          <NButton type="primary" size="small" :loading="analyzing" @click="handleAnalyze">
            <template #icon><NIcon><component :is="report ? ReloadOutline : SparklesOutline" /></NIcon></template>
            {{ report ? '重新核查' : 'AI 核查' }}
          </NButton>
        </div>

        <div v-if="updatedAt" class="meta-line">
          上次核查: {{ updatedAt.replace('T', ' ').slice(0, 16) }}
        </div>

        <div v-if="errorMsg" class="error-box">{{ errorMsg }}</div>

        <!-- 报告内容 -->
        <div v-if="report" class="report-box" v-html="renderedReport"></div>
        <NEmpty v-else-if="!analyzing" description="点击右上「AI 核查」生成报告" style="margin-top: 60px" />

        <!-- 人工评分区 -->
        <div v-if="report" class="score-section">
          <div class="section-title">👤 人工最终判定</div>
          <div class="score-row">
            <NRate v-model:value="score" :count="5" size="medium" />
            <NTag v-if="score > 0" :type="score >= 4 ? 'success' : score >= 3 ? 'warning' : 'error'" size="small">
              {{ scoreLabel }}
            </NTag>
          </div>
          <NInput
            v-model:value="note"
            type="textarea"
            placeholder="备注:你的判定理由(可选)"
            :autosize="{ minRows: 2, maxRows: 4 }"
            style="margin-top: 8px"
          />
          <NButton type="primary" size="small" :loading="saving" @click="handleSaveScore" style="margin-top: 10px">
            <template #icon><NIcon><SaveOutline /></NIcon></template>
            保存评分
          </NButton>
        </div>
      </NSpin>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.theme-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.theme-label {
  font-size: 12px;
  color: var(--text2);
  white-space: nowrap;
}
.meta-line {
  font-size: 11px;
  color: var(--text3);
  margin-bottom: 10px;
}
.error-box {
  padding: 8px 12px;
  background: rgba(207, 34, 46, 0.08);
  border-left: 3px solid var(--red, #CF222E);
  border-radius: 4px;
  color: var(--red, #CF222E);
  font-size: 12px;
  margin-bottom: 10px;
}
.report-box {
  padding: 12px 14px;
  background: rgba(0, 0, 0, 0.02);
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text1);
  border: 1px solid rgba(0, 0, 0, 0.06);
}
.report-box :deep(h3) {
  font-size: 15px;
  font-weight: 700;
  margin: 14px 0 8px;
  color: var(--primary);
}
.report-box :deep(h4) {
  font-size: 13px;
  font-weight: 700;
  margin: 10px 0 6px;
  color: var(--text1);
}
.report-box :deep(strong) {
  color: var(--text1);
  font-weight: 600;
}
.report-box :deep(ul) {
  padding-left: 18px;
  margin: 4px 0;
}
.report-box :deep(li) {
  margin: 2px 0;
}
.report-box :deep(hr) {
  border: none;
  border-top: 1px dashed rgba(0, 0, 0, 0.1);
  margin: 8px 0;
}
.score-section {
  margin-top: 18px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: rgba(46, 128, 255, 0.03);
}
.section-title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text1);
}
.score-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
