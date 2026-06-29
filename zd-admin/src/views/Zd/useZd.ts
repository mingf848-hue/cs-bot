import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { loadRuntimeStats, loadZdConfig, saveZdConfig } from '@/api/zd'

const defaultConfig = () => ({
  enabled: false,
  extra_enabled: true,
  account_monitor_enabled: {},
  approval_keywords: ['同意', '批准', 'ok'],
  schedule: { active: false, start: '09:00', end: '21:00' },
  resources: { groups: [], sender_prefixes: [] },
  ai_private_reply: { accounts: {} },
  rules: [],
  ticket_follow_tasks: [],
  scheduled_backend_actions: [],
  scheduled_messages: [],
  available_accounts: []
})

const defaultRuntime = () => ({
  main_account: '',
  server_time: '',
  timezone: '北京时间',
  today: { total: 0, success: 0, failed: 0, skipped: 0, action_count: 0, success_rate: 0 },
  yesterday: { total: 0, success: 0, failed: 0, skipped: 0, action_count: 0, success_rate: 0 },
  by_rule: [],
  recent: [],
  accounts: [],
  hourly: []
})

const state = reactive({
  config: defaultConfig() as any,
  runtime: defaultRuntime() as any,
  accounts: [] as string[],
  loading: false,
  saving: false,
  lastLoadedAt: ''
})

const loaded = ref(false)

const normalizeConfig = (data: any) => {
  const next = { ...defaultConfig(), ...(data || {}) }
  next.resources = {
    groups: Array.isArray(next.resources?.groups) ? next.resources.groups : [],
    sender_prefixes: Array.isArray(next.resources?.sender_prefixes)
      ? next.resources.sender_prefixes
      : []
  }
  next.schedule = { active: false, start: '09:00', end: '21:00', ...(next.schedule || {}) }
  next.rules = Array.isArray(next.rules) ? next.rules : []
  next.ticket_follow_tasks = Array.isArray(next.ticket_follow_tasks) ? next.ticket_follow_tasks : []
  next.scheduled_backend_actions = Array.isArray(next.scheduled_backend_actions)
    ? next.scheduled_backend_actions
    : []
  next.scheduled_messages = Array.isArray(next.scheduled_messages) ? next.scheduled_messages : []
  next.available_accounts = Array.isArray(next.available_accounts) ? next.available_accounts : []
  next.ai_private_reply = next.ai_private_reply || { accounts: {} }
  next.ai_private_reply.accounts = next.ai_private_reply.accounts || {}
  return next
}

const applyConfig = (data: any) => {
  Object.assign(state.config, normalizeConfig(data))
}

const applyRuntime = (data: any) => {
  Object.assign(state.runtime, { ...defaultRuntime(), ...(data || {}) })
  const main = state.runtime.main_account || '主账号'
  const extras = (state.runtime.accounts || [])
    .map((item: any) => String(item?.name || '').trim())
    .filter((name: string) => name && name !== main)
  state.accounts = [main, ...extras]
}

export const useZd = () => {
  const refresh = async () => {
    state.loading = true
    try {
      const [config, runtime] = await Promise.all([loadZdConfig(), loadRuntimeStats()])
      applyConfig(config)
      applyRuntime(runtime)
      state.lastLoadedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      loaded.value = true
    } finally {
      state.loading = false
    }
  }

  const refreshRuntime = async () => {
    const runtime = await loadRuntimeStats()
    applyRuntime(runtime)
    state.lastLoadedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }

  const save = async () => {
    state.saving = true
    try {
      const data = await saveZdConfig(state.config)
      if (data.config) applyConfig(data.config)
    } finally {
      state.saving = false
    }
  }

  const ensureLoaded = async () => {
    if (!loaded.value) {
      try {
        await refresh()
      } catch (error: any) {
        ElMessage.error(error?.message || '加载失败')
      }
    }
  }

  const ruleStats = computed(() => {
    const total = state.config.rules.length
    const running = state.config.rules.filter((rule: any) => rule.enabled !== false).length
    return {
      total,
      running,
      disabled: total - running,
      draft: state.config.rules.filter((rule: any) => !rule.groups?.length).length
    }
  })

  return {
    state,
    ruleStats,
    refresh,
    refreshRuntime,
    ensureLoaded,
    save
  }
}
