<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElCard, ElInput, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { useZd } from './useZd'

const { state, ensureLoaded, refreshRuntime } = useZd()

defineOptions({ name: 'ZdWorkload' })

const query = ref('')

onMounted(ensureLoaded)

const records = computed(() => state.runtime.records || state.runtime.recent || [])

const statusText = (record: any) => String(record.status || record.result || '-')

const workloadRows = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  const map = new Map<string, any>()
  records.value.forEach((record: any) => {
    const account = String(record.target_account || record.sender_name || '未标记')
    if (keyword && !account.toLowerCase().includes(keyword)) return
    const item =
      map.get(account) ||
      ({
        account,
        total: 0,
        success: 0,
        failed: 0,
        skipped: 0,
        action_count: 0,
        duration_ms: 0,
        last_time: ''
      } as any)
    item.total += 1
    item.action_count += Number(record.action_count || 0)
    item.duration_ms += Number(record.duration_ms || 0)
    const status = statusText(record)
    if (status === 'success') item.success += 1
    else if (status === 'failed') item.failed += 1
    else if (status === 'skipped') item.skipped += 1
    item.last_time = item.last_time || record.ts || record.time || ''
    map.set(account, item)
  })
  return Array.from(map.values()).sort((a, b) => b.total - a.total)
})

const totalSummary = computed(() =>
  workloadRows.value.reduce(
    (sum, item) => ({
      total: sum.total + item.total,
      success: sum.success + item.success,
      failed: sum.failed + item.failed,
      action_count: sum.action_count + item.action_count
    }),
    { total: 0, success: 0, failed: 0, action_count: 0 }
  )
)
</script>

<template>
  <div v-loading="state.loading" class="workload-page">
    <div class="zd-toolbar">
      <div>
        <div class="zd-title">工作量统计</div>
        <div class="zd-subtitle">按最近执行记录聚合账号处理量、成功失败和动作次数</div>
      </div>
      <div class="actions">
        <ElInput v-model="query" clearable placeholder="筛选账号" />
        <ElButton @click="refreshRuntime">刷新</ElButton>
      </div>
    </div>

    <div class="metric-row">
      <ElCard shadow="never"><strong>{{ totalSummary.total }}</strong><span>处理记录</span></ElCard>
      <ElCard shadow="never"><strong>{{ totalSummary.action_count }}</strong><span>执行动作</span></ElCard>
      <ElCard shadow="never"><strong>{{ totalSummary.success }}</strong><span>成功</span></ElCard>
      <ElCard shadow="never"><strong>{{ totalSummary.failed }}</strong><span>失败</span></ElCard>
    </div>

    <ElCard shadow="never">
      <ElTable :data="workloadRows" height="610" size="small">
        <ElTableColumn prop="account" label="账号" min-width="180" show-overflow-tooltip />
        <ElTableColumn prop="total" label="处理记录" width="110" />
        <ElTableColumn prop="action_count" label="动作次数" width="110" />
        <ElTableColumn prop="success" label="成功" width="90" />
        <ElTableColumn prop="failed" label="失败" width="90" />
        <ElTableColumn prop="skipped" label="跳过" width="90" />
        <ElTableColumn label="成功率" width="110">
          <template #default="scope">
            <ElTag size="small" :type="scope.row.failed ? 'warning' : 'success'">
              {{
                scope.row.success + scope.row.failed
                  ? Math.round((scope.row.success / (scope.row.success + scope.row.failed)) * 1000) /
                    10
                  : 0
              }}%
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="last_time" label="最近记录" min-width="180" show-overflow-tooltip />
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.workload-page {
  display: grid;
  gap: 12px;
}

.zd-toolbar,
.actions,
.metric-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.actions {
  min-width: 360px;
}

.metric-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.metric-row :deep(.el-card__body) {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.metric-row strong {
  color: var(--el-text-color-primary);
  font-size: 24px;
}

.metric-row span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
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
