<script setup lang="ts">
import { computed, onMounted } from 'vue'
import {
  ElButton,
  ElCard,
  ElInput,
  ElMessage,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { toggleMonitor } from '@/api/zd'
import { useZd } from './useZd'

const { state, ensureLoaded, refresh, save } = useZd()

defineOptions({ name: 'ZdAccounts' })

onMounted(ensureLoaded)

const accountRows = computed(() => state.runtime.accounts || [])

const ensureAiConfig = (account: string) => {
  state.config.ai_private_reply = state.config.ai_private_reply || { accounts: {} }
  state.config.ai_private_reply.accounts = state.config.ai_private_reply.accounts || {}
  if (!state.config.ai_private_reply.accounts[account]) {
    state.config.ai_private_reply.accounts[account] = { enabled: false, prompt: '' }
  }
  return state.config.ai_private_reply.accounts[account]
}

const toggleAccountMonitor = async (row: any) => {
  const next = !row.monitor_enabled
  row.monitor_enabled = next
  const res = await toggleMonitor({ account: row.name, effective_enabled: next })
  if (!res.success) throw new Error(res.msg || '切换失败')
  ElMessage.success(`${row.name} 已${next ? '开启' : '停止'}监听`)
  await refresh()
}
</script>

<template>
  <div v-loading="state.loading" class="accounts-page">
    <div class="zd-toolbar">
      <div>
        <div class="zd-title">账号管理</div>
        <div class="zd-subtitle">账号在线状态、单账号监听、私聊 AI 回复配置</div>
      </div>
      <div class="actions">
        <ElButton @click="refresh">刷新</ElButton>
        <ElButton type="primary" :loading="state.saving" @click="save">保存配置</ElButton>
      </div>
    </div>

    <ElCard shadow="never">
      <ElTable :data="accountRows" height="640" size="small">
        <ElTableColumn prop="name" label="账号" min-width="160" show-overflow-tooltip />
        <ElTableColumn prop="role" label="角色" width="100" />
        <ElTableColumn prop="user_id" label="User ID" width="140" show-overflow-tooltip />
        <ElTableColumn label="在线" width="90">
          <template #default="scope">
            <ElTag size="small" :type="scope.row.connected === false ? 'danger' : 'success'">
              {{ scope.row.connected === false ? '离线' : '在线' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="监听" width="120">
          <template #default="scope">
            <ElSwitch
              :model-value="scope.row.monitor_enabled !== false"
              @change="toggleAccountMonitor(scope.row)"
            />
          </template>
        </ElTableColumn>
        <ElTableColumn label="私聊 AI" width="110">
          <template #default="scope">
            <ElSwitch v-model="ensureAiConfig(scope.row.name).enabled" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="私聊提示词" min-width="360">
          <template #default="scope">
            <ElInput
              v-model="ensureAiConfig(scope.row.name).prompt"
              type="textarea"
              :rows="2"
              placeholder="这个账号私聊时的说话风格"
            />
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.accounts-page {
  display: grid;
  gap: 12px;
}

.zd-toolbar,
.actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.zd-title {
  font-size: 20px;
  font-weight: 700;
}

.zd-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
