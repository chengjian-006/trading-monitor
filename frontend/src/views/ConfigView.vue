<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  NInput, NInputNumber, NButton, NSpace, NSkeleton, NCard, NIcon, NSwitch, NPopconfirm,
} from 'naive-ui'
import CursorTooltip from '../components/common/CursorTooltip.vue'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { SendOutline, SaveOutline, HelpCircleOutline } from '@vicons/ionicons5'
import { useConfigStore } from '../stores/config'
import { testPushplus, testLark, saveConfig, saveThsPath, fetchThsGroups, fetchPushPrefs, revokePushPref, testSignalCard, type PushPref } from '../api/config'
import { useResponsive } from '../composables/useResponsive'

const configStore = useConfigStore()
const message = useGlobalMessage()
const { isPhone } = useResponsive()

// 推送偏好(快捷设置: 今日免打扰/个股静音/关模型/标记已处理)
const pushPrefs = ref<PushPref[]>([])
const prefsLoading = ref(false)
const prefRevoking = ref<number | null>(null)

async function loadPushPrefs() {
  prefsLoading.value = true
  try {
    pushPrefs.value = (await fetchPushPrefs()).prefs
  } catch { /* ignore */ } finally {
    prefsLoading.value = false
  }
}

function prefDesc(p: PushPref): string {
  if (p.kind === 'mute') return '今天剩余飞书推送已静音'
  if (p.kind === 'snooze') return `个股 ${p.target} 静音至 ${p.until_date.slice(5)}`
  if (p.kind === 'model_off') return `模型 ${p.target} 今日关推送`
  if (p.kind === 'ack') return `信号已标记处理（${p.target}）`
  return p.kind_label
}

async function handleRevokePref(id: number) {
  prefRevoking.value = id
  try {
    await revokePushPref(id)
    message.success('已撤销')
    await loadPushPrefs()
  } catch {
    message.error('撤销失败')
  } finally {
    prefRevoking.value = null
  }
}

const saveScanLoading = ref(false)
const saveAiLoading = ref(false)
const thsPath = ref('')
const thsPathSaving = ref(false)
const thsFileHint = ref('')

// PushPlus（个人微信）
const savePushplusLoading = ref(false)
const pushplusTestLoading = ref(false)
const pushplusTestResult = ref('')
const pushplusTestOk = ref(false)

const testCardLoading = ref(false)

async function handleTestSignalCard() {
  testCardLoading.value = true
  try {
    const result = await testSignalCard()
    if (result.ok) message.success('测试信号卡已发送')
    else message.warning(result.msg)
  } catch {
    message.error('发送失败')
  } finally {
    testCardLoading.value = false
  }
}

// 全局飞书设置
const saveLarkLoading = ref(false)
const larkTestLoading = ref(false)
const larkTestResult = ref('')
const larkTestOk = ref(false)

onMounted(async () => {
  configStore.loadConfig()
  try {
    const result = await fetchThsGroups()
    thsPath.value = result.ths_path || ''
    if (result.ok && result.path) thsFileHint.value = result.path
  } catch { /* ignore */ }
  loadPushPrefs()
})

async function handleSavePushplus() {
  savePushplusLoading.value = true
  try {
    await saveConfig({ pushplus_token: configStore.config.pushplus_token })
    message.success('PushPlus token 已保存')
  } catch {
    message.error('保存失败')
  } finally {
    savePushplusLoading.value = false
  }
}

async function handleTogglePushplus(val: boolean) {
  const prev = configStore.config.pushplus_enabled
  configStore.config.pushplus_enabled = val
  try {
    await saveConfig({ pushplus_enabled: val })
    message.success(val ? 'PushPlus 推送已开启' : 'PushPlus 推送已关闭')
  } catch {
    message.error('保存失败')
    configStore.config.pushplus_enabled = prev
  }
}

async function handleTestPushplus() {
  pushplusTestLoading.value = true
  pushplusTestResult.value = ''
  try {
    const result = await testPushplus()
    pushplusTestResult.value = result.msg
    pushplusTestOk.value = result.ok
    if (result.ok) message.success(result.msg)
    else message.error(result.msg)
  } catch {
    pushplusTestResult.value = '请求失败'
    pushplusTestOk.value = false
  } finally {
    pushplusTestLoading.value = false
  }
}

