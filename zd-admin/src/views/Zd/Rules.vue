<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ElButton,
  ElCard,
  ElDivider,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTag
} from 'element-plus'
import { useZd } from './useZd'

const { state, ruleStats, ensureLoaded, save } = useZd()
const query = ref('')
const selected = ref<any>(null)
const activeRuleTab = ref('basic')
const draggedStepIndex = ref<number | null>(null)

const accountOptions = computed(() => {
  const main = state.runtime.main_account || '主账号'
  const names = new Set<string>()
  ;(state.accounts || []).forEach((name: string) => name && names.add(name))
  ;(state.config.available_accounts || []).forEach((name: string) => name && names.add(name))
  return [main, ...Array.from(names).filter((name) => name !== main)]
})

const replyTypes = [
  { label: '文字回复', value: 'text' },
  { label: '编辑上一条', value: 'edit_prev' },
  { label: '转发消息', value: 'forward' },
  { label: '复制文件', value: 'copy_file' },
  { label: '金额分流', value: 'amount_logic' },
  { label: '抢答检测', value: 'preempt_check' },
  { label: '回扫未命中', value: 'scan_nearby_missed' },
  { label: '通知用户', value: 'notify_user' },
  { label: '触发后台解锁', value: 'backend_unlock' },
  { label: 'Agent 编排', value: 'agent_orchestrator' }
]

const backendActions = [
  { label: '解锁短信', value: 'unlock_sms' },
  { label: '解锁谷歌', value: 'unlock_google' },
  { label: '催结算', value: 'urge_settlement' },
  { label: '代理现有流程', value: 'agent_existing' }
]

