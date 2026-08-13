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
}) => api.get<ScriptListResponse>('/api/scripts', { params })

export const getScript = (id: number) => api.get<ScriptItem>(`/api/scripts/${id}`)

export const updateScript = (id: number, data: Partial<ScriptItem>) =>
  api.put<ScriptItem>(`/api/scripts/${id}`, data)

export const scanScripts = () => api.post<ScanResult>('/api/scripts/scan')

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
