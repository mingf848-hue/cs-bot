<script setup lang="ts">
import { onMounted } from 'vue'
import {
  ElButton,
  ElCard,
  ElCol,
  ElForm,
  ElFormItem,
  ElInput,
  ElRow,
  ElSwitch,
  ElTimePicker
} from 'element-plus'
import { useZd } from './useZd'

const { state, ensureLoaded, save } = useZd()

defineOptions({ name: 'ZdSettings' })

onMounted(ensureLoaded)
</script>

<template>
  <div v-loading="state.loading" class="settings-page">
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
  </div>
</template>

<style scoped>
.settings-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  max-width: 980px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
