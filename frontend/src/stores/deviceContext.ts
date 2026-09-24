import { create } from 'zustand'
import { getDevices, probeOnce } from '../services/api'
import type { DeviceItem, RuntimeItem } from '../services/api'

export interface DeviceContext {
  /** 当前设备 id；null = 本机 */
  currentDeviceId: number | null
  /** 设备 id → 名称映射（终端标签、选择器等展示用，单一来源） */
  deviceNames: Record<number, string>
  /** 设备 id → 平台族（批次 M-2：脚本可用性按「当前设备平台」判定；未知类型按 unix） */
  devicePlatforms: Record<number, 'windows' | 'unix'>
  /** 批次 AK②：设备 id → 最近一次 test/probe 的运行时缓存（SPEC §7.2-#2，来源 GET /api/devices）。
   *  三态，**别混**：`undefined`（键不存在）= 该设备从未探测过 / 缓存已过期；
   *  `null` = 探测过但没拿到运行时条目；两者都是「**无结论**」，不等于「没装」。
   *  只有数组里某条目的 `installed=false` 才是「确定没装」。据此判 .sh 是否能在 Windows 设备上跑。 */
  deviceRuntimes: Record<number, RuntimeItem[] | null>
  /** 批次 AO：GET /api/devices 的**原文**（本机信息页要按设备上下文复用其中的 probe 缓存结论：
   *  `os_info`/`runtimes`/`platform` 有值 ⇔ 后端存在未过 TTL 的 probe 条目 → 不必重探）。
   *  上面三个映射都由它派生，页面要更多字段（host/type/…）时别再各发一次请求。 */
  devices: DeviceItem[]
  setCurrentDeviceId: (id: number | null) => void
  refreshDevices: () => Promise<void>
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

/** 批次 AM：探测代次 —— 用来丢弃「切设备后迟到的探测结果」（竞态防护，见 reprobe）。 */
let probeSeq = 0

/**
 * 批次 AM：探一次当前设备并让结论跟上（**切设备立即重探**从「本机信息页」提升为全局行为）。
 *
 * 为什么放这里而不是各入口的 onChange：切设备的入口有多个（批次 AN 前 = 顶栏胶囊 + 脚本工作区 +
 * Web 布局；AN 后顶栏胶囊改只读，入口 = 设备页设备卡片 DesktopDevices + 脚本工作区 + Web 布局），
 * 全部经过 setCurrentDeviceId —— 写在 setter 内部只做一次、谁也漏不掉；写在入口就是「每个入口
 * 各写一遍」的下一个 bug。
 *
 * 探测本身在 service 侧落库到 probe 缓存（含 runtimes，脚本库三态判据）→ 探完 refreshDevices()
 * 把新结论拉回本 store，再派发 devices-changed 让顶栏状态点/终端底栏跟着刷新。
 * 失败静默：自动重探不该弹错，用户要看连接结果会去点「测试连接」。
 */
export async function reprobe(id: number | null) {
  const seq = ++probeSeq
  try {
    await probeOnce(id)
  } catch {
    return
  }
  // 期间又切过设备 → 这次结果已过时：别再刷 UI（迟到的 A 不许把视角刷回 A/B 的旧结论）
  if (seq !== probeSeq) return
  await useDeviceContext.getState().refreshDevices()
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('devices-changed'))
}

export const useDeviceContext = create<DeviceContext>((set, get) => ({
  currentDeviceId: load(),
  deviceNames: {},
  devicePlatforms: {},
  deviceRuntimes: {},
  devices: [],
  setCurrentDeviceId: (id) => {
    const prev = get().currentDeviceId
    try {
      if (id === null) localStorage.removeItem(STORAGE_KEY)
      else localStorage.setItem(STORAGE_KEY, String(id))
    } catch {
      // localStorage 不可用时降级为内存态
    }
    set({ currentDeviceId: id })
    // 批次 AM：切设备立即重探（同一台设备重复选中不重探）—— fire-and-forget，不挡 UI
    if (id !== prev) void reprobe(id)
  },
  refreshDevices: async () => {
    try {
      const res = await getDevices()
      const nm: Record<number, string> = {}
      const tp: Record<number, 'windows' | 'unix'> = {}
      const rt: Record<number, RuntimeItem[] | null> = {}
      ;(res.data || []).forEach((d: any) => {
        nm[d.id] = d.name
        // 平台族：type=windows → windows；linux/mac 及其他 → unix（与后端 detect_platform 的 win32/unix 二分类一致）
        tp[d.id] = d.type === 'windows' ? 'windows' : 'unix'
        // 批次 AK②：probe 缓存的 runtimes（null/缺失都表示「无结论」→ 键值统一存 null，消费方只看有/无条目）
        rt[d.id] = d.runtimes ?? null
      })
      set({ devices: res.data || [], deviceNames: nm, devicePlatforms: tp, deviceRuntimes: rt })
    } catch {
      // 拉取失败保留旧映射，不阻断 UI
    }
  },
}))
