<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import {
  ElButton,
  ElCard,
  ElCol,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElRow,
  ElSwitch,
  ElTimePicker
} from 'element-plus'
import { runDomainPinUpdate } from '@/api/zd'
import { useZd } from './useZd'

const { state, ensureLoaded, save } = useZd()

const domainPin = reactive({
  source_account: 'ara账号',
  target_account: '组长值班',
  source_chat_ids: '-1002819832851\n-1002560892878',
  history_limit: 80,
  dry_run: true,
  running: false,
  result: ''
})

onMounted(ensureLoaded)

const updateDomainPin = async (dryRun: boolean) => {
  domainPin.running = true
  domainPin.result = dryRun ? '正在预览...' : '正在更新...'
  try {
    const data = await runDomainPinUpdate({ ...domainPin, dry_run: dryRun })
    domainPin.result = [
      data.msg || (data.success ? '完成' : '失败'),
      ...(data.results || []).map((item: any) => {
        const label =
          item.status === 'failed'
            ? '失败'
            : item.status === 'preview'
              ? '预览'
              : item.status === 'unchanged'
                ? '无变化'
                : '成功'
        const chat = item.chat_name ? `${item.chat_name}（${item.chat_id}）` : item.chat_id
        return `${chat} ${label}${item.error ? `：${item.error}` : ''}`
      })
    ].join('\n')
    ElMessage[data.success ? 'success' : 'error'](data.msg || (dryRun ? '预览完成' : '更新完成'))
  } finally {
    domainPin.running = false
  }
}
</script>

<template>
  <div v-loading="state.loading" class="settings-grid">
    <ElCard shadow="never">
      <template #header>
        <div class="card-header">
          <span>系统设置</span>
          <ElButton type="primary" :loading="state.saving" @click="save">保存配置</ElButton>
        </div>
      </template>
      <ElForm label-position="top">
        <ElRow :gutter="12">
          <ElCol :span="8">
            <ElFormItem label="总监听开关"><ElSwitch v-model="state.config.enabled" /></ElFormItem>
          </ElCol>
          <ElCol :span="8">
            <ElFormItem label="副账号发送"
              ><ElSwitch v-model="state.config.extra_enabled"
            /></ElFormItem>
          </ElCol>
          <ElCol :span="8">
            <ElFormItem label="排班启用"
              ><ElSwitch v-model="state.config.schedule.active"
            /></ElFormItem>
          </ElCol>
          <ElCol :span="8">
            <ElFormItem label="上班时间"
              ><ElTimePicker
                v-model="state.config.schedule.start"
                format="HH:mm"
                value-format="HH:mm"
            /></ElFormItem>
          </ElCol>
          <ElCol :span="8">
            <ElFormItem label="下班时间"
              ><ElTimePicker
                v-model="state.config.schedule.end"
                format="HH:mm"
                value-format="HH:mm"
            /></ElFormItem>
          </ElCol>
          <ElCol :span="24">
            <ElFormItem label="审批关键词">
              <ElInput
                type="textarea"
                :rows="4"
                :model-value="(state.config.approval_keywords || []).join('\n')"
                @update:model-value="
                  state.config.approval_keywords = $event
                    .split(/\n|,/)
                    .map((x: string) => x.trim())
                    .filter(Boolean)
                "
              />
            </ElFormItem>
          </ElCol>
        </ElRow>
      </ElForm>
    </ElCard>

    <ElCard shadow="never">
      <template #header>域名更新置顶</template>
      <ElForm label-position="top">
        <ElRow :gutter="12">
          <ElCol :span="6"
            ><ElFormItem label="读取账号"><ElInput v-model="domainPin.source_account" /></ElFormItem
          ></ElCol>
          <ElCol :span="6"
            ><ElFormItem label="编辑账号"><ElInput v-model="domainPin.target_account" /></ElFormItem
          ></ElCol>
          <ElCol :span="6"
            ><ElFormItem label="读取条数"
              ><ElInputNumber v-model="domainPin.history_limit" :min="10" :max="300" /></ElFormItem
          ></ElCol>
          <ElCol :span="24">
            <ElFormItem label="来源群 ID">
              <ElInput v-model="domainPin.source_chat_ids" type="textarea" :rows="4" />
            </ElFormItem>
          </ElCol>
          <ElCol :span="24">
            <div class="domain-actions">
              <ElButton :loading="domainPin.running" @click="updateDomainPin(true)">预览</ElButton>
              <ElButton type="primary" :loading="domainPin.running" @click="updateDomainPin(false)"
                >执行更新</ElButton
              >
            </div>
          </ElCol>
          <ElCol :span="24">
            <ElInput v-model="domainPin.result" type="textarea" :rows="10" readonly />
          </ElCol>
        </ElRow>
      </ElForm>
    </ElCard>
  </div>
</template>

<style scoped>
.settings-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
.card-header,
.domain-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.domain-actions {
  justify-content: flex-end;
  margin-bottom: 12px;
}
</style>
