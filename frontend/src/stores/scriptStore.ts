import { create } from 'zustand'
import {
  getScripts,
  getScript,
  scanScripts,
  getScriptContent,
  parseScriptParams,
  updateScript,
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
  setSearch: (s: string) => void
  setDirectory: (d: string) => void
  setCategory: (c: string) => void
  fetchScripts: () => Promise<void>
  fetchScript: (id: number) => Promise<void>
  fetchContent: (id: number) => Promise<void>
  doScan: () => Promise<{ added: number; updated: number; removed: number }>
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

  setSearch: (s) => set({ search: s }),
  setDirectory: (d) => set({ directory: d }),
  setCategory: (c) => set({ category: c }),

  fetchScripts: async () => {
    const { page, pageSize, search, directory, category } = get()
    set({ loading: true })
    try {
      const { data } = await getScripts({ page, page_size: pageSize, search, directory, category })
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
