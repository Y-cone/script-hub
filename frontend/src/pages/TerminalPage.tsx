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
}

let seq = 0
const nextKey = () => `t${++seq}`

// shell 选项按目标平台划分（本机=类 Unix server）
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

  // 切换目标设备后重置 shell 选择（避免残留上一平台的值）
  useEffect(() => { setSelShell('') }, [currentDeviceId])

  const addTab = (shell: string) => {
    const key = nextKey()
    const tab: Tab = { key, deviceId: currentDeviceId, shell }
    setTabs((prev) => [...prev, tab])
    setActiveKey(key)
  }

  const removeTab = (key: string) => {
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
            label: makeTitle(t.deviceId, t.shell),
            children: (
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