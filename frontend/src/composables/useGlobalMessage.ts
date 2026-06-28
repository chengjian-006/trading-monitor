import { createDiscreteApi } from 'naive-ui'

const { message, loadingBar, dialog } = createDiscreteApi(['message', 'loadingBar', 'dialog'], {
  messageProviderProps: {
    placement: 'top',
    keepAliveOnHover: true,
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
