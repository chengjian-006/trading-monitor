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

/* ────────────────────────────────────────────────────────────────────────────
 * 写请求「在途去重」(v1.7.726)
 *
 * 目的: 手快连点两下按钮 → 第二次直接复用还在飞的那个 Promise, 不发第二个请求,
 *       防重复导入/重复建单/重复删。命中时【静默】复用(用户拍板: 不弹提示, 因为
 *       用户本就只想执行一次, 提示反而像报错; 按钮侧另有 loading 态给反馈)。
 *
 * 为什么只包写操作(post/put/delete), 不包 get:
 *   - 重复 GET 只是浪费流量; 重复写才会造成真实数据损害。
 *   - GET 去重反而有副作用风险 —— 3s 行情轮询等定时刷新可能被误判成"重复"而合并掉。
 *
 * 为什么包这几个方法就够: 全站 133 处 HTTP 调用全部走 client.get/post/put/delete
 *   (77 get / 39 post / 9 delete / 8 put), 无一处绕过 client 直接 import axios,
 *   故在导出的实例上替换这几个方法 = 100% 覆盖, 且不依赖 axios 内部适配器实现。
 *
 * 边界: 只拦【并发】重复(前一个还没回来); 请求结束即从在途表移除, 之后再点是正常新请求。
 * ──────────────────────────────────────────────────────────────────────────── */
const inflight = new Map<string, Promise<unknown>>()

function keyPart(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof FormData !== 'undefined' && v instanceof FormData) {
    // FormData 含 File 对象无法稳定 JSON 化, 用 字段名=文件名:大小 兜底成可比字符串
    const parts: string[] = []
    v.forEach((val, k) => {
      parts.push(`${k}=${val instanceof File ? `${val.name}:${val.size}` : String(val)}`)
    })
    return parts.join('&')
  }
  try {
    return JSON.stringify(v)
  } catch {
    return ''   // 循环引用等序列化不了的 → 视作无 body, 仅按 方法+URL 去重
  }
}

const rawPost = client.post.bind(client)
const rawPut = client.put.bind(client)
const rawDelete = client.delete.bind(client)

function track<T>(key: string, run: () => Promise<T>): Promise<T> {
  const running = inflight.get(key) as Promise<T> | undefined
  if (running) return running                       // 静默复用在途请求
  const p = run().finally(() => inflight.delete(key))
  inflight.set(key, p)
  return p
}

client.post = ((url: string, data?: unknown, config?: unknown) =>
  track(`post ${url} ${keyPart(data)}`, () => rawPost(url, data, config as never))) as typeof client.post

client.put = ((url: string, data?: unknown, config?: unknown) =>
  track(`put ${url} ${keyPart(data)}`, () => rawPut(url, data, config as never))) as typeof client.put

// delete 无 body, 签名是 (url, config) —— 按 URL + query 参数去重
client.delete = ((url: string, config?: { params?: unknown }) =>
  track(`delete ${url} ${keyPart(config?.params)}`, () => rawDelete(url, config as never))) as typeof client.delete

export default client
