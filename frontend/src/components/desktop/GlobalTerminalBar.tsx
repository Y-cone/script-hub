/**
 * V5-G 全局终端底栏（PRD 4.K 设计基准 v2）：
 * 28px 多标签头（可折叠）+ 终端区。任何页面可用。
 * 复用 V4 WS 会话（TerminalTab）。
 * 独立终端页在桌面形态由本组件取代；分屏/弹出窗口已作废（v1.7）。
 * 批次 BF：展开高度 = 窗口高 1/5（或 localStorage 里拖拽留下的值），上边缘可拖拽调整。
 */
import { useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import TerminalTab from '../TerminalTab'
import { useDeviceContext } from '../../stores/deviceContext'

interface Tab {
  key: string
  deviceId: number | null
  shell: string
}

let seq = 0
const nextKey = () => `gt${++seq}`

/* 批次 BF：底栏高度契约
 * COLLAPSED_H 折叠态 = 1px 上边 + 28px 头（实测值，BE 起沿用，不许动）。
 * 展开态默认 = window.innerHeight * 1/5（1280×800 → 160）；拖拽后以 localStorage 为准。
 * 上下限：MIN_H=120（body 剩 91px → (91-8)/24 = 3 行，够看，再矮就 1~2 行）；
 *        上限 = 窗口 4/5（800 → 640，仍给主区留 120px，不吞掉整个主区）。 */
const COLLAPSED_H = 29
const H_KEY = 'scripthub_gterm_h'
const MIN_H = 120
const defaultH = () => Math.round(window.innerHeight * 0.2)
const clampH = (h: number) =>
  Math.min(Math.max(Math.round(h), MIN_H), Math.max(MIN_H, Math.round(window.innerHeight * 0.8)))
const readSavedH = () => {
  try {
    const v = parseInt(localStorage.getItem(H_KEY) || '', 10)
    return Number.isFinite(v) ? v : null
  } catch { return null }
}

// 终端类型候选（批次 Q 方案 D：分段控件，点哪项立即开哪一类型）。
// value = 后端白名单契约值（session_manager.TERMINAL_SHELLS，与 executor.SHELL_RUNNERS 的键同源），
// label = 界面显示（分段项 与 标签页标题 共用）。空 shell '' = 设备默认，只由「+ 终端」主按钮触发。
const SHELLS: Record<'windows' | 'unix', { value: string; label: string }[]> = {
  windows: [
    { value: 'powershell', label: 'PowerShell' },
    { value: 'cmd', label: 'cmd' },
    { value: 'bash', label: 'Git Bash' },
  ],
  unix: [
    { value: 'bash', label: 'bash' },
    { value: 'sh', label: 'sh' },
    { value: 'zsh', label: 'zsh' },
  ],
}
// 本机平台判据与 ScriptWorkspace 的 LOCAL_SHELLS 一致（后端以 sys.platform 兜底校验）
const LOCAL_PLATFORM: 'windows' | 'unix' =
  typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent) ? 'windows' : 'unix'

