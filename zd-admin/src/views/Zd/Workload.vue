<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElButton, ElCard, ElDescriptions, ElDescriptionsItem, ElInput, ElMessage, ElTable, ElTableColumn, ElTag } from 'element-plus'
import { loadManualWorkloadStatus, runManualWorkloadScan } from '@/api/zd'
import { useZd } from './useZd'

const { state, ensureLoaded, refreshRuntime } = useZd()

defineOptions({ name: 'ZdWorkload' })

const query = ref('')
const manual = reactive({
  loading: false,
  status: {} as any,
  result: null as any
})

const loadManualStatus = async () => {
  try {
    manual.status = await loadManualWorkloadStatus()
  } catch (error: any) {
    ElMessage.error(error?.message || '读取手动工作量状态失败')
  }
}

const refreshAll = async () => {
  await Promise.all([refreshRuntime(), loadManualStatus()])
}

const runManual = async (sync: boolean) => {
  manual.loading = true
  try {
    const data = await runManualWorkloadScan(sync)
    manual.result = data
    await loadManualStatus()
    if (data.success) {
      ElMessage.success(sync ? '手动执行成功，已同步表格' : '扫描完成')
    } else {
      ElMessage.error(data.msg || '手动执行失败')
    }
  } catch (error: any) {
    manual.result = { success: false, msg: error?.message || '手动执行失败' }
    ElMessage.error(error?.message || '手动执行失败')
    await loadManualStatus()
  } finally {
    manual.loading = false
  }
}

onMounted(async () => {
  await ensureLoaded()
  await loadManualStatus()
})

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

const manualStatusType = computed(() => {
  const text = String(manual.status.status || manual.result?.msg || '')
  if (manual.result?.success || text.includes('成功')) return 'success'
  if (text.includes('失败') || text.includes('错误')) return 'danger'
  if (text.includes('执行中')) return 'warning'
  return 'info'
})

const latestEntry = computed(() => manual.result?.entry || null)
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
        <ElButton @click="refreshAll">刷新</ElButton>
      </div>
    </div>

    <ElCard shadow="never" class="manual-card">
      <template #header>
        <div class="card-header">
          <span>手动工作量同步</span>
          <ElTag :type="manualStatusType">{{ manual.status.status || '未读取' }}</ElTag>
        </div>
      </template>
      <div class="manual-layout">
        <ElDescriptions :column="4" border size="small">
          <ElDescriptionsItem label="来源群">
            {{ manual.status.source_group || manual.status.last_chat_title || '-' }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="统计人员">
            {{ manual.status.worker || '-' }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="自动时间">
            {{ String(manual.status.auto_hour ?? '-').padStart(2, '0') }}:{{
              String(manual.status.auto_minute ?? '-').padStart(2, '0')
            }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="下次执行">
            {{ manual.status.next_run || '-' }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="上次完成">
            {{ manual.status.last_finished || '-' }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="目标日期">
            {{ manual.status.last_target_day || latestEntry?.date || '-' }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="上次总量">
            {{ manual.status.last_total ?? latestEntry?.total ?? '-' }}
          </ElDescriptionsItem>
          <ElDescriptionsItem label="同步结果">
            {{ manual.status.last_sync_msg || manual.result?.msg || '-' }}
          </ElDescriptionsItem>
        </ElDescriptions>
        <div class="manual-actions">
          <ElButton :loading="manual.loading" @click="runManual(false)">只扫描</ElButton>
          <ElButton type="primary" :loading="manual.loading" @click="runManual(true)">
            手动执行并同步
          </ElButton>
          <ElButton tag="a" href="/tool/work_stats" target="_blank">打开旧统计页</ElButton>
        </div>
        <div v-if="manual.status.last_error || manual.result?.msg" class="manual-message">
          <strong>{{ manual.result?.success === false ? '失败原因' : '执行说明' }}：</strong>
          {{ manual.status.last_error || manual.result?.msg }}
        </div>
      </div>
    </ElCard>

    <div class="metric-row">
      <ElCard shadow="never"><strong>{{ totalSummary.total }}</strong><span>处理记录</span></ElCard>
      <ElCard shadow="never"><strong>{{ totalSummary.action_count }}</strong><span>执行动作</span></ElCard>
      <ElCard shadow="never"><strong>{{ totalSummary.success }}</strong><span>成功</span></ElCard>
      <ElCard shadow="never"><strong>{{ totalSummary.failed }}</strong><span>失败</span></ElCard>
    </div>

    <ElCard shadow="never">
      <template #header>
        <div class="card-header">
          <span>监控执行聚合</span>
          <span class="hint">来自最近执行记录，成功/失败已转成中文</span>
        </div>
      </template>
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
  min-width: 0;
}

.zd-toolbar,
.actions,
.metric-row,
.card-header {
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

.manual-card :deep(.el-card__body) {
  padding: 14px;
}

.manual-layout {
  display: grid;
  gap: 12px;
}

.manual-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.manual-message {
  border-radius: 8px;
  background: #f8fafc;
  padding: 10px 12px;
  color: #475569;
  font-size: 13px;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
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
