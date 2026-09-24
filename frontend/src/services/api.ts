import axios from 'axios'
import { apiBase } from '../config'

const api = axios.create({ baseURL: apiBase })

/** 批次 AE：等后端可用（桌面壳冷启动竞态）。
 *  壳是先建窗口/加载页面、sidecar 同时在起（main.rs setup 里 spawn 完就走），而 PyInstaller onefile
 *  解包到 uvicorn 真正开始监听实测约 1.1s。这期间页面 mount 发出的首批请求（脚本列表 / 设备 / 历史）
 *  直接 ECONNREFUSED，而调用处清一色 `.catch(() => {})` 静默吞掉且不再重试 →
 *  「每次重开应用脚本库是空的，得手动点一次扫描」「设备下拉只有本机，进设备页才出现」。
 *  幂等、可重复调用；超时也 resolve（调用方按旧行为渲染，不把首屏永久卡住）。 */
export async function waitForApi(timeoutMs = 15000, intervalMs = 250): Promise<void> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    try {
      await api.get('/api/health', { timeout: 3000 })
      return
    } catch {
      if (Date.now() >= deadline) return
      await new Promise((r) => setTimeout(r, intervalMs))
    }
  }
}

export interface ScriptItem {
  id: number
  name: string
  path: string
  relative_path: string
  extension: string
  category: string
  description: string
  parameters: string
  working_dir: string | null
  env_vars: string | null
  dangerous: boolean
  timeout: number
  source: string
  tags: string[]
  env_requests?: string | null
  dependencies?: string | null
  available?: boolean | null
  created_at: string
  updated_at: string
  last_run?: LastRunBrief | null // V5-G SPEC §7.2-#1
}

export interface LastRunBrief {
  status: string
  exit_code: number | null
  started_at: string | null
  duration: number | null
}

export interface ScriptListResponse {
  items: ScriptItem[]
  total: number
  page: number
  page_size: number
}

export interface ScanResult {
  added: number
  updated: number
  removed: number
  total: number
}

export const getScripts = (params?: {
  page?: number
  page_size?: number
  search?: string
  directory?: string
  category?: string
  tag_ids?: string
}) => api.get<ScriptListResponse>('/api/scripts', { params })

export const getScript = (id: number) => api.get<ScriptItem>(`/api/scripts/${id}`)

export const updateScript = (id: number, data: Partial<ScriptItem>) =>
  api.put<ScriptItem>(`/api/scripts/${id}`, data)

export const moveScript = (id: number, directory: string) =>
  api.post<ScriptItem>(`/api/scripts/${id}/move`, { directory })

export const deleteScript = (id: number) =>
  api.delete(`/api/scripts/${id}`)

export const getScriptDirs = () =>
  api.get<{ directories: string[] }>('/api/scripts/dirs')

export const scanScripts = () => api.post<ScanResult>('/api/scripts/scan')

export const uploadScript = (file: File, subdir = '') => {
  const form = new FormData()
  form.append('file', file)
  return api.post<ScanResult>('/api/scripts/upload', form, {
    params: subdir ? { subdir } : {},
  })
}

// 标签 API
export interface TagItem {
  id: number
  name: string
  color: string
  created_at: string
}

export const getTags = (search = '') =>
  api.get<{ items: TagItem[]; total: number }>('/api/tags', { params: { search } })

export const createTag = (data: { name: string; color?: string }) =>
  api.post<TagItem>('/api/tags', data)

export const updateTag = (id: number, data: { name?: string; color?: string }) =>
  api.put<TagItem>(`/api/tags/${id}`, data)

export const deleteTag = (id: number) => api.delete(`/api/tags/${id}`)

export const setScriptTags = (id: number, tagIds: number[]) =>
  api.put(`/api/scripts/${id}/tags`, { tag_ids: tagIds })

export const getScriptContent = (id: number) =>
  api.get<{ content: string; language: string }>(`/api/scripts/${id}/content`)

export const saveScriptContent = (id: number, content: string) =>
  api.put<{ message: string; language: string }>(`/api/scripts/${id}/content`, { content })

// 环境检测
export interface EnvCheckItem {
  type: string
  name: string
  required: string
  actual: string | null
  ok: boolean
  detail: string
}
export interface EnvCheckResult {
  script_id: number
  checks: EnvCheckItem[]
  unmet: EnvCheckItem[]
  all_ok: boolean
  /** 批次 AO：判据对象（local = Server 本机；device = device_id 那台设备） */
  source: 'local' | 'device'
  device_id: number | null
  /** 批次 AO：false = **无探测结论**（设备缓存缺失/过期/上次探测失败）→ checks 为空、
   *  一项都没判，绝不等于「不通过」。别把 all_ok=false 与它搞混：那个是「有结论但有未达标项」。 */
  conclusive: boolean
  /** conclusive=false 时的原因与下一步指引（可直接展示） */
  note: string | null
}
/** 批次 AO②：`deviceId` = 判定对象。传当前设备 → 按**目标设备**的 probe 缓存判平台/运行时；
 *  不传 → Server 本机（旧行为）。 */
