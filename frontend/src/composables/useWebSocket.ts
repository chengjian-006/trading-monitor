import { ref, onUnmounted, watch } from 'vue'
import { useAuthStore } from '../stores/auth'

export function useWebSocket(onMessage: (data: any) => void) {
  const connected = ref(false)
  const authStore = useAuthStore()
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let retryDelay = 3000
  const MAX_DELAY = 30000

  function connect() {
    if (!authStore.token) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${authStore.token}`)

    ws.onopen = () => {
      connected.value = true
      retryDelay = 3000
    }

    ws.onclose = () => {
      connected.value = false
      scheduleReconnect()
    }

    ws.onerror = () => {
      ws?.close()
    }

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'force_logout') {
          close()
          onMessage(data)
          return
        }
        onMessage(data)
      } catch { /* ignore parse errors */ }
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
      retryDelay = Math.min(retryDelay * 2, MAX_DELAY)
    }, retryDelay)
  }

  function close() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    ws?.close()
    ws = null
  }

  watch(() => authStore.token, (newToken) => {
    close()
    if (newToken) connect()
  })

  if (authStore.token) connect()

  onUnmounted(() => {
    close()
  })

  return { connected, close }
}
