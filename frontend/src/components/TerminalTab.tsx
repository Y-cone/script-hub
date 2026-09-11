import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getWsBase } from '../config'

interface Props {
  deviceId: number | null
  shell: string
  onError?: (msg: string) => void
}

/**
 * 交互终端标签：xterm 渲染 + WS 双向。deviceId=null→本机；shell 为空→默认。
 * 每个标签独立 WS 会话，关闭时发送 close 并断开。
 */
export default function TerminalTab({ deviceId, shell, onError }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const xtermRef = useRef<XTerm | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const xterm = new XTerm({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Consolas, "Courier New", monospace',
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
      scrollback: 2000,
    })
    const fitAddon = new FitAddon()
    xterm.loadAddon(fitAddon)
    xterm.open(containerRef.current)
    fitAddon.fit()
    xtermRef.current = xterm

    // WS 连接（会话变量全在 URL 查询参数；建连成功后服务端首帧 ready）
    const q = new URLSearchParams({
      device_id: String(deviceId ?? 0),
      shell,
      cols: '80',
      rows: '24',
    }).toString()
    const ws = new WebSocket(`${getWsBase()}/api/terminal/ws?${q}`)
    wsRef.current = ws

    let closed = false
    ws.onopen = () => {}
    ws.onmessage = (ev) => {
      if (closed) return
      let msg: any
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'output') {
        xterm.write(msg.data)
      } else if (msg.type === 'exit') {
        xterm.write('\r\n\x1b[90m[会话结束]\x1b[0m\r\n')
      } else if (msg.type === 'error') {
        xterm.write(`\r\n\x1b[91m[错误] ${msg.message}\x1b[0m\r\n`)
        onError?.(msg.message)
      } else if (msg.type === 'ready') {
        // session 建立；写一个提示换行便于定位输入位置
        xterm.write('')
      }
    }
    ws.onclose = () => {
      if (!closed) xterm.write('\r\n\x1b[90m[连接已断开]\x1b[0m\r\n')
    }
    // 连接失败/握手失败（如后端未启动、设备不可达）→ 友好提示
    ws.onerror = () => {
      // 主动 close（cleanup）会触发 onerror，此时 closed 已置位——不误报
      if (closed) return
      onError?.('无法连接终端服务，请确认后端已启动')
      xterm.write('\r\n\x1b[91m[连接失败] 无法连接终端服务\x1b[0m\r\n')
    }

    // 交互输入 → WS input
    const inputSub = xterm.onData((data) => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'input', data }))
      }
    })
    // resize → 同步 pty 尺寸
    const resizeSub = xterm.onResize(({ cols, rows }) => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }))
      }
    })
    const fitTimer = window.setTimeout(() => fitAddon.fit(), 100)

    return () => {
      closed = true
      // 关闭会话：发 close + 断开 WS
      try {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'close' }))
        }
        wsRef.current?.close()
      } catch {}
      wsRef.current = null
      inputSub.dispose()
      resizeSub.dispose()
      clearTimeout(fitTimer)
      xterm.dispose()
      xtermRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, shell])

  return <div ref={containerRef} style={{ height: '100%', padding: 8 }} />
}