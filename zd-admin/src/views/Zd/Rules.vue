<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useZd } from './useZd'

const { state, ruleStats, ensureLoaded, save } = useZd()
const query = ref('')
const selected = ref<any>(null)

onMounted(async () => {
  await ensureLoaded()
  selected.value = state.config.rules[0] || null
})

const filteredRules = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return state.config.rules
  return state.config.rules.filter((rule: any) =>
    [rule.name, rule.id, (rule.keywords || []).join(','), (rule.groups || []).join(',')]
      .join(' ')
      .toLowerCase()
      .includes(keyword)
  )
})

const addRule = () => {
  const rule = {
    id: `rule_${Date.now()}`,
    name: `新规则 #${state.config.rules.length + 1}`,
    enabled: true,
    groups: [],
    keywords: [],
    check_file: false,
    reply_account: '',
    cooldown: 1,
    replies: [{ type: 'text', text: '收到', min: 1, max: 2 }]
  }
  state.config.rules.unshift(rule)
  selected.value = rule
}

const removeRule = (row: any) => {
  const index = state.config.rules.indexOf(row)
  if (index >= 0) state.config.rules.splice(index, 1)
  if (selected.value === row) selected.value = state.config.rules[0] || null
}

const textList = (value: any) => (Array.isArray(value) ? value.join('\n') : '')
const setTextList = (row: any, key: string, value: string) => {
  row[key] = value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean)
}
</script>

<template>
  <div v-loading="state.loading" class="rules-layout">
    <ElCard shadow="never" class="rules-list">
      <template #header>
        <div class="card-header">
          <span>规则管理</span>
          <ElButton size="small" type="primary" @click="addRule">新建规则</ElButton>
        </div>
      </template>
      <div class="rule-summary">
        <ElTag>全部 {{ ruleStats.total }}</ElTag>
        <ElTag type="success">运行 {{ ruleStats.running }}</ElTag>
        <ElTag type="info">停用 {{ ruleStats.disabled }}</ElTag>
        <ElTag type="warning">草稿 {{ ruleStats.draft }}</ElTag>
      </div>
      <ElInput v-model="query" clearable placeholder="搜索规则名、关键词、群 ID" class="mt-12px" />
      <ElTable
        :data="filteredRules"
        height="calc(100vh - 270px)"
        size="small"
        highlight-current-row
        class="mt-12px"
        @row-click="selected = $event"
      >
        <ElTableColumn prop="name" label="规则" min-width="180" show-overflow-tooltip />
        <ElTableColumn label="状态" width="80">
          <template #default="{ row }">
            <ElTag size="small" :type="row.enabled === false ? 'info' : 'success'">
              {{ row.enabled === false ? '停用' : '运行' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn width="70" align="center">
          <template #default="{ row }">
            <ElButton link type="danger" @click.stop="removeRule(row)">删除</ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElCard shadow="never" class="rule-editor">
      <template #header>
        <div class="card-header">
          <span>{{ selected?.name || '选择规则' }}</span>
          <ElButton type="primary" :loading="state.saving" @click="save">保存配置</ElButton>
        </div>
      </template>
      <ElEmpty v-if="!selected" description="请选择或新建规则" />
      <ElForm v-else label-position="top" class="editor-grid">
        <ElFormItem label="规则名称"><ElInput v-model="selected.name" /></ElFormItem>
        <ElFormItem label="规则 ID"><ElInput v-model="selected.id" /></ElFormItem>
        <ElFormItem label="启用状态"><ElSwitch v-model="selected.enabled" /></ElFormItem>
        <ElFormItem label="冷却秒数"
          ><ElInputNumber v-model="selected.cooldown" :min="0"
        /></ElFormItem>
        <ElFormItem label="监听群 ID（一行一个）" class="span-2">
          <ElInput
            type="textarea"
            :rows="5"
            :model-value="textList(selected.groups)"
            @update:model-value="setTextList(selected, 'groups', $event)"
          />
        </ElFormItem>
        <ElFormItem label="关键词（一行一个）" class="span-2">
          <ElInput
            type="textarea"
            :rows="5"
            :model-value="textList(selected.keywords)"
            @update:model-value="setTextList(selected, 'keywords', $event)"
          />
        </ElFormItem>
        <ElFormItem label="回复账号"
          ><ElInput v-model="selected.reply_account" placeholder="留空使用默认账号"
        /></ElFormItem>
        <ElFormItem label="检查文件"><ElSwitch v-model="selected.check_file" /></ElFormItem>
        <ElFormItem label="回复流程 JSON" class="span-2">
          <ElInput
            type="textarea"
            :rows="14"
            :model-value="JSON.stringify(selected.replies || [], null, 2)"
            @change="
              (value: string) => {
                try {
                  selected.replies = JSON.parse(value)
                } catch (e) {}
              }
            "
          />
        </ElFormItem>
      </ElForm>
    </ElCard>
  </div>
</template>

<style scoped>
.rules-layout {
  display: grid;
  grid-template-columns: 420px minmax(0, 1fr);
  gap: 12px;
}
.card-header,
.rule-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.rule-summary {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.rules-list,
.rule-editor {
  min-height: calc(100vh - 126px);
}
.editor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 12px;
}
.span-2 {
  grid-column: 1 / -1;
}
</style>
