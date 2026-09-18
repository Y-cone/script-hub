/**
 * 前端运行配置（PRD-V5 4.B：API 寻址定案——运行时注入优先）。
 *
 * 解析顺序：
 * 1. window.__SCRIPTHUB_API__   —— Tauri 壳在页面加载前注入（动态端口，构建时未知）
 * 2. VITE_API_BASE 构建期环境变量 —— Web 高级部署场景
 * 3. 空（相对路径）             —— Web dev（vite proxy）/ 同源部署
 *
 * Tauri 环境检测：window.__SCRIPTHUB_API__ 存在即视为桌面壳（4.K 桌面令牌层也用此标记）。
 */
declare global {
  interface Window {
    __SCRIPTHUB_API__?: string
  }
}

export const isTauri = (): boolean => typeof window !== 'undefined' && !!window.__SCRIPTHUB_API__

function resolveApiBase(): string {
  const injected = (typeof window !== 'undefined' && window.__SCRIPTHUB_API__) || ''
  if (injected) return injected.replace(/\/+$/, '')
  return (import.meta.env.VITE_API_BASE ?? '').trim()
}

const _apiBase = resolveApiBase()

// Tauri 壳下注入脚本理论上先于页面执行；若时序异常（首帧缺失），
// 监听壳的 api-ready 事件兜底刷新（PRD 4.B）。
if (typeof window !== 'undefined' && (window as any).__TAURI_INTERNALS__ && !_apiBase) {
  window.addEventListener('DOMContentLoaded', () => {
    import('@tauri-apps/api/event').then(({ listen }) =>
      listen<string>('scripthub://api-ready', (ev) => {
        ;(window as any).__SCRIPTHUB_API__ = ev.payload
        window.location.reload() // 简单可靠：带地址整页重载一次
      })
    )
  })
}

/** API 基础地址（axios baseURL / 下载前缀用）；空 = 相对路径 */
export const apiBase = _apiBase

/** WebSocket 基础地址：由 API_BASE 推导（http→ws, https→wss）；未配置则用当前页面 host */
export function getWsBase(): string {
  if (_apiBase) {
    const url = new URL(_apiBase, window.location.origin)
    return `${url.protocol === 'https:' ? 'wss' : 'ws'}://${url.host}`
  }
  return `ws://${window.location.host}`
}

/** 绝对下载 URL（下载标签需完整地址） */
export function getDownloadUrl(path: string): string {
  return _apiBase ? `${_apiBase}${path}` : path
}
