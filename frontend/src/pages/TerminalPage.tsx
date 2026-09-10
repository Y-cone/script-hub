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

// 本机 shell 选项（远程用远端默认，shell 留空）
const SHELL_OPTIONS = [
  { value: '', label: '默认' },
  { value: 'bash', label: 'bash' },
  { value: 'zsh', label: 'zsh' },
  { value: 'cmd', label: 'cmd' },
  { value: 'powershell', label: 'powershell' },
]

export default function TerminalPage() {
  const { currentDeviceId } = useDeviceContext()
  const [devNames, setDevNames] = useState<Record<number, string>>({})

  // 拉设备列表 → id→name 映射（标签标题用设备名，而非“设备#id”）
  useEffect(() => {
    getDevices()
      .then((res) => {
        const map: Record<number, string> = {}
        ;(res.data || []).forEach((d: any) => { map[d.id] = d.name })
        setDevNames(map)
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

  const [tabs, setTabs] = useState<Tab[]>(() => [
    { key: nextKey(), deviceId: currentDeviceId, shell: '' },
  ])
  const [activeKey, setActiveKey] = useState<string>(tabs[0].key)

  const addTab = (shell: string) => {
    const key = nextKey()
    const tab: Tab = { key, deviceId: currentDeviceId, shell }
    setTabs((prev) => [...prev, tab])
    setActiveKey(key)
  }

  const removeTab = (key: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.key !== key)
      if (next.length === 0) {
        // 全部关闭 → 保留一个空标签
        const k = nextKey()
        return [{ key: k, deviceId: currentDeviceId, shell: '' }]
      }
      return next
    })
    setActiveKey((cur) => (cur === key ? tabs[0]?.key ?? '' : cur))
  }

  const onAdd = (shell: string) => addTab(shell)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Space style={{ marginBottom: 8 }} wrap>
        <Select
          style={{ width: 180 }}
          placeholder="选择 shell"
          defaultValue=""
          options={SHELL_OPTIONS}
          onChange={(v) => onAdd(String(v))}
        />
        <Button icon={<PlusOutlined />} onClick={() => addTab('')}>新建终端</Button>
      </Space>

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
    </div>
  )
}