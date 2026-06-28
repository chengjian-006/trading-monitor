<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import {
  NDataTable, NButton, NInput, NSelect, NModal, NSpace,
  NPopconfirm, NSkeleton, NIcon, NTag, NTooltip,
} from 'naive-ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { useResponsive } from '../composables/useResponsive'
import { h } from 'vue'
import { PersonAddOutline, KeyOutline, TrashOutline, CheckmarkOutline, SearchOutline, CreateOutline } from '@vicons/ionicons5'
import { listUsers, createUser, deleteUser, resetPassword, updateUser } from '../api/auth'
import type { User } from '../types'

const message = useGlobalMessage()
const { isMobile } = useResponsive()
const users = ref<User[]>([])
const loading = ref(false)

const showAddModal = ref(false)
const newUsername = ref('')
const newPassword = ref('')
const newRole = ref('user')
const addLoading = ref(false)

const showResetModal = ref(false)
const resetUserId = ref(0)
const resetUserName = ref('')
const resetPwd = ref('')
const resetLoading = ref(false)

const showEditModal = ref(false)
const editUser = ref<User | null>(null)
const editUsername = ref('')
const editRole = ref('user')
const editMobile = ref('')
const editLoading = ref(false)

onMounted(() => loadUsers())

async function loadUsers() {
  loading.value = true
  try {
    users.value = await listUsers()
  } catch (e: any) {
    const status = e.response?.status
    if (status === 403) {
      message.error('需要管理员权限才能查看用户列表')
    } else if (status !== 401) {
      message.error('加载用户列表失败')
    }
  } finally {
    loading.value = false
  }
}

async function handleAdd() {
  if (!newUsername.value || !newPassword.value) {
    message.warning('请填写用户名和密码')
    return
  }
  addLoading.value = true
  try {
    await createUser(newUsername.value, newPassword.value, newRole.value)
    message.success('用户创建成功')
    showAddModal.value = false
    newUsername.value = ''
    newPassword.value = ''
    newRole.value = 'user'
    await loadUsers()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '创建失败')
  } finally {
    addLoading.value = false
  }
}

async function handleDelete(userId: number) {
  try {
    await deleteUser(userId)
    message.success('用户已删除')
    await loadUsers()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '删除失败')
  }
}

function openResetModal(userId: number, username: string) {
  resetUserId.value = userId
  resetUserName.value = username
  resetPwd.value = ''
  showResetModal.value = true
}

async function handleReset() {
  if (!resetPwd.value) {
    message.warning('请输入新密码')
    return
  }
  resetLoading.value = true
  try {
    await resetPassword(resetUserId.value, resetPwd.value)
    message.success('密码已重置')
    showResetModal.value = false
  } catch {
    message.error('重置失败')
  } finally {
    resetLoading.value = false
  }
}

function openEditModal(row: User) {
  editUser.value = row
  editUsername.value = row.username
  editRole.value = row.role
  editMobile.value = row.mobile || ''
  showEditModal.value = true
}

async function handleEdit() {
  if (!editUser.value) return
  if (!editUsername.value.trim()) {
    message.warning('用户名不能为空')
    return
  }
  editLoading.value = true
  try {
    await updateUser(editUser.value.id, {
      username: editUsername.value.trim(),
      role: editRole.value,
      mobile: editMobile.value.trim(),
    })
    message.success('用户信息已更新')
    showEditModal.value = false
    await loadUsers()
  } catch (e: any) {
    message.error(e.response?.data?.detail || '更新失败')
  } finally {
    editLoading.value = false
  }
}

// 移动端: 按钮只显示图标(配 tooltip), 省去文字以缩窄操作列
function actionBtn(icon: any, label: string, props: any) {
  const slots: any = { icon: () => h(NIcon, null, { default: () => h(icon) }) }
  if (!isMobile.value) slots.default = () => label
  const btn = h(NButton, { size: 'small', ...props }, slots)
  return isMobile.value
    ? h(NTooltip, null, { trigger: () => btn, default: () => label })
    : btn
}

const columns = computed(() => {
  const cols: any[] = [
    { title: '用户名', key: 'username', width: isMobile.value ? 90 : 100 },
    {
      title: '角色',
      key: 'role',
      width: isMobile.value ? 64 : 80,
      render: (row: User) => h(NTag, {
        size: 'small',
        type: row.role === 'admin' ? 'warning' : 'default',
        bordered: false,
      }, () => row.role === 'admin' ? '管理员' : '用户'),
    },
    {
      title: '操作',
      key: 'action',
      width: isMobile.value ? 132 : 220,
      render: (row: User) =>
        h(NSpace, { size: 'small' }, () => [
          actionBtn(CreateOutline, '编辑', { type: 'primary', secondary: true, onClick: () => openEditModal(row) }),
          actionBtn(KeyOutline, '重置密码', { secondary: true, onClick: () => openResetModal(row.id, row.username) }),
          row.role !== 'admin'
            ? h(NPopconfirm, {
                onPositiveClick: () => handleDelete(row.id),
              }, {
                trigger: () => actionBtn(TrashOutline, '删除', { type: 'error', secondary: true }),
                default: () => `确认删除用户 ${row.username}?`,
              })
            : null,
        ]),
    },
  ]
  // 桌面端额外列: ID / 创建时间(移动端隐藏, 让出宽度)
  if (!isMobile.value) {
    cols.unshift({ title: 'ID', key: 'id', width: 50 })
    cols.splice(3, 0, {
      title: '创建时间',
      key: 'created_at',
      width: 140,
      render: (row: User) => (row.created_at ?? '').replace('T', ' ').slice(0, 16),
    })
  }
  return cols
})
</script>

