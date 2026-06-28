import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Signal } from '../types'
import { fetchTodaySignals } from '../api/signals'

export const useSignalStore = defineStore('signal', () => {
  const signals = ref<Signal[]>([])
  const loading = ref(false)

  async function loadTodaySignals() {
    loading.value = true
    try {
      signals.value = await fetchTodaySignals()
    } finally {
      loading.value = false
    }
  }

  function addSignal(signal: Signal) {
    const exists = signals.value.some(
      s => s.code === signal.code && s.signal_id === signal.signal_id
    )
    if (!exists) {
      signals.value.unshift(signal)
    }
  }

  return { signals, loading, loadTodaySignals, addSignal }
})
