<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  ElButton,
  ElCard,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElProgress,
  ElTable,
  ElTableColumn,
  ElTag
} from 'element-plus'
import { zdFetch } from '@/api/zd'

defineOptions({ name: 'ZdWorkload' })

type StatsRow = {
  keyword: string
  promo: number
  assist: number
}

type MatchLog = {
  kw: string
  text: string
  link?: string
}

const yesterdayDay = () => {
  const date = new Date()
  date.setDate(date.getDate() - 1)
  return date.getDate()
}

const day = ref(yesterdayDay())
const keywords = ref('')
const loadingKeywords = ref(false)
const running = ref(false)
const syncing = ref(false)
const progress = reactive({
  percent: 0,
  text: '准备就绪'
})
const resultRows = ref<StatsRow[]>([])
const matchLogs = ref<MatchLog[]>([])
const currentStats = ref<Record<string, { promo: number; assist: number }> | null>(null)
const syncStatus = ref('')

const totalCount = computed(() =>
  resultRows.value.reduce((sum, row) => sum + Number(row.promo || 0) + Number(row.assist || 0), 0)
)
const promoTotal = computed(() => resultRows.value.reduce((sum, row) => sum + Number(row.promo || 0), 0))
const assistTotal = computed(() =>
  resultRows.value.reduce((sum, row) => sum + Number(row.assist || 0), 0)
)

const keywordLines = computed(() =>
  keywords.value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
)

const renderStatsRows = (stats: Record<string, { promo: number; assist: number }>) => {
  resultRows.value = keywordLines.value.map((keyword) => ({
    keyword,
    promo: Number(stats?.[keyword]?.promo || 0),
    assist: Number(stats?.[keyword]?.assist || 0)
  }))
}

const loadDefaultKeywords = async () => {
  loadingKeywords.value = true
  try {
    const res = await fetch('/tool/work_stats', { credentials: 'same-origin' })
    const html = await res.text()
    const doc = new DOMParser().parseFromString(html, 'text/html')
    const textarea = doc.querySelector<HTMLTextAreaElement>('#keywordsInput')
    keywords.value = textarea?.value?.trim() || ''
    if (!keywords.value) ElMessage.warning('没有读取到默认关键词，可手动粘贴')
  } catch (error: any) {
    ElMessage.error(error?.message || '读取关键词失败')
  } finally {
    loadingKeywords.value = false
  }
}

const startStats = async () => {
  if (!day.value || !keywordLines.value.length) {
    ElMessage.warning('请填写统计日期和关键词')
    return
  }
  running.value = true
  progress.percent = 2
  progress.text = '连接服务器...'
  resultRows.value = []
  matchLogs.value = []
  currentStats.value = null
  syncStatus.value = ''
  try {
    const params = new URLSearchParams({
      day: String(day.value),
      keywords: keywords.value
    })
    const res = await fetch(`/api/work_stats_stream?${params}`, { credentials: 'same-origin' })
    if (!res.ok || !res.body) throw new Error(res.statusText || '统计请求失败')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        const text = line.trim()
        if (!text) continue
        const data = JSON.parse(text)
        if (data.type === 'progress') {
          progress.percent = Number(data.percent || 0)
          progress.text = data.msg || '统计中...'
        } else if (data.type === 'match') {
          matchLogs.value.push({
            kw: data.kw || '',
            text: data.text || '',
            link: data.link || ''
          })
        } else if (data.type === 'done') {
          currentStats.value = data.results || {}
          renderStatsRows(currentStats.value || {})
          progress.percent = 100
          progress.text = '统计完成'
        } else if (data.type === 'error') {
          throw new Error(data.msg || '统计失败')
        }
      }
    }
    ElMessage.success('统计完成')
  } catch (error: any) {
    progress.text = error?.message || '统计失败'
    ElMessage.error(progress.text)
  } finally {
    running.value = false
  }
}

const syncToSheet = async () => {
  if (!currentStats.value) {
    ElMessage.warning('请先统计后再同步')
    return
  }
  syncing.value = true
  syncStatus.value = '同步中...'
  try {
    const data = await zdFetch<any>('/api/sync_to_sheet', {
      method: 'POST',
      body: JSON.stringify({
        day: String(day.value),
        stats: currentStats.value
      })
    })
    if (data.success) {
      syncStatus.value = data.msg || '已同步'
      ElMessage.success(syncStatus.value)
    } else {
      syncStatus.value = data.msg || '同步失败'
      ElMessage.error(syncStatus.value)
    }
  } catch (error: any) {
    syncStatus.value = error?.message || '同步失败'
    ElMessage.error(syncStatus.value)
  } finally {
    syncing.value = false
  }
}

