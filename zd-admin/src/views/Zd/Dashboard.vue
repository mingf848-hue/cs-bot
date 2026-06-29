<script setup lang="ts">
import { computed, onMounted } from 'vue'
import {
  ElButton,
  ElCard,
  ElCol,
  ElMessage,
  ElRow,
  ElStatistic,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { toggleMonitor } from '@/api/zd'
import { useZd } from './useZd'

const { state, ruleStats, ensureLoaded, refresh, refreshRuntime } = useZd()

onMounted(() => {
  ensureLoaded()
  window.setInterval(() => refreshRuntime().catch(() => {}), 15000)
})

const mainAccount = computed(() => state.runtime.main_account || state.accounts[0] || '主账号')

const monitorText = computed(() => (state.config.enabled ? '监听运行中' : '监听已暂停'))

const toggleGlobalMonitor = async () => {
  const next = !state.config.enabled
  state.config.enabled = next
  const res = await toggleMonitor({ account: mainAccount.value, effective_enabled: next })
  if (!res.success) throw new Error(res.msg || '切换失败')
  ElMessage.success(`监听已${next ? '开启' : '关闭'}`)
  await refresh()
}
</script>

<template>
  <div v-loading="state.loading" class="zd-page">
    <div class="zd-toolbar">
      <div>
        <div class="zd-title">监控台</div>
        <div class="zd-subtitle">最后刷新：{{ state.lastLoadedAt || '未同步' }}</div>
      </div>
      <div class="zd-actions">
        <ElButton @click="refresh">刷新</ElButton>
        <ElButton :type="state.config.enabled ? 'danger' : 'primary'" @click="toggleGlobalMonitor">
          {{ state.config.enabled ? '暂停监听' : '开启监听' }}
        </ElButton>
      </div>
    </div>

    <ElRow :gutter="12">
      <ElCol :span="6">
        <ElCard shadow="never">
          <ElStatistic title="今日触发" :value="state.runtime.today.total || 0" />
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="never">
          <ElStatistic title="成功率" :value="state.runtime.today.success_rate || 0" suffix="%" />
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="never">
          <ElStatistic title="运行规则" :value="ruleStats.running" />
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="never">
          <ElStatistic title="账号数" :value="state.accounts.length" />
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="12" class="mt-12px">
      <ElCol :span="16">
        <ElCard shadow="never">
          <template #header>
            <div class="card-header">
              <span>最近执行</span>
              <ElTag :type="state.config.enabled ? 'success' : 'info'">{{ monitorText }}</ElTag>
            </div>
          </template>
          <ElTable :data="state.runtime.recent || []" height="430" size="small">
            <ElTableColumn prop="time" label="时间" width="160" />
            <ElTableColumn prop="rule_name" label="规则" min-width="160" show-overflow-tooltip />
            <ElTableColumn prop="status" label="状态" width="90">
              <template #default="scope">
                <ElTag
                  v-if="scope?.row"
                  size="small"
                  :type="scope.row.status === 'success' ? 'success' : 'danger'"
                >
                  {{ scope.row.status || '-' }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="detail" label="详情" min-width="260" show-overflow-tooltip />
          </ElTable>
        </ElCard>
      </ElCol>
      <ElCol :span="8">
        <ElCard shadow="never">
          <template #header>账号状态</template>
          <ElTable :data="state.runtime.accounts || []" height="430" size="small">
            <ElTableColumn prop="name" label="账号" min-width="120" />
            <ElTableColumn prop="status" label="状态" width="90">
              <template #default="scope">
                <ElTag
                  v-if="scope?.row"
                  size="small"
                  :type="scope.row.connected === false ? 'danger' : 'success'"
                >
                  {{ scope.row.connected === false ? '离线' : '在线' }}
                </ElTag>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>
  </div>
</template>

<style scoped>
.zd-page {
  display: grid;
  gap: 12px;
}
.zd-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.zd-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}
.zd-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.zd-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
