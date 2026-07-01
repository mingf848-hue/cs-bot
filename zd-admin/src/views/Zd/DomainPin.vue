<script setup lang="ts">
import { computed, onMounted, reactive, watch } from 'vue'
import {
  ElButton,
  ElCard,
  ElCol,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElRow,
  ElSelect,
  ElTag
} from 'element-plus'
import { loadMonitorAccountGroups, runDomainPinUpdate } from '@/api/zd'
import { useZd } from './useZd'

const { state, ensureLoaded } = useZd()

defineOptions({ name: 'ZdDomainPin' })

type GroupOption = {
  id: string
  name: string
}

const domainPin = reactive({
  source_account: 'ara账号',
  target_account: '组长值班',
  source_chat_ids: ['-1002819832851', '-1002560892878'] as string[],
  history_limit: 80,
  dry_run: true,
  running: false,
  groupLoading: false,
  result: '',
  preview: ''
})

const groupOptionsByAccount = reactive<Record<string, GroupOption[]>>({})
const sourceChatsStorageKey = (account: string) => `zd_domain_pin_source_chats:${account || 'default'}`

const loadSavedSourceChats = (account: string) => {
  try {
    const raw = window.localStorage.getItem(sourceChatsStorageKey(account))
    const parsed = raw ? JSON.parse(raw) : null
    return Array.isArray(parsed) ? parsed.map(normalizeGroupId).filter(Boolean) : []
  } catch {
    return []
  }
}

const saveSourceChats = (account: string, chats: string[]) => {
  try {
    window.localStorage.setItem(
      sourceChatsStorageKey(account),
      JSON.stringify((chats || []).map(normalizeGroupId).filter(Boolean))
    )
  } catch {
    // localStorage may be unavailable in private contexts.
  }
}

const accountOptions = computed(() => {
  const names = new Set<string>()
  const main = state.runtime.main_account || '主账号'
  names.add(main)
  ;(state.accounts || []).forEach((name: string) => name && names.add(name))
  ;(state.config.available_accounts || []).forEach((name: string) => name && names.add(name))
  return Array.from(names)
})

const sourceGroupOptions = computed(() => groupOptionsByAccount[domainPin.source_account] || [])

const normalizeGroupId = (value: any) => String(value ?? '').trim()

const loadGroupsForSourceAccount = async () => {
  const account = domainPin.source_account
  if (!account || groupOptionsByAccount[account]) return
  domainPin.groupLoading = true
  try {
    const data = await loadMonitorAccountGroups(account)
    groupOptionsByAccount[account] = (data.groups || []).map((group: any) => ({
      id: normalizeGroupId(group.id),
      name: String(group.name || group.title || group.id || '').trim()
    }))
  } catch (error: any) {
    ElMessage.error(error?.message || '读取来源群失败')
    groupOptionsByAccount[account] = []
  } finally {
    domainPin.groupLoading = false
  }
}

const updateDomainPin = async (dryRun: boolean) => {
  domainPin.running = true
  domainPin.result = dryRun ? '正在预览...' : '正在更新...'
  if (dryRun) domainPin.preview = '正在生成处理后的消息...'
  try {
    const data = await runDomainPinUpdate({
      ...domainPin,
      source_chat_ids: domainPin.source_chat_ids.join('\n'),
      dry_run: dryRun
    })
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
        const pinned = item.pinned_message_id ? ` #${item.pinned_message_id}` : ''
        return `${chat} ${label}${pinned}${item.error ? `：${item.error}` : ''}`
      })
    ].join('\n')
    domainPin.preview =
      data.texts?.combined_with_footer || data.texts?.combined || data.texts?.jiangnan_with_footer || ''
    ElMessage[data.success ? 'success' : 'error'](data.msg || (dryRun ? '预览完成' : '更新完成'))
  } catch (error: any) {
    domainPin.result = error?.message || '请求失败'
    if (dryRun) domainPin.preview = ''
    ElMessage.error(error?.message || '请求失败')
  } finally {
    domainPin.running = false
  }
}

onMounted(async () => {
  await ensureLoaded()
  if (!accountOptions.value.includes(domainPin.source_account) && accountOptions.value.length) {
    domainPin.source_account = accountOptions.value[0]
  }
  if (!accountOptions.value.includes(domainPin.target_account) && accountOptions.value.length) {
    domainPin.target_account = accountOptions.value[0]
  }
  const savedChats = loadSavedSourceChats(domainPin.source_account)
  if (savedChats.length) domainPin.source_chat_ids = savedChats
  await loadGroupsForSourceAccount()
})

