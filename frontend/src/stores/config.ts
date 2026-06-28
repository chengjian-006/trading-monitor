import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { AppConfig } from '../types'
import * as configApi from '../api/config'

export const useConfigStore = defineStore('config', () => {
  const config = ref<AppConfig>({
    pushplus_token: '',
    pushplus_enabled: true,
    lark_webhook: '',
    lark_enabled: false,
    scan_interval_seconds: 30,
    trading_hours: [
      { start: '09:30', end: '11:30' },
      { start: '13:00', end: '15:00' },
    ],
    anthropic_api_key: '',
    ai_base_url: 'https://api.deepseek.com/v1',
    ai_model: 'deepseek-chat',
    ai_report_enabled: true,
    sso_enabled: true,
  })
  const loading = ref(false)

  async function loadConfig() {
    loading.value = true
    try {
      const data = await configApi.fetchConfig()
      config.value.pushplus_token = data.pushplus_token ?? ''
      config.value.pushplus_enabled = data.pushplus_enabled ?? true
      config.value.lark_webhook = data.lark_webhook ?? ''
      config.value.lark_enabled = data.lark_enabled ?? false
      config.value.scan_interval_seconds = data.scan_interval_seconds ?? 30
      config.value.trading_hours = data.trading_hours ?? config.value.trading_hours
      config.value.anthropic_api_key = data.anthropic_api_key ?? ''
      config.value.ai_base_url = data.ai_base_url ?? 'https://api.deepseek.com/v1'
      config.value.ai_model = data.ai_model ?? 'deepseek-chat'
      config.value.ai_report_enabled = data.ai_report_enabled ?? true
      config.value.sso_enabled = data.sso_enabled ?? true
    } catch (e) {
      console.error('加载配置失败:', e)
    } finally {
      loading.value = false
    }
  }

  async function saveConfig() {
    await configApi.saveConfig(config.value)
  }

  return { config, loading, loadConfig, saveConfig }
})