onMounted(loadDefaultKeywords)
</script>

<template>
  <div class="workload-page">
    <div class="page-head">
      <div>
        <div class="zd-title">工作量统计</div>
        <div class="zd-subtitle">统计稍等关键词在推广群、协助群中的数量</div>
      </div>
      <div class="head-actions">
        <ElButton :loading="loadingKeywords" @click="loadDefaultKeywords">重新读取关键词</ElButton>
        <ElButton tag="a" href="/tool/work_stats" target="_blank">打开旧统计页</ElButton>
      </div>
    </div>

    <div class="stats-layout">
      <ElCard shadow="never" class="config-card">
        <template #header>统计条件</template>
        <div class="form-stack">
          <label>
            <span>统计日期</span>
            <ElInputNumber v-model="day" :min="1" :max="31" controls-position="right" />
          </label>
          <label>
            <span>稍等关键词</span>
            <ElInput
              v-model="keywords"
              type="textarea"
              :rows="14"
              placeholder="每行一个关键词"
            />
          </label>
          <ElButton type="primary" :loading="running" @click="startStats">开始统计</ElButton>
          <div class="progress-box">
            <ElProgress :percentage="progress.percent" :stroke-width="8" />
            <span>{{ progress.text }}</span>
          </div>
        </div>
      </ElCard>

      <ElCard shadow="never" class="result-card">
        <template #header>
          <div class="card-header">
            <span>统计结果</span>
            <div class="result-actions">
              <ElTag type="info">合计 {{ totalCount }}</ElTag>
              <ElTag>推广 {{ promoTotal }}</ElTag>
              <ElTag type="warning">协助 {{ assistTotal }}</ElTag>
              <ElButton
                type="success"
                size="small"
                :disabled="!currentStats"
                :loading="syncing"
                @click="syncToSheet"
              >
                同步到表格
              </ElButton>
            </div>
          </div>
        </template>
        <div v-if="syncStatus" class="sync-status">{{ syncStatus }}</div>
        <ElTable :data="resultRows" height="520" size="small" empty-text="统计后显示结果">
          <ElTableColumn prop="keyword" label="关键词" min-width="220" show-overflow-tooltip />
          <ElTableColumn prop="promo" label="推广群" width="120" align="center" />
          <ElTableColumn prop="assist" label="协助群" width="120" align="center" />
          <ElTableColumn label="合计" width="120" align="center">
            <template #default="scope">
              {{ Number(scope.row.promo || 0) + Number(scope.row.assist || 0) }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>
    </div>

    <ElCard shadow="never" class="logs-card">
      <template #header>实时抓取记录</template>
      <div v-if="!matchLogs.length" class="empty-log">开始统计后显示命中的原消息记录</div>
      <div v-else class="log-list">
        <div v-for="(log, index) in matchLogs" :key="`${log.kw}-${index}`" class="log-row">
          <ElTag size="small">{{ log.kw }}</ElTag>
          <a v-if="log.link" :href="log.link" target="_blank">原消息</a>
          <span>{{ log.text }}</span>
        </div>
      </div>
    </ElCard>
  </div>
</template>

<style scoped>
.workload-page {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.page-head,
.head-actions,
.card-header,
.result-actions {
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
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.stats-layout {
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.form-stack {
  display: grid;
  gap: 14px;
}

.form-stack label {
  display: grid;
  gap: 8px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  font-weight: 600;
}

.form-stack :deep(.el-input-number),
.form-stack :deep(.el-textarea) {
  width: 100%;
}

.progress-box {
  display: grid;
  gap: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.result-card,
.config-card,
.logs-card {
  min-width: 0;
}

.sync-status {
  margin-bottom: 10px;
  border-radius: 8px;
  background: #f0fdf4;
  padding: 8px 10px;
  color: #15803d;
  font-size: 13px;
}

.log-list {
  display: grid;
  max-height: 220px;
  overflow: auto;
  gap: 6px;
}

.log-row {
  display: grid;
  grid-template-columns: 120px 72px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  border-bottom: 1px dashed #e5e7eb;
  padding: 6px 0;
  color: #475569;
  font-size: 12px;
}

.log-row a {
  color: #16a34a;
  text-decoration: none;
  font-weight: 600;
}

.log-row span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-log {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
