import { onMounted, onUnmounted } from 'vue'

/**
 * 滚动入场: 给带 .reveal 的元素在进入视口时加 .in。
 * 用一个全局 IntersectionObserver, 不给每个组件各建一个。
 * 元素可用 style="--d: 90ms" 设置自己的延迟, 做成阶梯出场。
 */
export function useReveal(root = () => document as Document | HTMLElement | null) {
  let io: IntersectionObserver | null = null

  onMounted(() => {
    const host = root()
    if (!host) return

    const nodes = host.querySelectorAll<HTMLElement>('.reveal')

    // 不支持 IO 或用户要求减少动效时, 直接全部显形, 别把内容藏没了
    if (!('IntersectionObserver' in window)) {
      nodes.forEach((n) => n.classList.add('in'))
      return
    }

    io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue
          const el = e.target as HTMLElement
          const delay = el.style.getPropertyValue('--d') || '0ms'
          el.style.transitionDelay = delay
          el.classList.add('in')
          io?.unobserve(el) // 只入场一次, 不做往复动画
        }
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.08 },
    )

    nodes.forEach((n) => io!.observe(n))
  })

  onUnmounted(() => io?.disconnect())
}
