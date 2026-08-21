import { useEffect, useRef } from 'react'
import { Terminal as XTerminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface TerminalProps {
  output: string
  status: string
  onClear?: () => void
}

export default function Terminal({ output, status, onClear }: TerminalProps) {
  const terminalRef = useRef<HTMLDivElement>(null)
  const xtermRef = useRef<XTerminal | null>(null)

  useEffect(() => {
    if (!terminalRef.current) return
    const xterm = new XTerminal({
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
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
    if (output) {
      // xterm.js 需要 \r\n 换行
      xtermRef.current.write(output.replace(/\n/g, '\r\n'))
    }
    if (status === 'running') {
      xtermRef.current.write('\r\n\x1b[33m[运行中...]\x1b[0m')
    } else if (status === 'success') {
      xtermRef.current.write('\r\n\x1b[32m[执行成功]\x1b[0m')
    } else if (status === 'failed') {
      xtermRef.current.write('\r\n\x1b[31m[执行失败]\x1b[0m')
    } else if (status === 'timeout') {
      xtermRef.current.write('\r\n\x1b[31m[执行超时]\x1b[0m')
    } else if (status === 'killed') {
      xtermRef.current.write('\r\n\x1b[31m[已终止]\x1b[0m')
    }
  }, [output, status])

  return (
    <div style={{ position: 'relative' }}>
      <div ref={terminalRef} style={{ height: 400, border: '1px solid #d9d9d9', borderRadius: 6, padding: 8 }} />
      {onClear && (
        <button onClick={onClear} style={{ position: 'absolute', top: 8, right: 8, background: 'rgba(255,255,255,0.1)', border: 'none', color: '#d4d4d4', padding: '4px 8px', borderRadius: 4, cursor: 'pointer' }}>
          清空
        </button>
      )}
    </div>
  )
}
