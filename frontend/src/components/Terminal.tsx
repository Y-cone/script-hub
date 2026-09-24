import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface TerminalProps {
  output: string
  status: string
  onClear?: () => void
}

// 复制文本：优先 Clipboard API，不可用(非 localhost/非 HTTPS 时 navigator.clipboard 为 undefined)则用 execCommand 兜底
async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(ta)
  if (!ok) throw new Error('复制失败')
}

export default function Terminal({ output, status, onClear }: TerminalProps) {
  const terminalRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<XTerminal | null>(null)
  const [copyMsg, setCopyMsg] = useState('')

  useEffect(() => {
    if (!terminalRef.current) return
    const xterm = new XTerminal({
      theme: { background: '#191a1f', foreground: '#d4d4d4' },
      fontFamily: 'Consolas, "Courier New", monospace',
      fontSize: 14,
      lineHeight: 1.2,
      cursorBlink: false,
      disableStdin: true,
      scrollback: 1000,
    })
    const fitAddon = new FitAddon()
    xterm.loadAddon(fitAddon)
    xterm.open(terminalRef.current)
    fitAddon.fit()
    xtermRef.current = xterm
    const handleResize = () => fitAddon.fit()
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); xterm.dispose() }
  }, [])

  useEffect(() => {
    if (!xtermRef.current) return
    xtermRef.current.clear()
    // 先显示状态
    if (status === 'running') {
      xtermRef.current.write('\x1b[33m[运行中...]\x1b[0m\r\n')
    } else if (status === 'success') {
      xtermRef.current.write('\x1b[32m[执行成功]\x1b[0m\r\n')
    } else if (status === 'failed') {
      xtermRef.current.write('\x1b[31m[执行失败]\x1b[0m\r\n')
    } else if (status === 'timeout') {
      xtermRef.current.write('\x1b[31m[执行超时]\x1b[0m\r\n')
    } else if (status === 'killed') {
      xtermRef.current.write('\x1b[31m[已终止]\x1b[0m\r\n')
    }
    // 再显示输出
    if (output) {
      xtermRef.current.write('\r\n' + output.replace(/\n/g, '\r\n'))
    }
  }, [output, status])

  const handleCopyJson = async () => {
    if (!output.trim()) {
      setCopyMsg('无输出')
      return
    }
    // output 可能带命令回显前缀（$ python <path>\n），从首个 { 或 [ 提取 JSON
    let jsonStr = output
    const firstBrace = output.search(/[{\[]/)
    if (firstBrace > 0) {
      jsonStr = output.slice(firstBrace)
    }
    try {
      const parsed = JSON.parse(jsonStr.trim())
      const formatted = JSON.stringify(parsed, null, 2)
      await copyText(formatted)
      setCopyMsg('已复制格式化 JSON')
    } catch {
      setCopyMsg('输出非合法 JSON')
    }
    setTimeout(() => setCopyMsg(''), 2500)
  }

  // 外框走原型 §0 的 .out（term 底 + border + 6px + mono 11.5）
  return (
    <div className="out" style={{ position: 'relative', minHeight: 0 }}>
      <div ref={terminalRef} style={{ height: 400, border: 'none', borderRadius: 0, padding: 0, background: 'transparent' }} />
      <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 6, alignItems: 'center' }}>
        {copyMsg && <span style={{ color: '#d4d4d4', fontSize: 12, background: 'rgba(255,255,255,0.15)', padding: '2px 8px', borderRadius: 4 }}>{copyMsg}</span>}
        <button onClick={handleCopyJson} style={{ background: 'rgba(255,255,255,0.1)', border: 'none', color: '#d4d4d4', padding: '4px 8px', borderRadius: 4, cursor: 'pointer' }}>
          复制 JSON
        </button>
        {onClear && (
          <button onClick={onClear} style={{ background: 'rgba(255,255,255,0.1)', border: 'none', color: '#d4d4d4', padding: '4px 8px', borderRadius: 4, cursor: 'pointer' }}>
            清空
          </button>
        )}
      </div>
    </div>
  )
}
