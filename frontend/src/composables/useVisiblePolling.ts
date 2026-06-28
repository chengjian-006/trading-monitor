// 页面可见性感知的轮询: 标签页切走时跳过请求(定时器照转, 不发网络),
// 切回前台立即补刷一次再按节奏继续。组件卸载自动清理。
// 用法: useVisiblePolling(fetchFn, 30_000) — 替代裸 setInterval(fetchFn, 30_000)。
import { onMounted, onUnmounted } from 'vue'

export function useVisiblePolling(fn: () => void, intervalMs: number, opts?: { immediate?: boolean }) {
  let timer: ReturnType<typeof setInterval> | null = null

  function onVisibilityChange() {
    if (!document.hidden) fn()
  }

  onMounted(() => {
    if (opts?.immediate !== false) fn()
    timer = setInterval(() => { if (!document.hidden) fn() }, intervalMs)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onUnmounted(() => {
    if (timer) { clearInterval(timer); timer = null }
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })
}