<template>
  <div>
    <div class="page-header">
      <span class="page-title">用户管理</span>
      <NSpace size="small">
        <NButton type="primary" size="small" :loading="loading" @click="loadUsers">
          <template #icon><NIcon><SearchOutline /></NIcon></template>
          查询
        </NButton>
        <NButton type="primary" size="small" @click="showAddModal = true">
          <template #icon><NIcon><PersonAddOutline /></NIcon></template>
          新增用户
        </NButton>
      </NSpace>
    </div>

    <NSkeleton v-if="loading" :repeat="3" text />
    <Transition v-else name="content-fade" appear>
      <div>
        <div class="table-summary">共 {{ users.length }} 个用户</div>
        <NDataTable :columns="columns" :data="users" :bordered="false" size="small" :resizable-columns="true" :row-key="(r: User) => r.id" max-height="calc(100vh - 180px)" />
      </div>
    </Transition>

    <NModal v-model:show="showAddModal" preset="card" title="新增用户" style="max-width: 400px" :closable="true" :mask-closable="true" :on-close="() => { showAddModal = false }">
      <NSpace vertical>
        <NInput v-model:value="newUsername" placeholder="用户名" :input-props="{ name: 'newUsername', autocomplete: 'off', 'aria-label': '用户名' }" />
        <NInput v-model:value="newPassword" type="password" placeholder="密码" show-password-on="click" :input-props="{ name: 'newPassword', autocomplete: 'new-password', 'aria-label': '密码' }" />
        <NSelect
          v-model:value="newRole"
          :options="[{ label: '普通用户', value: 'user' }, { label: '管理员', value: 'admin' }]"
        />
        <NButton type="primary" size="small" block :loading="addLoading" @click="handleAdd">
          <template #icon><NIcon><CheckmarkOutline /></NIcon></template>
          创建
        </NButton>
      </NSpace>
    </NModal>

    <NModal v-model:show="showResetModal" preset="card" :title="`重置密码 - ${resetUserName}`" style="max-width: 400px" :closable="true" :mask-closable="true" :on-close="() => { showResetModal = false }">
      <NSpace vertical>
        <NInput v-model:value="resetPwd" type="password" placeholder="新密码" show-password-on="click" :input-props="{ name: 'resetPwd', autocomplete: 'new-password', 'aria-label': '新密码' }" />
        <NButton type="primary" size="small" block :loading="resetLoading" @click="handleReset">
          <template #icon><NIcon><CheckmarkOutline /></NIcon></template>
          确认重置
        </NButton>
      </NSpace>
    </NModal>

    <NModal v-model:show="showEditModal" preset="card" :title="`编辑用户 - ${editUser?.username}`" style="max-width: 440px" :closable="true" :mask-closable="true" :on-close="() => { showEditModal = false }">
      <NSpace vertical size="large">
        <div class="edit-field">
          <label for="edit-username">用户名</label>
          <NInput v-model:value="editUsername" placeholder="用户名" :input-props="{ id: 'edit-username', name: 'editUsername' }" />
        </div>
        <div class="edit-field">
          <label>角色</label>
          <NSelect
            v-model:value="editRole"
            :options="[{ label: '普通用户', value: 'user' }, { label: '管理员', value: 'admin' }]"
          />
        </div>
        <div class="edit-field">
          <label for="edit-mobile">手机号（用于@提醒）</label>
          <NInput v-model:value="editMobile" placeholder="13800138000" :input-props="{ id: 'edit-mobile', name: 'editMobile', type: 'tel', autocomplete: 'tel' }" />
        </div>
        <NButton type="primary" size="small" block :loading="editLoading" @click="handleEdit">
          <template #icon><NIcon><CheckmarkOutline /></NIcon></template>
          保存
        </NButton>
      </NSpace>
    </NModal>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  position: sticky;
  top: 0;
  z-index: 50;
  background: var(--bg);
  padding: 8px 0;
}
.page-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text1);
}
.table-summary {
  text-align: right;
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 8px;
}
.edit-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.edit-field label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text2);
}
</style>
