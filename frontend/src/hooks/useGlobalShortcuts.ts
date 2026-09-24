/**
 * V5-G 全局快捷键（SPEC §3.3 十键）——桌面形态单一路径：
 *   Ctrl+K 搜索面板 / Ctrl+/ 帮助面板 / Ctrl+Enter 执行 /
 *   Ctrl+1..5 工作区 tab（ScriptDetail 自处理）/ Ctrl+T·W 终端标签 / F5 刷新 / Esc（面板自处理）
 * 终端冲突（SPEC §3.3）：终端聚焦时 Ctrl+W/Ctrl+Insert/Shift+Insert/Esc 交给 xterm——
 * 由 TerminalTab attachCustomKeyEventHandler 先行消费，此处不抢（xterm handler 返回 false 已 stopPropagation）。
 */
import { useEffect } from 'react'

export function useGlobalShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey

      // Ctrl+K：搜索面板（SPEC §2.6）
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:palette'))
        return
      }
      // Ctrl+/：快捷键帮助面板（SPEC §2.8）
      if (mod && (e.key === '/' || e.key === '?')) {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:kbd'))
        return
      }
      // F5：刷新（阻止浏览器重载，交给页面拉数据）
      if (e.key === 'F5') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:refresh'))
        return
      }
      // Ctrl+Enter：执行 —— 批次 AZ：改交工作区（`scripthub:run-hotkey`）再派发 `scripthub:run`。
      // 原此处直接派发无 detail 的 `scripthub:run` → 右栏改的超时/Shell/参数既不下发也不落库
      // （右栏状态由 ScriptWorkspace 持有，它不在链路上）。工作区未挂载时同原行为：无监听者、无动作。
      if (mod && e.key === 'Enter') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:run-hotkey'))
        return
      }
      // Ctrl+T / Ctrl+W：终端标签
      if (mod && e.key.toLowerCase() === 't') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:term-new'))
        return
      }
      if (mod && e.key.toLowerCase() === 'w') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:term-close'))
      }
      // Ctrl+1..5 工作区 tab：由 ScriptDetail 自行监听（SPEC §3.3），此处不重复处理
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
