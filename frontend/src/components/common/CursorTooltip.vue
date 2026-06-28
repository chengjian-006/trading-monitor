<script setup lang="ts">
import { NPopover } from 'naive-ui'
import { ref } from 'vue'

defineProps<{
  width?: number
  dark?: boolean
}>()

const x = ref(0)
const y = ref(0)
const show = ref(false)

function onEnter(e: MouseEvent) {
  x.value = e.clientX
  y.value = e.clientY
  show.value = true
}

function onLeave() {
  show.value = false
}
</script>

<template>
  <span class="cursor-tip-trigger" @mouseenter="onEnter" @mouseleave="onLeave">
    <slot name="trigger" />
  </span>
  <NPopover
    trigger="manual"
    :show="show"
    :x="x"
    :y="y"
    :width="width"
    :show-arrow="false"
    placement="bottom-start"
    :class="{ 'cursor-tip-dark': dark }"
  >
    <slot />
  </NPopover>
</template>

<style scoped>
.cursor-tip-trigger {
  display: inline-flex;
  align-items: center;
}
</style>
