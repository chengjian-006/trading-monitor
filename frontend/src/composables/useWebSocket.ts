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
    const token = authStore.token
    if (!token) return

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${proto}://${location.host}/ws`)
    ws = socket

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'auth', token }))
    }

    socket.onclose = () => {
      if (ws !== socket) return
      connected.value = false
      ws = null
      scheduleReconnect()
    }

    socket.onerror = () => {
      socket.close()
    }

    socket.onmessage = (e) => {
      if (ws !== socket) return
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'auth_ok') {
          connected.value = true
          retryDelay = 3000
          return
        }
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
    connected.value = false
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
