<script setup lang="ts">
import { NCard, NTag, NTimeline, NTimelineItem } from 'naive-ui'
import changelog from '../data/changelog'

const tagMap = {
  new: { label: '新增', type: 'success' as const },
  improve: { label: '优化', type: 'info' as const },
  fix: { label: '修复', type: 'warning' as const },
}
</script>

<template>
  <div class="changelog">
    <NTimeline>
      <NTimelineItem
        v-for="entry in changelog"
        :key="entry.version"
        :title="`${entry.version} — ${entry.title}`"
        :time="entry.date"
        :type="entry === changelog[0] ? 'success' : 'default'"
      >
        <NCard size="small" class="version-card">
          <ul class="change-list">
            <li v-for="(item, i) in entry.changes" :key="i">
              <NTag
                v-if="item.tag"
                :type="tagMap[item.tag].type"
                size="small"
                :bordered="false"
                class="change-tag"
              >
                {{ tagMap[item.tag].label }}
              </NTag>
              <span>{{ item.text }}</span>
            </li>
          </ul>
        </NCard>
      </NTimelineItem>
    </NTimeline>
  </div>
</template>

<style scoped>
.changelog {
  max-width: 720px;
}
.version-card {
  margin-top: 4px;
}
.change-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.change-list li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
  line-height: 1.6;
  border-bottom: 1px solid var(--border);
}
.change-list li:last-child {
  border-bottom: none;
}
.change-tag {
  flex-shrink: 0;
  transform: scale(0.9);
}
</style>
