<script setup lang="ts">
// 基线0 业务件 (v1.7.646, 亮点增强 v1.7.647): 空状态五件套 —— 图标 + 为什么空 + 一句描述 + 主操作 + 帮助链接
// 文案口径三种: 首次使用 / 暂时没数据 / 出错了(错误态要说人话并给自救动作)
// brand=true 时用潮汐波纹插画替代 emoji 图标(观潮品牌时刻, 如「潮水未起」)
import { NButton } from 'naive-ui'
import TideWave from './TideWave.vue'

withDefaults(defineProps<{
  icon?: string        // emoji 图标
  brand?: boolean      // 用潮汐波纹插画替代 emoji
  title: string        // 为什么空(如「今天还没有触发信号」)
  description?: string // 一句描述(如「盘中每 3 分钟自动扫描, 触发即推送」)
  actionText?: string  // 主操作按钮文字
  helpText?: string    // 帮助链接文字
}>(), { brand: false })

const emit = defineEmits<{ (e: 'action'): void; (e: 'help'): void }>()
</script>

<template>
  <div class="empty-state">
    <div v-if="brand" class="es-tide" aria-hidden="true">
      <TideWave :width="72" :height="34" :layers="2" :stroke-width="1.6" />
    </div>
    <div v-else class="es-icon" aria-hidden="true">{{ icon || '🗒️' }}</div>
    <div class="es-title">{{ title }}</div>
    <div v-if="description" class="es-desc">{{ description }}</div>
    <NButton v-if="actionText" size="small" class="es-action" @click="emit('action')">{{ actionText }}</NButton>
    <a v-if="helpText" class="es-help" role="button" tabindex="0" @click="emit('help')" @keydown.enter="emit('help')">{{ helpText }}</a>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 24px 16px;
  gap: 4px;
}
.es-icon {
  font-size: 24px;
  opacity: 0.4;
}
.es-tide {
  opacity: 0.75;
  margin-bottom: 2px;
}
.es-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg-default);
  margin-top: 4px;
}
.es-desc {
  font-size: 12px;
  color: var(--fg-subtle);
}
.es-action {
  margin-top: 8px;
}
.es-help {
  margin-top: 4px;
  font-size: 12px;
  color: var(--accent-fg);
  cursor: pointer;
}
.es-help:hover {
  text-decoration: underline;
}
</style>
