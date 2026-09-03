/** 前端运行配置（桌面化准备：API 地址可配置） */
export const apiBase = (import.meta.env.VITE_API_BASE ?? '').trim()

/** WebSocket 基础地址：由 API_BASE 推导（http→ws, https→wss）；未配置则用当前页面 host */
export function getWsBase(): string {
  if (apiBase) {
    const url = new URL(apiBase, window.location.origin)
    return `${url.protocol === 'https:' ? 'wss' : 'ws'}://${url.host}`
  }
  return `ws://${window.location.host}`
}

/** 绝对下载 URL（下载标签需完整地址） */
export function getDownloadUrl(path: string): string {
  return apiBase ? `${apiBase}${path}` : path
}