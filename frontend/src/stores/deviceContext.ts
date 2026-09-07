import { create } from 'zustand'

export interface DeviceContext {
  /** 当前设备 id；null = 本机 */
  currentDeviceId: number | null
  /** 设备 id → 名称映射（选择器展示用） */
  setCurrentDeviceId: (id: number | null) => void
}

const STORAGE_KEY = 'scripthub_current_device'

function load(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === null || raw === '') return null
    const n = Number(raw)
    return Number.isInteger(n) && n > 0 ? n : null
  } catch {
    return null
  }
}

export const useDeviceContext = create<DeviceContext>((set) => ({
  currentDeviceId: load(),
  setCurrentDeviceId: (id) => {
    try {
      if (id === null) localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, String(id))
    } catch {
      // localStorage 不可用时降级为内存态
    }
    set({ currentDeviceId: id })
  },
}))