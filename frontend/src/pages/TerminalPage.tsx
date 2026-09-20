import { useEffect, useState } from 'react'
import { Tabs, Button, Select, Space, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import TerminalTab from '../components/TerminalTab'
import { getDevices } from '../services/api'
import { useDeviceContext } from '../stores/deviceContext'

interface Tab {
  key: string
  deviceId: number | null
  shell: string
  /** V5-C：恢复标签（未连接态，附只读历史） */
  restored?: boolean
  tabId?: string
  restoredHistory?: string
}

let seq = 0
const nextKey = () => `t${++seq}`

/** V5-C：恢复标签面板——只读历史 + 重连按钮（PRD 4.F：未连接态恢复） */
function RestoredPane({ history, tabId, onReconnect }: {
  history: string
  tabId: string
  onReconnect: () => void
}) {
  return (
    <div style={{
      height: 'calc(100vh - 260px)', display: 'flex', flexDirection: 'column',
      background: '#1e1e1e', padding: 8, borderRadius: 4,
    }}>
      <div style={{
        flex: 1, overflow: 'auto', fontFamily: 'Consolas, monospace', fontSize: 13,
        whiteSpace: 'pre-wrap', color: '#d4d4d4', paddingBottom: 8,
      }}>
        {history || '（无历史输出）'}
      </div>
      <div style={{ borderTop: '1px solid #333', paddingTop: 8, color: '#888', fontSize: 13 }}>
        会话已结束（应用重启）。以上为历史输出（只读）。设备端进程无法恢复，点击重连开启新会话。
        <Button size="small" type="primary" style={{ marginLeft: 12 }} onClick={onReconnect}>
          重连
        </Button>
        <span style={{ marginLeft: 8, fontSize: 12, color: '#666' }}>tab: {tabId}</span>
      </div>
    </div>
  )
}

// 本机 shell 选项按目标平台划分（本机=类 Unix server）
const WIN_SHELLS = [
  { value: '', label: '默认' },
  { value: 'cmd', label: 'CMD' },
  { value: 'powershell', label: 'PowerShell' },
  { value: 'bash', label: 'Git Bash' },
]
const UNIX_SHELLS = [
  { value: '', label: '默认' },
  { value: 'bash', label: 'Bash' },
  { value: 'zsh', label: 'Zsh' },
]
// 平台类型 → 可选 shell
const SHELL_BY_PLATFORM: Record<string, { value: string; label: string }[]> = {
  windows: WIN_SHELLS,
  linux: UNIX_SHELLS,
  mac: UNIX_SHELLS,
}

export default function TerminalPage() {
  const { currentDeviceId } = useDeviceContext()
  const [devNames, setDevNames] = useState<Record<number, string>>({})
  const [devTypes, setDevTypes] = useState<Record<number, string>>({})

  // 拉设备列表 → id→name / id→type 映射
  useEffect(() => {
    getDevices()
      .then((res) => {
        const nm: Record<number, string> = {}
        const tp: Record<number, string> = {}
        ;(res.data || []).forEach((d: any) => { nm[d.id] = d.name; tp[d.id] = d.type })
        setDevNames(nm)
        setDevTypes(tp)
      })
      .catch(() => {})
  }, [])

  const makeTitle = (deviceId: number | null, shell: string) => {
    const dev = deviceId
      ? (devNames[deviceId] || `设备#${deviceId}`)
      : '本机'
    const sh = shell || '默认'
    return `${dev} · ${sh}`
  }

  // 当前目标（本机或远程）的可选 shell
  const shellOptions = (() => {
    if (!currentDeviceId) return UNIX_SHELLS
    return SHELL_BY_PLATFORM[devTypes[currentDeviceId]] || UNIX_SHELLS
  })()

  const [tabs, setTabs] = useState<Tab[]>(() => [
    { key: nextKey(), deviceId: currentDeviceId, shell: '' },
  ])
  const [activeKey, setActiveKey] = useState<string>(() => tabs[0].key)
  const [selShell, setSelShell] = useState('') // ShellPicker 当前选中

  // V5-C：应用启动 → 恢复持久化标签（未连接态 + 只读历史）
  useEffect(() => {
    fetch(`${import.meta.env.VITE_API_BASE || ''}/api/terminal/sessions`)
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
          setTabs((prev) => {
            // 空默认标签则替换，否则追加
            if (prev.length === 1 && !prev[0].restored) return [...restored, ...prev]
            return [...restored, ...prev]
          })
        }
      })
      .catch(() => {})
  }, [])

  // 切换目标设备后重置 shell 选择（避免残留上一平台的值）
  useEffect(() => { setSelShell('') }, [currentDeviceId])

  const addTab = (shell: string) => {
    const key = nextKey()
    const tab: Tab = { key, deviceId: currentDeviceId, shell }
    setTabs((prev) => [...prev, tab])
    setActiveKey(key)
  }

  const removeTab = (key: string) => {
    // V5-C：关闭「恢复标签」→ 同时删除后端持久化记录
    const t = tabs.find((x) => x.key === key)
    if (t?.restored && t.tabId) {
      fetch(`/api/terminal/sessions/${t.tabId}`, { method: 'DELETE' }).catch(() => {})
    }
    // 基于当前闭包状态计算（避免在 setTabs updater 内调用 setState 副作用）
    const idx = tabs.findIndex((t) => t.key === key)
    const next = tabs.filter((t) => t.key !== key)
    setTabs(next)
    // 关闭激活标签 → 激活相邻（优先下一个，其次前一个）；非激活 → 保持
    if (activeKey === key) {
      setActiveKey(next.length === 0 ? '' : (next[idx] ?? next[idx - 1] ?? next[0]).key)
    }
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Space style={{ marginBottom: 8 }} wrap>
        <Select
          style={{ width: 180 }}
          placeholder="选择 shell"
          value={selShell}
          options={shellOptions}
          onChange={(v) => { setSelShell(String(v)); addTab(String(v)) }}
        />
        <Button icon={<PlusOutlined />} onClick={() => addTab(selShell)}>新建终端</Button>
      </Space>

      {tabs.length === 0 ? (
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: '#888', flexDirection: 'column', gap: 8,
        }}>
          <span>没有打开的终端</span>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => addTab(selShell)}>新建终端</Button>
        </div>
      ) : (
        <Tabs
          type="editable-card"
          activeKey={activeKey}
          onChange={setActiveKey}
          onEdit={(key, action) => {
            if (action === 'remove') removeTab(String(key))
          }}
          hideAdd
          items={tabs.map((t) => ({
            key: t.key,
            label: `${makeTitle(t.deviceId, t.shell)}${t.restored ? '（已断开）' : ''}`,
            children: t.restored ? (
              <RestoredPane
                history={t.restoredHistory || ''}
                tabId={t.tabId!}
                onReconnect={() => {
                  // 重连：原 tabId 新建会话标签，替换恢复标签
                  const key = nextKey()
                  setTabs((prev) => [
                    ...prev.filter((x) => x.key !== t.key),
                    { key, deviceId: t.deviceId, shell: t.shell },
                  ])
                  setActiveKey(key)
                }}
              />
            ) : (
              <div style={{ height: 'calc(100vh - 260px)' }}>
                <TerminalTab
                  deviceId={t.deviceId}
                  shell={t.shell}
                  onError={(m) => message.error(m)}
                />
              </div>
            ),
          }))}
          style={{ flex: 1, minHeight: 0 }}
        />
      )}
    </div>
  )
}