export default function GlobalTerminalBar() {
  const { currentDeviceId, deviceNames, devicePlatforms, refreshDevices } = useDeviceContext()
  const [tabs, setTabs] = useState<Tab[]>([])
  const [activeKey, setActiveKey] = useState('')
  const [collapsed, setCollapsed] = useState(true)
  // 拖过就记在 draggedRef：之后窗口 resize 只做夹取，不再回到 1/5 默认。有存档值视为拖过。
  // localStorage 只读一次（useRef 的参数每次渲染都会求值，useState 才是懒的）
  const [saved0] = useState(readSavedH)
  const draggedRef = useRef(saved0 !== null)
  const [h, setH] = useState(() => clampH(saved0 ?? defaultH()))
  const rootRef = useRef<HTMLDivElement>(null)

  // SPEC §5：窗口高度 <600 时底栏自动折叠（矮窗里展开的终端会挤掉主区）。
  // ponytail: 只在 resize/挂载时判定，用户手动展开不会被强行收回（除非再次 resize）。
  // 批次 BF：窗口 resize 时高度跟着重算（未拖过 → 1/5；拖过 → 只按新窗口夹取上下限）。
  useEffect(() => {
    const onResize = () => {
      if (window.innerHeight < 600) setCollapsed(true)
      setH((prev) => (draggedRef.current ? clampH(prev) : clampH(defaultH())))
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 批次 BF：上边缘 5px 手柄拖拽（Pointer Events —— 一套事件同时覆盖鼠标/触摸/触控笔）。
  // 注意：xdotool 合成的事件在真壳里触发不了它（真鼠标可以，已实测拖动成功），
  // 原因未定位；这类交互的自动化验证不可靠，需人工确认。
  // 拖动期间给 .gterm 挂 .gt-dragging（transition:none）——否则每帧高度都走 160ms 过渡，手感是拖尾的。
  // 拖动中高度直接写 DOM（不逐帧 setState，省掉 60 fps 的 React 重渲染），松手才提交 state + 落 localStorage。
  const onGripDown = (e: React.PointerEvent) => {
    const el = rootRef.current
    if (!el) return
    e.preventDefault()
    const startY = e.clientY
    const startH = el.getBoundingClientRect().height
    el.classList.add('gt-dragging')
    const move = (ev: PointerEvent) => { el.style.height = `${clampH(startH + startY - ev.clientY)}px` }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      el.classList.remove('gt-dragging')
      const nh = clampH(el.getBoundingClientRect().height)
      el.style.height = `${nh}px`
      draggedRef.current = true
      try { localStorage.setItem(H_KEY, String(nh)) } catch {}
      setH(nh)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // 设备名单一来源 = 设备上下文；设备增删后由 devices-changed 事件刷新映射
  useEffect(() => { refreshDevices() }, [refreshDevices])
  useEffect(() => {
    window.addEventListener('devices-changed', refreshDevices)
    return () => window.removeEventListener('devices-changed', refreshDevices)
  }, [refreshDevices])

  // 当前设备（本机 null）可选的终端类型
  const shellsFor = (deviceId: number | null) =>
    SHELLS[deviceId ? (devicePlatforms[deviceId] ?? 'unix') : LOCAL_PLATFORM]

  const makeTitle = (deviceId: number | null, shell: string) =>
    `${deviceId ? deviceNames[deviceId] || `设备#${deviceId}` : '本机'} · ${
      shellsFor(deviceId).find((o) => o.value === shell)?.label || shell || '默认'}`

  const addTab = (shell = '') => {
    const key = nextKey()
    setTabs((prev) => [...prev, { key, deviceId: currentDeviceId, shell }])
    setActiveKey(key)
    setCollapsed(false)
  }

  // 批次 Q（方案 D）：终端类型摊平成分段控件 —— 点哪项就立即新建该类型的终端（一步，不是先选中）。
  // 位置固定在「⌃ 展开 / ⌄ 收起」左侧：展开态由 .newt 的 margin-left:auto 把它顶到尾部；
  // 收起态（无标签）行里没有 .newt，故由 autoLeft 让 wrapper 吃 auto 左边距（否则会贴住「+ 终端」）。
  // 分段项的「选中」= 当前激活标签的 shell（收起态没有可见终端 → 不高亮，与 .gt-tab.on 同判据）
  const activeShell = collapsed ? '' : (tabs.find((t) => t.key === activeKey)?.shell ?? '')

  const shellSeg = (autoLeft = false) => (
    <span className="seg" style={autoLeft ? { marginLeft: 'auto' } : undefined}
      role="group" aria-label="新建终端（指定类型）">
      {shellsFor(currentDeviceId).map((o) => (
        <span key={o.value} className={`sg${o.value === activeShell ? ' on' : ''}`} role="button" tabIndex={0}
          title={`新建 ${o.label} 终端`}
          onClick={() => addTab(o.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); addTab(o.value) }
          }}>
          {o.label}
        </span>
      ))}
    </span>
  )

  const removeTab = (key: string) => {
    const idx = tabs.findIndex((t) => t.key === key)
    const next = tabs.filter((t) => t.key !== key)
    setTabs(next)
    if (activeKey === key) {
      setActiveKey(next.length === 0 ? '' : (next[idx] ?? next[idx - 1] ?? next[0]).key)
    }
    if (next.length === 0) setCollapsed(true)
  }

  // 快捷键：Ctrl+T / Ctrl+W（桌面形态由本组件承接）
  useEffect(() => {
    const onNew = () => addTab()
    const onClose = () => { if (activeKey) removeTab(activeKey) }
    window.addEventListener('scripthub:term-new', onNew)
    window.addEventListener('scripthub:term-close', onClose)
    return () => {
      window.removeEventListener('scripthub:term-new', onNew)
      window.removeEventListener('scripthub:term-close', onClose)
    }
  })

  if (tabs.length === 0) {
    // BE：收起态也带上 .gt-collapsed —— 底栏初始就是「0 标签」这一支，若这里不带类，
    // 第一次建标签时 gterm 的高度没有变化（29→h 那步没有），首帧展开就是瞬变。
    // BF：高度改由内联 height 给出（29 ↔ h），transition 挂 height（见 desktop.css）。
    return (
      <div ref={rootRef} className="gterm gt-collapsed" style={{ height: collapsed ? COLLAPSED_H : h }}>
        <div className="gt-head">
          <span className="newt gt-newt-left" style={{ cursor: 'pointer' }} onClick={() => addTab()}>+ 终端</span>
          {shellSeg(true)}
          <span style={{ color: 'var(--muted)' }}>⌃ 展开</span>
        </div>
      </div>
    )
  }

  return (
    <div ref={rootRef} className={collapsed ? 'gterm gt-collapsed' : 'gterm'} style={{ height: collapsed ? COLLAPSED_H : h }}>
      {/* 批次 BF：上边缘拖拽手柄（5px，绝对定位不占高 —— 折叠态仍必须是 1px 边 + 28px 头 = 29px）。
          折叠时不渲染：29px 里没有可拖的空间，拖了会和「内联 29px」打架。 */}
      {!collapsed && <div className="gt-grip" onPointerDown={onGripDown} title="拖动调整终端高度" />}
      {/* 28px 标签头（原型 §6.1 .gt-head） */}
      <div className="gt-head">
        {/* 标签条独立成可横向滚动的一格（批次 Q）：分段控件固定占位后，标签多时不再互相挤压换行 */}
        <span className="gt-tabs">
          {tabs.map((t) => (
            <span
              key={t.key}
              className={`gt-tab${t.key === activeKey && !collapsed ? ' on' : ''}`}
              style={{ cursor: 'pointer' }}
              onClick={() => { setActiveKey(t.key); setCollapsed(false) }}
            >
              <span className="ok">●</span>
              {makeTitle(t.deviceId, t.shell)}
              <span className="x" onClick={(e) => { e.stopPropagation(); removeTab(t.key) }}>✕</span>
            </span>
          ))}
        </span>
        <span className="newt" style={{ cursor: 'pointer' }} onClick={() => addTab()}
          title={`新建终端（当前设备：${currentDeviceId ? deviceNames[currentDeviceId] || `设备#${currentDeviceId}` : '本机'}）`}>+ 终端</span>
        {/* 「N 个会话挂起」移到分段控件左侧：让分段控件在收起态也紧贴「⌃ 展开」（批次 Q） */}
        {collapsed && <span className="mono">{tabs.length} 个会话挂起</span>}
        {shellSeg()}
        <span className="gt-toggle" style={{ cursor: 'pointer', color: 'var(--muted)' }} onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? '展开' : '收起'}>{collapsed ? '⌃ 展开' : '⌄ 收起'}</span>
      </div>
      {/* 终端区（BE：**始终挂载**）——原 `{!collapsed && …}` 条件渲染会在折叠瞬间卸载 body，
          高度当场归零、没有任何中间态可插值（实测 transition 恒 0s），故 body 必须一直在。
          折叠靠 `.gterm` 自身高度收起：BE 用 max-height 夹，BF 改为内联 `height`（29 ↔ h）驱动，
          transition 挂 height（见 desktop.css `.desktop-mode .gterm`）。
          原内联 `height: 228` 已删：实测该属性对实际高度毫无作用（BE 实测改成 0px 高度仍 584）——
          body 有 flex:1（flex-basis 0% 在主轴上胜过 height），实际高度由父容器 `.gterm` 给出。 */}
      <div className="gt-body" style={{ position: 'relative' }}>
        {tabs.map((t) => (
          <div key={t.key} style={{ display: t.key === activeKey ? 'block' : 'none', height: '100%' }}>
            <TerminalTab deviceId={t.deviceId} shell={t.shell} onError={(m) => message.error(m)} />
          </div>
        ))}
      </div>
    </div>
  )
}
