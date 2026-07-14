import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 官网独立构建, 产物 site/dist, 与 frontend/dist 互不影响。
// dev 时 /api 代理到本地后端, 生产由 nginx 同源反代(见 nginx-default.conf)。
export default defineConfig({
  plugins: [vue()],
  build: {
    // 官网就一页, 不做分包, 单文件反而首屏最快
    assetsInlineLimit: 2048,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8888',
        changeOrigin: true,
      },
    },
  },
})
