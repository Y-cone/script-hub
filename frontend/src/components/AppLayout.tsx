import { Layout, Menu, theme, Select } from 'antd'
import { FileOutlined, HistoryOutlined, ThunderboltOutlined, DesktopOutlined, ScheduleOutlined, ClusterOutlined, CodeOutlined } from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { getDevices } from '../services/api'
import { useDeviceContext } from '../stores/deviceContext'

const { Sider, Content, Header } = Layout

const menuItems = [
  { key: '/', icon: <FileOutlined />, label: '脚本库' },
  { key: '/history', icon: <HistoryOutlined />, label: '运行历史' },
  { key: '/schedules', icon: <ScheduleOutlined />, label: '定时调度' },
  { key: '/devices', icon: <ClusterOutlined />, label: '远程设备' },
  { key: '/terminal', icon: <CodeOutlined />, label: '终端' },
  { key: '/system', icon: <DesktopOutlined />, label: '本机信息' },
]

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { token: { colorBgContainer, borderRadiusLG } } = theme.useToken()
  const { currentDeviceId, setCurrentDeviceId } = useDeviceContext()
  const [devices, setDevices] = useState<{ id: number; name: string }[]>([])

  // 每次路由变化刷新设备列表（含：进入设备页添加后返回，新增设备立即出现在下拉）
  useEffect(() => {
    getDevices().then((res) => setDevices(res.data || [])).catch(() => {})
  }, [location.pathname])

  // 监听设备管理页增删操作 → 立即刷新下拉
  useEffect(() => {
    const onChanged = () =>
      getDevices().then((res) => setDevices(res.data || [])).catch(() => {})
    window.addEventListener('devices-changed', onChanged)
    return () => window.removeEventListener('devices-changed', onChanged)
  }, [])

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider
        collapsible
        theme="dark"
        style={{
          overflow: 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
        }}
      >
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          color: '#fff',
          fontSize: 20,
          fontWeight: 'bold',
          borderBottom: '1px solid rgba(255,255,255,0.1)',
        }}>
          <ThunderboltOutlined style={{ fontSize: 24, color: '#1677ff' }} />
          ScriptHub
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Layout style={{ marginLeft: 200, height: '100vh', overflow: 'hidden' }}>
        <Header style={{
          background: colorBgContainer,
          padding: '0 24px',
          fontSize: 16,
          fontWeight: 500,
          borderBottom: '1px solid #f0f0f0',
          display: 'flex',
          alignItems: 'center',
          flexShrink: 0,
        }}>
          {location.pathname === '/' ? '脚本库' : location.pathname === '/history' ? '运行历史' : location.pathname === '/system' ? '本机信息' : location.pathname === '/schedules' ? '定时调度' : location.pathname === '/devices' ? '远程设备' : location.pathname === '/terminal' ? '终端' : '脚本详情'}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 13, color: '#888' }}>当前设备</span>
            <Select
              size="small"
              style={{ width: 180 }}
              placeholder="本机"
              value={currentDeviceId ?? 0}
              onChange={(v) => setCurrentDeviceId(v === 0 ? null : (v ?? null))}
              options={[
                { value: 0, label: '本机' },
                ...devices.map((d) => ({ value: d.id, label: d.name })),
              ]}
            />
          </div>
        </Header>
        <Content style={{
          margin: 24,
          padding: 24,
          background: colorBgContainer,
          borderRadius: borderRadiusLG,
          overflow: 'hidden',
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
        }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
