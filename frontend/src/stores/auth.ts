import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref<{ id: number; username: string; role: string } | null>(null)

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function setAuth(t: string, u: { id: number; username: string; role: string }) {
    token.value = t
    user.value = u
    localStorage.setItem('token', t)
  }

  async function login(username: string, password: string) {
    token.value = ''
    const result = await authApi.login(username, password)
    setAuth(result.token, result.user)
  }

  async function fetchMe() {
    if (!token.value) return false
    try {
      const u = await authApi.getMe()
      user.value = u
      return true
    } catch {
      logout()
      return false
    }
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    // 硬跳转登录页(整页重载): 清空所有 pinia store 的内存态, 防共享机器上下个登录用户
    // 看到上一账号残留的自选池/模拟持仓/配置(含推送 token 等敏感值), 与 401 拦截器同口径。
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }

  return { token, user, isLoggedIn, isAdmin, login, fetchMe, logout }
})
