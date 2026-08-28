import { create } from 'zustand'
import {
  getScripts,
  getScript,
  scanScripts,
  getScriptContent,
  parseScriptParams,
  updateScript,
  uploadScript,
} from '../services/api'
import type { ScriptItem, ParamDef } from '../services/api'

interface ScriptState {
  scripts: ScriptItem[]
  total: number
  page: number
  pageSize: number
  currentScript: ScriptItem | null
  scriptContent: string | null
  scriptLanguage: string | null
  loading: boolean
  search: string
  directory: string
  category: string
  selectedTagIds: number[]
  setSearch: (s: string) => void
  setDirectory: (d: string) => void
  setCategory: (c: string) => void
  setSelectedTagIds: (ids: number[]) => void
  setPageSize: (n: number) => void
  fetchScripts: () => Promise<void>
  fetchScript: (id: number) => Promise<void>
  fetchContent: (id: number) => Promise<void>
  doScan: () => Promise<{ added: number; updated: number; removed: number }>
  doUpload: (file: File, subdir?: string) => Promise<void>
  parseParams: (id: number) => Promise<ParamDef[]>
  updateParams: (id: number, params: ParamDef[]) => Promise<void>
}

export const useScriptStore = create<ScriptState>((set, get) => ({
  scripts: [],
  total: 0,
  page: 1,
  pageSize: 20,
  currentScript: null,
  scriptContent: null,
  scriptLanguage: null,
  loading: false,
  search: '',
  directory: '',
  category: '',
  selectedTagIds: [],

  setSearch: (s) => set({ search: s }),
  setDirectory: (d) => set({ directory: d }),
  setCategory: (c) => set({ category: c }),
  setSelectedTagIds: (ids) => set({ selectedTagIds: ids }),
  setPageSize: (n) => set({ pageSize: n }),

  fetchScripts: async () => {
    const { page, pageSize, search, directory, category, selectedTagIds } = get()
    set({ loading: true })
    try {
      const { data } = await getScripts({
        page,
        page_size: pageSize,
        search,
        directory,
        category,
        tag_ids: selectedTagIds.length ? selectedTagIds.join(',') : undefined,
      })
      set({ scripts: data.items, total: data.total })
    } finally {
      set({ loading: false })
    }
  },

  fetchScript: async (id) => {
    const { data } = await getScript(id)
    set({ currentScript: data })
  },

  fetchContent: async (id) => {
    const { data } = await getScriptContent(id)
    set({ scriptContent: data.content, scriptLanguage: data.language })
  },

  doScan: async () => {
    const { data } = await scanScripts()
    await get().fetchScripts()
    return data
  },

  doUpload: async (file, subdir) => {
    await uploadScript(file, subdir)
    await get().fetchScripts()
  },

  parseParams: async (id) => {
    const { data } = await parseScriptParams(id)
    set((state) => ({
      currentScript: state.currentScript
        ? { ...state.currentScript, parameters: JSON.stringify(data.parameters) }
        : null,
    }))
    return data.parameters
  },

  updateParams: async (id, params) => {
    await updateScript(id, { parameters: JSON.stringify(params) })
    set((state) => ({
      currentScript: state.currentScript
        ? { ...state.currentScript, parameters: JSON.stringify(params) }
        : null,
    }))
  },
}))