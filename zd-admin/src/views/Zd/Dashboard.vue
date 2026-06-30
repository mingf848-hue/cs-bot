<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
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

defineOptions({ name: 'ZdDashboard' })

let refreshTimer: number | undefined

onMounted(() => {
  ensureLoaded()
  refreshTimer = window.setInterval(() => refreshRuntime().catch(() => {}), 15000)
})

onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})

const monitorText = computed(() => (state.config.enabled ? '监听运行中' : '监听已暂停'))
const recentRecords = computed(() => state.runtime.records || state.runtime.recent || [])

const toggleGlobalMonitor = async () => {
  const next = !state.config.enabled
  state.config.enabled = next
  const res = await toggleMonitor({ enabled: next })
  if (!res.success) throw new Error(res.msg || '切换失败')
  ElMessage.success(`监听已${next ? '开启' : '关闭'}`)
  await refresh()
}

const toggleAccountMonitor = async (row: any) => {
  const next = !row.monitor_enabled
  row.monitor_enabled = next
  const res = await toggleMonitor({ account: row.name, effective_enabled: next })
  if (!res.success) throw new Error(res.msg || '切换失败')
  ElMessage.success(`${row.name} 已${next ? '开启' : '停止'}监听`)
  await refresh()
}

const recordTime = (row: any) => row.time || row.created_at || row.ts || '-'
const recordRule = (row: any) => row.rule_name || row.rule || row.rule_id || '-'
const recordStatus = (row: any) => row.status || row.result || '-'
const recordDetail = (row: any) =>
  row.detail || row.message || row.reason || row.error || row.action || row.event || '-'

const accountWorkload = computed(() => {
  const map = new Map<string, { account: string; total: number; success: number; failed: number }>()
  recentRecords.value.forEach((record: any) => {
    const account = record.target_account || record.sender_name || '未标记'
    const item = map.get(account) || { account, total: 0, success: 0, failed: 0 }
    item.total += 1
    if (recordStatus(record) === 'success') item.success += 1
    if (recordStatus(record) === 'failed') item.failed += 1
    map.set(account, item)
  })
  return Array.from(map.values()).sort((a, b) => b.total - a.total).slice(0, 6)
})
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
          <ElTable :data="recentRecords" height="430" size="small">
            <ElTableColumn label="时间" width="170">
              <template #default="scope">{{ scope?.row ? recordTime(scope.row) : '-' }}</template>
            </ElTableColumn>
            <ElTableColumn label="规则" min-width="160" show-overflow-tooltip>
              <template #default="scope">{{ scope?.row ? recordRule(scope.row) : '-' }}</template>
            </ElTableColumn>
            <ElTableColumn prop="status" label="状态" width="90">
              <template #default="scope">
                <ElTag
                  v-if="scope?.row"
                  size="small"
                  :type="recordStatus(scope.row) === 'success' ? 'success' : 'danger'"
                >
                  {{ recordStatus(scope.row) }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="详情" min-width="260" show-overflow-tooltip>
              <template #default="scope">{{ scope?.row ? recordDetail(scope.row) : '-' }}</template>
            </ElTableColumn>
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
            <ElTableColumn label="监听" width="90">
              <template #default="scope">
                <ElTag
                  v-if="scope?.row"
                  size="small"
                  :type="scope.row.monitor_enabled === false ? 'info' : 'success'"
                >
                  {{ scope.row.monitor_enabled === false ? '已停止' : '已开启' }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn width="92" align="center">
              <template #default="scope">
                <ElButton
                  v-if="scope?.row"
                  link
                  :type="scope.row.monitor_enabled === false ? 'primary' : 'danger'"
                  @click="toggleAccountMonitor(scope.row)"
                >
                  {{ scope.row.monitor_enabled === false ? '开启' : '停止' }}
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="12">
      <ElCol :span="12">
        <ElCard shadow="never">
          <template #header>规则热度</template>
          <ElTable :data="state.runtime.by_rule || []" height="280" size="small">
            <ElTableColumn prop="name" label="规则" min-width="180" show-overflow-tooltip />
            <ElTableColumn prop="total" label="触发" width="86" />
            <ElTableColumn prop="action_count" label="动作" width="86" />
            <ElTableColumn prop="success_rate" label="成功率" width="96">
              <template #default="scope">{{ scope.row.success_rate || 0 }}%</template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
      <ElCol :span="12">
        <ElCard shadow="never">
          <template #header>账号工作量</template>
          <ElTable :data="accountWorkload" height="280" size="small">
            <ElTableColumn prop="account" label="账号" min-width="160" show-overflow-tooltip />
            <ElTableColumn prop="total" label="处理" width="90" />
            <ElTableColumn prop="success" label="成功" width="90" />
            <ElTableColumn prop="failed" label="失败" width="90" />
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
