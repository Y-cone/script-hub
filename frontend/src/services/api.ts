import axios from 'axios'

const api = axios.create()

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
  created_at: string
  updated_at: string
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

export const getScriptTags = (id: number) =>
  api.get<string[]>(`/api/scripts/${id}/tags`)

export const setScriptTags = (id: number, tagIds: number[]) =>
  api.put(`/api/scripts/${id}/tags`, { tag_ids: tagIds })

export const getScriptContent = (id: number) =>
  api.get<{ content: string; language: string }>(`/api/scripts/${id}/content`)

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
}) => api.get<RunHistoryListResponse>('/api/run/history', { params })

export const getRunDetail = (id: number) =>
  api.get<RunHistoryItem>(`/api/run/${id}`)

export const killRun = (id: number) =>
  api.post(`/api/run/${id}/kill`)
