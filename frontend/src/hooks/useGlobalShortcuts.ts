/**
 * V5-F F2 全局快捷键（PRD 4.K）：两形态同绑定。
 *  - Ctrl+K  聚焦脚本搜索框（脚本库页）
 *  - F5      刷新当前页数据（派发 scripthub:refresh 事件，页面自行监听）
 *  - Ctrl+Enter 执行（派发 scripthub:run 事件，脚本详情页监听）
 *  - Ctrl+T  新建终端标签 / Ctrl+W 关闭当前终端标签（派发 scripthub:term-new / term-close）
 */
import { useEffect } from 'react'

export function useGlobalShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey
      // Ctrl+K：桌面形态打开搜索面板；Web 形态聚焦页面搜索框
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        if (window.__SCRIPTHUB_API__) {
          window.dispatchEvent(new CustomEvent('scripthub:palette'))
        } else {
          const el = document.querySelector<HTMLInputElement>(
            'input[data-shortcut="search"]'
          )
          if (el) {
            el.focus()
            el.select()
          }
        }
        return
      }
      // F5：刷新（阻止浏览器重载，交给页面拉数据）
      if (e.key === 'F5') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:refresh'))
        return
      }
      // Ctrl+Enter：执行
      if (mod && e.key === 'Enter') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:run'))
        return
      }
      // Ctrl+T / Ctrl+W：终端标签
      if (mod && e.key.toLowerCase() === 't') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('scripthub:term-new'))
        return
      }
      if (mod && e.key.toLowerCase() === 'w') {
        // 仅当在终端页时拦截（避免影响浏览器关标签意图）
        if (location.pathname === '/terminal') {
          e.preventDefault()
          window.dispatchEvent(new CustomEvent('scripthub:term-close'))
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
