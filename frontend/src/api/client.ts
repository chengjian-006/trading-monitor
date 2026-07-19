import axios from 'axios'

const client = axios.create({
  baseURL: '',
  timeout: 10000,
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      if ((window as any).__forceLogoutInProgress) return Promise.reject(err)
      const detail = err.response?.data?.detail || ''
      if (detail === '会话已失效，请重新登录') {
        sessionStorage.setItem('kicked', '1')
      }
      localStorage.removeItem('token')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
      // 401 已经走完跳转, 不再打 console.error 干扰排错
      return Promise.reject(err)
    }
    if (err.response?.status === 403) {
      // 权限不足统一提示(后端角色收紧后普通用户各页反馈一致, 不再各页静默/弹原始报错)
      const d = err.response?.data?.detail
      ;(window as any).$message?.error(typeof d === 'string' && d ? d : '无权限执行该操作')
      return Promise.reject(err)
    }
    console.error('API Error:', err.response?.data || err.message)
    return Promise.reject(err)
  },
)

export default client