export const envCheckScript = (id: number, deviceId?: number | null) =>
  api.get<EnvCheckResult>(`/api/scripts/${id}/env-check`,
    { params: deviceId ? { device_id: deviceId } : {} })

export interface ParamDef {
  name: string
  type: string
  default: string | null
  required: boolean
  description: string | null
  choices: string[] | null
}

export const parseScriptParams = (id: number) =>
  api.post<{ id: number; parameters: ParamDef[] }>(`/api/scripts/${id}/parse`)

// 运行相关API
export interface RunRequest {
  script_id: number
  parameters?: Record<string, any>
  working_dir?: string
  env_vars?: Record<string, string>
  timeout?: number
  confirm_dangerous?: boolean
  confirm_env?: boolean
  device_id?: number
  // V5-G（SPEC §2.4）：解释器覆盖（后端白名单校验；None=按脚本类型自动）
  shell?: 'bash' | 'sh' | 'zsh' | 'cmd' | 'powershell' | 'pwsh' | 'python3' | null
}

export interface RunResponse {
  id: number
  status: string
  command: string
  message: string
}

export interface RunHistoryItem {
  id: number
  script_id: number
  parameters: string
  command: string
  output: string
  output_file: string | null
  exit_code: number | null
  status: string
  duration: number | null
  started_at: string
  finished_at: string | null
  is_scheduled: number | null
  schedule_id: number | null
  device_id?: number | null
  script_name?: string | null // V5-G SPEC §7.2-#4
  device_name?: string | null
}

export interface RunHistoryListResponse {
  items: RunHistoryItem[]
  total: number
  page: number
  page_size: number
}

export const runScript = (data: RunRequest) =>
  api.post<RunResponse>('/api/run', data)

export const getRunHistory = (params?: {
  page?: number
  page_size?: number
  script_id?: number
  schedule_id?: number
  device_id?: number
  from_time?: string // V5-G SPEC §7.3：时间窗（周历按周取）
  to_time?: string
  preview?: boolean  // true 时 output 截断 2KB
}) => api.get<RunHistoryListResponse>('/api/run/history', { params })

export const killRun = (id: number) =>
  api.post(`/api/run/${id}/kill`)

// 系统信息
export interface RuntimeItem {
  name: string
  installed: boolean
  version: string | null
}
export interface SystemInfo {
  hostname: string
  os: string
  os_version: string
  arch: string
  platform: string
  ip: string
  runtimes: RuntimeItem[]
  data_dir?: string   // V5-G SPEC §7.2-#5
  scripts_root?: string
  probed_at?: string
  readonly?: { data_dir: boolean; scripts_root: boolean }  // v2.14：置灰唯一依据
}
export const getSystemInfo = () => api.get<SystemInfo>('/api/system/info')
export const probeSystem = () => api.post<SystemInfo>('/api/system/probe')

// 设置面板（SPEC §2.9 v2.14）
export interface SettingsValidateResult {
  data_dir?: { exists: boolean; writable: boolean; empty: boolean; error: string | null; size_mb?: number }
  scripts_root?: { exists: boolean; writable: boolean; empty: boolean; error: string | null; script_count?: number; dir_count?: number }
}
export interface SettingsPutResult {
  ok: boolean
  data_dir: string
  scripts_root: string
  restart_required: boolean
  migrated?: boolean
  warnings: string[]
}
export const validateSettings = (body: { data_dir?: string; scripts_root?: string }) =>
  api.post<SettingsValidateResult>('/api/settings/validate', body)
export const putSettings = (body: { data_dir?: string; scripts_root?: string; migrate?: boolean }) =>
  api.put<SettingsPutResult>('/api/settings', body)

// 依赖分析
export interface DepItem {
  name: string
  constraint: string
  type: string
  installed: boolean
  installed_version: string | null
}
export const getScriptDeps = (id: number) =>
  api.get<{ script_id: number; deps: DepItem[]; dep_file: boolean }>(`/api/scripts/${id}/deps`)
export const getScriptDepCandidates = (id: number) =>
  api.get<{ files: string[] }>(`/api/scripts/${id}/dep-candidates`)

// 导入导出
export const exportScript = (id: number) =>
  api.get(`/api/scripts/${id}/export`, { responseType: 'blob' })
export const importScript = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post<{ message: string; imported: string[]; scan: Record<string, number> }>('/api/scripts/import', fd)
}

