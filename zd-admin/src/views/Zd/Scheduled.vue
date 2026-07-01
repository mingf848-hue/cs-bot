<script setup lang="ts">
import { onMounted } from 'vue'
import {
  ElButton,
  ElInput,
  ElInputNumber,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTimePicker
} from 'element-plus'
import { useZd } from './useZd'

const { state, ensureLoaded, save } = useZd()

defineOptions({ name: 'ZdScheduled' })

onMounted(ensureLoaded)

const addMessage = () => {
  state.config.scheduled_messages.push({
    id: `schedule_${Date.now()}`,
    name: `定时发送 #${state.config.scheduled_messages.length + 1}`,
    enabled: true,
    time: '09:00',
    frequency: 'daily',
    account: '',
    groups: [],
    text: ''
  })
}

const addBackend = () => {
  state.config.scheduled_backend_actions.push({
    id: `backend_${Date.now()}`,
    name: `后台操作 #${state.config.scheduled_backend_actions.length + 1}`,
    enabled: true,
    time: '06:00',
    frequency: 'daily',
    action: 'venue_display_control',
    target: '双赢彩票',
    mode: 'maintenance',
    sites: ['9001', '6001'],
    maintenance_start: '06:00',
    maintenance_end: '07:05'
  })
}
</script>

<template>
  <div v-loading="state.loading" class="zd-page">
    <div class="zd-toolbar">
      <div>
        <div class="zd-title">定时任务</div>
        <div class="zd-subtitle">定时发送、后台操作、跟单查询</div>
      </div>
      <ElButton type="primary" :loading="state.saving" @click="save">保存配置</ElButton>
    </div>

    <ElTabs type="border-card">
      <ElTabPane label="定时发送">
        <div class="tab-head"
          ><ElButton size="small" type="primary" @click="addMessage">添加任务</ElButton></div
        >
        <ElTable :data="state.config.scheduled_messages" height="560" size="small">
          <ElTableColumn label="启用" width="80"
            ><template #default="scope"
              ><ElSwitch v-if="scope?.row" v-model="scope.row.enabled" /></template
          ></ElTableColumn>
          <ElTableColumn label="名称" min-width="180"
            ><template #default="scope"
              ><ElInput v-if="scope?.row" v-model="scope.row.name" /></template
          ></ElTableColumn>
          <ElTableColumn label="时间" width="130"
            ><template #default="scope"
              ><ElTimePicker
                v-if="scope?.row"
                v-model="scope.row.time"
                format="HH:mm"
                value-format="HH:mm" /></template
          ></ElTableColumn>
          <ElTableColumn label="频率" width="130"
            ><template #default="scope"
              ><ElSelect v-if="scope?.row" v-model="scope.row.frequency"
                ><ElOption label="每天" value="daily" /><ElOption
                  label="一次"
                  value="once" /></ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="内容" min-width="260"
            ><template #default="scope"
              ><ElInput
                v-if="scope?.row"
                v-model="scope.row.text"
                type="textarea"
                :rows="2" /></template
          ></ElTableColumn>
          <ElTableColumn width="80"
            ><template #default="scope"
              ><ElButton
                v-if="scope?.$index !== undefined"
                link
                type="danger"
                @click="state.config.scheduled_messages.splice(scope.$index, 1)"
                >删除</ElButton
              ></template
            ></ElTableColumn
          >
        </ElTable>
      </ElTabPane>
      <ElTabPane label="后台操作">
        <div class="tab-head"
          ><ElButton size="small" type="primary" @click="addBackend">添加操作</ElButton></div
        >
        <ElTable :data="state.config.scheduled_backend_actions" height="560" size="small">
          <ElTableColumn label="启用" width="80"
            ><template #default="scope"
              ><ElSwitch v-if="scope?.row" v-model="scope.row.enabled" /></template
          ></ElTableColumn>
          <ElTableColumn label="名称" min-width="180"
            ><template #default="scope"
              ><ElInput v-if="scope?.row" v-model="scope.row.name" /></template
          ></ElTableColumn>
          <ElTableColumn label="时间" width="130"
            ><template #default="scope"
              ><ElTimePicker
                v-if="scope?.row"
                v-model="scope.row.time"
                format="HH:mm"
                value-format="HH:mm" /></template
          ></ElTableColumn>
          <ElTableColumn label="操作" width="120"
            ><template #default="scope"
              ><ElSelect v-if="scope?.row" v-model="scope.row.mode">
                <ElOption label="维护" value="maintenance" />
                <ElOption label="启用" value="enable" />
              </ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="场馆" min-width="160"
            ><template #default="scope"
              ><ElInput v-if="scope?.row" v-model="scope.row.target" placeholder="双赢彩票" /></template
          ></ElTableColumn>
          <ElTableColumn label="站点" width="170"
            ><template #default="scope"
              ><ElSelect v-if="scope?.row" v-model="scope.row.sites" multiple collapse-tags>
                <ElOption label="9001" value="9001" />
                <ElOption label="6001" value="6001" />
              </ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="维护开始" width="130">
            <template #default="scope">
              <ElTimePicker
                v-if="scope?.row && scope.row.mode !== 'enable'"
                v-model="scope.row.maintenance_start"
                format="HH:mm"
                value-format="HH:mm" />
            </template>
          </ElTableColumn>
          <ElTableColumn label="维护结束" width="130">
            <template #default="scope">
              <ElTimePicker
                v-if="scope?.row && scope.row.mode !== 'enable'"
                v-model="scope.row.maintenance_end"
                format="HH:mm"
                value-format="HH:mm" />
            </template>
          </ElTableColumn>
          <ElTableColumn width="80"
            ><template #default="scope"
              ><ElButton
                v-if="scope?.$index !== undefined"
                link
                type="danger"
                @click="state.config.scheduled_backend_actions.splice(scope.$index, 1)"
                >删除</ElButton
              ></template
            ></ElTableColumn
          >
        </ElTable>
      </ElTabPane>
      <ElTabPane label="跟单查询">
        <ElTable :data="state.config.ticket_follow_tasks" height="590" size="small">
          <ElTableColumn label="启用" width="80"
            ><template #default="scope"
              ><ElSwitch v-if="scope?.row" v-model="scope.row.enabled" /></template
          ></ElTableColumn>
          <ElTableColumn label="名称" min-width="180"
            ><template #default="scope"
              ><ElInput v-if="scope?.row" v-model="scope.row.name" /></template
          ></ElTableColumn>
          <ElTableColumn label="间隔分钟" width="140"
            ><template #default="scope"
              ><ElInputNumber
                v-if="scope?.row"
                v-model="scope.row.interval_minutes"
                :min="1" /></template
          ></ElTableColumn>
          <ElTableColumn label="推送目标" min-width="180"
            ><template #default="scope"
              ><ElInput v-if="scope?.row" v-model="scope.row.telegram_target" /></template
          ></ElTableColumn>
        </ElTable>
      </ElTabPane>
    </ElTabs>
  </div>
</template>

<style scoped>
.zd-page {
  display: grid;
  gap: 12px;
}
.zd-toolbar,
.tab-head {
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
.tab-head {
  justify-content: flex-end;
  margin-bottom: 12px;
}
</style>
