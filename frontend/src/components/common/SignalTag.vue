<script setup lang="ts">
// 基线0 业务件 (v1.7.646): 信号/状态标签
// 规范: muted 浅底 + 语义色深字 + 25% 描边(不用实底色块), 尺寸两档; 语义映射 up=买点/涨, down=卖点/跌
withDefaults(defineProps<{
  label?: string
  tone?: 'up' | 'down' | 'warn' | 'accent' | 'neutral'
  size?: 'small' | 'medium'
}>(), { tone: 'neutral', size: 'medium' })
</script>

<template>
  <span class="signal-tag" :class="[tone, size]"><slot>{{ label }}</slot></span>
</template>

<style scoped>
.signal-tag {
  display: inline-flex;
  align-items: center;
  font-weight: 600;
  border-radius: 999px;
  white-space: nowrap;
  line-height: 1.4;
}
.medium { font-size: 12px; padding: 1px 9px; }
.small { font-size: 11px; padding: 0 7px; }

.up { color: var(--up-fg); background: var(--up-bg-muted); border: 1px solid rgba(217, 43, 38, 0.25); }
.down { color: var(--down-fg); background: var(--down-bg-muted); border: 1px solid rgba(15, 138, 95, 0.25); }
.warn { color: var(--warn-fg); background: var(--warn-bg-muted); border: 1px solid rgba(212, 167, 44, 0.4); }
.accent { color: var(--accent-fg); background: var(--accent-bg-muted); border: 1px solid rgba(9, 105, 218, 0.25); }
.neutral { color: var(--fg-muted); background: var(--bg-default); border: 1px solid var(--border-default); }
</style>
