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
  const fitRef = useRef<FitAddon | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const xterm = new XTerm({
      cursorBlink: true,
      fontSize: 14,
      // CJK 等宽字体必须排第一位：xterm 的单元格尺寸取自「首个可用字体」的行高与字宽
      // （实测 Noto Sans Mono CJK SC = 行高 20px/em、半角 7px = 汉字 14px 正好 2 格）。
      // 西文优先时 xterm 按西文字宽定格（JetBrains Mono 8.4px、Consolas 7.7px），
      // 而汉字仍由回退字体按 14px 排版 → 汉字比 2 格窄 2.8/1.4px，后续所有字符逐字错位，
      // 且行高只按西文算（14px）→ 汉字顶部被裁（"关"削掉「丷」后与"天"无异）。
      fontFamily: '"Noto Sans Mono CJK SC", "NSimSun", Consolas, monospace',
      // 行高按「字体自然行高 × lineHeight」取整：20 × 1.2 = 24px 单元格，
      // 汉字顶部留 6.17px 余量（lineHeight 1.0 → 20px 格、余量 4.17px 亦不裁顶）。
      lineHeight: 1.2,
      theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
      scrollback: 2000,
    })
    const fitAddon = new FitAddon()
    xterm.loadAddon(fitAddon)
    xterm.open(containerRef.current)
    fitAddon.fit()
    xtermRef.current = xterm
    fitRef.current = fitAddon

    // WS 连接（会话变量全在 URL 查询参数；建连成功后服务端首帧 ready）
    // 尺寸必须用 fit 后的真实值：写死 80×24 会让 pty 按 80×24 布局（远端 ConPTY 按 24 行
    // 发初始化换行、只给 80 列折行，本机 pty 甚至从未被 setwinsz 过 = 0×0），
    // 而本端 xterm 屏幕是另一个尺寸 → 顶部空白块 + 输出行尾与下次输入挤同一行。
    const q = new URLSearchParams({
      device_id: String(deviceId ?? 0),
      shell,
      cols: String(xterm.cols),
      rows: String(xterm.rows),
    }).toString()
    const ws = new WebSocket(`${getWsBase()}/api/terminal/ws?${q}`)
    wsRef.current = ws

    // V5-F F2：桌面终端惯例——Ctrl+Insert 复制 / Shift+Insert 粘贴（xterm 默认键位不含）
    xterm.attachCustomKeyEventHandler((e) => {
      if (e.type !== 'keydown') return true
      if (e.ctrlKey && e.key === 'Insert') {
        const sel = xterm.getSelection()
        if (sel) navigator.clipboard?.writeText(sel)
        return false
      }
      if (e.shiftKey && e.key === 'Insert') {
        navigator.clipboard?.readText().then((t) => {
          if (t) wsRef.current?.send(JSON.stringify({ type: 'input', data: t }))
        })
        return false
      }
      return true
    })

    let closed = false
    // 建连时同步一次真实尺寸：mount 时那次 fit 在订阅 onResize 之前就执行完了，
    // 其尺寸变化没人转发 → 只靠后续 fit 不会补发（尺寸没变就不触发 onResize）。
    ws.onopen = () => {
      try {
        ws.send(JSON.stringify({ type: 'resize', cols: xterm.cols, rows: xterm.rows }))
      } catch {}
    }
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
      fitRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, shell])

  // 批次 BF：容器尺寸一变就重新 fit。
  // 原实现只有「mount 后 100ms 那一次」fit，容器变高变矮（底栏拖拽/折叠/窗口 resize/切标签页）
  // 它都无感 —— 行列数就此与实际容器不符，pty 也跟着错。
  // 用 ResizeObserver 而非在调用处各写一处 fit：底层组件一次修好，所有使用方都受益。
  // 过渡期间不 fit：底栏折叠/展开是 160ms 的 height 过渡，中途每次回调量到的都是中间高度，
  // 最矮会被算成 1 行（proposeDimensions: rows = max(1, floor(h/cellH))），把 pty 也 resize 成 1 行。
  // 判定用 getAnimations()（CSS 过渡在运行时会出现在里面），跳过就 60ms 后再看一次，结束即 fit。
  // ponytail: 轮询式「等过渡结束」，不引状态机；代价是折叠期间多几次空转定时器。
  useEffect(() => {
    const el = containerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let timer = 0
    const refit = () => {
      // 容器没高度就别 fit：折叠态 body 是 0（隐藏标签 display:none 也是 0），
      // 而 proposeDimensions 的行数下限是 1 —— fit 只会把 xterm 和 pty 一起改成 1 行，
      // 展开后还要再改回来（pty 被反复 resize，TUI 会重画）。等它有高度时 RO 会再叫我。
      if (el.clientHeight < 20) return
      const bar = el.closest('.gterm') as HTMLElement | null
      if (bar?.getAnimations && bar.getAnimations().length) {
        timer = window.setTimeout(refit, 60)
        return
      }
      fitRef.current?.fit()
    }
    const ro = new ResizeObserver(() => {
      clearTimeout(timer)
      timer = window.setTimeout(refit, 0)
    })
    ro.observe(el)
    return () => {
      ro.disconnect()
      clearTimeout(timer)
    }
  }, [])

  // 批次 Z：内距归零 —— 终端四周留白改由容器语义选择器承担（桌面底栏 `.desktop-mode .gterm .xterm`；
  // Web 终端页在调用处补 padding:8）。此处再留 8px 会与容器内距叠加成「上 14 / 左 20」的黑边，
  // 且 xterm fit 插件量的是本容器的内容盒，多一层 padding 只是白占列宽。
  return <div ref={containerRef} style={{ height: '100%' }} />
}