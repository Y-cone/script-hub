/**
 * V5-F 桌面交互层（PRD 4.K F2）：原生能力桥。
 *
 * 桌面形态（isTauri()，判据 = !!window.__SCRIPTHUB_API__）走 Tauri 原生 API。
 * 保留的浏览器回退分支是**判据失效时的兜底**（真壳内恒不走、CDP 验前端也不走）——
 * 不是 Web 形态支持：Web 形态已废弃（只保留桌面端）。
 */
import { isTauri } from '../config'

/** 保存二进制文件（导出 zip 等）。返回所选路径；取消返回 null。 */
export async function saveBinaryFile(defaultName: string, bytes: Uint8Array): Promise<string | null> {
  if (isTauri()) {
    const { save } = await import('@tauri-apps/plugin-dialog')
    const path = await save({ defaultPath: defaultName })
    if (!path) return null
    const { writeFile } = await import('@tauri-apps/plugin-fs')
    await writeFile(path, bytes)
    return path
  }
  const blob = new Blob([bytes as BlobPart], { type: 'application/zip' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = defaultName
  a.click()
  URL.revokeObjectURL(a.href)
  return defaultName
}

/** 打开二进制文件 → File 对象（喂给现有上传接口）。取消返回 null。 */
export async function openBinaryFile(): Promise<File | null> {
  if (isTauri()) {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const path = await open({ multiple: false })
    if (!path || Array.isArray(path)) return null
    const { readFile } = await import('@tauri-apps/plugin-fs')
    const bytes = await readFile(path)
    const name = path.split(/[\\/]/).pop() || 'import.zip'
    return new File([bytes as BlobPart], name, { type: 'application/zip' })
  }
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.zip'
    input.onchange = () => resolve(input.files?.[0] ?? null)
    input.click()
  })
}

/** 右键上下文菜单（Web 自绘浮层；Tauri WebView 同样适用——HTML 浮层在两形态一致） */
export async function showContextMenu(
  x: number,
  y: number,
  items: { label: string; onClick: () => void }[]
): Promise<void> {
  const div = document.createElement('div')
  div.style.cssText = `position:fixed;left:${x}px;top:${y}px;z-index:10000;background:var(--card,#fff);border:1px solid var(--border,#d9d9d9);border-radius:6px;box-shadow:0 3px 12px rgba(0,0,0,.15);padding:4px 0;min-width:140px`
  for (const it of items) {
    const item = document.createElement('div')
    item.textContent = it.label
    item.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:13px'
    item.onmouseenter = () => (item.style.background = 'rgba(127,127,127,.15)')
    item.onmouseleave = () => (item.style.background = '')
    item.onclick = () => {
      div.remove()
      it.onClick()
    }
    div.appendChild(item)
  }
  document.body.appendChild(div)
  const close = (ev: MouseEvent) => {
    if (!div.contains(ev.target as Node)) {
      div.remove()
      document.removeEventListener('mousedown', close)
    }
  }
  setTimeout(() => document.addEventListener('mousedown', close), 0)
}
