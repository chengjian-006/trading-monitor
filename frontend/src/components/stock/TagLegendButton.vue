<script setup lang="ts">
// 名称列标签 legend - v1.7.x
// 集中说明 StockTable 名称列出现的所有徽章含义, 解决"用户不知道这些标签是干嘛的"
import { ref } from 'vue'
import { NPopover, NIcon } from 'naive-ui'
import { InformationCircleOutline } from '@vicons/ionicons5'

const open = ref(false)

interface TagSpec {
  label: string
  desc: string
  bg: string
  color: string
}

const tags: TagSpec[] = [
  { label: '小额', desc: '今日预估全天成交额 <20亿 (按时点系数外推, 10 分钟评估一次)', bg: '#fef3c7', color: '#b45309' },
  { label: '高换', desc: '换手率 ≥15% — 短线情绪票 / 注意筹码松动', bg: '#fee2e2', color: '#b91c1c' },
  { label: '异动', desc: '量比 ≥3 — 突然放量, 主力进场或恐慌出货, 看方向', bg: '#dbeafe', color: '#1d4ed8' },
  { label: '涨停', desc: '今日封涨停板 (按板块阈值: 主板10% / 创业板·科创20% / 北交所30% / ST 5%)', bg: '#CF222E', color: '#fff' },
  { label: '跌停', desc: '今日封跌停板 (阈值同上, 负向)', bg: '#1A7F37', color: '#fff' },
  { label: '2连板', desc: '连续涨停 N 个交易日 (≥2 板, 橙红渐变, 高标龙头/情绪高度)', bg: 'linear-gradient(135deg, #ff6b00, #ff2d00)', color: '#fff' },
  { label: '首板', desc: '最近一个交易日涨停 (单板)', bg: '#fff1f0', color: '#cf1322' },
  { label: '芯片', desc: '「概念」列蓝色 chip — 个股所属概念题材 (最多显示 2 个, 鼠标悬停看全部)', bg: '#f0f5ff', color: '#2f54eb' },
  { label: '最强', desc: '板块内排名第 1 (龙头股, 行业列尾标)', bg: 'linear-gradient(135deg, #ff6b00, #ff3b00)', color: '#fff' },
]

const verdictColors: Array<{ label: string; desc: string; color: string }> = [
  { label: '建议执行', desc: '决策综合分 ≥50, 建议仓位 20-30%', color: '#16a34a' },
  { label: '轻仓试单', desc: '决策综合分 25-49, 建议仓位 5-10%', color: '#0284c7' },
  { label: '观望确认', desc: '决策综合分 0-24, 暂不入场', color: '#d97706' },
  { label: '回避',     desc: '决策综合分 <0 (大盘危险 / 历史胜率低), 不要碰', color: '#dc2626' },
]
</script>

<template>
  <NPopover trigger="click" placement="bottom-end" v-model:show="open" :width="380" :show-arrow="false">
    <template #trigger>
      <button class="legend-btn" :title="open ? '关闭说明' : '查看标签说明'" :aria-label="open ? '关闭说明' : '查看标签说明'" :aria-expanded="open">
        <NIcon :size="14"><InformationCircleOutline /></NIcon>
        <span>标签说明</span>
      </button>
    </template>

    <div class="legend-body">
      <div class="legend-title">名称列标签</div>
      <div class="legend-list">
        <div v-for="t in tags" :key="t.label" class="legend-row">
          <span class="tag" :style="{ background: t.bg, color: t.color }">{{ t.label }}</span>
          <span class="legend-desc">{{ t.desc }}</span>
        </div>
      </div>

      <div class="legend-title" style="margin-top: 10px;">决策快查色条</div>
      <div class="legend-list">
        <div v-for="v in verdictColors" :key="v.label" class="legend-row">
          <span class="verdict-bar" :style="{ borderColor: v.color }">{{ v.label }}</span>
          <span class="legend-desc">{{ v.desc }}</span>
        </div>
      </div>

      <div class="legend-hint">所有标签 hover 上可见具体数值</div>
    </div>
  </NPopover>
</template>

<style scoped>
.legend-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  font-size: 11px;
  color: var(--text2);
  background: transparent;
  border: 1px dashed var(--border, #d4d4d8);
  border-radius: 4px;
  cursor: pointer;
  user-select: none;
  touch-action: manipulation;
}
.legend-btn:hover { color: var(--primary); border-color: var(--primary); }
.legend-body { font-size: 12px; }
.legend-title {
  font-weight: 700;
  color: var(--text1);
  margin-bottom: 6px;
  font-size: 12px;
}
.legend-list { display: flex; flex-direction: column; gap: 5px; }
.legend-row { display: flex; align-items: center; gap: 8px; }
.tag {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  padding: 0 5px;
  border-radius: 2px;
  font-weight: normal;
  line-height: 16px;
  white-space: nowrap;
  flex: 0 0 auto;
  min-width: 36px;
  justify-content: center;
}
.verdict-bar {
  font-size: 11px;
  padding: 1px 6px;
  border-left: 3px solid;
  background: #f8fafc;
  font-weight: 600;
  flex: 0 0 auto;
  min-width: 60px;
}
.legend-desc { font-size: 11px; color: var(--text2); line-height: 1.4; }
.legend-hint {
  margin-top: 10px;
  padding-top: 6px;
  border-top: 1px dashed var(--border, #e5e5e5);
  font-size: 10px;
  color: var(--text3);
  font-style: italic;
}
</style>
