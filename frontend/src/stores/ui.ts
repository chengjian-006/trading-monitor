import { defineStore } from 'pinia'
import { ref } from 'vue'

// 通用个股详情弹窗的全局入口: 任意组件 useUiStore().openStock(code, name?) 即弹。
// 单实例弹窗挂在 App.vue, 避免每个列表各挂一个。
export const useUiStore = defineStore('ui', () => {
  const stockShow = ref(false)
  const stockCode = ref('')
  const stockName = ref('')

  function openStock(code: string, name = '') {
    if (!code) return
    stockCode.value = code
    stockName.value = name
    stockShow.value = true
  }
  function closeStock() {
    stockShow.value = false
  }

  return { stockShow, stockCode, stockName, openStock, closeStock }
})
