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
  ElTabs
} from 'element-plus'
import { useZd } from './useZd'
import { milanVenueOptions, venueOptions } from './venueOptions'

const { state, ensureLoaded, save } = useZd()

defineOptions({ name: 'ZdScheduled' })

onMounted(ensureLoaded)

const weekdayOptions = [
  { label: '周一', value: 1 },
  { label: '周二', value: 2 },
  { label: '周三', value: 3 },
  { label: '周四', value: 4 },
  { label: '周五', value: 5 },
  { label: '周六', value: 6 },
  { label: '周日', value: 7 }
]

const ensureWeekdays = (row: any) => {
  if (row?.frequency === 'weekly' && (!Array.isArray(row.weekdays) || !row.weekdays.length)) {
    row.weekdays = weekdayOptions.map((item) => item.value)
  }
}

const addMessage = () => {
  state.config.scheduled_messages.push({
    id: `schedule_${Date.now()}`,
    name: `定时发送 #${state.config.scheduled_messages.length + 1}`,
    enabled: true,
    time: '09:00',
    frequency: 'daily',
    weekdays: [1, 2, 3, 4, 5, 6, 7],
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
    weekdays: [1, 2, 3, 4, 5, 6, 7],
    action: 'venue_display_control',
    target: '双赢彩票',
    mode: 'maintenance',
    sites: ['9001', '6001'],
    maintenance_start: '06:00',
    maintenance_end: '07:05',
    jump_venue_id: 14
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
              ><input
                v-if="scope?.row"
                class="native-time"
                type="time"
                v-model="scope.row.time"
            /></template
          ></ElTableColumn>
          <ElTableColumn label="频率" width="130"
            ><template #default="scope"
              ><ElSelect
                v-if="scope?.row"
                v-model="scope.row.frequency"
                @change="ensureWeekdays(scope.row)"
                ><ElOption label="每天" value="daily" /><ElOption
                  label="每周"
                  value="weekly" /><ElOption
                  label="一次"
                  value="once" /></ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="周几" width="190">
            <template #default="scope">
              <ElSelect
                v-if="scope?.row && scope.row.frequency === 'weekly'"
                v-model="scope.row.weekdays"
                multiple
                collapse-tags
                collapse-tags-tooltip
              >
                <ElOption
                  v-for="day in weekdayOptions"
                  :key="`msg-day-${day.value}`"
                  :label="day.label"
                  :value="day.value"
                />
              </ElSelect>
            </template>
          </ElTableColumn>
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
              ><input
                v-if="scope?.row"
                class="native-time"
                type="time"
                v-model="scope.row.time"
            /></template
          ></ElTableColumn>
          <ElTableColumn label="频率" width="130"
            ><template #default="scope"
              ><ElSelect
                v-if="scope?.row"
                v-model="scope.row.frequency"
                @change="ensureWeekdays(scope.row)"
              >
                <ElOption label="每天" value="daily" />
                <ElOption label="每周" value="weekly" />
                <ElOption label="单次" value="once" />
              </ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="周几" width="190">
            <template #default="scope">
              <ElSelect
                v-if="scope?.row && scope.row.frequency === 'weekly'"
                v-model="scope.row.weekdays"
                multiple
                collapse-tags
                collapse-tags-tooltip
              >
                <ElOption
                  v-for="day in weekdayOptions"
                  :key="`backend-day-${day.value}`"
                  :label="day.label"
                  :value="day.value"
                />
              </ElSelect>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="120"
            ><template #default="scope"
              ><ElSelect v-if="scope?.row" v-model="scope.row.mode">
                <ElOption label="维护" value="maintenance" />
                <ElOption label="启用" value="enable" />
              </ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="场馆" min-width="170"
            ><template #default="scope"
              ><ElSelect v-if="scope?.row" v-model="scope.row.target" filterable>
                <ElOption
                  v-for="venue in venueOptions"
                  :key="venue.id"
                  :label="`${venue.label} / ${venue.category}`"
                  :value="venue.value"
                />
              </ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="站点" width="170"
            ><template #default="scope"
              ><ElSelect v-if="scope?.row" v-model="scope.row.sites" multiple collapse-tags>
                <ElOption label="米兰" value="9001" />
                <ElOption label="江南" value="6001" />
              </ElSelect></template
          ></ElTableColumn>
          <ElTableColumn label="维护开始" width="120">
            <template #default="scope">
              <input
                v-if="scope?.row && scope.row.mode !== 'enable'"
                class="native-time"
                type="time"
                v-model="scope.row.maintenance_start"
              />
            </template>
          </ElTableColumn>
          <ElTableColumn label="维护结束" width="120">
            <template #default="scope">
              <input
                v-if="scope?.row && scope.row.mode !== 'enable'"
                class="native-time"
                type="time"
                v-model="scope.row.maintenance_end"
              />
            </template>
          </ElTableColumn>
          <ElTableColumn label="自动跳转" min-width="170">
            <template #default="scope">
              <ElSelect
                v-if="scope?.row && scope.row.mode !== 'enable'"
                v-model="scope.row.jump_venue_id"
                filterable
              >
                <ElOption
                  v-for="venue in milanVenueOptions"
                  :key="`jump-${venue.id}`"
                  :label="`${venue.label} / ${venue.category}`"
                  :value="venue.id"
                />
              </ElSelect>
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
.native-time {
  width: 100%;
  height: 32px;
  box-sizing: border-box;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  padding: 0 10px;
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-blank);
  font-size: 13px;
  outline: none;
}
.native-time:focus {
  border-color: var(--el-color-primary);
}
</style>
