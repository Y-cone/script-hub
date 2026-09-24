/**
 * V5-G 桌面 Shell（PRD 4.K 设计基准 v2）：
 * 40px 顶栏（文字导航 + 全局搜索 + 设备上下文）+ 主区路由 + 全局终端底栏。
 * 仅桌面形态渲染（App.tsx 唯一根布局；批次 AS 起 Web 形态 AppLayout 已删除，无形态分支）。
 */
import { useEffect, useRef, useState } from 'react'
import { Spin } from 'antd'   // Select 已在顶栏改用自绘 Sel（原型 §1 + 批次 K-4）
import { SearchOutlined } from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { getDevices, getScripts, waitForApi } from '../../services/api'
import type { ScriptItem } from '../../services/api'
import { useDeviceContext, reprobe } from '../../stores/deviceContext'
import GlobalTerminalBar from './GlobalTerminalBar'
import SettingsPalette from './SettingsPalette'

/** G1-B：窗口已自绘（decorations=false），点击内容区不激活窗口 → 主动 setFocus。
 *  与三键同一调用模式；动态 import 只做一次（模块级缓存）。 */
let winRef: Promise<import('@tauri-apps/api/window').Window> | null = null
const currentWin = () => (winRef ??= import('@tauri-apps/api/window').then((m) => m.getCurrentWindow()))

const NAV = [
  { key: '/', label: '脚本库' },
  { key: '/schedules', label: '定时调度' },
  { key: '/history', label: '运行历史' },
  { key: '/devices', label: '设备' },
  { key: '/system', label: '本机信息' },
]

/** V5-G SPEC §2.8：快捷键帮助面板（Ctrl+/ 唤出）——内容与 SPEC §3.3 键位表同源 */
const SHORTCUT_GROUPS: { group: string; items: { keys: string; desc: string }[] }[] = [
  {
    group: '全局',
    items: [
      { keys: 'Ctrl + K', desc: '搜索面板' },
      { keys: 'Ctrl + /', desc: '快捷键帮助面板' },
      { keys: 'F5', desc: '刷新当前页' },
      { keys: 'Esc', desc: '关闭面板 / 弹层' },
    ],
  },
  {
    group: '脚本工作区',
    items: [
      { keys: 'Ctrl + Enter', desc: '执行脚本' },
      { keys: 'Ctrl + 1 … 5', desc: '切换工作区标签' },
    ],
  },
  {
    group: '终端',
    items: [
      { keys: 'Ctrl + T', desc: '新建终端标签' },
      { keys: 'Ctrl + W', desc: '关闭当前终端标签' },
      { keys: 'Ctrl + Insert', desc: '终端复制' },
      { keys: 'Shift + Insert', desc: '终端粘贴' },
    ],
  },
]

function ShortcutPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div onMouseDown={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,.55)',
      display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: 80,
    }}>
      <div className="sh-palette" onMouseDown={(e) => e.stopPropagation()} style={{
        width: 560, background: 'var(--sh-float, #2e3038)', border: '1px solid var(--sh-border, #3a3d46)',
        borderRadius: 6, boxShadow: '0 20px 60px rgba(0,0,0,.5)', overflow: 'hidden',
      }}>
        <div style={{
          padding: '10px 14px', fontSize: 14, fontWeight: 600,
          borderBottom: '1px solid var(--sh-border, #3a3d46)',
        }}>
          键盘快捷键 <span style={{ marginLeft: 6, fontWeight: 400, fontSize: 12, color: 'var(--sh-muted, #8b909a)', fontFamily: 'var(--sh-mono)' }}>Ctrl + /</span>
        </div>
        <div style={{ maxHeight: 420, overflow: 'auto' }}>
          <table>
            <thead>
              <tr><th style={{ width: 190 }}>快捷键</th><th>作用</th><th style={{ width: 200 }}>生效范围</th></tr>
            </thead>
            <tbody>
              {SHORTCUT_GROUPS.map((g) => g.items.map((it) => (
                <tr key={it.keys}>
                  <td><span className="kbd">{it.keys}</span></td>
                  <td>{it.desc}</td>
                  <td style={{ color: 'var(--muted)' }}>{g.group}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
        <div style={{
          borderTop: '1px solid var(--sh-border, #3a3d46)', padding: '6px 14px', fontSize: 12,
          color: 'var(--sh-muted, #8b909a)',
        }}>
          终端聚焦时 Ctrl+W / Ctrl+Insert / Shift+Insert 交给终端；Esc 在终端内不关闭本面板
        </div>
      </div>
    </div>
  )
}

/** 搜索面板：Ctrl+K / 点搜索框 → 浮层选脚本 → 跳详情 */
function SearchPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [kw, setKw] = useState('')
  const [items, setItems] = useState<ScriptItem[]>([])
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState(0)
  const [ms, setMs] = useState<number | null>(null)   // 原型 §6.2「N 个结果 · 14ms」
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) { setKw(''); setItems([]); setSel(0); return }
    setTimeout(() => inputRef.current?.focus(), 30)
  }, [open])

  useEffect(() => {
    if (!open) return
    setLoading(true)
    const t0 = performance.now()
    const t = setTimeout(() => {
      getScripts({ page: 1, page_size: 30, search: kw })
        .then((res) => { setItems(res.data.items || []); setMs(Math.round(performance.now() - t0)) })
        .catch(() => {})
        .finally(() => setLoading(false))
    }, 200)
    return () => clearTimeout(t)
  }, [kw, open])

  if (!open) return null

  const pick = (s: ScriptItem) => { onClose(); navigate(`/scripts/${s.id}`) }

  return (
    <div onMouseDown={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,.55)', // SPEC §2.6
      display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: 80,
    }}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        {/* 原型 §6.2 DOM：.pl-in（🔍 + 输入 + 「N 个结果 · Xms」）/ .pl-list（.pl-item）/ .pl-foot。
            这些类 proto.css 里本来就有，之前 JSX 用内联样式把它们架空了（等于没复刻）。 */}
        <div className="pl-in">
          <SearchOutlined style={{ color: 'var(--muted)' }} />
          <input
            ref={inputRef}
            value={kw}
            onChange={(e) => { setKw(e.target.value); setSel(0) }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)) }
              else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
              else if (e.key === 'Enter' && items[sel]) { pick(items[sel]) }
              else if (e.key === 'Escape') { onClose() }
            }}
            placeholder="搜索脚本…"
            style={{ flex: 1, minWidth: 0, background: 'transparent', border: 0, outline: 'none', color: 'var(--text)', font: 'inherit' }}
          />
          <span className="mono">{items.length} 个结果{ms != null ? ` · ${ms}ms` : ''}</span>
        </div>
        <div className="pl-list" style={{ overflow: 'auto' }}>
          {loading && items.length === 0 && <div style={{ padding: 16, textAlign: 'center' }}><Spin /></div>}
          {!loading && items.length === 0 && (
            <div style={{ padding: 16, color: 'var(--muted)', textAlign: 'center', fontSize: 13 }}>无匹配脚本</div>
          )}
          {items.map((s, i) => (
            <div key={s.id} className={`pl-item${i === sel ? ' on' : ''}`}
              onMouseEnter={() => setSel(i)} onClick={() => pick(s)}>
              <span>{s.name}</span>
              <span className="mono">{s.relative_path.split(/[/\\]/).slice(0, -1).join('/') || '/'}</span>
            </div>
          ))}
        </div>
        <div className="pl-foot">
          <span>↑↓ 选择</span><span>↵ 打开</span><span>Esc 关闭</span>
          <span className="mono">{items[sel] ? `Enter → /scripts/${items[sel].id}` : ''}</span>
        </div>
      </div>
    </div>
  )
}