async function handleSaveScan() {
  saveScanLoading.value = true
  try {
    await saveConfig({
      scan_interval_seconds: configStore.config.scan_interval_seconds,
      trading_hours: configStore.config.trading_hours,
    })
    message.success('扫描设置已保存')
  } catch {
    message.error('保存失败')
  } finally {
    saveScanLoading.value = false
  }
}

async function handleSaveAi() {
  saveAiLoading.value = true
  try {
    await saveConfig({
      anthropic_api_key: configStore.config.anthropic_api_key,
      ai_base_url: configStore.config.ai_base_url,
      ai_model: configStore.config.ai_model,
      ai_report_enabled: configStore.config.ai_report_enabled,
    })
    message.success('AI 日报设置已保存')
  } catch {
    message.error('保存失败')
  } finally {
    saveAiLoading.value = false
  }
}

async function handleToggleAiReport(val: boolean) {
  configStore.config.ai_report_enabled = val
  try {
    await saveConfig({ ai_report_enabled: val })
    message.success(val ? 'AI 日报已开启' : 'AI 日报已关闭')
  } catch {
    message.error('保存失败')
    configStore.config.ai_report_enabled = !val
  }
}

async function handleSaveThsPath() {
  thsPathSaving.value = true
  try {
    await saveThsPath(thsPath.value)
    const result = await fetchThsGroups()
    if (result.ok && result.path) {
      thsFileHint.value = result.path
      message.success('路径已保存，已定位到自选股文件')
    } else {
      thsFileHint.value = ''
      message.warning('路径已保存，但未找到自选股文件，请检查路径')
    }
  } catch {
    message.error('保存失败')
  } finally {
    thsPathSaving.value = false
  }
}

async function handleToggleSso(val: boolean) {
  configStore.config.sso_enabled = val
  try {
    await saveConfig({ sso_enabled: val })
    message.success(val ? '单点登录已开启，同一账号仅允许一处登录' : '单点登录已关闭，同一账号可多处登录')
  } catch {
    message.error('保存失败')
    configStore.config.sso_enabled = !val
  }
}

// ── 全局飞书 ──
async function handleSaveLark() {
  saveLarkLoading.value = true
  try {
    await saveConfig({
      lark_webhook: configStore.config.lark_webhook,
      lark_enabled: configStore.config.lark_enabled,
    })
    message.success('飞书推送设置已保存')
  } catch {
    message.error('保存失败')
  } finally {
    saveLarkLoading.value = false
  }
}

// 飞书推送总开关(单一全局配置, 个人/全局已合并为一个)
async function handleToggleLark(val: boolean) {
  const prev = configStore.config.lark_enabled
  configStore.config.lark_enabled = val
  try {
    await saveConfig({ lark_enabled: val })
    message.success(val ? '飞书推送已开启' : '飞书推送已关闭')
  } catch {
    message.error('保存失败')
    configStore.config.lark_enabled = prev
  }
}

async function handleTestLark() {
  const webhook = configStore.config.lark_webhook
  if (!webhook) {
    message.warning('请先粘贴飞书Webhook地址')
    return
  }
  larkTestLoading.value = true
  larkTestResult.value = ''
  try {
    const result = await testLark(webhook)
    larkTestResult.value = result.msg
    larkTestOk.value = result.ok
    if (result.ok) message.success(result.msg)
    else message.error(result.msg)
  } catch {
    larkTestResult.value = '请求失败'
    larkTestOk.value = false
  } finally {
    larkTestLoading.value = false
  }
}

</script>

