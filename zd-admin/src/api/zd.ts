import { ElMessage } from 'element-plus'

export interface ZdResponse<T = any> {
  success?: boolean
  msg?: string
  login_required?: boolean
  [key: string]: any
}

const ensureLogin = (data: ZdResponse) => {
  if (data?.login_required) {
    window.location.hash = '#/login'
    throw new Error(data.msg || '请先登录')
  }
}

export const zdFetch = async <T = any>(url: string, options: RequestInit = {}): Promise<T> => {
  const headers = new Headers(options.headers || {})
  if (options.body && !headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers
  })
  const contentType = res.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await res.json() : await res.text()
  if (!res.ok) {
    if (typeof data === 'object') ensureLogin(data)
    throw new Error((typeof data === 'object' && data?.msg) || res.statusText)
  }
  if (typeof data === 'object') ensureLogin(data)
  return data as T
}

export const loadZdConfig = () => zdFetch<any>('/tool/monitor_settings_json')

export const loadRuntimeStats = (limit = 240) =>
  zdFetch<any>(`/api/monitor_runtime_stats?limit=${encodeURIComponent(String(limit))}`)

export const saveZdConfig = async (payload: any) => {
  const data = await zdFetch<ZdResponse>('/api/monitor_settings?return_config=1', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
  if (!data.success) throw new Error(data.msg || '保存失败')
  ElMessage.success(data.msg || '保存成功')
  return data
}

export const toggleMonitor = (payload: any) =>
  zdFetch<ZdResponse>('/api/monitor_toggle', {
    method: 'POST',
    body: JSON.stringify(payload)
  })

export const runDomainPinUpdate = (payload: any) =>
  zdFetch<ZdResponse>('/api/domain_pin_update', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