onMounted(async () => {
  await ensureLoaded()
  hydrateAllRules()
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

const defaultApprovalAction = () => ({
  reply_admin: '',
  reply_origin: '',
  forward_to: '',
  delay_1_min: 1,
  delay_1_max: 2,
  delay_2_min: 1,
  delay_2_max: 3,
  delay_3_min: 1,
  delay_3_max: 2
})

const hydrateApprovalAction = (action: any = {}) => {
  const base: any = defaultApprovalAction()
  const merged: any = { ...base, ...(action || {}) }
  ;['reply_admin', 'reply_origin', 'forward_to'].forEach((key) => {
    merged[key] = merged[key] || ''
  })
  for (let index = 1; index <= 3; index += 1) {
    merged[`delay_${index}_min`] = Number(merged[`delay_${index}_min`] ?? base[`delay_${index}_min`])
    merged[`delay_${index}_max`] = Number(merged[`delay_${index}_max`] ?? base[`delay_${index}_max`])
  }
  return merged
}

const hydrateReply = (reply: any = {}) => ({
  ...reply,
  type:
    reply.type === 'backend_unlock' && reply.backend_action === 'agent_existing'
      ? 'agent_orchestrator'
      : reply.type || 'text',
  text: reply.text || '',
  forward_to: reply.forward_to || '',
  member_pattern: reply.member_pattern || '',
  ip_pattern: reply.ip_pattern || '',
  backend_action: reply.backend_action || 'unlock_sms',
  telegram_account: reply.telegram_account || '',
  fail_notify_to: reply.fail_notify_to || '',
  fail_notify_text: reply.fail_notify_text || '',
  min: reply.min ?? 1,
  max: reply.max ?? 3,
  high_reply_min: reply.high_reply_min ?? reply.min ?? 1,
  high_reply_max: reply.high_reply_max ?? reply.max ?? 3,
  low_first_min: reply.low_first_min ?? reply.min ?? 1,
  low_first_max: reply.low_first_max ?? reply.max ?? 3,
  low_forward_min: reply.low_forward_min ?? 1.5,
  low_forward_max: reply.low_forward_max ?? 3,
  low_reply_min: reply.low_reply_min ?? 1.5,
  low_reply_max: reply.low_reply_max ?? 3
})

const hydrateRule = (rule: any) => {
  rule.enabled = rule.enabled !== false
  rule.groups = Array.isArray(rule.groups) ? rule.groups : []
  rule.keywords = Array.isArray(rule.keywords) ? rule.keywords : []
  rule.file_extensions = Array.isArray(rule.file_extensions) ? rule.file_extensions : []
  rule.filename_keywords = Array.isArray(rule.filename_keywords) ? rule.filename_keywords : []
  rule.sender_prefixes = Array.isArray(rule.sender_prefixes) ? rule.sender_prefixes : []
  rule.sender_mode = rule.sender_mode || 'exclude'
  rule.cooldown = rule.cooldown ?? 1
  rule.reply_account = rule.reply_account || ''
  rule.check_file = Boolean(rule.check_file)
  rule.enable_approval = Boolean(rule.enable_approval)
  rule.approval_action = hydrateApprovalAction(rule.approval_action)
  rule.replies = Array.isArray(rule.replies) ? rule.replies.map(hydrateReply) : []
  return rule
}

const hydrateAllRules = () => {
  state.config.rules = (state.config.rules || []).map(hydrateRule)
}

const addRule = () => {
  const rule = hydrateRule({
    id: `rule_${Date.now()}_${Math.floor(Math.random() * 10000)}`,
    name: `新规则 #${state.config.rules.length + 1}`,
    enabled: true,
    groups: [],
    check_file: false,
    keywords: [],
    file_extensions: [],
    filename_keywords: [],
    enable_approval: false,
    approval_action: defaultApprovalAction(),
    sender_mode: 'exclude',
    sender_prefixes: [],
    cooldown: 1,
    replies: [hydrateReply({ type: 'text', text: '', min: 1, max: 2 })],
    reply_account: ''
  })
  state.config.rules.unshift(rule)
  selected.value = rule
}

const removeRule = (row: any) => {
  const index = state.config.rules.indexOf(row)
  if (index >= 0) state.config.rules.splice(index, 1)
  if (selected.value === row) selected.value = state.config.rules[0] || null
}

const selectRule = (rule: any) => {
  selected.value = hydrateRule(rule)
}

const textList = (value: any) => (Array.isArray(value) ? value.join('\n') : '')
const setTextList = (row: any, key: string, value: string) => {
  const splitter = key === 'groups' ? /[\r\n]+/ : /[\r\n,，]+/
  row[key] = value
    .split(splitter)
    .map((item) => item.trim())
    .filter(Boolean)
}

const addStep = (rule: any) => {
  if (!Array.isArray(rule.replies)) rule.replies = []
  rule.replies.push(hydrateReply({ type: 'text', text: '', min: 1, max: 3 }))
}

const removeStep = (rule: any, index: number) => {
  rule.replies.splice(index, 1)
}

const moveStep = (rule: any, from: number, to: number) => {
  if (!rule?.replies || to < 0 || to >= rule.replies.length || from === to) return
  const [item] = rule.replies.splice(from, 1)
  rule.replies.splice(to, 0, item)
}

const onStepDragStart = (index: number) => {
  draggedStepIndex.value = index
}

const onStepDrop = (rule: any, index: number) => {
  if (draggedStepIndex.value === null) return
  moveStep(rule, draggedStepIndex.value, index)
  draggedStepIndex.value = null
}

const onStepDragEnd = () => {
  draggedStepIndex.value = null
}

const normalizeStep = (reply: any) => {
  Object.assign(reply, hydrateReply(reply))
}

const ensureApprovalAction = (rule: any) => {
  rule.approval_action = hydrateApprovalAction(rule.approval_action)
}

const splitAmountConfig = (reply: any) => {
  const parts = String(reply.text || '').split('|')
  while (parts.length < 3) parts.push('')
  return parts.slice(0, 3)
}

const amountPart = (reply: any, index: number) => splitAmountConfig(reply)[index] || ''
const setAmountPart = (reply: any, index: number, value: string) => {
  const parts = splitAmountConfig(reply)
  parts[index] = value
  reply.text = parts.join('|')
}

const amountLowLines = (reply: any) =>
  String(amountPart(reply, 2) || '')
    .split(/(?:;;|\\n|\r?\n)+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .join('\n')

const setAmountLowLines = (reply: any, value: string) => {
  const lines = value
    .split(/[\r\n]+/)
    .map((item) => item.trim())
    .filter(Boolean)
  setAmountPart(reply, 2, lines.join(';;'))
}

const handleSave = async () => {
  try {
    hydrateAllRules()
    await save()
    ElMessage.success('配置已保存')
  } catch (error: any) {
    ElMessage.error(error?.message || '保存失败')
  }
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
        @row-click="selectRule"
      >
        <ElTableColumn prop="name" label="规则" min-width="170" show-overflow-tooltip />
        <ElTableColumn label="状态" width="74">
          <template #default="scope">
            <ElTag
              v-if="scope?.row"
              size="small"
              :type="scope.row.enabled === false ? 'info' : 'success'"
            >
              {{ scope.row.enabled === false ? '停用' : '运行' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn width="64" align="center">
          <template #default="scope">
            <ElButton v-if="scope?.row" link type="danger" @click.stop="removeRule(scope.row)"
              >删除</ElButton
            >
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <ElCard shadow="never" class="rule-editor">
      <template #header>
        <div class="card-header">
          <span>{{ selected?.name || '选择规则' }}</span>
          <ElButton type="primary" :loading="state.saving" @click="handleSave">保存配置</ElButton>
        </div>
      </template>
      <ElEmpty v-if="!selected" description="请选择或新建规则" />
      <div v-else class="editor-body">
        <ElTabs v-model="activeRuleTab" type="border-card" class="rule-tabs">
          <ElTabPane label="基础规则" name="basic">
            <ElForm label-position="top" class="editor-grid compact-grid">
              <ElFormItem label="规则名称"><ElInput v-model="selected.name" /></ElFormItem>
              <ElFormItem label="规则 ID"><ElInput v-model="selected.id" /></ElFormItem>
              <ElFormItem label="启用状态"><ElSwitch v-model="selected.enabled" /></ElFormItem>
              <ElFormItem label="冷却秒数">
                <ElInputNumber v-model="selected.cooldown" :min="0" :controls="false" />
              </ElFormItem>
              <ElFormItem label="监听群 ID（一行一个）">
                <ElInput
                  type="textarea"
                  :rows="4"
                  :model-value="textList(selected.groups)"
                  @update:model-value="setTextList(selected, 'groups', $event)"
                />
              </ElFormItem>
              <ElFormItem label="关键词（一行一个，支持 r: 正则）">
                <ElInput
                  type="textarea"
                  :rows="4"
                  :model-value="textList(selected.keywords)"
                  @update:model-value="setTextList(selected, 'keywords', $event)"
                />
              </ElFormItem>
              <ElFormItem label="文件模式"><ElSwitch v-model="selected.check_file" /></ElFormItem>
              <ElFormItem label="发送者过滤">
                <ElSelect v-model="selected.sender_mode">
                  <ElOption label="排除前缀" value="exclude" />
                  <ElOption label="仅允许前缀" value="include" />
                </ElSelect>
              </ElFormItem>
              <ElFormItem label="文件后缀">
                <ElInput
                  :model-value="textList(selected.file_extensions)"
                  placeholder="xlsx, png"
                  @update:model-value="setTextList(selected, 'file_extensions', $event)"
                />
              </ElFormItem>
              <ElFormItem label="文件名关键词">
                <ElInput
                  :model-value="textList(selected.filename_keywords)"
                  placeholder="一行或逗号分隔"
                  @update:model-value="setTextList(selected, 'filename_keywords', $event)"
                />
              </ElFormItem>
              <ElFormItem label="发送者前缀" class="span-2">
                <ElInput
                  :model-value="textList(selected.sender_prefixes)"
                  placeholder="YY, AA"
                  @update:model-value="setTextList(selected, 'sender_prefixes', $event)"
                />
              </ElFormItem>
            </ElForm>
          </ElTabPane>

          <ElTabPane label="执行动作流" name="steps">
            <div class="section-head compact-head">
              <div>
                <div class="section-title">执行动作流</div>
                <div class="section-desc">拖动步骤调整顺序，也可以用上下按钮微调。</div>
              </div>
              <ElButton type="primary" plain @click="addStep(selected)">添加步骤</ElButton>
            </div>
            <div class="account-row">
              <span>规则回复账号</span>
              <ElSelect v-model="selected.reply_account" placeholder="主账号（默认）" clearable>
                <ElOption label="主账号（默认）" value="" />
                <ElOption v-for="account in accountOptions" :key="account" :label="account" :value="account" />
              </ElSelect>
            </div>

            <ElEmpty v-if="!selected.replies?.length" description="暂无动作步骤" />
            <div v-else class="steps">
              <div
                v-for="(reply, index) in selected.replies"
                :key="index"
                class="step-panel"
                draggable="true"
                @dragstart="onStepDragStart(index)"
                @dragend="onStepDragEnd"
                @dragover.prevent
                @drop="onStepDrop(selected, index)"
              >
                <div class="step-head">
                  <div class="step-index" title="拖动调整顺序">#{{ index + 1 }}</div>
                  <ElSelect v-model="reply.type" class="step-type" @change="normalizeStep(reply)">
                    <ElOption
                      v-for="item in replyTypes"
                      :key="item.value"
                      :label="item.label"
                      :value="item.value"
                    />
                  </ElSelect>
                  <div v-if="reply.type !== 'amount_logic'" class="delay-inputs">
                    <ElInputNumber v-model="reply.min" :controls="false" :min="0" />
                    <span>-</span>
                    <ElInputNumber v-model="reply.max" :controls="false" :min="0" />
                    <span>秒</span>
                  </div>
                  <div class="step-order">
                    <ElButton link :disabled="index === 0" @click="moveStep(selected, index, index - 1)">
                      上移
                    </ElButton>
                    <ElButton
                      link
                      :disabled="index === selected.replies.length - 1"
                      @click="moveStep(selected, index, index + 1)"
                    >
                      下移
                    </ElButton>
                  </div>
                  <ElButton link type="danger" @click="removeStep(selected, index)">删除</ElButton>
                </div>

                <div v-if="reply.type === 'text' || reply.type === 'edit_prev'" class="step-grid">
                  <ElFormItem label="回复内容" class="span-2">
                    <ElInput v-model="reply.text" type="textarea" :rows="3" />
                  </ElFormItem>
                </div>

              <div v-else-if="reply.type === 'forward' || reply.type === 'copy_file'" class="step-grid">
                <ElFormItem label="目标群/用户名">
                  <ElInput v-model="reply.forward_to" placeholder="-1001234567890 或 @username" />
                </ElFormItem>
                <ElFormItem label="附加说明">
                  <ElInput v-model="reply.text" placeholder="可留空" />
                </ElFormItem>
              </div>

              <div v-else-if="reply.type === 'amount_logic'" class="step-grid">
                <ElFormItem label="金额阈值">
                  <ElInput
                    :model-value="amountPart(reply, 0)"
                    placeholder="例如 500"
                    @update:model-value="setAmountPart(reply, 0, $event)"
                  />
                </ElFormItem>
                <ElFormItem label="大额回复">
                  <ElInput
                    :model-value="amountPart(reply, 1)"
                    @update:model-value="setAmountPart(reply, 1, $event)"
                  />
                </ElFormItem>
                <ElFormItem label="小额回复（一行一个）" class="span-2">
                  <ElInput
                    type="textarea"
                    :rows="3"
                    :model-value="amountLowLines(reply)"
                    @update:model-value="setAmountLowLines(reply, $event)"
                  />
                </ElFormItem>
                <div class="delay-matrix span-2">
                  <label>大额回复延迟</label>
                  <ElInputNumber v-model="reply.high_reply_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="reply.high_reply_max" :controls="false" :min="0" />
                  <label>小额首回延迟</label>
                  <ElInputNumber v-model="reply.low_first_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="reply.low_first_max" :controls="false" :min="0" />
                  <label>小额转发延迟</label>
                  <ElInputNumber v-model="reply.low_forward_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="reply.low_forward_max" :controls="false" :min="0" />
                  <label>小额回原消息延迟</label>
                  <ElInputNumber v-model="reply.low_reply_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="reply.low_reply_max" :controls="false" :min="0" />
                </div>
              </div>

              <div v-else-if="reply.type === 'backend_unlock' || reply.type === 'agent_orchestrator'" class="step-grid">
                <ElFormItem label="后台动作">
                  <ElSelect v-model="reply.backend_action">
                    <ElOption
                      v-for="item in backendActions"
                      :key="item.value"
                      :label="item.label"
                      :value="item.value"
                    />
                  </ElSelect>
                </ElFormItem>
                <ElFormItem label="发送账号">
                  <ElSelect v-model="reply.telegram_account" placeholder="主账号（默认）" clearable>
                    <ElOption label="主账号（默认）" value="" />
                    <ElOption
                      v-for="account in accountOptions"
                      :key="account"
                      :label="account"
                      :value="account"
                    />
                  </ElSelect>
                </ElFormItem>
                <ElFormItem label="提取账号正则">
                  <ElInput v-model="reply.member_pattern" placeholder="留空自动识别" />
                </ElFormItem>
                <ElFormItem label="提取 IP 正则">
                  <ElInput v-model="reply.ip_pattern" placeholder="可留空" />
                </ElFormItem>
                <ElFormItem label="转发/目标群">
                  <ElInput v-model="reply.forward_to" placeholder="-1001234567890 或 @username" />
                </ElFormItem>
                <ElFormItem label="TG 消息模板">
                  <ElInput v-model="reply.text" type="textarea" :rows="2" placeholder="可留空使用默认模板" />
                </ElFormItem>
                <ElFormItem label="失败通知对象">
                  <ElInput v-model="reply.fail_notify_to" placeholder="用户 ID 或 @username" />
                </ElFormItem>
                <ElFormItem label="失败通知内容">
                  <ElInput v-model="reply.fail_notify_text" type="textarea" :rows="2" />
                </ElFormItem>
              </div>

                <div v-else class="step-grid">
                  <ElFormItem label="内容" class="span-2">
                    <ElInput v-model="reply.text" type="textarea" :rows="3" />
                  </ElFormItem>
                </div>
              </div>
            </div>
          </ElTabPane>

          <ElTabPane label="同意后流程" name="approval">
            <div class="section-head compact-head">
              <div>
                <div class="section-title">同意后流程</div>
                <div class="section-desc">领导引用报备并发送同意词后执行。</div>
              </div>
              <ElSwitch v-model="selected.enable_approval" @change="ensureApprovalAction(selected)" />
            </div>
            <template v-if="selected.enable_approval">
              <ElDivider />
              <div class="approval-flow">
                <ElFormItem label="同意后回复领导引用消息">
                  <ElInput v-model="selected.approval_action.reply_admin" type="textarea" :rows="2" />
                </ElFormItem>
                <ElFormItem label="同意后转发原始报备到群">
                  <ElInput v-model="selected.approval_action.forward_to" placeholder="-1001234567890" />
                </ElFormItem>
                <ElFormItem label="同意后回复原始报备">
                  <ElInput v-model="selected.approval_action.reply_origin" type="textarea" :rows="2" />
                </ElFormItem>
                <div class="approval-delays">
                  <label>先回领导延迟</label>
                  <ElInputNumber v-model="selected.approval_action.delay_1_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="selected.approval_action.delay_1_max" :controls="false" :min="0" />
                  <label>转发延迟</label>
                  <ElInputNumber v-model="selected.approval_action.delay_2_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="selected.approval_action.delay_2_max" :controls="false" :min="0" />
                  <label>回原始报备延迟</label>
                  <ElInputNumber v-model="selected.approval_action.delay_3_min" :controls="false" :min="0" />
                  <span>-</span>
                  <ElInputNumber v-model="selected.approval_action.delay_3_max" :controls="false" :min="0" />
                </div>
              </div>
            </template>
            <ElEmpty v-else description="同意后流程未开启" />
          </ElTabPane>
        </ElTabs>
      </div>
    </ElCard>
  </div>
</template>

<style scoped>
.rules-layout {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 12px;
}
.card-header,
.rule-summary,
.section-head,
.step-head,
.account-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.rule-summary {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.rules-list,
.rule-editor {
  min-height: calc(100vh - 126px);
}
.editor-body {
  display: grid;
  gap: 12px;
}
.rule-tabs {
  min-height: calc(100vh - 196px);
}
:deep(.rule-tabs > .el-tabs__content) {
  padding: 12px;
}
.editor-section {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 14px;
  background: var(--el-bg-color);
}
.section-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}
.section-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.editor-grid,
.step-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 12px;
}
.span-2 {
  grid-column: 1 / -1;
}
.account-row {
  justify-content: flex-start;
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}
.account-row span {
  width: 92px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.account-row .el-select {
  width: 260px;
}
.steps {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.step-panel {
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-extra-light);
  cursor: grab;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.step-panel:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 2px 8px rgb(0 0 0 / 5%);
}
.step-panel:active {
  cursor: grabbing;
}
.step-head {
  margin-bottom: 10px;
}
.step-index {
  width: 34px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
}
.step-order {
  display: flex;
  align-items: center;
  gap: 2px;
}
.step-type {
  width: 180px;
}
.delay-inputs {
  display: grid;
  grid-template-columns: 76px 12px 76px 24px;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.delay-matrix,
.approval-delays {
  display: grid;
  grid-template-columns: 120px 86px 12px 86px;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-regular);
}
.approval-flow {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 12px;
}
.approval-delays {
  grid-column: 1 / -1;
}
:deep(.el-form-item) {
  margin-bottom: 12px;
}
:deep(.el-input-number) {
  width: 100%;
}
</style>