watch(
  () => domainPin.source_account,
  async () => {
    domainPin.source_chat_ids = loadSavedSourceChats(domainPin.source_account)
    await loadGroupsForSourceAccount()
  }
)

watch(
  () => [...domainPin.source_chat_ids],
  (chats) => saveSourceChats(domainPin.source_account, chats)
)
</script>

<template>
  <div v-loading="state.loading" class="domain-pin-page">
    <div class="page-head">
      <div>
        <h2>域名更新置顶</h2>
        <p>从指定账号的来源群读取域名消息，转换格式后编辑目标群置顶。</p>
      </div>
      <div class="head-actions">
        <ElButton :loading="domainPin.running" @click="updateDomainPin(true)">预览</ElButton>
        <ElButton type="primary" :loading="domainPin.running" @click="updateDomainPin(false)">
          执行更新
        </ElButton>
      </div>
    </div>

    <div class="domain-workspace">
      <div class="left-stack">
        <ElCard shadow="never" class="tool-card">
          <ElForm label-position="top">
            <ElRow :gutter="16">
              <ElCol :span="8">
                <ElFormItem label="读取账号">
                  <ElSelect v-model="domainPin.source_account" filterable>
                    <ElOption
                      v-for="account in accountOptions"
                      :key="`source-${account}`"
                      :label="account"
                      :value="account"
                    />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol :span="8">
                <ElFormItem label="编辑账号">
                  <ElSelect v-model="domainPin.target_account" filterable>
                    <ElOption
                      v-for="account in accountOptions"
                      :key="`target-${account}`"
                      :label="account"
                      :value="account"
                    />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol :span="8">
                <ElFormItem label="读取条数">
                  <ElInputNumber v-model="domainPin.history_limit" :min="10" :max="300" />
                </ElFormItem>
              </ElCol>
              <ElCol :span="24">
                <ElFormItem label="来源群">
                  <ElSelect
                    v-model="domainPin.source_chat_ids"
                    multiple
                    filterable
                    collapse-tags
                    collapse-tags-tooltip
                    :max-collapse-tags="5"
                    :loading="domainPin.groupLoading"
                    placeholder="从读取账号已加入的群中选择"
                  >
                    <ElOption
                      v-for="group in sourceGroupOptions"
                      :key="group.id"
                      :label="`${group.name}（${group.id}）`"
                      :value="group.id"
                    />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol :span="24">
                <div class="selected-groups">
                  <ElTag v-for="groupId in domainPin.source_chat_ids" :key="groupId" size="small">
                    {{ groupId }}
                  </ElTag>
                </div>
              </ElCol>
            </ElRow>
          </ElForm>
        </ElCard>

        <ElCard shadow="never" class="result-card">
          <template #header>目标群结果</template>
          <ElInput
            v-model="domainPin.result"
            type="textarea"
            :rows="14"
            readonly
            placeholder="预览或执行后显示每个目标群结果"
          />
        </ElCard>
      </div>

      <ElCard shadow="never" class="result-card preview-card">
        <template #header>处理后消息</template>
        <ElInput
          v-model="domainPin.preview"
          type="textarea"
          :rows="25"
          readonly
          placeholder="点击预览后，这里显示将写入置顶的完整消息"
        />
      </ElCard>
    </div>
  </div>
</template>

<style scoped>
.domain-pin-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
  min-width: 0;
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  padding: 4px 2px;
}

.page-head h2 {
  margin: 0;
  color: #111827;
  font-size: 20px;
  font-weight: 700;
}

.page-head p {
  margin: 6px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tool-card :deep(.el-select),
.tool-card :deep(.el-input-number) {
  width: 100%;
}

.selected-groups {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 24px;
}

.domain-workspace {
  display: grid;
  grid-template-columns: 760px minmax(420px, 1fr);
  gap: 14px;
  align-items: start;
  min-width: 0;
}

.left-stack {
  display: grid;
  gap: 14px;
  min-width: 0;
}

.result-card :deep(textarea) {
  font-family:
    ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New',
    monospace;
  line-height: 1.55;
}

.preview-card {
  position: sticky;
  top: 0;
}
</style>
