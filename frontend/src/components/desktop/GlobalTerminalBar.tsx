/**
 * V5-G 全局终端底栏（PRD 4.K 设计基准 v2）：
 * 28px 多标签头（可折叠）+ 200px 终端区。任何页面可用。
 * 复用 V4 WS 会话（TerminalTab）+ V5-C 会话恢复（restored 标签）。
 * 独立终端页在桌面形态由本组件取代；分屏/弹出窗口已作废（v1.7）。
 */
import { useEffect, useState } from 'react'
import { Button, message } from 'antd'
import TerminalTab from '../TerminalTab'
import { apiBase } from '../../config'
import { useDeviceContext } from '../../stores/deviceContext'

interface Tab {
  key: string
  deviceId: number | null
  shell: string
  restored?: boolean
  tabId?: string
  restoredHistory?: string
}

let seq = 0
const nextKey = () => `gt${++seq}`

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

  // SPEC §5：窗口高度 <600 时底栏自动折叠（矮窗里展开的终端会挤掉主区）。
  // ponytail: 只在 resize/挂载时判定，用户手动展开不会被强行收回（除非再次 resize）。
  useEffect(() => {
    const onResize = () => { if (window.innerHeight < 600) setCollapsed(true) }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

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

  // V5-C：恢复持久化标签（未连接态 + 只读历史）
  useEffect(() => {
    fetch(`${apiBase}/api/terminal/sessions`)
      .then((r) => r.json())
      .then((d) => {
        const restored: Tab[] = (d.sessions || []).map((s: any) => ({
          key: nextKey(),
          deviceId: s.device_id ?? null,
          shell: s.shell || '',
          restored: true,
          tabId: s.tab_id,
          restoredHistory: s.scrollback || '',
        }))
        if (restored.length) {
          setTabs(restored)
          setActiveKey(restored[0].key)
        }
      })
      .catch(() => {})
  }, [])

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
    const t = tabs.find((x) => x.key === key)
    if (t?.restored && t.tabId) {
      fetch(`${apiBase}/api/terminal/sessions/${t.tabId}`, { method: 'DELETE' }).catch(() => {})
    }
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
    // 第一次建标签时 gterm 的 max-height 没变化（29→900 那步没有），首帧展开就是瞬变。
    return (
      <div className="gterm gt-collapsed">
        <div className="gt-head">
          <span className="newt gt-newt-left" style={{ cursor: 'pointer' }} onClick={() => addTab()}>+ 终端</span>
          {shellSeg(true)}
          <span style={{ color: 'var(--muted)' }}>⌃ 展开</span>
        </div>
      </div>
    )
  }

  return (
    <div className={collapsed ? 'gterm gt-collapsed' : 'gterm'}>
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
              {!t.restored && <span className="ok">●</span>}
              {makeTitle(t.deviceId, t.shell)}{t.restored ? '（已断开）' : ''}
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
          高度当场归零、没有任何中间态可插值（实测 transition 恒 0s）。改由 .gterm 的 max-height 夹
          高度来收起（desktop.css「终端底栏展开/折叠」段），body 只需一直在。
          原内联 `height: 228` 已删：实测该属性对实际高度毫无作用（改成 0px 高度仍 584）——
          body 有 flex:1（flex-basis 0% 在主轴上胜过 height），实际高度由内容（xterm）决定。 */}
      <div className="gt-body" style={{ position: 'relative' }}>
        {tabs.map((t) => (
          <div key={t.key} style={{ display: t.key === activeKey ? 'block' : 'none', height: '100%' }}>
            {t.restored ? (
              <div style={{ height: '100%', display: 'flex', flexDirection: 'column', padding: '6px 10px' }}>
                <div style={{
                  flex: 1, overflow: 'auto', fontFamily: 'var(--sh-mono)', fontSize: 13,
                  whiteSpace: 'pre-wrap', color: '#a8b3c2',
                }}>
                  {t.restoredHistory || '（无历史输出）'}
                </div>
                <div style={{ borderTop: '1px solid var(--sh-border, #3a3d46)', paddingTop: 4, color: 'var(--sh-muted, #8b909a)', fontSize: 13 }}>
                  会话已结束（应用重启）。以上为历史输出（只读）。
                  <Button size="small" type="primary" style={{ marginLeft: 10 }} onClick={() => {
                    const key = nextKey()
                    setTabs((prev) => [...prev.filter((x) => x.key !== t.key), { key, deviceId: t.deviceId, shell: t.shell }])
                    setActiveKey(key)
                  }}>重连</Button>
                </div>
              </div>
            ) : (
              <TerminalTab deviceId={t.deviceId} shell={t.shell} onError={(m) => message.error(m)} />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
