import { createDiscreteApi } from 'naive-ui'

const { message, loadingBar, dialog } = createDiscreteApi(['message', 'loadingBar', 'dialog'], {
  messageProviderProps: {
    placement: 'top',
    // v1.7.725: keepAliveOnHover 由 true 改 false。原先它与"消息被 CSS 强制居中到屏幕正中"
    // (variables.css 的 .n-message-container{top:50%}, 同版已一并去掉)叠加, 效果是消息正好
    // 出现在鼠标最常停留的位置, 悬停即不倒计时 → 提示常年挂着不走, 表现为"停留时间太长"。
    // 现改为到点就走, 与鼠标位置无关。
    keepAliveOnHover: false,
  },
})
;(window as any).$message = message
;(window as any).$loadingBar = loadingBar

export function useGlobalMessage() {
  return message
}

export function useGlobalLoadingBar() {
  return loadingBar
}

export function useGlobalDialog() {
  return dialog
}
