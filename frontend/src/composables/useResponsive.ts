import { ref, computed, onMounted, onUnmounted } from 'vue'

/**
 * 全项目断点契约(单一真相源)。三档固定:
 *   桌面 isDesktop ≥ 1024 / 平板 isTablet 768–1023 / 手机 isPhone < 768
 * 兼容: isMobile 保留语义 = isPhone( < 768 ), 现有所有 isMobile 调用零改动。
 * CSS 侧请统一用: 手机 @media (max-width: 768px) / 平板 768–1023 / 桌面 ≥1024,
 * 不要再随手写 720/820/900 等杂断点。
 */
export const BREAKPOINTS = { tablet: 768, desktop: 1024 } as const

export function useResponsive(breakpoint = BREAKPOINTS.tablet) {
  const width = ref(window.innerWidth)

  function update() {
    width.value = window.innerWidth
  }

  onMounted(() => window.addEventListener('resize', update))
  onUnmounted(() => window.removeEventListener('resize', update))

  // 三档恒按固定断点计算, 不受 breakpoint 参数影响
  const isPhone = computed(() => width.value < BREAKPOINTS.tablet)
  const isTablet = computed(() => width.value >= BREAKPOINTS.tablet && width.value < BREAKPOINTS.desktop)
  const isDesktop = computed(() => width.value >= BREAKPOINTS.desktop)
  // 向后兼容: 旧式 isMobile 仍按传入 breakpoint(默认 768 即 isPhone)判定
  const isMobile = computed(() => width.value < breakpoint)

  return { width, isPhone, isTablet, isDesktop, isMobile }
}