<template>
  <div>
    <div class="page-header"><span class="page-title">系统设置</span></div>

    <NSkeleton v-if="configStore.loading" :repeat="4" text />

    <Transition v-else name="content-fade" appear>
    <div>
      <NCard title="飞书推送" size="small" style="margin-bottom: 16px">
        <div class="form-row" style="margin-bottom: 12px">
          <label class="field-label">推送总开关</label>
          <div class="field-inline">
            <NSwitch
              :value="configStore.config.lark_enabled"
              @update:value="handleToggleLark"
            />
            <span class="config-hint">{{ configStore.config.lark_enabled ? '已开启，信号将推送到飞书' : '已关闭，不推飞书' }}</span>
          </div>
        </div>
        <div class="form-row">
          <label class="field-label" for="cfg-lark-webhook">
            飞书 Webhook 地址
            <CursorTooltip>
              <template #trigger>
                <NIcon :component="HelpCircleOutline" :size="15" class="help-icon" />
              </template>
              飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 Webhook 地址。所有飞书推送(个股信号 / 盘面播报 / 日报 / 汇总)统一走这一个地址
            </CursorTooltip>
          </label>
          <div class="field-inline">
            <NInput
              v-model:value="configStore.config.lark_webhook"
              placeholder="粘贴飞书Webhook地址 (open.feishu.cn/...)"
              size="small"
              style="flex: 1; min-width: min(500px, 100%)"
              :input-props="{ id: 'cfg-lark-webhook', name: 'lark_webhook', type: 'url', autocomplete: 'off' }"
            />
            <NButton :loading="larkTestLoading" @click="handleTestLark" type="primary" secondary size="small">
              <template #icon><NIcon><SendOutline /></NIcon></template>
              测试推送
            </NButton>
            <NButton type="primary" size="small" :loading="saveLarkLoading" @click="handleSaveLark">
              <template #icon><NIcon><SaveOutline /></NIcon></template>
              保存
            </NButton>
          </div>
          <div v-if="larkTestResult" :style="{ color: larkTestOk ? 'var(--green)' : 'var(--red)', fontSize: '12px', marginTop: '2px' }">
            {{ larkTestResult }}
          </div>
        </div>
        <div class="form-row" style="margin-top: 12px">
          <label class="field-label">预览测试</label>
          <div class="field-inline">
            <NButton :loading="testCardLoading" @click="handleTestSignalCard" type="info" secondary size="small">
              <template #icon><NIcon><SendOutline /></NIcon></template>
              发送测试信号卡
            </NButton>
            <span class="config-hint">发一条样例买点信号卡到你的飞书，可看到完整卡片(战绩表 + 查看分时图 + 快捷动作行)</span>
          </div>
        </div>
      </NCard>

      <NCard title="推送偏好（快捷设置）" size="small" style="margin-bottom: 16px">
        <div class="pref-note">
          飞书推送卡片底部的快捷按钮（今日免打扰 / 个股静音 / 关模型 / 标记已处理）点击后生效的设置都列在这里，可一键撤销。过期项次日自动消失。
        </div>
        <NSkeleton v-if="prefsLoading" :repeat="2" text />
        <div v-else-if="pushPrefs.length === 0" class="config-hint" style="padding: 6px 0">
          当前没有生效中的快捷设置。
        </div>
        <!-- 移动端卡片化 / PC 行式列表, 遵循全站宽表自适应规范 -->
        <div v-else class="pref-list">
          <div v-for="p in pushPrefs" :key="p.id" class="pref-item" :class="{ 'pref-item--phone': isPhone }">
            <div class="pref-main">
              <span class="pref-tag">{{ p.kind_label }}</span>
              <span class="pref-desc">{{ prefDesc(p) }}</span>
            </div>
            <div class="pref-meta">
              <span class="config-hint">至 {{ p.until_date.slice(5) }}</span>
              <NPopconfirm @positive-click="handleRevokePref(p.id)">
                <template #trigger>
                  <NButton size="tiny" type="error" tertiary :loading="prefRevoking === p.id">
                    撤销
                  </NButton>
                </template>
                确认撤销这条快捷设置？
              </NPopconfirm>
            </div>
          </div>
        </div>
      </NCard>

      <NCard title="PushPlus（个人微信）" size="small" style="margin-bottom: 16px">
        <div class="form-row" style="margin-bottom: 12px">
          <label class="field-label">推送开关</label>
          <div class="field-inline">
            <NSwitch
              :value="configStore.config.pushplus_enabled"
              @update:value="handleTogglePushplus"
            />
            <span class="config-hint">{{ configStore.config.pushplus_enabled ? '已开启，信号将推送到 PushPlus(个人微信)' : '已关闭，保留 token 但不推' }}</span>
          </div>
        </div>
        <div class="form-row">
          <label class="field-label" for="cfg-pushplus-token">
            PushPlus Token
            <CursorTooltip>
              <template #trigger>
                <NIcon :component="HelpCircleOutline" :size="15" class="help-icon" />
              </template>
              访问 pushplus.plus → 微信扫码登录 → 复制你的 token。PushPlus 是个人微信推送，替代原企业微信。
            </CursorTooltip>
          </label>
          <div class="field-inline">
            <NInput
              v-model:value="configStore.config.pushplus_token"
              placeholder="去 pushplus.plus 微信扫码复制你的 token"
              size="small"
              style="flex: 1; min-width: min(500px, 100%)"
              :input-props="{ id: 'cfg-pushplus-token', name: 'pushplus_token', autocomplete: 'off' }"
            />
            <NButton :loading="pushplusTestLoading" @click="handleTestPushplus" type="primary" secondary size="small">
              <template #icon><NIcon><SendOutline /></NIcon></template>
              测试
            </NButton>
            <NButton type="primary" size="small" :loading="savePushplusLoading" @click="handleSavePushplus">
              <template #icon><NIcon><SaveOutline /></NIcon></template>
              保存
            </NButton>
          </div>
          <div v-if="pushplusTestResult" :style="{ color: pushplusTestOk ? 'var(--green)' : 'var(--red)', fontSize: '12px', marginTop: '2px' }">
            {{ pushplusTestResult }}
          </div>
          <span class="config-hint">PushPlus 是个人微信推送，已替代原企业微信。在 pushplus.plus 扫码后复制 token 粘贴到这里。</span>
        </div>
      </NCard>

      <NCard title="扫描设置" size="small" style="margin-bottom: 16px">
        <div class="form-row">
          <label class="field-label">扫描间隔（秒）</label>
          <div class="field-inline">
            <NInputNumber
              v-model:value="configStore.config.scan_interval_seconds"
              :min="10"
              :max="300"
              size="small"
              style="width: 140px"
            />
            <span class="config-hint">建议30秒</span>
            <div style="flex: 1"></div>
            <NButton type="primary" size="small" :loading="saveScanLoading" @click="handleSaveScan">
              <template #icon><NIcon><SaveOutline /></NIcon></template>
              保存
            </NButton>
          </div>
        </div>
      </NCard>

      <NCard title="AI 市场日报" size="small" style="margin-bottom: 16px">
        <div class="form-row" style="margin-bottom: 12px">
          <label class="field-label">日报开关</label>
          <div class="field-inline">
            <NSwitch
              :value="configStore.config.ai_report_enabled"
              @update:value="handleToggleAiReport"
            />
            <span class="config-hint">{{ configStore.config.ai_report_enabled ? '已开启，定时生成盘面分析' : '已关闭' }}</span>
          </div>
        </div>
        <div class="form-row">
          <label class="field-label" for="cfg-api-key">
            Anthropic API Key
            <CursorTooltip>
              <template #trigger>
                <NIcon :component="HelpCircleOutline" :size="15" class="help-icon" />
              </template>
              DeepSeek 官方或兼容 OpenAI 格式的 API Key
            </CursorTooltip>
          </label>
          <div class="field-inline">
            <NInput
              v-model:value="configStore.config.anthropic_api_key"
              placeholder="sk-..."
              size="small"
              type="text"
              style="flex: 1; min-width: min(400px, 100%)"
              :input-props="{ id: 'cfg-api-key', name: 'anthropic_api_key', autocomplete: 'off' }"
            />
          </div>
          <label class="field-label" for="cfg-ai-base-url" style="margin-top: 10px">API 地址</label>
          <div class="field-inline">
            <NInput
              v-model:value="configStore.config.ai_base_url"
              placeholder="https://api.deepseek.com/v1"
              size="small"
              style="flex: 1; min-width: min(400px, 100%)"
              :input-props="{ id: 'cfg-ai-base-url', name: 'ai_base_url', type: 'url', autocomplete: 'off' }"
            />
          </div>
          <label class="field-label" for="cfg-ai-model" style="margin-top: 10px">模型名称</label>
          <div class="field-inline">
            <NInput
              v-model:value="configStore.config.ai_model"
              placeholder="deepseek-chat"
              size="small"
              style="width: 250px"
              :input-props="{ id: 'cfg-ai-model', name: 'ai_model', autocomplete: 'off' }"
            />
            <NButton type="primary" size="small" :loading="saveAiLoading" @click="handleSaveAi">
              <template #icon><NIcon><SaveOutline /></NIcon></template>
              保存
            </NButton>
          </div>
          <span class="config-hint">推送时段：9:26 / 10:00 / 11:30 / 14:00 / 15:00</span>
        </div>
      </NCard>

      <NCard title="登录安全" size="small" style="margin-bottom: 16px">
        <div class="form-row">
          <label class="field-label">
            单点登录
            <CursorTooltip>
              <template #trigger>
                <NIcon :component="HelpCircleOutline" :size="15" class="help-icon" />
              </template>
              开启后同一账号仅允许一台设备登录，新登录将踢出已有会话；关闭后可多设备同时在线。所有登录行为均会记录到操作日志。
            </CursorTooltip>
          </label>
          <div class="field-inline">
            <NSwitch
              :value="configStore.config.sso_enabled"
              @update:value="handleToggleSso"
            />
            <span class="config-hint">{{ configStore.config.sso_enabled ? '已开启，同一账号仅允许一处登录' : '已关闭，同一账号可多设备同时登录' }}</span>
          </div>
        </div>
      </NCard>

      <NCard title="同花顺数据源" size="small" style="margin-bottom: 16px">
        <div class="form-row">
          <label class="field-label" for="cfg-ths-path">
            同花顺安装路径
            <CursorTooltip>
              <template #trigger>
                <NIcon :component="HelpCircleOutline" :size="15" class="help-icon" />
              </template>
              设置后可在股票池页面导入同花顺自选股分组
            </CursorTooltip>
          </label>
          <div class="field-inline">
            <NInput
              v-model:value="thsPath"
              placeholder="例如: D:\Program Files\同花顺远航版"
              size="small"
              style="flex: 1; min-width: min(400px, 100%)"
              :input-props="{ id: 'cfg-ths-path', name: 'ths_path', autocomplete: 'off' }"
            />
            <NButton type="primary" size="small" :loading="thsPathSaving" @click="handleSaveThsPath">
              <template #icon><NIcon><SaveOutline /></NIcon></template>
              保存
            </NButton>
          </div>
          <span v-if="thsFileHint" class="config-hint" style="color: var(--green)">已定位文件: {{ thsFileHint }}</span>
        </div>
      </NCard>
    </div>
    </Transition>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text1);
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text2);
}
.field-inline {
  display: flex;
  align-items: center;
  gap: 8px;
}
.help-icon {
  color: var(--primary);
  cursor: help;
}
.config-hint {
  font-size: 12px;
  color: var(--text2);
  white-space: nowrap;
}
.pref-note {
  font-size: 12px;
  color: var(--text2);
  line-height: 1.6;
  margin-bottom: 10px;
}
.pref-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pref-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--border, #eee);
  border-radius: 8px;
  background: var(--surface, #fff);
}
.pref-item--phone {
  flex-direction: column;
  align-items: flex-start;
}
.pref-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.pref-tag {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  background: var(--primary-fade, rgba(32,128,240,.1));
  padding: 1px 8px;
  border-radius: 4px;
}
.pref-desc {
  font-size: 13px;
  color: var(--text1);
  word-break: break-word;
}
.pref-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.pref-item--phone .pref-meta {
  width: 100%;
  justify-content: space-between;
}
</style>
