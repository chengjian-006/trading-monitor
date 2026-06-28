import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            // 图表库单独拆(仅分时/K线/情绪图相关视图加载时才拉, ~52KB gzip)
            if (/lightweight-charts|fancy-canvas/.test(id)) return 'charts'
            // xlsx 单独拆: 仅点导出时动态加载, 不进首屏池子/复盘 chunk(~100KB gzip)
            if (/[\\/]xlsx[\\/]/.test(id)) return 'xlsx'
            // 核心框架(vue/router/pinia/axios)拆成稳定 vendor, 跨部署可缓存
            if (/[\\/]@?vue[\\/]|vue-router|[\\/]pinia[\\/]|[\\/]axios[\\/]/.test(id)) return 'vendor'
            // naive-ui 不强行合并: 让 Rollup 按视图 tree-shake, 避免手机分时页拉整包
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8888',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8888',
        ws: true,
      },
    },
  },
})