// 远程设备
export interface DeviceItem {
  id: number
  name: string
  type: string
  host: string
  port: number
  auth_type: string
  username: string
  online?: boolean | null    // V5-G SPEC §7.2-#2（未探测=null）
  latency_ms?: number | null
  os_info?: string | null
  last_error?: string | null
  /** 批次 AK①：最近一次 test/probe 的运行时缓存（同 probeDevice 的 runtimes 结构）。
   *  null/缺失 = 从未探测过或探过没拿到条目 → **无结论**，不等于「没装」；installed=false 才是「确定没装」。 */
  runtimes?: RuntimeItem[] | null
  /** 批次 AO：最近一次探测到的平台原文（win32/unix/windows；null/缺失 = 未探测或已过期）。
   *  与 os_info/runtimes 同一份 probe 缓存条目 —— 本机信息页有它就说明「后端有新鲜结论」，不必重探。 */
  platform?: string | null
  /** 批次 AQ：这份缓存结论的**年龄**（毫秒；null/缺失 = 当前没有新鲜结论）。
   *  后端只给年龄不给时间点（缓存 ts 是 time.monotonic）—— 展示用「N 分钟前」，别当时间戳用。 */
  probed_age_ms?: number | null
  schedule_count?: number
  delegated_count?: number
}
export const getDevices = () => api.get<DeviceItem[]>('/api/devices')
// 批次 M-1：设备名单变更后通知所有消费方（顶栏胶囊 / 全局终端底栏 / 设备上下文映射）立即刷新。
// 放 API 层是唯一公共出口——设备页与顶栏胶囊 / 全局终端底栏 / 设备上下文映射都走这些接口，不必在每个按钮上各写一遍。
// 放在 .then 里：只有请求成功（名单真变了）才派发；失败不改名单，不惊动监听方。
const deviceChanged = () => {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('devices-changed'))
}
export const createDevice = (data: Record<string, unknown>) =>
  api.post<DeviceItem>('/api/devices', data).then((r) => { deviceChanged(); return r })
export const updateDevice = (id: number, data: Record<string, unknown>) =>
  api.put<DeviceItem>(`/api/devices/${id}`, data).then((r) => { deviceChanged(); return r })
export const deleteDevice = (id: number) =>
  api.delete(`/api/devices/${id}`).then((r) => { deviceChanged(); return r })
// 测试连接会回写 online/latency/os_info（顶栏胶囊状态点用 online）→ 同一出口一并通知
export const testDevice = (id: number) =>
  api.post<{ ok: boolean; message: string; platform?: string; os_info?: string; latency_ms?: number | null }>(`/api/devices/${id}/test`)
    .then((r) => { deviceChanged(); return r })
export const probeDevice = (id: number) => api.get<{ name: string; type: string; host: string; platform: string; os: string; port: number; runtimes: RuntimeItem[] }>(`/api/devices/${id}/probe`)

// 批次 AM：**探测去重** —— 同一台设备在飞行中只发一次请求。
// 同一刻有两个调用方会要同一台设备的结论：切设备/进入应用的自动重探（stores/deviceContext）
// 与「本机信息」页自己的重探（DesktopSystemInfo）。不去重就是每次切设备探两遍（双倍 SSH 握手）。
// 只做 in-flight 去重，不做结果缓存：缓存新鲜度由后端的 probe 缓存（TTL）一家说了算。
const _probeInflight = new Map<string, Promise<any>>()
export function probeOnce(id: number): ReturnType<typeof probeDevice>
export function probeOnce(id: null): ReturnType<typeof probeSystem>
export function probeOnce(id: number | null): ReturnType<typeof probeDevice> | ReturnType<typeof probeSystem>
export function probeOnce(id: number | null): Promise<any> {
  const key = id === null ? 'local' : String(id)
  const hit = _probeInflight.get(key)
  if (hit) return hit
  const p = (id === null ? probeSystem() : probeDevice(id))
    .finally(() => { if (_probeInflight.get(key) === p) _probeInflight.delete(key) })
  _probeInflight.set(key, p)
  return p
}

// 调度任务
export interface ScheduleItem {
  id: number
  script_id: number
  script_name: string | null
  script_path: string | null
  device_id: number | null
  device_name: string | null
  name: string
  cron_expr: string | null
  interval_seconds: number | null
  enabled: boolean
  parameters: string
  env_vars: string | null
  working_dir: string | null
  timeout: number
  exec_location: 'local' | 'device'
  next_run_at?: string | null // V5-G SPEC §7.2-#3
  last_run?: LastRunBrief | null
  delegated?: boolean | null
  device_type?: string | null
  created_at: string
  updated_at: string
}
export const getSchedules = () => api.get<{ items: ScheduleItem[]; total: number }>('/api/schedules')
export const createSchedule = (data: Partial<ScheduleItem>) => api.post<ScheduleItem>('/api/schedules', data)
export const updateSchedule = (id: number, data: Partial<ScheduleItem>) => api.put<ScheduleItem>(`/api/schedules/${id}`, data)
export const deleteSchedule = (id: number) => api.delete(`/api/schedules/${id}`)
export const runScheduleNow = (id: number) => api.post(`/api/schedules/${id}/run`)

// V5-G SPEC §7.1：新建任务 Modal「下次触发」实时预览
export const previewSchedule = (data: { cron_expr?: string; interval_seconds?: number }) =>
  api.post<{ valid: boolean; next_runs: string[]; error?: string }>('/api/schedules/preview', data)