export default function DesktopShell() {
  const navigate = useNavigate()
  const location = useLocation()
  // 批次 AN：顶栏不再切设备 → 这里只读 currentDeviceId 做展示（切换入口在「设备」页）
  const { currentDeviceId } = useDeviceContext()
  const [devices, setDevices] = useState<{ id: number; name: string; online?: boolean | null }[]>([])
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [kbdOpen, setKbdOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // 批次 AE：后端就绪标记 —— 就绪前不挂页面内容（竞态成因见 services/api.ts waitForApi 注释）
  const [apiReady, setApiReady] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)   // R-2：.desktop-mode 元素本体，最大化标记打在它身上
  // SPEC §2.8：与搜索面板互斥——打开一个关闭另一个；设置面板（§2.9 v2.14）同样互斥
  const openPalette = () => { setKbdOpen(false); setSettingsOpen(false); setPaletteOpen(true) }
  const openKbd = () => { setPaletteOpen(false); setSettingsOpen(false); setKbdOpen(true) }
  const openSettings = () => { setPaletteOpen(false); setKbdOpen(false); setSettingsOpen(true) }
  const closeSettings = () => setSettingsOpen(false)

  // 批次 AE：等 sidecar 真能应答再挂页面内容。
  // 原先的写法是「页面 mount 就发一次请求，失败就 `.catch(()=>{})` 吞掉，靠路由变化再补一次」——
  // 桌面壳冷启动时 sidecar 还没监听，首批请求 100% 落空 → 脚本库空 + 顶栏只有「本机」，
  // 直到用户点了扫描 / 进了设备页才补上。这里改成先等 /api/health 通（实测冷启动约 1.1s，最多等 15s，
  // 超时也放行按旧行为渲染），就绪后再挂载页面 → 页面自己的 mount 请求天然落在后端可用之后。
  useEffect(() => {
    let dead = false
    waitForApi().then(() => { if (!dead) setApiReady(true) })
    return () => { dead = true }
  }, [])

  // 批次 AM：进入应用对「当前/默认设备」探一次。currentDeviceId 是 localStorage 持久化的
  // （stores/deviceContext.load()），上次关窗到现在可能装了/卸了 Git Bash —— 脚本库三态要的是
  // 当下结论。用 getState() 读初值而不是依赖 currentDeviceId：否则每次切设备都会再探一次
  // （切设备那份由 setCurrentDeviceId → reprobe 负责，别重探两遍）。
  // fire-and-forget：reprobe 自己不抛错，也不 await，不挡首屏。
  useEffect(() => {
    if (!apiReady) return
    void reprobe(useDeviceContext.getState().currentDeviceId)
  }, [apiReady])

  useEffect(() => {
    getDevices().then((res) => setDevices(res.data || [])).catch(() => {})
  }, [apiReady])
  useEffect(() => {
    const onChanged = () => getDevices().then((res) => setDevices(res.data || [])).catch(() => {})
    window.addEventListener('devices-changed', onChanged)
    return () => window.removeEventListener('devices-changed', onChanged)
  }, [])

  // Ctrl+K → 搜索面板；Ctrl+/ → 帮助面板（桌面形态）
  useEffect(() => {
    const onOpen = () => openPalette()
    const onKbd = () => openKbd()
    window.addEventListener('scripthub:palette', onOpen)
    window.addEventListener('scripthub:kbd', onKbd)
    return () => {
      window.removeEventListener('scripthub:palette', onOpen)
      window.removeEventListener('scripthub:kbd', onKbd)
    }
  }, [])

  const isScriptLib = location.pathname === '/' || location.pathname.startsWith('/scripts/')

  // [7] 弹层 portal 挂 body 下，.desktop-mode 类取不到 → 根元素挂 desktop-root 标记，desktop.css 用它门控弹层皮肤
  useEffect(() => {
    document.documentElement.classList.add('desktop-root')
    return () => document.documentElement.classList.remove('desktop-root')
  }, [])

  // R-2：无装饰窗口最大化时补 .sh-maxed（window.css:34 那条规则 R 批只写了 CSS，标记一直没人打）。
  // 为什么不用 CSS 的 :maximized 伪类 —— WebKitGTK 不支持；为什么不用手搓 listen('tauri://resize') ——
  // Tauri v2 现成的 Window.onResized()（@tauri-apps/api@2.11.1 window.d.ts:1214）就是该事件的封装。
  // 只在真 Tauri 壳内生效：__TAURI_INTERNALS__ 缺席（CDP 跑前端）时整个 effect 直接返回，零副作用。
  useEffect(() => {
    const el = rootRef.current
    if (!el || !(window as any).__TAURI_INTERNALS__) return
    let unlisten: (() => void) | undefined
    let dead = false
    // 每次都重新问窗口状态，不自己猜——resize 只当"该复查了"的信号
    const sync = () => currentWin()
      .then((w) => w.isMaximized())
      .then((max) => el.classList.toggle('sh-maxed', max))
      .catch(() => {})
    sync()   // 初始判定：可能一启动就是最大化态（此时不会有 resize 事件）
    currentWin()
      .then((w) => w.onResized(sync))
      .then((un) => { if (dead) un(); else unlisten = un })
      .catch(() => {})
    return () => { dead = true; unlisten?.() }
  }, [])

  const winAction = async (fn: 'minimize' | 'toggleMaximize' | 'close') => {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    const w = getCurrentWindow()
    if (fn === 'minimize') w.minimize()
    else if (fn === 'toggleMaximize') w.toggleMaximize()
    else w.close()
  }

  // G1-B：窗口无原生装饰后，点内容区 WM 不一定激活本窗口 → 任意位置按下即主动 setFocus。
  // 捕获阶段绑定，子元素 stopPropagation 也拦不住。
  // __TAURI_INTERNALS__：可能跑在壳外（CDP 测试前端），此时无 Tauri API 可调。
  const requestFocus = () => {
    if (!(window as any).__TAURI_INTERNALS__) return
    currentWin().then((w) => w.setFocus()).catch(() => {})
  }

  return (
    <div
      ref={rootRef}
      className="desktop-mode"
      onPointerDownCapture={requestFocus}
      style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
    >
      {/* 顶栏 40px —— 原型 §1 结构：logo / nav.tab / .search(240) / .devpick */}
      <div className="topbar" data-tauri-drag-region style={{ height: 40, flexShrink: 0 }}>
        <span className="logo">ScriptHub</span>
        <div className="nav">
          {NAV.map((n) => (
            <span
              key={n.key}
              className={`it${location.pathname === n.key ? ' on' : ''}`}
              style={{ cursor: 'pointer' }}
              onClick={() => navigate(n.key)}
            >
              {n.label}
            </span>
          ))}
        </div>
        <div
          className="search"
          onClick={() => setPaletteOpen(true)}
          data-shortcut="search"
          style={{ width: 240, display: 'flex', alignItems: 'center', gap: 6, cursor: 'text' }}
        >
          <SearchOutlined />
          搜索脚本…
          <span className="mono" style={{ marginLeft: 'auto' }}>Ctrl+K</span>
        </div>
        {/* 批次 AN（用户拍板）：「当前设备」只用于**展示现在在哪台设备上**，不用于切换 →
            去下拉（原 Sel 覆盖层 + ▾）、去 cursor:pointer/hover，只留胶囊外形 + 状态点 + 设备名。
            切换入口改在「设备」页的设备卡片（先探测、通了才切）。原型 §1 同步去 ▾。
            状态点语义不变（本机恒在线 / 远程按 online 三态）；设备名找不到时兜底「本机」。 */}
        <div className="devpick" data-testid="devpick" title="当前设备 · 在「设备」页切换">
          <span
            className="dot"
            style={{
              background: currentDeviceId === null || devices.find((d) => d.id === currentDeviceId)?.online
                ? 'var(--ok)' : 'var(--muted)',
            }}
          />
          <b>{devices.find((d) => d.id === currentDeviceId)?.name || '本机'}</b>
        </div>
        {/* SPEC §2.9 v2.14：齿轮（与各面板互斥）；v2.16：26×26 圆形按钮，样式见 desktop.css */}
        <span
          className={`gear${settingsOpen ? ' on' : ''}`}
          title="设置"
          data-testid="gear"
          style={{ cursor: 'pointer' }}
          onClick={() => (settingsOpen ? closeSettings() : openSettings())}
        >⚙</span>
        {/* v2.16：自绘窗口三键（decorations=false，走 Tauri Window API） */}
        <div className="wcb" title="窗口控件（最小化 / 最大化 / 关闭）">
          <i title="最小化" onClick={() => winAction('minimize')}>—</i>
          <i title="最大化" onClick={() => winAction('toggleMaximize')}>▢</i>
          <i className="x" title="关闭" onClick={() => winAction('close')}>✕</i>
        </div>
      </div>

      {/* 主区 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {apiReady ? <Outlet /> : (
          /* 批次 AE：只挡内容区，顶栏/终端栏照常渲染（首屏不卡） */
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, color: 'var(--muted)', fontSize: 13 }}>
            <Spin size="small" />
            <span>正在连接本地服务…</span>
          </div>
        )}
      </div>

      {/* 全局终端底栏 */}
      <GlobalTerminalBar />

      <SearchPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ShortcutPalette open={kbdOpen} onClose={() => setKbdOpen(false)} />
      <SettingsPalette open={settingsOpen} onClose={closeSettings} />
      {/* isScriptLib 供脚本工作区接入（当前先渲染路由页，G 后续迭代把列表页换为工作区） */}
      <span data-desk-lib={isScriptLib ? '1' : '0'} style={{ display: 'none' }} />
    </div>
  )
}
